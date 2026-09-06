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


if __name__ == "__main__":
    unittest.main()
