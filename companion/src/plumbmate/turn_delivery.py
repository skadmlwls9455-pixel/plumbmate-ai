"""Read-only, scoped reply projection for the authenticated local socket.

Only the companion reads files. A poll never invokes a provider, mutates a
turn, scans history, or restores memory. The original request (pending or
archived) must corroborate scope before any dialogue is returned.
"""

from .protocol import BridgePaths, ProtocolError, read_json, validate_request_id, validate_response

CAPABILITY = "turn_delivery_v1"
SCOPE_FIELDS = ("request_id", "binding_id", "scene_id")
FAILURE_CODES = frozenset(("invalid_request", "provider_failed", "session_limit",
                           "spending_blocked", "roles_required"))


class TurnDeliveryReader:
    def __init__(self, paths: BridgePaths):
        self.paths = paths

    def __call__(self, scope: dict) -> dict:
        if not isinstance(scope, dict) or set(scope) != set(SCOPE_FIELDS):
            raise ProtocolError("Invalid delivery scope")
        for key in SCOPE_FIELDS:
            validate_request_id(scope[key])
        result = dict(scope, phase="pending")
        request_id = scope["request_id"]
        # A failed receipt contains no dialogue and is safe even after the
        # malformed original was quarantined under a different filename.
        status_path = self.paths.state / ("turn-" + request_id + ".json")
        if status_path.exists():
            status = read_json(status_path)
            if (status.get("request_id") == request_id and status.get("phase") == "failed"
                    and isinstance(status.get("reason_code"), str)
                    and status.get("reason_code") in FAILURE_CODES):
                return dict(result, phase="failed", reason_code=status["reason_code"])
        request = None
        for directory in (self.paths.requests, self.paths.archive):
            path = directory / (request_id + ".json")
            if path.exists():
                request = read_json(path)
                break
        if request is None:
            return result
        roles = request.get("roles")
        if (request.get("request_id") != request_id or not isinstance(roles, dict)
                or any(roles.get(key) != scope[key] for key in ("binding_id", "scene_id"))):
            raise ProtocolError("Delivery scope does not match original request")
        path = self.paths.responses / (request_id + ".json")
        if not path.exists():
            return result
        response = read_json(path)
        validate_response(response, expected_request_id=request_id)
        # Never transport memory patches, prompts, usage details or history.
        projection = {key: response[key] for key in
                      ("protocol_version", "request_id", "partner_message", "action")}
        return dict(result, phase="completed", response=projection)
