"""One-request-at-a-time bridge processor."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .budget import clamp_request, state_delta
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


class CompanionEngine:
    """Processes requests one at a time; a bad request must never wedge the rest.

    A request the protocol itself rejects is quarantined immediately: it can
    only fail the same way again. A provider failure (network, HTTP, malformed
    model output) is retried — the exception propagates so the caller decides
    the pacing — and after ``max_attempts`` the request is quarantined too, so
    one poisonous file cannot pin the queue forever. Quarantined requests move
    to the archive with a suffixed name plus a ``.reason.txt`` sidecar, and the
    move is announced through :meth:`drain_notices`.
    """

    def __init__(
        self, paths: BridgePaths, provider: Provider, *, max_attempts: int = 5,
    ) -> None:
        self.paths = paths
        self.provider = provider
        self.paths.ensure()
        self._max_attempts = max(1, int(max_attempts))
        self._attempts: dict[str, int] = {}
        self._notices: list[str] = []

    def drain_notices(self) -> list[str]:
        """Return and clear quarantine announcements for the caller to log."""
        notices, self._notices = self._notices, []
        return notices

    def process_one(self) -> Path | None:
        while True:
            request_path = next(iter(pending_requests(self.paths)), None)
            if request_path is None:
                return None
            try:
                request = clamp_request(read_json(request_path))
                validate_request(request)
            except Exception as exc:
                # Parsing, clamping, and validating are pure computation over
                # the file's bytes: ANY failure here (ProtocolError, but also a
                # TypeError from a wrong-typed field) is deterministic, and
                # retrying cannot change the answer. Catching only ProtocolError
                # would let a wrong-typed field bypass quarantine and pin the
                # mtime-ordered queue forever.
                self._quarantine(request_path, "rejected", f"{type(exc).__name__}: {exc}")
                continue
            try:
                # Session-state corruption counts as retryable too: it lands in
                # quarantine after max_attempts instead of looping forever.
                model_request, snapshot, prior_facts = self._prepare_request(request)
                response = self.provider.respond(model_request)
                validate_response(response)
            except Exception as exc:
                attempts = self._attempts.get(request_path.name, 0) + 1
                self._attempts[request_path.name] = attempts
                if attempts >= self._max_attempts:
                    self._attempts.pop(request_path.name, None)
                    self._quarantine(
                        request_path, "failed",
                        f"{type(exc).__name__}: {exc} (after {attempts} attempts)",
                    )
                    continue
                raise
            self._attempts.pop(request_path.name, None)
            response_path = self.paths.responses / f"{request['request_id']}.json"
            atomic_write_json(response_path, response)
            self._save_session(request["session_id"], snapshot, prior_facts, response)
            shutil.move(str(request_path), str(self.paths.archive / request_path.name))
            return response_path

    def _quarantine(self, request_path: Path, kind: str, detail: str) -> None:
        target = self.paths.archive / f"{request_path.stem}.{kind}.json"
        shutil.move(str(request_path), str(target))
        reason_path = target.with_suffix(".reason.txt")
        reason_path.write_text(detail + "\n", encoding="utf-8")
        self._notices.append(f"{kind}: {request_path.name}: {detail}")

    def process_all(self) -> list[Path]:
        completed: list[Path] = []
        while True:
            result = self.process_one()
            if result is None:
                return completed
            completed.append(result)

    def _session_path(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        return self.paths.state / f"session-{digest}.json"

    def _prepare_request(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        session_path = self._session_path(str(request["session_id"]))
        previous: dict[str, Any] = {}
        if session_path.exists():
            try:
                previous = read_json(session_path)
            except ProtocolError:
                # A corrupt session file must not poison every future turn of
                # that session; losing the compact memory and starting fresh
                # is the recoverable outcome.
                previous = {}
        prior_snapshot = previous.get("snapshot", {})
        prior_facts = [str(item)[:240] for item in previous.get("memory_facts", [])[-12:]]
        snapshot = {
            "game": request.get("game", {}),
            "actor": request.get("actor", {}),
            "household": request.get("household", []),
        }

        prepared = dict(request)
        if prior_snapshot:
            delta = state_delta(prior_snapshot, snapshot)
            prepared["state_delta"] = delta
            actor = request.get("actor", {})
            prepared["actor"] = {
                "sim_id": actor.get("sim_id", "unknown"),
                "name": actor.get("name", "Unknown Sim"),
            }
            if "game" not in delta:
                prepared["game"] = {}
            if "household" not in delta:
                prepared["household"] = []
        else:
            prepared["state_delta"] = {}

        if prior_facts:
            supplied = str(prepared.get("memory_summary", "")).strip()
            local_summary = " | ".join(prior_facts[-8:])
            prepared["memory_summary"] = (supplied + " | " + local_summary).strip(" |")[:1600]
        return clamp_request(prepared), snapshot, prior_facts

    def _save_session(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        prior_facts: list[str],
        response: dict[str, Any],
    ) -> None:
        facts = prior_facts + [str(item)[:240] for item in response.get("memory_patch", [])]
        deduplicated: list[str] = []
        for fact in facts:
            if fact and fact not in deduplicated:
                deduplicated.append(fact)
        atomic_write_json(
            self._session_path(str(session_id)),
            {"snapshot": snapshot, "memory_facts": deduplicated[-12:]},
        )
