import errno
import importlib.util
import json
import socket
import time
import unittest
from pathlib import Path

from plumbmate.event_socket import ALLOWED_EVENT_KINDS as SERVER_EVENT_KINDS
from plumbmate.event_socket import MIN_TOKEN_CHARACTERS, EventSocketServer

source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/bridge_client.py"
spec = importlib.util.spec_from_file_location("bridge_client_under_test", source)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


TOKEN = "t" * MIN_TOKEN_CHARACTERS
OTHER_TOKEN = "u" * MIN_TOKEN_CHARACTERS


class Clock(object):
    """Deterministic monotonic clock; the client never sleeps on its own."""

    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def pump_until(client, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.pump()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def closed_port():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class ConstructionTests(unittest.TestCase):
    def test_short_token_is_refused(self):
        with self.assertRaises(ValueError):
            bridge.BridgeClient("a" * (MIN_TOKEN_CHARACTERS - 1), 1)

    def test_non_loopback_host_is_refused(self):
        # '١٢٧.0.0.1' uses Arabic-Indic digits: isdigit()/int() accept them,
        # but the socket layer would IDNA-encode the host and do a blocking
        # DNS lookup on the engine thread — exactly what this guard forbids.
        for host in (
            "0.0.0.0", "192.168.1.10", "example.invalid", "", "::1", "127.0.0",
            "١٢٧.0.0.1",
        ):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    bridge.BridgeClient(TOKEN, 1, host=host)

    def test_loopback_range_is_accepted(self):
        for host in ("127.0.0.1", "127.0.0.2", "127.1.2.3"):
            with self.subTest(host=host):
                self.assertEqual(bridge.BridgeClient(TOKEN, 1, host=host).state, "DISCONNECTED")

    def test_allow_list_matches_the_companion_exactly(self):
        self.assertEqual(bridge.ALLOWED_EVENT_KINDS, SERVER_EVENT_KINDS)

    def test_protocol_version_matches_the_companion(self):
        from plumbmate.protocol import PROTOCOL_VERSION
        self.assertEqual(bridge.PROTOCOL_VERSION, PROTOCOL_VERSION)


class BuildEventTests(unittest.TestCase):
    def test_unlisted_kind_and_bad_payload_are_refused(self):
        with self.assertRaises(ValueError):
            bridge.build_event("queue_interaction")
        with self.assertRaises(ValueError):
            bridge.build_event("heartbeat", ["not", "a", "dict"])

    def test_event_ids_are_unique_and_payload_is_preserved(self):
        first = bridge.build_event("zone_loaded", {"slot_id": "Slot_ffffffff"})
        second = bridge.build_event("zone_loaded", {"slot_id": "Slot_ffffffff"})
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["payload"], {"slot_id": "Slot_ffffffff"})


