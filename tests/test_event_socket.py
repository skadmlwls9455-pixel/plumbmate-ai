import json
import os
import socket
import stat
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

from plumbmate.event_socket import (
    ALLOWED_EVENT_KINDS,
    MIN_TOKEN_CHARACTERS,
    TOKEN_ENVIRONMENT_VARIABLE,
    AuthenticationError,
    BridgeClient,
    EventSocketServer,
    SocketBridgeError,
    DEFAULT_BRIDGE_PORT,
    _FrameStream,
    _proof,
    generate_token,
    configured_port,
    endpoint_path,
    load_token,
    mock_event,
    token_path,
    validate_event,
    write_token,
    write_endpoint,
)
from plumbmate.protocol import PROTOCOL_VERSION, BridgePaths, ProtocolError


TOKEN = "t" * MIN_TOKEN_CHARACTERS
OTHER_TOKEN = "u" * MIN_TOKEN_CHARACTERS


class EndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = BridgePaths(Path(temporary.name))

    def test_initial_default_and_explicit_override(self):
        self.assertEqual(configured_port(self.paths), DEFAULT_BRIDGE_PORT)
        self.assertEqual(configured_port(self.paths, 0), 0)
        self.assertEqual(configured_port(self.paths, 51000), 51000)
        for port in (-1, 65536):
            with self.assertRaises(SocketBridgeError):
                configured_port(self.paths, port)

    def test_saved_port_is_reused_and_bad_endpoint_is_not_guessed(self):
        write_endpoint(self.paths, "127.0.0.1", 51000)
        self.assertEqual(configured_port(self.paths), 51000)
        for host, port in (("0.0.0.0", 51000), ("localhost", 51000), ("127.0.0.1", 70000)):
            write_endpoint(self.paths, host, port)
            with self.assertRaises(SocketBridgeError):
                configured_port(self.paths)

    def test_cli_keeps_configuration_on_exit_and_reuses_it(self):
        from plumbmate.cli import main
        event = SimpleNamespace(kind="heartbeat", event_id="test", payload={}, received_at="test")
        with mock.patch("plumbmate.cli.load_token", return_value=TOKEN), \
             mock.patch("plumbmate.cli.EventSocketServer") as factory, \
             mock.patch("builtins.print"):
            factory.return_value.port = 51991
            factory.return_value.poll.return_value = event
            args = ["listen", "--bridge", str(self.paths.root), "--events", "1"]
            self.assertEqual(main(args + ["--port", "0"]), 0)
            self.assertEqual(configured_port(self.paths), 51991)
            self.assertEqual(main(args), 0)
            self.assertEqual([call.kwargs["port"] for call in factory.call_args_list], [0, 51991])
            self.assertEqual(factory.return_value.stop.call_count, 2)

    def test_port_conflict_leaves_existing_configuration_unchanged(self):
        from plumbmate.cli import main
        with EventSocketServer(TOKEN) as other:
            write_endpoint(self.paths, "127.0.0.1", other.port)
            before = endpoint_path(self.paths).read_bytes()
            with mock.patch("plumbmate.cli.load_token", return_value=TOKEN), \
                 mock.patch("builtins.print") as output:
                self.assertEqual(main(["listen", "--bridge", str(self.paths.root)]), 2)
            self.assertEqual(endpoint_path(self.paths).read_bytes(), before)
            self.assertIn("no alternate port", output.call_args.args[0])


