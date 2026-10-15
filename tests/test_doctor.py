import json
import errno
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plumbmate.doctor import inspect_bridge
from plumbmate.event_socket import EventSocketServer, write_token, write_endpoint
from plumbmate.protocol import BridgePaths


class DoctorTests(unittest.TestCase):
    def test_missing_install_does_not_create_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory) / "missing")
            result = inspect_bridge(paths)
            self.assertFalse(result["ok"])
            self.assertFalse(paths.root.exists())
            self.assertEqual(result["api_calls"], 0)

    def test_authenticated_check_does_not_emit_events_or_expose_token(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            token = write_token(paths)
            server = EventSocketServer(token, port=0)
            server.start()
            self.addCleanup(server.stop)
            write_endpoint(paths, "127.0.0.1", server.port)
            with mock.patch("plumbmate.providers.post_openai") as api:
                report = inspect_bridge(paths, connect=True)
                api.assert_not_called()
            self.assertTrue(report["ok"])
            self.assertIsNone(server.poll(timeout=0.01))
            self.assertNotIn(token, json.dumps(report))
            self.assertNotIn(directory, json.dumps(report))

    def test_refused_connection_has_actionable_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = BridgePaths(Path(directory))
            paths.ensure()
            write_token(paths)
            with mock.patch("plumbmate.doctor.BridgeClient.connect", side_effect=ConnectionRefusedError(errno.ECONNREFUSED, "private path")):
                report = inspect_bridge(paths, connect=True)
            self.assertFalse(report["ok"])
            self.assertEqual(report["checks"][-1]["remedy"], "start_companion")
            self.assertNotIn("private path", json.dumps(report))
