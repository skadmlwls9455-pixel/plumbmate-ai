# Research Report Review and Unresolved Runtime Tests

_English translation of RUNTIME_REVIEW_2026-09-05_KO.md. If the two differ, the Korean original is authoritative._

Reference date: 2026-09-05. Target game: local install 1.127.41.1230.

What to adopt first from Aiden's technical review report is external app separation, staging of execution permissions, and queue occupancy/load order testing. Some of the report's conclusions were inferred without looking at the current PlumbMate implementation, or were asserted more strongly than the cited sources support. The judgments below are based on code, author materials, and local static analysis, and do not mean that actual game tests have been passed.

## 1. What we take from the report and what to correct

| Report claim | Review result and design decision |
|---|---|
| There is precedent for an external app + game bridge | Adopted. The [Sentient Sims App author documentation](https://github.com/guspuffygit/sentient-sims-app) explains that the companion app handles external API communication and mod state/storage. The stability of exact save synchronization/threading is not confirmed by the README. |
| Existing AI mods all only converse and do not act autonomously | Corrected. The [dnavaria/sims4ai execution code](https://github.com/dnavaria/sims4ai/blob/master/sims4_ai_mod/ai_sim_mod/tool_executor.py) has move/inject/cancel attempts. It is a prototype that downgrades to `proposed_only` on failure, so the existence of autonomous action attempts is distinguished from real-game stability. We neither abandon our core feature nor claim it is already verified on the basis of competing cases. |
| Polling below 3 seconds of real time is prohibited | Rejected. The [TURBODRIVER explanation](https://turbodriver.io/simulation) is a diagnostic criterion that counts repeating alarms of 3 game minutes or less as Active tasks. It is not a 3-second real-time lower bound or a universal prohibition. Game tick processing time, alarm counts, and backlog are measured. |
| SQLite should be abandoned and memories put into the game save | This conclusion is unnecessary. External SQLite is kept while memories are attributed to branches and save commits. Whether sqlite3 exists inside the game does not determine whether an external DB is adopted. |
| Slot ID + game time ensures rollback consistency | Insufficient. Consecutive saves during pause and a branch at the same time can have the same key. Verify `save_scope + save_commit_id + watermark` and matching against the actually loaded save. |
| The current PlumbMate accumulates the DB append-only regardless of saves | The current implementation has no SQLite memory storage. The master has a designed save boundary, but it is not yet implemented. A design omission and incomplete implementation are distinguished. |
| Including source `.py` will automatically survive future Python changes | Insufficient grounds, and it also conflicts with the report's `.pyc`-only claim. The cited [SimAI build code](https://github.com/dnavaria/sims4ai/blob/master/sims4_ai_mod/build.py) also uses a 3.7 bytecode generation approach. The claim itself that the loader changed from a specific patch is a separate verification target. Our distribution criterion is testing the installed bytecode and loader. |
| Autonomy v1 is possible only if conflicts with WW are suppressed 100% | Changed to a practical release criterion. Measure verified version combinations, a limited action list, yielding when occupied, halting injection when the state is unclear, and 0 cases of mistakenly cancelling player actions. No guarantee is made for arbitrary combinations of all mods. |

This review did not re-verify all of the report's EA policy, latest version numbers of specific mods, Memory Boost causation, and patch dates. These items will be confirmed with official sources at distribution time. The original research text and third-party code were not copied into the project.

## 2. Actual implementation status

Currently `game_mod/src/plumbmate_mod/commands.py` handles file-based requests/responses and a small snapshot when the user runs a command. Actual action injection, automatic event observation, save integration, a memory DB, and local socket authentication are not implemented.

Waiting on the external API happens in the companion process, but the game command's file reads/writes are synchronous. The manual prototype round trip is not described as a completed production non-blocking collector. In the event-based version, callbacks write only up to an in-memory queue, and workers handle I/O.

PM-Wire is a contract for message meaning, versions, and identifiers, separate from the transport method. If a loopback socket is chosen in the future, it binds only to loopback and includes session authentication, size limits, and rejection of stale messages. It is not claimed that a token alone blocks even a malicious process with the same user privileges.

## 3. Event channel results

| Observation | Path confirmed in the install | Registration method |
|---|---|---|
| Interaction complete | `InteractionComplete → EventManagerService` | Common event handler |
| buff start/end | `BuffBeganEvent / BuffEndedEvent → EventManagerService` | Register the needed event types with the same manager |
| Dominant mood change | `MoodChange → EventManagerService` | Same manager |
| Need values | statistic tracker watcher and continuous statistic threshold callbacks | Registered per tracker/existing statistic of the managed Sim |

The conclusion is "merge a common event receiver + a need receiver into one normalization stage." The static paths are confirmed; live receipt omissions, frequency, and unregistration have not been tested. Specific symbols and member hashes are recorded in the [build evidence table](runtime-evidence/TS4_1.127.41.1230.md).

Acceptance conditions for event tests:

- In a two-way interaction, merge each participant's completion notification and record it as 1 event. Do not confuse the primary actor with other participants.
- Do not promote cancellation, route failure, or reset to a successful hug/meal.
- Even if the order of buff start/end and mood change differs, the latest state is consistent. Do not assume every buff causes a mood change.
- Test direct need changes and natural decay without any action separately. Confirm that thresholds work during pause, at speed 1/3, and after load.
- The number of listeners does not increase after repeated household switches, lot switches, and re-registration. On load, existing buffs are not recorded as new events.
- Callbacks do no I/O or model calls. Record actual game tick time and omission/overflow figures.

## 4. `_plumbmate_decision_id` test specification

Status: **NOT_TESTED — there is not yet an actual enqueue executor or development instrumentation.** Confirmation that `setattr` works on ordinary objects or fake-queue unit tests do not count as passing this status.

Instrumentation fields for each stage:

```text
build, session_uuid, scene_epoch, decision_id, actor_id,
runtime_identity, marker, parent_identity, phase,
ownership_state, cancellation_requested_by
```

`runtime_identity` is a diagnostic value within a session and object lifetime. Neither Python `id(obj)` nor a single game interaction ID is used as a persistent ownership key.

| Test | Marker observation | Product pass condition |
|---|---|---|
| enqueue | Set on the actually created object and immediately re-read | `PENDING` is only intent. `BOUND` when the exact object and injection result are confirmed |
| start | Compare object and marker from waiting → running | The verified object link is kept. Unconfirmed replacement has no cancellation authority |
| continuation | Parent and child objects and their respective markers | Ownership is inherited only when an actual parent → child relationship is observed |
| cancel control group | Player/another mod runs the same action/target | 0 cases where the control group is included in PlumbMate's cancellation targets |
| Sim reset | Previous object, late callbacks, new instance | Previous link ends. Late responses ignored. Only the goal is replanned |
| lot unload | ID, zone, epoch before and after moving | The same numeric ID on the new lot is not mistaken for a previous execution |
| save/load/rollback | Ledger, markers, and save boundary | Replaying past runtime queues is prohibited. Only goals of confirmed commits are restored |

Marker persistence is recorded per stage as `SUPPORTED / UNSUPPORTED / NOT_TESTED`. Even if the marker disappears, the product ownership test can pass if it can be handled safely with the verified ledger. Conversely, even if the marker remains, wrongly granting cancellation authority over player/other-mod actions is an immediate failure. Yielding with `ownership=unknown` during a test is not a success that hides a failure but an explicit safe outcome, and its incidence is recorded separately.

## 5. Reflected in documents this time

The master's event receiver, `PENDING→BOUND` ownership, save commit identification, initial action caps, and asynchronous WorldTrace recording were made concrete. The earlier wording "SQLite write-through immediately on observation" conflicted with the game thread non-blocking principle and was corrected. No gameplay, Mods/Saves setting changes, or dynamic attribute installation tests were performed.
