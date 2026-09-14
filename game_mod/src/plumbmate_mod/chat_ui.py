"""Native composer adapter, verified structurally on TS4 1.127.41.1230.

No auto-send, model calls, timers or file polling. IME/display still need live
testing. Tuned global defaults are never modified for this dialog.
"""

from types import SimpleNamespace

from plumbmate_chat_input import Composer, MAX_DRAFT_CHARS
from plumbmate_role_setup_ui import _text, notify

INPUT_NAME = "plumbmate_message"


class _InputFields(object):
    # Native on_text_input uses hasattr; build_msg separately requires items().
    def __init__(self, field):
        setattr(self, INPUT_NAME, field)

    def items(self):
        return ((INPUT_NAME, getattr(self, INPUT_NAME)),)


def _make_dialog(owner, partner_name, draft):
    from ui.ui_dialog_generic import UiDialogTextInputOkCancel
    from ui.ui_text_input import UiTextInput, _TextInputLengthFixed

    # New per-dialog instances, not mutation of a shared .default singleton.
    length = _TextInputLengthFixed(
        min_length=SimpleNamespace(length=1, tooltip=None), max_length=MAX_DRAFT_CHARS)
    field = UiTextInput(
        sort_order=0, default_text=lambda *args, **kwargs: _text("하고 싶은 말을 적어 주세요"),
        initial_value=(lambda *args, **kwargs: _text(draft)) if draft else None,
        title=lambda *args, **kwargs: _text("메시지 · 최대 1000자"),
        length_restriction=length, restricted_characters=None, check_profanity=False, height=100)
    return UiDialogTextInputOkCancel.TunableFactory(text_inputs=(INPUT_NAME,)).default(
        owner=owner, text_inputs=_InputFields(field),
        title=lambda *args, **kwargs: _text("PlumbMate · {}에게 말하기".format(partner_name)),
        text=lambda *args, **kwargs: _text("전송을 누를 때만 동반 앱으로 보내요. 이 시험판의 답변 확인은 plumbmate.read예요."),
        text_ok=lambda *args, **kwargs: _text("전송"),
        text_cancel=lambda *args, **kwargs: _text("취소"))


class ChatUI(object):
    def __init__(self):
        self.composer = Composer()

    def open(self, owner, partner_name, scope, current_scope, submit, output):
        ticket = self.composer.begin(scope)
        dialog = _make_dialog(owner, partner_name, self.composer.draft)

        def console(message):
            try:
                output(message)
            except Exception:
                pass  # Client may disappear while an old dialog closes.

        def report(message):
            console(message)
            try:
                notify(owner, "PlumbMate · 대화 상태", message)
            except Exception:
                console("알림을 표시하지 못해 상태를 콘솔에 남겼어요.")

        def selected(result):
            if not self.composer.is_current(ticket):
                return
            try:
                if not result.accepted:
                    self.composer.cancel(ticket)
                    return
                try:
                    latest_scope = current_scope()
                except Exception:
                    self.composer.cancel(ticket)
                    report("심이나 장소 정보를 확인할 수 없어요. 대화창을 다시 열어 주세요. 전송하지 않았어요.")
                    return
                value = result.text_input_responses.get(INPUT_NAME)
                message = self.composer.accept(ticket, latest_scope, value)
                # Shared with console input: role, pending-turn and write guards.
                receipt = submit(message)
                self.composer.sent(ticket)
                report(receipt)
            except ValueError as exc:
                if self.composer.is_current(ticket):
                    self.composer.cancel(ticket)
                report(str(exc))
            except Exception:
                # Consume a malformed result too; no automatic retry or body echo.
                if self.composer.is_current(ticket):
                    self.composer.cancel(ticket)
                report("대화를 처리하지 못했어요. 자동 재시도하지 않아요. plumbmate.read로 전송 상태를 확인해 주세요.")

        dialog.show_dialog(on_response=selected)
