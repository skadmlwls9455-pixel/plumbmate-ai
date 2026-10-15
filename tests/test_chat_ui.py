"""Composer + native-adapter contract tests. These do not prove IME/display."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load("chat_policy_test", "chat_input.py")


class ComposerTests(unittest.TestCase):
    def test_korean_decomposed_jamo_multiline_and_emoji_are_preserved(self):
        text = "오늘  뭐할까?\n안녕 👩‍💻 🌻"
        self.assertEqual(policy.validate_message("  " + text + "  "), text)

    def test_thousand_emoji_are_not_counted_as_two_thousand_python_characters(self):
        self.assertEqual(policy.validate_message("🌻" * 1000), "🌻" * 1000)
        with self.assertRaises(policy.InputError):
            policy.validate_message("가" * 1001)

    def test_invalid_input_never_becomes_partial_message(self):
        for value in (None, 42, b"text", "", " \n\t ", "\x00hello", "\x1fhello", "hi\x7f", "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(policy.InputError):
                policy.validate_message(value)

    def test_ticket_is_single_use_even_when_invalid(self):
        composer = policy.Composer()
        ticket = composer.begin(("binding", "scene"))
        with self.assertRaises(policy.InputError):
            composer.accept(ticket, ("binding", "scene"), "")
        with self.assertRaises(policy.InputError):
            composer.accept(ticket, ("binding", "scene"), "hello")

    def test_old_window_cannot_overwrite_new_window_draft(self):
        composer = policy.Composer()
        old = composer.begin("scope")
        current = composer.begin("scope")
        composer.accept(current, "scope", "new draft")
        with self.assertRaises(policy.InputError):
            composer.accept(old, "scope", "old draft")
        composer.cancel(old)
        composer.sent(old)
        self.assertEqual(composer.draft, "new draft")

    def test_changed_scope_discards_old_draft_and_cannot_submit(self):
        composer = policy.Composer()
        ticket = composer.begin("old")
        with self.assertRaises(policy.InputError):
            composer.accept(ticket, "new", "old private text")
        self.assertEqual(composer.draft, "")
        self.assertFalse(composer.is_current(ticket))

    def test_cancel_or_success_drops_draft(self):
        composer = policy.Composer()
        ticket = composer.begin("scope")
        composer.accept(ticket, "scope", "unsent")
        composer.sent(ticket)
        self.assertEqual(composer.draft, "")
        ticket = composer.begin("scope")
        composer.accept(ticket, "scope", "unsent")
        composer.cancel(composer.begin("scope"))
        self.assertEqual(composer.draft, "")

    def test_draft_is_bounded_safe_and_never_crosses_role_scope(self):
        composer = policy.Composer()
        ticket = composer.begin("scope")
        with self.assertRaises(policy.InputError):
            composer.accept(ticket, "scope", "가" * 1001)
        self.assertEqual(composer.draft, "가" * 1001)  # allow correction, no truncation
        composer.begin("different role")
        self.assertEqual(composer.draft, "")
        for value in ("가" * 2001, "\ud800", "\x00hi", None):
            ticket = composer.begin("scope")
            with self.assertRaises(policy.InputError):
                composer.accept(ticket, "scope", value)
            self.assertEqual(composer.draft, "")


class ChatAdapterTests(unittest.TestCase):
    def setUp(self):
        self.dialogs, self.notices, self.lines = [], [], []
        dialogs = self.dialogs

        class TextInput:
            def __init__(self, *, sort_order, default_text, initial_value, title,
                         length_restriction, restricted_characters, check_profanity, height):
                self.__dict__.update({key: value for key, value in locals().items() if key != "self"})

        class Length:
            def __init__(self, *, min_length, max_length):
                self.min_length, self.max_length = min_length, max_length

        class Dialog:
            def __init__(self, max_value=None, *, owner, text_inputs, title, text, text_ok, text_cancel):
                self.__dict__.update({key: value for key, value in locals().items() if key != "self"})
                self.text_input_responses = {}
                dialogs.append(self)

            @staticmethod
            def TunableFactory(*, text_inputs):
                if text_inputs != ("plumbmate_message",):
                    raise ValueError("Wrong text input key")
                return types.SimpleNamespace(default=Dialog)

            def show_dialog(self, *, on_response):
                self.callback = on_response

        generic = types.ModuleType("ui.ui_dialog_generic")
        generic.UiDialogTextInputOkCancel = Dialog
        field = types.ModuleType("ui.ui_text_input")
        field.UiTextInput, field._TextInputLengthFixed = TextInput, Length
        localization = types.ModuleType("plumbmate_role_setup_ui")
        localization._text = str
        localization.notify = lambda *args: self.notices.append(args)
        modules = {"ui": types.ModuleType("ui"), "ui.ui_dialog_generic": generic,
                   "ui.ui_text_input": field, "plumbmate_chat_input": policy,
                   "plumbmate_role_setup_ui": localization}
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.module = load("native_chat_adapter_test", "chat_ui.py")
        self.ui = self.module.ChatUI()
        self.owner = object()
        self.scope = ("binding", "scene")
        self.submit = mock.Mock(return_value="동반 앱에 요청을 남겼어요.")

    def open(self):
        self.ui.open(self.owner, "AI 파트너", self.scope, lambda: self.scope, self.submit, self.lines.append)
        return self.dialogs[-1]

    def respond(self, dialog, value, accepted=True):
        dialog.accepted = accepted
        dialog.text_input_responses = {self.module.INPUT_NAME: value}
        dialog.callback(dialog)

    def test_open_has_recipient_label_local_buttons_and_no_send(self):
        dialog = self.open()
        self.assertIs(dialog.owner, self.owner)
        self.assertIn("AI 파트너", dialog.title())
        self.assertIn("알림 담벼락", dialog.text())
        self.assertNotIn("plumbmate.read", dialog.text())
        self.assertEqual((dialog.text_ok(), dialog.text_cancel()), ("전송", "취소"))
        field = getattr(dialog.text_inputs, self.module.INPUT_NAME)
        self.assertEqual(dialog.text_inputs.items(), ((self.module.INPUT_NAME, field),))
        self.assertEqual(field.length_restriction.max_length, 2000)
        self.assertIsNone(field.restricted_characters)
        self.assertIsNone(field.initial_value)
        self.submit.assert_not_called()

    def test_hangul_reply_submitted_once_and_draft_cleared(self):
        dialog = self.open()
        text = "안녕  오늘은\n같이 쉬자 🌻"
        self.respond(dialog, text)
        self.respond(dialog, text)
        self.submit.assert_called_once_with(text)
        self.assertEqual(self.ui.composer.draft, "")
        self.assertEqual(self.notices, [])
        self.assertEqual(self.ui.receipt(self.scope), self.submit.return_value)
        self.assertEqual(self.ui.receipt(("other", "scene")), "")

    def test_cancel_does_not_read_missing_response_fields_or_send(self):
        dialog = self.open()
        dialog.callback(types.SimpleNamespace(accepted=False))
        self.submit.assert_not_called()
        self.assertEqual(self.notices, [])

    def status_dialog(self, current=None, dismiss=None):
        dialog = mock.Mock()
        native = types.ModuleType("ui.ui_dialog")
        native.ButtonType = types.SimpleNamespace(DIALOG_RESPONSE_OK=1)
        native.UiDialogOkCancel = mock.Mock()
        native.UiDialogOkCancel.TunableFactory.return_value.default.return_value = dialog
        with mock.patch.dict(sys.modules, {"ui.ui_dialog": native}):
            self.ui.show_status(self.owner, "파트너", "대기 상태", self.scope,
                                current or (lambda: self.scope), dismiss or self.submit, self.lines.append)
        return dialog.show_dialog.call_args.kwargs["on_response"], native

    def test_status_close_is_not_dismiss_and_affirmative_is_single_use(self):
        for response in (0, -1, None):
            callback, _ = self.status_dialog()
            callback(types.SimpleNamespace(response=response))
            self.submit.assert_not_called()
        callback, native = self.status_dialog()
        result = types.SimpleNamespace(response=1)
        callback(result)
        callback(result)
        self.submit.assert_called_once_with()
        fields = native.UiDialogOkCancel.TunableFactory.return_value.default.call_args.kwargs
        self.assertEqual(fields["text_ok"](), "기다리기 끝내기")
        self.assertEqual(fields["text_cancel"](), "닫고 기다리기")

    def test_old_status_window_cannot_dismiss_new_window_or_changed_scope(self):
        old, _ = self.status_dialog()
        self.status_dialog()
        old(types.SimpleNamespace(response=1))
        changed, _ = self.status_dialog(current=lambda: ("new", "scene"))
        changed(types.SimpleNamespace(response=1))
        old, _ = self.status_dialog()
        self.open()
        old(types.SimpleNamespace(response=1))
        self.submit.assert_not_called()

    def test_status_notice_failure_does_not_repeat_dismiss(self):
        callback, _ = self.status_dialog()
        with mock.patch.object(self.module, "notify", side_effect=RuntimeError()):
            callback(types.SimpleNamespace(response=1))
            callback(types.SimpleNamespace(response=1))
        self.submit.assert_called_once()
        self.assertTrue(self.lines)

    def test_role_or_scene_change_rejects_old_window(self):
        dialog = self.open()
        self.scope = ("new-binding", "scene")
        self.respond(dialog, "old message")
        self.submit.assert_not_called()
        self.assertEqual(self.ui.composer.draft, "")
        self.assertTrue(any("바뀌었어요" in line for line in self.lines))

    def test_newer_window_supersedes_older_callback(self):
        old = self.open()
        current = self.open()
        self.respond(old, "ignore me")
        self.submit.assert_not_called()
        self.respond(current, "send me")
        self.submit.assert_called_once_with("send me")

    def test_rejected_input_is_preserved_for_explicit_correction(self):
        dialog = self.open()
        self.respond(dialog, "가" * 1001)
        self.submit.assert_not_called()
        replacement = self.open()
        field = getattr(replacement.text_inputs, self.module.INPUT_NAME)
        self.assertEqual(field.initial_value(), "가" * 1001)
        self.respond(replacement, "줄인 내용")
        self.submit.assert_called_once_with("줄인 내용")

    def test_pending_rejection_keeps_one_draft_and_does_not_retry(self):
        self.submit.side_effect = ValueError("앞서 보낸 대화가 있어요.")
        dialog = self.open()
        self.respond(dialog, "later")
        self.respond(dialog, "later")
        self.submit.assert_called_once()
        self.assertEqual(self.ui.composer.draft, "later")

    def test_failed_send_does_not_echo_error_body_or_retry(self):
        self.submit.side_effect = OSError("private path or remote text")
        dialog = self.open()
        self.respond(dialog, "hello")
        self.respond(dialog, "hello")
        self.submit.assert_called_once()
        self.assertNotIn("private path", " ".join(self.lines))

    def test_missing_response_or_context_failure_cannot_send(self):
        for failure in ("response", "context"):
            with self.subTest(failure=failure):
                if failure == "context":
                    self.ui.open(self.owner, "partner", self.scope, mock.Mock(side_effect=ValueError()),
                                 self.submit, self.lines.append)
                    dialog = self.dialogs[-1]
                    self.respond(dialog, "hi")
                else:
                    dialog = self.open()
                    dialog.callback(types.SimpleNamespace(accepted=True))
                self.submit.assert_not_called()

    def test_malformed_result_value_error_is_also_consumed_once(self):
        dialog = self.open()
        result = types.SimpleNamespace(accepted=True,
                                       text_input_responses=types.SimpleNamespace(get=mock.Mock(side_effect=ValueError("bad result"))))
        dialog.callback(result)
        dialog.callback(result)
        self.assertEqual(len(self.notices), 1)
        self.submit.assert_not_called()

    def test_success_does_not_depend_on_notification_availability(self):
        dialog = self.open()
        with mock.patch.object(self.module, "notify", side_effect=RuntimeError()) as notify:
            self.respond(dialog, "hello")
        notify.assert_not_called()
        self.submit.assert_called_once()
        self.assertEqual(self.ui.composer.draft, "")
        self.assertIn(self.submit.return_value, self.lines)

    def test_both_feedback_channels_can_fail_without_escaping_callback(self):
        self.ui.open(self.owner, "partner", self.scope, lambda: self.scope,
                     self.submit, mock.Mock(side_effect=RuntimeError("client gone")))
        with mock.patch.object(self.module, "notify", side_effect=RuntimeError("UI gone")):
            self.respond(self.dialogs[-1], "hello")
            self.respond(self.dialogs[-1], "hello")
        self.submit.assert_called_once()
        self.assertEqual(self.ui.composer.draft, "")

    def test_new_dialog_owns_its_input_field_and_length_settings(self):
        first = getattr(self.open().text_inputs, self.module.INPUT_NAME)
        second = getattr(self.open().text_inputs, self.module.INPUT_NAME)
        self.assertIsNot(first, second)
        self.assertIsNot(first.length_restriction, second.length_restriction)


if __name__ == "__main__":
    unittest.main()
