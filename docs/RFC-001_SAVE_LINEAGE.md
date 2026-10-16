# RFC-001: Save Lineage Identification — Companion Implementation Subset

_English translation of RFC-001_SAVE_LINEAGE_KO.md. If the two differ, the Korean original is authoritative._

Status: offline pure policy functions and synthetic fixture tests implemented. Connection to actual game save/load signals and integration with persistent storage are not implemented.

The parent contract is 5.3 Save Lineage and 9.1 Restart Restoration in [MASTER_DESIGN.md](MASTER_DESIGN.md). This document does not mean completion of the entire RFC-001 memory model.

## Implementation scope

`companion/src/plumbmate/save_lineage.py` makes a determination from the current observation, the previous observation, the companion's confirmed commit records, and a separately verified load provenance. It does not access files, the game API, or SQLite, and it is not connected to the existing `CompanionEngine` or the game mod. It cannot be assumed that this policy has already been applied to the existing MVP's session-based memory files.

`assess_save_lineage()` returns `UNRESOLVED` or `VERIFIED_LOAD` and diagnostic hints. `select_restorable_memory()` splits references into `eligible` and `quarantined` using the returned confirmed boundary. Quarantine means exclusion from lookup candidates and does not perform deletion, moving, or automatic merging of originals.

`companion/src/plumbmate/save_slots.py` strictly normalizes slot input and classifies save slot metadata for confirmed game builds. File names are checked only as strings, and files are not opened.

## Cross-checking slot, household, and game time

`SaveObservation` has `session_id`, `load_id`, `slot_id`, `household_id`, and `ticks`. Game IDs are received as opaque strings normalized by the upper adapter. `household_id` is the active household ID, not a save identity. Even within the same process, a new `load_id` is required whenever a load occurs.

| Observed difference | Diagnostic hint | Handling meaning |
|---|---|---|
| First observation | `FIRST_OBSERVATION` | Does not conclude it is a new save |
| Some metadata missing | `INCOMPLETE_OBSERVATION` | Not arbitrarily filled in with previous values |
| Slot changed | `SLOT_CHANGED` | Candidate for new save / save as / loading an existing slot. Time order across different slots is not compared |
| Active household changed | `HOUSEHOLD_CHANGED` | Rotational play is possible. Not a reason to create a branch automatically |
| Time decreased in the same slot | `TICKS_REGRESSED` | Rollback/recovery candidate. Memories from the previous timeline are not restored automatically |
| Same time in the same slot | `TICKS_EQUAL` | Cannot distinguish consecutive saves during pause from a different branch at the same time |
| Time increased in the same slot | `TICKS_ADVANCED` | Only a progression candidate; does not prove continuity or the same commit |

A hash of this tuple must not be made into `save_scope_id` or `save_commit_id`. Different saves with the same tuple are possible, and the household can differ within the same save. Hints are clues for search and diagnostics, and no hint alone grants restoration permission.

## Reserved autosave slot and save origin

In `services/persistence_service.pyc` inside `simulation.zip` of the current local build `1.127.41.1230`, `AUTO_SAVE_SLOT_ID = 4294967295`, i.e. `0xffffffff`, was confirmed. The confirmed member SHA-256 is `96d4ffd7c4696232306d7aff7ae977c5d9eeaa5599425b71a4df984dcecc58d1`, and the constant assignment corresponds to original source line info 134. This is static build evidence, not the result of actually running an autosave.

`classify_save_slot(value, game_build=...)` classifies that number as `ENGINE_AUTOSAVE_RESERVED` only when the build is exactly this build. Other numbers are `OTHER_SLOT` and are not classified as manual saves. If the build is missing or different, even `0xffffffff` is `UNKNOWN`. Authentication of the game build report itself and re-verification of the constant in the next patch are the responsibility of the caller/capability layer.

`normalize_slot_id()` accepts only uint32 integers, 1–10 digit ASCII decimal strings, 1–8 digit hexadecimal strings starting with `0x`, and the following exact **file names only**.

- `Slot_XXXXXXXX`
- `Slot_XXXXXXXX.save`
- `Slot_XXXXXXXX.save.verN`

`XXXXXXXX` is exactly 8 hexadecimal digits and is case-insensitive. `N` is a non-negative ASCII integer without leading zeros, and `0` itself is allowed. Support for the backup suffix is support for the name format, not a claim that the game creates backups for every N. Paths, whitespace, trailing suffixes, negative numbers, bools, floats, and values exceeding uint32 are rejected. The `canonical_id` of the classification result is returned in a format like `Slot_ffffffff`, and the actual adapter uses it as the normalized input for `SaveObservation.slot_id` and `SaveCommit.slot_id`. A slot value with the suffix removed does not preserve the identity of the backup file, so the link between the actually loaded artifact and the commit must be kept in separate provenance.

The kind of save location (`storage_kind`) and the party that initiated the save (`origin`) are separate. This classification function returns `origin=UNKNOWN` in all cases. The mere fact that it is a reserved slot cannot prove that a given call originated from an engine autosave, and ordinary numbered slots can also be used for autosaves by mods such as MCCC.

In `save_game_gen` of the same bytecode (starting line info 538), there is a branch inside the `send_save_message` path that calls the manual-save callback under the conditions success, `msg.slot_id > 1`, and `msg.slot_id != AUTO_SAVE_SLOT_ID`. Therefore it cannot be claimed that that callback alone observes the completion of every save. The existence of `AutosaveService` and `REQUEST_AUTOSAVE` is also distinguished from an actual save completion signal or load provenance verification.

