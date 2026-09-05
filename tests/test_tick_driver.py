import importlib.util
import time
import unittest
from pathlib import Path

from plumbmate.event_socket import MIN_TOKEN_CHARACTERS, EventSocketServer

root = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, root / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver_module = _load("tick_driver_under_test", "tick_driver.py")
bridge = _load("bridge_client_for_tick_test", "bridge_client.py")

TOKEN = "t" * MIN_TOKEN_CHARACTERS


class FakeZone(object):
    """Mirrors the real signature: Zone.update(self, absolute_ticks)."""

    def __init__(self):
        self.seen = []

    def update(self, absolute_ticks):
        self.seen.append(absolute_ticks)
        return "engine-result"


class RecordingClient(object):
    def __init__(self, result="READY"):
        self.pumps = 0
        self.result = result

    def pump(self):
        self.pumps += 1
        return self.result


class ExplodingClient(object):
    def __init__(self):
        self.pumps = 0

    def pump(self):
        self.pumps += 1
        raise RuntimeError("engine must never see this")


class DriverTests(unittest.TestCase):
    def test_idle_driver_does_not_pump(self):
        driver = driver_module.TickDriver()
        self.assertFalse(driver.active)
        self.assertFalse(driver.on_tick(1))
        self.assertEqual(driver.stats["ticks"], 1)
        self.assertEqual(driver.stats["pumps"], 0)
        self.assertEqual(driver.stats["skipped"], 1)

    def test_every_tick_pumps_once_per_tick(self):
        client = RecordingClient()
        driver = driver_module.TickDriver(client)
        for tick in range(5):
            self.assertTrue(driver.on_tick(tick))
        self.assertEqual(client.pumps, 5)
        self.assertEqual(driver.stats["pumps"], 5)
        self.assertEqual(driver.last_state, "READY")

    def test_throttle_pumps_every_nth_tick(self):
        client = RecordingClient()
        driver = driver_module.TickDriver(client, every_n_ticks=3)
        results = [driver.on_tick(tick) for tick in range(9)]
        self.assertEqual(results, [False, False, True] * 3)
        self.assertEqual(client.pumps, 3)

    def test_throttle_below_one_is_refused(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    driver_module.TickDriver(RecordingClient(), every_n_ticks=value)

    def test_raising_client_never_reaches_the_caller(self):
        client = ExplodingClient()
        driver = driver_module.TickDriver(client, max_consecutive_errors=3)
        self.assertFalse(driver.on_tick(1))
        self.assertEqual(driver.stats["errors"], 1)
        self.assertIn("RuntimeError", driver.last_error)
        self.assertTrue(driver.active)

    def test_repeated_failures_disable_the_driver_instead_of_burning_ticks(self):
        client = ExplodingClient()
        driver = driver_module.TickDriver(client, max_consecutive_errors=3)
        for tick in range(20):
            driver.on_tick(tick)
        self.assertFalse(driver.active)
        self.assertEqual(client.pumps, 3)
        self.assertEqual(driver.disabled_reason, "pump_failed_3_times")

    def test_resume_and_attach_clear_a_disable(self):
        driver = driver_module.TickDriver(ExplodingClient(), max_consecutive_errors=1)
        driver.on_tick(1)
        self.assertFalse(driver.active)
        driver.resume()
        self.assertTrue(driver.active)
        driver.on_tick(2)
        self.assertFalse(driver.active)
        driver.attach(RecordingClient())
        self.assertTrue(driver.active)
        self.assertIsNone(driver.disabled_reason)

    def test_a_recovered_pump_resets_the_error_streak(self):
        class Flaky(object):
            def __init__(self):
                self.calls = 0

            def pump(self):
                self.calls += 1
                if self.calls % 2:
                    raise RuntimeError("odd call")
                return "READY"

        driver = driver_module.TickDriver(Flaky(), max_consecutive_errors=2)
        for tick in range(10):
            driver.on_tick(tick)
        self.assertTrue(driver.active)
        self.assertEqual(driver.stats["pumps"], 5)

    def test_detach_returns_the_client_without_closing_it(self):
        client = RecordingClient()
        driver = driver_module.TickDriver(client)
        self.assertIs(driver.detach(), client)
        self.assertFalse(driver.active)
        self.assertFalse(driver.on_tick(1))
        self.assertEqual(client.pumps, 0)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.original = FakeZone.update
        self.addCleanup(setattr, FakeZone, "update", self.original)
        self.client = RecordingClient()
        self.driver = driver_module.TickDriver(self.client)
        self.zone = FakeZone()

    def test_hook_runs_the_original_first_and_preserves_its_result(self):
        driver_module.install_tick_hook(FakeZone, self.driver, "update")
        self.assertEqual(self.zone.update(41), "engine-result")
        self.assertEqual(self.zone.seen, [41])
        self.assertEqual(self.client.pumps, 1)

    def test_absolute_ticks_reaches_the_driver(self):
        seen = []
        self.driver.on_tick = seen.append
        driver_module.install_tick_hook(FakeZone, self.driver, "update")
        for tick in (7, 8, 9):
            self.zone.update(tick)
        self.assertEqual(seen, [7, 8, 9])

    def test_install_is_idempotent(self):
        self.assertTrue(driver_module.install_tick_hook(FakeZone, self.driver, "update"))
        wrapper = FakeZone.update
        self.assertFalse(driver_module.install_tick_hook(FakeZone, self.driver, "update"))
        self.assertIs(FakeZone.update, wrapper)
        self.zone.update(1)
        self.assertEqual(self.client.pumps, 1)

    def test_remove_restores_the_exact_original(self):
        driver_module.install_tick_hook(FakeZone, self.driver, "update")
        self.assertTrue(driver_module.is_hooked(FakeZone, "update"))
        self.assertTrue(driver_module.remove_tick_hook(FakeZone, "update"))
        self.assertIs(FakeZone.update, self.original)
        self.assertFalse(driver_module.is_hooked(FakeZone, "update"))
        self.zone.update(1)
        self.assertEqual(self.client.pumps, 0)

    def test_remove_without_a_hook_reports_false(self):
        self.assertFalse(driver_module.remove_tick_hook(FakeZone, "update"))

    def test_missing_method_is_reported(self):
        with self.assertRaises(AttributeError):
            driver_module.install_tick_hook(FakeZone, self.driver, "no_such_method")

    def test_a_failing_original_is_not_masked_and_skips_the_pump(self):
        class BrokenZone(object):
            def update(self, absolute_ticks):
                raise ValueError("engine failure")

        driver_module.install_tick_hook(BrokenZone, self.driver, "update")
        with self.assertRaises(ValueError):
            BrokenZone().update(1)
        self.assertEqual(self.client.pumps, 0)

    def test_an_exploding_driver_does_not_break_the_tick(self):
        driver = driver_module.TickDriver(ExplodingClient())
        driver_module.install_tick_hook(FakeZone, driver, "update")
        self.assertEqual(self.zone.update(3), "engine-result")
        self.assertEqual(self.zone.seen, [3])


class TickedInteropTests(unittest.TestCase):
    """Fake Zone ticks a real client against the real companion server."""

    def setUp(self):
        self.original = FakeZone.update
        self.addCleanup(setattr, FakeZone, "update", self.original)
        self.server = EventSocketServer(TOKEN, idle_timeout=5.0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = bridge.BridgeClient(TOKEN, self.server.port)
        self.addCleanup(self.client.close)
        self.driver = driver_module.TickDriver(self.client)
        driver_module.install_tick_hook(FakeZone, self.driver, "update")
        self.zone = FakeZone()

    def tick_until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        tick = 0
        while time.monotonic() < deadline:
            self.zone.update(tick)
            tick += 1
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_ticks_alone_complete_the_handshake(self):
        self.assertTrue(self.tick_until(lambda: self.client.connected),
                        "never connected: {}".format(self.client.last_error))
        self.assertEqual(self.client.stats["handshakes"], 1)
        self.assertGreater(self.driver.stats["pumps"], 0)

    def test_ticks_deliver_a_queued_event_end_to_end(self):
        self.assertTrue(self.tick_until(lambda: self.client.connected))
        event_id = self.client.queue_event("zone_loaded", {"absolute_sim_ticks": 12345})
        self.assertTrue(self.tick_until(lambda: self.client.stats["acked_events"] == 1))
        event = self.server.poll(timeout=2.0)
        self.assertEqual(event.event_id, event_id)
        self.assertEqual(event.payload, {"absolute_sim_ticks": 12345})

    def test_removing_the_hook_stops_all_bridge_work(self):
        self.assertTrue(self.tick_until(lambda: self.client.connected))
        driver_module.remove_tick_hook(FakeZone, "update")
        pumps = self.driver.stats["pumps"]
        ticks_before = len(self.zone.seen)
        for tick in range(50):
            self.zone.update(tick)
        # The engine still ticks; only the bridge work stops.
        self.assertEqual(self.driver.stats["pumps"], pumps)
        self.assertEqual(len(self.zone.seen) - ticks_before, 50)

    def test_companion_restart_recovers_through_ticks_alone(self):
        self.assertTrue(self.tick_until(lambda: self.client.connected))
        port = self.server.port
        self.server.stop()
        self.assertTrue(self.tick_until(lambda: self.client.state == "BACKOFF"))
        self.client.queue_event("save_committed", {"slot_id": "Slot_00000002"})
        replacement = EventSocketServer(TOKEN, port=port, idle_timeout=5.0)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.assertTrue(
            self.tick_until(lambda: self.client.stats["acked_events"] == 1, timeout=8.0)
        )
        self.assertEqual(replacement.poll(timeout=2.0).kind, "save_committed")


if __name__ == "__main__":
    unittest.main()