class OfflineStateMachineTests(unittest.TestCase):
    """No server at all; the client must degrade quietly and never raise."""

    def setUp(self):
        self.clock = Clock()
        self.port = closed_port()
        self.client = bridge.BridgeClient(TOKEN, self.port, now=self.clock)
        self.addCleanup(self.client.close)

    def test_refused_connection_enters_backoff_without_raising(self):
        self.assertTrue(
            pump_until(self.client, lambda: self.client.state == "BACKOFF"),
            "never entered backoff: state={} error={}".format(
                self.client.state, self.client.last_error),
        )
        self.assertGreaterEqual(self.client.stats["failures"], 1)
        self.assertIsNotNone(self.client.last_error)

    def test_backoff_grows_and_is_honoured_by_the_clock(self):
        delays = []
        for _ in range(4):
            self.assertTrue(pump_until(self.client, lambda: self.client.state == "BACKOFF"))
            delays.append(self.client.backoff_seconds)
            attempts = self.client.stats["connect_attempts"]
            self.client.pump()
            self.assertEqual(self.client.stats["connect_attempts"], attempts)
            self.clock.advance(self.client.backoff_seconds)
            self.client.pump()
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0])

    def test_backoff_is_capped(self):
        self.client._backoff = 1000.0
        self.client._fail("synthetic")
        self.assertEqual(self.client.backoff_seconds, bridge.MAX_BACKOFF_SECONDS)

    def test_pump_never_raises_even_on_a_broken_socket(self):
        class Exploding(object):
            def recv(self, _size):
                raise RuntimeError("engine would have seen this")

            def getpeername(self):
                return ("127.0.0.1", 1)

            def close(self):
                pass

        self.client._socket = Exploding()
        self.client.state = "AWAITING_CHALLENGE"
        self.client._deadline = self.clock.value + 100
        self.assertEqual(self.client.pump(), "BACKOFF")
        self.assertIn("RuntimeError", self.client.last_error)

    def test_pump_returns_promptly_without_a_server(self):
        started = time.monotonic()
        for _ in range(50):
            self.client.pump()
            self.clock.advance(60.0)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_events_queue_while_offline_and_are_not_lost(self):
        for index in range(5):
            self.assertIsNotNone(self.client.queue_event("heartbeat", {"index": index}))
        for _ in range(6):
            self.client.pump()
            self.clock.advance(60.0)
        self.assertEqual(self.client.queued, 5)
        self.assertEqual(self.client.stats["lost_events"], 0)

    def test_full_outbox_refuses_instead_of_growing(self):
        client = bridge.BridgeClient(TOKEN, self.port, now=self.clock, max_outbox=3)
        self.addCleanup(client.close)
        accepted = [client.queue_event("heartbeat") for _ in range(5)]
        self.assertEqual(len([item for item in accepted if item]), 3)
        self.assertEqual(client.queued, 3)
        self.assertEqual(client.stats["rejected_by_outbox"], 2)

    def test_oversized_event_is_refused_at_queue_time(self):
        with self.assertRaises(ValueError):
            self.client.queue_event("chat_turn", {"text": "x" * (bridge.MAX_FRAME_BYTES + 1)})
        self.assertEqual(self.client.queued, 0)


class FrameBurstTests(unittest.TestCase):
    """Regression for the buffered-frame starvation bug.

    When one recv returns more frames than a single pump's budget, the surplus
    sits in the client's inbound buffer. Later pumps must drain that buffer
    even though the socket has nothing new to offer — before the fix, the
    EAGAIN on the next recv returned early and the buffered acks were never
    parsed, wedging the client in READY with acks outstanding forever.
    """

    def test_buffered_frames_drain_without_new_bytes(self):
        client = bridge.BridgeClient(TOKEN, 1)
        self.addCleanup(client.close)
        total = bridge.MAX_FRAMES_PER_PUMP + 8
        event_ids = ["event-{}".format(index) for index in range(total)]
        burst = b"".join(
            json.dumps({
                "protocol_version": bridge.PROTOCOL_VERSION,
                "kind": "ack",
                "event_id": event_id,
            }).encode("utf-8") + b"\n"
            for event_id in event_ids
        )
        self.assertLessEqual(len(burst), 4096, "burst must fit one recv call")

        class OneBurstSocket(object):
            def __init__(self):
                self.chunks = [burst]

            def recv(self, _size):
                if self.chunks:
                    return self.chunks.pop(0)
                raise OSError(errno.EAGAIN, "no new bytes on the wire")

            def send(self, data):
                return len(data)

            def close(self):
                pass

        client._socket = OneBurstSocket()
        client.state = bridge.READY
        client._pending = list(event_ids)

        client.pump()
        self.assertEqual(client.stats["acked_events"], bridge.MAX_FRAMES_PER_PUMP)
        client.pump()
        self.assertEqual(client.stats["acked_events"], total)
        self.assertEqual(client.awaiting_ack, 0)
        self.assertEqual(client.state, "READY")


class KeepaliveTests(unittest.TestCase):
    """An idle READY connection must outlive the companion's idle timeout."""

    def _ready_client(self, **kwargs):
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        clock = Clock()
        client = bridge.BridgeClient(
            TOKEN, server.port, now=clock,
            connect_timeout=30.0, handshake_timeout=30.0, **kwargs,
        )
        self.addCleanup(client.close)
        self.assertTrue(pump_until(client, lambda: client.connected))
        return server, clock, client

    def test_idle_ready_connection_sends_a_keepalive_heartbeat(self):
        server, clock, client = self._ready_client()
        self.assertEqual(client.stats["queued_events"], 0)
        clock.advance(bridge.KEEPALIVE_INTERVAL_SECONDS + 1.0)
        self.assertTrue(
            pump_until(client, lambda: client.stats["acked_events"] == 1),
            "keepalive was never sent or acked: {}".format(client.last_error),
        )
        event = server.poll(timeout=15.0)
        self.assertEqual(event.kind, "heartbeat")
        self.assertEqual(event.payload, {"source": "keepalive"})

    def test_zero_interval_disables_keepalives(self):
        _server, clock, client = self._ready_client(keepalive_interval=0)
        clock.advance(10_000.0)
        for _ in range(20):
            client.pump()
            time.sleep(0.005)
        self.assertEqual(client.stats["queued_events"], 0)
        self.assertTrue(client.connected)


