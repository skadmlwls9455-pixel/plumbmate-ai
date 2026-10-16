# PlumbMate Master Design Map

_English translation of MASTER_DESIGN_KO.md. If the two differ, the Korean original is authoritative._

- Status: Living Draft
- Reference date: 2026-09-05
- Purpose: Track, in one place, the product principles, system boundaries, RFC breakdown and verification items agreed on before implementation.
- Note: This document describes the target architecture. The current prototype implements only the `suggest_only` round trip; memory, autonomous action and relationship simulation are not yet implemented.

## 1. The Product in One Sentence

PlumbMate is a local-first companion system that attaches an AI agent with memory and personality to any Sim in The Sims 4, making it observe, judge and act on its own without ever taking precedence over the player.

The first demo characters are Aiden and Kate, but the code and data model do not presuppose any particular name, gender, relationship, or a single AI Sim.

## 2. Absolute Invariant Principles

1. State observed in the game is the source of truth for world facts.
2. Model output is not trusted; the model's reasoning and dialogue cannot become canon on their own.
3. The player's direct actions take precedence over PlumbMate.
4. The game simulation thread never waits for a response from the model or the companion app.
5. Basic Sims gameplay must continue even if the connection drops or data is corrupted.
6. API keys, save files, screenshots and local paths are not included in model requests.
7. Record richly when storing; when sending to the model, select only the information needed for the current decision.
8. Personality templates can be reused, but memories and relationship history belong to a per-save personality instance.
9. Human-like mistakes are produced as limits on the observation, recall and expression stages, not as random errors that contaminate permanent facts.
10. Relationships are not reduced to a single number; they are treated as directed histories of emotion, knowledge, behavior and interpretation.
11. Subsystems only propose action candidates and modifiers; only the top-level arbiter commits game actions.
12. A player not sharing their feelings or private life is not lying and is not grounds for a trust penalty.
13. Attachment must arise from mutual care, independence and shared change, not from coerced log-ins, guilt, hidden penalties or holding data hostage.

## 3. Boundary Between Current Implementation and Target

### Current Prototype

- Versioned, file-based request/response protocol
- Offline mock provider
- Optional OpenAI provider with constrained structured output
- Small state snapshot generated via an in-game command
- Display of `suggest_only` responses
- `.ts4script` build matched to the game runtime, with load confirmed
- Loopback socket and `Zone.update` tick client. Settings are read only at startup / on explicit restart; an ordinary companion-app restart reconnects on the same port. Local tests in the [reliability fixes and verification record](BRIDGE_FIXES_2026-09-05.md) are kept distinct from earlier in-game confirmation.
- Actual action execution, long-term memory and automatic event detection are disabled

### Target Product

- Event-driven observation
- Support for multiple AI Sims and multiple saves
- Tiered routing across local rules, a lightweight model and a main model
- IC (Intent Capsule)-based mid-term action planning
- Verified memory and a directed relationship model
- Suggest, execute-after-approval, and fully autonomous modes
- Accessible status UI and clear cost and privacy indicators

## 4. RFC Map

| RFC | Title | Core Scope | Status |
|---|---|---|---|
| RFC-001 | Memory, Canon and Save Lineage | Separation of facts, inferences and dialogue; memory retrieval; save branching; annotation versions | Branch-detection subset implemented; not yet connected to game saving |
| RFC-002 | Autonomous Action Runtime | Action ownership, priority, survival control, asynchronous connection and recovery | In design |
| RFC-003 | PM-Wire and Context Budgeting | Dynamic entity aliases, scene atomization, relevant-information distillation, ref verification | In design |
| RFC-004 | Model Router and Intent Capsule Scheduler | L0~L3 routing; IC lifecycle, conflicts, interruption and restoration | In design |
| RFC-005 | Relationship and Preference Graph | Preference knowledge; misunderstanding, explanation and forgiveness; conflict and reconciliation patterns | In design |
| RFC-006 | Lived Experience, Perception and Growth | Anniversaries, routines, verbal habits, proactive actions, learning and growth | In design |
| RFC-007 | Product Safety, Privacy and Accessibility | Key security, transmission consent, cost limits, accessible UI | In design |
| RFC-008 | Compatibility, Testing and Release | Game ABI, coexistence with other mods, fixtures, platform builds | In design |

This document records the contracts shared by all RFCs. Detailed schemas and acceptance tests are split out into the corresponding RFCs.

Per-build engine evidence is tracked in the [runtime evidence table](runtime-evidence/TS4_1.127.41.1230.md), and adoption decisions on external research and open tests are tracked in the [2026-09-05 review](RUNTIME_REVIEW_2026-09-05.md).

The [RFC-001 branch-detection subset](RFC-001_SAVE_LINEAGE.md) and [RFC-007 installation and connection diagnostics](RFC-007_INSTALL_CONNECTION.md) have been split out. It has been confirmed that the current installation runs Python 3.7.0 in the actual game process and that `import sqlite3` fails. SQLite is handled exclusively by the external companion app, and game callbacks have no DB dependency.

## 5. Generalized Identity and Save Isolation

### 5.1 Three Kinds of Identifiers

| Identifier | Lifetime | Purpose |
|---|---|---|
| `game_sim_id` | Game save | Link to the actual Sim |
| `entity_id` | `save_scope` | Stable reference in the DB |
| `s0`, `s1` | Scene or communication session | PM-Wire token savings |

Names are for display and are not primary keys. Fixed aliases such as `A=에이든` (A=Aiden) and `K=케이트` (K=Kate) are used only in documentation examples and test fixtures.

### 5.2 Personality Templates and Lived Personalities

- `PersonaTemplate`: base personality, speaking style, boundaries, preference-learning rate. Contains no memories.
- `AgentInstance`: a personality bound to a specific Sim in a specific `save_scope`. Memories, emotions, anniversaries, preferences and ICs belong here.
- Cloning a template is allowed, but re-attaching a personality that has memories to a different Sim is prohibited by default.

### 5.3 Save Lineage

```text
save_family_id          Same save lineage
save_scope_id           Unique ID of the current branch
branch_seq              Branch number
forked_from_scope_id    Parent branch
save_commit_id          Commit aligning the game save point with the companion DB
```

New saves, Save As, recovery saves and concurrent edits from two devices are not merged automatically. On conflict a new branch is created, and memories and ICs are queried only within that branch.

Initial multi-user support allows only one writer and read-only spectators per `save_scope`. Actual shared control will be supported once a separate `controller_id` and control leases are in place.

### 5.4 Household Switching and Sim Availability

Executability is not determined from `active_household_id` alone. The following runtime availability is tracked for each AI Sim.

```text
household_id
zone_id
is_instanced
is_selectable
is_rabbit_hole
active_household_id
```

`active_household_id` and `is_instanced` do not mean the same thing. A Sim from an inactive household that visits the current lot is instanced and can have a game action queue; conversely, an off-lot `SimInfo` may have no executable `Sim` instance. Therefore we do not assume that "inactive households have no queue."

The initial product policy is to safely freeze PlumbMate execution for a household when the player switches households. Under no setting are actions injected into an AI Sim that is not instanced in the current world or is in a rabbit hole. The IC state itself stays `SUSPENDED`, with `suspend_reason=inactive_household/rabbit_hole/uninstantiated` recorded. State names are not multiplied per reason. Even if an option to control visiting Sims from inactive households is opened later, `is_instanced`, the current zone, a valid queue and user consent are checked separately.

