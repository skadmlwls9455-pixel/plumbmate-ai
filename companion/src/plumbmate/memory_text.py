"""Text helpers for the memory store: tokenizing, particle stripping, packing.

Deliberately rule-based and dependency-free. Korean particles are stripped
with a suffix table so "낚시를" and "낚시" land on the same keyword; that is
the interim substitute for a morphological analyzer, and RFC-001 lists its
misses (F2) as a known limit rather than something to hide.
"""

from __future__ import annotations

import re

# Longest first so "에서는" wins over "는". A particle is only stripped when
# the token keeps at least two characters, so "은행" is not cut to "은".
PARTICLES = (
    "으로부터", "에서는", "에게서", "한테서", "까지는", "으로는", "에서", "에게",
    "한테", "께서", "부터", "까지", "처럼", "보다", "이랑", "으로", "랑", "로",
    "은", "는", "이", "가", "을", "를", "에", "도", "와", "과", "의", "만", "께",
)

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
_MIN_TOKEN_CHARACTERS = 2


def strip_particle(token: str) -> str:
    for particle in PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= _MIN_TOKEN_CHARACTERS:
            return token[: -len(particle)]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercased, particle-stripped, de-duplicated keywords in first-seen order."""
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _TOKEN.findall(str(text or "").lower()):
        token = strip_particle(raw)
        if len(token) < _MIN_TOKEN_CHARACTERS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def fts_query(tokens: list[str]) -> str:
    """An OR query of quoted terms; quoting neutralizes FTS5 operator syntax."""
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def pack_lines(lines: list[str], *, max_chars: int, max_line_chars: int) -> str:
    """Join whole lines into a budget; a line that does not fit is dropped,
    never truncated mid-sentence, so the model sees complete memories only."""
    packed: list[str] = []
    used = 0
    for line in lines:
        line = clip(line, max_line_chars)
        cost = len(line) + (1 if packed else 0)
        if used + cost > max_chars:
            continue
        packed.append(line)
        used += cost
    return "\n".join(packed)