class FrameBoundaryTests(unittest.TestCase):
    class Socket:
        def __init__(self, data):
            self.data = data

        def recv(self, size):
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    def test_completed_oversized_frame_cannot_bypass_limit(self):
        stream = _FrameStream(self.Socket(b'{"value":"' + b"x" * 80 + b'"}\n'), 64)
        with self.assertRaises(ProtocolError):
            stream.read_frame()

    def test_limit_includes_newline_and_accepts_exact_boundary(self):
        data = b'{}' + b' ' * 61 + b'\n'
        self.assertEqual(_FrameStream(self.Socket(data), 64).read_frame(), {})
        with self.assertRaises(ProtocolError):
            _FrameStream(self.Socket(data), 63).read_frame()


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = BridgePaths(Path(self.directory.name))
        self.paths.ensure()
        os.environ.pop(TOKEN_ENVIRONMENT_VARIABLE, None)

    def test_missing_token_fails_closed(self):
        with self.assertRaises(AuthenticationError):
            load_token(self.paths)

    def test_short_token_is_refused_everywhere(self):
        short = "a" * (MIN_TOKEN_CHARACTERS - 1)
        for call in (
            lambda: load_token(self.paths, token=short),
            lambda: write_token(self.paths, short),
            lambda: EventSocketServer(short),
            lambda: BridgeClient(short, 1),
        ):
            with self.assertRaises(AuthenticationError):
                call()

    def test_generated_token_is_long_and_unique(self):
        first, second = generate_token(), generate_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), MIN_TOKEN_CHARACTERS)

    def test_written_token_is_owner_only_and_reloadable(self):
        written = write_token(self.paths)
        mode = stat.S_IMODE(token_path(self.paths).stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(load_token(self.paths), written)

    def test_explicit_token_outranks_environment_and_file(self):
        write_token(self.paths, OTHER_TOKEN)
        os.environ[TOKEN_ENVIRONMENT_VARIABLE] = "e" * MIN_TOKEN_CHARACTERS
        self.addCleanup(os.environ.pop, TOKEN_ENVIRONMENT_VARIABLE, None)
        self.assertEqual(load_token(self.paths, token=TOKEN), TOKEN)

    def test_environment_outranks_the_file(self):
        write_token(self.paths, OTHER_TOKEN)
        os.environ[TOKEN_ENVIRONMENT_VARIABLE] = TOKEN
        self.addCleanup(os.environ.pop, TOKEN_ENVIRONMENT_VARIABLE, None)
        self.assertEqual(load_token(self.paths), TOKEN)


class EventValidationTests(unittest.TestCase):
    def test_well_formed_mock_event_passes(self):
        validate_event(mock_event("save_committed", {"slot_id": "Slot_ffffffff"}))

    def test_every_allow_listed_kind_is_accepted(self):
        for kind in ALLOWED_EVENT_KINDS:
            with self.subTest(kind=kind):
                validate_event(mock_event(kind))

    def test_unlisted_kind_and_bad_envelope_are_rejected(self):
        cases = {
            "kind_not_allow_listed": dict(mock_event(), kind="queue_interaction"),
            "unsupported_protocol": dict(mock_event(), protocol_version="9.9"),
            "blank_event_id": dict(mock_event(), event_id="  "),
            "nonstring_event_id": dict(mock_event(), event_id=7),
            "payload_not_object": dict(mock_event(), payload=[1, 2]),
        }
        for name, frame in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ProtocolError):
                    validate_event(frame)

    def test_missing_fields_are_named(self):
        for field in ("protocol_version", "kind", "event_id"):
            with self.subTest(field=field):
                frame = mock_event()
                del frame[field]
                with self.assertRaises(ProtocolError) as caught:
                    validate_event(frame)
                self.assertIn(field, str(caught.exception))


class LoopbackBindingTests(unittest.TestCase):
    def test_non_loopback_host_is_refused_before_binding(self):
        for host in ("0.0.0.0", "192.168.1.10", "example.invalid", ""):
            with self.subTest(host=host):
                with self.assertRaises(SocketBridgeError):
                    EventSocketServer(TOKEN, host=host)
                with self.assertRaises(SocketBridgeError):
                    BridgeClient(TOKEN, 1, host=host)

    def test_listener_binds_loopback_only(self):
        with EventSocketServer(TOKEN) as server:
            self.assertEqual(server.address[0], "127.0.0.1")
            self.assertGreater(server.port, 0)

    def test_port_is_unavailable_before_start(self):
        with self.assertRaises(SocketBridgeError):
            EventSocketServer(TOKEN).port

    def test_double_start_is_refused(self):
        with EventSocketServer(TOKEN) as server:
            with self.assertRaises(SocketBridgeError):
                server.start()