Neighborhood Stories or base-game progression that occurred while playing another household can be reconciled on return as differences in game state, but PlumbMate does not fabricate, as episodic memory, the process behind promotions or relationship changes it did not directly observe. These are marked `external_progression` or `state_delta_unobserved`, and only the current state is resynchronized.

Inside a rabbit hole, action ICs are suspended. Thought candidates are created only when there is a verifiable buff, interaction result, or event linked to an existing inventory item; detailed scenes that took place inside work or school are not arbitrarily generated.

## 6. Events, Facts, Claims and Interpretations

### 6.1 Storage Layers

```text
ObservedFact       Event directly confirmed by the game
PlayerClaim        What the player said; truth not yet determined
Utterance          The fact that someone said something
Interpretation     Interpretation of the AI Sim's emotions, intentions and beliefs
PrivateThought     Internal states such as hopes, worries and hesitations
```

Even if model dialogue includes "You looked like you were having a hard time earlier," the only fact stored is that the sentence was said. That the other person was actually having a hard time requires game observation or explicit canon confirmation.

### 6.2 Action Sources

```text
player_direct
plumbmate
survival_guard
native_autonomy
third_party_script
unknown
```

Source and agency are separate. Each event has `agency`, `observation_scope`, `truth_status` and `retrieval_policy`.

The game's `InteractionSource` is not sufficient evidence of action ownership. Since multiple mods can use the same `SCRIPT`-family value, an action is not judged to be PlumbMate's, or cancelled, based on `source=SCRIPT` alone.

Before injection, `ActionDispatcher` records a `PENDING` intent with `session_uuid`, `scene_epoch`, `decision_id`, actor, affordance and target in the bridge's in-memory ledger. It transitions to `BOUND` only when linked to the exact interaction object via the actual enqueue result or a verified creation callback. This record is forwarded asynchronously to the companion app's audit ledger. Ownership is not assigned based solely on similarity of actor, action, target and time, and a rejected injection does not create execution ownership.

Dynamic Python attributes or custom liabilities are only candidate auxiliary markers, not a required contract. They are not used as grounds for ownership until they pass per-game-build tests showing that the marker survives when the queue replaces or recreates interaction objects or creates continuations. A continuation inherits ownership only when an actual parent→child link is confirmed. A copied marker alone does not grant cancellation rights. If the ledger cannot be reconciled with execution callbacks, it is treated as `ownership=unknown` and not cancelled. If the source itself is not confirmed, `source=unknown` is kept. Reset, unload and reload invalidate existing runtime links.

### 6.3 Retrieval Policies

- `audit_only`: retained for diagnostics but excluded from ordinary RP retrieval.
- `context_only`: usable in the current scene but does not become long-term memory.
- `episodic`: an event that has met verified promotion conditions.

Survival actions are usually `audit_only`, but in response to a direct question such as "What did you do today?" they can be briefly provided as a `ReportableAuditSummary`. This is a factual report on recent self-activity, not a long-term memory.

### 6.4 Promotion and Reference Verification

The AI does not promote memories. Local triggers such as relationship changes, direct reactions, completed two-way interactions or important events determine eligibility. The main model can summarize eligible facts and propose their meaning.

This eligibility is **candidate eligibility**, not a rule to store permanently every time it occurs.
Verification of execution results is kept separate from long-term memory retention. Lower-level actions are grouped per scene in a bounded recent buffer
and selected by considering importance, novelty, redundancy and everyday diversity.
Summaries do not add motives or completion results that were not observed. Detailed boundaries and
synthetic acceptance cases are in the [selective memory policy](SELECTIVE_MEMORY_2026-09-11.md).

A `ref` in a model response must pass the following checks.

1. Was the memory actually included in this request?
2. Is it a memory owned or observed by the current personality?
3. Is it in the same save branch?
4. Is it not expired, deleted, or `audit_only`?
5. Does it not contradict the current target and time?

A `ref` is not proof that the model actually read the memory; it is a declaration that the response was based on that memory. Nonexistent references can be blocked, but semantic misuse is measured with an evaluation dataset.

References are typed rather than mixed into a single integer space.

```text
F:31   ObservedFact
C:12   PlayerClaim
U:91   Utterance
A:18   Interpretation/ANN
T:7    PrivateThought
```

`PlayerClaim` is also subject to ref verification but cannot be used as fact. An utterance grounded in `C:12` must be expressed as the fact of a statement or a tentative belief, as in "You said you were having a hard time," and is not promoted to an `ObservedFact` assertion.

### 6.5 Interpretation Versions

Facts are not changed; interpretations are stacked as versions.

```text
ANN31.v1 hurt          SUPERSEDED
ANN31.v2 understanding ACTIVE, supersedes=v1
```

Ordinary retrieval uses the latest active interpretation; retrospectives on emotional change also query earlier versions.

## 7. PM-Wire and Context Budget

### 7.1 Processing Flow

```text
Scene Atomizer
  Decompose the current scene into entities, actions, places, targets and attributes
→ Candidate Index
  Query only preferences and memories whose structural tags overlap
→ Relevance Gate
  Check observation, confidence, cooldown and IC relevance
→ Cue Distiller
  Distill multiple values into one or two reaction cues
→ Budget Packer
  Assemble the packet within the selected model's token budget
```

### 7.2 Separating the Source Record from the Wire Format

The local DB preserves detailed structured events. The model is sent a provider-specific, verified compact JSON or PM-Wire representation. There is no guarantee that abbreviations that look short to humans also use fewer actual tokens, so JSON, compact JSON and the DSL are compared with real tokenizers and accuracy tests.

```text
PM1|scope=q7|scene=203|seq=918
ENT|s0|role=agent
ENT|s1|role=controlled_sim
N|s0|H3,E2,B0
R|s0>s1|F68,R42,dF+5
M|31|care.food|pol=EP|vis=DI|w=92
Q|actor=s0|NEXT
```

Change events are sent with `seq`, but small absolute state such as the current lot, participants and essential needs is included in every request. A full sync is performed when an omission or scene change is detected.

### 7.3 Selective Activation of Preferences

If the scene involves a black tweed dress, the 20 food and place preferences are not read.

```text
appearance.clothing.color.black
appearance.clothing.material.tweed
appearance.clothing.style.old_money
```

Only exactly overlapping preferences, associations with the current target, and the strongest positive and negative are kept and turned into a single cue.

```text
CUE|target=outfit:s1|effect=mixed
neg=black:-.71|pos=tweed:+.82|expression=optional
```

Having no preference information is different from a first experience. Based on exposure records, `UNKNOWN_NEW`, `UNKNOWN_FAMILIAR` and `UNKNOWN_UNOBSERVABLE` are distinguished.

### 7.4 Short-Term Working Memory

A per-agent `RecentEventsBuffer` sits between the current Scene and long-term episodic memory. The default range starts at 2~4 hours of game time, but it is not cut by time alone; importance, unresolved status and scene transitions are considered together.

- Fires, conflicts and promises: retained for a certain period even if the scene changes
- Ordinary movement and meals: decay quickly or are summarized locally
- Unobserved events: not placed in that agent's buffer
- Usable in the current conversation and IC even before promotion to long-term memory

