"""The mod-load autostart path, exercised without the game.

``commands.py`` is the module the build publishes as ``plumbmate_boot.pyc``,
and its autostart runs at import. To test that, the game modules it imports are
replaced with stubs and the source is placed inside a fake ``.ts4script``
directory so its own bridge-path resolution runs for real.

Stubs stand in for ``services``, ``sims4.commands`` and ``zone``. They prove
nothing about the real engine; what they let us test is the part that is ours,
namely that a hook installed with nothing listening stays idle, picks up a
companion that appears later, and follows it to a new port after a restart.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from plumbmate.event_socket import MIN_TOKEN_CHARACTERS, EventSocketServer

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod"
TOKEN = "a" * MIN_TOKEN_CHARACTERS


class FakeZoneClass(object):
    """Mirrors the real signature: Zone.update(self, absolute_ticks)."""

    def update(self, absolute_ticks):
        return "engine-result"


def _install_game_stubs():
    """Put the game-only modules commands.py imports into sys.modules."""
    services = types.ModuleType("services")
    services.client_manager = lambda: None

    class CommandType(object):
        Live = "Live"

    class CheatOutput(object):
        transcript = []  # class-level so tests can read what commands printed

        def __init__(self, connection=None):
            self.lines = []

        def __call__(self, line):
            self.lines.append(line)
            type(self).transcript.append(line)

    def Command(*names, **kwargs):
        return lambda function: function

    sims4 = types.ModuleType("sims4")
    sims4_commands = types.ModuleType("sims4.commands")
    sims4_commands.Command = Command
    sims4_commands.CommandType = CommandType
    sims4_commands.CheatOutput = CheatOutput
    sims4.commands = sims4_commands

    zone_module = types.ModuleType("zone")
    zone_module.Zone = FakeZoneClass

    return {
        "services": services,
        "sims4": sims4,
        "sims4.commands": sims4_commands,
        "zone": zone_module,
    }


def _load_root_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE_ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.addCleanup(self._restore_modules)

        temporary = tempfile.mkdtemp(prefix="plumbmate-autostart-")
        self.addCleanup(shutil.rmtree, temporary, True)
        self.mods_root = Path(temporary)
        # _mod_directory() looks for the .ts4script marker in its own path.
        archive = self.mods_root / "PlumbMate.ts4script"
        archive.mkdir()
        self.boot_source = archive / "plumbmate_boot.py"
        shutil.copyfile(SOURCE_ROOT / "commands.py", self.boot_source)
        self.state = self.mods_root / "PlumbMateBridge" / "state"

        # A fresh Zone class per test; the hook mutates the class it is given.
        self.zone_class = type("Zone", (FakeZoneClass,), {})

    def _restore_modules(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def boot(self, *, bind_interval=0.0):
        """Import the boot module with stubs in place, as mod load would."""
        sys.modules.update(_install_game_stubs())
        sys.modules["zone"].Zone = self.zone_class
        _load_root_module("plumbmate_bridge_client", "bridge_client.py")
        _load_root_module("plumbmate_tick_driver", "tick_driver.py")
        spec = importlib.util.spec_from_file_location(
            "plumbmate_boot_under_test", self.boot_source
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.addCleanup(self._unhook, module)
        if module.TICK_DRIVER is not None:
            # Production waits 5s between attempts; tests should not.
            module.TICK_DRIVER.set_binder(module._autostart_binder, bind_interval)
        return module

    def _unhook(self, module):
        try:
            sys.modules["plumbmate_tick_driver"].remove_tick_hook(self.zone_class, "update")
        except Exception:
            pass
        try:
            client = module.TICK_DRIVER.detach()
            if client is not None:
                client.close()
        except Exception:
            pass

    def write_token(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "bridge-token").write_text(TOKEN, encoding="utf-8")

    def write_endpoint(self, port, host="127.0.0.1", refresh_seconds=None):
        self.state.mkdir(parents=True, exist_ok=True)
        payload = {"host": host, "port": int(port), "protocol_version": "0.1"}
        if refresh_seconds is not None:
            payload["refresh_seconds"] = refresh_seconds
        (self.state / "bridge-endpoint.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )

    def age_endpoint(self, seconds):
        target = self.state / "bridge-endpoint.json"
        stamp = time.time() - seconds
        os.utime(str(target), (stamp, stamp))

    def clear_endpoint(self):
        try:
            os.remove(str(self.state / "bridge-endpoint.json"))
        except OSError:
            pass

    def tick_until(self, zone, predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        tick = 0
        while time.monotonic() < deadline:
            zone.update(tick)
            tick += 1
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_import_alone_installs_the_hook(self):
        module = self.boot()
        self.assertTrue(module._hook_installed())
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_companion")
        self.assertIsNone(module.TICK_DRIVER.client)

    def test_no_companion_means_ticks_stay_idle_and_harmless(self):
        module = self.boot()
        zone = self.zone_class()
        for tick in range(200):
            self.assertEqual(zone.update(tick), "engine-result")
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.TICK_DRIVER.stats["pumps"], 0)
        self.assertEqual(module.TICK_DRIVER.stats["bind_errors"], 0)
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_companion")

    def test_a_token_without_a_listener_still_binds_nothing(self):
        self.write_token()
        module = self.boot()
        zone = self.zone_class()
        for tick in range(50):
            zone.update(tick)
        # The endpoint file is the liveness signal; a token alone is not one.
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_companion")

    def test_a_listener_without_a_token_reports_why_it_waits(self):
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        self.write_endpoint(server.port)
        module = self.boot()
        zone = self.zone_class()
        for tick in range(50):
            zone.update(tick)
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_token")

    def test_a_companion_started_after_the_game_is_picked_up(self):
        module = self.boot()
        zone = self.zone_class()
        for tick in range(20):
            zone.update(tick)
        self.assertIsNone(module.TICK_DRIVER.client)

        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        self.write_token()
        self.write_endpoint(server.port)

        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ), "autostart never connected")
        self.assertEqual(module.AUTOSTART_STATE, "bound")

        event_id = module.TICK_DRIVER.client.queue_event("heartbeat", {"source": "autostart"})
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client.stats["acked_events"] == 1
        ))
        received = server.poll(timeout=15.0)
        self.assertEqual(received.event_id, event_id)
        self.assertEqual(received.payload, {"source": "autostart"})

    def test_a_companion_restart_on_a_new_port_is_followed(self):
        first = EventSocketServer(TOKEN, idle_timeout=60.0)
        first.start()
        self.addCleanup(first.stop)
        self.write_token()
        self.write_endpoint(first.port)
        module = self.boot()
        zone = self.zone_class()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ))
        original = module.TICK_DRIVER.client

        # 'plumbmate listen' asks the kernel for a port, so a restart moves it.
        first_port = first.port
        first.stop()
        self.clear_endpoint()
        second = EventSocketServer(TOKEN, idle_timeout=60.0)
        second.start()
        self.addCleanup(second.stop)
        self.assertNotEqual(second.port, first_port)
        self.write_endpoint(second.port)

        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not original
            and module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected,
            timeout=25.0,
        ), "never rebound to the new port")
        self.assertEqual(module.TICK_DRIVER.stats["binds"], 2)

        module.TICK_DRIVER.client.queue_event("heartbeat", {"source": "rebind"})
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client.stats["acked_events"] == 1
        ))
        self.assertEqual(second.poll(timeout=15.0).payload, {"source": "rebind"})

    def test_a_stable_endpoint_is_not_rebound_every_attempt(self):
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        self.write_token()
        self.write_endpoint(server.port)
        module = self.boot()
        zone = self.zone_class()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ))
        client = module.TICK_DRIVER.client
        for tick in range(200):
            zone.update(tick)
        self.assertIs(module.TICK_DRIVER.client, client)
        self.assertEqual(module.TICK_DRIVER.stats["binds"], 1)
        self.assertEqual(client.stats["handshakes"], 1)

    def test_the_disable_file_keeps_the_hook_off_entirely(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "autostart-disabled").write_text("", encoding="utf-8")
        module = self.boot()
        self.assertEqual(module.AUTOSTART_STATE, "disabled_by_file")
        self.assertFalse(module._hook_installed())
        self.assertIsNone(module.TICK_DRIVER.client)

    def test_manual_start_without_a_live_endpoint_refuses(self):
        # There is no port worth guessing; the command must say so, not bind
        # a client to a port nothing will ever listen on.
        self.write_token()
        module = self.boot()
        module.plumbmate_bridge_start()
        self.assertIsNone(module.TICK_DRIVER.client)
        transcript = "\n".join(sys.modules["sims4.commands"].CheatOutput.transcript)
        self.assertIn("not publishing an endpoint", transcript)

    def test_a_stale_endpoint_file_is_treated_as_absent(self):
        # A file that advertises a refresh cadence but has aged far past it
        # belongs to a companion that died without cleanup.
        self.write_token()
        self.write_endpoint(45999, refresh_seconds=1.0)
        self.age_endpoint(300)
        module = self.boot()
        zone = self.zone_class()
        for tick in range(50):
            zone.update(tick)
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_companion")

    def test_a_legacy_endpoint_without_cadence_never_ages_out(self):
        # Older companions do not refresh the file; their endpoint files must
        # keep working no matter how old they look.
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        self.write_token()
        self.write_endpoint(server.port)
        self.age_endpoint(3600)
        module = self.boot()
        zone = self.zone_class()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ), "legacy endpoint file was wrongly aged out")

    def test_queued_events_survive_a_port_move(self):
        first = EventSocketServer(TOKEN, idle_timeout=60.0)
        first.start()
        self.addCleanup(first.stop)
        self.write_token()
        self.write_endpoint(first.port)
        module = self.boot()
        zone = self.zone_class()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ))

        # Stop the companion, queue an event while nothing is listening, then
        # bring a new companion up on a different port. The queue happens only
        # after the client has noticed the loss, so the event is verifiably
        # unsent rather than in flight when the endpoint moves.
        first_port = first.port
        first.stop()
        self.clear_endpoint()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client.state != "READY"
        ))
        event_id = module.TICK_DRIVER.client.queue_event("heartbeat", {"source": "queued-offline"})
        self.assertIsNotNone(event_id)

        second = EventSocketServer(TOKEN, idle_timeout=60.0)
        second.start()
        self.addCleanup(second.stop)
        self.assertNotEqual(second.port, first_port)
        self.write_endpoint(second.port)

        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.stats["acked_events"] == 1,
            timeout=25.0,
        ), "the queued event never reached the new companion")
        received = second.poll(timeout=15.0)
        self.assertEqual(received.event_id, event_id)
        self.assertEqual(received.payload, {"source": "queued-offline"})

    def test_stop_command_unhooks_and_leaves_the_engine_alone(self):
        server = EventSocketServer(TOKEN, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        self.write_token()
        self.write_endpoint(server.port)
        module = self.boot()
        zone = self.zone_class()
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ))
        module.plumbmate_bridge_stop()
        self.assertFalse(module._hook_installed())
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.AUTOSTART_STATE, "stopped_by_command")
        binds = module.TICK_DRIVER.stats["binds"]
        for tick in range(100):
            self.assertEqual(zone.update(tick), "engine-result")
        # With the hook gone the binder never runs again this session.
        self.assertEqual(module.TICK_DRIVER.stats["binds"], binds)


if __name__ == "__main__":
    unittest.main()