class ResolveStatsTests(unittest.TestCase):
    """Acks for events that were never pending must not inflate delivery stats."""

    def test_a_fabricated_ack_counts_as_an_unknown_frame(self):
        client = bridge.BridgeClient(TOKEN, 1)
        self.addCleanup(client.close)
        frame = json.dumps({
            "protocol_version": bridge.PROTOCOL_VERSION,
            "kind": "ack", "event_id": "never-sent",
        }).encode("utf-8") + b"\n"

        class OneFrameSocket(object):
            def __init__(self):
                self.chunks = [frame]

            def recv(self, _size):
                if self.chunks:
                    return self.chunks.pop(0)
                raise OSError(errno.EAGAIN, "drained")

            def send(self, data):
                return len(data)

            def close(self):
                pass

        client._socket = OneFrameSocket()
        client.state = bridge.READY
        client.pump()
        self.assertEqual(client.stats["acked_events"], 0)
        self.assertEqual(client.stats["unknown_frames"], 1)


class AdoptOutboxTests(unittest.TestCase):
    """A replacement client takes over the old client's unsent events."""

    def test_unsent_events_move_to_the_replacement(self):
        old = bridge.BridgeClient(TOKEN, closed_port())
        new = bridge.BridgeClient(TOKEN, closed_port())
        self.addCleanup(old.close)
        self.addCleanup(new.close)
        for index in range(3):
            self.assertIsNotNone(old.queue_event("heartbeat", {"index": index}))
        self.assertEqual(new.adopt_outbox(old), 3)
        self.assertEqual(old.queued, 0)
        self.assertEqual(new.queued, 3)

    def test_overflow_stays_behind_and_is_counted(self):
        old = bridge.BridgeClient(TOKEN, closed_port())
        new = bridge.BridgeClient(TOKEN, closed_port(), max_outbox=2)
        self.addCleanup(old.close)
        self.addCleanup(new.close)
        for index in range(5):
            old.queue_event("heartbeat", {"index": index})
        self.assertEqual(new.adopt_outbox(old), 2)
        self.assertEqual(old.queued, 3)
        self.assertEqual(new.queued, 2)
        self.assertEqual(new.stats["rejected_by_outbox"], 3)

    def test_adopted_events_reach_the_server_in_order(self):
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        old = bridge.BridgeClient(TOKEN, closed_port())
        self.addCleanup(old.close)
        ids = [old.queue_event("heartbeat", {"index": index}) for index in range(3)]
        new = bridge.BridgeClient(
            TOKEN, server.port, connect_timeout=30.0, handshake_timeout=30.0,
        )
        self.addCleanup(new.close)
        self.assertEqual(new.adopt_outbox(old), 3)
        self.assertTrue(
            pump_until(new, lambda: new.stats["acked_events"] == 3),
            "adopted events were never acked: {}".format(new.last_error),
        )
        received = [server.poll(timeout=15.0).event_id for _ in range(3)]
        self.assertEqual(received, ids)


