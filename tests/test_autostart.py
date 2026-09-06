"""The mod-load autostart path, exercised without the game.

``commands.py`` is the module the build publishes as ``plumbmate_boot.pyc``,
and its autostart runs at import. To test that, the game modules it imports are
replaced with stubs and the source is placed inside a fake ``.ts4script``
directory so its own bridge-path resolution runs for real.

Stubs stand in for ``services``, ``sims4.commands`` and ``zone``. They prove
nothing about the real engine; what they let us test is the part that is ours,
namely that preconfigured clients reconnect to the same port without file I/O
on ticks. Deliberate token/port changes are reloaded only by an explicit start.
"""

import importlib.util
import functools
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
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
        def __init__(self, connection=None):
            self.lines = []

        def __call__(self, line):
            self.lines.append(line)

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
        _load_root_module("plumbmate_turn_bridge", "turn_bridge.py")
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
            module.plumbmate_bridge_stop()
        except Exception:
            pass

    def write_token(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "bridge-token").write_text(TOKEN, encoding="utf-8")

    def write_endpoint(self, port, host="127.0.0.1"):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "bridge-endpoint.json").write_text(
            json.dumps({"host": host, "port": int(port), "protocol_version": "0.1"}),
            encoding="utf-8",
        )

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
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_token")
        self.assertIsNone(module.TICK_DRIVER.client)

    def test_no_companion_means_ticks_stay_idle_and_harmless(self):
        module = self.boot()
        zone = self.zone_class()
        for tick in range(200):
            self.assertEqual(zone.update(tick), "engine-result")
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.TICK_DRIVER.stats["pumps"], 0)
        self.assertEqual(module.TICK_DRIVER.stats["bind_errors"], 0)
        self.assertEqual(module.AUTOSTART_STATE, "waiting_for_token")

    def test_a_token_without_endpoint_uses_the_stable_default(self):
        self.write_token()
        module = self.boot()
        client = module._autostart_binder(None)
        self.assertIsNotNone(client)
        self.assertEqual(client._port, module.DEFAULT_BRIDGE_PORT)
        self.assertFalse(client.connected)
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
        # Installation provisions credentials/configuration before game load.
        reservation = EventSocketServer(TOKEN)
        reservation.start()
        port = reservation.port
        reservation.stop()
        self.write_token()
        self.write_endpoint(port)
        module = self.boot()
        zone = self.zone_class()
        for tick in range(20):
            zone.update(tick)
        self.assertFalse(module.TICK_DRIVER.client.connected)

        server = EventSocketServer(TOKEN, port=port, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)

        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client is not None
            and module.TICK_DRIVER.client.connected
        ), "autostart never connected")
        zone.update(21)
        self.assertEqual(module.AUTOSTART_STATE, "bound")

        event_id = module.TICK_DRIVER.client.queue_event("heartbeat", {"source": "autostart"})
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client.stats["acked_events"] == 1
        ))
        received = server.poll(timeout=15.0)
        self.assertEqual(received.event_id, event_id)
        self.assertEqual(received.payload, {"source": "autostart"})

    def test_a_companion_restart_on_the_same_port_reuses_the_client(self):
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

        # A normal CLI restart reuses persistent configuration, not a new port.
        first_port = first.port
        first.stop()
        second = EventSocketServer(TOKEN, port=first_port, idle_timeout=60.0)
        second.start()
        self.addCleanup(second.stop)

        self.assertTrue(self.tick_until(
            zone, lambda: original.connected and original.stats["handshakes"] == 2,
            timeout=25.0,
        ), "never reconnected to the same port")
        self.assertIs(module.TICK_DRIVER.client, original)
        self.assertEqual(module.TICK_DRIVER.stats["binds"], 1)

        module.TICK_DRIVER.client.queue_event("heartbeat", {"source": "rebind"})
        self.assertTrue(self.tick_until(
            zone, lambda: module.TICK_DRIVER.client.stats["acked_events"] == 1
        ))
        self.assertEqual(second.poll(timeout=15.0).payload, {"source": "rebind"})

    def test_deliberate_port_change_requires_explicit_reload(self):
        with EventSocketServer(TOKEN) as first, EventSocketServer(TOKEN) as second:
            self.write_token()
            self.write_endpoint(first.port)
            module = self.boot()
            zone = self.zone_class()
            self.assertTrue(self.tick_until(zone, lambda: module.TICK_DRIVER.client.connected))
            original = module.TICK_DRIVER.client
            self.write_endpoint(second.port)
            for tick in range(30):
                zone.update(tick)
            self.assertIs(module.TICK_DRIVER.client, original)
            module.plumbmate_bridge_start()
            self.assertTrue(self.tick_until(zone, lambda: module.TICK_DRIVER.client.connected))
            self.assertIsNot(module.TICK_DRIVER.client, original)
            self.assertEqual(module.TICK_DRIVER.client._port, second.port)
            self.assertEqual(original.state, "CLOSED")

    def test_late_token_is_loaded_by_explicit_start_only(self):
        module = self.boot()
        self.write_token()
        with EventSocketServer(TOKEN) as server:
            self.write_endpoint(server.port)
            zone = self.zone_class()
            zone.update(1)
            self.assertIsNone(module.TICK_DRIVER.client)
            module.plumbmate_bridge_start()
            self.assertTrue(self.tick_until(zone, lambda: module.TICK_DRIVER.client.connected))

    def test_no_filesystem_operations_on_ticks_even_while_binding(self):
        self.write_token()
        module = self.boot()
        client = mock.Mock(connected=False)
        module._BOOTSTRAP_CLIENT = client
        zone = self.zone_class()
        with mock.patch("builtins.open", side_effect=AssertionError("tick file read")) as read, \
             mock.patch("os.path.exists", side_effect=AssertionError("tick stat")) as exists, \
             mock.patch("os.path.isdir", side_effect=AssertionError("tick stat")) as isdir, \
             mock.patch("os.makedirs", side_effect=AssertionError("tick mkdir")) as mkdir:
            for tick in range(200):
                self.assertEqual(zone.update(tick), "engine-result")
            for operation in (read, exists, isdir, mkdir):
                operation.assert_not_called()
        self.assertEqual(module.TICK_DRIVER.stats["bind_errors"], 0)
        self.assertEqual(client.pump.call_count, 200)

    def test_bad_configuration_stays_idle_until_explicitly_fixed(self):
        self.write_token()
        self.write_endpoint(70000)
        module = self.boot()
        self.zone_class().update(1)
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.AUTOSTART_STATE, "invalid_bridge_config")

    def test_read_only_returns_the_exact_pending_response(self):
        module = self.boot()
        responses = self.state.parent / "responses"
        responses.mkdir(parents=True, exist_ok=True)
        module._LAST_REQUEST_ID = "a" * 32
        unrelated = {"protocol_version": "0.1", "request_id": "b" * 32,
                     "partner_message": "wrong turn", "action": {"name": "suggest_only"}}
        (responses / ("b" * 32 + ".json")).write_text(json.dumps(unrelated), encoding="utf-8")
        self.assertIsNone(module._latest_response())
        target = responses / ("a" * 32 + ".json")
        target.write_text(json.dumps(unrelated), encoding="utf-8")
        with self.assertRaises(ValueError):
            module._latest_response()
        matched = dict(unrelated, request_id="a" * 32, partner_message="right turn")
        target.write_text(json.dumps(matched), encoding="utf-8")
        self.assertEqual(module._latest_response()["partner_message"], "right turn")

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

    def _stop_under_foreign_wrapper(self, copy_markers):
        self.write_token()
        module = self.boot()
        inner = self.zone_class.update
        calls = []

        def foreign(zone, tick):
            calls.append(tick)
            return inner(zone, tick)
        if copy_markers:
            foreign = functools.wraps(inner)(foreign)
        self.zone_class.update = foreign
        # Stop must work even when a new socket cannot be created.
        with mock.patch.object(module, "_bridge_available", return_value=False):
            module.plumbmate_bridge_stop()
        binds = module.TICK_DRIVER.stats["binds"]
        zone = self.zone_class()
        for tick in range(100):
            self.assertEqual(zone.update(tick), "engine-result")
            module.TICK_DRIVER.on_tick(tick)  # also defend direct stale callbacks
        self.assertIs(self.zone_class.update, foreign)
        self.assertEqual(len(calls), 100)
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.TICK_DRIVER.stats["binds"], binds)
        self.assertEqual(module.AUTOSTART_STATE, "stopped_by_command")
        # Explicit start is the only way back; it must not double-pump.
        with EventSocketServer(TOKEN) as server:
            self.write_endpoint(server.port)
            module.plumbmate_bridge_start()
            self.assertTrue(self.tick_until(zone, lambda: module.TICK_DRIVER.client.connected))
            pumps = module.TICK_DRIVER.stats["pumps"]
            zone.update(999)
            self.assertEqual(module.TICK_DRIVER.stats["pumps"], pumps + 1)

    def test_stop_under_unmarked_foreign_wrapper_cannot_rebind(self):
        self._stop_under_foreign_wrapper(False)

    def test_stop_preserves_foreign_wrapper_with_copied_markers(self):
        self._stop_under_foreign_wrapper(True)

    def test_multiword_chat_round_trip_with_active_sim(self):
        from plumbmate.engine import CompanionEngine
        from plumbmate.protocol import BridgePaths, read_json
        from plumbmate.providers import MockProvider

        module = self.boot()
        info = types.SimpleNamespace(sim_id=42, full_name="Test Partner", age="ADULT", gender="X")
        sim = types.SimpleNamespace(sim_info=info)
        manager = types.SimpleNamespace(get=lambda connection: types.SimpleNamespace(active_sim=sim))
        lines = []
        with mock.patch.object(module.services, "client_manager", return_value=manager), \
                mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            # TS4 passes each token positionally and _connection by keyword.
            module.plumbmate_chat("오늘", "같이", "산책할까?", _connection=7)
            paths = BridgePaths(self.mods_root / "PlumbMateBridge")
            request_path, = paths.requests.glob("*.json")
            self.assertEqual(read_json(request_path)["player_message"], "오늘 같이 산책할까?")
            self.assertIsNotNone(CompanionEngine(paths, MockProvider()).process_one())
            module.plumbmate_read(_connection=7)
        self.assertTrue(any("오늘 같이 산책할까?" in line for line in lines))

    def test_chat_without_client_manager_is_a_normal_refusal(self):
        module = self.boot()
        lines = []
        with mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_chat("hello", "there", _connection=7)
        self.assertTrue(any("active Sim not found" in line for line in lines))

    def test_unicode_path_does_not_shift_archive_boundary(self):
        module = self.boot()
        with mock.patch.object(module, "__file__", "/Users/İ/Mods/PlumbMate.TS4SCRIPT/plumbmate_boot.pyc"):
            self.assertEqual(module._mod_directory(), "/Users/İ/Mods")

    def test_unicode_digits_are_rejected_before_socket_creation(self):
        module = self.boot()
        client_module = module.plumbmate_bridge_client
        for host in ("１２７.０.０.１", "127.٠.٠.١", "127.00.0.1", "127.0.0.01"):
            with self.subTest(host=host), mock.patch.object(client_module.socket, "socket") as factory:
                with self.assertRaises(ValueError):
                    client_module.BridgeClient(TOKEN, 47651, host=host)
                factory.assert_not_called()



