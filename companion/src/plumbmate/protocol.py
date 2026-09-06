"""Filesystem bridge shared by the game adapter and companion."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


from .contracts import (
    PROTOCOL_VERSION, ALLOWED_ACTIONS, ProtocolError,
    validate_request_id, validate_request, validate_response,
)

MAX_JSON_BYTES = 64 * 1024


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


def _reject_constant(value: str) -> None:
    raise ProtocolError("Non-finite JSON numbers are not accepted")


def read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise ProtocolError("Symlinked bridge files are not accepted")
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ProtocolError("Bridge JSON exceeds size limit")
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("Bridge JSON could not be read safely") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{path.name} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProtocolError("Refusing to replace a symlinked bridge file")
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
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


def pending_requests(paths: BridgePaths) -> Iterable[Path]:
    paths.ensure()
    candidates = []
    for path in paths.requests.glob("*.json"):
        try:
            # lstat keeps dangling symlinks visible to rejection/quarantine.
            candidates.append((path.lstat().st_mtime_ns, path.name, path))
        except FileNotFoundError:
            continue  # A producer/consumer removed it during enumeration.
    yield from (item[2] for item in sorted(candidates))
