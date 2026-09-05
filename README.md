# PlumbMate AI

**An AI companion that plays a life simulation beside you, not over you.**

PlumbMate is an experimental, local-first bridge between The Sims 4 and an AI
companion. The companion receives a deliberately small snapshot of the active
Sim, chats with the player, and returns a structured, allow-listed gameplay
suggestion. The long-term goal is a “second player” experience: the AI explains
what it wants to do, the player can approve it, and only then is the action
queued for the companion Sim.

This repository is an MVP and portfolio prototype. It does **not** modify an
existing save, install itself into the game, or execute autonomous gameplay
actions yet.

## Why this is different

- Dialogue and gameplay intent are generated together.
- The model never receives screenshots, save files, tray files, or arbitrary
  files from the computer.
- Game control is an allow-list, not unrestricted mouse/keyboard access.
- The bridge sends state changes on demand instead of streaming every frame.
- API keys stay in the companion process and never enter the Mods folder.
- A mock provider works without an account, network access, or API cost.

## MVP flow

1. The game-side script writes a compact JSON request when the player runs
   `plumbmate.chat <message>` in the cheat console.
2. The local companion reads the request and asks a configured provider for a
   short Korean reply plus one structured action suggestion.
3. The response is written back to the local bridge directory.
4. `plumbmate.read` displays the newest response in the cheat console.

The current game-side adapter reports only a safe, small identity/household
snapshot. Action execution is intentionally disabled in this first milestone.

## Event socket

Alongside the file bridge, the mod opens a loopback socket to the companion so
the game adapter can send events (automatic gameplay observation is still
pending). Provision the bridge token before launching the game. The mod hooks
`Zone.update` at load and connects when `plumbmate listen` starts, even if the
companion starts later. Disconnections retry with bounded exponential backoff.

The CLI reuses the configured port, initially `127.0.0.1:47651`. It retains
`state/bridge-endpoint.json` on exit; this is configuration, **not** proof that
the companion is running. The game reads configuration at mod load or explicit
start only: no filesystem reads, stats, directory creation, or worker threads
on the tick path. On a port conflict, the CLI fails visibly rather than moving
silently or stopping another process. Deliberately changing `--port` (including
manual `--port 0` discovery), creating a missing token after game load, or
rotating it requires `plumbmate.bridge.start` or a game restart.

- `plumbmate.status` — whether the bridge is available, hooked, and connected.
- `plumbmate.bridge.status` — connection state, counters, and last error.
- `plumbmate.bridge.stop` — stop binding/pumping until explicit start or game
  restart. Remove only our exact outermost hook; leave foreign wrappers intact
  and disable our retained inner wrapper.
- `plumbmate.bridge.start` — explicitly reload local configuration and resume.
- To keep autostart off from the start, create an empty file named
  `autostart-disabled` in `PlumbMateBridge/state/`.

The socket is loopback-only and authenticated per connection with an HMAC
answer to the server's nonce; the token itself is never sent.

See [bridge reliability fixes and verification](docs/BRIDGE_FIXES_2026-09-05_KO.md)
for the four reviewed defects, regression coverage, and remaining in-game checks.

## Repository layout

```text
companion/       Local provider bridge and CLI (Python 3.11+)
game_mod/        The Sims 4 script-mod source (Python 3.7 compatible)
protocol/        Versioned JSON schemas shared by both sides
docs/            Architecture, safety, token budget, and roadmap
tests/           Offline tests; no game or API key required
```

한국어 프로젝트 소개와 포트폴리오용 데모 구성은
[`docs/PORTFOLIO_KO.md`](docs/PORTFOLIO_KO.md)에 정리되어 있습니다.

기억, 모델 라우팅, Intent Capsule, 관계 시뮬레이션, 개인정보와
배포 안정성을 포함한 목표 설계의 전체 지도는
[`docs/MASTER_DESIGN_KO.md`](docs/MASTER_DESIGN_KO.md)를 참고하세요.
이 문서는 미래 아키텍처를 설명하며 현재 MVP의 구현 완료 범위와는
명확히 구분됩니다.

## Try the companion without the game

```bash
PYTHONPATH=companion/src python3 -m plumbmate.cli init --bridge .plumbmate
PYTHONPATH=companion/src python3 -m plumbmate.cli demo --bridge .plumbmate
PYTHONPATH=companion/src python3 -m plumbmate.cli serve-once \
  --bridge .plumbmate --provider mock
PYTHONPATH=companion/src python3 -m plumbmate.cli show-latest --bridge .plumbmate
```

Packaging metadata is also included for an editable installation.

## OpenAI provider (opt-in)

No API call is made unless `--provider openai` is selected. Configure secrets
only in the process environment:

```bash
export OPENAI_API_KEY="..."
export PLUMBMATE_OPENAI_MODEL="your-supported-model-id"
PYTHONPATH=companion/src python3 -m plumbmate.cli serve \
  --bridge .plumbmate --provider openai
```

The model is intentionally not hard-coded. This avoids silently switching cost
or behavior when model availability changes. Requests use `store: false`; local
compact memory will remain the source of continuity.

## Build the game-side archive

The installed game currently embeds Python 3.7. The build uses that exact
embedded runtime to compile compatible bytecode, then verifies its magic number
against the installed game's `core.zip`:

```bash
python3 game_mod/build_game_mod.py
```

This creates `dist/PlumbMate.ts4script`. Script discovery has been smoke-tested
on game patch `1.127.41.1230`; keep it in a dedicated test folder until the
household command round trip in `docs/ROADMAP.md` is complete.

## Distribution note

An AI-connected mod needs unusually clear privacy disclosure. ModTheSims does
not accept script/core mods that communicate with external LLM providers, so a
future public release should use GitHub plus a suitable release host, provide a
local/mock mode, publish the exact transmitted schema, and never bundle keys.

## Status

- [x] Versioned bridge protocol
- [x] Offline mock companion
- [x] OpenAI Responses adapter with bounded structured output
- [x] Deterministic state delta and token budget estimator
- [x] Python 3.7-compatible game command prototype
- [x] In-game script discovery and import smoke test
- [ ] Throwaway-household command round trip
- [ ] Player approval UI
- [ ] Allow-listed action execution
- [ ] Gemini and local-model adapters
- [ ] Korean localization package and public release pipeline
