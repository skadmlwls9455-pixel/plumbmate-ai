import tempfile
import unittest
from pathlib import Path

from plumbmate.cli import make_demo_request
from plumbmate.engine import CompanionEngine
from plumbmate.protocol import BridgePaths, ProtocolError, MAX_JSON_BYTES, atomic_write_json, read_json, validate_response
from plumbmate.providers import MockProvider


class RecordingProvider:
    def __init__(self):
        self.requests = []
        self.mock = MockProvider()

    def respond(self, request):
        self.requests.append(request)
        return self.mock.respond(request)


class ProtocolTests(unittest.TestCase):
    def test_mock_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            request = make_demo_request("산책할까?")
            request_path = paths.requests / f"{request['request_id']}.json"
            atomic_write_json(request_path, request)
            result = CompanionEngine(paths, MockProvider()).process_one()
            self.assertIsNotNone(result)
            response = read_json(result)
            self.assertEqual(response["request_id"], request["request_id"])
            self.assertEqual(response["action"]["name"], "suggest_only")
            self.assertFalse(request_path.exists())

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ProtocolError):
            validate_response({
                "protocol_version": "0.1",
                "request_id": "1" * 32,
                "partner_message": "no",
                "action": {
                    "name": "run_arbitrary_code",
                    "arguments": {"target_sim_id": None, "interaction": None, "idea": None},
                    "reason": "no",
                },
                "memory_patch": [],
            })

    def test_unverified_session_never_restores_memory_or_omits_current_state(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            provider = RecordingProvider()
            engine = CompanionEngine(paths, provider)

            first = make_demo_request("첫 데이트를 기억해줘")
            atomic_write_json(paths.requests / f"{first['request_id']}.json", first)
            engine.process_one()

            second = make_demo_request("다음엔 뭘 할까?")
            second["session_id"] = first["session_id"]
            atomic_write_json(paths.requests / f"{second['request_id']}.json", second)
            engine.process_one()

            sent = provider.requests[1]
            self.assertEqual(sent["state_delta"], {})
            self.assertEqual(sent["household"], second["household"])
            self.assertEqual(sent["memory_summary"], "")
            self.assertEqual(list(paths.state.glob("session-*.json")), [])
            response = read_json(paths.responses / f"{second['request_id']}.json")
            self.assertEqual(response["memory_patch"], [])

    def test_invalid_id_never_becomes_an_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            provider = RecordingProvider()
            for value in ("../escape", "/tmp/escape", 123, "a" * 500):
                request = dict(make_demo_request("test"), request_id=value)
                atomic_write_json(paths.requests / ("a" * 32 + ".json"), request)
                self.assertIsNone(CompanionEngine(paths, provider).process_one())
            self.assertEqual(provider.requests, [])
            self.assertEqual(list(paths.responses.iterdir()), [])
            self.assertEqual(len(list((paths.archive / "rejected").iterdir())), 4)

    def test_poison_request_does_not_block_the_next_valid_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            (paths.requests / "bad.json").write_text("{", encoding="utf-8")
            request = make_demo_request("test")
            atomic_write_json(paths.requests / f"{request['request_id']}.json", request)
            engine = CompanionEngine(paths, MockProvider())
            self.assertIsNotNone(engine.process_one())
            self.assertEqual(engine.rejected_requests, 1)

    def test_response_mismatch_is_quarantined_without_retry(self):
        class Mismatch(RecordingProvider):
            def respond(self, request):
                return dict(super().respond(request), request_id="0" * 32)
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            request = make_demo_request("test")
            atomic_write_json(paths.requests / f"{request['request_id']}.json", request)
            provider = Mismatch()
            engine = CompanionEngine(paths, provider)
            with self.assertRaises(ProtocolError):
                engine.process_one()
            self.assertIsNone(engine.process_one())
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(list(paths.responses.iterdir()), [])

    def test_duplicate_answered_id_does_not_call_provider_again(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            provider = RecordingProvider()
            engine = CompanionEngine(paths, provider)
            request = make_demo_request("test")
            path = paths.requests / f"{request['request_id']}.json"
            atomic_write_json(path, request)
            engine.process_one()
            atomic_write_json(path, request)
            self.assertIsNone(engine.process_one())
            self.assertEqual(len(provider.requests), 1)

    def test_oversized_json_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "huge.json"
            target.write_bytes(b" " * (MAX_JSON_BYTES + 1))
            with self.assertRaises(ProtocolError):
                read_json(target)
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(ProtocolError):
                read_json(link)
            with self.assertRaises(ProtocolError):
                atomic_write_json(link, {})
            self.assertEqual(target.stat().st_size, MAX_JSON_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
