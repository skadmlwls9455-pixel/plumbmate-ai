"""Memory wired into the turn engine: record always, recall only when asked,
never let the store decide a turn's fate."""

import tempfile
import unittest
import uuid
from pathlib import Path

from plumbmate.engine import CompanionEngine
from plumbmate.memory import MemoryStore
from plumbmate.protocol import PROTOCOL_VERSION, BridgePaths, atomic_write_json
from plumbmate.providers import MockProvider


class SpyProvider(object):
    """Delegates to the mock and keeps the last request the engine sent."""

    def __init__(self):
        self.inner = MockProvider()
        self.requests = []

    def respond(self, request):
        self.requests.append(dict(request))
        return self.inner.respond(request)


class ExplodingStore(MemoryStore):
    def recall_summary(self, request):
        raise RuntimeError("disk on fire")

    def remember_turn_request(self, request):
        raise RuntimeError("disk on fire")


class EngineMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-engine-memory-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.paths = BridgePaths(Path(temporary))
        self.paths.ensure()
        self.db = self.paths.state / "memory.sqlite3"

    def write_turn(self, message, session="household-1"):
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": uuid.uuid4().hex,
            "kind": "turn",
            "created_at": "2026-09-06T00:00:00+00:00",
            "player_message": message,
            "session_id": session,
            "actor": {"sim_id": 1, "name": "케이트"},
            "household": [{"sim_id": 1, "name": "케이트"}, {"sim_id": 2, "name": "에이든"}],
            "roles": {"binding_id": "a" * 32, "scene_id": "b" * 32, "player_sim_id": 1, "partner_sim_id": 2},
            "recent_events": [],
            "memory_summary": "게임이 보낸 요약은 항상 버려진다",
        }
        atomic_write_json(self.paths.requests / f"{request['request_id']}.json", request)
        return request

    def test_recall_feeds_an_earlier_turn_into_a_later_one(self):
        spy = SpyProvider()
        with MemoryStore(self.db) as memory:
            engine = CompanionEngine(self.paths, spy, memory=memory, recall=True)
            self.write_turn("에이든, 뒷마당에서 불이 났어. 진짜 무서웠어")
            self.assertIsNotNone(engine.process_one())
            self.assertEqual(spy.requests[0]["memory_summary"], "", "nothing to recall on the first turn")
            self.write_turn("아까 뒷마당 불난 거 기억나?")
            self.assertIsNotNone(engine.process_one())
            summary = spy.requests[1]["memory_summary"]
            self.assertIn("뒷마당", summary)
            self.assertNotIn("아까 뒷마당 불난 거 기억나", summary, "a turn must not recall itself")
            self.assertGreaterEqual(engine.last_recall_lines, 1)
            self.assertEqual(engine.memory_errors, 0)
            # Both the player's line and the companion's reply were stored.
            self.assertGreaterEqual(memory.stats()["memories"]["episodic"], 3)

    def test_recall_is_off_by_default_but_recording_still_happens(self):
        spy = SpyProvider()
        with MemoryStore(self.db) as memory:
            engine = CompanionEngine(self.paths, spy, memory=memory)
            self.write_turn("뒷마당에서 불이 났어")
            engine.process_one()
            self.write_turn("불난 거 기억나?")
            engine.process_one()
            self.assertEqual(spy.requests[1]["memory_summary"], "")
            self.assertFalse(engine.recall_enabled)
            self.assertEqual(memory.stats()["memories"]["episodic"], 4)

    def test_no_memory_store_means_no_change_in_behaviour(self):
        spy = SpyProvider()
        engine = CompanionEngine(self.paths, spy, recall=True)
        self.write_turn("안녕")
        self.assertIsNotNone(engine.process_one())
        self.assertFalse(engine.recall_enabled)
        self.assertEqual(spy.requests[0]["memory_summary"], "")

    def test_a_broken_store_never_fails_the_turn(self):
        spy = SpyProvider()
        with ExplodingStore(self.db) as memory:
            engine = CompanionEngine(self.paths, spy, memory=memory, recall=True)
            self.write_turn("안녕")
            self.assertIsNotNone(engine.process_one())
            self.assertGreaterEqual(engine.memory_errors, 2)
            self.assertEqual(spy.requests[0]["memory_summary"], "")

    def test_recalled_summary_respects_the_request_contract(self):
        spy = SpyProvider()
        with MemoryStore(self.db) as memory:
            for index in range(40):
                memory.add_memory("household-1", "episodic",
                                  f"에이든과 낚시를 {index}번째로 갔다 " + "정말 " * 40, importance=8)
            engine = CompanionEngine(self.paths, spy, memory=memory, recall=True)
            self.write_turn("낚시 얘기 해줘")
            self.assertIsNotNone(engine.process_one())
            summary = spy.requests[0]["memory_summary"]
            self.assertTrue(summary)
            self.assertLessEqual(len(summary), 1600)
            self.assertLessEqual(summary.count("\n") + 1, 6)


