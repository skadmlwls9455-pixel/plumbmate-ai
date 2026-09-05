"""Deterministic context reduction and approximate token budgeting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def estimate_tokens(value: Any) -> int:
    """Cheap language-agnostic upper-ish estimate; never used for billing."""
    text = value if isinstance(value, str) else canonical_json(value)
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def state_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return changed and removed top-level fields only."""
    changed: dict[str, Any] = {}
    for key, value in current.items():
        if key not in previous or stable_hash(previous[key]) != stable_hash(value):
            changed[key] = value
    removed = sorted(key for key in previous if key not in current)
    if removed:
        changed["_removed"] = removed
    return changed


def clamp_request(request: dict[str, Any]) -> dict[str, Any]:
    """Bound untrusted bridge input before it can become model context."""
    result = dict(request)
    result["player_message"] = str(result.get("player_message", ""))[:1000]
    result["memory_summary"] = str(result.get("memory_summary", ""))[:1600]
    result["recent_events"] = [str(item)[:240] for item in result.get("recent_events", [])[-8:]]
    result["household"] = list(result.get("household", []))[:12]
    return result


@dataclass(frozen=True)
class TokenBudget:
    estimated_input_tokens: int
    max_output_tokens: int = 220

    @classmethod
    def for_request(cls, request: dict[str, Any], max_output_tokens: int = 220) -> "TokenBudget":
        return cls(estimate_tokens(clamp_request(request)), max_output_tokens)

