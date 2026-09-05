"""Command-line interface for local development and the future companion app."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import CompanionEngine
from .protocol import BridgePaths, PROTOCOL_VERSION, atomic_write_json, read_json
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

    for command in ("init", "demo", "serve-once", "serve", "show-latest"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--bridge", required=True, help="Local bridge directory")
        if command in {"serve-once", "serve"}:
            sub.add_argument("--provider", choices=("mock", "openai"), default="mock")
        if command == "demo":
            sub.add_argument("--message", default="에이든, 오늘 우리 뭐 하면서 놀까?")
        if command == "serve":
            sub.add_argument("--interval", type=float, default=0.75)

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
                result = engine.process_one()
                if result:
                    print(result, flush=True)
                else:
                    time.sleep(max(0.1, args.interval))
        except KeyboardInterrupt:
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

