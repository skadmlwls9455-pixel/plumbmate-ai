"""Deterministic game-side watch tests: no EA modules, socket or filesystem."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from test_bridge_client import Clock

source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/reply_delivery.py"
spec = importlib.util.spec_from_file_location("reply_delivery_test", source)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class ReplyWatchTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.watch = delivery.ReplyDelivery(now=self.clock)
        self.watch.start("a" * 32, "b" * 32, "c" * 32, 7, self.clock())
        self.scope = dict(self.watch.scope)
        self.client = mock.Mock(supports_turn_delivery=True)
        self.client.take_turn_delivery.return_value = None
        self.context = mock.Mock(return_value=("b" * 32, "c" * 32))
        self.display = mock.Mock()

    def step(self):
        self.watch.advance(self.client, self.context, self.display)

    def test_idle_and_expired_watches_do_no_work(self):
        self.clock.advance(180)
        self.step()
        self.context.assert_not_called()
        self.client.request_turn_delivery.assert_not_called()
        self.assertEqual(self.watch.state, "expired")
        self.step()
        self.context.assert_not_called()

    def test_polling_rate_is_real_time_not_game_ticks(self):
        self.step()
        for _ in range(10000):
            self.step()
        self.context.assert_called_once()
        self.client.request_turn_delivery.assert_called_once_with(self.scope)
        self.clock.advance(1)
        self.step()
        self.assertEqual(self.client.request_turn_delivery.call_count, 2)

    def test_context_transition_cancels_even_while_offline(self):
        for mode in ("different_scope", "missing_sim"):
            self.setUp()
            self.client.supports_turn_delivery = False
            if mode == "different_scope":
                self.context.return_value = ("d" * 32, "c" * 32)
            else:
                self.context.side_effect = ValueError("off lot")
            self.step()
            self.assertEqual(self.watch.state, "context_changed")
            self.client.request_turn_delivery.assert_not_called()

    def test_old_scope_or_wrong_request_is_never_displayed(self):
        for key in self.scope:
            self.setUp()
            self.client.take_turn_delivery.return_value = dict(self.scope, phase="completed", **{key: "f" * 32})
            self.step()
            self.display.assert_not_called()
            self.assertIsNotNone(self.watch.scope)

    def test_terminal_result_delivered_once_even_with_reentrant_callback(self):
        for phase in ("completed", "failed"):
            self.setUp()
            self.client.take_turn_delivery.return_value = dict(self.scope, phase=phase)
            self.display.side_effect = lambda _: self.step()
            self.step()
            self.clock.advance(1)
            self.step()
            self.display.assert_called_once()
            self.client.request_turn_delivery.assert_not_called()

    def test_failed_display_is_not_retried_on_every_tick(self):
        self.client.take_turn_delivery.return_value = dict(self.scope, phase="completed")
        self.display.side_effect = RuntimeError("native UI unavailable")
        self.step()
        self.clock.advance(1)
        self.step()
        self.assertEqual(self.watch.state, "display_failed")
        self.display.assert_called_once()

    def test_new_request_replaces_old_without_history_growth(self):
        self.watch.start("d" * 32, "b" * 32, "c" * 32, 8, self.clock())
        self.client.take_turn_delivery.return_value = dict(self.scope, phase="completed")
        self.step()
        self.display.assert_not_called()
        self.assertEqual(self.watch.connection, 8)
        self.assertEqual(self.watch.scope["request_id"], "d" * 32)
