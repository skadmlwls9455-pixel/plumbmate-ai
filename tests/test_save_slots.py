import unittest
from dataclasses import replace

from plumbmate.save_lineage import (
    CommitState,
    LineageStatus,
    LoadProvenance,
    MemoryReference,
    SaveCommit,
    SaveObservation,
    assess_save_lineage,
    select_restorable_memory,
)
from plumbmate.save_slots import (
    SaveOrigin,
    StorageSlotKind,
    classify_save_slot,
    normalize_slot_id,
)


VERIFIED_BUILD = "1.127.41.1230"


class SaveSlotTests(unittest.TestCase):
    def test_numeric_slot_representations_normalize_to_uint32(self):
        for value, expected in (
            (0, 0), (1, 1), (2, 2), (4294967295, 0xFFFFFFFF),
            ("0", 0), ("00000002", 2), ("4294967295", 0xFFFFFFFF),
            ("0x0", 0), ("0x2", 2), ("0xFFFFFFFF", 0xFFFFFFFF),
            ("0Xffffffff", 0xFFFFFFFF),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_slot_id(value), expected)

    def test_exact_filenames_and_backup_names_normalize(self):
        for value in (
            "Slot_ffffffff", "Slot_ffffffff.save", "Slot_ffffffff.save.ver0",
            "Slot_ffffffff.save.ver4", "Slot_ffffffff.save.ver12",
            "SLOT_FFFFFFFF.SAVE.VER1", "slot_FfFfFfFf.Save",
        ):
            with self.subTest(value=value):
                slot = classify_save_slot(value, game_build=VERIFIED_BUILD)
                self.assertEqual(slot.slot_id, 0xFFFFFFFF)
                self.assertEqual(slot.canonical_id, "Slot_ffffffff")
                self.assertIs(slot.storage_kind, StorageSlotKind.ENGINE_AUTOSAVE_RESERVED)

    def test_regular_slots_normalize_with_eight_hex_digits(self):
        for value in (2, "2", "0x2", "Slot_00000002.save.ver0"):
            with self.subTest(value=value):
                self.assertEqual(classify_save_slot(value).canonical_id, "Slot_00000002")

    def test_invalid_numeric_types_and_uint32_overflow_are_rejected(self):
        for value in (
            True, False, -1, 4294967296, 1.0, None, b"Slot_ffffffff", [], {},
            "-1", "+1", "4294967296", "0x100000000", "1e2", "1.0", "0x",
            "00000000000", "４２", "0xＦＦ",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_slot_id(value)

    def test_malformed_names_paths_and_suffixes_are_rejected(self):
        for value in (
            "", "ffffffff", "Slot_fffffff", "Slot_fffffffff", "Slot_gggggggg",
            " Slot_ffffffff", "Slot_ffffffff ", "Slot_ffffffff\n", "Slot_ffffffff\x00",
            "Slot_ffffffff.save.tmp", "Slot_ffffffff.save.ver", "Slot_ffffffff.ver0",
            "Slot_ffffffff.save.ver-1", "Slot_ffffffff.save.ver01",
            "Slot_ffffffff.save.ver０", "Slot_ffffffff.save.ver1.more",
            "./Slot_ffffffff.save", "../Slot_ffffffff.save", "/tmp/Slot_ffffffff.save",
            "saves/Slot_ffffffff.save", "saves\\Slot_ffffffff.save",
            "C:\\saves\\Slot_ffffffff.save", "Slot_ffffffff.save/other",
            "Slot_ffffffff.backup", "prefixSlot_ffffffff", "Slot_ffffffff.save.save",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_slot_id(value)

    def test_reserved_slot_classification_includes_exact_build_evidence(self):
        slot = classify_save_slot(0xFFFFFFFF, game_build=VERIFIED_BUILD)
        self.assertIs(slot.storage_kind, StorageSlotKind.ENGINE_AUTOSAVE_RESERVED)
        self.assertEqual(slot.evidence.game_build, VERIFIED_BUILD)
        self.assertEqual(slot.evidence.source_member, "services/persistence_service.pyc")
        self.assertEqual(
            slot.evidence.source_sha256,
            "96d4ffd7c4696232306d7aff7ae977c5d9eeaa5599425b71a4df984dcecc58d1",
        )

    def test_unverified_or_future_build_does_not_certify_reserved_slot(self):
        for build in (None, "", "1.127", "1.127.41.1231", "1.128.0.0", VERIFIED_BUILD + " "):
            for slot_id in (0, 2, 0xFFFFFFFF):
                with self.subTest(build=build, slot_id=slot_id):
                    slot = classify_save_slot(slot_id, game_build=build)
                    self.assertIs(slot.storage_kind, StorageSlotKind.UNKNOWN)
                    self.assertIsNone(slot.evidence)

    def test_slot_storage_kind_never_proves_save_origin(self):
        for slot_id in (0, 1, 2, 0xFFFFFFFE, 0xFFFFFFFF):
            with self.subTest(slot_id=slot_id):
                slot = classify_save_slot(slot_id, game_build=VERIFIED_BUILD)
                self.assertIs(slot.origin, SaveOrigin.UNKNOWN)
                expected = (
                    StorageSlotKind.ENGINE_AUTOSAVE_RESERVED
                    if slot_id == 0xFFFFFFFF else StorageSlotKind.OTHER_SLOT
                )
                self.assertIs(slot.storage_kind, expected)

    def test_nonstring_build_is_rejected(self):
        for build in (False, 123, [], {}):
            with self.subTest(build=build):
                with self.assertRaises(ValueError):
                    classify_save_slot(0xFFFFFFFF, game_build=build)

    def test_autosave_slot_and_equal_ticks_never_authorize_restore(self):
        slot = classify_save_slot("Slot_ffffffff.save", game_build=VERIFIED_BUILD)
        current = SaveObservation("session", "load-2", slot.canonical_id, "household", 100)
        result = assess_save_lineage(current, previous=replace(current, load_id="load-1"))
        self.assertIs(result.status, LineageStatus.UNRESOLVED)
        self.assertFalse(result.can_restore)

    def test_reserved_slot_is_shared_metadata_not_cross_world_identity(self):
        slot = classify_save_slot(0xFFFFFFFF, game_build=VERIFIED_BUILD).canonical_id
        current = SaveObservation("session", "load", slot, "household", 100)
        commit_a = SaveCommit(
            "world-a", "commit-1", "epoch-1", 10, slot, 100,
            state=CommitState.COMMITTED, verified=True,
        )
        commit_b = replace(commit_a, save_scope_id="world-b")
        provenance = LoadProvenance(
            "session", "load", "world-b", "commit-1", "epoch-1", 10,
            "synthetic-load-evidence", verified=True,
        )
        result = assess_save_lineage(
            current, commits=[commit_a, commit_b], provenance=provenance,
        )
        memory_a = MemoryReference("a", "world-a", "commit-1", "epoch-1", 10, 100)
        memory_b = replace(memory_a, record_id="b", save_scope_id="world-b")
        selection = select_restorable_memory(result, [memory_a, memory_b])
        self.assertEqual(selection.eligible, (memory_b,))
        self.assertEqual(selection.quarantined, (memory_a,))

    def test_same_tick_autosaves_keep_distinct_commit_boundaries(self):
        slot = classify_save_slot("SLOT_FFFFFFFF.SAVE.VER0", game_build=VERIFIED_BUILD).canonical_id
        current = SaveObservation("session", "load", slot, "household", 100)
        earlier = SaveCommit(
            "scope", "commit-1", "epoch", 10, slot, 100,
            state=CommitState.COMMITTED, verified=True,
        )
        later = replace(earlier, save_commit_id="commit-2", watermark_seq=11)
        provenance = LoadProvenance(
            "session", "load", "scope", "commit-1", "epoch", 10,
            "synthetic-backup-load-evidence", verified=True,
        )
        result = assess_save_lineage(current, commits=[later, earlier], provenance=provenance)
        old_memory = MemoryReference("old", "scope", "commit-1", "epoch", 10, 100)
        new_memory = replace(old_memory, record_id="new", save_commit_id="commit-2", event_seq=11)
        selection = select_restorable_memory(result, [new_memory, old_memory])
        self.assertEqual(selection.eligible, (old_memory,))
        self.assertEqual(selection.quarantined, (new_memory,))


if __name__ == "__main__":
    unittest.main()
