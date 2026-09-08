"""One-request-at-a-time bridge processor."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from .budget import SessionBudget, clamp_request
from .protocol import (
    BridgePaths,
    ProtocolError,
    atomic_write_json,
    pending_requests,
    read_json,
    validate_request,
    validate_response,
)
from .providers import Provider
from .turn_status import write_turn_status
from .pricing import SpendingBlocked


class CompanionEngine:
    def __init__(self, paths: BridgePaths, provider: Provider, *, max_attempts: int = 20) -> None:
        self.paths = paths
        self.provider = provider
        self.paths.ensure()
        self.rejected_requests = 0
        self.budget = SessionBudget(max_attempts=max_attempts)

    def _quarantine(self, path: Path) -> None:
        destination = self.paths.archive / "rejected"
        destination.mkdir(exist_ok=True)
        shutil.move(str(path), str(destination / (uuid.uuid4().hex + ".json")))
        self.rejected_requests += 1

    def process_one(self) -> Path | None:
        for index, request_path in enumerate(pending_requests(self.paths)):
            if index >= 16:
                return None
            try:
                request = read_json(request_path)
                validate_request(request)
                if request_path.stem != request["request_id"]:
                    raise ProtocolError("Request filename and identifier differ")
                # No verified save receipt exists in this alpha. A household
                # ID cannot authorize cross-turn state or factual memory reuse.
                request["memory_summary"] = ""
                request["state_delta"] = {}
                request = clamp_request(request)
            except ProtocolError:
                write_turn_status(self.paths, request_path.stem, "failed", "invalid_request")
                self._quarantine(request_path)
                continue
            response_path = self.paths.responses / f"{request['request_id']}.json"
            if response_path.exists():
                self._quarantine(request_path)
                continue
            if not self.budget.reserve(request):
                write_turn_status(self.paths, request["request_id"], "failed", "session_limit")
                self._quarantine(request_path)
                continue
            write_turn_status(self.paths, request["request_id"], "processing")
            try:
                response = self.provider.respond(request)
                validate_response(response, expected_request_id=request["request_id"])
                response = dict(response, memory_patch=[])
                validate_response(response, expected_request_id=request["request_id"])
                atomic_write_json(response_path, response)
            except Exception as exc:
                # Timeouts may already incur charges: retain, never auto-retry.
                reason = "spending_blocked" if isinstance(exc, SpendingBlocked) else "provider_failed"
                write_turn_status(self.paths, request["request_id"], "failed", reason)
                self._quarantine(request_path)
                if isinstance(exc, SpendingBlocked):
                    raise SpendingBlocked(str(exc)) from None
                raise ProtocolError("Turn failed; retained in archive/rejected; no automatic retry") from exc
            shutil.move(str(request_path), str(self.paths.archive / request_path.name))
            write_turn_status(self.paths, request["request_id"], "completed")
            return response_path
        return None

    def process_all(self) -> list[Path]:
        completed: list[Path] = []
        while True:
            result = self.process_one()
            if result is None:
                return completed
            completed.append(result)
