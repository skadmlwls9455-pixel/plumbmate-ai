import importlib.util
import functools
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

    def close(self):
        pass


class ExplodingClient(object):
    def __init__(self):
        self.pumps = 0

    def pump(self):
        self.pumps += 1
        raise RuntimeError("engine must never see this")


class DriverTests(unittest.TestCase):
    def test_ui_lifecycle_runs_offline_but_not_after_stop(self):
        seen = []
        driver = driver_module.TickDriver(before_pump=lambda: seen.append("ui"),
                                          on_suspend=lambda: seen.append("close"))
        driver.on_tick()
        driver.suspend()
        driver.on_tick()
        self.assertEqual(seen, ["ui", "close"])

    def test_ui_failure_cannot_disable_or_delay_transport(self):
        def broken():
            raise ValueError("UI unavailable")
        client = RecordingClient()
        driver = driver_module.TickDriver(client, before_pump=broken, on_suspend=broken)
        for _ in range(20):
            self.assertTrue(driver.on_tick())
        self.assertEqual(client.pumps, 20)
        self.assertIsNone(driver.disabled_reason)
        self.assertEqual(driver.stats["errors"], 0)
        self.assertEqual(driver.last_ui_error, "ValueError")
        driver.suspend()
        self.assertFalse(driver.active)

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

    def test_suspend_blocks_binder_and_attach_until_explicit_resume(self):
        calls = []
        client = RecordingClient()
        driver = driver_module.TickDriver(binder=lambda current: calls.append(current))
        driver.suspend()
        driver.attach(client)
        for tick in range(20):
            self.assertFalse(driver.on_tick(tick))
        self.assertEqual(calls, [])
        self.assertEqual(client.pumps, 0)
        driver.resume()
        self.assertTrue(driver.on_tick(21))
        self.assertEqual(client.pumps, 1)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.original = FakeZone.update
        self.addCleanup(setattr, FakeZone, "update", self.original)
        self.addCleanup(driver_module.remove_tick_hook, FakeZone)
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

    def _foreign_wrapper(self, copy_attributes):
        original = FakeZone.update
        calls = []

        def foreign(zone, *args, **kwargs):
            calls.append(args)
            return original(zone, *args, **kwargs)

        if copy_attributes:
            foreign = functools.wraps(original)(foreign)
        FakeZone.update = foreign
        return foreign, calls

    def test_wraps_copied_markers_do_not_grant_unhook_ownership(self):
        driver_module.install_tick_hook(FakeZone, self.driver)
        foreign, calls = self._foreign_wrapper(True)
        self.assertTrue(getattr(foreign, driver_module.HOOK_MARKER))
        self.assertFalse(driver_module.remove_tick_hook(FakeZone))
        self.assertIs(FakeZone.update, foreign)
        self.assertFalse(driver_module.is_hooked(FakeZone))
        self.assertEqual(self.zone.update(1), "engine-result")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.client.pumps, 0)

    def test_restart_under_an_unmarked_wrapper_pumps_only_once(self):
        driver_module.install_tick_hook(FakeZone, self.driver)
        foreign, calls = self._foreign_wrapper(False)
        self.assertFalse(driver_module.remove_tick_hook(FakeZone))
        self.assertTrue(driver_module.install_tick_hook(FakeZone, self.driver))
        self.zone.update(1)
        self.assertEqual(self.client.pumps, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(driver_module.remove_tick_hook(FakeZone))
        self.assertIs(FakeZone.update, foreign)
        self.zone.update(2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.client.pumps, 1)

    def test_reload_reuses_exact_wrapper_with_new_driver(self):
        driver_module.install_tick_hook(FakeZone, self.driver)
        original_wrapper = FakeZone.update
        reloaded = _load("tick_driver_reloaded", "tick_driver.py")
        client = RecordingClient()
        self.assertFalse(reloaded.install_tick_hook(FakeZone, reloaded.TickDriver(client)))
        self.assertIs(FakeZone.update, original_wrapper)
        self.zone.update(1)
        self.assertEqual(self.client.pumps, 0)
        self.assertEqual(client.pumps, 1)
        self.assertTrue(reloaded.remove_tick_hook(FakeZone))
        self.assertIs(FakeZone.update, self.original)

    def test_uncaught_driver_failure_does_not_escape_wrapper(self):
        def broken_tick(_tick):
            raise RuntimeError("broken driver")
        self.driver.on_tick = broken_tick
        driver_module.install_tick_hook(FakeZone, self.driver)
        self.assertEqual(self.zone.update(1), "engine-result")
        self.assertFalse(driver_module.is_hooked(FakeZone))


class FakeClock(object):
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class BinderTests(unittest.TestCase):
    """The binder is what lets the hook be installed before anything listens."""

    def setUp(self):
        self.clock = FakeClock()

    def driver(self, binder, interval=5.0):
        return driver_module.TickDriver(
            binder=binder, bind_interval_seconds=interval, now=self.clock,
        )

    def test_failing_clock_is_contained_like_a_failing_binder(self):
        def broken_clock():
            raise RuntimeError("clock failure")
        driver = driver_module.TickDriver(binder=lambda current: None, now=broken_clock)
        self.assertFalse(driver.on_tick(1))
        self.assertEqual(driver.stats["bind_errors"], 1)

    def test_driver_stays_idle_while_the_binder_supplies_nothing(self):
        driver = self.driver(lambda current: None)
        for tick in range(100):
            self.clock.advance(1.0)
            self.assertFalse(driver.on_tick(tick))
        self.assertIsNone(driver.client)
        self.assertFalse(driver.active)
        self.assertEqual(driver.stats["pumps"], 0)
        self.assertEqual(driver.stats["binds"], 0)

    def test_a_late_client_is_picked_up_and_pumped(self):
        supply = []

        def binder(current):
            return supply[0] if supply else None

        driver = self.driver(binder)
        self.clock.advance(10.0)
        driver.on_tick(0)
        self.assertIsNone(driver.client)
        client = RecordingClient()
        supply.append(client)
        self.clock.advance(10.0)
        self.assertTrue(driver.on_tick(1))
        self.assertIs(driver.client, client)
        self.assertEqual(client.pumps, 1)
        self.assertEqual(driver.stats["binds"], 1)

    def test_binder_is_throttled_by_time_not_by_ticks(self):
        calls = []

        def binder(current):
            calls.append(self.clock.value)
            return None

        driver = self.driver(binder, interval=5.0)
        for _ in range(1000):
            self.clock.advance(0.001)
            driver.on_tick(0)
        self.assertEqual(len(calls), 1)
        self.clock.advance(5.0)
        driver.on_tick(0)
        self.assertEqual(len(calls), 2)

    def test_binder_sees_the_current_client_and_can_replace_it(self):
        first, second = RecordingClient(), RecordingClient()
        seen = []

        def binder(current):
            seen.append(current)
            return second if current is first else first

        driver = self.driver(binder)
        driver.on_tick(0)
        self.assertIs(driver.client, first)
        self.clock.advance(10.0)
        driver.on_tick(1)
        self.assertIs(driver.client, second)
        self.assertEqual(seen, [None, first])

    def test_returning_the_same_client_is_not_counted_as_a_rebind(self):
        client = RecordingClient()
        driver = self.driver(lambda current: client)
        driver.on_tick(0)
        self.clock.advance(10.0)
        driver.on_tick(1)
        self.assertEqual(driver.stats["binds"], 1)
        self.assertEqual(client.pumps, 2)

    def test_an_exploding_binder_never_reaches_the_engine(self):
        def binder(current):
            raise RuntimeError("companion state is unreadable")

        driver = self.driver(binder)
        driver_module.install_tick_hook(FakeZone, driver, "update")
        self.addCleanup(driver_module.remove_tick_hook, FakeZone, "update")
        zone = FakeZone()
        self.clock.advance(10.0)
        self.assertEqual(zone.update(7), "engine-result")
        self.assertEqual(driver.stats["bind_errors"], 1)
        self.assertIn("companion state is unreadable", driver.last_bind_error)

    def test_a_rebind_revives_a_driver_disabled_by_pump_failures(self):
        supply = [ExplodingClient()]
        driver = self.driver(lambda current: supply[0] if supply[0] is not current else None)
        driver.on_tick(0)
        for tick in range(driver_module.DEFAULT_MAX_CONSECUTIVE_ERRORS):
            driver.on_tick(tick)
        self.assertIsNotNone(driver.disabled_reason)
        supply[0] = RecordingClient()
        self.clock.advance(10.0)
        self.assertTrue(driver.on_tick(99))
        self.assertIsNone(driver.disabled_reason)
        self.assertTrue(driver.active)


class TickedInteropTests(unittest.TestCase):
    """Fake Zone ticks a real client against the real companion server."""

    def setUp(self):
        self.original = FakeZone.update
        self.addCleanup(setattr, FakeZone, "update", self.original)
        self.server = EventSocketServer(TOKEN, idle_timeout=60.0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = bridge.BridgeClient(
            TOKEN, self.server.port, connect_timeout=30.0, handshake_timeout=30.0,
        )
        self.addCleanup(self.client.close)
        self.driver = driver_module.TickDriver(self.client)
        driver_module.install_tick_hook(FakeZone, self.driver, "update")
        self.zone = FakeZone()

    def tick_until(self, predicate, timeout=20.0):
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
        event = self.server.poll(timeout=15.0)
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
        replacement = EventSocketServer(TOKEN, port=port, idle_timeout=60.0)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.assertTrue(
            self.tick_until(lambda: self.client.stats["acked_events"] == 1, timeout=25.0)
        )
        self.assertEqual(replacement.poll(timeout=15.0).kind, "save_committed")


if __name__ == "__main__":
    unittest.main()
