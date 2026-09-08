"""Python 3.7, non-blocking client for the companion event socket.

This runs on the game's engine thread, so it must never block, never start a
thread, and never raise into a callback. Every call does a bounded amount of
work and returns; progress happens across repeated ``pump()`` calls driven by a
console command or a tick handler.

The socket is non-blocking from creation and stays that way. Connection
completion is detected with ``getpeername``/``SO_ERROR`` rather than ``select``.

An earlier note here claimed select was absent from the game. That was wrong:
it was scanned for in ``base.zip``'s ``lib/``, but the C extensions live in
``Frameworks/lib-dynload/``, and the in-game probe found ``select.dylib`` there.
The select-free path is kept anyway, because this client owns exactly one
socket and polling it directly needs no extra module, but it is a preference
now, not a constraint. Only socket, json, hmac, hashlib, uuid, time, and errno
are used, and the in-game probe confirmed every one of them imports.

The token is never sent; the server's per-connection nonce is answered with an
HMAC proof, so a captured frame cannot be replayed on the next connection.

Delivery is at-most-once. An event counts as delivered only when a matching ack
arrives. If the connection drops after a send, the event is counted lost and is
not resent, because a silent retry could duplicate an event the companion has
already applied. Queued-but-unsent events do survive a reconnect.

Engine independence keeps this unit-testable against the real companion server.
Those tests prove the protocol and the state machine. They prove nothing about
the engine thread, the tick budget, or the game's shutdown order.
"""

import errno
import hashlib
import hmac
import json
import socket
import time
import uuid


PROTOCOL_VERSION = "0.1"
LOOPBACK_HOST = "127.0.0.1"

MIN_TOKEN_CHARACTERS = 32
MAX_FRAME_BYTES = 64 * 1024
MAX_OUTBOX_EVENTS = 64
MAX_PENDING_ACKS = 64
MAX_READ_BYTES_PER_PUMP = 16 * 1024
MAX_WRITE_BYTES_PER_PUMP = 16 * 1024
MAX_FRAMES_PER_PUMP = 16
CONNECT_TIMEOUT_SECONDS = 5.0
HANDSHAKE_TIMEOUT_SECONDS = 5.0
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
ACK_TIMEOUT_SECONDS = 15.0
KEEPALIVE_SECONDS = 60.0

# Must stay identical to the companion's transport allow list. It bounds what
# the socket carries and says nothing about whether an event may be acted on.
ALLOWED_EVENT_KINDS = frozenset((
    "heartbeat",
    "zone_loaded",
    "save_begin",
    "save_committed",
    "interaction_started",
    "chat_turn",
))

DISCONNECTED = "DISCONNECTED"
CONNECTING = "CONNECTING"
AWAITING_CHALLENGE = "AWAITING_CHALLENGE"
AWAITING_WELCOME = "AWAITING_WELCOME"
READY = "READY"
BACKOFF = "BACKOFF"
CLOSED = "CLOSED"


def _errno_set(*names):
    codes = set()
    for name in names:
        code = getattr(errno, name, None)
        if code is not None:
            codes.add(code)
    return frozenset(codes)


# Windows reports the same conditions under WSA* names.
_CONNECT_PENDING = _errno_set(
    "EINPROGRESS", "EALREADY", "EWOULDBLOCK", "EAGAIN", "EINVAL",
    "WSAEINPROGRESS", "WSAEALREADY", "WSAEWOULDBLOCK", "WSAEINVAL",
)
_RETRY_LATER = _errno_set("EAGAIN", "EWOULDBLOCK", "EINTR", "WSAEWOULDBLOCK")
_ALREADY_CONNECTED = _errno_set("EISCONN", "WSAEISCONN")


def _is_loopback(host):
    """Accept only a literal 127.0.0.0/8 address; this client is AF_INET."""
    parts = host.split(".") if isinstance(host, str) else []
    if len(parts) != 4 or not all(
            1 <= len(part) <= 3 and all("0" <= digit <= "9" for digit in part)
            and (len(part) == 1 or part[0] != "0") for part in parts):
        return False
    octets = [int(part) for part in parts]
    return octets[0] == 127 and all(0 <= octet <= 255 for octet in octets)


def _proof(token, nonce):
    return hmac.new(
        token.encode("utf-8"), nonce.encode("ascii"), hashlib.sha256,
    ).hexdigest()


def build_event(kind, payload=None, event_id=None):
    """Build one event frame; the caller owns whatever the payload claims."""
    if kind not in ALLOWED_EVENT_KINDS:
        raise ValueError("Event kind is not allow-listed: {}".format(kind))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Event payload must be a dict")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": kind,
        "event_id": event_id or uuid.uuid4().hex,
        "payload": payload,
    }


