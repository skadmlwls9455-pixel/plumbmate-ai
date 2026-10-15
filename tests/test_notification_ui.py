"""Notification portrait routing, not proof of native portrait rendering."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/role_setup_ui.py"


class NotificationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("notification_adapter_test", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.dialog = mock.Mock()
        self.factory = mock.Mock(return_value=self.dialog)
        self.icon = mock.Mock(side_effect=lambda **kwargs: types.SimpleNamespace(**kwargs))
        notification = types.ModuleType("ui.ui_dialog_notification")
        notification.UiDialogNotification = types.SimpleNamespace(
            TunableFactory=lambda: types.SimpleNamespace(default=self.factory))
        shared = types.ModuleType("distributor.shared_messages")
        shared.IconInfoData = self.icon
        patch = mock.patch.dict(sys.modules, {
            "ui": types.ModuleType("ui"), "ui.ui_dialog_notification": notification,
            "distributor": types.ModuleType("distributor"), "distributor.shared_messages": shared})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(self.module, "_text", side_effect=str)
        patch.start()
        self.addCleanup(patch.stop)

    def test_speaker_not_owner_is_explicit_native_icon(self):
        owner = object()
        speaker = types.SimpleNamespace(icon_info=(42, 7))
        self.module.notify(owner, "파트너", "안녕", portrait=speaker)
        self.assertIs(self.factory.call_args.kwargs["owner"], owner)
        self.icon.assert_called_once_with(obj_instance=speaker)
        self.dialog.show_dialog.assert_called_once()
        self.assertIs(self.dialog.show_dialog.call_args.kwargs["icon_override"].obj_instance, speaker)

    def test_system_notice_does_not_automatically_impersonate_owner(self):
        self.module.notify(object(), "PlumbMate · 연결 안내", "연결이 필요해요")
        self.icon.assert_not_called()
        self.dialog.show_dialog.assert_called_once_with()

    def test_unavailable_portrait_preserves_text_once(self):
        for speaker in (object(), types.SimpleNamespace(icon_info=None),
                        types.SimpleNamespace(icon_info=(42, None))):
            self.dialog.reset_mock()
            with self.subTest(speaker=speaker):
                self.module.notify(object(), "파트너", "안녕", portrait=speaker)
                self.dialog.show_dialog.assert_called_once_with()
                self.assertEqual(self.factory.call_args.kwargs["text"](), "안녕")

    def test_icon_construction_failure_is_not_a_dialogue_failure(self):
        self.icon.side_effect = RuntimeError("unsupported icon")
        self.module.notify(object(), "파트너", "안녕", portrait=types.SimpleNamespace(icon_info=(1, 2)))
        self.dialog.show_dialog.assert_called_once_with()

    def test_show_failure_is_never_retried_after_possible_native_delivery(self):
        self.dialog.show_dialog.side_effect = RuntimeError("UI unavailable")
        with self.assertRaises(RuntimeError):
            self.module.notify(object(), "파트너", "안녕", portrait=types.SimpleNamespace(icon_info=(1, 2)))
        self.dialog.show_dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
