import unittest
from dataclasses import replace

from plumbmate.save_lineage import (
    CommitState,
    LineageHint,
    LineageStatus,
    LoadProvenance,
    MemoryReference,
    SaveCommit,
    SaveObservation,
    assess_save_lineage,
    select_restorable_memory,
)

VERIFIED_BUILD = "1.127.41.1230"


class SaveLineageTests(unittest.TestCase):
    def setUp(self):
        self.current = SaveObservation("session-2", "load-2", "slot-1", "household-1", 100)
        self.previous = SaveObservation("session-1", "load-1", "slot-1", "household-1", 90)
        self.commit = SaveCommit(
            "scope-1", "commit-1", "epoch-1", 20, "slot-1", 100,
            state=CommitState.COMMITTED, verified=True,
        )
        self.provenance = LoadProvenance(
            "session-2", "load-2", "scope-1", "commit-1", "epoch-1", 20,
            "synthetic-verified-load-evidence", verified=True,
        )
        self.memory = MemoryReference("memory-1", "scope-1", "commit-1", "epoch-1", 20, 100)

    def assess_verified(self, current=None, **kwargs):
        return assess_save_lineage(
            current or self.current, previous=self.previous,
            commits=kwargs.get("commits", [self.commit]),
            provenance=kwargs.get("provenance", self.provenance),
        )

    def assert_unresolved(self, result, reason=None):
        self.assertIs(result.status, LineageStatus.UNRESOLVED)
        self.assertFalse(result.can_restore)
        self.assertIsNone(result.restore_boundary)
        if reason is not None:
            self.assertEqual(result.reason, reason)
        selection = select_restorable_memory(result, [self.memory])
        self.assertEqual(selection.eligible, ())
        self.assertEqual(selection.quarantined, (self.memory,))

    def test_first_observation_does_not_invent_a_new_save(self):
        result = assess_save_lineage(self.current)
        self.assert_unresolved(result, "load_provenance_unverified")
        self.assertEqual(result.hints, (LineageHint.FIRST_OBSERVATION,))

    def test_same_tuple_never_proves_same_commit(self):
        previous = replace(self.previous, ticks=100)
        result = assess_save_lineage(self.current, previous=previous, commits=[self.commit])
        self.assert_unresolved(result)
        self.assertIn(LineageHint.TICKS_EQUAL, result.hints)

    def test_forward_ticks_do_not_prove_continuity(self):
        result = assess_save_lineage(self.current, previous=self.previous, commits=[self.commit])
        self.assert_unresolved(result)
        self.assertIn(LineageHint.TICKS_ADVANCED, result.hints)

    def test_rollback_candidate_is_quarantined_without_provenance(self):
        result = assess_save_lineage(
            self.current, previous=replace(self.previous, ticks=200), commits=[self.commit],
        )
        self.assert_unresolved(result)
        self.assertIn(LineageHint.TICKS_REGRESSED, result.hints)

    def test_different_slots_do_not_compare_clock_order(self):
        result = assess_save_lineage(
            replace(self.current, slot_id="slot-2", ticks=10), previous=self.previous,
        )
        self.assert_unresolved(result)
        self.assertIn(LineageHint.SLOT_CHANGED, result.hints)
        self.assertNotIn(LineageHint.TICKS_REGRESSED, result.hints)

    def test_household_change_is_not_an_automatic_branch(self):
        result = self.assess_verified(replace(self.current, household_id="household-2"))
        self.assertTrue(result.can_restore)
        self.assertIn(LineageHint.HOUSEHOLD_CHANGED, result.hints)
        self.assertEqual(result.restore_boundary.save_scope_id, "scope-1")

    def test_household_change_alone_does_not_authorize_restore(self):
        result = assess_save_lineage(
            replace(self.current, household_id="household-2"), previous=self.previous,
        )
        self.assert_unresolved(result)
        self.assertIn(LineageHint.HOUSEHOLD_CHANGED, result.hints)

    def test_missing_household_is_diagnostic_not_save_identity(self):
        result = self.assess_verified(replace(self.current, household_id=None))
        self.assertTrue(result.can_restore)
        self.assertIn(LineageHint.INCOMPLETE_OBSERVATION, result.hints)

    def test_missing_previous_metadata_is_not_filled_in(self):
        result = assess_save_lineage(
            self.current, previous=replace(self.previous, slot_id=None, ticks=None),
        )
        self.assert_unresolved(result)
        self.assertEqual(result.hints, (LineageHint.INCOMPLETE_OBSERVATION,))

    def test_missing_current_slot_or_ticks_blocks_verified_restore(self):
        for field in ("slot_id", "ticks"):
            with self.subTest(field=field):
                result = self.assess_verified(replace(self.current, **{field: None}))
                self.assert_unresolved(result, "save_cross_check_missing")

    def test_verification_flags_default_to_false(self):
        commit = SaveCommit("scope", "commit", "epoch", 0, "slot", 0)
        provenance = LoadProvenance("session", "load", "scope", "commit", "epoch", 0, "evidence")
        self.assertFalse(commit.verified)
        self.assertIs(commit.state, CommitState.PENDING)
        self.assertFalse(provenance.verified)

    def test_unverified_provenance_is_not_enough(self):
        result = self.assess_verified(provenance=replace(self.provenance, verified=False))
        self.assert_unresolved(result, "load_provenance_unverified")

    def test_provenance_cannot_be_replayed_in_another_load_or_session(self):
        for field in ("session_id", "load_id"):
            with self.subTest(field=field):
                result = self.assess_verified(
                    provenance=replace(self.provenance, **{field: "previous"}),
                )
                self.assert_unresolved(result, "load_provenance_mismatch")

    def test_unknown_scope_or_commit_is_not_selected_by_matching_tuple(self):
        for field in ("save_scope_id", "save_commit_id"):
            with self.subTest(field=field):
                result = self.assess_verified(
                    provenance=replace(self.provenance, **{field: "unknown"}),
                )
                self.assert_unresolved(result, "commit_not_found")

    def test_pending_aborted_and_unverified_commits_block_restore(self):
        for commit in (
            replace(self.commit, state=CommitState.PENDING),
            replace(self.commit, state=CommitState.ABORTED),
            replace(self.commit, verified=False),
        ):
            with self.subTest(commit=commit):
                self.assert_unresolved(
                    self.assess_verified(commits=[commit]), "commit_not_verified_committed",
                )

    def test_duplicate_commit_identity_is_ambiguous_even_when_records_match(self):
        result = self.assess_verified(commits=[self.commit, self.commit])
        self.assert_unresolved(result, "commit_identity_ambiguous")

    def test_conflicting_commit_records_do_not_choose_first_match(self):
        for records in (
            [self.commit, replace(self.commit, watermark_seq=21)],
            [replace(self.commit, watermark_seq=21), self.commit],
        ):
            self.assert_unresolved(
                self.assess_verified(commits=records), "commit_identity_ambiguous",
            )

    def test_watermark_and_epoch_must_match_the_exact_commit(self):
        for changes in ({"watermark_seq": 19}, {"watermark_seq": 21}, {"working_epoch": "other"}):
            with self.subTest(changes=changes):
                result = self.assess_verified(provenance=replace(self.provenance, **changes))
                self.assert_unresolved(result, "commit_boundary_mismatch")

    def test_verified_link_still_requires_matching_slot(self):
        result = self.assess_verified(replace(self.current, slot_id="slot-2"))
        self.assert_unresolved(result, "save_slot_mismatch")

    def test_observation_before_referenced_commit_cannot_restore_future_memory(self):
        result = self.assess_verified(replace(self.current, ticks=99))
        self.assert_unresolved(result, "observed_before_commit")

    def test_verified_load_returns_only_confirmed_boundary(self):
        result = self.assess_verified(replace(self.current, ticks=110))
        self.assertIs(result.status, LineageStatus.VERIFIED_LOAD)
        self.assertTrue(result.can_restore)
        self.assertEqual(result.restore_boundary.ticks, 100)
        self.assertEqual(result.restore_boundary.watermark_seq, 20)
        self.assertEqual(result.restore_boundary.save_commit_id, "commit-1")

    def test_same_ticks_multiple_saves_use_explicit_commit_not_registry_order(self):
        other = replace(self.commit, save_commit_id="commit-2", watermark_seq=21)
        for records in ([other, self.commit], [self.commit, other]):
            result = self.assess_verified(commits=iter(records))
            self.assertEqual(result.restore_boundary.save_commit_id, "commit-1")
            selection = select_restorable_memory(result, [replace(self.memory, save_commit_id="commit-2")])
            self.assertEqual(selection.eligible, ())

    def test_same_slot_same_ticks_branch_memory_cannot_cross_scope(self):
        branch = replace(self.commit, save_scope_id="scope-2")
        result = self.assess_verified(
            commits=[self.commit, branch],
            provenance=replace(self.provenance, save_scope_id="scope-2"),
        )
        branch_memory = replace(self.memory, record_id="branch-memory", save_scope_id="scope-2")
        selection = select_restorable_memory(result, [self.memory, branch_memory])
        self.assertEqual(selection.eligible, (branch_memory,))
        self.assertEqual(selection.quarantined, (self.memory,))

    def test_new_slot_verified_load_uses_its_own_scope_without_merging(self):
        commit = replace(self.commit, save_scope_id="scope-2", slot_id="slot-2", ticks=10)
        current = replace(self.current, slot_id="slot-2", ticks=10)
        result = self.assess_verified(
            current, commits=[self.commit, commit],
            provenance=replace(self.provenance, save_scope_id="scope-2"),
        )
        self.assertTrue(result.can_restore)
        self.assertIn(LineageHint.SLOT_CHANGED, result.hints)
        self.assertEqual(select_restorable_memory(result, [self.memory]).eligible, ())

    def test_verified_rollback_excludes_future_and_unrelated_memory(self):
        result = assess_save_lineage(
            self.current, previous=replace(self.previous, ticks=200),
            commits=[self.commit, replace(self.commit, save_commit_id="future", ticks=200)],
            provenance=self.provenance,
        )
        invalid = [
            replace(self.memory, record_id="future-commit", save_commit_id="future"),
            replace(self.memory, record_id="future-event", event_seq=21),
            replace(self.memory, record_id="future-time", ticks=101),
            replace(self.memory, record_id="other-epoch", working_epoch="epoch-2", event_seq=1),
            replace(self.memory, record_id="other-scope", save_scope_id="scope-2"),
            replace(self.memory, record_id="ancestor-unproven", save_commit_id="ancestor", event_seq=1),
        ]
        records = [self.memory, *invalid]
        selection = select_restorable_memory(result, iter(records))
        self.assertTrue(result.can_restore)
        self.assertIn(LineageHint.TICKS_REGRESSED, result.hints)
        self.assertEqual(selection.eligible, (self.memory,))
        self.assertEqual(selection.quarantined, tuple(invalid))
        self.assertEqual(records, [self.memory, *invalid])

    def test_post_load_progress_does_not_expand_memory_restore_window(self):
        result = self.assess_verified(replace(self.current, ticks=200))
        record = replace(self.memory, ticks=150)
        selection = select_restorable_memory(result, [record])
        self.assertEqual(selection.eligible, ())
        self.assertEqual(selection.quarantined, (record,))

    def test_sequence_and_ticks_zero_are_valid_boundaries(self):
        result = self.assess_verified(
            replace(self.current, ticks=0),
            commits=[replace(self.commit, ticks=0, watermark_seq=0)],
            provenance=replace(self.provenance, watermark_seq=0),
        )
        record = replace(self.memory, event_seq=0, ticks=0)
        self.assertEqual(select_restorable_memory(result, [record]).eligible, (record,))

    def test_invalid_ticks_and_sequences_are_rejected(self):
        for value in (-1, True, 1.5, "100"):
            for original, field in (
                (self.current, "ticks"), (self.commit, "ticks"),
                (self.commit, "watermark_seq"), (self.provenance, "watermark_seq"),
                (self.memory, "event_seq"), (self.memory, "ticks"),
            ):
                with self.subTest(value=value, field=field, original=type(original).__name__):
                    with self.assertRaises(ValueError):
                        replace(original, **{field: value})

    def test_reserved_autosave_slot_is_hinted_only_on_an_inspected_build(self):
        current = replace(
            self.current, slot_id="Slot_ffffffff.save", game_build=VERIFIED_BUILD,
        )
        result = self.assess_verified(
            current, commits=[replace(self.commit, slot_id="Slot_ffffffff")],
        )
        self.assertIn(LineageHint.AUTOSAVE_SLOT_OBSERVED, result.hints)
        self.assertTrue(result.can_restore)

    def test_autosave_hint_needs_the_build_that_certifies_the_reserved_value(self):
        for build in (None, "1.128.0.1000"):
            with self.subTest(build=build):
                result = assess_save_lineage(
                    replace(self.current, slot_id="Slot_ffffffff.save", game_build=build),
                )
                self.assertNotIn(LineageHint.AUTOSAVE_SLOT_OBSERVED, result.hints)

    def test_unparseable_slot_name_yields_no_autosave_hint(self):
        result = assess_save_lineage(replace(self.current, game_build=VERIFIED_BUILD))
        self.assertNotIn(LineageHint.AUTOSAVE_SLOT_OBSERVED, result.hints)

    def test_backup_suffixes_are_the_same_slot_not_a_slot_change(self):
        current = replace(self.current, slot_id="Slot_ffffffff.save")
        previous = replace(self.previous, slot_id="SLOT_FFFFFFFF.save.ver4")
        result = assess_save_lineage(
            current, previous=previous,
            commits=[replace(self.commit, slot_id="Slot_ffffffff")],
            provenance=self.provenance,
        )
        self.assertNotIn(LineageHint.SLOT_CHANGED, result.hints)
        self.assertIn(LineageHint.TICKS_ADVANCED, result.hints)
        self.assertTrue(result.can_restore)

    def test_autosave_slot_alone_still_cannot_restore(self):
        result = assess_save_lineage(
            replace(self.current, slot_id="Slot_ffffffff.save", game_build=VERIFIED_BUILD),
            previous=self.previous, commits=[self.commit],
        )
        self.assert_unresolved(result, "load_provenance_unverified")
        self.assertIn(LineageHint.AUTOSAVE_SLOT_OBSERVED, result.hints)

    def test_empty_identity_and_nonboolean_verification_are_rejected(self):
        for original, field, value in (
            (self.current, "load_id", " "), (self.current, "slot_id", ""),
            (self.commit, "save_scope_id", ""), (self.commit, "state", "COMMITTED"),
            (self.provenance, "evidence_id", ""), (self.memory, "record_id", None),
            (self.provenance, "verified", "true"), (self.commit, "verified", 1),
            (self.current, "game_build", ""),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    replace(original, **{field: value})


if __name__ == "__main__":
    unittest.main()
