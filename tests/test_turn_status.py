import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from plumbmate.cli import main
from plumbmate.protocol import BridgePaths, ProtocolError
from plumbmate.turn_status import write_turn_status

SOURCE = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/turn_bridge.py"
spec = importlib.util.spec_from_file_location("turn_bridge_status_test", SOURCE)
game_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_bridge)


class StatusTests(unittest.TestCase):
    def test_receipts_are_readable_by_game_without_companion_import(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            for reason in ("invalid_request", "provider_failed", "session_limit"):
                self.assertTrue(write_turn_status(paths, "a" * 32, "failed", reason))
                result = game_bridge.read_status(directory, "a" * 32)
                self.assertEqual(result["reason_code"], reason)
            self.assertIsNone(game_bridge.read_status(directory, "b" * 32))

    def test_invalid_id_or_bad_receipt_cannot_be_used_as_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            self.assertFalse(write_turn_status(paths, "../escape", "failed", "invalid_request"))
            with self.assertRaises(ValueError):
                game_bridge.read_status(directory, "../escape")
            paths.ensure()
            path = paths.state / ("turn-" + "a" * 32 + ".json")
            path.write_text(json.dumps({"request_id": "b" * 32, "phase": "failed", "reason_code": "provider_failed"}))
            with self.assertRaises(ValueError):
                game_bridge.read_status(directory, "a" * 32)

    def test_receipt_disk_failure_is_best_effort(self):
        with mock.patch("plumbmate.turn_status.atomic_write_json", side_effect=OSError("disk full")):
            self.assertFalse(write_turn_status(BridgePaths(Path("unused")), "a" * 32, "processing"))

    def test_missing_provider_configuration_is_an_actionable_cli_error(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output, \
                mock.patch("plumbmate.cli.create_provider", side_effect=ProtocolError("Configure the provider key")):
            self.assertEqual(main(["serve-once", "--bridge", directory]), 2)
            self.assertIn("Configure the provider key", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_invalid_session_limit_is_rejected_before_provider_call(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), \
                mock.patch("plumbmate.cli.create_provider") as provider:
            self.assertEqual(main(["serve-once", "--bridge", directory, "--max-turns", "0"]), 2)
            provider.return_value.respond.assert_not_called()

    def test_serve_once_failure_returns_nonzero(self):
        from plumbmate.cli import make_demo_request
        from plumbmate.protocol import atomic_write_json
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), \
                mock.patch("plumbmate.cli.create_provider") as provider:
            request = make_demo_request("test")
            atomic_write_json(Path(directory) / "requests" / (request["request_id"] + ".json"), request)
            provider.return_value.respond.side_effect = TimeoutError("uncertain")
            self.assertEqual(main(["serve-once", "--bridge", directory]), 2)
            provider.return_value.respond.assert_called_once()
