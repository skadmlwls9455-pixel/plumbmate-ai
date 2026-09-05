import tempfile
import unittest
from pathlib import Path

from plumbmate.cli import make_demo_request
from plumbmate.engine import CompanionEngine
from plumbmate.protocol import BridgePaths, ProtocolError, atomic_write_json, read_json, validate_response
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
                "request_id": "12345678",
                "partner_message": "no",
                "action": {
                    "name": "run_arbitrary_code",
                    "arguments": {"target_sim_id": None, "interaction": None, "idea": None},
                    "reason": "no",
                },
                "memory_patch": [],
            })

    def test_second_turn_uses_delta_and_local_memory(self):
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
            self.assertEqual(sent["household"], [])
            self.assertIn("첫 데이트를 기억해줘", sent["memory_summary"])


if __name__ == "__main__":
    unittest.main()
