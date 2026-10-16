# Foundation Integration Work Plan

_English translation of FOUNDATION_INTEGRATION_2026-09-05_KO.md. If the two differ, the Korean original is authoritative._

Baseline: `96a8b7e` (Lumen), `d45a4d0` (Fable PR #1). This is development branch work, not a release or a main merge.

## Responsibilities and policies decided before the changes

1. The game command adapter handles EA state lookups and console input/output. The network pump stays in the existing `tick_driver`/`bridge_client`. The ban on file I/O in the tick path is kept.
2. The structure/length/permitted fields of requests and responses are defined by **the companion app's packaged JSON Schema**. Runtime validation and the model output contract use the same definition. File reading and quarantine are the responsibility of `protocol`/`engine`, and the validation module does not import the game API.
3. Connection settings are persistent settings. File existence is not used as a liveness signal. Reconnecting to the same address is automatic; port/token changes require an explicit reload.
4. Server start/stop is called from a single control thread. If existing workers remain past the shutdown deadline, references are kept and restarts are refused. Workers of a different generation are not mixed into a new server.
5. Invalid requests are not sent to the provider. Uncertain API failures are not retried automatically. Model memories from an unvalidated save scope are not restored as fact.

## Scope of this implementation

- Regression fixes for multi-word chat, no active Sim, and Unicode path/host input.
- Reviewed and selectively integrated Fable's fixes for server restart, transient accept errors, shutdown races, worker creation failure, and total handshake time limit.
- Single source for the request/response contract, blocking of extra fields, revalidation of the final response, quarantine of corrupted requests.
- Positive integration check: synthetic game stub → request file → mock companion → response read.
- Full regression check and Python 3.7 bytecode build for the actual install. The build is not in-game verification.

## Next steps (not to be claimed as implemented this time)

- In-game smoke test of multi-word chat/stop-start/coexistence with other wrappers.
- Contract and tests for ACK deadlines, keepalive, and migration of the unsent outbox.
- File exchange was separated into `turn_bridge` during this work. Further cleanup of the EA snapshot/connection settings boundary is a follow-up candidate.
- Complete save receipt → branch isolation → storage of observed facts as one vertical feature.
- After actual usage/cost limits, connect model routing and the IC executor step by step.

## Iterative improvement rules

Reproduce the defect → failing check → confirm responsibility boundary → minimal fix → confirm regression/contract/build → update the reality and design documents. The number of tests itself is not a quality goal. Real-game and stub evidence are distinguished, and performance optimizations do not claim numeric effects without measurements.

## September 6: scope supplements following usability and long-term token feedback

- `budget` handles selection and size limits of validated input. The default context JSON is at most 12,000 UTF-8 bytes. The current utterance and actor are preserved, and the additional household and old recent_events are trimmed first. The full history is not attached every turn. This is not an exact token/dollar cap.
- The paid call count limit is managed in one place, the session policy of `engine`. Separate counters are not created per CLI. Failed attempts are also deducted, and after the limit is exhausted, new requests are not silently queued; a rejected state is left instead.
- The request status file holds only diagnostic metadata: request_id, phase, reason_code. Conversation text and keys are not included. Status recording is best-effort and is not treated as a save receipt or a distributed transaction ledger.
- `plumbmate.read` displays failure and limit exhaustion distinctly. However, the companion app connection state and the LLM processing state are different things. Automatic response display/Sim designation/menu UI are later usability stages.
- The count limit is reset by a process restart. A daily amount limit, actual usage accounting, and persistent idempotency that covers collisions/crashes are needed separately. This safeguard is not exaggerated as being those features.

## Selective integration results

The direction of Fable PR #1's fixes to the chat signature, Unicode input, and server lifecycle/authentication deadline was adopted. The 3 server regression checks and the CI configuration were brought in based on the original PR and extended. Checks were also added for confirming previous worker shutdown, a total stop deadline, thread constructor failure, and delayed accept races. The entire branch was not merged, and the endpoint liveness file, paid call retries, and model-patch-based memory restoration were not adopted.

The contract module and the model output schema were built after checking the required-field/no-extra-field constraints in the [official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs). The actual API was not called, and per-model compatibility is a separate verification item. The validator uses the Draft 2020-12 implementation from the [official python-jsonschema documentation](https://python-jsonschema.readthedocs.io/en/stable/validate/).

## September 6 local verification results

- Final offline checks **228, passed twice in a row** (host macOS/Python 3.13). This replaces the earlier intermediate result of 219.
- Positive path Korean multi-word → active Sim stub → request → mock response → read display passed.
- In a 200-turn mock scenario, history did not accumulate automatically in the prompt; separate maximum-field/emoji inputs passed the 12KB context limit. This is not a measured token/performance benchmark or RP quality verification.
- Two kinds of builds with the game's actual Python 3.7 runtime succeeded. Passed checks of the 4 root modules of the produced mod archive and bytecode magic `420d0d0a`.
- After wheel creation, passed a smoke test loading contract resources from inside the wheel rather than the source path.
- Includes public schema drift checks, Python 3.7 syntax/game–companion app dependency boundary checks, and checks for malformed types/unsupported fields/broken links/UTF-8 surrogates/response forgery/session limits/server shutdown races.
- **Not performed**: in-game installation of this fixed build, household play, actual model API calls. No main merge, release, or changes to Saves/Tray/other mods. A CI definition was added, but remote run results are confirmed separately.
