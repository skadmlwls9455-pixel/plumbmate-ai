# Structure–Design Consistency Audit and Integration Handoff

_English translation of ARCHITECTURE_AUDIT_2026-09-05_KO.md. If the two differ, the Korean original is authoritative._

> This document is the audit record of September 5. For the subsequent fix status, follow `FOUNDATION_INTEGRATION_2026-09-05.md` and `UX_CONTEXT_REVIEW_2026-09-06.md`. The "remaining defects" below are as of that time and do not mean they remain as-is in the latest code.

## Scope and verdict

The review covered `main@81da97c`, Lumen's local changes on top of it (195 tests), and `d45a4d0` of [Fable PR #1](https://github.com/skadmlwls9455-pixel/plumbmate-ai/pull/1). The same test count does not mean the same tests/code. The PR was draft/open at the time of review, and the success of the macOS and Ubuntu jobs in [CI run 4](https://github.com/skadmlwls9455-pixel/plumbmate-ai/actions/runs/33967059060) was confirmed via the API.

**Conclusion: there is no need to rebuild it wholesale. The major boundaries for game/external app/transport/provider/save identification are separated. However, each boundary applies different rules, and different responsibilities are piling up in command handling and socket files. Integration contracts and real in-game round-trip verification come before new RP features.**

Per this latest request, deployment, release, and immediate merge are on hold. The game was exited normally from the main menu, but the fixed build was not installed into the actual Mods folder, no household was loaded, and nothing was saved. This document is not an in-game pass report.

## 1. Modularization status

Line counts are an auxiliary value for finding refactoring grounds; the line count itself is not used as a quality score. The figures below are based on this local snapshot.

| Boundary | Current state | Assessment and needed cleanup |
|---|---|---|
| Game ↔ external app | Source roots and Python runtimes separated. The game handles EA lookups and the non-blocking socket; the external app handles the model and server workers | Keep. Sharing code by importing 3.11 companion modules from the game is prohibited |
| `commands.py` 443 lines | Cheat registration, snapshots, file request/response, settings paths, auto-connect lifecycle, global state | Overloaded with responsibilities. First split out `game_adapter` (EA), `turn_bridge` (request correlation), `bridge_runtime` (settings/connection), and keep commands thin |
| `tick_driver.py` 240 lines | Tick schedule and hook ownership. No game imports | Current boundary is good. Keep lifecycle exception and reinstall contract tests |
| Game `bridge_client.py` 431 lines | Transport state machine, buffers, authentication, retries. No game imports | State machine cohesion is appropriate. Do not split it up merely because it is long |
| External `event_socket.py` 585 lines | Token/endpoint files, frames, authentication, event schema, server lifecycle, mock client | Candidate for splitting into small boundaries: settings storage, framing/authentication, server, test client |
| `cli.py` 191 lines | `main()` at 135 lines holds argument parsing, file service, socket service, conversation handling, turn limits | Split into sub-command handlers and service loop. The CLI should handle only assembly/display |
| `engine.py` 76 lines | Single-request handling, validation, quarantine, provider calls | Small in size. The contract for failure policy and save validation must be settled first |
| `save_lineage.py` 340 lines + `save_slots.py` 113 lines | Types, pure identification functions, unit tests | Well modularized. Not yet connected to the actual game save hook/conversation engine |
| `providers.py` 191 lines | Provider interface, mock, OpenAI, response schema | No large plugin framework needed right now. Split when a second real provider is added |

When splitting game modules, the root-level `.pyc` list in `build_game_mod.py` must also change. A regression where only source tests pass while a new module is left out of the distribution archive is checked separately.

## 2. Remaining defects directly confirmed in this review

1. **Multi-word cheat invocation**: Binding three words and `_connection=None` to the local `plumbmate_chat(message, _connection)` reproduces `TypeError: multiple values for argument '_connection'`. Fable fixed it with `*message_parts, _connection=None`. That regression test only checks whether it reaches "active Sim not found" in a fake game module; it does not prove active Sim → request file → response display. Unquoted multiple words, quoted sentences, and Korean/emoji must each be confirmed in the actual console. The report of "100% failure of all multi-word input" should not be quoted in amplified form without checking the quoting/engine parser.
2. **Unicode path index**: In `/Users/İ/Mods/PlumbMate.ts4script/...`, the original suffix index is 23 and the result of lowering the whole string is 24. The slicing in the local `_mod_directory` goes wrong. Fable's search based on the original string is a candidate for adoption. This is a Unicode case-conversion length problem, not a specific OS locale.
3. **Non-ASCII numeric host**: Locally, `_is_loopback('１２７.０.０.１')` is True. This differs from the contract of allowing only ASCII literals and breaks the assumption of a DNS-free connection. Fable's ASCII digit check and related regression tests are candidates for integration. The audit did not itself execute any external DNS calls.
4. **Mismatch between public schema and runtime validation**: The request schema has ID 8–80 characters, actor name 120 characters, session 80 characters, but local validation has ID hex32, name 128 characters, session 160 characters. Arbitrary top-level fields forbidden by the schema also remain in `clamp_request`. It was confirmed that a synthetic `undocumented_field=SYNTHETIC_MARKER` is passed all the way into the `RecordingProvider` input. No external API calls were made.
5. **Response schema omission in my own added changes too**: The `memory_policy` attached by the local engine is not in the public response schema. It does not fit the `additionalProperties:false` contract. The approach of altering the envelope after validation must be fixed first. Current unit tests do not execute the public JSON Schema, so this problem remains even if all 195 pass.

