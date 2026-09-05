import importlib.util
import socket
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/socket_probe.py"
spec = importlib.util.spec_from_file_location("socket_probe_under_test", source)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class BrokenSocketModule(object):
    """Stands in for a runtime where the extension loaded but is unusable."""

    AF_INET = socket.AF_INET
    SOCK_STREAM = socket.SOCK_STREAM
    SOL_SOCKET = socket.SOL_SOCKET
    SO_ERROR = socket.SO_ERROR

    def socket(self, *_args):
        raise OSError(97, "Address family not supported by protocol")


class ModuleReportTests(unittest.TestCase):
    def test_every_module_the_bridge_needs_is_reported(self):
        report = probe.probe_modules()
        for name in probe.REQUIRED_MODULES:
            with self.subTest(module=name):
                self.assertTrue(report[name]["required_by_bridge"])
                self.assertIn(report[name]["result"], ("IMPORTED", "IMPORT_FAILED"))

    def test_bridge_client_imports_are_covered_by_the_probe(self):
        client_source = (
            Path(__file__).resolve().parents[1]
            / "game_mod/src/plumbmate_mod/bridge_client.py"
        ).read_text(encoding="utf-8")
        imported = {
            line.split()[1]
            for line in client_source.splitlines()
            if line.startswith("import ")
        }
        self.assertTrue(imported)
        self.assertTrue(imported.issubset(set(probe.REQUIRED_MODULES)))

    def test_optional_modules_are_recorded_as_not_required(self):
        report = probe.probe_modules()
        for name in probe.OPTIONAL_MODULES:
            with self.subTest(module=name):
                self.assertFalse(report[name]["required_by_bridge"])

    def test_a_missing_module_is_reported_not_raised(self):
        result = probe._import_report("plumbmate_definitely_not_a_module")
        self.assertEqual(result["result"], "IMPORT_FAILED")
        self.assertIn("error", result)


class LoopbackTests(unittest.TestCase):
    def test_round_trip_succeeds_on_a_working_runtime(self):
        result = probe.probe_loopback(socket)
        self.assertEqual(result["result"], "ROUND_TRIP_OK", result)
        self.assertIsInstance(result["port"], int)
        self.assertGreater(result["port"], 0)
        for stage in ("listener_created", "bound", "listening", "accepted",
                      "connected", "sent", "received"):
            self.assertIn(stage, result["stages"])

    def test_round_trip_binds_loopback_only(self):
        captured = []
        real_bind = socket.socket.bind

        def recording_bind(self_socket, address):
            captured.append(address)
            return real_bind(self_socket, address)

        socket.socket.bind = recording_bind
        self.addCleanup(setattr, socket.socket, "bind", real_bind)
        probe.probe_loopback(socket)
        self.assertEqual(captured, [("127.0.0.1", 0)])

    def test_an_unusable_socket_module_is_reported_not_raised(self):
        result = probe.probe_loopback(BrokenSocketModule())
        self.assertEqual(result["result"], "FAILED")
        self.assertEqual(result["error_type"], "OSError")
        self.assertEqual(result["stages"], [])

    def test_a_spin_budget_of_zero_reports_timeout_not_failure(self):
        original = probe.MAX_SPINS
        probe.MAX_SPINS = 0
        self.addCleanup(setattr, probe, "MAX_SPINS", original)
        result = probe.probe_loopback(socket)
        self.assertEqual(result["result"], "TIMEOUT")
        self.assertIn("listening", result["stages"])

    def test_sockets_are_closed_even_when_the_probe_times_out(self):
        opened = []
        real_socket = socket.socket

        class Tracking(real_socket):
            def __init__(self, *args, **kwargs):
                real_socket.__init__(self, *args, **kwargs)
                opened.append(self)

        module = type("M", (), {k: getattr(socket, k) for k in
                                ("AF_INET", "SOCK_STREAM", "SOL_SOCKET", "SO_ERROR")})()
        module.socket = Tracking
        original = probe.MAX_SPINS
        probe.MAX_SPINS = 0
        self.addCleanup(setattr, probe, "MAX_SPINS", original)
        probe.probe_loopback(module)
        self.assertTrue(opened)
        for connection in opened:
            self.assertEqual(connection.fileno(), -1)


class VerdictTests(unittest.TestCase):
    def test_working_runtime_reports_available(self):
        payload = probe.probe_socket_capability()
        self.assertEqual(payload["verdict"], "AVAILABLE", payload)
        self.assertEqual(payload["missing_required"], [])
        self.assertIn("available", probe.summary_line(payload))
        self.assertIn("loopback round trip ok", probe.summary_line(payload))

    def test_missing_extension_reports_unavailable_with_the_reason(self):
        original = probe.REQUIRED_MODULES
        probe.REQUIRED_MODULES = original + ("plumbmate_missing_extension",)
        self.addCleanup(setattr, probe, "REQUIRED_MODULES", original)
        payload = probe.probe_socket_capability()
        self.assertEqual(payload["verdict"], "IMPORT_FAILED")
        self.assertEqual(payload["missing_required"], ["plumbmate_missing_extension"])
        self.assertEqual(payload["loopback"]["result"], "NOT_RUN")
        line = probe.summary_line(payload)
        self.assertIn("unavailable", line)
        self.assertIn("plumbmate_missing_extension", line)

    def test_summary_line_survives_an_empty_payload(self):
        self.assertIn("unavailable", probe.summary_line({}))


if __name__ == "__main__":
    unittest.main()
