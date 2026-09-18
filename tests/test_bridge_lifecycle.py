"""Lifetime contracts without EA modules, network access or configuration files."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


def load(name):
    path = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_lifetime_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BridgeLifecycle = load("bridge_lifecycle").BridgeLifecycle
TickDriver = load("tick_driver").TickDriver


class BridgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.driver = TickDriver(bind_interval_seconds=0)
        self.client = mock.Mock(connected=False)
        self.endpoint = ("127.0.0.1", 47651)
        self.loader = mock.Mock(return_value=(self.client, self.endpoint))
        self.probe = mock.Mock(return_value=(True, "available"))
        self.disabled = mock.Mock(return_value=False)
        self.install, self.remove = mock.Mock(), mock.Mock(return_value=True)
        self.lifecycle = BridgeLifecycle(self.driver, self.loader, self.probe,
                                         self.disabled, self.install, self.remove)

    def test_disable_file_prevents_probe_load_and_hook(self):
        self.disabled.return_value = True
        self.assertFalse(self.lifecycle.autostart())
        self.assertEqual(self.lifecycle.state, "disabled_by_file")
        for operation in (self.probe, self.loader, self.install):
            operation.assert_not_called()

    def test_unavailable_runtime_does_not_load_configuration(self):
        self.probe.return_value = (False, "missing socket")
        self.assertFalse(self.lifecycle.autostart())
        self.assertEqual(self.lifecycle.state, "unavailable: missing socket")
        self.assertEqual(self.lifecycle.start(), ("unavailable", None))
        self.loader.assert_not_called()
        self.install.assert_not_called()

    def test_missing_token_keeps_ui_ticks_but_does_not_poll_config(self):
        self.loader.side_effect = FileNotFoundError()
        self.driver.before_pump = mock.Mock()
        self.assertTrue(self.lifecycle.autostart())
        for _ in range(20):
            self.driver.on_tick()
        self.assertEqual(self.lifecycle.state, "waiting_for_token")
        self.assertEqual(self.driver.before_pump.call_count, 20)
        self.loader.assert_called_once_with()
        self.assertIsNone(self.driver.client)

    def test_invalid_config_does_not_expose_exception_text(self):
        self.loader.side_effect = ValueError("secret-token-value")
        self.assertTrue(self.lifecycle.autostart())
        self.assertEqual(self.lifecycle.state, "invalid_bridge_config")
        self.assertEqual(self.lifecycle.start(), ("invalid_config", None))

    def test_bound_ticks_never_reload_config_or_probe_socket(self):
        self.lifecycle.autostart()
        self.driver.on_tick()
        self.client.connected = True
        for _ in range(20):
            self.driver.on_tick()
        self.assertEqual(self.lifecycle.state, "bound")
        self.loader.assert_called_once_with()
        self.probe.assert_called_once_with()
        self.assertEqual(self.client.pump.call_count, 21)

    def test_explicit_start_closes_pending_bootstrap_not_yet_attached(self):
        self.lifecycle.autostart()
        replacement = mock.Mock(connected=False)
        self.loader.return_value = (replacement, self.endpoint)
        self.assertEqual(self.lifecycle.start(), ("started", self.endpoint))
        self.client.close.assert_called_once_with()
        replacement.close.assert_not_called()
        self.assertIs(self.driver.client, replacement)

    def test_failed_configuration_preserves_running_client(self):
        self.lifecycle.start()
        self.loader.side_effect = FileNotFoundError()
        self.assertEqual(self.lifecycle.start(), ("no_token", None))
        self.assertIs(self.driver.client, self.client)
        self.assertTrue(self.driver.active)
        self.client.close.assert_not_called()

    def test_failed_hook_install_closes_candidate_and_keeps_previous_client(self):
        self.lifecycle.start()
        replacement = mock.Mock()
        self.loader.return_value = (replacement, self.endpoint)
        self.install.side_effect = RuntimeError("private detail")
        self.assertEqual(self.lifecycle.start(), ("start_failed", "RuntimeError"))
        replacement.close.assert_called_once_with()
        self.client.close.assert_not_called()
        self.assertIs(self.driver.client, self.client)
        self.assertTrue(self.driver.active)

    def test_stop_closes_shared_client_once_and_is_idempotent(self):
        self.lifecycle.start()
        self.assertTrue(self.lifecycle.stop())
        self.lifecycle.stop()
        self.client.close.assert_called_once_with()
        self.assertIsNone(self.lifecycle.endpoint)
        self.assertIsNone(self.lifecycle.bootstrap_client)
        self.assertIsNone(self.driver.client)
        self.assertFalse(self.driver.active)
        self.assertEqual(self.lifecycle.state, "stopped_by_command")

    def test_hook_removal_failure_still_closes_and_prevents_revival(self):
        self.lifecycle.start()
        stale_binder = self.lifecycle.bind
        self.remove.side_effect = RuntimeError("private hook detail")
        self.assertFalse(self.lifecycle.stop())
        self.assertEqual(self.lifecycle.cleanup_errors, ["RuntimeError"])
        self.client.close.assert_called_once_with()
        self.assertIsNone(stale_binder(None))
        for _ in range(20):
            self.driver.on_tick()
        self.client.pump.assert_not_called()
        self.loader.assert_called_once_with()

    def test_close_failure_does_not_skip_distinct_bootstrap_client(self):
        self.lifecycle.autostart()
        attached = mock.Mock()
        attached.close.side_effect = OSError("secret")
        self.driver.attach(attached)
        self.lifecycle.stop()
        attached.close.assert_called_once_with()
        self.client.close.assert_called_once_with()
        self.assertEqual(self.lifecycle.cleanup_errors, ["OSError"])
        self.assertIsNone(self.driver.client)

    def test_autostart_hook_failure_cleans_up_and_stays_idle(self):
        self.install.side_effect = AttributeError("Zone changed")
        self.assertFalse(self.lifecycle.autostart())
        self.assertEqual(self.lifecycle.state, "hook_failed: AttributeError")
        self.client.close.assert_called_once_with()
        self.assertIsNone(self.lifecycle.bind(None))
        self.driver.on_tick()
        self.client.pump.assert_not_called()

    def test_manual_restart_after_stop_reloads_and_resumes_once(self):
        self.lifecycle.start()
        self.lifecycle.stop()
        replacement = mock.Mock(connected=False)
        self.loader.return_value = (replacement, self.endpoint)
        self.lifecycle.start()
        self.driver.on_tick()
        replacement.pump.assert_called_once_with()
        self.client.pump.assert_not_called()
        self.client.close.assert_called_once_with()
        self.assertEqual(self.loader.call_count, 2)
        self.assertEqual(self.lifecycle.cleanup_errors, [])

    def test_failed_driver_attach_closes_candidate_and_disables_retry(self):
        self.lifecycle.autostart()
        replacement = mock.Mock()
        self.loader.return_value = (replacement, self.endpoint)
        with mock.patch.object(self.driver, "attach", side_effect=RuntimeError("secret")):
            self.assertEqual(self.lifecycle.start(), ("start_failed", "RuntimeError"))
        self.client.close.assert_called_once_with()
        replacement.close.assert_called_once_with()
        self.assertEqual(self.lifecycle.state, "start_failed: RuntimeError")
        self.assertFalse(self.driver.active)
        self.assertIsNone(self.lifecycle.bind(None))
