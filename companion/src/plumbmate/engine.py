"""One-request-at-a-time bridge processor."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .budget import clamp_request, state_delta
from .protocol import (
    BridgePaths,
    atomic_write_json,
    pending_requests,
    read_json,
    validate_request,
    validate_response,
)
from .providers import Provider


class CompanionEngine:
    def __init__(self, paths: BridgePaths, provider: Provider) -> None:
        self.paths = paths
        self.provider = provider
        self.paths.ensure()

    def process_one(self) -> Path | None:
        request_path = next(iter(pending_requests(self.paths)), None)
        if request_path is None:
            return None
        request = clamp_request(read_json(request_path))
        validate_request(request)
        model_request, snapshot, prior_facts = self._prepare_request(request)
        response = self.provider.respond(model_request)
        validate_response(response)
        response_path = self.paths.responses / f"{request['request_id']}.json"
        atomic_write_json(response_path, response)
        self._save_session(request["session_id"], snapshot, prior_facts, response)
        shutil.move(str(request_path), str(self.paths.archive / request_path.name))
        return response_path

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
            previous = read_json(session_path)
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
