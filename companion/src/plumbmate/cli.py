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
        "actor": {"sim_id": 2, "name": "에이든", "age": "YOUNGADULT", "mood": "HAPPY"},
        "household": [{"sim_id": 1, "name": "플레이어 심"}, {"sim_id": 2, "name": "에이든"}],
        "recent_events": ["둘이 같은 집으로 이사했다", "사진 촬영용 의상을 골랐다"],
        "memory_summary": "서로 다정한 연인이고 함께 보내는 조용한 시간을 좋아한다.",
    }


def newest_response(paths: BridgePaths) -> Path | None:
    candidates = list(paths.responses.glob("*.json"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plumbmate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = (
        "init", "demo", "serve-once", "serve", "show-latest",
        "token", "listen", "run", "mock-event",
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
        if command == "run":
            sub.add_argument("--max-turns", type=int, default=20,
                             help="Maximum model attempts this session; no silent automatic retries")
        if command == "mock-event":
            sub.add_argument("--port", type=int, required=True)
            sub.add_argument("--kind", choices=sorted(ALLOWED_EVENT_KINDS), default="heartbeat")
            sub.add_argument("--payload", default="{}", help="JSON object")

    args = parser.parse_args(argv)
    paths = bridge_from(args.bridge)
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
        engine = CompanionEngine(paths, create_provider(args.provider))
        if args.command == "serve-once":
            result = engine.process_one()
            print(result if result else "no pending request")
            return 0
        try:
            while True:
                try:
                    result = engine.process_one()
                except ProtocolError as exc:
                    print(str(exc), flush=True)
                    continue
                if result:
                    print(result, flush=True)
                else:
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
            engine = CompanionEngine(paths, create_provider(args.provider))
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
            attempts = 0
            try:
                while args.events <= 0 or seen < args.events:
                    if engine is not None and attempts < args.max_turns:
                        # Count uncertain failures too; never retry them.
                        try:
                            result = engine.process_one()
                        except ProtocolError as exc:
                            attempts += 1
                            print(str(exc), flush=True)
                        else:
                            if result is not None:
                                attempts += 1
                                print("turn completed", flush=True)
                        if attempts >= args.max_turns:
                            print("Session turn limit reached; no further model calls. Restart explicitly to continue.", flush=True)
                    event = server.poll(timeout=0.25)
                    if event is None:
                        continue
                    seen += 1
                    print(json.dumps({
                        "kind": event.kind, "event_id": event.event_id,
                        "payload": event.payload, "received_at": event.received_at,
                    }, ensure_ascii=False), flush=True)
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


if __name__ == "__main__":
    raise SystemExit(main())
