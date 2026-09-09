"""Single-turn, bounded automatic reply watch. Python 3.7; no game or disk IO.

The one-second interval is our UX default, not an EA engine requirement.
Only a pending turn is polled. Game-thread callbacks own scope checks and UI.
"""

import time

POLL_SECONDS = 1.0
DISPLAY_TTL_SECONDS = 180.0


def failure_message(reason):
    return {
        "invalid_request": "요청 형식이 맞지 않아 처리하지 못했어요.",
        "provider_failed": "모델 응답에 실패했어요. 중복 호출 방지를 위해 자동 재시도하지 않아요.",
        "session_limit": "이번 세션의 호출 한도에 도달했어요. 동반 앱에서 한도를 확인해 주세요.",
        "spending_blocked": "금액 한도 또는 미확정 과금 때문에 호출을 멈췄어요. 동반 앱의 budget status를 확인해 주세요.",
        "roles_required": "역할 설정이 맞지 않아요. plumbmate.setup으로 다시 선택해 주세요.",
    }.get(reason, "응답을 처리하지 못했어요.")


class ReplyDelivery(object):
    def __init__(self, now=None):
        self._now = now or time.monotonic
        self.scope = None
        self.connection = None
        self.state = "idle"
        self._next_at = 0.0
        self._expires_at = 0.0

    def start(self, request_id, binding_id, scene_id, connection, sent_at):
        self.scope = {"request_id": request_id, "binding_id": binding_id, "scene_id": scene_id}
        self.connection = connection
        self._next_at = 0.0
        self._expires_at = sent_at + DISPLAY_TTL_SECONDS
        self.state = "waiting"

    def stop(self, state="done"):
        self.scope = None
        self.connection = None
        self.state = state

    def advance(self, client, current_scope, deliver):
        if self.scope is None:
            return
        now = self._now()
        if now >= self._expires_at:
            self.stop("expired")
            return
        if now < self._next_at:
            return
        self._next_at = now + POLL_SECONDS
        try:
            scope = current_scope()
        except Exception:
            self.stop("context_changed")
            return
        if scope != (self.scope["binding_id"], self.scope["scene_id"]):
            self.stop("context_changed")
            return
        if not getattr(client, "supports_turn_delivery", False):
            return  # Older companion: explicit plumbmate.read still works.
        reply = client.take_turn_delivery()
        if isinstance(reply, dict) and all(reply.get(key) == value for key, value in self.scope.items()):
            if reply.get("phase") in ("completed", "failed"):
                # Claim before calling UI; a reentrant/raising callback must
                # never create a notification loop. Manual recovery stays open.
                self.stop("received")
                try:
                    deliver(reply)
                except Exception:
                    self.state = "display_failed"
                return
        client.request_turn_delivery(dict(self.scope))
