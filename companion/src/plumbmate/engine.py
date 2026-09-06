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
from .drives import describe as describe_drives
from .memory import MemoryStore
from .reflection import maintain
from .providers import Provider
from .turn_status import write_turn_status


class CompanionEngine:
    """One request at a time.

    ``memory`` records what passed through (player utterances, replies, and
    adapter-reported events) so nothing is lost; ``recall`` additionally feeds
    three to six retrieved memories into ``memory_summary``. Recall is opt-in
    because save scope is not verified yet: rows are keyed by the request's
    session_id, which a household id can name but cannot authenticate.
    """

    def __init__(self, paths: BridgePaths, provider: Provider, *, max_attempts: int = 20,
                 memory: MemoryStore | None = None, recall: bool = False,
                 maintenance_every: int = 20) -> None:
        self.paths = paths
        self.provider = provider
        self.paths.ensure()
        self.rejected_requests = 0
        self.budget = SessionBudget(max_attempts=max_attempts)
        self.memory = memory
        self.recall_enabled = bool(recall and memory is not None)
        self.memory_errors = 0
        self.last_recall_lines = 0
        # Between-turns cognition (merge, archive, summaries, reflection)
        # runs after every N completed turns; 0 disables it. It is rule-based
        # and touches only SQLite, so it costs no model attempt.
        self.maintenance_every = max(0, int(maintenance_every))
        self.completed_turns = 0
        self.maintenance_runs = 0
        self.last_maintenance: dict[str, int] | None = None

    def _memory_call(self, action, *args):
        """Memory must never decide a turn's fate; failures are counted."""
        if self.memory is None:
            return None
        try:
            return action(*args)
        except Exception:
            self.memory_errors += 1
            return None

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
                if self.recall_enabled:
                    summary = self._memory_call(self.memory.recall_summary, request)
                    request["memory_summary"] = summary or ""
                    self.last_recall_lines = summary.count("\n") + 1 if summary else 0
                actor = request.get("actor") or {}
                state = describe_drives(actor.get("motives"), actor.get("buffs"), actor.get("mood"))
                if state:
                    request["drive_state"] = state
                else:
                    request.pop("drive_state", None)
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
            # The turn happened whether or not the provider answers; recall
            # already ran, so the current message cannot recall itself.
            if self.memory is not None:
                self._memory_call(self.memory.remember_turn_request, request)
            try:
                response = self.provider.respond(request)
                validate_response(response, expected_request_id=request["request_id"])
                response = dict(response, memory_patch=[])
                validate_response(response, expected_request_id=request["request_id"])
                atomic_write_json(response_path, response)
            except Exception as exc:
                # Timeouts may already incur charges: retain, never auto-retry.
                write_turn_status(self.paths, request["request_id"], "failed", "provider_failed")
                self._quarantine(request_path)
                raise ProtocolError("Turn failed; retained in archive/rejected; no automatic retry") from exc
            shutil.move(str(request_path), str(self.paths.archive / request_path.name))
            write_turn_status(self.paths, request["request_id"], "completed")
            self.completed_turns += 1
            if self.memory is not None:
                self._memory_call(self.memory.remember_turn_response, request, response)
                if self.maintenance_every and self.completed_turns % self.maintenance_every == 0:
                    outcome = self._memory_call(maintain, self.memory, request.get("session_id"))
                    if outcome is not None:
                        self.maintenance_runs += 1
                        self.last_maintenance = outcome
            return response_path
        return None

    def process_all(self) -> list[Path]:
        completed: list[Path] = []
        while True:
            result = self.process_one()
            if result is None:
                return completed
            completed.append(result)
