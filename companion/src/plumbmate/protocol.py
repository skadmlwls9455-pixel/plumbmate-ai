"""Filesystem bridge shared by the game adapter and companion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "0.1"


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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{path.name} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_request(value: dict[str, Any]) -> None:
    required = ("protocol_version", "request_id", "kind", "player_message", "session_id", "actor")
    missing = [key for key in required if key not in value]
    if missing:
        raise ProtocolError("Missing request fields: " + ", ".join(missing))
    if value["protocol_version"] != PROTOCOL_VERSION or value["kind"] != "turn":
        raise ProtocolError("Unsupported request protocol or kind")
    if not isinstance(value["actor"], dict) or "name" not in value["actor"]:
        raise ProtocolError("Request actor is invalid")


ALLOWED_ACTIONS = {
    "suggest_only",
    "queue_social",
    "queue_self_interaction",
    "cancel_current",
}


def validate_response(value: dict[str, Any]) -> None:
    required = ("protocol_version", "request_id", "partner_message", "action", "memory_patch")
    missing = [key for key in required if key not in value]
    if missing:
        raise ProtocolError("Missing response fields: " + ", ".join(missing))
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported response protocol")
    action = value["action"]
    if not isinstance(action, dict) or action.get("name") not in ALLOWED_ACTIONS:
        raise ProtocolError("Response action is not allow-listed")
    message = value["partner_message"]
    # The provider schema already bounds these, but this function is the trust
    # boundary for anything read back off the file bridge, edited or not.
    if not isinstance(message, str) or len(message) > 2000:
        raise ProtocolError("Response partner_message must be a string of at most 2000 characters")
    patch = value["memory_patch"]
    if not isinstance(patch, list) or len(patch) > 3:
        raise ProtocolError("Response memory_patch is invalid")
    if any(not isinstance(item, str) or len(item) > 500 for item in patch):
        raise ProtocolError("Response memory_patch items must be strings of at most 500 characters")


def pending_requests(paths: BridgePaths) -> Iterable[Path]:
    paths.ensure()
    yield from sorted(paths.requests.glob("*.json"), key=lambda path: path.stat().st_mtime)