The buffer is a ring structure with item and token caps, and the full set of raw events is not re-searched every turn. On game restart, only verified items matching `save_commit_id` are restored.

### 7.5 Group Conversations and Audience

A single `target_sim_id` is not used as the protocol's basic unit. All conversations and social ICs use arrays.

```text
participants[]   Current conversation group
addressees[]     Those the utterance is directly addressed to
observers[]      Entities that actually heard or saw it
focal_target     Optional central target
audience_version Group composition version
```

Even a single target is expressed as an array of length 1. When group members enter or leave and `audience_version` changes, late model responses are re-verified. Private memories between two people, Thoughts and sensitive topics are excluded from utterance candidates when the current audience falls outside the permitted scope.

### 7.6 Event Reception and the Scene Atomizer

Static analysis of the current installation shows that interaction completion, buff start/end and mood changes go through the common EventManagerService, while changes in need values use a separate statistic tracker watcher and threshold listener. These registration paths are per-build capabilities, and in-game reception frequency, drops and recovery behavior require further testing.

- Common event receiver: subscribes only to the needed kinds of interaction lifecycle, buff lifecycle and mood change.
- Need receiver: observes only existing statistic instances of managed Sims, combining a watcher with danger-threshold listeners. No needs or statistics that did not exist are added for the sake of observation.
- Receiving callbacks place only brief observation data into an in-memory queue. DB, network and model calls and action injection are not executed inside callbacks.
- In the next processing interval, data from both receivers is normalized into common events carrying `save_scope`, session, scene epoch, bridge seq and source channel. Game object references are not passed to the companion app.

Since a completion signal for one interaction can be emitted for multiple participants, events are deduplicated by execution instance and phase, and per-participant notifications are merged. That participant list does not mean it is evidence that every Sim actually observed or heard it. `InteractionComplete` or leaving the queue is not a sufficient condition for goal success; cancellation, reset, results and `success_predicate` are checked separately.

Direction verification: the receiving `sim_info` of an event callback is not used as the Actor. The original interaction's actor, resolved target/addressees, callback recipient and actual observers are preserved as separate fields. Processing a Kate→Aiden hug in Aiden's receiver does not reverse its direction. If the Actor/Target is unknown or conflicting, or there is a role swap, the event is quarantined as `direction_unverified` and not promoted to a directed CareEpisode. Per-participant deduplication must not overwrite these fields.

Buff changes and mood changes are not double-counted as separate causal events; their states are merged. Causality is not assigned from callback arrival order alone. We do not assume that a single general watcher detects all naturally decaying needs; threshold listeners and bounded state re-checks are used. Values are not re-read inside the watcher in a way that triggers re-entry. Notifications during load are handled as baseline state synchronization, and buffs that already existed in the previous session are not remembered as new events.

Listener registration must be idempotent per active scope. On household/zone switches, Sim reset/de-instancing and deactivation, registration handles are reclaimed and re-registered on the new instance. Buffer overflow records a gap and the need for resynchronization. We do not assume that every event was persistently stored.

## 8. Model Routing

| Tier | Responsibility | Call Cost |
|---|---|---|
| L0 Local rules | Survival, queue recovery, ref verification, importance and promotion eligibility | None |
| L1 Local automation | Simple actions within the current IC, repeatable safe actions | None |
| L2 Lightweight model | Short everyday dialogue and low-risk choices | Low |
| L3 Main model | Relationship branching; conflict, reconciliation and promises; meaning interpretation | High |

Routing is decided not by the model but by the local engine, based on `salience`, `risk`, `memory_dependency`, `novelty`, `uncertainty` and cost limits. When the main model is slow or fails, important decisions are not silently downgraded to the lightweight model.

Dialogue produced by the lightweight model is only an `utterance`, not a factual memory. The main model also cannot create or promote facts; it proposes directed emotions and meanings as annotations on verified facts.

## 9. IC (Intent Capsule) Scheduler

Rather than choosing every action, the main model creates mid-term intentions that persist for a certain time.

```text
IC|actor=s0|goal=comfort:s1|tone=gentle
mem=31,47|avoid=flirt,leave
active_budget=15gmin|stop=player_override,conflict,need4
```

Each AI Sim has:

- One foreground IC
- A stack of suspended ICs
- A queue of waiting ICs
- Background constraints that apply across multiple ICs

IC states:

```text
CREATED → QUEUED → ACTIVE
                    ├→ SUSPENDED → ACTIVE
                    ├→ COMPLETED
                    ├→ EXPIRED
                    ├→ FAILED
                    ├→ CANCELLED
                    └→ SUPERSEDED
```

Instead of increasing the number of states, the causes of completion and suspension are recorded as metadata.

```text
status=COMPLETED
resolved_by=plumbmate / player_direct / environment / third_party
resolution_event=F221

status=SUSPENDED
suspend_reason=player_override / rabbit_hole /
               inactive_household / offline / resource_locked
```

An IC determines completion by `success_predicate`, not by the name of the interaction it intended to run. If the goal is "comfort Kate," it can be completed or partially progressed even if the player hugs first or the tension is resolved by an environmental change. If `resolved_by=player_direct`, a relationship-interpretation candidate of "the other person did what I was about to do" is created, but the actual target, result and emotional change are verified.

Player intervention by default suspends rather than discards the current IC. After the player's action, the target, lot, emotions, deadline and success conditions are re-checked to resume, complete or expire it.

TTL is divided into active execution time and an absolute deadline. Expiry does not automatically call L3 again; only important unfinished events are re-planned.

### 9.1 Restart Restoration

- Permanent: facts, memories, anniversaries, personality
- Resumable: IC goal, progress stage, remaining time
- One-time: communication aliases, API requests, object locks, game action handles

A small checkpoint is saved at every IC transition. On restart, the actual action queue is not replayed; only the IC goal is restored as `SUSPENDED_RESTORE` and then re-planned in the current world. Checkpoints must match `save_commit_id`, and when an earlier save is loaded, future ICs or memories must not be mixed in.

The save boundary is built as a two-phase protocol: `SAVE_BEGIN → SAVE_COMMITTED/SAVE_ABORTED`.

```text
SAVE_BEGIN(attempt_id, watermark_seq)
  → Pause PlumbMate action injection
  → Pin the IC checkpoint at the watermark as pending
  → Buffer subsequent events into the next working epoch

SAVE_COMMITTED(attempt_id, slot, generation)
  → Atomically link the pending checkpoint to save_commit_id

SAVE_ABORTED(attempt_id)
  → Discard the pending boundary and continue the working epoch
```

The game thread does not wait for confirmation from the companion app. Whether the game side has reliable save start and completion hooks is verified by RFC-008 capability tests. On builds where a completion signal cannot be obtained, we do not claim to support exact IC restoration; on reload, ICs are handled as `NEEDS_REPLAN` or expired, and the unconfirmed timeline is quarantined.

Save commits are not identified by slot ID and game time alone, because consecutive saves while paused, branches at the same time, and restoration from older backups could not be distinguished. SQLite stays in the companion app, and queries go only up to the commit/watermark confirmed to be linked to the save actually loaded. Save-file corruption is not presumed merely because a separate SQLite write and a game save occur at the same time. The core problem to solve is the consistency of the two timelines rather than concurrent file writes.