class ServerHarness(unittest.TestCase):
    """Runs a real loopback server; no game, no network, no API key."""

    max_connections = 4
    queue_size = 256

    def setUp(self):
        self.server = EventSocketServer(
            TOKEN, max_connections=self.max_connections, queue_size=self.queue_size,
            handshake_timeout=30.0, idle_timeout=60.0,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    def client(self, token=TOKEN):
        client = BridgeClient(token, self.server.port, timeout=30.0)
        self.addCleanup(client.close)
        return client

    def raw(self):
        connection = socket.create_connection(self.server.address, 30.0)
        connection.settimeout(30.0)
        self.addCleanup(connection.close)
        return connection

    def raw_line(self, connection):
        buffer = bytearray()
        while b"\n" not in buffer:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            buffer.extend(chunk)
        return json.loads(bytes(buffer).split(b"\n", 1)[0].decode("utf-8"))


class AuthenticationTests(ServerHarness):
    def test_valid_proof_is_accepted_and_event_is_queued(self):
        with self.client() as client:
            ack = client.send_event("zone_loaded", {"slot_id": "Slot_00000002"})
        self.assertEqual(ack["kind"], "ack")
        event = self.server.poll(timeout=15.0)
        self.assertEqual(event.kind, "zone_loaded")
        self.assertEqual(event.payload, {"slot_id": "Slot_00000002"})
        self.assertEqual(event.event_id, ack["event_id"])
        self.assertTrue(event.received_at.endswith("+00:00"))

    def test_wrong_token_is_refused(self):
        with self.assertRaises(AuthenticationError):
            self.client(OTHER_TOKEN).connect()
        self.assertEqual(self.server.stats.failed_handshakes, 1)

    def test_unauthenticated_peer_cannot_queue_an_event(self):
        connection = self.raw()
        self.raw_line(connection)
        connection.sendall(json.dumps(mock_event()).encode("utf-8") + b"\n")
        self.assertEqual(self.raw_line(connection)["reason"], "unauthorized")
        self.assertIsNone(self.server.poll(timeout=0.3))
        self.assertEqual(self.server.stats.accepted_events, 0)

    def test_proof_from_one_nonce_cannot_be_replayed_on_the_next(self):
        first = self.raw()
        captured = self.raw_line(first)["nonce"]
        first.sendall(json.dumps({
            "protocol_version": PROTOCOL_VERSION, "kind": "hello",
            "proof": _proof(TOKEN, captured),
        }).encode("utf-8") + b"\n")
        self.assertEqual(self.raw_line(first)["kind"], "welcome")

        second = self.raw()
        second_nonce = self.raw_line(second)["nonce"]
        self.assertNotEqual(second_nonce, captured)
        second.sendall(json.dumps({
            "protocol_version": PROTOCOL_VERSION, "kind": "hello",
            "proof": _proof(TOKEN, captured),
        }).encode("utf-8") + b"\n")
        self.assertEqual(self.raw_line(second)["reason"], "unauthorized")

    def test_token_never_crosses_the_socket(self):
        connection = self.raw()
        nonce = self.raw_line(connection)["nonce"]
        hello = {
            "protocol_version": PROTOCOL_VERSION, "kind": "hello",
            "proof": _proof(TOKEN, nonce),
        }
        self.assertNotIn(TOKEN, json.dumps(hello))
        self.assertNotIn(TOKEN, json.dumps({"nonce": nonce}))

    def test_malformed_handshake_frames_are_refused(self):
        for hello in (
            {"protocol_version": PROTOCOL_VERSION, "kind": "hello"},
            {"protocol_version": PROTOCOL_VERSION, "kind": "hello", "proof": 12},
            {"protocol_version": "9.9", "kind": "hello", "proof": "x"},
            {"protocol_version": PROTOCOL_VERSION, "kind": "save_committed"},
        ):
            with self.subTest(hello=hello):
                connection = self.raw()
                self.raw_line(connection)
                connection.sendall(json.dumps(hello).encode("utf-8") + b"\n")
                self.assertEqual(self.raw_line(connection)["reason"], "unauthorized")


class EventTransportTests(ServerHarness):
    def test_many_events_arrive_in_order_on_one_connection(self):
        sent = []
        with self.client() as client:
            for index in range(20):
                ack = client.send_event("heartbeat", {"index": index})
                sent.append(ack["event_id"])
        received = [event.event_id for event in self._collect(20)]
        self.assertEqual(received, sent)

    def test_several_connections_are_served_concurrently(self):
        clients = [self.client().connect() for _ in range(self.max_connections)]
        for index, client in enumerate(clients):
            client.send_event("heartbeat", {"index": index})
        self.assertEqual(len(self._collect(self.max_connections)), self.max_connections)
        self.assertEqual(self.server.stats.accepted_connections, self.max_connections)

    def test_rejected_event_does_not_close_the_connection(self):
        with self.client() as client:
            error = client.send_event("queue_interaction")
            self.assertEqual(error["kind"], "error")
            self.assertIn("allow-listed", error["reason"])
            self.assertEqual(client.send_event("heartbeat")["kind"], "ack")
        self.assertEqual(self.server.stats.rejected_events, 1)
        self.assertEqual(self.server.stats.accepted_events, 1)

    def test_oversized_and_unparseable_frames_are_refused(self):
        for raw in (b"x" * 70000 + b"\n", b"not json\n", b"[1,2,3]\n"):
            with self.subTest(size=len(raw)):
                connection = self.raw()
                nonce = self.raw_line(connection)["nonce"]
                connection.sendall(json.dumps({
                    "protocol_version": PROTOCOL_VERSION, "kind": "hello",
                    "proof": _proof(TOKEN, nonce),
                }).encode("utf-8") + b"\n")
                self.assertEqual(self.raw_line(connection)["kind"], "welcome")
                connection.sendall(raw)
                self.assertEqual(self.raw_line(connection)["kind"], "error")
        self.assertIsNone(self.server.poll(timeout=0.2))

    def test_drain_returns_everything_then_empties(self):
        with self.client() as client:
            for _ in range(5):
                client.send_event("heartbeat")
        self._collect(5)
        self.assertEqual(self.server.drain(), [])
        self.assertIsNone(self.server.poll())

    def test_send_before_connect_is_refused(self):
        with self.assertRaises(SocketBridgeError):
            BridgeClient(TOKEN, self.server.port).send_event()

    def _collect(self, count, timeout=15.0):
        events = []
        while len(events) < count:
            event = self.server.poll(timeout=timeout)
            if event is None:
                self.fail(f"only received {len(events)} of {count} events")
            events.append(event)
        return events


class BackpressureTests(ServerHarness):
    queue_size = 2

    def test_full_queue_reports_instead_of_dropping_silently(self):
        with self.client() as client:
            for _ in range(self.queue_size):
                self.assertEqual(client.send_event("heartbeat")["kind"], "ack")
            refused = client.send_event("heartbeat")
            self.assertEqual(refused["reason"], "queue_full")
            self.assertEqual(self.server.stats.dropped_events, 1)
            self.assertEqual(len(self.server.drain()), self.queue_size)
            self.assertEqual(client.send_event("heartbeat")["kind"], "ack")


class ConnectionLimitTests(ServerHarness):
    max_connections = 1

    def test_extra_connection_is_refused_without_disturbing_the_first(self):
        first = self.client().connect()
        overflow = BridgeClient(TOKEN, self.server.port, timeout=30.0)
        self.addCleanup(overflow.close)
        with self.assertRaises((SocketBridgeError, OSError, ConnectionError)):
            overflow.connect()
        self.assertEqual(first.send_event("heartbeat")["kind"], "ack")
        self.assertEqual(self.server.stats.rejected_peers, 1)


class LifecycleTests(unittest.TestCase):
    def test_stop_releases_the_port_and_is_idempotent(self):
        server = EventSocketServer(TOKEN)
        server.start()
        port = server.port
        with BridgeClient(TOKEN, port, timeout=30.0) as client:
            client.send_event("heartbeat")
        server.stop()
        server.stop()
        replacement = EventSocketServer(TOKEN, port=port)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.assertEqual(replacement.port, port)


if __name__ == "__main__":
    unittest.main()
