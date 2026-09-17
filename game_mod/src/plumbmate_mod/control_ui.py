"""Console-free entry prototype; native rendering still needs live acceptance.

One owned launcher per live household/zone, with no disk/network/model work.
Never edits the game's global dialog classes or another mod's notification.
"""

import time

from plumbmate_role_setup_ui import _text

_LAUNCHER_TYPE = None

def _launcher(owner, activate):
    global _LAUNCHER_TYPE
    from ui.ui_dialog import ButtonType, UiDialogResponse
    from ui.ui_dialog_notification import UiDialogNotification
    import services

    if _LAUNCHER_TYPE is None:
        class LauncherNotification(UiDialogNotification):
            def respond(self, response):
                if response == ButtonType.DIALOG_RESPONSE_CUSTOM_1:
                    # Native dialog_respond retains dialogs whose respond is False.
                    # This is a reusable launcher, not a single-use conversation.
                    self._plumbmate_activate()
                    return False
                return super().respond(response)
        # One class, not a new tunable factory/cache entry on every lot change.
        _LAUNCHER_TYPE = LauncherNotification

    dialog = _LAUNCHER_TYPE.TunableFactory().default(
        owner=owner, title=lambda *a, **k: _text("PlumbMate"),
        text=lambda *a, **k: _text("함께 이야기할 심을 고르고 대화를 시작하세요. 아래 버튼으로 설정과 대화창을 다시 열 수 있어요."))
    dialog._plumbmate_activate = activate
    dialog.set_responses((UiDialogResponse(
        dialog_response_id=ButtonType.DIALOG_RESPONSE_CUSTOM_1,
        text=lambda *a, **k: _text("PlumbMate 열기")),))
    service = services.ui_dialog_service()
    try:
        dialog.show_dialog()
        if service.get_dialog(dialog.dialog_id) is not dialog:
            raise RuntimeError("launcher_not_registered")
    except Exception:
        # A failed build may already have registered the dialog server-side.
        service.dialog_cancel(dialog.dialog_id)
        raise
    return lambda: service.dialog_cancel(dialog.dialog_id)


class ControlUI(object):
    def __init__(self, clock=None, make_launcher=None):
        self._clock = clock or time.monotonic
        self._make_launcher = make_launcher or _launcher
        self._next_check = 0.0
        self._scope = None
        self._cancel = None
        self._attempts = 0
        self._generation = 0
        self._epoch = 0
        self.last_error = None

    def close(self):
        self._generation += 1
        self._epoch += 1
        cancel, self._cancel = self._cancel, None
        self._scope = None
        self._attempts = 0
        if cancel is not None:
            try:
                cancel()
            except Exception:
                pass  # Old zone's UI service may already be torn down.

    def pulse(self, environment, activate):
        """At most one memory-only environment lookup per five real seconds."""
        now = self._clock()
        if now < self._next_check:
            return
        self._next_check = now + 5.0
        try:
            zone, household, members, owner = environment()
            if zone is None or household is None or owner is None or not members:
                raise ValueError("household_not_ready")
        except Exception:
            self.close()
            return
        scope = (zone, household)
        if self._scope is None or self._scope[0] is not zone or self._scope[1] is not household:
            self.close()
            self._scope = scope
        if self._cancel is not None or self._attempts >= 3:
            return
        self._attempts += 1
        epoch = self._epoch

        def selected():
            try:
                current = environment()
                if epoch == self._epoch and self._scope == scope and current[0] is zone and current[1] is household:
                    activate()
            except Exception as exc:
                self.last_error = type(exc).__name__

        try:
            self._cancel = self._make_launcher(owner, selected)
            self.last_error = None
        except Exception as exc:
            self.last_error = type(exc).__name__

    def open(self, environment, summary, chat, setup, output):
        from ui.ui_dialog import UiDialogOkCancel, UiDialogResponse, ButtonType
        zone, household, _, owner = environment()
        self._generation += 1
        generation = self._generation
        dialog = UiDialogOkCancel.TunableFactory().default(
            owner=owner, title=lambda *a, **k: _text("PlumbMate · 대화"),
            text=lambda *a, **k: _text(summary),
            text_ok=lambda *a, **k: _text("대화 열기"),
            text_cancel=lambda *a, **k: _text("닫기"))
        dialog.set_responses((UiDialogResponse(
            dialog_response_id=ButtonType.DIALOG_RESPONSE_CUSTOM_1,
            text=lambda *a, **k: _text("역할 선택")),))
        consumed = False

        def selected(result):
            nonlocal consumed
            if consumed or generation != self._generation:
                return
            consumed = True
            try:
                current = environment()
                if current[0] is not zone or current[1] is not household:
                    return
                if result.response == ButtonType.DIALOG_RESPONSE_OK:
                    chat()
                elif result.response == ButtonType.DIALOG_RESPONSE_CUSTOM_1:
                    setup()
            except Exception:
                try:
                    output("대화창을 열지 못했어요. 현재 가족과 역할을 확인해 주세요. 자동 전송하지 않았어요.")
                except Exception:
                    pass

        dialog.show_dialog(on_response=selected)

    def open_home(self, environment, describe_roles, chat, setup, output, notify):
        """Compose the home view without owning role resolution or sending.

        describe_roles returns display names and an already scope-checked receipt.
        Only explicit menu response callbacks may enter chat or role selection.
        """
        owner = environment()[3]

        def report(message):
            try:
                notify(owner, "PlumbMate · 안내", message)
            except Exception:
                output(message)

        try:
            player_name, partner_name, receipt = describe_roles()
            summary = "내가 맡을 심: {}\nAI가 맡을 심: {}".format(player_name, partner_name)
            if receipt:
                summary += "\n\n" + receipt
        except ValueError:
            summary = "먼저 내가 맡을 심과 AI가 맡을 심을 골라 주세요. 같은 가족의 서로 다른 두 심이 필요해요."
        summary += "\n\n답변은 알림 담벼락에 표시돼요. 대화 열기에서 작성하거나 기다리는 답변의 상태를 확인할 수 있어요."
        self.open(environment, summary, chat, setup, report)