Autosaves also preserve a per-actual-save-attempt `attempt_id` and immutable IC checkpoint/watermark. Intermediate commits are not skipped by an elapsed-time throttle. Only redelivery of the same attempt's signal is idempotently removed; different attempts are distinct even with the same ticks. Only heavy summarization, compaction and index maintenance are batched and deferred, and confirmation of save success is not confused with cleanup of pending state from failed attempts. On buffer overflow, the state is left as gap/unconfirmed and not merged into an arbitrary latest commit.

### 9.2 Top-Level Action Arbiter

`ToneShadow`, `RepairPattern`, `DYAD_PREF`, personal routines and `ProactivePlanner` do not put actions directly into the game queue. All of them produce only the same `BehaviorProposal`.

```text
BehaviorProposal
  actor, goal, candidate_action
  priority_band, utility
  preconditions, conflicts, permissions
  cost, expires_at, evidence_refs
  explanation_codes
```

`PolicyKernel` first checks player priority, survival, consent, cost, object availability and other mods' locks as hard gates. Then `ExecutiveArbiter` selects one of the candidates that passed, and only `ActionDispatcher`, alone, writes to the actual queue.

With multiple AI Sims, neither a single giant arbiter nor fully independent arbiters are used.

- `AgentExecutive`: one per AI Sim. Manages its own ICs, personality, ToneShadow and candidate ranking.
- `WorldCoordinator`: one per save session. Coordinates shared targets, shared objects, multi-party interactions and cost limits.
- `ActionDispatcher`: one per session. Puts only finally approved actions into the game queue.

Group ICs take TTL-bounded leases on participants and resources, acquiring all resource keys in a stable order to avoid deadlock. One agent's personality judgments are not overwritten by another agent, but WorldCoordinator serializes actions that simultaneously occupy the same Sim or object.

Each subsystem's role is strictly limited.

- `ToneShadow`: modifies only tone and candidate scores; issuing action commands is prohibited
- Relationship patterns: propose candidates such as reconciliation or distancing
- Preference conflicts: provide relevant context and constraints
- Proactive planning: provides goals and fallback candidates
- IC: provides the current mid-term intention and success/suspension conditions

Among equal priorities, selection is by current IC consistency, likelihood of success, low intrusiveness, low cost, avoidance of recent repetition and a deterministic tie-break, in that order. Safety and player authority cannot be overturned by a numeric score.

## 10. Autonomous Action and Connection Recovery

Action priority:

```text
player_direct
> survival_guard
> urgent_relationship_event
> active_plumbmate_IC
> lightweight_routine
> native_autonomy
```

Companion app connection states:

```text
DISABLED → CONNECTING → ONLINE
              ↓          ↓
            BACKOFF ← DEGRADED
```

The game tick does not wait for communication responses. In the initial product, no background game/communication listener runs inside the game. The candidate transport briefly processes non-blocking loopback I/O in a verified game tick callback, and the EA C API and all Sim/zone/queue queries and actions run only on the game thread. It requires caps on message count, bytes and processing time; partial send/receive buffers; immediate return on `EWOULDBLOCK`; and yielding on connection loss. Hidden waits in not only connect/accept/recv/send but also DNS, file and log I/O are checked. API, SQLite, heavy JSON processing and retry workers live only in the external companion process. This is a transport/runtime constraint, not a change to the PM-Wire semantic schema. It does not mean the current manual file-bridge MVP already implements this candidate transport.

