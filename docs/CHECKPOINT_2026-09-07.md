# 2026-09-07 Pause/Resume Checkpoint

_English translation of CHECKPOINT_2026-09-07_KO.md. If the two differ, the Korean original is authoritative._

Follow-up changes: [2026-09-08 role contract checkpoint](CHECKPOINT_2026-09-08_ROLES.md).
What follows is a record as of `7f697a1`; for the subsequent state of the role implementation, follow the follow-up document.

## Know this first

Before the usage reset, the user requested only small tasks that are safe to interrupt. This checkpoint does not hastily implement a new role UI or automatic notification delivery. A loading indicator above the Sim was explicitly excluded by the user. No game saving, creation of additional Sims, paid API testing, or public release is done.

Previous remote baseline: `dd2977d` on `lumen/bridge-reliability`. The changes below are this local work and are not described as reflected on the remote until push/CI is separately confirmed.

## Changes implemented in code

| Responsibility | Module | Verification points |
|---|---|---|
| Pricing and USD limit contract | `pricing.py` | Decimal-based integer amounts, exact model IDs, price expiry, input and output caps, separate cache read/write |
| Persistent cost ledger | `spend_ledger.py` | External app SQLite, call after reservation, duplicate ID blocking, restart and concurrent runs, conservative holding of unconfirmed costs, version 1→2 migration |
| Cost operations commands | `billing_cli.py` | configure/status/reconcile, default limit 0, ledger preserved even on reconfiguration, audit history of manual reconciliation |
| Provider HTTP | `provider_http.py` | 1MiB response cap, no redirects or automatic retries, remote error bodies/keys not exposed |
| Model input | `providers.py` | Local transport IDs and timestamps removed, fixed prompt cache key, input tokens of the full request precomputed, usage settled before answer validation |
| Connection diagnostics | `doctor.py` | Distinguishes folder, settings, and authentication connection; 0 API calls; tokens/conversations/paths not exposed; no automatic creation of missing paths |
| Game connection | `bridge_client.py` | 60-second idle keepalive, 15-second ACK deadline, no re-execution of unconfirmed sends, rejection of duplicate event IDs |
| Game feedback | `commands.py`, `turn_bridge.py` | Reason code for amount blocking; explicit rejection instead of silent truncation of messages over 1000 characters |

The amount limit is **a limit on PlumbMate local calls based on a price table the user reviewed**. It does not guarantee a billing cap for the entire provider account, the actual invoice, taxes, exchange rates, or other apps' usage. It applies to runs that share one price configuration and the ledger next to it. Creating another configuration/ledger makes a separate scope, so in day-to-day operation the ledger is not duplicated or deleted.

That context is also sent to the input-counting API. Generation calls are sent only after the approved configuration, reservation, and input cap are validated. Precomputation failure, missing usage, a different model/service tier, or an unknown send result keeps the reservation. The initial model output cap is configurable, and even if an incomplete response comes out under a small cap, it stops after recording the charge and does not retry automatically.

`budget reconcile` is not a refund feature. An operator who has stopped all companion apps and in-progress calls and has confirmed the provider-side result corrects the ledger with `--request-id`, `--confirmed-usd`, and `--confirm-no-active-calls`. The past state remains in adjustments, and the original request ID cannot be reused. Unconfirmed requests are not automatically treated as free.

## Verification evidence and limits

- Full regression: **261 passed, 11.985 seconds**. Includes duplicate ID, deadline input validation, and cost settlement/migration checks. `git diff --check` and the schema export check also passed. This is a local result, not a remote CI result for this change.
- Synthetic 500-turn benchmark: context JSON average 785→549 UTF-8 bytes, median about 0.164ms, p95 about 0.172ms. This is a single local measurement and does not mean a 30% reduction in token count, full prompt, or cost.
- Confirmed loading of `0.1.1-alpha.2` in the real game `1.127.41.1230`.
- Confirmed the Korean round trip `plumbmate.chat 오늘 같이 산책하고 사진 찍을까?` (“Shall we go for a walk and take pictures together today?”) → local mock → `plumbmate.read`. The screen tool's typeText on the first screen dropped the Korean, and the full sentence was confirmed via the paste path. Separate testing of the user's own direct IME input and of a new input UI is needed.
- Real game connection: confirmed a snapshot with READY, one connection/handshake, all 7 heartbeats ACKed, and failures/lost/errors 0. This does not mean long-duration compatibility or compatibility with all mod combinations.
- `doctor --connect`: all checks passed, 0 external API calls.
- Paid API, actual billing, and the model's RP quality were not tested. Only mock continues to be used until model/amount approval is obtained from the user.
- SHA-256 of the installed test build: `1a3030882a570aa46fe41f335fa7257ed0c581a176742d382d4a12233d587c29`. The duplicate event ID/deadline argument defenses added after installation are not yet in this build. It must be rebuilt from the latest source, installed after exiting the game, and re-verified.
- The previous two mod files were preserved in `dist/install-backups/2026-09-07-alpha2/`. No other CC/mods/Tray were modified and no save command was executed. A check that save file metadata is unchanged after exit still remains.

