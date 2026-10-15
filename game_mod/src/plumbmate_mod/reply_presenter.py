"""Present a validated turn; no game imports, files, sockets or model calls.

The caller checks the current role/scene and eligibility before entering.
Injected callbacks keep native notification details outside this module.
"""


EXPIRY_MESSAGE = (
    "답변 대기 시간이 끝났어요. 늦게 온 답변은 표시하지 않으며 자동 재전송하지 않아요. "
    "동반 앱 상태를 확인해 주세요. 이미 시작된 모델 호출은 계속될 수 있고, 새 전송은 별도 호출이에요.")


class ReplyPresenter(object):
    def __init__(self, state, delivery, notify, validate, name_of, text_of):
        self.state = state
        self.delivery = delivery
        self.notify = notify
        self.validate = validate
        self.name_of = name_of
        self.text_of = text_of

    def show(self, response, partner, output):
        response = self.validate(response, self.state.request_id)
        try:
            self.notify(partner, "PlumbMate · " + self.name_of(partner),
                        response["partner_message"], portrait=partner)
        except Exception:
            output(response["partner_message"])
            output("알림을 표시하지 못해 콘솔에 답변을 표시했어요.")
        self.state.display()
        if self.delivery is not None:
            self.delivery.stop()
        # This output is diagnostic only. No action dispatcher is invoked.
        output("Suggested action: {} | {}".format(response["action"]["name"],
                                                  self.text_of(response["action"].get("reason", ""))))
        output("Automatic execution is disabled in prototype 0.1.")

    def system_notice(self, owner, message, output):
        """Failures/expiry belong to the system, not the character portrait."""
        try:
            self.notify(owner, "PlumbMate · 연결 안내", message)
        except Exception:
            output(message)

    def failed(self, owner, message, output):
        self.system_notice(owner, message, output)
        self.state.display()

    def expired(self, owner, output):
        # Expiry is not a completed display, remote cancellation or refund.
        self.system_notice(owner, EXPIRY_MESSAGE, output)
