"""Homeostatic drive urgency from the game's motive bars.

Keramati & Gutkin's homeostatic view (NaviBrain #9): motivation is the
deviation from a set point, not the raw level. The Sims already exposes that
level per motive (-100 = crisis, +100 = fully satisfied), so urgency is the
normalized distance below the comfortable set point. This module only turns
numbers into a short, deterministic Korean state line; it never decides an
action and never calls a model.
"""

from __future__ import annotations

import math
from typing import Any

MOTIVES = ("hunger", "energy", "social", "fun", "hygiene", "bladder")
LABELS_KO = {
    "hunger": "배고픔", "energy": "피로", "social": "외로움",
    "fun": "지루함", "hygiene": "불결함", "bladder": "화장실",
}
SET_POINT = 50.0     # at or above this the motive contributes nothing
FLOOR = -100.0       # the game's minimum
MAX_STATE_CHARS = 240
NOTICE_THRESHOLD = 0.2


def urgency(value: float) -> float:
    """0.0 at or above the set point, 1.0 at the game's floor, linear between."""
    try:
        level = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(level):
        return 0.0
    return max(0.0, min(1.0, (SET_POINT - level) / (SET_POINT - FLOOR)))


def assess(motives: dict[str, Any] | None) -> dict[str, float]:
    """Urgency per known motive present in the snapshot, unknown keys ignored."""
    if not isinstance(motives, dict):
        return {}
    return {name: urgency(motives[name]) for name in MOTIVES if name in motives}


def dominant(urgencies: dict[str, float]) -> tuple[str, float] | None:
    """The most pressing motive, or None when nothing exceeds the notice threshold."""
    if not urgencies:
        return None
    name = max(sorted(urgencies), key=lambda key: urgencies[key])
    return (name, urgencies[name]) if urgencies[name] >= NOTICE_THRESHOLD else None


def band(level: float) -> str:
    if level < NOTICE_THRESHOLD:
        return "낮음"
    if level < 0.5:
        return "보통"
    if level < 0.8:
        return "높음"
    return "매우 높음"


def describe(motives: dict[str, Any] | None, buffs: list[str] | None = None,
             mood: str | None = None) -> str:
    """One bounded line the model can read: pressing needs, mood, a few buffs.

    Order is by urgency then name so the same snapshot always yields the same
    text (prompt-cache friendly). Nothing below the notice threshold is listed.
    """
    urgencies = assess(motives)
    pressing = sorted(
        ((name, level) for name, level in urgencies.items() if level >= NOTICE_THRESHOLD),
        key=lambda item: (-item[1], item[0]),
    )
    parts: list[str] = []
    if urgencies:
        if pressing:
            parts.append("욕구: " + ", ".join(f"{LABELS_KO[name]} {band(level)}" for name, level in pressing))
        else:
            parts.append("욕구: 안정")
    if mood:
        parts.append("기분: " + " ".join(str(mood).split())[:40])
    if isinstance(buffs, (list, tuple)) and buffs:
        shown = [" ".join(str(item).split())[:30] for item in list(buffs)[:4] if str(item).strip()]
        if shown:
            parts.append("버프: " + ", ".join(shown))
    line = " | ".join(parts)
    return line[:MAX_STATE_CHARS]