## Key defect found in actual use

Currently `actor` is the selected Sim. The AI partner has not been explicitly designated, and because the mock answers under the selected Sim's name, it can look as though roles exist. The current household has only one Sim. This is a problem of confusing a successful connection test with the product being usable.

The review criterion is expanded from code/communication passing to **the entire path of a new user's first conversation**. A state without role selection, conversation UI, or readiness status is not presented as finished AI partner play.

## Next work order — one bundle at a time

1. Role contract and fixtures: separate player/AI Sims, prohibit the same Sim, validate different saves/households, guidance when unassigned. Two Sims from the same household were proposed as the first product scope; details of the actual selection UI are not yet agreed.
2. Readiness checks before cost-incurring calls: separate role, connection, provider, consent, and amount. Distinguish the development mock that is possible even in the current unassigned state from actual partner conversation.
3. Input box UI: always show the recipient; show send complete, response preparing, delay, and failure small near the message. Changing the selected Sim must not automatically swap roles.
4. Notification wall: first confirm the actual notification API/signature in the game install. Dialogue and technical errors use different system presentations, and private thoughts are not displayed.
5. Automatic delivery: response ID + role at the time of request + session/scene validity check, display only once, discard late answers. Do not reintroduce file polling in game ticks; design and verify a bounded socket path.
6. Two-Sim synthetic tests and user-permitted real-game scenarios. Do not arbitrarily add Sims to the current one-Sim household.

### UI acceptance criteria

- Do not make a loading indicator/emotion thermometer above the Sim the default.
- Readiness status is first considered as small, clear text/markers near the input panel. Exact wording and layout are not yet finalized.
- "Response preparing" is a system status. Network errors are not hidden behind a narrative of "the character is sulking/hesitating."
- System notifications provide the cause and next action at a low level of interruption. They match the design tone of normal dialogue but do not confuse speaker and personality.
- Automatically test post-send feedback, prevention of duplicate sends, prevention of duplicate display, and blocking of late answers from previous roles/saves.
- A beginner user must be able to reach the first conversation without knowing request IDs, JSON, or the console `read` command.

## Unimplemented items relative to the master design

| Master scope | Current state | Prerequisites |
|---|---|---|
| Attaching a persona to an arbitrary Sim, role settings, multiple AIs | Not implemented | Explicit role and save/scene identification contract |
| save lineage | Only pure identification functions exist | Connecting real-game save begin/commit/abort and watermark |
| Fact/claim/interpretation, typed ref, ANN versions | Design only | Observation provenance, scope, permissions, schema |
| Scene Atomizer, actor/target direction, need/emotion events | Automatic collection not implemented | Per-build listeners, cleanup, duplicate/gap tests |
| RecentEventsBuffer, related memory retrieval/PM-Wire evaluation | Not implemented; only a byte packer exists | Validated events, candidate limits, evaluation fixtures |
| L0–L3 routing, IC, arbiter/action execution | Not implemented | Roles, permitted actions, ownership, survival/player priority |
| Household/lot/rabbit-hole transitions, goal achievement by proxy | Design only | Availability, scene lifetime, success conditions |
| Preferences, knowledge, conflict/reconciliation, promise/trust graph | Not implemented | Directional fact, claim, and memory foundation |
| Anniversaries/cake, routines, caregiving, verbal tics, WorldTrace/Lore | Not implemented | Observation of actual outcomes, retention/consent, grounded lookup |
| Perception/misunderstanding/correction/growth, Thought/ToneShadow | Not implemented | Separation of interpretation and fact, permitted expression and evaluation |
| Cost limits/usage | Implemented locally this time | Real verification per paid model, user-friendly settings UI |
| Keychain, send preview/consent, history deletion/retention UI | Not implemented | Security and privacy review before a public app |
| Input UI/notifications, accessibility, cause explanation/ImmersionGuard | Not implemented | The UX vertical path above takes priority |
| Windows, signing, distribution, long-term/other-mod compatibility | Not complete | Packaging and real-environment combination testing |

Something is not counted as an implemented feature just because its name is in a document. Next, starting with the first user path on this list, code → tests → real-game evidence will be attached.

## Cautions when resuming

- Freshly check the current game/companion app state. The previous test app started with mock and an 8-attempt limit, but do not assume whether it is still running.
- The real Mods write permission received in a previous turn may be needed again.
- Check local changes, the actually installed build, and remote commits separately. If a new user message arrives, it takes precedence over this checkpoint.
- Do not assume new permission has been granted for saving, household creation, or paid API use.
