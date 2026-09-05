"""The engine must survive any single request: quarantine, retry, continue.

A request the protocol rejects can only fail the same way again, so it is
quarantined immediately. A provider failure might be transient, so it is
retried — the exception propagates so the serve loop owns the pacing — and
after ``max_attempts`` the request is quarantined too. Either way the next
request still gets processed; one poisonous file must never pin the queue.
"""

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from plumbmate.engine import CompanionEngine
from plumbmate.protocol import PROTOCOL_VERSION, BridgePaths, atomic_write_json
from plumbmate.providers import MockProvider


def valid_request(request_id=None, session_id="engine-test"):
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id or uuid.uuid4().hex,
        "kind": "turn",
        "player_message": "오늘 뭐 할까?",
        "session_id": session_id,
        "actor": {"sim_id": 2, "name": "에이든"},
        "household": [{"sim_id": 1, "name": "플레이어 심"}],
        "recent_events": [],
        "memory_summary": "",
    }


class FlakyProvider(object):
    """Fails the first ``failures`` calls, then delegates to the mock."""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self._inner = MockProvider()

    def respond(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("provider unavailable")
        return self._inner.respond(request)


class EngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-engine-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.paths = BridgePaths(Path(temporary))
        self.paths.ensure()

    def write_request(self, payload):
        target = self.paths.requests / "{}.json".format(payload["request_id"])
        atomic_write_json(target, payload)
        return target

    def write_poison(self, name="00-poison.json", text="{this is not json"):
        target = self.paths.requests / name
        target.write_text(text, encoding="utf-8")
        # Age it so mtime ordering picks the poison first, deterministically.
        stamp = os.path.getmtime(str(target)) - 60
        os.utime(str(target), (stamp, stamp))
        return target

    def test_success_archives_the_request_and_writes_a_response(self):
        engine = CompanionEngine(self.paths, MockProvider())
        request = valid_request()
        self.write_request(request)
        result = engine.process_one()
        self.assertIsNotNone(result)
        self.assertTrue(result.exists())
        self.assertEqual(list(self.paths.requests.glob("*.json")), [])
        self.assertTrue((self.paths.archive / "{}.json".format(request["request_id"])).exists())
        self.assertEqual(engine.drain_notices(), [])

    def test_poison_json_is_quarantined_and_the_next_request_still_runs(self):
        engine = CompanionEngine(self.paths, MockProvider())
        self.write_poison()
        request = valid_request()
        self.write_request(request)
        result = engine.process_one()
        self.assertIsNotNone(result, "the valid request behind the poison was not processed")
        self.assertTrue((self.paths.archive / "00-poison.rejected.json").exists())
        self.assertTrue((self.paths.archive / "00-poison.rejected.reason.txt").exists())
        notices = engine.drain_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("rejected", notices[0])
        self.assertEqual(engine.drain_notices(), [], "notices must be drained once")

    def test_schema_invalid_request_is_quarantined(self):
        engine = CompanionEngine(self.paths, MockProvider())
        broken = valid_request()
        del broken["actor"]
        self.write_request(broken)
        self.assertIsNone(engine.process_one())
        quarantined = list(self.paths.archive.glob("*.rejected.json"))
        self.assertEqual(len(quarantined), 1)

    def test_transient_provider_failure_retries_and_then_succeeds(self):
        provider = FlakyProvider(failures=2)
        engine = CompanionEngine(self.paths, provider, max_attempts=5)
        self.write_request(valid_request())
        with self.assertRaises(RuntimeError):
            engine.process_one()
        with self.assertRaises(RuntimeError):
            engine.process_one()
        result = engine.process_one()
        self.assertIsNotNone(result)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(list(self.paths.archive.glob("*.failed.json")), [])

    def test_permanent_provider_failure_is_quarantined_after_max_attempts(self):
        engine = CompanionEngine(self.paths, FlakyProvider(failures=99), max_attempts=3)
        request = valid_request()
        self.write_request(request)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                engine.process_one()
        # The third attempt hits the cap: quarantine instead of raising.
        self.assertIsNone(engine.process_one())
        failed = self.paths.archive / "{}.failed.json".format(request["request_id"])
        self.assertTrue(failed.exists())
        reason = (self.paths.archive / "{}.failed.reason.txt".format(request["request_id"]))
        self.assertIn("RuntimeError", reason.read_text(encoding="utf-8"))
        notices = engine.drain_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("failed", notices[0])
        self.assertEqual(list(self.paths.requests.glob("*.json")), [])

    def test_a_failing_request_does_not_block_a_later_one_after_quarantine(self):
        engine = CompanionEngine(self.paths, FlakyProvider(failures=2), max_attempts=2)
        first = valid_request()
        self.write_request(first)
        with self.assertRaises(RuntimeError):
            engine.process_one()
        second = valid_request()
        self.write_request(second)
        # Attempt 2 quarantines the first request, then the provider has
        # recovered and the second request processes in the same call.
        result = engine.process_one()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "{}.json".format(second["request_id"]))
        self.assertTrue(
            (self.paths.archive / "{}.failed.json".format(first["request_id"])).exists()
        )


if __name__ == "__main__":
    unittest.main()
