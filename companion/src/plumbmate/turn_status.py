"""Best-effort user-facing turn receipts, never a source of RP facts."""

from .protocol import BridgePaths, ProtocolError, atomic_write_json, validate_request_id

PHASES = frozenset(("processing", "completed", "failed"))
REASONS = frozenset(("none", "invalid_request", "provider_failed", "session_limit", "spending_blocked"))


def write_turn_status(paths: BridgePaths, request_id: str, phase: str, reason: str = "none") -> bool:
    try:
        validate_request_id(request_id)
        if phase not in PHASES or reason not in REASONS:
            raise ProtocolError("Invalid turn status")
        atomic_write_json(paths.state / ("turn-" + request_id + ".json"), {
            "request_id": request_id, "phase": phase, "reason_code": reason,
        })
        return True
    except (OSError, ProtocolError):
        return False  # Diagnostics must not trigger a second paid attempt.
