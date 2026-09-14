"""Bounded, session-only composer policy. No EA APIs, disk or network access."""

MAX_MESSAGE_CHARS = 1000
# Native text controls may count UTF-16 units. Leave room for 1000 emoji;
# validate Python characters ourselves instead of sending a truncated prefix.
MAX_DRAFT_CHARS = 2000


class InputError(ValueError):
    pass


def validate_message(value):
    if not isinstance(value, str):
        raise InputError("입력 내용을 읽지 못했어요. 대화창을 다시 열어 주세요.")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        raise InputError("보이지 않는 제어 문자가 있어요. 내용을 확인해 주세요. 아직 전송하지 않았어요.")
    message = value.strip()
    if not message:
        raise InputError("보낼 내용을 입력해 주세요. 아직 전송하지 않았어요.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise InputError("메시지는 1000자까지 보낼 수 있어요. 내용을 줄여 주세요. 아직 전송하지 않았어요.")
    try:
        message.encode("utf-8")
    except UnicodeEncodeError:
        raise InputError("지원하지 않는 문자가 있어요. 붙여넣은 내용을 확인해 주세요.")
    return message


class Composer(object):
    """One bounded draft, scoped to a role/scene; never saved as RP memory."""

    def __init__(self):
        self._generation = 0
        self._scope = None
        self.draft = ""

    def begin(self, scope):
        if scope != self._scope:
            self.draft = ""
        self._scope = scope
        self._generation += 1
        return self._generation

    def is_current(self, ticket):
        return ticket == self._generation

    def cancel(self, ticket):
        if self.is_current(ticket):
            self._generation += 1
            self._scope = None
            self.draft = ""

    def accept(self, ticket, scope, value):
        if not self.is_current(ticket):
            raise InputError("이전 대화창의 입력은 전송하지 않았어요.")
        self._generation += 1  # consume even an invalid response exactly once
        if scope != self._scope:
            self._scope, self.draft = None, ""
            raise InputError("역할이나 장소가 바뀌었어요. 대화창을 다시 열어 주세요.")
        self.draft = value if isinstance(value, str) and len(value) <= MAX_DRAFT_CHARS else ""
        try:
            self.draft.encode("utf-8")
        except UnicodeEncodeError:
            self.draft = ""
        if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in self.draft):
            self.draft = ""
        return validate_message(value)

    def sent(self, ticket):
        # Never clear a newer draft opened by a reentrant UI callback.
        if self._generation == ticket + 1:
            self.draft = ""
