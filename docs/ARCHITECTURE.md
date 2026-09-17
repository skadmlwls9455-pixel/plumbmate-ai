# Architecture: implemented foundation vs target product

Updated 2026-09-12 (local checkpoint). The master RFC map describes a target, not shipped behavior.

## Current responsibilities

- `game_mod/commands.py`: EA snapshot queries, cheat registration/output, bridge startup configuration.
- `game_mod/role_binding.py`: pure session-only player/partner binding, object/ID identity fences. No game APIs, persistence or transport.
- `game_mod/role_setup_ui.py`: optional native two-step picker and notification adapter. No Sim creation or model calls; basic display verified on the installed Mac build.
- `game_mod/chat_input.py`: bounded Unicode validation and single-draft/single-use composer tickets, with role/scene fencing. No EA APIs or persistence.
- `game_mod/chat_ui.py`: optional native text-input adapter and non-narrative submission feedback. Calls the same guarded send function as console chat; never retries or polls files.
- `game_mod/chat_ui.py` also owns the user-opened status dialog: exact response-ID and single-use callback fences; closing is inert. Commands own turn identity/dismissal, not the UI adapter.
- `companion/provider_worker.py`: short-lived spawn boundary around blocking HTTP; fixed 1 MiB result buffer, wall-clock deadlines, bounded cleanup. No EA modules, ledger writes, retry or remote cancellation claims. `provider_http.py` retains endpoint/body/redirect validation.
- `game_mod/turn_bridge.py`: bounded file requests/responses and user-facing turn receipts. File functions run only on explicit commands/Send; a pure response validator is shared by both delivery paths.
- `game_mod/reply_delivery.py`: one bounded active-turn watch, real-time throttle/expiry, stale-scope rejection, terminal callback claim. No EA APIs, filesystem, history or provider calls.
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
- `companion/turn_delivery.py`: read-only scope-checked file projection for the authenticated socket. Never enumerates history or invokes a provider. Original request corroborates dialogue scope; failures expose only fixed reason codes.
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
9. Opening/cancelling a dialog does not send. Duplicate or superseded UI callbacks cannot submit. A feedback failure after a committed request must not trigger another request. Drafts never enter memory/archives until explicit Send creates a turn request.
10. Auto/manual reply display share the same pending-turn/role/expiry/duplicate gate. Optional `turn_delivery_v1` is negotiated; queries bypass the gameplay event queue. One lookup is in flight and one response is buffered. Connection loss clears that inbox; resumed reads never regenerate dialogue.

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

Automatic delivery adds no API tokens: a pending-turn lookup at most once per
real second reads bounded files in the companion, not in the game. The server
also rate-limits disk lookups to at most four per second per authenticated
connection (maximum four connections). No whole-directory scans or growing
context/history cache were added. Display expires at 180 real seconds; a paid
call already in progress is not cancelled/refunded by this display policy.
User dismissal uses a separate last-dismissed ID, not the delivered marker.
Both manual and automatic display reject dismissed replies; only explicit Send
creates a replacement request. A status window cannot dismiss a new request,
different role, expired turn or a response already shown while the window was open.
There is no backend queued-request cancellation yet: dismissal only ends display.

Provider deadlines are independent of UI expiry: input count 30s, generation 60s,
each including worker startup with up to 2s additional cleanup. A surviving worker
blocks new worker creation. The parent owns billing and preserves uncertain
reservations even after local worker termination. Normal mock mode spawns no worker.

## Not implemented yet

