# Architecture: implemented foundation vs target product

Updated 2026-09-06. The master RFC map describes a target, not shipped behavior.

## Current responsibilities

- `game_mod/commands.py`: EA snapshot queries, cheat registration/output, bridge startup configuration.
- `game_mod/turn_bridge.py`: bounded file requests/responses and user-facing turn receipts. Runs only from explicit commands, never the tick pump.
- `game_mod/tick_driver.py`: hook ownership, suspension, bounded pump scheduling.
- `game_mod/bridge_client.py`: nonblocking, literal-loopback transport and authentication on the engine thread. No model calls or worker threads here.
- `companion/contracts.py` + `schemas/`: canonical shape/type/length/allow-list contracts, compiled once at companion startup. Provider-generated fields derive from the response contract; identifiers remain locally owned.
- `companion/protocol.py`: bounded JSON file exchange and request enumeration, re-exporting contracts for existing callers.
- `companion/budget.py`: validated context packing and per-process attempt allowance; no billing claims.
- `companion/engine.py`: request validation, context policy, duplicate response check, attempt reservation, provider invocation, final validation, quarantine, status.
- `companion/turn_status.py`: best-effort non-narrative processing/failure receipts, not a transactional action/save ledger.
- `companion/event_socket.py`: loopback listener and pure-Python companion workers. Settings/framing/server can be separated later; no game APIs are called by workers.
- `companion/cli.py`: configuration, service loops, and human-readable progress. Attempt accounting is owned by the engine, not duplicated per command.
- `companion/drives.py`: homeostatic urgency from `actor.motives` and the bounded `drive_state` line the engine adds to a request. Pure function; no model, no action decisions.
- `companion/memory.py` + `memory_text.py`: SQLite memory store (provenance-tagged rows, rule-based recall with a per-turn `retrieval_log` and `retrieval_turns`, single tunable weight row, status-only lifecycle: merge, archive, supersede, dispute, evidence-gated canon promotion). Recording is default, recall is opt-in per session_id until save receipts exist. Never imported by game code.
- `companion/reflection.py`: between-turns batch over the store (relationship summaries per sim pair, Reflexion self-notes from retrieval misses, open promises and disputed guesses). Output is always `ai_inferred`; the `Summarizer` protocol is where a model call would plug in, with a deterministic rule implementation as the only one today.

## Boundaries that must remain true

1. Game code stays Python 3.7-compatible and never imports companion dependencies. The build manifest must include every published local module.
2. Game ticks perform bounded nonblocking socket work, not file polling, model calls, or background EA API calls. Endpoint files are persistent configuration, not liveness signals.
3. No field outside the request schema reaches a provider. Model output cannot override request identifiers or introduce executable commands. A syntactically valid action is still only a suggestion in this prototype.
4. Final responses are validated *after* local transforms. Game reads match the specific request ID; global newest-response lookup is only a developer CLI tool.
5. Unknown save scope means no persistent RP memory restoration by default. The memory store records under the request's `session_id`; feeding recalled rows back into a turn is an explicit `--recall` opt-in, labeled as unverified scope. Model-generated patches are not promoted to observed facts. Existing session files are preserved but ignored.
6. API uncertainty does not trigger automatic retries during normal single-engine processing. Connection retries are independent from paid model retries.
7. Sequential server lifecycle control: stop admissions before sweeping connections. Retain timed-out worker references and refuse restart while old workers survive.

## Resource and privacy limits

Request/response files and socket frames are capped at 64 KiB. Context JSON is
capped at 12,000 UTF-8 bytes; optional items are removed without altering the
current message or actor. Per-field limits come from one schema, not parallel
hand-written validators. Public schema copies are generated and checked by CI.

All CLI engine modes default to 20 model attempts per process, including failed
attempts. The context token estimate is a coarse UTF-8 heuristic and excludes
API envelope/system/schema overhead; it is not provider usage or a money limit.
Daily persisted spend limits and usage reconciliation are not implemented.

Event payload logging is opt-in. Turn receipts contain only request IDs and
fixed status/reason codes. Local request/response archives still contain private
play text; retention, export, deletion UI, and disk quotas remain release work.

## Not implemented yet

The autonomous NPC-like partner, selected partner binding, observed event
atomization, verified save/branch receipts, memory consolidation/reflection, L0–L3 router,
IC scheduler, and relationship features remain separate milestones. Pure
save-lineage and state-delta helpers exist but are not proof of end-to-end use.

A future memory DB can grow without growing every prompt: scope-filtered indexed
retrieval, local ranking, deduplication, and fixed-budget packing must sit between
storage and model context. A byte cap alone does not solve relevance or RP quality.

## Verification and remaining release gates

CI is configured for the installed companion on macOS/Ubuntu, Python 3.11/3.13,
plus game-source 3.7 syntax and schema export guards. Positive stub tests cover
multiword Korean chat through files, mock response, and game display. Local
game-runtime builds check bytecode compatibility separately; neither CI nor a
build proves gameplay.

Before a release: in-game smoke, response/save-scope isolation, request claiming
against multiple consumers or process crashes, actual usage accounting, disk
retention, and an ordinary-player UI. See the Korean UX/context review for the
priority order. Do not mask transport failures as invented character behavior.
