"""Phase 3b: relationship summaries and Reflexion self-notes.

Both are derived, ai_inferred rows: they compress or annotate observed
evidence and can never become canon without the promotion gate.
"""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from plumbmate.cli import main as cli_main
from plumbmate.engine import CompanionEngine
from plumbmate.memory import MemoryStore
from plumbmate.cli import bridge_from
from plumbmate.protocol import atomic_write_json
from plumbmate.providers import MockProvider
from plumbmate.reflection import (
    Finding, RuleSummarizer, _evidence, findings, maintain, reflect, refresh_relationship_summaries,
)

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


class Harness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-reflection-")
        self.addCleanup(lambda: shutil.rmtree(temporary, True))
        self.root = Path(temporary)
        self.clock = Clock()
        self.store = MemoryStore(self.root / "memory.sqlite3", now=self.clock)
        self.addCleanup(self.store.close)
        # Something the session already knows, so a later miss is a real gap.
        self.store.add_memory(SESSION, "canon", "에이든은 요리를 잘한다", provenance="player_canon")
        self.clock.advance(60)

    def chat(self, message, reply="응, 그러자"):
        turn = request(message)
        self.store.remember_turn_request(turn)
        self.store.remember_turn_response(turn, {"partner_message": reply})
        return turn

    def summaries(self):
        return self.store.rows_of(SESSION, memory_type="relationship_summary")


class RelationshipSummaryTests(Harness):
    def test_pair_with_enough_evidence_gets_one_active_summary(self):
        for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
            self.chat(line)
            self.clock.advance(3600)
        created = refresh_relationship_summaries(self.store, SESSION)
        self.assertEqual(len(created), 1)
        row = self.store.get(created[0])
        self.assertEqual((row["memory_type"], row["provenance"], row["status"]), ("relationship_summary", "ai_inferred", "active"))
        self.assertLessEqual(row["confidence"], 0.9)
        self.assertIn("케이트↔에이든", row["summary"])
        self.assertIn("기억 6건", row["summary"])
        self.assertIsNotNone(row["source_ids"])
        # The summary takes at most one of the six slots (type cap) while the
        # raw lines are fresh, and keeps its place as they decay (30-day
        # half-life against 7), labeled as the companion's own reading.
        fresh = self.store.recall(request("낚시 어땠어?"))
        self.assertEqual(sum(1 for r in fresh if r.memory_type == "relationship_summary"), 1)
        self.assertEqual(len(fresh), 6)
        self.clock.advance(14 * DAY)
        recalled = self.store.recall(request("낚시 어땠어?"))
        chosen = [r for r in recalled if r.memory_type == "relationship_summary"]
        self.assertEqual(len(chosen), 1)
        self.assertTrue(chosen[0].line().startswith("[에이든의 추측]"))

    def test_too_little_evidence_yields_nothing(self):
        self.chat("안녕")
        self.assertEqual(refresh_relationship_summaries(self.store, SESSION), [])

    def test_regeneration_waits_for_the_interval_and_new_evidence(self):
        for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
            self.chat(line)
        first = refresh_relationship_summaries(self.store, SESSION)[0]
        self.clock.advance(8 * DAY)
        self.assertEqual(refresh_relationship_summaries(self.store, SESSION), [], "old but no new evidence")
        self.chat("새로운 일이 생겼어")
        second = refresh_relationship_summaries(self.store, SESSION)
        self.assertEqual(len(second), 1)
        self.clock.advance(DAY)
        self.chat("오늘도 재밌었어")
        self.assertEqual(refresh_relationship_summaries(self.store, SESSION), [], "younger than a week")
        self.assertEqual(self.store.get(first)["status"], "superseded")
        self.assertEqual(self.store.get(first)["superseded_by"], second[0])
        active = [r for r in self.summaries() if r["status"] == "active"]
        self.assertEqual([r["memory_id"] for r in active], second)

    def test_quiet_relationship_retires_its_summary(self):
        for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
            self.chat(line)
        first = refresh_relationship_summaries(self.store, SESSION)[0]
        self.clock.advance(40 * DAY)
        self.assertEqual(refresh_relationship_summaries(self.store, SESSION), [])
        self.assertEqual(self.store.get(first)["status"], "archived")
        self.assertIsNone(self.store.relationship_summary(SESSION, 1, 2))

    def test_summary_is_deterministic_and_bounded(self):
        for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
            self.chat(line)
        rule = RuleSummarizer()
        evidence = _evidence(self.store.pair_evidence(SESSION, 1, 2))
        a = rule.relationship("케이트", "에이든", evidence, now=self.clock())
        b = rule.relationship("케이트", "에이든", evidence, now=self.clock())
        self.assertEqual(a, b)
        long = [type(evidence[0])(i, "아주 긴 기억 " * 40, "episodic", "chat_turn", 3, "active", self.clock())
                for i in range(50)]
        stored = self.store.add_memory(SESSION, "relationship_summary", rule.relationship("케이트", "에이든", long, now=self.clock()),
                                       provenance="ai_inferred", confidence=0.8)
        self.assertLessEqual(len(self.store.get(stored)["summary"]), 240)

    def test_summary_quoting_a_disliked_thing_is_not_disputed(self):
        self.store.add_memory(SESSION, "canon", "케이트는 매운 음식을 싫어한다", provenance="player_canon")
        for line in ("나 매운 음식 좋아해", "같이 낚시 가자", "저녁은 내가 할게"):
            self.chat(line)
        created = refresh_relationship_summaries(self.store, SESSION)
        self.assertEqual(self.store.get(created[0])["status"], "active")


