"""Python 3.7 file exchange for explicit commands, never imported by the tick pump.

No EA APIs, model calls, or current-Sim state live here. Responses and receipts
are read only for the exact pending ID, never the newest file in a directory.
"""

import json
import os
import re
import uuid

PROTOCOL_VERSION = "0.1"
MAX_JSON_BYTES = 65536


def _valid_id(request_id):
    return isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{32}", request_id) is not None


def _read(path, limit=MAX_JSON_BYTES):
    if os.path.islink(path):
        raise ValueError("Symlinked bridge files are not accepted")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(raw) > limit:
        raise ValueError("Bridge file exceeds size limit")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bridge file must be an object")
    return value


def write_request(root, payload):
    request_id = payload.get("request_id")
    if not _valid_id(request_id):
        raise ValueError("Invalid request identifier")
    target = os.path.join(root, "requests", request_id + ".json")
    if os.path.lexists(target):
        raise ValueError("Request already exists")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("Request exceeds size limit")
    temporary = target + "." + uuid.uuid4().hex + ".tmp"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def read_response(root, request_id):
    if request_id is None:
        return None
    if not _valid_id(request_id):
        raise ValueError("Invalid pending request identifier")
    response = _read(os.path.join(root, "responses", request_id + ".json"))
    if response is None:
        return None
    if response.get("request_id") != request_id or response.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Response does not match the pending turn")
    if not isinstance(response.get("partner_message"), str) or len(response["partner_message"]) > 500:
        raise ValueError("Invalid response text")
    action = response.get("action")
    if not isinstance(action, dict) or action.get("name") not in (
            "suggest_only", "queue_social", "queue_self_interaction", "cancel_current"):
        raise ValueError("Invalid action suggestion")
    return response


def read_status(root, request_id):
    if request_id is None:
        return None
    if not _valid_id(request_id):
        raise ValueError("Invalid pending request identifier")
    status = _read(os.path.join(root, "state", "turn-" + request_id + ".json"), 2048)
    if status is None:
        return None
    if (status.get("request_id") != request_id
            or status.get("phase") not in ("processing", "completed", "failed")
            or status.get("reason_code") not in ("none", "invalid_request", "provider_failed", "session_limit", "spending_blocked", "roles_required")):
        raise ValueError("Invalid turn status")
    return status
