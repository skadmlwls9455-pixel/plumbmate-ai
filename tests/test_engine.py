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


class QuarantineBreadthTests(unittest.TestCase):
    """Deterministic input failures of ANY exception type must quarantine."""

    def setUp(self):
        temporary = tempfile.mkdtemp(prefix="plumbmate-engine-q-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, True))
        self.paths = BridgePaths(Path(temporary))
        self.paths.ensure()

    def test_wrong_typed_field_is_quarantined_not_looping(self):
        # household: 42 raises TypeError inside clamp_request — before the
        # fix that bypassed the ProtocolError-only catch and the mtime-ordered
        # queue retried the same file forever, starving everything behind it.
        engine = CompanionEngine(self.paths, MockProvider())
        broken = valid_request()
        broken["household"] = 42
        target = self.paths.requests / "{}.json".format(broken["request_id"])
        atomic_write_json(target, broken)
        os.utime(str(target), (os.path.getmtime(str(target)) - 60,) * 2)
        healthy = valid_request()
        atomic_write_json(self.paths.requests / "{}.json".format(healthy["request_id"]), healthy)
        result = engine.process_one()
        self.assertIsNotNone(result, "the healthy request behind the poison never ran")
        self.assertTrue(
            (self.paths.archive / "{}.rejected.json".format(broken["request_id"])).exists()
        )

    def test_path_traversal_request_id_is_rejected(self):
        from plumbmate.protocol import ProtocolError, validate_request
        for hostile in ("../../evil", "a/b", "a\\b", "", "x" * 65, 7):
            request = valid_request()
            request["request_id"] = hostile
            with self.assertRaises(ProtocolError, msg=repr(hostile)):
                validate_request(request)

    def test_corrupt_session_file_resets_instead_of_poisoning_the_session(self):
        engine = CompanionEngine(self.paths, MockProvider())
        request = valid_request(session_id="corrupt-session")
        session_path = engine._session_path("corrupt-session")
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("{broken json", encoding="utf-8")
        atomic_write_json(self.paths.requests / "{}.json".format(request["request_id"]), request)
        result = engine.process_one()
        self.assertIsNotNone(result, "a corrupt session file blocked the turn")
        # The session file is rewritten cleanly for the next turn.
        import json as json_module
        rewritten = json_module.loads(session_path.read_text(encoding="utf-8"))
        self.assertIn("memory_facts", rewritten)


class TrustBoundaryTests(unittest.TestCase):
    """Bounds that hold even for hand-edited bridge files and hostile saves."""

    def test_unbounded_names_are_clamped_before_model_context(self):
        from plumbmate.budget import clamp_request
        request = valid_request()
        request["actor"]["name"] = "가" * 5000
        request["household"] = [{"sim_id": 1, "name": "나" * 5000}]
        clamped = clamp_request(request)
        self.assertEqual(len(clamped["actor"]["name"]), 120)
        self.assertEqual(len(clamped["household"][0]["name"]), 120)

    def test_oversized_or_nonstring_response_fields_are_rejected(self):
        from plumbmate.protocol import ProtocolError, validate_response
        from plumbmate.providers import MockProvider
        base = MockProvider().respond(valid_request())
        for mutate in (
            lambda r: r.update(partner_message="x" * 2001),
            lambda r: r.update(partner_message=42),
            lambda r: r.update(memory_patch=["y" * 501]),
            lambda r: r.update(memory_patch=[{"not": "a string"}]),
        ):
            broken = dict(base)
            mutate(broken)
            with self.assertRaises(ProtocolError):
                validate_response(broken)
        validate_response(base)


if __name__ == "__main__":
    unittest.main()