class OverrunningStore(MemoryStore):
    def recall_summary(self, request):
        return "가" * 1601


class RecallContractTests(unittest.TestCase):
    def test_a_summary_over_the_contract_is_dropped_not_fatal(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-overrun-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        paths = BridgePaths(Path(temporary) / "bridge")
        paths.ensure()
        store = OverrunningStore(paths.state / "memory.sqlite3")
        self.addCleanup(store.close)
        engine = CompanionEngine(paths, MockProvider(), memory=store, recall=True)
        turn = {
            "protocol_version": PROTOCOL_VERSION, "request_id": uuid.uuid4().hex, "kind": "turn",
            "created_at": "2026-09-06T00:00:00+00:00", "player_message": "안녕",
            "session_id": "household-1", "actor": {"sim_id": 1, "name": "케이트"},
            "household": [{"sim_id": 1, "name": "케이트"}, {"sim_id": 2, "name": "에이든"}],
            "roles": {"binding_id": "a" * 32, "scene_id": "b" * 32, "player_sim_id": 1, "partner_sim_id": 2},
        }
        atomic_write_json(paths.requests / f"{turn['request_id']}.json", turn)
        self.assertIsNotNone(engine.process_one())
        self.assertEqual((engine.rejected_requests, engine.memory_errors, engine.last_recall_lines), (0, 1, 0))


class DriveStateTests(unittest.TestCase):
    """The companion's needs reach the provider as one bounded line and change
    the mock's suggestion — the Phase 2a acceptance test."""

    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-engine-drives-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.paths = BridgePaths(Path(temporary))
        self.paths.ensure()

    def turn(self, motives=None, mood=None):
        actor = {"sim_id": 1, "name": "케이트"}
        if motives is not None:
            actor["motives"] = motives
        if mood is not None:
            actor["mood"] = mood
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": uuid.uuid4().hex,
            "kind": "turn",
            "created_at": "2026-09-06T00:00:00+00:00",
            "player_message": "오늘 뭐 할까?",
            "session_id": "household-1",
            "actor": actor,
            "household": [{"sim_id": actor["sim_id"], "name": actor["name"]}, {"sim_id": 2, "name": "에이든"}],
            "roles": {"binding_id": "a" * 32, "scene_id": "b" * 32, "player_sim_id": actor["sim_id"], "partner_sim_id": 2},
        }
        atomic_write_json(self.paths.requests / f"{request['request_id']}.json", request)
        return request

    def test_drive_state_is_injected_only_when_the_snapshot_has_needs(self):
        spy = SpyProvider()
        engine = CompanionEngine(self.paths, spy)
        self.turn()
        engine.process_one()
        self.assertNotIn("drive_state", spy.requests[0])
        self.turn(motives={"hunger": -60, "energy": 80}, mood="Mood_Happy")
        engine.process_one()
        self.assertEqual(spy.requests[1]["drive_state"], "욕구: 배고픔 높음 | 기분: Mood_Happy")

    def test_a_hungry_companion_and_a_full_one_get_different_suggestions(self):
        spy = SpyProvider()
        engine = CompanionEngine(self.paths, spy)
        self.turn(motives={"hunger": -70, "fun": 60})
        hungry = engine.process_one()
        self.turn(motives={"hunger": 90, "fun": 60})
        full = engine.process_one()
        import json as json_module
        hungry_idea = json_module.loads(hungry.read_text(encoding="utf-8"))["action"]["arguments"]["idea"]
        full_idea = json_module.loads(full.read_text(encoding="utf-8"))["action"]["arguments"]["idea"]
        self.assertIn("먹자", hungry_idea)
        self.assertNotEqual(hungry_idea, full_idea)


if __name__ == "__main__":
    unittest.main()
