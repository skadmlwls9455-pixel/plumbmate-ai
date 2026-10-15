"""Real companion/transport tests. No EA APIs or paid provider calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plumbmate.cli import make_demo_request
from plumbmate.engine import CompanionEngine
from plumbmate.event_socket import EventSocketServer
from plumbmate.protocol import BridgePaths, ProtocolError, atomic_write_json
from plumbmate.providers import MockProvider
from plumbmate.turn_delivery import CAPABILITY, TurnDeliveryReader
from plumbmate.turn_status import write_turn_status
from test_bridge_client import bridge, pump_until, TOKEN


class TurnDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = BridgePaths(Path(temporary.name))
        self.request = make_demo_request("오늘 같이 산책할까? 🐙")
        self.scope = {"request_id": self.request["request_id"],
                      **{key: self.request["roles"][key] for key in ("binding_id", "scene_id")}}
        self.reader = TurnDeliveryReader(self.paths)
        atomic_write_json(self.paths.requests / (self.request["request_id"] + ".json"), self.request)

    def complete(self):
        provider = mock.Mock(wraps=MockProvider())
        CompanionEngine(self.paths, provider).process_one()
        return provider

    def connect(self, reader):
        server = EventSocketServer(TOKEN, delivery_reader=reader)
        server.start()
        self.addCleanup(server.stop)
        client = bridge.BridgeClient(TOKEN, server.port)
        self.addCleanup(client.close)
        self.assertTrue(pump_until(client, lambda: client.connected))
        return server, client

    def test_projection_preserves_korean_but_excludes_memory_and_budget(self):
        self.assertEqual(self.reader(self.scope)["phase"], "pending")
        provider = self.complete()
        result = self.reader(self.scope)
        self.assertEqual(result["phase"], "completed")
        self.assertIn("산책할까? 🐙", result["response"]["partner_message"])
        self.assertEqual(set(result["response"]), {"protocol_version", "request_id", "partner_message", "action"})
        for _ in range(10):
            self.reader(self.scope)
        provider.respond.assert_called_once()

    def test_real_socket_roundtrip_does_not_fill_event_queue_or_call_provider(self):
        provider = self.complete()
        server, client = self.connect(self.reader)
        self.assertTrue(client.supports_turn_delivery)
        self.assertIsNotNone(client.request_turn_delivery(self.scope))
        self.assertIsNone(client.request_turn_delivery(self.scope))
        self.assertTrue(pump_until(client, lambda: client._delivery_reply is not None))
        reply = client.take_turn_delivery()
        self.assertEqual(reply["request_id"], self.request["request_id"])
        self.assertEqual(reply["phase"], "completed")
        self.assertIsNone(client.take_turn_delivery())
        self.assertEqual(server.drain(), [])
        provider.respond.assert_called_once()

    def test_old_server_negotiates_manual_fallback(self):
        _, client = self.connect(None)
        self.assertFalse(client.supports_turn_delivery)
        self.assertIsNone(client.request_turn_delivery(self.scope))
        self.assertEqual(client.queued, 0)

    def test_delivery_contract_capability_matches_game_client(self):
        self.assertEqual(CAPABILITY, bridge.DELIVERY_CAPABILITY)

    def test_failed_provider_polling_never_retries_generation(self):
        provider = mock.Mock()
        provider.respond.side_effect = TimeoutError("uncertain provider result")
        engine = CompanionEngine(self.paths, provider)
        with self.assertRaises(ProtocolError):
            engine.process_one()
        for _ in range(10):
            result = self.reader(self.scope)
            self.assertEqual(result["reason_code"], "provider_failed")
            self.assertNotIn("response", result)
        self.assertIsNone(engine.process_one())
        provider.respond.assert_called_once()

    def test_malformed_delivery_does_not_poison_connection_or_event_queue(self):
        server, client = self.connect(self.reader)
        client.request_turn_delivery(dict(self.scope, request_id="../escape"))
        self.assertTrue(pump_until(client, lambda: client.stats["refused_events"] == 1))
        self.assertIsNone(client.take_turn_delivery())
        self.assertTrue(client.connected)
        event = client.queue_event("heartbeat", {})
        self.assertTrue(pump_until(client, lambda: client.stats["acked_events"] == 1))
        self.assertEqual([item.event_id for item in server.drain()], [event])

    def test_malformed_failure_reason_cannot_break_reader(self):
        atomic_write_json(self.paths.state / ("turn-" + self.scope["request_id"] + ".json"),
                          {"request_id": self.scope["request_id"], "phase": "failed", "reason_code": []})
        self.assertEqual(self.reader(self.scope)["phase"], "pending")

    def test_rate_limit_does_not_repeat_disk_reads(self):
        patch = mock.patch("plumbmate.event_socket.DELIVERY_MIN_INTERVAL_SECONDS", 60.0)
        patch.start()
        self.addCleanup(patch.stop)
        reader = mock.Mock(return_value=dict(self.scope, phase="pending"))
        _, client = self.connect(reader)
        client.request_turn_delivery(self.scope)
        self.assertTrue(pump_until(client, lambda: client._delivery_reply is not None))
        client.take_turn_delivery()
        client.request_turn_delivery(self.scope)
        self.assertTrue(pump_until(client, lambda: client.stats["refused_events"] == 1))
        reader.assert_called_once()
        self.assertTrue(client.connected)

    def test_scope_id_and_symlink_fences(self):
        self.complete()
        for key in self.scope:
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                self.reader(dict(self.scope, **{key: "../escape"}))
        for key in ("binding_id", "scene_id"):
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                self.reader(dict(self.scope, **{key: "f" * 32}))
        original = self.paths.archive / (self.request["request_id"] + ".json")
        moved = original.with_suffix(".original")
        original.rename(moved)
        original.symlink_to(moved)
        with self.assertRaises(ProtocolError):
            self.reader(self.scope)

    def test_orphan_response_never_served_and_bad_response_never_trusted(self):
        self.complete()
        original = self.paths.archive / (self.request["request_id"] + ".json")
        original.rename(original.with_suffix(".unused"))
        self.assertEqual(self.reader(self.scope)["phase"], "pending")
        atomic_write_json(original, self.request)
        path = self.paths.responses / (self.request["request_id"] + ".json")
        atomic_write_json(path, {"request_id": self.request["request_id"]})
        with self.assertRaises(ProtocolError):
            self.reader(self.scope)

    def test_failed_receipt_is_generic_and_does_not_require_quarantined_request(self):
        path = self.paths.requests / (self.request["request_id"] + ".json")
        path.rename(path.with_suffix(".quarantined"))
        write_turn_status(self.paths, self.request["request_id"], "failed", "spending_blocked")
        self.assertEqual(self.reader(self.scope), dict(self.scope, phase="failed", reason_code="spending_blocked"))

    def test_unsolicited_ack_and_disconnect_cannot_leave_reply_inbox(self):
        client = bridge.BridgeClient(TOKEN, 1)
        client.state = bridge.READY
        client.supports_turn_delivery = True
        event_id = client.request_turn_delivery(self.scope)
        frame = {"kind": "ack", "event_id": event_id, "delivery": dict(self.scope, phase="completed")}
        # ACK before send is not proof of delivery.
        client._handle_frame(json.dumps(frame).encode())
        self.assertIsNone(client.take_turn_delivery())
        client._encode_outbox()
        client._handle_frame(json.dumps(frame).encode())
        self.assertIsNotNone(client._delivery_reply)
        client._fail("test disconnect")
        self.assertIsNone(client.take_turn_delivery())
        self.assertFalse(client.supports_turn_delivery)
        client.state = bridge.READY
        client.supports_turn_delivery = True
        client.request_turn_delivery(self.scope)
        client.close()
        self.assertEqual(client.queued, 0)


if __name__ == "__main__":
    unittest.main()