class InteropHarness(unittest.TestCase):
    """Drives the real companion server; no game and no API key involved."""

    queue_size = 256

    def setUp(self):
        self.server = EventSocketServer(TOKEN, queue_size=self.queue_size, idle_timeout=60.0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = bridge.BridgeClient(
            TOKEN, self.server.port, connect_timeout=30.0, handshake_timeout=30.0,
        )
        self.addCleanup(self.client.close)

    def ready(self):
        self.assertTrue(
            pump_until(self.client, lambda: self.client.connected),
            "client never reached READY: {}".format(self.client.last_error),
        )


class InteropTests(InteropHarness):
    def test_handshake_reaches_ready(self):
        self.ready()
        self.assertEqual(self.client.stats["handshakes"], 1)
        self.assertEqual(self.server.stats.accepted_connections, 1)
        self.assertEqual(self.server.stats.failed_handshakes, 0)

    def test_event_round_trip_carries_payload_and_is_acked(self):
        self.ready()
        payload = {"slot_id": "Slot_ffffffff.save", "메모": "한글 페이로드"}
        event_id = self.client.queue_event("zone_loaded", payload)
        self.assertTrue(pump_until(self.client, lambda: self.client.stats["acked_events"] == 1))
        event = self.server.poll(timeout=15.0)
        self.assertEqual(event.event_id, event_id)
        self.assertEqual(event.kind, "zone_loaded")
        self.assertEqual(event.payload, payload)
        self.assertEqual(self.client.awaiting_ack, 0)

    def test_many_events_are_all_acked_in_order(self):
        self.ready()
        ids = [self.client.queue_event("heartbeat", {"index": i}) for i in range(30)]
        self.assertTrue(
            pump_until(self.client, lambda: self.client.stats["acked_events"] == 30, timeout=20.0)
        )
        received = [self.server.poll(timeout=15.0).event_id for _ in range(30)]
        self.assertEqual(received, ids)
        self.assertEqual(self.client.stats["lost_events"], 0)

    def test_wrong_token_never_reaches_ready(self):
        wrong = bridge.BridgeClient(
            OTHER_TOKEN, self.server.port, connect_timeout=30.0, handshake_timeout=30.0,
        )
        self.addCleanup(wrong.close)
        self.assertTrue(pump_until(wrong, lambda: wrong.state == "BACKOFF"))
        self.assertFalse(wrong.connected)
        self.assertEqual(wrong.last_error, "unauthorized")
        self.assertEqual(self.server.stats.failed_handshakes, 1)

    def test_token_is_never_written_to_the_socket(self):
        captured = []
        real_send = socket.socket.send

        def recording_send(self_socket, data, *args):
            captured.append(bytes(data))
            return real_send(self_socket, data, *args)

        socket.socket.send = recording_send
        self.addCleanup(setattr, socket.socket, "send", real_send)
        self.ready()
        self.client.queue_event("heartbeat")
        pump_until(self.client, lambda: self.client.stats["acked_events"] == 1)
        self.assertTrue(captured)
        self.assertNotIn(TOKEN.encode("utf-8"), b"".join(captured))

    def test_server_shutdown_is_detected_and_in_flight_events_are_counted_lost(self):
        self.ready()
        self.client.queue_event("heartbeat")
        self.client.pump()
        self.server.stop()
        self.assertTrue(pump_until(self.client, lambda: self.client.state == "BACKOFF"))
        self.assertFalse(self.client.connected)
        self.assertGreaterEqual(self.client.stats["failures"], 1)
        self.assertEqual(
            self.client.stats["lost_events"] + self.client.stats["acked_events"], 1
        )

    def test_client_reconnects_after_the_companion_restarts(self):
        self.ready()
        port = self.server.port
        self.server.stop()
        self.assertTrue(pump_until(self.client, lambda: self.client.state == "BACKOFF"))
        self.client.queue_event("save_committed", {"slot_id": "Slot_00000002"})

        replacement = EventSocketServer(TOKEN, port=port, idle_timeout=60.0)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.assertTrue(
            pump_until(self.client, lambda: self.client.stats["acked_events"] == 1, timeout=25.0)
        )
        self.assertEqual(self.client.stats["handshakes"], 2)
        self.assertEqual(replacement.poll(timeout=15.0).kind, "save_committed")


class BackpressureInteropTests(InteropHarness):
    queue_size = 2

    def test_refused_event_is_counted_and_not_resent(self):
        self.ready()
        for _ in range(4):
            self.client.queue_event("heartbeat")
        self.assertTrue(
            pump_until(
                self.client,
                lambda: self.client.stats["acked_events"] + self.client.stats["refused_events"] == 4,
                timeout=20.0,
            )
        )
        self.assertEqual(self.client.stats["acked_events"], self.queue_size)
        self.assertEqual(self.client.stats["refused_events"], 2)
        self.assertEqual(self.client.last_error, "queue_full")
        self.assertTrue(self.client.connected)
        self.assertEqual(self.client.queued, 0)
        self.assertEqual(self.client.awaiting_ack, 0)


if __name__ == "__main__":
    unittest.main()