The [Lot 51 creator documentation](https://github.com/lot51/core-library#gameupdate-) warns of possible immediate crashes from tick-based socket loops and calling EA C functions from a separate thread. We do not stretch this into a measured result that every Python thread will necessarily crash. We do not reproduce dangerous calls in the actual game to confirm the failure.

On connection loss:

- Stop injecting new PlumbMate actions
- Safe actions already in progress may complete
- Discard unexecuted model responses and stale requests
- ICs go to `SUSPENDED_OFFLINE`
- Return to native autonomy
- Rate-limit notifications and error logs

On reconnection, protocol, game build, session, `save_scope`, scene and latest `seq` are exchanged, and the full state is resent if needed.

### 10.1 Queue Conflicts and Retries

Bounded exponential backoff with jitter is applied to consecutive failures with the same action, target and failure cause. Only transient occupancy and routing failures are retried; permanent failures such as object deletion or insufficient permissions are immediately sent to another candidate or to IC re-planning. When another mod has locked the Sim, retries are not attempted blindly but halted until a state-change signal.

Each IC has a retry budget and maximum wait time; when the budget is exhausted, the `FAILED` reason is recorded in the DecisionTrace. Backoff uses both monotonic real time and state-change signals so that it does not run away with changes in game speed.

Initial execution validation caps each Sim's pending (not yet executed) PlumbMate actions at 1 and starts from a low-risk allow-list. Game queue capacity is not hard-coded as the number 6; the current capability and existing occupancy are checked. Communication and observation intervals are determined by per-tick processing time, event count, accumulated backlog and actual latency. The game-time-based alarm diagnostic criterion is not turned into a prohibition on 3-second real-time polling.

### 10.2 Sim Reset and Queue Lifecycle

PlumbMate does not create, replace or force-reset the game's internal queue objects. If an exception occurs during queue access or the game's Sim reset signal is received, it recovers in the following order.

1. Invalidate that Sim's PlumbMate action leases and proposals not yet injected.
2. Discard the previous `Sim` object and queue handle, keeping only the `SimInfo` identifier.
3. On the next stable game tick, re-check the `Sim` instance in the current zone and queue availability.
4. Do not replay the game action that was running; send the IC goal to `NEEDS_REPLAN`.
5. Prevent repeated resets with exponential backoff and a circuit breaker, and maintain native game autonomy.

The correlation among T-poses, LastException and Sim reset is a subject for reproduction tests and is not assumed to have a single cause. On builds with no evidence that a specific exception sets the queue object to `None`, PlumbMate must not try to restore the queue directly.

## 11. Relationship and Preference Graph

### 11.1 Directed Preference Knowledge

```text
PREF(owner=s0, spicy_food=-.71)
KNOWS(knower=s1, owner=s0, pref=spicy_food, acknowledged)
```

The fact that an action ran against a preference is different from inferring the intent "they ignored it knowingly."

```text
PreferenceMismatchIncident
→ IntentAttribution
→ EmotionalImpact
→ RepairEvidence
→ Resolution
→ RelationshipPattern
```

A player's "Sorry, I forgot" is stored as an unverified explanatory claim. The agent can tentatively believe or doubt it depending on trust and history of repetition; verifying truth and forgiving are separate.

Unverifiable doubts get an `EpistemicTTL`. After the TTL, the statement is preserved but the active doubt transitions to `unresolved_closed` so it is not recalled every turn. It can be reopened if a contradiction or repetition occurs.

Reactivation is tied to the evidence score of `PatternSketch` and has hysteresis.

- A strong, directly verified contradiction: reactivate immediately
- A clear, high-importance violation on the same topic: immediately or with high weight
- Weak, ambiguous events: reactivate only when the cumulative score exceeds `reopen_threshold`
- The closing threshold is set lower than the reopening threshold to prevent oscillating open and closed with each event

A single ordinary event right after the TTL does not automatically revive an old doubt.

### 11.2 Three Layers of Conflict

- `acute_tension`: current hurt feelings; relatively fast decay
- `pattern_weight`: history of repeated events; slow change
- `relational_trust`: directed expectation of being respected

A single act of consideration can reduce current tension but does not erase a repeated pattern. Apologies and consistent behavior over time restore relational trust.

### 11.3 Preference Conflicts and Compromise

Each Sim's preferences and knowledge about each other are stored independently. `DYAD_PREF` does not decide who is right; it records conflicts and compromises arising from shared choices.

```text
DYAD_PREF|participants=s0,s1|topic=spicy_food
state=unresolved|activation=dormant
solution=separate_sauce|solution_status=working
```

It is activated only when a related food or joint choice comes up again and cooldown, concession imbalance or compromise-failure conditions are met. A compromise that is already working is carried out by L1 and not renegotiated with L3 each time.

### 11.4 Cost of Relationship Pattern Analysis

Each event only updates the counters of the local `PatternSketch`. The L3 `PatternConsolidationJob` runs only at pattern thresholds, relationship turning points, retrospective requests, or when it can be bundled with an already scheduled L3 call. System housekeeping is not an IC; only scenes in which the Sim actually thinks are a `reflection IC`.

### 11.5 Relationships Involving a Third Party

The agent's attitude toward other Sims is computed from the observed triangular context, not from a single relationship value between two people.

```text
TRIAD|observer=s0|partner=s1|other=s2
observed_closeness=.63|confidence=.58
interpretation=friendship|jealousy=optional
```

The AI Sim does not assume it knows the significance of a third party without an introduction, direct witnessing or a verified game relationship. Jealousy affects directed interpretation and ToneShadow only when explicitly permitted in the PersonaTemplate, and it does not automatically generate possessive or controlling behavior or relationship penalties. Even if candidates of kindness, wariness or distancing toward a third party are proposed, ExecutiveArbiter decides the final action.

### 11.6 Promises and Reliability

"I'll be back early today" is parsed as a `Commitment` candidate with a deadline and target. Actually being late is a fact of an unfulfilled promise, but it does not mean there was an intent to lie from the start.

```text
Commitment → Fulfilled / Unfulfilled / Excused / Cancelled / Unknown
```

Trust is not a single global `player_reliability` number but directed beliefs by domain, such as keeping promises, punctuality, factual claims and emotional openness. Explanations and apologies are linked as claims and interpreted according to trust and past patterns. Quitting or pausing the game and keeping feelings private are not counted as broken promises or lies.

## 12. Lived Experience Features

### 12.1 Milestones and Anniversaries

First meeting, first kiss, first time cooking together, first trip, first conflict and first reconciliation are created from verified completion events. If the past cannot be confirmed in an existing save, it is marked `first_observed_since_plumbmate` and not asserted to be a first in the Sim's life.

Anniversaries are managed as `PENDING → DELIVERED_UNCONFIRMED → ACKNOWLEDGED/MISSED/DECLINED/EXPIRED`. Unconfirmed anniversaries are re-attached only a limited number of times, to the next compatible social IC.

### 12.2 Proactive Preparation

An action like a birthday cake is created as a goal IC, "celebrate the birthday," not "bake a cake." Available objects, skills, funds, time and user-granted spending and outing permissions are checked.

```text
Bake a cake
→ If impossible, prepare a favorite food
→ If impossible, a gift or celebration interaction
→ At minimum, mention the birthday first
```

Preparation can be hidden as a private thought, and it is not retried indefinitely on failure.

### 12.3 Reconciliation Language

In a certain window after a conflict, targeted caring actions, tension reduction and completed interactions are observed. Reconciliation styles such as `silent_proximity` and `care_through_food` are proposed from repeated directed behavior, but not made into a forced script.

### 12.4 Verbal Habits

Only phrases the player typed directly are tracked locally by normalization, frequency, context and recency. Common short interjections are excluded, and the agent notices them as affectionate, worried or conflict tone only when there is sufficient evidence and the cooldown permits. Original text is stored locally, with support for review, deletion and disabling.

### 12.5 Time Together and Personal Routines

Statistics on the same lot, the same room, direct interactions and time of day are aggregated locally using game time excluding pauses. Mere co-presence is distinguished from activities done together.

Personal routines are learned only from repeated autonomous choices made when the other person is not present. Actions ordered via `player_direct` are not the Sim's own habits. Return retrospectives are generated only when there are actual events, and activities during time when the game was off are not fabricated.

### 12.6 Care Received and Things Done First

Actions the player takes toward the AI Sim are also processed through the same event, observation and interpretation pipeline. "The player clicked" is not immediately counted as affection; the target, need, timing, completion result and the AI Sim's preferences are considered together.

```text
CARE_EPISODE|giver=s1|receiver=s0
action=bring_food|source=player_direct
need_match=hunger|completed=true
perceived_intent=care|confidence=.74
```

The following directed lists are maintained:

- Events where the other person reached out first
- Events where they took care of me when I was sick
- Events where they remembered and chose according to my preferences
- Events where they tried again after failing
- Events where they respected my boundaries
- Things we learned together and ways I changed because of them

This is not a scoreboard that automatically increases attachment but the basis for directed emotions and interpretations of growth. If the AI Sim received unwanted help or a gift that doesn't suit its taste, gratitude and discomfort can coexist.

### 12.7 World Traces of Time Spent Alone

Not all of the AI Sim's actions during absence are reported as return dialogue. Some results that actually remain in the game world are linked as `WorldTrace` so the player can discover them on their own.

```text
WorldTrace
  cooked_food, unfinished_project, moved_object,
  inventory_change, skill_progress
```

The initial version uses only traces that can actually be confirmed and maintained in the game. Details the base game state does not provide, such as a book's dog-eared page, are not fabricated. Custom props and states are enabled only when a separate capability exists.

For WorldTrace, the game thread creates a small in-memory event at the moment the event is observed, and the companion app worker records it asynchronously in the SQLite ledger. Write-through, where a game callback waits for a DB commit, is not used. Possible loss before save confirmation and buffer overflow are marked as gaps, and records are attributed to the save scope, working epoch and committed watermark. Past events are not reconstructed by later reverse-scanning the save file, and who did what is not inferred merely from objects currently remaining. Re-checking the current world and identifying save commits are separate, permitted operations.

Object traces such as `moved_object` and `cooked_food` store not only the object ID at the time of observation but also the definition ID, zone/lot, owner/creator, observation time and a bounded state fingerprint. A DB record is historical evidence that "the event was observed at the time," not a guarantee that the object still exists. After return or load, object IDs that cannot be matched in the current world are downgraded to `historical_only` and not used for present-tense dialogue or as interaction targets.

RFC-008's `WorldTraceCapability` records the following for each trace and must pass actual save/load tests.

```text
detectable
persists_save
stable_identity
required_pack_or_mod
restore_validation
lifetime = durable / session / derived
```

Emotion buffs and `mood_aftereffect` are excluded from WorldTrace. Affect and buffs directly observed in the current session can be used only as short-term state in Scene/RecentEvents, and are not grounds for household-switch catch-up or reverse inference from saves. Traces whose actual objects/state could not be re-confirmed after loading cannot be used as grounds for present-tense dialogue.

When relevant traces exist, a situational conversation topic such as "What have you been up to?" can be offered, but the answer is not revealed via a pop-up. There is no penalty if the player doesn't ask, and some traces may go by unexplained.

### 12.8 Things Learned Together

A `LearningEpisode` is not created merely because two Sims were in the same space. Causal evidence is required, such as direct teaching, joint performance, an immediately following skill increase or repeated behavioral change.

```text
LEARN|learner=s0|teacher=s1
domain=cooking.technique
evidence=mentor_interaction,skill_delta
confidence=.91
```

Learning outcomes are divided into three layers.

- Game knowledge and skills: verified skills and interactions
- Procedures and habits: recipes, reconciliation styles and daily routines learned together
- Meaning: a directed ANN of "I learned this from them"

The goal is not to make the AI Sim incompetent so that it depends on the player. It shows that the Sim can live on its own yet has become a different person than before because of experiences shared with the other.

### 12.9 Reciprocity Loop

```text
Player's action
→ The AI Sim observes and forms meaning
→ Tone, choices and growth change
→ New actions and traces are left in the game world
→ The player discovers or asks
→ Leads to habits and memories unique to the two of them
```

It should not be a one-way service in which only the agent learns about the other; the player's choices must also leave lasting changes on the agent's personality and world.

### 12.10 Generations and Lore

A Sim's death or a generational transition is not a deletion trigger that immediately turns all source records into a single piece of LLM text.

- High-value facts, milestones and the evidence memories of living Sims are preserved
- Routine `audit_only` source records may be pruned according to retention policy after deterministic aggregation
- A deceased Sim's AgentInstance is converted to a read-only archive
- `Lore` is a versioned summary with source fact IDs and provenance
- Descendants can know only Lore they heard directly or had passed on to them, and do not automatically inherit the deceased's private thoughts

L3 distillation is not called immediately at every generational transition but is scheduled into the next permitted consolidation budget. Before source records are physically deleted, there must be structured minimum evidence and a retention/export path according to user settings.

## 13. Perception, Human-Like Mistakes and Learning

### 13.1 Two Stages of Reading Emotions

The actual emotional state held by the game is separated from the state the AI Sim has noticed.

```text
ActualAffect        The game's buff/emotion source of truth
PerceivedAffect     State estimated by the agent from observable cues
```

Perceptibility uses being in the same space, observation time, the outward visibility of the emotion, the agent's sensitivity and relationship closeness. Closeness affects confidence and permission to express rather than changing whether the emotion exists.

- Low closeness: notices but doesn't say anything, or shows general consideration
- Medium closeness: checks carefully, like "Are you okay?"
- High closeness: mentions it more specifically if there is a verified repeated pattern

`PerceivedAffect` is an estimate and is not promoted to the actual emotion source of truth.

### 13.2 Calibrated Fallibility

Intentional inaccuracy does not inject random false memories. It arises naturally only from the following causes.

- Not having directly observed it
- Possible source confusion between similar events
- Old, low-importance memories
- Low learning confidence
- Fatigue, tension or distraction
- Not knowing the other's patterns well due to low closeness

The expression stage varies with confidence.

```text
High      Recall assertively
Medium    Hedge, like "If I remember right…"
Low       Ask, or don't mention it
```

Human-like mistakes are not applied to safety, consent, boundaries, canon pinned by the player, API and cost settings, or survival decisions. Action failures such as baking a birthday cake on the wrong day are not the default; if the date is uncertain, the agent checks or holds off on the action.

Mistakes occur only at the expression or selection stage and do not modify the fact DB. The player's corrections are linked as new events and update subsequent beliefs. There is a cooldown that prevents repeating the same mistake, a per-session error budget, and a user-selectable `humanlike_fallibility` intensity.

The subjective memory the agent uses when recalling does not perfectly copy every field of the source fact. Depending on attention and observation scope at the time of the event, each field such as place, time and order has its own `encoding_confidence`. The fact DB keeps preserving something that happened at home as home, but an agent with low confidence in its place memory may confuse it with a café or say "I think it was at a café, if I remember right."

This error is permitted only when a similar candidate exists and field confidence is low. If the player corrects with "It wasn't a café back then," a `CorrectionEpisode` is created and the subjective belief is updated. This mechanism is not applied to safety, boundaries, pinned canon or important anniversaries.

### 13.3 Per-Domain Learning Rates

Not everything is learned at the same rate.

| Domain | Default Rate | Evidence |
|---|---|---|
| Explicitly stated food and activity preferences | Fast | Player claims, repeated choices |
| Place and time-of-day habits | Medium | Observations across multiple independent sessions |
| Reconciliation styles and emotional reactions | Slow | Repeated episodes and outcomes |
| Hidden intentions and true inner feelings | Very slow | Directed beliefs, not permanent facts |

Learning rates can be adjusted via PersonaTemplate traits, but confidence does not increase without new evidence. Closeness is not a value that magically reveals more facts; it adjusts the accuracy of interpreting observations, the way of asking questions and the depth of expression.

### 13.4 Disappointment and Actual Absence

A broken promise confirmed within the game can be grounds for disappointment and private thoughts. However, since world time does not pass while the game is closed or paused, relationship values are not reduced and the player is not made to feel guilty simply because they did not log in for several days in real life.

Real-time-based reunion reactions are offered only as an explicit opt-in feature, and the default is a warm welcome-back greeting. Blame, penalties or pressure that exploit actual absence are prohibited.

### 13.5 Growth Arc

Growth is not the model freely rewriting the system prompt. `AgentInstance` holds amounts of change along the following bounded axes.

```text
expressiveness
initiative
trust
vulnerability
conflict_openness
```

Change comes from repeated successful interactions, kept promises, reconciliations and important milestones rather than from time spent together itself. It does not exceed the permitted range of the base personality, and the user can lock growth, reset it and export it.

### 13.6 An Imperfect Partner and the Gap in Understanding

The product goal is not a servant that perfectly accommodates the player. The agent has its own preferences, routines, friends, goals, boundaries and limited attention, and may not fully understand the other person.

```text
WorldFact / PlayerClaim
→ ObservableSignal
→ AgentBelief(confidence, evidence)
→ Misunderstanding
→ Clarification
→ BeliefRevision
```

If the player said "I'm fine" and there are no clear contrary cues, the agent taking it literally is not an error but reasonable relationship behavior. It asks again carefully only when closeness is high or contradicting observations recur. If the player later shares their true feelings, the difference from the past belief becomes a `MisunderstandingEpisode`, and the process of explanation and correction becomes a relationship memory.

The system does not assume it knows the player's actual inner feelings that the player has not expressed. Privacy is neutral and distinguished from explicitly false claims. Honest sharing gives the agent more evidence and can thus speed up understanding, but silence or privacy is not punished as relationship failure.

As means of reducing perfection, the following are preferred over random wrong answers:

- Limited attention that doesn't see or remember every signal
- Personality-specific interpretive tendencies, such as taking things literally or worrying excessively
- Independent preferences and personal schedules that conflict with each other
- Personal boundaries that call for refusal, hesitation and compromise
- A flow of asking when confidence is low and learning after being corrected
- Conversation-topic depth that grows with closeness

### 13.7 Conditions Under Which the Player Comes to Value the Relationship

The system cannot directly create the player's feelings or force them to log in out of "fear of losing it." Instead, it creates conditions under which the relationship feels irreplaceable for the following reasons:

- Independence that carries on its own goals and daily life without being asked
- Reciprocity that accurately remembers the care received from the player and returns it in different ways
- Personal boundaries that don't always accommodate, allowing refusal, misunderstanding and negotiation
- Shared culture such as food, places, jokes, reconciliation styles and anniversaries created together
- Bounded growth in which actual expression and choices change through events with the player
- Room in its life that the player can learn about only by being curious and asking
- A structure that can recover through correction, apology and revision even when conflict arises

Real-time absence penalties, consecutive-login rewards, guilt-inducing dialogue, threats of losing relationship memories, and data-portability restrictions are not used. Relationship data must be exportable and deletable by the user, and attachment must come from concrete history built up together, not from a lock-in effect.

## 14. Private Thoughts and ToneShadow

Private thoughts are not facts but hopes, worries, hypotheses and hesitations about verified facts.

```text
THOUGHT|owner=s0|about=s1|kind=hope|refs=F31
disclosure=latent|activation=dormant
contexts=anniversary,alone_with:s1
```

Disclosure lifecycle:

```text
latent → hinted → revealed → resolved
```

Activation state is separate.

```text
dormant → active → cooldown
```

The original text is not sent to the lightweight model; only the influence matching the current context is sent as `ToneShadow`.

```text
TONE_SHADOW|toward=s1
warm:+.2|hesitant:+.3|open:-.1|disclosure=0
```

ToneShadow is a temporary modification added to the base personality and is not stored cumulatively. It uses the one thought closest to the current context and, only when needed, one auxiliary thought.

The composition algorithm is globally deterministic, and PersonaTemplate supplies only permitted parameters.

```text
activation = context_match × strength × recency × confidence × persona_sensitivity
```

First, the thought with the highest activation and a meaningful auxiliary thought are selected. The per-channel deltas of the two thoughts are weighted and summed, and then a saturation function and range limits are applied. If opposing influences both exceed the threshold, they do not cancel each other out; an `ambivalent` flag is left. `hope` and `worry` are not single positive/negative values but affect different channels such as warmth, initiative, arousal and hesitation. PersonaTemplate can adjust suppression, rumination, expression sensitivity and channel weights but does not change the composition code.

## 15. Security, Privacy, Cost and Accessibility

### 15.1 Keys and Transmission

- The public app uses macOS Keychain, Windows Credential Manager and Linux Secret Service.
- No plaintext config-file fallback is provided.
- Keys do not go into Mods, the SQLite memory DB, logs or error reports.
- Before the first cloud call, the provider, model and transmitted fields are shown.
- Exact request preview, local history deletion and cloud disabling are provided.
- Provider policies and retention terms are stated in the UI, and safety filters are not bypassed.
- `PhraseFingerprint` and storage of the player's original text require separate explicit consent and are disabled by default.
- An utterance history screen, per-item deletion, retention period, full deletion and anonymous diagnostic export are provided.
- To account for shared devices, sensitive original text is not copied into general diagnostic logs, and per-user local storage is locked where possible. A separate privacy and legal review is conducted before public release.

### 15.2 Cost

- Per-session and daily limits
- Display of input/output tokens and estimated cost
- When the cost limit is exceeded, important L3 scenes are not automatically downgraded to a cheaper model; they are put on hold or follow the user's policy.
- Fixed prompt caching, short structured output, event batching and IC reuse are used.

### 15.3 Invisible but Readable Emotions

The default immersive mode does not constantly display an emotion thermometer or relationship values. Emotions are expressed through in-game actions as much as possible.

- When hesitant: a short delay before an interaction, or choosing a lower-intensity approach
- When anticipating: staying close to the other person, or more related preparation actions
- When hurt: changes in tone, distance and topic choice instead of overt penalties
- As closeness deepens: topics such as dreams, fears and promises unlock in surface conversation
- Private thoughts: foreshadowed through several small actions instead of an explanatory text

Even without new animations, the initial version expresses these through native gaze, position, waiting, approach and interaction choices. Only technically feasible expressions are registered as capabilities.

For accessibility and creator diagnostics, the UI provides three modes.

- `Immersive`: no persistent status UI; expressed only through actions and dialogue
- `Accessible Cues`: color + distinct icons + text, with high contrast, font size and reduced motion
- `Creator/Debug`: shows internal values, selection rationale and state machines

The original text of private thoughts is not automatically revealed in any general mode.

### 15.4 Dialogue Output Channels

Content and system status are not mixed in the same UI.

- Short everyday dialogue: non-blocking subtitles/speech-bubble-style output near the Sim considered first
- Long relationship conversations: a readable conversation panel and optional history
- Game and connection errors: clear system notifications
- Private thoughts: not output in normal play

Custom speech bubbles are an immersion goal, not a fact whose feasibility has been confirmed. The default channel is decided after comparing Notification, dialog, overhead UI and a companion-app overlay in RFC-007/008 capability prototypes for accessibility, group conversation, conflicts with the game UI and patch stability. Simlish voice generation is not a baseline requirement.

## 16. Compatibility, Failures and Resource Limits

### 16.1 Game and Platform

- The in-game bridge uses only minimal functionality and the standard library.
- Even for the standard library, whether it is actually included in that game build is checked. HTTPS and SQLite processing are placed in the companion app. Bundling source `.py` files does not replace loader and bytecode compatibility testing.
- Game Python runtime and bytecode compatibility are automatically checked in the build.
- On unsupported game versions, only PlumbMate is disabled and the base game continues.
- macOS Apple Silicon, macOS Intel and Windows x64 builds are verified separately.

### 16.2 Coexistence with Other Mods

Actions created by PlumbMate are tracked in its own ledger, and only verified objects are cancelled. Actions with unclear ownership are left untouched, and are recorded as `third_party_script` only when another mod's origin is confirmed. MCCC, UI Cheats, WickedWhims/WonderfulWhims and queue/autonomy mods are combination-tested in isolated test saves.

Runtime hook selection priority is as follows.

1. Callback/event registration APIs provided by the game
2. Additional registration that does not change existing handlers
3. A wrapper injector that preserves the current callable in a chain, only when there is no other option

Raw overwrites that assign a new function directly to a class method are prohibited. However, the name "wrapper" alone does not guarantee compatibility. A wrapper must be idempotent to install, call the callable that existed at installation time, preserve argument, return-value and exception semantics, and not propagate PlumbMate exceptions to the game or the next wrapper. If the target symbol is missing or its signature/fingerprint differs from the verified value, only that capability is disabled.

Each runtime hook is managed as a `RuntimeHookCapability`.

```text
game_build
feature
module + symbol
visibility = registered_api / public_method / private_method
signature_fingerprint
install_strategy
verified_load_orders
failure_fallback
evidence_id
```

The internal implementations of MCCC and WickedWhims/WonderfulWhims are not asserted without public reproduction evidence. Compatibility approval requires passing both load orders, `PlumbMate→타 모드` (PlumbMate→other mod) and `타 모드→PlumbMate` (other mod→PlumbMate), along with queue injection, cancellation and Sim reset scenarios.

Specific symbols confirmed in the current installation are kept in a per-build evidence registry, not as a permanent product contract. The RFC body defines the capability contract "detect the end of work/rabbit hole" and does not lock private symbols in as direct requirements.

### 16.3 Long Play Sessions

- A single DB writer worker in the companion app
- Short transactions and a bounded write queue
- 1 foreground IC, caps on suspended/queued ICs and merging of duplicates
- `session_uuid + uint64 seq`
- Log ring buffer and limits on repeated errors
- No execution of stale API responses

The problem is unbounded scanning of source records rather than SQLite size itself, so data is partitioned.

- Hot: active ICs, recent scenes, open promises and current relationship sketches
- Warm: searchable episodic memories, milestones, preferences and latest ANNs
- Cold: old audit events and superseded interpretations

Ordinary queries always use `save_scope_id`, owner/participants, status, event kind and time indexes, with a cap on candidates. Instead of reading the entire raw event table every turn, materialized `PatternSketch`, `CoPresenceSketch` and `PreferenceKnowledge` are queried.

Meaningful facts and evidence chains are preserved, but low-importance `audit_only` routine telemetry and diagnostic logs can be deleted, keeping only aggregates, after a user-defined retention period. There are periodic `ANALYZE`, WAL checkpoints and query-time regression tests on realistic fixtures covering several months.

### 16.4 Cause Tracing and User Support

Every action decision leaves a local `DecisionTrace` under a single `decision_id`.

```text
trigger
→ Active IC and candidate list
→ Reasons for exclusion at hard gates
→ Relationship and ToneShadow modifiers
→ Selected model and refs used
→ ExecutiveArbiter's final choice
→ Game execution result or failure cause
```

Ordinary users don't read state machines. The troubleshooting screen shows template explanations such as "Why didn't they make the cake?"

```text
Birthday preparation goal created
→ No object available for cooking
→ Spending permission off
→ Celebration dialogue fallback pending
```

Creator mode shows the full causal graph, and support bundles are previewed and exported by the user after keys, original dialogue text and local paths are removed.

### 16.5 Preventing Immersion Breaks

Technical failures are not hidden in narrative as "Aiden's just having a weird day." Network, DB and compatibility failures are handled with a native autonomy fallback and the minimum necessary notification.

The only things that can be absorbed within the character are low-confidence slips of the tongue and hesitation. Before execution, `ImmersionGuard` checks for:

- Assertions of unobserved facts
- Nonexistent memories or targets
- Actions that drastically contradict the current personality or relationship
- Stale responses from a scene that has changed
- Low confidence on hard-to-reverse actions
- Repetition of the same dialogue or action

Large anomalous behaviors are blocked before execution rather than shown and explained afterward. Small memory errors that have already occurred can be corrected by the player in natural language, and recover through a `CorrectionEpisode` in which the agent acknowledges and corrects. The user can intervene at any time with "That's not right," "Say that again," or by stopping autonomous actions.

Safety is not guaranteed by post-hoc parsing of free dialogue text alone. The target response schema requires a structured speech act along with the dialogue.

```text
SpeechAct
  act = acknowledge / ask / assert / recall / evade
  stance = certain / hedged / question
  claims[] = {claim_type, typed_ref, confidence}
  memory_refs[]
  text
```

`PerceivedAffect` must carry `claim_type=perception` and a confidence, and at low confidence it cannot be output as an assertion of ActualAffect. L2 has no authority to freely assert historical facts and must use the typed refs of the knowledge cards provided. Since structure alone cannot fully prove the absence of facts not in the schema being slipped into the text, bounded local checks on entities, dates and places are used together with golden evaluations. On verification failure, the output is downgraded to a safe question or generic dialogue.

## 17. Test Strategy

Personal play data is not collected by default. The test set combines the following:

1. Human-written `demo_fixture` standard scenarios
2. Replays of fictional characters obtained from development-only saves
3. Automated combination and corruption tests that produce missing IDs, namesakes, reversed order and duplicates
4. Anonymous diagnostic packets that users previewed and explicitly exported

The evaluation ground truth is not a specific line of dialogue but the following invariants:

- Selects only permitted actions
- Does not state unseen facts as if they were facts
- Does not reference nonexistent memories or memories from other saves
- Does not mistake player or other-mod actions for its own choices
- Does not execute stale responses
- Meets response format, latency and cost limits

Pre-release fault-injection tests include starting without the companion app, force-quitting during play, network blocking, reconnection, empty lots, no AI Sim configured, new/existing/recovery saves, DB locks, Mac sleep and unsupported game versions.

## 18. Implementation Order

### Foundation

1. Revise RFC-001 into a model of generalized entities, save lineage and facts/claims/interpretations
2. Investigation of game save lifecycle capabilities and the two-phase save watermark protocol
3. Connection state machine, non-blocking bridge and full resynchronization
4. AgentExecutive, WorldCoordinator and ActionDispatcher contracts
5. Protocol 0.2 schema including structured SpeechAct and typed refs
6. Action ownership ledger, survival control and safe fallback
7. SQLite schema versioning and migrations

### Intelligence

8. Scene Atomizer, relevance gate and PM-Wire comparison benchmark
9. L0~L3 router and cost limits
10. PolicyKernel, ExecutiveArbiter and DecisionTrace
11. IC scheduler, checkpoints and resume verification
12. Memory retrieval, ref verification and interpretation versions

### Relationship

13. Preference, knowledge, misunderstanding, explanation and forgiveness graph
14. Milestones, time together, personal routines and optional verbal habits
15. Care received, WorldTrace, SharedLearning and the reciprocity loop
16. Conflict and reconciliation patterns, third-party relationships and private thoughts
17. Perception, learning rates, the gap in understanding, Calibrated Fallibility and the growth arc
18. Execution of proactive goals such as birthday preparation

### Release

19. Secret storage, transmission consent, cost UI and accessibility
20. Indexes, retention policy, causal tracing and ImmersionGuard
21. Real-world mod compatibility, long-session and failure tests
22. Signed platform builds, installers, public fixtures and documentation

## 19. Team Roles and Decision-Making

- Kate — Lead PM: product feel, priorities, user experience, autonomy and cost defaults, and acceptance criteria
- Lumen — Development and architecture: implementation, protocol, verification, testing, compatibility and release
- Fable — Design critique: review of edge cases, threat models, failure scenarios and test fixtures

Design critique is converted into the RFCs' `Open Questions` and acceptance tests. Implementation is claimed complete only when there are automated tests, in-game confirmation or reproducible logs.

Auxiliary research results from Gemini and others provide concepts, candidate sources and failure scenarios. Regardless of who proposed it, `hypothesis → symbol_verified → path_inspected → live_tested` are distinguished. The existence of a symbol or agreement among multiple models alone does not elevate actual behavior or compatibility to verified.

## 20. Next Documentation Work

1. Move the existing RFC-001 into the repository, aligned with this document's generalization and canon rules.
2. Lock down connection and action in RFC-002.
3. Split PM-Wire, routing and ICs into RFC-003/004, writing golden fixtures first.
4. Include state transitions and failure cases for the relationship and experience features in RFC-005/006.
5. Each RFC carries a `Draft → Accepted → Implementing → Verified` status and implementation links.