An autosave is one kind of save and does not mean a new lineage. Even if different worlds use the same `Slot_ffffffff` value, they do not share memories. Even if repeated saves at the same ticks occur in that slot, each commit/watermark must be kept.

### Cost control for frequent saves

Distinct save attempts are not throttled so as to omit `SAVE_BEGIN`, `SAVE_COMMITTED`, or `SAVE_ABORTED` boundaries. The same slot, time, and autosave kind are not a deduplication key. Only delivery retries of the **same stage** for the same `attempt_id` are handled idempotently, and the start and completion of the same attempt are each processed. If a conflicting payload arrives for the same ID, it must not be discarded as a simple duplicate but left in an unresolved state.

Expensive summary/index updates may be batched after the confirmed boundary is recorded, but the commit/watermark record of every save and the evidence linking it to loads are preserved. This paragraph is the contract for a future save lifecycle implementation and does not mean that throttling, event reception, or a deduplicator has been implemented in the current module.

## Restoration permission contract

A verified `LoadProvenance` links this `session_id` and `load_id` to a specific `save_scope_id`, `save_commit_id`, `working_epoch`, and `watermark_seq`. `evidence_id` is a reference to the evidence that independently confirmed that link. UI information that a slot was simply selected, or the same tuple, is not evidence.

`SaveCommit.verified` and `LoadProvenance.verified` default to `False`. The flag is a caller contract inside the trust boundary, not an authentication feature. `verified=true` in a model response, an ordinary chat bridge request, or unvalidated JSON must not be trusted as-is. The current implementation has no adapter that fills this value from actual game evidence.

Restoration is possible only when all of the following checks pass.

1. The provenance is verified and exactly matches the current session and load IDs.
2. The scope/commit combination pointed to by the provenance is unique in the companion commit list. Duplicate records with the same key are also treated as ambiguity.
3. The record state is a verified `COMMITTED`. `PENDING`, `ABORTED`, and unverified records are excluded.
4. The epoch and watermark exactly match between the provenance and the commit record.
5. The observed slot is the same as the record, and the observed time is not earlier than the save time. If the slot or time cannot be obtained, it is unresolved. A missing household ID does not invalidate an independently verified save link.

Even if the game progresses after a load and the observed time increases, the restoration boundary stays fixed at the `ticks` and `watermark_seq` at the time of saving. Provenance for a different session or a previous load cannot be reused. Multiple saves at the same time are distinguished by the exact commit of the provenance and are not selected by list order.

## Memory and IC boundary

`MemoryReference` represents an ID, scope, commit, epoch, event sequence number, and time without a payload. A restoration candidate must satisfy all of the following.

- There is a verified restoration determination.
- The scope, commit, and epoch are exactly the same as the restoration boundary.
- The event sequence number is at or below the confirmed watermark, and the event time is at or before the save time.

Even with the same slot and same time, memories of a different scope or commit are excluded. When going back to an earlier save, memories from later commits, future sequence numbers, future times, and different epochs are all quarantined. Ordering across epochs is not inferred from low sequence numbers alone.

This implementation subset does not traverse the ancestor commit graph. Reading memories from earlier commits will require a verified ancestor relationship in the future or an immutable snapshot explicitly linked to the loaded commit. Earlier records are not restored merely because they have the same scope as the current one or an earlier time. There is also no behavior that unconditionally reattributes records in the DB.

The same boundary checks must be applied to IC checkpoints as well, but there is no actual IC scheduler connection yet. What will be restored in the future is goal and progress information; action queue handles are not replayed. A verified checkpoint must recheck the current world in `SUSPENDED_RESTORE` and apply `NEEDS_REPLAN` or an expiry policy if the save link is unconfirmed.

## Remaining integration work

- In RFC-008, verify per-build capabilities for identifying save start, completion, and actual loads. This module does not read or write game saves and does not install hooks.
- Implement the atomic link between `SAVE_BEGIN → SAVE_COMMITTED/SAVE_ABORTED` and pending checkpoints. The current `CommitState` is an input contract, not a save transaction implementation.
- Without trustworthy actual load provenance, keep `UNRESOLVED` and quarantine unconfirmed memories. Do not indicate that exact restoration is supported in the absence of game save signals.
- For new saves, save as, writes after recovery, and concurrent device modifications, a separate lineage coordinator must assign the scope. This identification function surfaces branch candidates but does not perform automatic scope creation or merging.
- Connect scope/commit/epoch/watermark filters to the actual lookup paths of the companion DB and engine, and add regression tests. Unresolved states are not handled by game save access, arbitrary DB deletion, or full data resets.

## Verification

Uses only synthetic fixtures and requires no game or API account.

```bash
PYTHONPATH=companion/src python3 -m unittest discover -s tests -p 'test_save_lineage.py' -v
PYTHONPATH=companion/src python3 -m unittest discover -s tests -p 'test_save_slots.py' -v
```

The tests cover same-tuple/same-time branches, household switching, slot changes, rollback, blocking of future memories, provenance reuse, unverified/aborted commits, duplicate commit keys, epoch/watermark mismatches, missing/invalid metadata, and progression after load. Passing with synthetic `verified=True` fixtures does not mean that actual game save signals have been verified.

The slot tests verify normalization of numbers, case, and backup names; rejection of invalid paths, formats, and ranges; differences in classification between confirmed and unconfirmed builds; separation of slot kind and origin; quarantine of memories across different worlds sharing the reserved slot; and separation of autosave commits at the same time.
