"""The memory store: tokenizing, scoring, selection, scope, and the audit log.

These are golden tests for the rule-based recall in RFC-001 §4 as shipped in
the Phase 1 slice. No model, no game, no network. A fake clock drives recency.
"""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from plumbmate.memory import UNSCOPED_SESSION, MemoryStore, Recalled
from plumbmate.memory_text import fts_query, pack_lines, strip_particle, tokenize

DAY = 86400.0


def request(message, session="household-1", request_id=None, actor=None, household=None, events=None):
    return {
        "protocol_version": "0.1",
        "request_id": request_id or uuid.uuid4().hex,
        "kind": "turn",
        "created_at": "2026-09-06T00:00:00+00:00",
        "player_message": message,
        "session_id": session,
        "actor": actor or {"sim_id": 1, "name": "케이트"},
        "household": household if household is not None else [{"sim_id": 1, "name": "케이트"}, {"sim_id": 2, "name": "에이든"}],
        "recent_events": events or [],
        "memory_summary": "",
    }


class Clock(object):
    def __init__(self, value=1_700_000_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class TextTests(unittest.TestCase):
    def test_particles_are_stripped_but_short_stems_are_kept(self):
        self.assertEqual(strip_particle("낚시를"), "낚시")
        self.assertEqual(strip_particle("뒷마당에서"), "뒷마당")
        self.assertEqual(strip_particle("은행"), "은행")
        self.assertEqual(strip_particle("에"), "에")

    def test_tokenize_lowercases_dedupes_and_drops_single_characters(self):
        self.assertEqual(tokenize("Aiden이 낚시를 낚시를 갔다. 가!"), ["aiden", "낚시", "갔다"])

    def test_fts_query_quotes_terms(self):
        self.assertEqual(fts_query(['a"b', "c"]), '"a""b" OR "c"')

    def test_pack_lines_drops_instead_of_truncating(self):
        packed = pack_lines(["a" * 100, "b" * 100, "c" * 100], max_chars=205, max_line_chars=240)
        self.assertEqual(packed, "a" * 100 + "\n" + "b" * 100)


class StoreHarness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-memory-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.clock = Clock()
        self.store = MemoryStore(Path(temporary) / "memory.sqlite3", now=self.clock)
        self.addCleanup(self.store.close)


class StoreTests(StoreHarness):
    def test_empty_store_recalls_nothing_and_does_not_crash(self):
        self.assertEqual(self.store.recall(request("아까 뭐 했었지?")), [])
        self.assertEqual(self.store.recall_summary(request("아무거나")), "")

    def test_turn_request_is_stored_with_actor_and_witness_links(self):
        ids = self.store.remember_turn_request(request("에이든, 뒷마당에서 불이 났어", events=["그릴에 불이 붙었다"]))
        self.assertEqual(len(ids), 2)
        stats = self.store.stats()
        self.assertEqual(stats["memories"]["episodic"], 2)
        # "에이든" appears in the text, so the household member is also linked as mentioned.
        rows = self.store._db.execute(
            "SELECT role FROM memory_entities WHERE memory_id = ? ORDER BY role", (ids[0],)).fetchall()
        self.assertIn("actor", [r["role"] for r in rows])
        self.assertIn("witness", [r["role"] for r in rows])

    def test_repeated_recent_events_are_not_duplicated_within_a_day(self):
        self.store.remember_turn_request(request("안녕", events=["둘이 이사했다"]))
        self.store.remember_turn_request(request("또 안녕", events=["둘이 이사했다"]))
        count = self.store._db.execute(
            "SELECT COUNT(*) FROM memories WHERE event_kind = 'recent_event'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_keyword_recall_finds_the_fire(self):
        self.store.remember_turn_request(request("에이든, 뒷마당에서 불이 났어. 무서웠어"))
        self.store.remember_turn_request(request("오늘 저녁은 파스타 어때"))
        recalled = self.store.recall(request("아까 뒷마당 불난 거 기억나?"))
        self.assertTrue(recalled)
        self.assertIn("뒷마당", recalled[0].summary)
        # "불난" vs "불이" is a morphology miss (RFC-001 F2); coverage is honest, not 1.0.
        self.assertGreater(recalled[0].breakdown["kw"], 0.0)
        self.assertNotIn("파스타", recalled[0].summary)

    def test_alias_lookup_links_mentions_to_entities(self):
        self.store.remember_turn_request(request("베라랑 낚시 갔다 왔어", household=[
            {"sim_id": 1, "name": "케이트"}, {"sim_id": 7, "name": "베라"}]))
        rows = self.store._db.execute(
            "SELECT e.name, me.role FROM memory_entities me JOIN entities e ON e.entity_id = me.entity_id").fetchall()
        roles = {(r["name"], r["role"]) for r in rows}
        self.assertIn(("베라", "witness"), roles)

    def test_importance_outlasts_recency(self):
        # RFC-001 T5: a major event from months ago beats a trivial one from yesterday.
        session = "household-1"
        self.store.add_memory(session, "episodic", "케이트와 결혼식 날 함께 춤췄다", importance=9,
                              event_kind="wedding", created_at=self.clock.value)
        self.clock.advance(200 * DAY)
        self.store.add_memory(session, "episodic", "케이트와 아침을 먹었다", importance=1,
                              event_kind="routine", created_at=self.clock.value)
        self.clock.advance(1 * DAY)
        recalled = self.store.recall(request("케이트랑 뭐 했었지", session=session))
        self.assertGreaterEqual(len(recalled), 2)
        self.assertIn("결혼식", recalled[0].summary, [r.summary for r in recalled])

    def test_selection_is_capped_at_six(self):
        for index in range(30):
            self.store.add_memory("household-1", "episodic", f"에이든과 {index}번째 낚시를 갔다", importance=8)
        recalled = self.store.recall(request("낚시 얘기 해줘"))
        self.assertEqual(len(recalled), 6)
        summary = self.store.recall_summary(request("낚시 얘기 해줘"))
        self.assertLessEqual(len(summary), 1600)
        self.assertEqual(summary.count("\n"), 5)

    def test_canon_is_capped_at_three_per_turn(self):
        for index in range(5):
            self.store.add_memory("household-1", "canon", f"에이든은 고소공포증이 있다 ({index})", importance=9)
        recalled = self.store.recall(request("에이든 고소공포증 있었나"))
        self.assertEqual(sum(1 for r in recalled if r.memory_type == "canon"), 3)

    def test_sessions_do_not_leak_into_each_other(self):
        self.store.remember_turn_request(request("뒷마당에 불이 났어", session="household-A"))
        self.assertEqual(self.store.recall(request("불난 거 기억나?", session="household-B")), [])
        self.assertTrue(self.store.recall(request("불난 거 기억나?", session="household-A")))

    def test_unscoped_events_are_stored_for_audit_but_never_recalled(self):
        self.assertIsNone(self.store.remember_event("heartbeat", {"source": "keepalive"}))
        memory_id = self.store.remember_event("zone_loaded", {"lot": "home", "fire": True})
        self.assertIsNotNone(memory_id)
        self.assertEqual(self.store.recall(request("fire zone_loaded", session=UNSCOPED_SESSION)), [])
        self.assertEqual(self.store.recall(request("zone_loaded fire", session="household-1")), [])
        self.assertTrue(self.store.search("zone_loaded"))

    def test_scoped_event_is_recalled(self):
        self.store.remember_event("zone_loaded", {"lot": "beach", "session_id": "household-1"})
        recalled = self.store.recall(request("beach 갔던 거", session="household-1"))
        self.assertTrue(recalled)
        self.assertEqual(recalled[0].provenance, "game_observed")

    def test_ai_inferred_confidence_is_hard_capped(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_memory("household-1", "sentiment", "에이든은 케이트를 신뢰한다",
                                  provenance="ai_inferred", confidence=1.0)
        memory_id = self.store.add_memory("household-1", "sentiment", "에이든은 케이트를 신뢰한다",
                                          provenance="ai_inferred", confidence=0.6)
        recalled = self.store.recall(request("에이든 케이트 신뢰"))
        self.assertTrue(any(r.memory_id == memory_id for r in recalled))
        chosen = next(r for r in recalled if r.memory_id == memory_id)
        self.assertEqual(chosen.breakdown["conf"], 0.6)
        self.assertTrue(chosen.line().startswith("[에이든의 추측]"))

    def test_open_promise_with_matching_trigger_is_boosted(self):
        promise = self.store.add_memory("household-1", "promise", "다음 비 오는 날 낚시 가기로 했다",
                                        status="open", trigger_keywords=["비", "낚시"])
        self.store.add_memory("household-1", "episodic", "낚시 도구를 정리했다", importance=2)
        recalled = self.store.recall(request("오늘 낚시 어때"))
        self.assertEqual(recalled[0].memory_id, promise)
        self.assertEqual(recalled[0].breakdown["open"], 1.5)
        self.assertIn("미완료 약속", recalled[0].reason)

    def test_retrieval_log_explains_every_candidate(self):
        self.store.remember_turn_request(request("뒷마당에서 불이 났어"))
        turn = request("불난 거 기억나?", request_id="a" * 32)
        recalled = self.store.recall(turn)
        rows = self.store.explain("a" * 32)
        self.assertTrue(rows)
        self.assertTrue(any(r["selected"] for r in rows))
        self.assertIn("breakdown", rows[0])
        self.assertIn("reason", rows[0])
        count = self.store._db.execute(
            "SELECT recall_count FROM memories WHERE memory_id = ?", (recalled[0].memory_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_config_is_a_single_tunable_row(self):
        self.store.set_config(w_kw=3.0)
        self.assertEqual(self.store.config()["w_kw"], 3.0)
        with self.assertRaises(ValueError):
            self.store.set_config(bogus=1)


class NoFtsStore(MemoryStore):
    def _enable_fts(self):
        return False


class FallbackTests(unittest.TestCase):
    def test_like_fallback_still_finds_keywords(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-memory-nofts-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        store = NoFtsStore(Path(temporary) / "memory.sqlite3")
        self.addCleanup(store.close)
        self.assertFalse(store.fts_available)
        store.remember_turn_request(request("뒷마당에서 불이 났어"))
        recalled = store.recall(request("불난 거 기억나?"))
        self.assertTrue(recalled)
        self.assertIsInstance(recalled[0], Recalled)


if __name__ == "__main__":
    unittest.main()
