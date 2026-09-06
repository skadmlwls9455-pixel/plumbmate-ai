"""RFC-001 §8/§9 lifecycle: merge, archive, pin, edit, delete, dispute, promote.

Every transition is a status change; the only physical DELETE is the explicit
purge. Golden tests T6, T7, T8, T11, T12 from the RFC live here.
"""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from plumbmate.cli import main as cli_main
from plumbmate.memory import MemoryStore

DAY = 86400.0
SESSION = "household-1"


def request(message, session=SESSION):
    return {
        "protocol_version": "0.1", "request_id": uuid.uuid4().hex, "kind": "turn",
        "created_at": "2026-09-06T00:00:00+00:00", "player_message": message,
        "session_id": session, "actor": {"sim_id": 1, "name": "케이트"},
        "household": [{"sim_id": 1, "name": "케이트"}, {"sim_id": 2, "name": "에이든"}],
        "recent_events": [], "memory_summary": "",
    }


class Clock(object):
    def __init__(self, value=1_700_000_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class LifecycleHarness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-lifecycle-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.root = Path(temporary)
        self.clock = Clock()
        self.store = MemoryStore(self.root / "memory.sqlite3", now=self.clock)
        self.addCleanup(self.store.close)

    def status(self, memory_id):
        return self.store.get(memory_id)["status"]

    def recalled_ids(self, message):
        return [r.memory_id for r in self.store.recall(request(message))]


class MergeTests(LifecycleHarness):
    def test_three_identical_observations_fold_into_one_summary(self):
        # RFC-001 T6
        ids = []
        for _ in range(3):
            ids.append(self.store.remember_event("zone_loaded", {"lot": "home", "session_id": SESSION}))
            self.clock.advance(6 * 3600)
        self.assertEqual(self.store.consolidate(), 1)
        for memory_id in ids:
            self.assertEqual(self.status(memory_id), "merged")
            self.assertIsNotNone(self.store.get(memory_id)["merged_into"])
        summary_id = self.store.get(ids[0])["merged_into"]
        summary = self.store.get(summary_id)
        self.assertEqual(summary["status"], "active")
        self.assertIn("3회 반복", summary["summary"])
        self.assertEqual(summary["provenance"], "game_observed")
        recalled = self.recalled_ids("zone_loaded home")
        self.assertIn(summary_id, recalled)
        self.assertFalse(set(ids) & set(recalled), "merged originals must stay hidden")

    def test_events_outside_the_window_do_not_merge(self):
        for _ in range(3):
            self.store.remember_event("zone_loaded", {"lot": "home", "session_id": SESSION})
            self.clock.advance(2 * DAY)
        self.assertEqual(self.store.consolidate(), 0)

    def test_chat_turns_and_pinned_rows_are_never_merged(self):
        for _ in range(3):
            self.store.remember_turn_request(request("오늘도 같이 요리했어"))
        pinned = []
        for _ in range(3):
            pinned.append(self.store.remember_event("zone_loaded", {"lot": "park", "session_id": SESSION}))
        for memory_id in pinned:
            self.store.pin(memory_id)
        self.assertEqual(self.store.consolidate(), 0)
        self.assertTrue(all(self.status(i) == "active" for i in pinned))


class ArchiveTests(LifecycleHarness):
    def test_stale_trivial_rows_are_archived_but_pinned_ones_survive(self):
        # RFC-001 T7
        stale = self.store.add_memory(SESSION, "episodic", "평범한 점심을 먹었다", importance=2)
        pinned = self.store.add_memory(SESSION, "episodic", "첫 데이트 장소를 골랐다", importance=2, pinned=True)
        important = self.store.add_memory(SESSION, "episodic", "결혼식을 올렸다", importance=9)
        self.clock.advance(100 * DAY)
        self.assertEqual(self.store.archive_stale(), 1)
        self.assertEqual(self.status(stale), "archived")
        self.assertEqual(self.status(pinned), "active")
        self.assertEqual(self.status(important), "active")
        recalled = self.store.recall(request("데이트 장소"))
        self.assertIn(pinned, [r.memory_id for r in recalled])
        self.assertEqual(next(r for r in recalled if r.memory_id == pinned).breakdown["pin"], 1.5)

    def test_recalled_rows_are_not_archived(self):
        row = self.store.add_memory(SESSION, "episodic", "케이트와 산책했다", importance=1)
        self.store.recall(request("케이트 산책"))
        self.clock.advance(100 * DAY)
        self.assertEqual(self.store.archive_stale(), 0)
        self.assertEqual(self.status(row), "active")

    def test_unpromoted_inferences_expire(self):
        guess = self.store.add_memory(SESSION, "sentiment", "에이든은 케이트를 신뢰하는 것 같다",
                                      provenance="ai_inferred", confidence=0.7, importance=6)
        self.clock.advance(61 * DAY)
        self.assertEqual(self.store.archive_stale(), 1)
        self.assertEqual(self.status(guess), "archived")


class EditAndDeleteTests(LifecycleHarness):
    def test_edit_supersedes_and_only_the_new_row_is_recalled(self):
        # RFC-001 T8
        old = self.store.add_memory(SESSION, "canon", "에이든은 고소공포증이 있다",
                                    provenance="player_canon", importance=8)
        new = self.store.edit(old, "에이든은 고소공포증을 극복했다")
        self.assertEqual(self.status(old), "superseded")
        self.assertEqual(self.store.get(old)["superseded_by"], new)
        recalled = self.recalled_ids("에이든 고소공포증")
        self.assertIn(new, recalled)
        self.assertNotIn(old, recalled)
        with self.assertRaises(ValueError):
            self.store.edit(old, "다시 고칠 수 없다")

    def test_soft_delete_hides_and_purge_removes(self):
        row = self.store.add_memory(SESSION, "episodic", "지워질 기억")
        self.store.soft_delete(row)
        self.assertEqual(self.status(row), "deleted")
        self.assertIsNotNone(self.store.get(row), "soft delete keeps the row")
        self.assertNotIn(row, self.recalled_ids("지워질 기억"))
        self.assertEqual(self.store.purge_deleted(), 1)
        self.assertIsNone(self.store.get(row))
        self.assertEqual(self.store.purge_deleted(), 0)


class DisputeAndPromotionTests(LifecycleHarness):
    def test_contradicting_inference_is_quarantined(self):
        # RFC-001 T11
        canon = self.store.add_memory(SESSION, "canon", "베라는 매운 음식을 싫어한다",
                                      provenance="player_canon", importance=7)
        guess = self.store.add_memory(SESSION, "sentiment", "베라는 매운 음식을 좋아한다",
                                      provenance="ai_inferred", confidence=0.6)
        self.assertEqual(self.status(guess), "disputed")
        self.assertIn(f"#{canon}", self.store.get(guess)["details"])
        recalled = self.recalled_ids("베라 매운 음식")
        self.assertIn(canon, recalled)
        self.assertNotIn(guess, recalled)

    def test_a_merely_different_inference_is_not_disputed(self):
        self.store.add_memory(SESSION, "canon", "베라는 매운 음식을 싫어한다", provenance="player_canon")
        other = self.store.add_memory(SESSION, "sentiment", "베라는 낚시를 좋아한다",
                                      provenance="ai_inferred", confidence=0.6)
        self.assertEqual(self.status(other), "active")

    def test_promotion_needs_evidence_and_is_recorded(self):
        # RFC-001 T12
        guess = self.store.add_memory(SESSION, "sentiment", "에이든은 개를 무서워하는 것 같다",
                                      provenance="ai_inferred", confidence=0.5)
        with self.assertRaises(ValueError):
            self.store.promote_to_canon(guess, "player_confirm", "")
        with self.assertRaises(ValueError):
            self.store.promote_to_canon(guess, "gut_feeling", "just because")
        self.assertEqual(self.store.promotions(guess), [])
        self.store.promote_to_canon(guess, "player_confirm", "맞아, 에이든은 개를 무서워해")
        row = self.store.get(guess)
        self.assertEqual((row["provenance"], row["memory_type"], row["confidence"]), ("player_canon", "canon", 1.0))
        log = self.store.promotions(guess)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["old_provenance"], "ai_inferred")
        self.assertEqual(log[0]["evidence_kind"], "player_confirm")
        # Promoted canon never decays and is recalled without the guess label.
        recalled = self.store.recall(request("에이든 개 무서워"))
        chosen = next(r for r in recalled if r.memory_id == guess)
        self.assertEqual(chosen.breakdown["rec"], 1.0)
        self.assertFalse(chosen.line().startswith("[에이든의 추측]"))

    def test_repetition_never_raises_confidence(self):
        guess = self.store.add_memory(SESSION, "sentiment", "에이든은 커피를 좋아하는 것 같다",
                                      provenance="ai_inferred", confidence=0.5)
        for _ in range(5):
            self.store.recall(request("에이든 커피"))
        self.assertEqual(self.store.get(guess)["confidence"], 0.5)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._db.execute("UPDATE memories SET confidence = 1.0 WHERE memory_id = ?", (guess,))


class AdminCliTests(LifecycleHarness):
    def test_memory_admin_round_trip(self):
        bridge = self.root / "bridge"
        db = bridge / "state" / "memory.sqlite3"
        with MemoryStore(db) as store:
            row = store.add_memory(SESSION, "episodic", "CLI로 다룰 기억")
        base = ["memory-admin", "--bridge", str(bridge)]
        self.assertEqual(cli_main(base + ["pin", "--id", str(row)]), 0)
        self.assertEqual(cli_main(base + ["show", "--id", str(row)]), 0)
        self.assertEqual(cli_main(base + ["edit", "--id", str(row), "--summary", "고쳐진 기억"]), 0)
        self.assertEqual(cli_main(base + ["promote", "--id", str(row)]), 2, "promotion without evidence is refused")
        self.assertEqual(cli_main(base + ["show", "--id", "999"]), 1)
        self.assertEqual(cli_main(base + ["consolidate"]), 0)
        self.assertEqual(cli_main(base + ["archive"]), 0)
        with MemoryStore(db) as store:
            self.assertEqual(store.get(row)["status"], "superseded")
            self.assertEqual(store.get(row)["pinned"], 1)


if __name__ == "__main__":
    unittest.main()
