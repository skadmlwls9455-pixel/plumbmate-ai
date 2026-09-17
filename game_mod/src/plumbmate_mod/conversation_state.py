"""One live conversation's display policy; pure Python 3.7, no IO/game/UI.

This is request UI state, NOT factual memory or a save identity. The bootstrap
marks sent only after the request was committed, and display after delivery.
"""


class ConversationState(object):
    def __init__(self):
        self.request_id = None
        self.binding_id = None
        self.displayed_id = None
        self.dismissed_id = None
        self.sent_at = 0.0

    def sent(self, request_id, binding_id, now):
        self.request_id = request_id
        self.binding_id = binding_id
        self.sent_at = now

    def display(self):
        self.displayed_id = self.request_id

    def dismiss(self):
        self.dismissed_id = self.request_id

    def ineligible_reason(self, binding_id, now, ttl_seconds=180.0):
        if self.request_id is None or self.binding_id != binding_id:
            return "이 역할 설정에서 보낸 대화가 없어요. 이전 설정의 답변은 표시하지 않아요."
        if self.displayed_id == self.request_id:
            return "이미 표시한 답변이에요. 알림 담벼락 또는 콘솔에서 확인해 주세요."
        if self.dismissed_id == self.request_id:
            return "기다리기를 끝낸 답변은 표시하지 않아요. 새로 대화할 수 있어요."
        if now - self.sent_at >= ttl_seconds:
            return "보낸 지 오래된 답변은 표시하지 않아요. 필요하면 새로 대화해 주세요."
        return None