Items 4–5 above are contract defects to resolve before "cleanup refactoring." Simply catching general exceptions unconditionally must not replace schema validation.

## 3. Integration contract with Fable's changes

| Topic | Lumen local | Fable PR | Integration proposal |
|---|---|---|---|
| endpoint | Persistent setting read only at start/explicit restart. Default 47651, reconnect on same port | Reads the file periodically; tracks mtime liveness signal and kernel port changes | Keep the ban on file I/O in game ticks. Do not claim that implicit port changes/token rotation are automated without a separate control path |
| Hooks/stop/reload | Actual wrapper object and class registry, suspend/binder release | Wrapper state/reload driver rebinding, binder release on stop | Keep only one of the two ownership implementations and make it pass both sides' regression tests. stop must work regardless of failure to create a new socket |
| Incoming frames | Buffer first + completed-frame size cap + duplicate-ACK statistics defense | Buffer first, keepalive, migration of unsent outbox, duplicate-ACK defense | Keep the buffer budget; consider adding keepalive and unsent migration. Do not resend events whose transmission result is uncertain |
| External server lifecycle | Audit defects remain in the initial accept/stop path | stop ordering, transient accept errors, full handshake deadline, handling of worker start failure | Prioritize adopting Fable's changes and deterministic tests. Distinguish the deadline from per-recv timeout |
| Provider failure | Uncertain requests are quarantined, no automatic retry | Up to 5 retries, then quarantine | A cost policy decision is needed. Network connection retries and paid model request retries are separate things. Recommended conservative default: do not automatically retry model requests where billing is unclear |
| Memory | No validated save scope, so session memory restoration and model patch promotion are blocked | Ignores corrupted session files and restarts, keeps model-patch-based memory_facts | File corruption recovery alone does not solve the provenance/save isolation problem. Keep the principle model inference ≠ observed fact |
| Automated tests | Local 195, real game Python 3.7 build | A different 195, macOS and Ubuntu CI and 3.7 syntax guard | Review the tests as a union and update tests whose contract changed, with reasons. No simple summed figures |

The PR's server changes were read and their grounds checked, but not all of the individual audit outputs of the forwarded "all 3 rebutters reproduced it" were read. Code, regression tests, and CI evidence are distinguished from the reporter's explanation.

## 4. Initial design vs. actual connection status

| User experience | Current code/evidence | Still needed |
|---|---|---|
| Converse by receiving the Sim's state as small text | Manual file request, mock/selectable provider, response display | Real console round trip including multi-word input; distinguishing the selected AI Sim from the player Sim |
| Observe game events without images | Socket heartbeat and allow-list transport possible | Actual event listeners, Actor/Target and provenance, Scene Atomizer, observation scope |
| A companion with memory and preferences | Save lineage pure functions and documents exist | Save hook/validation receipt, external memory DB, migration, query connection. The current block on memory reuse is a temporary safety measure, not a finished feature |
| A second player that acts on its own | Real general action execution disabled; ownership probe is a separate diagnostic | Sim designation, action catalogue, Dispatcher/Arbiter, player priority, survival, zone/save guards, completion observation |
| L0/L1/L2/L3 and IC | In the target documents | Actual Router/IC scheduler/ToneShadow and state machine not implemented |
| API use with cost control | Input estimates, output cap, attempt count cap for local runs | Actual recording of provider usage, currency cost/daily limit, consent UI. An attempt count cap is not a money limit |

Therefore, the observation that "it worked when tested with Claude" is not denied. Connection/some conversation working, and memory and autonomous action being connected as in the initial design, are different verification stages.

## 5. Proposed maintenance routine

- **Every change**: reproduce the problem → smallest fix → regression test. Do not mix feature additions with large-scale moves/renames in the same commit.
- **Before integrating two lines of work**: check the public schema, failure/cost policy, save scope, and thread/file I/O boundaries via diff. Do not keep two implementations with different contracts together.
- **Common CI**: macOS+Ubuntu, Python 3.7 syntax guard, real JSON Schema contract tests, adversarial fixtures such as non-ASCII/wrong types/partial frames/duplicates/timeouts/calls after stop. The real game build is a separate stage.
- **When one execution path gets connected**: positive E2E from command → request → provider → exact response → in-game display. Fake game module tests are not labeled as engine verification.
- **Small structural check per feature bundle**: split when one file ends up changing UI, policy, and storage at the same time, or when the same rule is modified in multiple files. Do not interpret "periodic optimization" as unconditional code rewriting.
- **Performance after measurement**: measure actual tick p95/p99, processed frames/bytes, queue length, connection retries, API attempts/tokens. Do not substitute test counts, short code, or number of shortcuts for performance metrics.
- **New abstractions when a real use site appears**: do not pre-create every RFC name as an empty class or mass-produce single-method files. The initial split targets are commands/runtime, socket settings/framing/server, and CLI handlers.

## Recommended next order

1. Hand off the Lumen snapshot as a separate branch. Leave main/PR #1 as they are.
2. Agree on the two contracts and merge Fable-specific fixes and Lumen-specific fixes/tests into small integration commits.
3. Align the request/response schemas to a single standard and verify the game build/import.
4. With the fixed build, confirm in-game multi-word chat → response display without saving, plus connect/stop/reconnect.
5. After establishing that baseline, do the responsibility-split refactoring. Then implement in the order automatic observation → a single safe action → validated memory.

The current branch is a working snapshot for integration and does not mean deployment approval, completion of in-game verification, or completion of the PR merge.