class ReflectionTests(Harness):
    def test_missed_recall_becomes_a_note_that_wakes_on_the_topic(self):
        self.assertEqual(self.store.recall(request("우리 처음 만난 공원 기억나?")), [])
        created = reflect(self.store, SESSION)
        self.assertEqual(len(created), 1)
        row = self.store.get(created[0])
        self.assertEqual((row["event_kind"], row["provenance"], row["confidence"]), ("reflection", "ai_inferred", 0.6))
        self.assertIn("떠오른 기억이 없었다", row["summary"])
        self.assertIn("공원", row["trigger_keywords"])
        self.clock.advance(2 * DAY)
        off_topic = self.store.recall(request("오늘 저녁 뭐 먹을까"))
        self.assertNotIn(created[0], [r.memory_id for r in off_topic])
        on_topic = self.store.recall(request("공원에서 처음 만난 날 말이야"))
        chosen = next(r for r in on_topic if r.memory_id == created[0])
        self.assertEqual(chosen.breakdown["open"], 1.0)
        self.assertIn("되새길 교훈", chosen.reason)

    def test_open_promise_and_disputed_guess_are_raised_first(self):
        promise = self.store.add_memory(SESSION, "promise", "주말에 같이 캠핑 가기로 했다", status="open",
                                        trigger_keywords=["캠핑"])
        self.store.add_memory(SESSION, "canon", "베라는 매운 음식을 싫어한다", provenance="player_canon")
        guess = self.store.add_memory(SESSION, "sentiment", "베라는 매운 음식을 좋아한다",
                                      provenance="ai_inferred", confidence=0.6)
        self.clock.advance(4 * DAY)
        self.store.recall(request("무슨 노래 좋아해?"))
        found = findings(self.store, SESSION)
        self.assertEqual([f.kind for f in found], ["open_promise", "disputed_inference", "missed_recall"])
        self.assertEqual(found[0].source_ids, (promise,))
        self.assertEqual(found[0].days, 4)
        self.assertEqual(found[1].source_ids, (guess,))
        created = reflect(self.store, SESSION, max_lines=2)
        self.assertEqual(len(created), 2)
        texts = [self.store.get(i)["summary"] for i in created]
        self.assertIn("며칠째 열려 있다", texts[0])
        self.assertIn("보류 중이다", texts[1])

    def test_an_open_promise_yields_one_note_across_daily_runs(self):
        promise = self.store.add_memory(SESSION, "promise", "주말에 같이 캠핑 가기로 했다", status="open",
                                        trigger_keywords=["캠핑"])
        self.clock.advance(4 * DAY)
        first = reflect(self.store, SESSION)
        self.assertEqual(len(first), 1)
        for _ in range(6):
            self.clock.advance(DAY)
            self.assertEqual(reflect(self.store, SESSION), [])
        notes = [r for r in self.store.rows_of(SESSION, event_kind="reflection") if r["status"] == "active"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(self.store.active_note(SESSION, "open_promise", [promise]), first[0])
        recalled = self.store.recall(request("캠핑 언제 가?"))
        self.assertEqual(sum(1 for r in recalled if r.memory_id in first), 1)

    def test_regeneration_sees_new_evidence_beyond_the_highlight_sample(self):
        kate = self.store.ensure_entity("sim", "1", "케이트")
        aiden = self.store.ensure_entity("sim", "2", "에이든")
        for index in range(10):
            self.store.add_memory(SESSION, "episodic", f"큰 사건 {index}", importance=7,
                                  entities={kate: "actor", aiden: "target"})
        first = refresh_relationship_summaries(self.store, SESSION)[0]
        self.clock.advance(8 * DAY)
        for line in ("오늘 산책했어", "저녁 같이 먹자"):
            self.chat(line)
        second = refresh_relationship_summaries(self.store, SESSION)
        self.assertEqual(len(second), 1, "new low-importance chat is still new evidence")
        self.assertEqual(self.store.get(first)["status"], "superseded")
        self.assertIn("기억 14건", self.store.get(second[0])["summary"])

    def test_reflection_is_idempotent_within_the_window(self):
        self.store.recall(request("우리 처음 만난 공원 기억나?"))
        self.assertEqual(len(reflect(self.store, SESSION)), 1)
        self.assertEqual(reflect(self.store, SESSION), [])
        self.clock.advance(8 * DAY)
        self.assertEqual(reflect(self.store, SESSION), [], "the miss itself has aged out of the window")

    def test_first_turns_of_an_empty_session_are_not_misses(self):
        empty = "household-9"
        self.assertEqual(self.store.recall(request("우리 처음 만난 공원 기억나?", session=empty)), [])
        self.assertEqual(reflect(self.store, empty), [])

    def test_short_queries_are_not_worth_a_note(self):
        self.store.recall(request("안녕"))
        self.assertEqual(reflect(self.store, SESSION), [])

    def test_notes_never_become_canon_by_themselves(self):
        self.store.recall(request("우리 처음 만난 공원 기억나?"))
        note = reflect(self.store, SESSION)[0]
        for _ in range(5):
            self.store.recall(request("공원에서 처음 만난 날"))
        row = self.store.get(note)
        self.assertEqual((row["provenance"], row["confidence"], row["memory_type"]), ("ai_inferred", 0.6, "episodic"))
        self.store.promote_to_canon(note, "player_confirm", "맞아, 공원에서 처음 만났지")
        self.assertEqual(self.store.get(note)["provenance"], "player_canon")

    def test_notes_expire_with_other_inferences_and_never_merge(self):
        self.store.recall(request("우리 처음 만난 공원 기억나?"))
        note = reflect(self.store, SESSION)[0]
        self.assertEqual(self.store.consolidate(), 0)
        self.clock.advance(61 * DAY)
        self.store.archive_stale()
        self.assertEqual(self.store.get(note)["status"], "archived")

    def test_unknown_finding_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            RuleSummarizer().reflection(Finding("mystery", "x"))


class MaintainTests(Harness):
    def test_maintain_runs_every_job_for_every_scoped_session(self):
        self.assertEqual(self.store.recall(request("우리 처음 만난 공원 기억나?")), [])
        for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
            self.chat(line)
        self.store.remember_event("zone_loaded", {"lot": "home"})  # unscoped: audit only
        result = maintain(self.store)
        self.assertEqual(result, {"merged_groups": 0, "archived": 0, "summaries": 1, "reflections": 1})
        self.assertEqual(maintain(self.store), {"merged_groups": 0, "archived": 0, "summaries": 0, "reflections": 0})
        self.assertEqual(self.store.rows_of("unscoped", memory_type="relationship_summary"), [])

    def test_engine_runs_maintenance_every_n_turns(self):
        paths = bridge_from(self.root / "bridge")
        paths.ensure()
        store = MemoryStore(paths.state / "memory.sqlite3")
        self.addCleanup(store.close)
        store.add_memory(SESSION, "canon", "에이든은 요리를 잘한다", provenance="player_canon")
        engine = CompanionEngine(paths, MockProvider(), memory=store, recall=True, maintenance_every=2)
        for message in ("우리 처음 만난 공원 기억나?", "오늘 저녁 뭐 먹을까", "낚시 갈래?"):
            turn = request(message)
            atomic_write_json(paths.requests / f"{turn['request_id']}.json", turn)
            self.assertIsNotNone(engine.process_one())
        self.assertEqual((engine.completed_turns, engine.maintenance_runs), (3, 1))
        self.assertEqual(engine.last_maintenance["reflections"], 1)
        self.assertEqual(engine.memory_errors, 0)
        notes = store.rows_of(SESSION, event_kind="reflection")
        self.assertEqual(len(notes), 1)

    def test_maintenance_counter_persists_across_processes(self):
        paths = bridge_from(self.root / "bridge")
        paths.ensure()
        store = MemoryStore(paths.state / "memory.sqlite3")
        self.addCleanup(store.close)

        def one_turn(engine, message):
            turn = request(message)
            atomic_write_json(paths.requests / f"{turn['request_id']}.json", turn)
            self.assertIsNotNone(engine.process_one())

        one_turn(CompanionEngine(paths, MockProvider(), memory=store, maintenance_every=3), "첫 번째")
        one_turn(CompanionEngine(paths, MockProvider(), memory=store, maintenance_every=3), "두 번째")
        self.assertEqual(store.turns_since_maintenance(), 2)
        third = CompanionEngine(paths, MockProvider(), memory=store, maintenance_every=3)
        one_turn(third, "세 번째")
        self.assertEqual((third.completed_turns, third.maintenance_runs), (1, 1))
        self.assertEqual(store.turns_since_maintenance(), 0)

    def test_a_failing_maintenance_never_fails_the_turn(self):
        from unittest import mock
        paths = bridge_from(self.root / "bridge")
        paths.ensure()
        store = MemoryStore(paths.state / "memory.sqlite3")
        self.addCleanup(store.close)
        engine = CompanionEngine(paths, MockProvider(), memory=store, maintenance_every=1)
        turn = request("안녕")
        atomic_write_json(paths.requests / f"{turn['request_id']}.json", turn)
        with mock.patch("plumbmate.engine.maintain", side_effect=RuntimeError("disk full")):
            self.assertIsNotNone(engine.process_one())
        self.assertEqual((engine.maintenance_runs, engine.memory_errors), (0, 1))
        self.assertTrue((paths.responses / f"{turn['request_id']}.json").exists())

    def test_engine_default_and_disabled_maintenance(self):
        paths = bridge_from(self.root / "bridge")
        self.assertEqual(CompanionEngine(paths, MockProvider()).maintenance_every, 20)
        self.assertEqual(CompanionEngine(paths, MockProvider(), maintenance_every=-3).maintenance_every, 0)

    def test_a_broken_store_file_does_not_stop_serving(self):
        bridge = self.root / "bridge"
        bridge.mkdir()
        broken = self.root / "not-a-database.sqlite3"
        broken.write_text("this is not sqlite")
        self.assertEqual(cli_main(["serve-once", "--bridge", str(bridge), "--memory-db", str(broken)]), 0)
        self.assertEqual(cli_main(["memory-stats", "--bridge", str(bridge), "--memory-db", str(broken)]), 2)

    def test_admin_cli_actions(self):
        bridge = self.root / "bridge"
        db = bridge / "state" / "memory.sqlite3"
        with MemoryStore(db) as store:
            store.add_memory(SESSION, "canon", "에이든은 요리를 잘한다", provenance="player_canon")
            store.recall(request("우리 처음 만난 공원 기억나?"))
            for line in ("같이 낚시 가자", "저녁은 내가 할게", "정원에 꽃 심었어"):
                turn = request(line)
                store.remember_turn_request(turn)
                store.remember_turn_response(turn, {"partner_message": "좋아"})
        base = ["memory-admin", "--bridge", str(bridge)]
        self.assertEqual(cli_main(base + ["summarize", "--session", SESSION]), 0)
        self.assertEqual(cli_main(base + ["reflect"]), 0)
        self.assertEqual(cli_main(base + ["maintain"]), 0)
        with MemoryStore(db) as store:
            self.assertEqual(len(store.rows_of(SESSION, memory_type="relationship_summary")), 1)
            self.assertEqual(len(store.rows_of(SESSION, event_kind="reflection")), 1)


if __name__ == "__main__":
    unittest.main()
