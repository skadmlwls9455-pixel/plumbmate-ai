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
        "provider_failed": "모델 응답을 완료하지 못했어요. 자동 재시도하지 않아요. 유료 모델이었다면 과금 확인 전까지 추가 호출이 차단될 수 있어요.",
        "session_limit": "이번 세션의 호출 한도에 도달했어요. 동반 앱에서 한도를 확인해 주세요.",
        "spending_blocked": "금액 한도 또는 미확정 과금으로 추가 호출을 막았어요. 게임 오류가 아니며, 다시 보내거나 재시작해도 해제되지 않아요. 동반 앱의 budget status에서 원인을 확인해 주세요.",
        "roles_required": "역할 설정이 맞지 않아요. plumbmate.setup으로 다시 선택해 주세요.",
    }.get(reason, "응답을 처리하지 못했어요.")


def pending_message(status, elapsed, automatic):
    """Bounded, factual UI text; elapsed time is not an ETA or model emotion."""
    seconds = max(0, min(180, int(elapsed)))
    phase = status.get("phase") if isinstance(status, dict) else None
    state = ("동반 앱이 요청을 처리하고 있어요." if phase == "processing" else
             "아직 동반 앱의 처리 확인이 없어요. 동반 앱이 실행 중인지 확인해 주세요.")
    delivery = ("답변은 자동으로 알림 담벼락에 표시돼요." if automatic else
                "자동 수신 연결을 확인 중이에요. 잠시 후 대화창을 다시 열면 답변을 확인할 수 있어요.")
    return ("보낸 지 약 {}초 · {}\n{}\n\n"
            "기다리기를 끝내면 이 답변은 나중에도 표시하지 않아요. "
            "이미 보낸 요청의 처리·과금 취소는 아니며, 새 전송은 별도 호출이에요.").format(seconds, state, delivery)


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

    def advance(self, client, current_scope, deliver, on_expired=None):
        if self.scope is None:
            return
        now = self._now()
        if now >= self._expires_at:
            expired_scope = dict(self.scope)
            self.stop("expired")
            # Claim first: even a reentrant/raising UI callback runs only once.
            # The adapter must recheck the scene before showing a system notice.
            if on_expired is not None:
                try:
                    on_expired(expired_scope)
                except Exception:
                    self.state = "expiry_notice_failed"
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
