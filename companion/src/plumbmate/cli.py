"""Command-line interface for local development and the future companion app."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import CompanionEngine
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
from . import billing_cli
from .doctor import inspect_bridge


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
        "actor": {"sim_id": 2, "name": "플레이어 심", "age": "YOUNGADULT", "mood": "HAPPY"},
        "household": [{"sim_id": 1, "name": "AI 파트너"}, {"sim_id": 2, "name": "플레이어 심"}],
        "roles": {"binding_id": uuid.uuid4().hex, "scene_id": uuid.uuid4().hex, "player_sim_id": 2, "partner_sim_id": 1},
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
    ledger = getattr(engine.provider, "ledger", None)
    if ledger is not None and engine.budget.attempts != before:
        print(json.dumps(ledger.report(), ensure_ascii=False), flush=True)
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plumbmate")
    subparsers = parser.add_subparsers(dest="command", required=True)
    billing_cli.register(subparsers)

    commands = (
        "init", "demo", "serve-once", "serve", "show-latest",
        "token", "listen", "run", "mock-event", "doctor",
    )
    for command in commands:
        sub = subparsers.add_parser(command)
        sub.add_argument("--bridge", required=True, help="Local bridge directory")
        if command == "doctor":
            sub.add_argument("--connect", action="store_true", help="Test authenticated loopback only; no model calls")
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
        if command == "mock-event":
            sub.add_argument("--port", type=int, required=True)
            sub.add_argument("--kind", choices=sorted(ALLOWED_EVENT_KINDS), default="heartbeat")
            sub.add_argument("--payload", default="{}", help="JSON object")

    args = parser.parse_args(argv)
    if args.command == "budget":
        return billing_cli.run(args)
    paths = bridge_from(args.bridge)
    if args.command == "doctor":
        report = inspect_bridge(paths, connect=args.connect)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    paths.ensure()

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
        engine = CompanionEngine(paths, create_provider(args.provider), max_attempts=args.max_turns)
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
    if args.command == "token":
        target = token_path(paths)
        if args.rotate or not target.exists():
            write_token(paths)
        print(load_token(paths))
        return 0
    if args.command in {"listen", "run"}:
        engine = None
        if args.command == "run":
            if args.max_turns < 1:
                print("--max-turns must be positive")
                return 2
            engine = CompanionEngine(paths, create_provider(args.provider), max_attempts=args.max_turns)
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
                    summary = {"kind": event.kind, "event_id": event.event_id, "received_at": event.received_at}
                    if args.verbose_events:
                        summary["payload"] = event.payload
                    print(json.dumps(summary, ensure_ascii=False), flush=True)
            except KeyboardInterrupt:
                pass
        finally:
            server.stop()
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