The console-free entry point, actual-model RP quality, autonomous NPC-like partner, durable partner/profile settings, observed event
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
The alpha.4 input adapter adds hash-pinned native message-method tests, not
native display or IME proof; see [the composer checkpoint](CHECKPOINT_2026-09-09_CHAT_KO.md).
Alpha.5 added automatic socket-to-notification orchestration. On September 10,
the native role picker, basic Korean IME/paste, paused automatic delivery,
manual duplicate suppression and empty composer cancellation were verified in
a separate QA save. Previous manual saves were preserved; native autosave changed
during the earlier QA household transition and its backup remains available.
Alpha.6 adds concise input/receipt copy and one scope-checked expiry system
notice. Neither expiry nor a transport retry authorizes another model call.
Alpha.7 adds an explicit partner portrait override. On September 11 its native
reply portrait, selected-Sim independence, Korean input and automatic/manual
duplicate suppression passed the Mac QA-family smoke test. Expiry UI still needs
its own live test; system notices do not impersonate the partner.
See [the live checkpoint](LIVE_CHECKPOINT_2026-09-10_KO.md) for exact evidence
and installation status; older checkpoint limitations are historical.

Before a release: live-verified role UX, response/save-scope isolation,
general durable request claiming (paid IDs are protected by the spending ledger),
real-provider billing validation, disk retention, and an ordinary-player UI.
Do not mask transport failures as invented character behavior.

### Alpha.9 console-free UI prototype (not live-accepted)

`control_ui.py` owns the notification entry and menu, separate from composer,
role selection and transport. A five-real-second, memory-only lifecycle check
runs before transport binding/pumping, including when no token/client exists.
It owns at most one launcher; zone/household changes and stop/reload close only
that owned dialog. Three failed show attempts per continuous scope bound failure
work. UI exceptions cannot count as transport pump failures. No automatic modal,
request, paid call or gameplay action is generated by this lifecycle.

Role selection can continue into the composer after both selections complete;
cancel is never mapped to role reassignment. Successful send receipts are held
only in scoped, bounded in-memory composer state and exposed in the control menu,
not emitted as another notification or included in the model prompt. Actual
partner replies and actionable failures retain their existing wall paths.

The reusable launcher's server-side response contract was checked against pinned
native bytecode. Client-side persistence, subclass factory construction, and
actual menu navigation are **not verified**. Native notification deletion is not
overridden or undone automatically; `plumbmate.ui` remains a recovery entry until
a permanent phone/Sim menu is implemented and tested. See the alpha.9 checkpoint.

### September 17 modularity checkpoint

`scene_adapter.py` owns read-only active-Sim resolution, bounded snapshots,
household context and resolving the explicitly bound player/partner. It takes
EA services as an argument and imports no game modules, UI, filesystem or network
code. Calls must still run on the engine thread; dependency injection does not
make EA objects thread-safe. It is the future integration point for reviewed
needs/event adapters from the separate memory branch.

`conversation_state.py` owns the five current-turn fields previously scattered
as bootstrap globals, and the shared displayed/dismissed/expired eligibility
policy. It has no I/O or game dependencies and retains no growing history.
The bootstrap records a new turn only after request commit; marking a turn
displayed or dismissed does not imply cancellation of processing or accounting.
Role/scene validation and native presentation remain separate responsibilities.

This is an incremental extraction, not a complete runtime rewrite. Commands
still compose dependencies and own bridge startup/configuration and UI routing.
Extracting those is deferred to avoid combining memory integration, transport
lifecycle changes and UI redesign in the same regression surface. Existing
command entry points and thin private adapter wrappers are retained for now.

### Presentation extraction (second September 17 checkpoint)

`reply_presenter.py` now owns validated reply rendering, explicit partner
portrait routing, console fallback, system failure/expiry notices and display
acknowledgement. It receives state, watcher, notifier, validator and name/text
formatting callbacks; it never imports the bootstrap,
EA services, file bridge or provider. The commands layer still performs the
role/scene, request and expiry fences **before** calling the presenter.

`ControlUI.open_home` owns menu copy and fallback setup instructions. The caller
supplies role names plus a scope-checked receipt and explicit chat/setup callbacks.
Opening the menu does not invoke either callback or submit a request. Input and
pending-status routing remains in the bootstrap for a later bounded extraction;
bridge lifecycle is intentionally unchanged by this presentation refactor.
