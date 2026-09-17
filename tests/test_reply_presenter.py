"""Presentation contracts without an EA runtime, bridge or provider."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


def load(name):
    path = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_presentation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


presentation = load("reply_presenter")
state_module = load("conversation_state")


class PresenterTests(unittest.TestCase):
    def setUp(self):
        self.state = state_module.ConversationState()
        self.state.sent("request", "binding", 0)
        self.notify, self.watch, self.output = mock.Mock(), mock.Mock(), mock.Mock()
        self.validate = mock.Mock(side_effect=lambda response, _: response)
        self.partner = SimpleNamespace(name="다른 이름의 파트너")
        self.response = {"partner_message": "같이 걷자!", "action": {"name": "suggest_only", "reason": "진단"}}
        self.presenter = presentation.ReplyPresenter(
            self.state, self.watch, self.notify, self.validate, lambda sim: sim.name, str)

    def test_validated_reply_uses_designated_partner_and_stops_watch(self):
        self.presenter.show(self.response, self.partner, self.output)
        self.validate.assert_called_once_with(self.response, "request")
        self.notify.assert_called_once_with(self.partner, "PlumbMate · 다른 이름의 파트너",
                                             "같이 걷자!", portrait=self.partner)
        self.assertEqual(self.state.displayed_id, "request")
        self.watch.stop.assert_called_once_with()
        self.assertIn("Automatic execution is disabled", self.output.call_args.args[0])

    def test_invalid_reply_has_no_display_side_effects(self):
        self.validate.side_effect = ValueError("wrong request")
        with self.assertRaises(ValueError):
            self.presenter.show(self.response, self.partner, self.output)
        self.notify.assert_not_called()
        self.output.assert_not_called()
        self.watch.stop.assert_not_called()
        self.assertIsNone(self.state.displayed_id)

    def test_native_failure_falls_back_once_without_a_second_notification(self):
        self.notify.side_effect = RuntimeError("UI unavailable")
        self.presenter.show(self.response, self.partner, self.output)
        self.notify.assert_called_once()
        self.assertEqual(self.output.call_args_list[0].args, ("같이 걷자!",))
        self.assertEqual(self.state.displayed_id, "request")
        self.watch.stop.assert_called_once()

    def test_both_channels_fail_without_marking_unseen_text_as_displayed(self):
        self.notify.side_effect = RuntimeError("UI unavailable")
        self.output.side_effect = RuntimeError("console unavailable")
        with self.assertRaises(RuntimeError):
            self.presenter.show(self.response, self.partner, self.output)
        self.assertIsNone(self.state.displayed_id)
        self.watch.stop.assert_not_called()

    def test_failure_is_system_notice_not_character_portrait(self):
        owner = object()
        self.presenter.failed(owner, "연결 확인이 필요해요", self.output)
        self.notify.assert_called_once_with(owner, "PlumbMate · 연결 안내", "연결 확인이 필요해요")
        self.assertEqual(self.state.displayed_id, "request")
        self.validate.assert_not_called()

    def test_expiry_preserves_conversation_state_and_mentions_no_retry(self):
        before = dict(vars(self.state))
        self.presenter.expired(self.partner, self.output)
        self.assertEqual(vars(self.state), before)
        self.assertNotIn("portrait", self.notify.call_args.kwargs)
        self.assertIn("자동 재전송하지", self.notify.call_args.args[2])
        self.watch.stop.assert_not_called()

    def test_diagnostics_failure_after_native_delivery_keeps_display_mark(self):
        self.output.side_effect = RuntimeError("console went away")
        with self.assertRaises(RuntimeError):
            self.presenter.show(self.response, self.partner, self.output)
        self.assertEqual(self.state.displayed_id, "request")
        self.watch.stop.assert_called_once()

    def test_no_delivery_watcher_still_supports_manual_presentation(self):
        self.presenter.delivery = None
        self.presenter.show(self.response, self.partner, self.output)
        self.assertEqual(self.state.displayed_id, "request")


if __name__ == "__main__":
    unittest.main()
