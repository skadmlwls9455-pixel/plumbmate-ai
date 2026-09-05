"""Strict slot parsing and build-scoped metadata; no save-file access.

A storage slot is neither lineage identity nor evidence of who started a save.
Only the locally inspected build below certifies the reserved autosave value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


UINT32_MAX = 0xFFFFFFFF

_DECIMAL_SLOT = re.compile(r"[0-9]{1,10}", re.ASCII)
_HEX_SLOT = re.compile(r"0x[0-9a-f]{1,8}", re.IGNORECASE | re.ASCII)
_SLOT_FILENAME = re.compile(
    r"Slot_([0-9a-f]{8})(?:\.save(?:\.ver(?:0|[1-9][0-9]*))?)?",
    re.IGNORECASE | re.ASCII,
)


class StorageSlotKind(str, Enum):
    ENGINE_AUTOSAVE_RESERVED = "ENGINE_AUTOSAVE_RESERVED"
    OTHER_SLOT = "OTHER_SLOT"
    UNKNOWN = "UNKNOWN"


class SaveOrigin(str, Enum):
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SlotMetadataEvidence:
    game_build: str
    reserved_autosave_slot_id: int
    source_member: str
    source_sha256: str


_VERIFIED_SLOT_METADATA = {
    "1.127.41.1230": SlotMetadataEvidence(
        game_build="1.127.41.1230",
        reserved_autosave_slot_id=0xFFFFFFFF,
        source_member="services/persistence_service.pyc",
        source_sha256="96d4ffd7c4696232306d7aff7ae977c5d9eeaa5599425b71a4df984dcecc58d1",
    ),
}


@dataclass(frozen=True)
class SlotClassification:
    slot_id: int
    storage_kind: StorageSlotKind
    evidence: SlotMetadataEvidence | None
    origin: SaveOrigin = SaveOrigin.UNKNOWN

    @property
    def canonical_id(self) -> str:
        """Normalized metadata key, never a save scope or a commit identifier."""
        return f"Slot_{self.slot_id:08x}"


def normalize_slot_id(value: int | str) -> int:
    """Accept a uint32, decimal/0x string, or an exact bare slot filename.

    Filename shapes are Slot_XXXXXXXX, Slot_XXXXXXXX.save, and
    Slot_XXXXXXXX.save.verN. X is hexadecimal and N is a nonnegative ASCII
    integer with no leading zeroes. Matching is case-insensitive. Backup suffixes
    describe only the input name and never prove its commit or load provenance.
    Paths, whitespace, unprefixed hex, floats, bools, and overflow are rejected.
    """
    if type(value) is int:
        slot_id = value
    elif isinstance(value, str):
        if _DECIMAL_SLOT.fullmatch(value):
            slot_id = int(value, 10)
        elif _HEX_SLOT.fullmatch(value):
            slot_id = int(value, 16)
        else:
            match = _SLOT_FILENAME.fullmatch(value)
            if match is None:
                raise ValueError("slot must be a uint32 or an exact bare slot filename")
            slot_id = int(match.group(1), 16)
    else:
        raise ValueError("slot must be an integer or string, excluding bool")
    if not 0 <= slot_id <= UINT32_MAX:
        raise ValueError("slot must be within uint32 range")
    return slot_id


def classify_save_slot(
    value: int | str, *, game_build: str | None = None,
) -> SlotClassification:
    """Classify storage metadata only for an exact inspected build.

    Unknown or future builds stay UNKNOWN, including for 0xffffffff. A reserved
    slot does not prove an engine-initiated save, and other slots can be used by
    manual saves or mod autosaves. Origin therefore remains UNKNOWN in all cases.
    This function does not validate game build reports or authenticate input.
    """
    slot_id = normalize_slot_id(value)
    if game_build is not None and not isinstance(game_build, str):
        raise ValueError("game_build must be a string or None")
    evidence = _VERIFIED_SLOT_METADATA.get(game_build)
    if evidence is None:
        kind = StorageSlotKind.UNKNOWN
    elif slot_id == evidence.reserved_autosave_slot_id:
        kind = StorageSlotKind.ENGINE_AUTOSAVE_RESERVED
    else:
        kind = StorageSlotKind.OTHER_SLOT
    return SlotClassification(slot_id, kind, evidence)
