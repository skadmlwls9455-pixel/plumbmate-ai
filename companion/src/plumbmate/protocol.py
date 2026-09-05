"""Filesystem bridge shared by the game adapter and companion."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "0.1"
MAX_JSON_BYTES = 64 * 1024
REQUEST_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class BridgePaths:
    root: Path

    @property
    def requests(self) -> Path:
        return self.root / "requests"

    @property
    def responses(self) -> Path:
        return self.root / "responses"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def state(self) -> Path:
        return self.root / "state"

    def ensure(self) -> None:
        for path in (self.requests, self.responses, self.archive, self.state):
            path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise ProtocolError("Symlinked bridge files are not accepted")
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ProtocolError("Bridge JSON exceeds size limit")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("Bridge JSON could not be read safely") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{path.name} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProtocolError("Refusing to replace a symlinked bridge file")
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ProtocolError("Bridge JSON exceeds size limit")
    descriptor, temporary = tempfile.mkstemp(prefix=".pm-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_request_id(value: Any) -> None:
    if not isinstance(value, str) or REQUEST_ID_PATTERN.fullmatch(value) is None:
        raise ProtocolError("request_id must be a 32-character lowercase hex identifier")


def validate_request(value: dict[str, Any]) -> None:
    required = ("protocol_version", "request_id", "kind", "player_message", "session_id", "actor")
    missing = [key for key in required if key not in value]
    if missing:
        raise ProtocolError("Missing request fields: " + ", ".join(missing))
    validate_request_id(value["request_id"])
    if value["protocol_version"] != PROTOCOL_VERSION or value["kind"] != "turn":
        raise ProtocolError("Unsupported request protocol or kind")
    if not isinstance(value["actor"], dict) or "name" not in value["actor"]:
        raise ProtocolError("Request actor is invalid")
    if not isinstance(value["actor"]["name"], str) or len(value["actor"]["name"]) > 128:
        raise ProtocolError("Request actor name is invalid")
    if not isinstance(value["player_message"], str) or len(value["player_message"]) > 1000:
        raise ProtocolError("Request message is invalid")
    if not isinstance(value["session_id"], str) or not 1 <= len(value["session_id"]) <= 160:
        raise ProtocolError("Request session is invalid")
    if not isinstance(value.get("household", []), list) or not isinstance(value.get("recent_events", []), list):
        raise ProtocolError("Request context must contain lists")


ALLOWED_ACTIONS = {
    "suggest_only",
    "queue_social",
    "queue_self_interaction",
    "cancel_current",
}


def validate_response(value: dict[str, Any], *, expected_request_id: str | None = None) -> None:
    required = ("protocol_version", "request_id", "partner_message", "action", "memory_patch")
    missing = [key for key in required if key not in value]
    if missing:
        raise ProtocolError("Missing response fields: " + ", ".join(missing))
    validate_request_id(value["request_id"])
    if expected_request_id is not None and value["request_id"] != expected_request_id:
        raise ProtocolError("Response request_id does not match the pending turn")
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported response protocol")
    action = value["action"]
    if not isinstance(action, dict) or action.get("name") not in ALLOWED_ACTIONS:
        raise ProtocolError("Response action is not allow-listed")
    if not isinstance(value["memory_patch"], list) or len(value["memory_patch"]) > 3:
        raise ProtocolError("Response memory_patch is invalid")
    if any(not isinstance(item, str) or len(item) > 240 for item in value["memory_patch"]):
        raise ProtocolError("Response memory_patch entries are invalid")
    if not isinstance(value["partner_message"], str) or len(value["partner_message"]) > 500:
        raise ProtocolError("Response message is invalid")
    if not isinstance(action.get("reason"), str) or len(action["reason"]) > 240:
        raise ProtocolError("Response action reason is invalid")
    arguments = action.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != {"target_sim_id", "interaction", "idea"}:
        raise ProtocolError("Response action arguments are invalid")
    if any(item is not None and (not isinstance(item, str) or len(item) > 240)
           for item in arguments.values()):
        raise ProtocolError("Response action argument values are invalid")


def pending_requests(paths: BridgePaths) -> Iterable[Path]:
    paths.ensure()
    yield from sorted(paths.requests.glob("*.json"), key=lambda path: path.stat().st_mtime)