class BridgeClient(object):
    """Non-blocking, thread-free client driven entirely by ``pump()``.

    ``now`` is injectable so backoff and timeouts are testable without sleeping.
    """

    def __init__(
        self,
        token,
        port,
        host=LOOPBACK_HOST,
        now=None,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        handshake_timeout=HANDSHAKE_TIMEOUT_SECONDS,
        initial_backoff=INITIAL_BACKOFF_SECONDS,
        max_backoff=MAX_BACKOFF_SECONDS,
        max_outbox=MAX_OUTBOX_EVENTS,
        max_frame_bytes=MAX_FRAME_BYTES,
        ack_timeout=ACK_TIMEOUT_SECONDS,
        keepalive=KEEPALIVE_SECONDS,
    ):
        if not isinstance(token, str) or len(token.strip()) < MIN_TOKEN_CHARACTERS:
            raise ValueError(
                "Bridge token must be at least {} characters".format(MIN_TOKEN_CHARACTERS)
            )
        if not _is_loopback(host):
            raise ValueError("Refusing a non-loopback host: {!r}".format(host))
        self._token = token
        self._host = host
        self._port = int(port)
        if not 1 <= self._port <= 65535:
            raise ValueError("Bridge port must be between 1 and 65535")
        self._now = now or time.monotonic
        self._connect_timeout = connect_timeout
        self._handshake_timeout = handshake_timeout
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._max_outbox = max_outbox
        self._max_frame_bytes = max_frame_bytes
        for interval in (ack_timeout, keepalive):
            if type(interval) not in (int, float) or not 0 < interval <= 3600:
                raise ValueError("Connection deadlines must be finite positive seconds, at most 3600")
        self._ack_timeout = ack_timeout
        self._keepalive = keepalive
        self._next_keepalive = self._now() + keepalive

        self.state = DISCONNECTED
        self.last_error = None
        self.stats = {
            "connect_attempts": 0, "handshakes": 0, "failures": 0,
            "queued_events": 0, "sent_events": 0, "acked_events": 0,
            "refused_events": 0, "lost_events": 0, "rejected_by_outbox": 0,
            "unknown_frames": 0,
        }
        self._socket = None
        self._deadline = 0.0
        self._retry_at = 0.0
        self._backoff = 0.0
        self._inbound = bytearray()
        self._outbound = bytearray()
        self._outbox = []
        self._pending = []
        self._pending_since = {}

    @property
    def connected(self):
        return self.state == READY

    @property
    def queued(self):
        return len(self._outbox)

    @property
    def awaiting_ack(self):
        return len(self._pending)

    @property
    def backoff_seconds(self):
        """Current retry delay; 0 before the first failure and after a handshake."""
        return self._backoff

    def queue_event(self, kind, payload=None, event_id=None):
        """Encode and queue one event without touching the socket.

        Returns the event_id, or None when the outbox is full. Encoding here
        means an oversized payload is refused by the caller's own command
        instead of being cut off mid-frame by the server.
        """
        frame = build_event(kind, payload, event_id)
        if (frame["event_id"] in self._pending
                or any(item[0] == frame["event_id"] for item in self._outbox)):
            raise ValueError("Duplicate queued event identifier")
        encoded = json.dumps(frame, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) + 1 > self._max_frame_bytes:
            raise ValueError("Event frame exceeds the maximum size")
        if len(self._outbox) >= self._max_outbox:
            self.stats["rejected_by_outbox"] += 1
            return None
        self._outbox.append((frame["event_id"], encoded + b"\n"))
        self.stats["queued_events"] += 1
        return frame["event_id"]

    def pump(self):
        """Advance the connection by a bounded step; never blocks, never raises."""
        try:
            self._pump()
        except Exception as exc:
            # A game callback must never see an exception from the bridge.
            self._fail("{}: {}".format(type(exc).__name__, exc))
        return self.state

    def close(self):
        """Release the socket. Queued events are kept; in-flight ones are lost."""
        self.stats["lost_events"] += len(self._pending)
        self._pending = []
        self._pending_since.clear()
        self._close_socket()
        self._inbound = bytearray()
        self._outbound = bytearray()
        self.state = CLOSED

    def reopen(self):
        """Allow a closed client to connect again on the next pump."""
        if self.state == CLOSED:
            self.state = DISCONNECTED
            self._backoff = 0.0

    def _pump(self):
        now = self._now()
        if self.state == CLOSED:
            return
        if self.state == BACKOFF:
            if now < self._retry_at:
                return
            self.state = DISCONNECTED
        if self.state == DISCONNECTED:
            self._start_connect(now)
            return
        if self.state == CONNECTING:
            if not self._connect_completed():
                if self.state == CONNECTING and now >= self._deadline:
                    self._fail("connect_timeout")
                return
            self.state = AWAITING_CHALLENGE
            self._deadline = now + self._handshake_timeout
        if self.state in (AWAITING_CHALLENGE, AWAITING_WELCOME) and now >= self._deadline:
            self._fail("handshake_timeout")
            return
        self._read_frames()
        if self.state == READY:
            if any(now - sent_at >= self._ack_timeout for sent_at in self._pending_since.values()):
                self._fail("ack_timeout")
                return
            if not self._pending and not self._outbox and now >= self._next_keepalive:
                self.queue_event("heartbeat", {"source": "transport_keepalive"})
            self._encode_outbox()
        if self._socket is not None:
            self._flush_outbound()

    def _start_connect(self, now):
        self._close_socket()
        self._inbound = bytearray()
        self._outbound = bytearray()
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            connection.setblocking(False)
            code = connection.connect_ex((self._host, self._port))
        except OSError as exc:
            connection.close()
            self._fail("connect_error_{}".format(exc.errno))
            return
        if code and code not in _CONNECT_PENDING and code not in _ALREADY_CONNECTED:
            connection.close()
            self._fail("connect_refused_{}".format(code))
            return
        self._socket = connection
        self.state = CONNECTING
        self._deadline = now + self._connect_timeout
        self.stats["connect_attempts"] += 1

    def _connect_completed(self):
        """Detect completion without select; SO_ERROR reports a real failure."""
        try:
            self._socket.getpeername()
            return True
        except OSError:
            code = self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if code:
                self._fail("connect_refused_{}".format(code))
            return False

    def _read_frames(self):
        read_bytes = 0
        frames = 0
        while frames < MAX_FRAMES_PER_PUMP:
            # A previous pump may have hit its frame budget with complete
            # frames still buffered. Drain those BEFORE recv/EAGAIN/EOF.
            index = self._inbound.find(b"\n")
            if index >= 0:
                if index + 1 > self._max_frame_bytes:
                    self._fail("frame_too_large")
                    return
                raw = bytes(self._inbound[:index])
                del self._inbound[: index + 1]
                frames += 1
                self._handle_frame(raw)
                if self.state in (BACKOFF, CLOSED):
                    return
                continue
            if len(self._inbound) >= self._max_frame_bytes:
                self._fail("frame_too_large")
                return
            if read_bytes >= MAX_READ_BYTES_PER_PUMP:
                return
            try:
                chunk = self._socket.recv(min(4096, MAX_READ_BYTES_PER_PUMP - read_bytes))
            except OSError as exc:
                if exc.errno in _RETRY_LATER:
                    return
                self._fail("recv_error_{}".format(exc.errno))
                return
            if not chunk:
                self._fail("peer_closed")
                return
            read_bytes += len(chunk)
            self._inbound.extend(chunk)

    def _handle_frame(self, raw):
        try:
            frame = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._fail("frame_not_json")
            return
        if not isinstance(frame, dict):
            self._fail("frame_not_object")
            return
        kind = frame.get("kind")
        if self.state == AWAITING_CHALLENGE:
            nonce = frame.get("nonce")
            if kind != "challenge" or not isinstance(nonce, str):
                self._fail("challenge_expected")
                return
            self._write_frame({
                "protocol_version": PROTOCOL_VERSION, "kind": "hello",
                "proof": _proof(self._token, nonce),
            })
            self.state = AWAITING_WELCOME
        elif self.state == AWAITING_WELCOME:
            if kind != "welcome":
                self._fail(str(frame.get("reason", "handshake_refused")))
                return
            self.state = READY
            self._backoff = 0.0
            self.last_error = None
            self.stats["handshakes"] += 1
            self._next_keepalive = self._now() + self._keepalive
        elif self.state == READY:
            if kind == "ack":
                self._resolve(frame.get("event_id"), True, None)
            elif kind == "error":
                self._resolve(frame.get("event_id"), False, frame.get("reason"))
            else:
                self.stats["unknown_frames"] += 1

    def _resolve(self, event_id, accepted, reason):
        if event_id not in self._pending:
            self.stats["unknown_frames"] += 1
            return
        self._pending.remove(event_id)
        self._pending_since.pop(event_id, None)
        if accepted:
            self.stats["acked_events"] += 1
        else:
            # The companion refused this event; a blind resend would loop.
            self.stats["refused_events"] += 1
            self.last_error = reason

    def _encode_outbox(self):
        while self._outbox and len(self._pending) < MAX_PENDING_ACKS:
            if len(self._outbound) >= MAX_WRITE_BYTES_PER_PUMP:
                return
            event_id, encoded = self._outbox.pop(0)
            self._outbound.extend(encoded)
            self._pending.append(event_id)
            self._pending_since[event_id] = self._now()
            self._next_keepalive = self._now() + self._keepalive
            self.stats["sent_events"] += 1

    def _write_frame(self, value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._outbound.extend(encoded + b"\n")

    def _flush_outbound(self):
        written = 0
        while self._outbound and written < MAX_WRITE_BYTES_PER_PUMP:
            try:
                sent = self._socket.send(bytes(self._outbound[:4096]))
            except OSError as exc:
                if exc.errno in _RETRY_LATER:
                    return
                self._fail("send_error_{}".format(exc.errno))
                return
            if sent <= 0:
                return
            del self._outbound[:sent]
            written += sent

    def _fail(self, reason):
        self.stats["failures"] += 1
        # In-flight events have an unknown outcome and are never resent.
        self.stats["lost_events"] += len(self._pending)
        self._pending = []
        self._pending_since.clear()
        self.last_error = reason
        self._close_socket()
        self._inbound = bytearray()
        self._outbound = bytearray()
        self._backoff = min(
            max(self._backoff * 2.0, self._initial_backoff), self._max_backoff,
        )
        self._retry_at = self._now() + self._backoff
        self.state = BACKOFF

    def _close_socket(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
