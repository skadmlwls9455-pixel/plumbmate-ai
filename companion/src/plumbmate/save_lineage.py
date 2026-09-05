"""Conservative save-lineage policy, with no game, filesystem, or database I/O.

Slot, active household, and ticks are cross-checks, never save identity. A trusted
adapter must independently verify both the committed boundary and its connection
to this exact load before setting either ``verified`` flag. No current game hook
produces that evidence; these types define the companion-side contract only.

Reserved-autosave classification is reported as a hint only. An autosave is one
kind of save, not a separate lineage, so the slot's storage kind never grants,
blocks, or branches a restore.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .save_slots import StorageSlotKind, classify_save_slot


def _identifier(name: str, value: str | None, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _sequence(name: str, value: int | None, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


class CommitState(str, Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"


class LineageStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    VERIFIED_LOAD = "VERIFIED_LOAD"


class LineageHint(str, Enum):
    FIRST_OBSERVATION = "FIRST_OBSERVATION"
    INCOMPLETE_OBSERVATION = "INCOMPLETE_OBSERVATION"
    SLOT_CHANGED = "SLOT_CHANGED"
    HOUSEHOLD_CHANGED = "HOUSEHOLD_CHANGED"
    TICKS_REGRESSED = "TICKS_REGRESSED"
    TICKS_EQUAL = "TICKS_EQUAL"
    TICKS_ADVANCED = "TICKS_ADVANCED"
    AUTOSAVE_SLOT_OBSERVED = "AUTOSAVE_SLOT_OBSERVED"


@dataclass(frozen=True)
class SaveObservation:
    """Metadata observed after loading, not an authoritative save identifier.

    ``load_id`` must change on every load, including reloads within one session.
    ``household_id`` denotes the active household; it can change during play.
    Missing metadata stays missing and is never inferred from the previous load.
    ``game_build`` is the build the adapter reported for this load; without it a
    slot cannot be classified, because the reserved value is build-scoped.
    """

    session_id: str
    load_id: str
    slot_id: str | None
    household_id: str | None
    ticks: int | None
    game_build: str | None = None

    def __post_init__(self) -> None:
        _identifier("session_id", self.session_id)
        _identifier("load_id", self.load_id)
        _identifier("slot_id", self.slot_id, optional=True)
        _identifier("household_id", self.household_id, optional=True)
        _sequence("ticks", self.ticks, optional=True)
        _identifier("game_build", self.game_build, optional=True)


@dataclass(frozen=True)
class SaveCommit:
    """Immutable companion record of a SAVE_BEGIN / SAVE_COMMITTED boundary.

    An unverified, pending, or aborted record cannot authorize restoration.
    Sequence numbers are comparable only within the same ``working_epoch``.
    """

    save_scope_id: str
    save_commit_id: str
    working_epoch: str
    watermark_seq: int
    slot_id: str
    ticks: int
    state: CommitState = CommitState.PENDING
    verified: bool = False

    def __post_init__(self) -> None:
        for name in ("save_scope_id", "save_commit_id", "working_epoch", "slot_id"):
            _identifier(name, getattr(self, name))
        _sequence("watermark_seq", self.watermark_seq)
        _sequence("ticks", self.ticks)
        if not isinstance(self.state, CommitState):
            raise ValueError("state must be a CommitState")
        if type(self.verified) is not bool:
            raise ValueError("verified must be a boolean")


@dataclass(frozen=True)
class LoadProvenance:
    """Separately verified link from an actual load to a committed boundary.

    ``evidence_id`` references the adapter's evidence, not a token made by hashing
    slot/household/ticks. A matching tuple or player-selected slot alone must
    never set ``verified=True``. This class does not authenticate bridge input.
    """

    session_id: str
    load_id: str
    save_scope_id: str
    save_commit_id: str
    working_epoch: str
    watermark_seq: int
    evidence_id: str
    verified: bool = False

    def __post_init__(self) -> None:
        for name in (
            "session_id", "load_id", "save_scope_id", "save_commit_id",
            "working_epoch", "evidence_id",
        ):
            _identifier(name, getattr(self, name))
        _sequence("watermark_seq", self.watermark_seq)
        if type(self.verified) is not bool:
            raise ValueError("verified must be a boolean")


@dataclass(frozen=True)
class RestoreBoundary:
    """The only scope, commit, epoch, sequence, and time eligible for restore."""

    save_scope_id: str
    save_commit_id: str
    working_epoch: str
    watermark_seq: int
    ticks: int


@dataclass(frozen=True)
class LineageAssessment:
    status: LineageStatus
    hints: tuple[LineageHint, ...]
    reason: str
    restore_boundary: RestoreBoundary | None = None

    @property
    def can_restore(self) -> bool:
        return self.status is LineageStatus.VERIFIED_LOAD and self.restore_boundary is not None


def _slot_key(slot_id: str | None) -> str | None:
    """Canonical slot key when the name parses, otherwise the raw string.

    Normalizing makes Slot_ffffffff, Slot_ffffffff.save and Slot_ffffffff.save.ver0
    compare equal instead of producing a spurious slot mismatch. Values the slot
    grammar does not cover are compared verbatim and are never coerced.
    """
    if slot_id is None:
        return None
    try:
        return classify_save_slot(slot_id).canonical_id
    except ValueError:
        return slot_id


def _reserved_autosave_slot(observation: SaveObservation) -> bool:
    """True only for the reserved autosave value on an inspected build.

    An unparseable slot, a missing build, or an uninspected build is not a
    negative finding; it simply produces no hint.
    """
    if observation.slot_id is None or observation.game_build is None:
        return False
    try:
        classification = classify_save_slot(
            observation.slot_id, game_build=observation.game_build,
        )
    except ValueError:
        return False
    return classification.storage_kind is StorageSlotKind.ENGINE_AUTOSAVE_RESERVED


def _cross_checks(
    current: SaveObservation, previous: SaveObservation | None,
) -> tuple[LineageHint, ...]:
    hints: list[LineageHint] = []
    if previous is None:
        hints.append(LineageHint.FIRST_OBSERVATION)
    observations = (current,) if previous is None else (previous, current)
    if any(
        observation.slot_id is None or observation.household_id is None
        or observation.ticks is None
        for observation in observations
    ):
        hints.append(LineageHint.INCOMPLETE_OBSERVATION)
    if _reserved_autosave_slot(current):
        hints.append(LineageHint.AUTOSAVE_SLOT_OBSERVED)
    if previous is None:
        return tuple(hints)
    if current.household_id is not None and previous.household_id is not None:
        if current.household_id != previous.household_id:
            hints.append(LineageHint.HOUSEHOLD_CHANGED)
    if current.slot_id is None or previous.slot_id is None:
        return tuple(hints)
    if _slot_key(current.slot_id) != _slot_key(previous.slot_id):
        hints.append(LineageHint.SLOT_CHANGED)
        # Game clocks in different slots have no ordered relationship.
        return tuple(hints)
    if current.ticks is not None and previous.ticks is not None:
        if current.ticks < previous.ticks:
            hints.append(LineageHint.TICKS_REGRESSED)
        elif current.ticks == previous.ticks:
            hints.append(LineageHint.TICKS_EQUAL)
        else:
            hints.append(LineageHint.TICKS_ADVANCED)
    return tuple(hints)


def assess_save_lineage(
    current: SaveObservation,
    *,
    previous: SaveObservation | None = None,
    commits: Iterable[SaveCommit] = (),
    provenance: LoadProvenance | None = None,
) -> LineageAssessment:
    """Resolve an exact verified load, or quarantine its unresolved timeline.

    Heuristic hints never grant restore, create a branch, or merge any data.
    Every load needs its own provenance; a previous resolved observation is not
    carried forward as proof. ``commits`` must come from the companion registry,
    not from an untrusted model response or ordinary chat request.
    """
    hints = _cross_checks(current, previous)

    def unresolved(reason: str) -> LineageAssessment:
        return LineageAssessment(LineageStatus.UNRESOLVED, hints, reason)

    if provenance is None or not provenance.verified:
        return unresolved("load_provenance_unverified")
    if (provenance.session_id, provenance.load_id) != (current.session_id, current.load_id):
        return unresolved("load_provenance_mismatch")
    candidates = [
        commit for commit in commits
        if (commit.save_scope_id, commit.save_commit_id)
        == (provenance.save_scope_id, provenance.save_commit_id)
    ]
    if not candidates:
        return unresolved("commit_not_found")
    if len(candidates) != 1:
        return unresolved("commit_identity_ambiguous")
    commit = candidates[0]
    if not commit.verified or commit.state is not CommitState.COMMITTED:
        return unresolved("commit_not_verified_committed")
    if (provenance.working_epoch, provenance.watermark_seq) != (
        commit.working_epoch, commit.watermark_seq,
    ):
        return unresolved("commit_boundary_mismatch")
    if current.slot_id is None or current.ticks is None:
        return unresolved("save_cross_check_missing")
    if _slot_key(current.slot_id) != _slot_key(commit.slot_id):
        return unresolved("save_slot_mismatch")
    if current.ticks < commit.ticks:
        return unresolved("observed_before_commit")
    return LineageAssessment(
        LineageStatus.VERIFIED_LOAD,
        hints,
        "verified_load_boundary",
        RestoreBoundary(
            commit.save_scope_id, commit.save_commit_id, commit.working_epoch,
            commit.watermark_seq, commit.ticks,
        ),
    )


@dataclass(frozen=True)
class MemoryReference:
    """Metadata for an immutable memory/checkpoint bound to one exact commit.

    This subset deliberately does not traverse ancestor commits. Historical
    snapshots must be explicitly bound to the loaded commit by a trusted writer;
    sharing a slot, scope, timestamp, or low sequence number is insufficient.
    """

    record_id: str
    save_scope_id: str
    save_commit_id: str
    working_epoch: str
    event_seq: int
    ticks: int

    def __post_init__(self) -> None:
        for name in ("record_id", "save_scope_id", "save_commit_id", "working_epoch"):
            _identifier(name, getattr(self, name))
        _sequence("event_seq", self.event_seq)
        _sequence("ticks", self.ticks)


@dataclass(frozen=True)
class MemorySelection:
    eligible: tuple[MemoryReference, ...]
    quarantined: tuple[MemoryReference, ...]


def select_restorable_memory(
    assessment: LineageAssessment, records: Iterable[MemoryReference],
) -> MemorySelection:
    """Partition references without mutating, deleting, or replaying anything.

    Rollbacks exclude future records using commit identity AND epoch/watermark
    AND saved ticks. A post-load observation can advance; it never extends the
    confirmed memory boundary. Unresolved provenance quarantines every record.
    """
    eligible: list[MemoryReference] = []
    quarantined: list[MemoryReference] = []
    boundary = assessment.restore_boundary
    for record in records:
        allowed = (
            assessment.can_restore and boundary is not None
            and record.save_scope_id == boundary.save_scope_id
            and record.save_commit_id == boundary.save_commit_id
            and record.working_epoch == boundary.working_epoch
            and record.event_seq <= boundary.watermark_seq
            and record.ticks <= boundary.ticks
        )
        (eligible if allowed else quarantined).append(record)
    return MemorySelection(tuple(eligible), tuple(quarantined))
