"""Owned launcher lifecycle and native callback contracts, not live UX proof."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class ControlTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/control_ui.py"
        localization = types.SimpleNamespace(_text=str)
        with mock.patch.dict(sys.modules, {"plumbmate_role_setup_ui": localization}):
            spec = importlib.util.spec_from_file_location("control_ui_test", source)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.now = 0.0
        self.zone, self.household, self.owner = object(), object(), object()
        self.members = [self.owner]
        self.cancel = mock.Mock()
        self.make = mock.Mock(return_value=self.cancel)
        self.activate = mock.Mock()
        self.ui = self.module.ControlUI(lambda: self.now, self.make)

    def environment(self):
        return self.zone, self.household, self.members, self.owner

    def pulse(self):
        self.ui.pulse(self.environment, self.activate)

    def test_one_launcher_per_scope_and_reusable_action(self):
        self.pulse()
        for _ in range(100):
            self.now += 5
            self.pulse()
        self.make.assert_called_once()
        callback = self.make.call_args.args[1]
        callback()
        callback()
        self.assertEqual(self.activate.call_count, 2)

    def test_environment_lookup_is_throttled_before_reading_game_state(self):
        environment = mock.Mock(side_effect=self.environment)
        for _ in range(1000):
            self.ui.pulse(environment, self.activate)
        environment.assert_called_once()

    def test_zero_sims_and_main_menu_never_make_launcher(self):
        self.members = []
        self.pulse()
        self.make.assert_not_called()
        self.now += 5
        self.members, self.owner = [object()], None
        self.pulse()
        self.make.assert_not_called()

    def test_transition_cancels_only_owned_launcher_and_rejects_old_button(self):
        self.pulse()
        old = self.make.call_args.args[1]
        self.zone = object()
        old()
        self.activate.assert_not_called()
        self.now += 5
        self.pulse()
        self.cancel.assert_called_once()
        old()
        self.activate.assert_not_called()
        self.assertEqual(self.make.call_count, 2)

    def test_stop_closes_once_and_old_callback_cannot_reopen(self):
        self.pulse()
        old = self.make.call_args.args[1]
        self.ui.close()
        self.ui.close()
        old()
        self.cancel.assert_called_once()
        self.activate.assert_not_called()

    def test_three_attempt_limit_bounds_ui_failures(self):
        self.make.side_effect = RuntimeError("unavailable")
        for _ in range(100):
            self.pulse()
            self.now += 5
        self.assertEqual(self.make.call_count, 3)
        self.assertEqual(self.ui.last_error, "RuntimeError")
        self.zone = object()
        self.pulse()
        self.assertEqual(self.make.call_count, 4)

    def native(self):
        self.dialog = mock.Mock()
        native = types.SimpleNamespace(
            ButtonType=types.SimpleNamespace(DIALOG_RESPONSE_OK=1, DIALOG_RESPONSE_CUSTOM_1=10),
            UiDialogResponse=lambda **kw: types.SimpleNamespace(**kw),
            UiDialogOkCancel=types.SimpleNamespace(TunableFactory=lambda: types.SimpleNamespace(default=lambda **kw: self.dialog)))
        return native

    def open(self):
        self.chat, self.setup, self.output = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.dict(sys.modules, {"ui.ui_dialog": self.native()}):
            self.ui.open(self.environment, "내가 맡을 심 / AI 심", self.chat, self.setup, self.output)
        return self.dialog.show_dialog.call_args.kwargs["on_response"]

    def test_exact_buttons_route_without_cancel_becoming_role_change(self):
        for response in (None, -1, 0, 2, 999):
            callback = self.open()
            callback(types.SimpleNamespace(response=response))
            self.chat.assert_not_called()
            self.setup.assert_not_called()
        callback = self.open()
        callback(types.SimpleNamespace(response=1))
        callback(types.SimpleNamespace(response=1))
        self.chat.assert_called_once()
        callback = self.open()
        callback(types.SimpleNamespace(response=10))
        self.setup.assert_called_once()

    def test_old_menu_and_changed_household_cannot_act(self):
        old = self.open()
        current = self.open()
        old(types.SimpleNamespace(response=1))
        self.chat.assert_not_called()
        self.household = object()
        current(types.SimpleNamespace(response=10))
        self.setup.assert_not_called()

    def test_native_launcher_keeps_only_its_own_button_registered(self):
        native = self.native()
        instances = []

        class Notification:
            @classmethod
            def TunableFactory(cls):
                return types.SimpleNamespace(default=cls)

            def __init__(self, **kwargs):
                self.dialog_id = 17
                instances.append(self)

            def set_responses(self, responses):
                self.responses = responses

            def show_dialog(self):
                pass

            def respond(self, response):
                return True

        service = mock.Mock()
        service.get_dialog.side_effect = lambda _: instances[-1]
        with mock.patch.dict(sys.modules, {
                "ui.ui_dialog": native,
                "ui.ui_dialog_notification": types.SimpleNamespace(UiDialogNotification=Notification),
                "services": types.SimpleNamespace(ui_dialog_service=lambda: service)}):
            close = self.module._launcher(self.owner, self.activate)
        self.assertFalse(instances[0].respond(10))
        self.assertFalse(instances[0].respond(10))
        self.assertTrue(instances[0].respond(-1))
        self.assertEqual(self.activate.call_count, 2)
        close()
        service.dialog_cancel.assert_called_once_with(17)
        self.assertIs(Notification.respond, Notification.__dict__["respond"])

    def test_old_button_stays_invalid_after_stop_and_restart_in_same_household(self):
        self.pulse()
        old = self.make.call_args.args[1]
        self.ui.close()
        self.now += 5
        self.pulse()
        old()
        self.activate.assert_not_called()
        self.make.call_args.args[1]()
        self.activate.assert_called_once()

    def test_home_only_describes_roles_and_receipt_until_user_selects(self):
        chat, setup, notify, output = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(self.ui, "open") as opened:
            self.ui.open_home(self.environment, lambda: ("Player", "Partner", "전송 요청을 남겼어요"),
                              chat, setup, output, notify)
        summary = opened.call_args.args[1]
        self.assertIn("내가 맡을 심: Player", summary)
        self.assertIn("AI가 맡을 심: Partner", summary)
        self.assertIn("전송 요청", summary)
        chat.assert_not_called()
        setup.assert_not_called()
        notify.assert_not_called()
        opened.call_args.args[2]()
        chat.assert_called_once_with()

    def test_home_missing_roles_offers_setup_without_inventing_a_partner(self):
        with mock.patch.object(self.ui, "open") as opened:
            self.ui.open_home(self.environment, mock.Mock(side_effect=ValueError()),
                              mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())
        self.assertIn("서로 다른 두 심", opened.call_args.args[1])
        self.assertNotIn("에이든", opened.call_args.args[1])

    def test_home_error_report_falls_back_to_supplied_output(self):
        output, notify = mock.Mock(), mock.Mock(side_effect=RuntimeError())
        with mock.patch.object(self.ui, "open") as opened:
            self.ui.open_home(self.environment, lambda: ("P", "A", ""),
                              mock.Mock(), mock.Mock(), output, notify)
        opened.call_args.args[4]("안내")
        notify.assert_called_once_with(self.owner, "PlumbMate · 안내", "안내")
        output.assert_called_once_with("안내")


if __name__ == "__main__":
    unittest.main()
