"""Loopback-only event socket that receives game-bridge events.

The companion process listens; the game adapter connects. The listener binds
127.0.0.1 and every accepted peer address is checked again, so it stays
unreachable from another host even if a caller passes a different interface.

Authentication is a per-connection HMAC challenge. The shared token is never
sent over the socket, each nonce is used once, and a proof captured from one
connection cannot be replayed on the next. An unauthenticated peer can do
nothing except fail the handshake; no event it sends is ever queued.

Threading here is pure Python only: sockets, json, hmac, and a bounded queue.
This module belongs to the companion process, which contains no game code, and
must never be imported by the game-side mod, where callbacks run on the engine
thread and must not be moved onto a worker. Events are handed to the caller
through a queue so the consumer keeps running on its own thread.

Receiving an event is not permission to act on it. Payload semantics, action
execution, and save-lineage trust remain the caller's decision.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import queue
import secrets
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .protocol import PROTOCOL_VERSION, BridgePaths, ProtocolError, atomic_write_json


LOOPBACK_HOST = "127.0.0.1"
TOKEN_ENVIRONMENT_VARIABLE = "PLUMBMATE_BRIDGE_TOKEN"
TOKEN_FILENAME = "bridge-token"
ENDPOINT_FILENAME = "bridge-endpoint.json"
DEFAULT_BRIDGE_PORT = 47651

MIN_TOKEN_CHARACTERS = 32
NONCE_BYTES = 32
MAX_FRAME_BYTES = 64 * 1024
MAX_EVENT_QUEUE = 256
MAX_CONNECTIONS = 4
HANDSHAKE_TIMEOUT_SECONDS = 5.0
IDLE_TIMEOUT_SECONDS = 300.0
_ACCEPT_POLL_SECONDS = 0.1

# Transport-level allow list. It bounds what the socket will carry; it does not
# claim any of these events is produced, trustworthy, or safe to act on.
ALLOWED_EVENT_KINDS = frozenset((
    "heartbeat",
    "zone_loaded",
    "save_begin",
    "save_committed",
    "interaction_started",
    "chat_turn",
))


class SocketBridgeError(RuntimeError):
    """The socket layer refused to start, bind, or continue."""


class AuthenticationError(SocketBridgeError):
    """A peer failed the challenge, or no token was configured."""


def generate_token() -> str:
    """Return a fresh token; callers persist it, this module never invents one."""
    return secrets.token_hex(32)


def token_path(paths: BridgePaths) -> Path:
    return paths.state / TOKEN_FILENAME


def endpoint_path(paths: BridgePaths) -> Path:
    return paths.state / ENDPOINT_FILENAME


def write_endpoint(paths: BridgePaths, host: str, port: int) -> Path:
    """Persist connection configuration, retained across listener restarts.

    This is a convenience for discovery, never an authorization: a peer still
    has to answer the challenge. File existence does NOT imply a live listener.
    """
    paths.ensure()
    target = endpoint_path(paths)
    atomic_write_json(target, {"host": host, "port": int(port),
                               "protocol_version": PROTOCOL_VERSION})
    return target


def clear_endpoint(paths: BridgePaths) -> None:
    """Explicit configuration reset only; never call on listener shutdown."""
    try:
        endpoint_path(paths).unlink()
    except OSError:
        pass


def configured_port(paths: BridgePaths, requested: int | None = None) -> int:
    """Reuse the last port; fail visibly on conflict instead of silently moving.

    Explicit port 0 remains available for tests/manual discovery. The game must
    explicitly reload configuration after a deliberate port or token change.
    """
    if requested is not None:
        if not 0 <= requested <= 65535:
            raise SocketBridgeError("Port must be between 0 and 65535")
        return requested
    try:
        with endpoint_path(paths).open(encoding="utf-8") as handle:
            endpoint = json.loads(handle.read(4097))
        port = int(endpoint["port"])
        if endpoint.get("host", LOOPBACK_HOST) != LOOPBACK_HOST or not 1 <= port <= 65535:
            raise ValueError("Invalid loopback endpoint")
        return port
    except FileNotFoundError:
        return DEFAULT_BRIDGE_PORT
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SocketBridgeError("Invalid stored endpoint; choose an explicit --port") from exc


def write_token(paths: BridgePaths, token: str | None = None) -> str:
    """Persist a token readable only by the current user.

    File permissions are the trust boundary, exactly as for the rest of the
    bridge directory. A token in a world-readable location is not a secret.
    """
    token = token or generate_token()
    _validate_token(token)
    paths.ensure()
    target = token_path(paths)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(target, 0o600)
    return token


def load_token(
    paths: BridgePaths | None = None, *, token: str | None = None,
) -> str:
    """Resolve the token from an explicit value, the environment, or the file.

    Fail closed: a missing or too-short token raises instead of starting an
    unauthenticated listener.
    """
    candidate = token or os.environ.get(TOKEN_ENVIRONMENT_VARIABLE)
    if not candidate and paths is not None:
        target = token_path(paths)
        if target.exists():
            candidate = target.read_text(encoding="utf-8").strip()
    if not candidate:
        raise AuthenticationError(
            "No bridge token configured; set "
            f"{TOKEN_ENVIRONMENT_VARIABLE} or write {TOKEN_FILENAME}"
        )
    _validate_token(candidate)
    return candidate


def _validate_token(token: str) -> None:
    if not isinstance(token, str) or len(token.strip()) < MIN_TOKEN_CHARACTERS:
        raise AuthenticationError(
            f"Bridge token must be at least {MIN_TOKEN_CHARACTERS} characters"
        )


def _proof(token: str, nonce: str) -> str:
    return hmac.new(
        token.encode("utf-8"), nonce.encode("ascii"), hashlib.sha256,
    ).hexdigest()


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class BridgeEvent:
    """One accepted event. Accepted means well-formed and authenticated only."""

    kind: str
    event_id: str
    payload: dict[str, Any]
    received_at: str
    peer: str


@dataclass
class ServerStats:
    accepted_connections: int = 0
    rejected_peers: int = 0
    failed_handshakes: int = 0
    accepted_events: int = 0
    rejected_events: int = 0
    dropped_events: int = 0


def mock_event(
    kind: str = "heartbeat",
    payload: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Build a well-formed event frame for tests and offline demos."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": kind,
        "event_id": event_id or uuid.uuid4().hex,
        "payload": payload or {},
    }


def validate_event(value: dict[str, Any]) -> None:
    """Check envelope shape and the transport allow list, never payload meaning."""
    missing = [key for key in ("protocol_version", "kind", "event_id") if key not in value]
    if missing:
        raise ProtocolError("Missing event fields: " + ", ".join(missing))
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported event protocol")
    if value["kind"] not in ALLOWED_EVENT_KINDS:
        raise ProtocolError("Event kind is not allow-listed")
    event_id = value["event_id"]
    if not isinstance(event_id, str) or not event_id.strip():
        raise ProtocolError("Event event_id must be a non-empty string")
    if not isinstance(value.get("payload", {}), dict):
        raise ProtocolError("Event payload must be a JSON object")


class _FrameStream:
    """Newline-delimited JSON with a hard size cap in both directions."""

    def __init__(self, connection: socket.socket, max_bytes: int = MAX_FRAME_BYTES) -> None:
        self._connection = connection
        self._buffer = bytearray()
        self._max_bytes = max_bytes

    def read_frame(self) -> dict[str, Any]:
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                if index + 1 > self._max_bytes:
                    raise ProtocolError("Frame exceeds the maximum size")
                raw = bytes(self._buffer[:index])
                del self._buffer[: index + 1]
                return _decode_frame(raw)
            if len(self._buffer) >= self._max_bytes:
                raise ProtocolError("Frame exceeds the maximum size")
            chunk = self._connection.recv(4096)
            if not chunk:
                raise ConnectionError("Peer closed the connection")
            self._buffer.extend(chunk)

    def write_frame(self, value: dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) + 1 > self._max_bytes:
            raise ProtocolError("Frame exceeds the maximum size")
        self._connection.sendall(data + b"\n")


def _decode_frame(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Frame is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Frame must be a JSON object")
    return value


class EventSocketServer:
    """Bounded loopback listener that queues authenticated bridge events.

    ``port=0`` asks the kernel for a free port, which is how tests and the mock
    demo run without reserving a fixed one. Read :attr:`port` after ``start()``.
    """

    def __init__(
        self,
        token: str,
        *,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        max_connections: int = MAX_CONNECTIONS,
        queue_size: int = MAX_EVENT_QUEUE,
        handshake_timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        _validate_token(token)
        if not _is_loopback(host):
            raise SocketBridgeError(f"Refusing to bind a non-loopback host: {host!r}")
        self._token = token
        self._host = host
        self._requested_port = port
        self._max_frame_bytes = max_frame_bytes
        self._handshake_timeout = handshake_timeout
        self._idle_timeout = idle_timeout
        self._slots = threading.Semaphore(max_connections)
        self._events: queue.Queue[BridgeEvent] = queue.Queue(maxsize=queue_size)
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._listener: socket.socket | None = None
        self._acceptor: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self.stats = ServerStats()

    @property
    def port(self) -> int:
        if self._listener is None:
            raise SocketBridgeError("Server is not started")
        return int(self._listener.getsockname()[1])

    @property
    def address(self) -> tuple[str, int]:
        return (self._host, self.port)

    def start(self) -> None:
        if self._listener is not None:
            raise SocketBridgeError("Server is already started")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self._host, self._requested_port))
            listener.listen(MAX_CONNECTIONS)
        except OSError as exc:
            listener.close()
            raise SocketBridgeError(f"Cannot bind {self._host}: {exc}") from exc
        listener.settimeout(_ACCEPT_POLL_SECONDS)
        self._listener = listener
        self._acceptor = threading.Thread(
            target=self._accept_loop, name="plumbmate-accept", daemon=True,
        )
        self._acceptor.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        with self._lock:
            live = list(self._connections)
        for connection in live:
            # Unblock a worker parked in recv; the worker still closes its own socket.
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        if self._acceptor is not None:
            self._acceptor.join(timeout)
            self._acceptor = None
        for worker in list(self._workers):
            worker.join(timeout)
        self._workers.clear()

    def __enter__(self) -> EventSocketServer:
        self.start()
        return self

    def __exit__(self, *_exception: object) -> None:
        self.stop()

    def poll(self, timeout: float = 0.0) -> BridgeEvent | None:
        """Return the next queued event, or None once the timeout elapses."""
        try:
            return self._events.get(timeout=timeout) if timeout > 0 else self._events.get_nowait()
        except queue.Empty:
            return None

    def drain(self) -> list[BridgeEvent]:
        events: list[BridgeEvent] = []
        while True:
            event = self.poll()
            if event is None:
                return events
            events.append(event)

    def _accept_loop(self) -> None:
        listener = self._listener
        while not self._stopping.is_set() and listener is not None:
            try:
                connection, address = listener.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            if not _is_loopback(address[0]):
                # Cannot happen through a loopback bind; refused anyway.
                self._count("rejected_peers")
                connection.close()
                continue
            if not self._slots.acquire(blocking=False):
                self._count("rejected_peers")
                connection.close()
                continue
            self._count("accepted_connections")
            with self._lock:
                self._connections.add(connection)
            self._workers = [thread for thread in self._workers if thread.is_alive()]
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection, "{}:{}".format(*address[:2])),
                name="plumbmate-bridge",
                daemon=True,
            )
            self._workers.append(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket, peer: str) -> None:
        stream = _FrameStream(connection, self._max_frame_bytes)
        try:
            connection.settimeout(self._handshake_timeout)
            nonce = secrets.token_hex(NONCE_BYTES)
            stream.write_frame({
                "protocol_version": PROTOCOL_VERSION, "kind": "challenge", "nonce": nonce,
            })
            if not self._authenticate(stream.read_frame(), nonce):
                self._count("failed_handshakes")
                stream.write_frame({
                    "protocol_version": PROTOCOL_VERSION,
                    "kind": "error", "reason": "unauthorized",
                })
                return
            stream.write_frame({"protocol_version": PROTOCOL_VERSION, "kind": "welcome"})
            connection.settimeout(self._idle_timeout)
            self._event_loop(stream, peer)
        except ProtocolError as exc:
            self._reply_error(stream, str(exc))
        except (OSError, ConnectionError, ValueError):
            # A disconnect or malformed peer ends this connection and nothing else.
            pass
        finally:
            with self._lock:
                self._connections.discard(connection)
            connection.close()
            self._slots.release()

    def _event_loop(self, stream: _FrameStream, peer: str) -> None:
        while not self._stopping.is_set():
            frame = stream.read_frame()
            try:
                validate_event(frame)
            except ProtocolError as exc:
                self._count("rejected_events")
                stream.write_frame({
                    "protocol_version": PROTOCOL_VERSION, "kind": "error",
                    "event_id": frame.get("event_id"), "reason": str(exc),
                })
                continue
            event = BridgeEvent(
                kind=frame["kind"],
                event_id=frame["event_id"],
                payload=dict(frame.get("payload", {})),
                received_at=datetime.now(timezone.utc).isoformat(),
                peer=peer,
            )
            try:
                self._events.put_nowait(event)
            except queue.Full:
                # Backpressure is reported, never silently dropped.
                self._count("dropped_events")
                stream.write_frame({
                    "protocol_version": PROTOCOL_VERSION, "kind": "error",
                    "event_id": event.event_id, "reason": "queue_full",
                })
                continue
            self._count("accepted_events")
            stream.write_frame({
                "protocol_version": PROTOCOL_VERSION, "kind": "ack",
                "event_id": event.event_id,
            })

    def _authenticate(self, hello: dict[str, Any], nonce: str) -> bool:
        if hello.get("kind") != "hello":
            return False
        if hello.get("protocol_version") != PROTOCOL_VERSION:
            return False
        proof = hello.get("proof")
        if not isinstance(proof, str):
            return False
        return hmac.compare_digest(proof, _proof(self._token, nonce))

    def _reply_error(self, stream: _FrameStream, reason: str) -> None:
        try:
            stream.write_frame({
                "protocol_version": PROTOCOL_VERSION, "kind": "error", "reason": reason,
            })
        except (OSError, ProtocolError):
            pass

    def _count(self, name: str) -> None:
        with self._lock:
            setattr(self.stats, name, getattr(self.stats, name) + 1)


class BridgeClient:
    """Minimal loopback client used by tests, the mock demo, and future adapters.

    The game-side adapter needs its own Python 3.7-compatible port of this
    handshake; this class targets the companion runtime only.
    """

    def __init__(
        self,
        token: str,
        port: int,
        *,
        host: str = LOOPBACK_HOST,
        timeout: float = 5.0,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        _validate_token(token)
        if not _is_loopback(host):
            raise SocketBridgeError(f"Refusing to connect to a non-loopback host: {host!r}")
        self._token = token
        self._host = host
        self._port = port
        self._timeout = timeout
        self._max_frame_bytes = max_frame_bytes
        self._connection: socket.socket | None = None
        self._stream: _FrameStream | None = None

    def connect(self) -> BridgeClient:
        connection = socket.create_connection((self._host, self._port), self._timeout)
        try:
            connection.settimeout(self._timeout)
            stream = _FrameStream(connection, self._max_frame_bytes)
            challenge = stream.read_frame()
            if challenge.get("kind") != "challenge" or not isinstance(challenge.get("nonce"), str):
                raise SocketBridgeError("Server did not send a challenge")
            stream.write_frame({
                "protocol_version": PROTOCOL_VERSION, "kind": "hello",
                "proof": _proof(self._token, challenge["nonce"]),
            })
            welcome = stream.read_frame()
            if welcome.get("kind") != "welcome":
                raise AuthenticationError(str(welcome.get("reason", "handshake refused")))
        except BaseException:
            # A refused or dropped handshake must not leak the socket.
            connection.close()
            raise
        self._connection = connection
        self._stream = stream
        return self

    def send_event(
        self,
        kind: str = "heartbeat",
        payload: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Send one event and return the server's ack or error frame."""
        if self._stream is None:
            raise SocketBridgeError("Client is not connected")
        self._stream.write_frame(mock_event(kind, payload, event_id=event_id))
        return self._stream.read_frame()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._stream = None

    def __enter__(self) -> BridgeClient:
        return self.connect()

    def __exit__(self, *_exception: object) -> None:
        self.close()