class SnapshotTests(AutostartTests):
    """Motives/buffs in the request snapshot: guarded, optional, bounded."""

    def _fake_sim(self, with_tracker=True):
        info = types.SimpleNamespace(sim_id=42, full_name="Test Partner", age="ADULT", gender="X")
        household = types.SimpleNamespace(id=42, sim_info_gen=lambda: iter([info]))
        levels = {"motive_Hunger": -160.0, "motive_Energy": 20.0, "motive_Fun": 250.0}

        class Statistic(object):
            def __init__(self, value):
                self.value = value

            def get_value(self):
                return self.value

        tracker = types.SimpleNamespace(
            get_statistic=lambda tuning: Statistic(levels[tuning.__name__]) if tuning.__name__ in levels else None
        )
        buffs = types.SimpleNamespace(get_active_buff_types=lambda: [
            types.SimpleNamespace(__name__="buff_Hungry"), types.SimpleNamespace(__name__="buff_Hungry")])
        sim = types.SimpleNamespace(
            sim_info=info, household=household, get_mood=lambda: types.SimpleNamespace(__name__="Mood_Happy"),
            Buffs=buffs,
        )
        if with_tracker:
            sim.commodity_tracker = tracker
        return sim

    def _fake_statistic_manager(self):
        tunings = {index: types.SimpleNamespace(__name__=name) for index, name in enumerate(
            ("motive_Hunger", "motive_Energy", "motive_Social", "motive_Fun", "motive_Hygiene",
             "motive_Bladder", "commodity_Unrelated"))}
        return types.SimpleNamespace(types=tunings)

    def test_snapshot_reports_motives_and_buffs_when_the_game_exposes_them(self):
        module = self.boot()
        manager = self._fake_statistic_manager()
        with mock.patch.object(module.services, "get_instance_manager", create=True, return_value=manager), \
                mock.patch.object(module.sims4, "resources", create=True,
                                  new=types.SimpleNamespace(Types=types.SimpleNamespace(STATISTIC="statistic"))):
            module._MOTIVE_TYPES = None
            actor, household = module._snapshot(self._fake_sim())
        # Values outside the game's range are clamped to the contract's [-100, 100].
        self.assertEqual(actor["motives"], {"hunger": -100.0, "energy": 20.0, "fun": 100.0})
        self.assertEqual(actor["buffs"], ["buff_Hungry"])
        self.assertEqual(actor["mood"], "Mood_Happy")
        from plumbmate.contracts import validate_request
        validate_request({
            "protocol_version": "0.1", "request_id": "a" * 32, "kind": "turn",
            "created_at": "now", "player_message": "hi", "session_id": "household-42",
            "actor": actor, "household": household,
        })

    def test_snapshot_without_statistic_manager_omits_motives_and_keeps_the_turn(self):
        module = self.boot()
        actor, _household = module._snapshot(self._fake_sim(with_tracker=False))
        self.assertNotIn("motives", actor)
        self.assertEqual(actor["name"], "Test Partner")

    def test_ping_carries_the_household_session_id(self):
        module = self.boot()
        calls = []
        fake_client = types.SimpleNamespace(
            queue_event=lambda kind, payload=None: calls.append((kind, payload)) or "event-1",
            close=lambda: None,
        )
        module.TICK_DRIVER.attach(fake_client)
        manager = types.SimpleNamespace(get=lambda connection: types.SimpleNamespace(active_sim=self._fake_sim()))
        lines = []
        with mock.patch.object(module.services, "client_manager", return_value=manager), \
                mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_bridge_ping(_connection=7)
        self.assertEqual(calls, [("heartbeat", {"source": "cheat_console", "session_id": "household-42"})])
        self.assertTrue(any("queued heartbeat" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
