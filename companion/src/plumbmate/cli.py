"""Command-line interface for local development and the future companion app."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import CompanionEngine
from .memory import MemoryStore
from .reflection import maintain, reflect, refresh_relationship_summaries
from .event_socket import (
    ALLOWED_EVENT_KINDS,
    BridgeClient,
    EventSocketServer,
    SocketBridgeError,
    configured_port,
    load_token,
    token_path,
    write_endpoint,
    write_token,
)
from .protocol import BridgePaths, PROTOCOL_VERSION, ProtocolError, atomic_write_json, read_json
from .providers import create_provider


def bridge_from(value: str) -> BridgePaths:
    return BridgePaths(Path(value).expanduser().resolve())


def make_demo_request(message: str) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": uuid.uuid4().hex,
        "kind": "turn",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "player_message": message,
        "session_id": "portfolio-demo",
        "game": {"name": "The Sims 4", "adapter": "prototype"},
        "actor": {"sim_id": 2, "name": "에이든", "age": "YOUNGADULT", "mood": "HAPPY",
                  "motives": {"hunger": -60, "energy": 20, "social": 40, "fun": -10, "hygiene": 60, "bladder": 30}},
        "household": [{"sim_id": 1, "name": "플레이어 심"}, {"sim_id": 2, "name": "에이든"}],
        "recent_events": ["둘이 같은 집으로 이사했다", "사진 촬영용 의상을 골랐다"],
        "memory_summary": "서로 다정한 연인이고 함께 보내는 조용한 시간을 좋아한다.",
    }


def newest_response(paths: BridgePaths) -> Path | None:
    candidates = list(paths.responses.glob("*.json"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _process_turn(engine: CompanionEngine, *, fail_on_error: bool = False) -> Path | None:
    """One visible result path for all CLI service modes."""
    before = engine.budget.attempts
    try:
        result = engine.process_one()
    except ProtocolError as exc:
        if fail_on_error:
            raise
        print(str(exc), flush=True)
        result = None
    if engine.budget.attempts != before:
        budget = engine.budget
        print(f"model attempts: {budget.attempts}/{budget.max_attempts}; "
              f"context estimate total: ~{budget.estimated_context_tokens} tokens "
              "(not provider usage or money)", flush=True)
    if result is not None:
        print("turn completed: " + result.name, flush=True)
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plumbmate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = (
        "init", "demo", "serve-once", "serve", "show-latest",
        "token", "listen", "run", "mock-event", "memory-search", "memory-stats", "memory-admin",
    )
    for command in commands:
        sub = subparsers.add_parser(command)
        sub.add_argument("--bridge", required=True, help="Local bridge directory")
        if command in {"serve-once", "serve", "run"}:
            sub.add_argument("--provider", choices=("mock", "openai"), default="mock")
        if command == "demo":
            sub.add_argument("--message", default="에이든, 오늘 우리 뭐 하면서 놀까?")
        if command == "serve":
            sub.add_argument("--interval", type=float, default=0.75)
        if command == "token":
            sub.add_argument("--rotate", action="store_true", help="Replace the stored token")
        if command in {"listen", "run"}:
            sub.add_argument("--port", type=int, default=None,
                             help="Reuse configured port (initially 47651); 0 is manual discovery")
            sub.add_argument("--events", type=int, default=0, help="Stop after N events")
        if command in {"listen", "run"}:
            sub.add_argument("--verbose-events", action="store_true", help="Include private event payloads in local console output")
        if command in {"serve-once", "serve", "run"}:
            sub.add_argument("--max-turns", type=int, default=20,
                             help="Maximum model attempts this session; no silent automatic retries")
            sub.add_argument("--recall", action="store_true",
                             help="Feed retrieved memories into memory_summary (session-scoped, "
                                  "save scope is NOT verified; off by default)")
        if command in {"serve-once", "serve", "run", "listen", "memory-search", "memory-stats", "memory-admin"}:
            sub.add_argument("--memory-db", default=None,
                             help="SQLite path (default: <bridge>/state/memory.sqlite3)")
        if command in {"serve-once", "serve", "run", "listen"}:
            sub.add_argument("--no-memory", action="store_true",
                             help="Do not record turns/events into the memory store")
        if command == "memory-admin":
            sub.add_argument("action", choices=(
                "show", "pin", "unpin", "edit", "delete", "purge-deleted", "promote", "consolidate", "archive",
                "summarize", "reflect", "maintain"))
            sub.add_argument("--id", type=int, default=None, help="memory_id for show/pin/unpin/edit/delete/promote")
            sub.add_argument("--summary", default=None, help="edit: replacement text (old row is superseded)")
            sub.add_argument("--evidence-kind", choices=("player_confirm", "game_event"), default=None)
            sub.add_argument("--evidence", default=None, help="promote: the confirmation text or game event id")
            sub.add_argument("--session", default=None,
                             help="consolidate/summarize/reflect/maintain: restrict to one session_id")
        if command == "memory-search":
            sub.add_argument("--query", required=True, help="Free text to score against stored memories")
            sub.add_argument("--session", default=None, help="Restrict to one session_id")
            sub.add_argument("--limit", type=int, default=10)
        if command == "mock-event":
            sub.add_argument("--port", type=int, required=True)
            sub.add_argument("--kind", choices=sorted(ALLOWED_EVENT_KINDS), default="heartbeat")
            sub.add_argument("--payload", default="{}", help="JSON object")

    args = parser.parse_args(argv)
    paths = bridge_from(args.bridge)
    paths.ensure()

    def open_memory() -> MemoryStore | None:
        if getattr(args, "no_memory", False):
            return None
        target = getattr(args, "memory_db", None) or (paths.state / "memory.sqlite3")
        return MemoryStore(target)

    if args.command == "memory-stats":
        with open_memory() as memory:
            print(json.dumps(memory.stats(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "memory-admin":
        needs_id = args.action in {"show", "pin", "unpin", "edit", "delete", "promote"}
        if needs_id and args.id is None:
            print("--id is required for " + args.action)
            return 2
        with open_memory() as memory:
            try:
                if args.action == "show":
                    row = memory.get(args.id)
                    if row is None:
                        print("no such memory")
                        return 1
                    print(json.dumps(row, ensure_ascii=False, indent=2))
                    for promotion in memory.promotions(args.id):
                        print("promotion: " + json.dumps(promotion, ensure_ascii=False))
                elif args.action in {"pin", "unpin"}:
                    memory.pin(args.id, args.action == "pin")
                    print(f"{args.action}: {args.id}")
                elif args.action == "edit":
                    if not args.summary:
                        print("--summary is required for edit")
                        return 2
                    print(f"superseded {args.id} by {memory.edit(args.id, args.summary)}")
                elif args.action == "delete":
                    memory.soft_delete(args.id)
                    print(f"deleted (soft): {args.id}; run purge-deleted to remove rows physically")
                elif args.action == "purge-deleted":
                    print(f"purged: {memory.purge_deleted()}")
                elif args.action == "promote":
                    if not (args.evidence_kind and args.evidence):
                        print("--evidence-kind and --evidence are required for promote")
                        return 2
                    memory.promote_to_canon(args.id, args.evidence_kind, args.evidence)
                    print(f"promoted to canon: {args.id}")
                elif args.action == "consolidate":
                    print(f"merged groups: {memory.consolidate(args.session)}")
                elif args.action == "archive":
                    print(f"archived: {memory.archive_stale()}")
                elif args.action in {"summarize", "reflect"}:
                    job = refresh_relationship_summaries if args.action == "summarize" else reflect
                    sessions = [args.session] if args.session else memory.sessions()
                    for scope in sessions:
                        for memory_id in job(memory, scope):
                            row = memory.get(memory_id) or {}
                            print(f"{scope} #{memory_id}: {row.get('summary', '')}")
                elif args.action == "maintain":
                    print(json.dumps(maintain(memory, args.session), ensure_ascii=False))
            except (KeyError, ValueError) as exc:
                print("memory-admin: " + str(exc))
                return 2
        return 0
    if args.command == "memory-search":
        with open_memory() as memory:
            for item in memory.search(args.query, session_id=args.session, limit=args.limit):
                print(json.dumps({"memory_id": item.memory_id, "score": item.score, "type": item.memory_type,
                                  "provenance": item.provenance, "summary": item.summary,
                                  "reason": item.reason, "breakdown": item.breakdown},
                                 ensure_ascii=False), flush=True)
        return 0

    if args.command == "init":
        print(paths.root)
        return 0
    if args.command == "demo":
        request = make_demo_request(args.message)
        target = paths.requests / f"{request['request_id']}.json"
        atomic_write_json(target, request)
        print(target)
        return 0
    if args.command in {"serve-once", "serve"}:
        memory = open_memory()
        engine = CompanionEngine(paths, create_provider(args.provider), max_attempts=args.max_turns,
                                 memory=memory, recall=args.recall)
        if args.recall:
            print("memory recall ON (session-scoped; save scope unverified)", flush=True)
        try:
            if args.command == "serve-once":
                result = _process_turn(engine, fail_on_error=True)
                if result is None:
                    print("no completed turn; check pending requests or turn status")
                return 0
            try:
                while True:
                    result = _process_turn(engine)
                    if result is None:
                        time.sleep(max(0.1, args.interval))
            except KeyboardInterrupt:
                return 0
        finally:
            if memory is not None:
                memory.close()
    if args.command == "token":
        target = token_path(paths)
        if args.rotate or not target.exists():
            write_token(paths)
        print(load_token(paths))
        return 0
    if args.command in {"listen", "run"}:
        engine = None
        memory = open_memory()
        if args.command == "run":
            if args.max_turns < 1:
                print("--max-turns must be positive")
                return 2
            engine = CompanionEngine(paths, create_provider(args.provider), max_attempts=args.max_turns,
                                     memory=memory, recall=args.recall)
            if args.recall:
                print("memory recall ON (session-scoped; save scope unverified)", flush=True)
        # The socket layer is loopback-only and refuses to start without a token.
        try:
            server = EventSocketServer(load_token(paths), port=configured_port(paths, args.port))
            server.start()
        except (OSError, SocketBridgeError) as exc:
            print(f"Listener not started: {exc}. Check the token/configuration and port owner; "
                  "no process was stopped and no alternate port was chosen.")
            return 2
        try:
            write_endpoint(paths, "127.0.0.1", server.port)
            print(f"listening on 127.0.0.1:{server.port}", flush=True)
            seen = 0
            limit_announced = False
            try:
                while args.events <= 0 or seen < args.events:
                    if engine is not None:
                        _process_turn(engine)
                        if engine.budget.exhausted and not limit_announced:
                            limit_announced = True
                            print("Session turn limit reached; no further model calls. Restart explicitly to continue.", flush=True)
                    event = server.poll(timeout=0.25)
                    if event is None:
                        continue
                    seen += 1
                    if memory is not None:
                        try:
                            memory.remember_event(event.kind, event.payload)
                        except Exception as exc:
                            print(f"memory: event not stored ({type(exc).__name__})", flush=True)
                    summary = {"kind": event.kind, "event_id": event.event_id, "received_at": event.received_at}
                    if args.verbose_events:
                        summary["payload"] = event.payload
                    print(json.dumps(summary, ensure_ascii=False), flush=True)
            except KeyboardInterrupt:
                pass
        finally:
            server.stop()
            if memory is not None:
                memory.close()
            # Keep configuration: a later launch must reach the same endpoint
            # cached by the game, without disk polling on the engine thread.
        return 0
    if args.command == "mock-event":
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            print("payload must be a JSON object")
            return 2
        with BridgeClient(load_token(paths), args.port) as client:
            print(json.dumps(client.send_event(args.kind, payload), ensure_ascii=False))
        return 0
    if args.command == "show-latest":
        latest = newest_response(paths)
        if latest is None:
            print("no response")
            return 1
        print(json.dumps(read_json(latest), ensure_ascii=False, indent=2))
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ProtocolError, SocketBridgeError) as exc:
        print("PlumbMate: " + str(exc), flush=True)
        return 2
    except OSError:
        print("PlumbMate: local file/socket operation failed; check the bridge path and permissions.", flush=True)
        return 2
    except json.JSONDecodeError:
        print("PlumbMate: input must be valid JSON.", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
