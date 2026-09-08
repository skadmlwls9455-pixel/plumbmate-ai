# Architecture: implemented foundation vs target product

Updated 2026-09-08 (local checkpoint). The master RFC map describes a target, not shipped behavior.

## Current responsibilities

- `game_mod/commands.py`: EA snapshot queries, cheat registration/output, bridge startup configuration.
- `game_mod/role_binding.py`: pure session-only player/partner binding, object/ID identity fences. No game APIs, persistence or transport.
- `game_mod/role_setup_ui.py`: optional native two-step picker and notification adapter. No Sim creation or model calls; display remains live-unverified.
- `game_mod/turn_bridge.py`: bounded file requests/responses and user-facing turn receipts. Runs only from explicit commands, never the tick pump.
- `game_mod/tick_driver.py`: hook ownership, suspension, bounded pump scheduling.
- `game_mod/bridge_client.py`: nonblocking, literal-loopback transport and authentication on the engine thread. No model calls or worker threads here.
- `companion/contracts.py` + `schemas/`: canonical shape/type/length/allow-list contracts, compiled once at companion startup. Provider-generated fields derive from the response contract; identifiers remain locally owned.
- `companion/protocol.py`: bounded JSON file exchange and request enumeration, re-exporting contracts for existing callers.
- `companion/budget.py`: validated context packing and per-process attempt allowance; no billing claims.
- `companion/dialogue.py`: validates explicit distinct player/partner roles against the snapshot before model execution; projects only relevant role context.
- `companion/pricing.py`, `spend_ledger.py`, `billing_cli.py`: explicit USD rate/limit policy, external-only durable reservations and audited operator reconciliation. No keys or RP text in the ledger.
- `companion/provider_http.py`: bounded official-endpoint HTTPS, no redirects, retries, or remote error-body echo.
- `companion/doctor.py`: non-destructive local configuration and optional authenticated-loopback diagnostics; no model request.
- `companion/engine.py`: request validation, context policy, duplicate response check, attempt reservation, provider invocation, final validation, quarantine, status.
- `companion/turn_status.py`: best-effort non-narrative processing/failure receipts, not a transactional action/save ledger.
- `companion/event_socket.py`: loopback listener and pure-Python companion workers. Settings/framing/server can be separated later; no game APIs are called by workers.
- `companion/cli.py`: configuration, service loops, and human-readable progress. Attempt accounting is owned by the engine, not duplicated per command.

## Boundaries that must remain true

1. Game code stays Python 3.7-compatible and never imports companion dependencies. The build manifest must include every published local module.
2. Game ticks perform bounded nonblocking socket work, not file polling, model calls, or background EA API calls. Endpoint files are persistent configuration, not liveness signals.
3. No field outside the request schema reaches a provider. Model output cannot override request identifiers or introduce executable commands. A syntactically valid action is still only a suggestion in this prototype.
4. Final responses are validated *after* local transforms. Game reads match the specific request ID; global newest-response lookup is only a developer CLI tool.
5. Unknown save scope means no persistent RP memory restoration. Model-generated patches are not promoted to observed facts. Existing session files are preserved but ignored.
6. API uncertainty does not trigger automatic retries during normal single-engine processing. Connection retries are independent from paid model retries.
7. Sequential server lifecycle control: stop admissions before sweeping connections. Retain timed-out worker references and refuse restart while old workers survive.
8. The selected Sim is not the AI identity. Require a matching role binding; reject missing/ambiguous roles before provider/attempt use. Role/zone replacement invalidates display of previous requests. These in-memory fences are NOT verified save lineage.

## Resource and privacy limits

Request/response files and socket frames are capped at 64 KiB. Context JSON is
capped at 12,000 UTF-8 bytes; optional items are removed without altering the
current message or either role identity. Per-field limits come from one schema, not parallel
hand-written validators. Public schema copies are generated and checked by CI.

All CLI engine modes default to 20 model attempts per process, including failed
attempts. The context token estimate is a coarse UTF-8 heuristic and excludes
API envelope/system/schema overhead; it is not provider usage or a money limit.
OpenAI calls additionally require an explicit, unexpired price/limit policy and
a shared persistent ledger. Reserve before HTTP; count the full request input;
record provider usage before parsing the answer. Missing/uncertain billing data
retains the reservation and blocks further paid calls, including after restart.
Daily limits use UTC. This is local rate-card accounting, not an account-wide
invoice guarantee. Mock mode remains free of provider calls.

Event payload logging is opt-in. Turn receipts contain only request IDs and
fixed status/reason codes. Local request/response archives still contain private
play text; retention, export, deletion UI, and disk quotas remain release work.

## Not implemented yet

The autonomous NPC-like partner, durable partner/profile settings, observed event
atomization, verified save/branch receipts, memory DB/retrieval, L0–L3 router,
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
build proves gameplay. Role picker tests use stricter constructor stubs derived
from installed bytecode, not the actual game UI.

The 2026-09-07 installed alpha passed a Korean multiword mock round trip and
idle keepalive/ACK checks in the game. The last two defensive source changes
still need a new build/install; see the exact artifact hash and limitations in
[the checkpoint](CHECKPOINT_2026-09-07_KO.md). The newer alpha.3 role and
notification adapter build is not installed/live-tested yet; see
[the role checkpoint](CHECKPOINT_2026-09-08_ROLES_KO.md).

Before a release: live-verified role UX, response/save-scope isolation,
general durable request claiming (paid IDs are protected by the spending ledger),
real-provider billing validation, disk retention, and an ordinary-player UI.
Do not mask transport failures as invented character behavior.
