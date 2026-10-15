"""Deterministic context reduction and approximate token budgeting."""

from __future__ import annotations

import hashlib
import json
import copy
from dataclasses import dataclass
from typing import Any

from .contracts import ProtocolError, validate_request

MAX_CONTEXT_BYTES = 12_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def estimate_tokens(value: Any) -> int:
    """Coarse UTF-8 heuristic, NOT a tokenizer, upper bound, or billing usage."""
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


def clamp_request(request: dict[str, Any], max_context_bytes: int = MAX_CONTEXT_BYTES) -> dict[str, Any]:
    """Validate, then pack whole optional items; never truncate the live turn.

    This is a byte budget for the context JSON only, not the full API envelope.
    Actor and the player's current message are mandatory. Long-term retrieval
    needs a relevance-aware packer later; this alpha has no restored memory.
    """
    validate_request(request)
    result = copy.deepcopy(request)
    protected = {int(value) for key, value in result.get("roles", {}).items()
                 if key in ("player_sim_id", "partner_sim_id")}
    while len(canonical_json(result).encode("utf-8")) > max_context_bytes:
        removable = [index for index, member in enumerate(result.get("household", []))
                     if int(member["sim_id"]) not in protected]
        if result.get("memory_summary"):
            result["memory_summary"] = ""
        elif removable:
            result["household"].pop(removable[-1])
        elif result.get("recent_events"):
            result["recent_events"].pop(0)
        else:
            raise ProtocolError("Current message and actor exceed the context byte budget")
    return result


@dataclass
class SessionBudget:
    """In-process model attempt allowance, independent of CLI polling loops."""

    max_attempts: int = 20
    attempts: int = 0
    estimated_context_tokens: int = 0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ProtocolError("max_attempts must be a positive integer")

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    def reserve(self, request: dict[str, Any]) -> bool:
        if self.exhausted:
            return False
        estimate = estimate_tokens(request)
        self.attempts += 1  # Reserve before calling: uncertain failures still count.
        self.estimated_context_tokens += estimate
        return True


@dataclass(frozen=True)
class TokenBudget:
    estimated_input_tokens: int
    max_output_tokens: int = 220

    @classmethod
    def for_request(cls, request: dict[str, Any], max_output_tokens: int = 220) -> "TokenBudget":
        return cls(estimate_tokens(clamp_request(request)), max_output_tokens)
