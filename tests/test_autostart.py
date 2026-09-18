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

    def boot(self, *, bind_interval=0.0, missing_lifecycle=False):
        """Import the boot module with stubs in place, as mod load would."""
        sys.modules.update(_install_game_stubs())
        sys.modules["zone"].Zone = self.zone_class
        _load_root_module("plumbmate_scene_adapter", "scene_adapter.py")
        _load_root_module("plumbmate_conversation_state", "conversation_state.py")
        _load_root_module("plumbmate_bridge_client", "bridge_client.py")
        if missing_lifecycle:
            sys.modules["plumbmate_bridge_lifecycle"] = None
        else:
            _load_root_module("plumbmate_bridge_lifecycle", "bridge_lifecycle.py")
        _load_root_module("plumbmate_reply_delivery", "reply_delivery.py")
        _load_root_module("plumbmate_reply_presenter", "reply_presenter.py")
        _load_root_module("plumbmate_tick_driver", "tick_driver.py")
        _load_root_module("plumbmate_turn_bridge", "turn_bridge.py")
        _load_root_module("plumbmate_role_binding", "role_binding.py")
        _load_root_module("plumbmate_role_setup_ui", "role_setup_ui.py")
        _load_root_module("plumbmate_chat_input", "chat_input.py")
        _load_root_module("plumbmate_chat_ui", "chat_ui.py")
        _load_root_module("plumbmate_control_ui", "control_ui.py")
        spec = importlib.util.spec_from_file_location(
            "plumbmate_boot_under_test", self.boot_source
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.addCleanup(self._unhook, module)
        if module.TICK_DRIVER is not None and module.BRIDGE_LIFECYCLE is not None:
            # Production waits 5s between attempts; tests should not.
            module.TICK_DRIVER.set_binder(module.BRIDGE_LIFECYCLE.bind, bind_interval)
        return module

    def test_missing_lifecycle_module_keeps_diagnostics_and_stop_available(self):
        module = self.boot(missing_lifecycle=True)
        lines = []
        with mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_status()
            module.plumbmate_bridge_start()
            module.plumbmate_bridge_stop()
        self.assertIsNone(module.BRIDGE_LIFECYCLE)
        self.assertFalse(module._hook_installed())
        self.assertTrue(any("lifecycle_unavailable" in line for line in lines))
        self.assertTrue(any("no automatic bridge was started" in line for line in lines))

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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "waiting_for_token")
        self.assertIsNone(module.TICK_DRIVER.client)

    def test_no_companion_means_ticks_stay_idle_and_harmless(self):
        module = self.boot()
        zone = self.zone_class()
        for tick in range(200):
            self.assertEqual(zone.update(tick), "engine-result")
        self.assertIsNone(module.TICK_DRIVER.client)
        self.assertEqual(module.TICK_DRIVER.stats["pumps"], 0)
        self.assertEqual(module.TICK_DRIVER.stats["bind_errors"], 0)
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "waiting_for_token")

    def test_a_token_without_endpoint_uses_the_stable_default(self):
        self.write_token()
        module = self.boot()
        client = module.BRIDGE_LIFECYCLE.bind(None)
        self.assertIsNotNone(client)
        self.assertEqual(client._port, module.DEFAULT_BRIDGE_PORT)
        self.assertFalse(client.connected)
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "waiting_for_companion")

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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "waiting_for_token")

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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "bound")

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
        module.BRIDGE_LIFECYCLE.bootstrap_client = client
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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "invalid_bridge_config")

    def test_read_only_returns_the_exact_pending_response(self):
        module = self.boot()
        responses = self.state.parent / "responses"
        responses.mkdir(parents=True, exist_ok=True)
        module.TURN_STATE.request_id = "a" * 32
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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "disabled_by_file")
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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "stopped_by_command")
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
        self.assertEqual(module.BRIDGE_LIFECYCLE.state, "stopped_by_command")
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
        partner = types.SimpleNamespace(sim_id=43, full_name="Other Sim", get_sim_instance=lambda: object())
        members = [info, partner]
        household = types.SimpleNamespace(sim_info_gen=lambda: iter(members))
        sim = types.SimpleNamespace(sim_info=info, household=household)
        zone_obj = object()
        module.services.current_zone = lambda: zone_obj
        module.ROLE_BINDING.bind(zone_obj, household, info, partner, members)
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

    def _ready_role_pair(self, module):
        player = types.SimpleNamespace(sim_id=10, full_name="Player")
        partner = types.SimpleNamespace(sim_id=11, full_name="Partner")
        members = [player, partner]
        household = types.SimpleNamespace(sim_info_gen=lambda: iter(members))
        player_sim = types.SimpleNamespace(sim_info=player, household=household)
        partner_sim = types.SimpleNamespace(sim_info=partner, household=household)
        player.get_sim_instance = lambda: player_sim
        partner.get_sim_instance = lambda: partner_sim
        current = {"selected": player_sim, "zone": object()}
        module.services.client_manager = lambda: types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(active_sim=current["selected"]),
            get_first_client=lambda: types.SimpleNamespace(active_sim=current["selected"]))
        module.services.current_zone = lambda: current["zone"]
        module.ROLE_BINDING.bind(current["zone"], household, player, partner, members)
        return current, player_sim, partner_sim

    def test_changing_selected_sim_does_not_swap_player_and_ai(self):
        from plumbmate.protocol import read_json
        module = self.boot()
        current, player, partner = self._ready_role_pair(module)
        current["selected"] = partner
        module.plumbmate_chat("hello", "partner", _connection=7)
        path, = (self.mods_root / "PlumbMateBridge/requests").glob("*.json")
        request = read_json(path)
        self.assertEqual(request["actor"]["sim_id"], player.sim_info.sim_id)
        self.assertEqual(request["roles"]["partner_sim_id"], partner.sim_info.sim_id)

    def test_console_free_launcher_works_without_a_bridge_token_or_disk_polling(self):
        module = self.boot()
        self._ready_role_pair(module)
        self.assertIsNone(module.TICK_DRIVER.client)
        with mock.patch.object(module.CONTROL_UI, "_make_launcher", return_value=mock.Mock()) as launch, \
                mock.patch("builtins.open", side_effect=AssertionError("tick must not read files")), \
                mock.patch("os.stat", side_effect=AssertionError("tick must not stat files")):
            module.TICK_DRIVER.on_tick()
        launch.assert_called_once()
        self.assertIsNone(module.TURN_STATE.request_id)

    def test_control_menu_opens_composer_without_console_connection_or_send(self):
        module = self.boot()
        self._ready_role_pair(module)
        with mock.patch.object(module.CONTROL_UI, "open") as menu:
            module._open_control()
        menu.assert_called_once()
        with mock.patch.object(module.CHAT_UI, "open") as composer:
            menu.call_args.args[2]()
        composer.assert_called_once()
        self.assertIsNone(module.TURN_STATE.request_id)
        self.assertFalse((self.mods_root / "PlumbMateBridge/requests").exists())

    def test_chat_double_submit_creates_only_one_request(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("hello", _connection=7)
        module.plumbmate_chat("again", _connection=7)
        self.assertEqual(len(list((self.mods_root / "PlumbMateBridge/requests").glob("*.json"))), 1)

    def test_failed_request_write_never_advances_conversation_state(self):
        module = self.boot()
        _, player, _ = self._ready_role_pair(module)
        module._write_request(player, "first")
        previous = dict(vars(module.TURN_STATE))
        with mock.patch.object(module.plumbmate_turn_bridge, "write_request", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                module._write_request(player, "not committed")
        self.assertEqual(vars(module.TURN_STATE), previous)

    def test_missing_conversation_module_never_writes_a_request(self):
        module = self.boot()
        _, player, _ = self._ready_role_pair(module)
        module.TURN_STATE = None
        with mock.patch.object(module.plumbmate_turn_bridge, "write_request") as write:
            with self.assertRaises(ValueError):
                module._write_request(player, "must not send")
        write.assert_not_called()

    def test_missing_scene_adapter_preserves_stop_and_status_diagnostics(self):
        module = self.boot()
        module.plumbmate_scene_adapter = None
        lines = []
        with mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_chat("hello", _connection=7)
            module.plumbmate_status(_connection=7)
            module.plumbmate_bridge_stop(_connection=7)
        self.assertTrue(any("active Sim not found" in line for line in lines))
        self.assertTrue(any("bridge stopped" in line for line in lines))
        self.assertIsNone(module.TURN_STATE.request_id)

    def test_pending_chat_opens_status_instead_of_another_composer(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("first", _connection=7)
        with mock.patch.object(module.CHAT_UI, "show_status") as status, mock.patch.object(module.CHAT_UI, "open") as compose:
            module.plumbmate_chat(_connection=7)
        status.assert_called_once()
        compose.assert_not_called()
        self.assertIn("처리 확인이 없어요", status.call_args.args[2])
        self.assertEqual(len(list((self.mods_root / "PlumbMateBridge/requests").glob("*.json"))), 1)

    def test_corrupt_response_still_offers_dismiss_without_replay(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("first", _connection=7)
        with mock.patch.object(module, "_latest_response", side_effect=ValueError()), mock.patch.object(module.CHAT_UI, "show_status") as status:
            module.plumbmate_chat(_connection=7)
        self.assertIn("기록을 읽지 못했어요", status.call_args.args[2])
        status.call_args.args[5]()
        self.assertEqual(module.TURN_STATE.dismissed_id, module.TURN_STATE.request_id)

    def test_dismiss_suppresses_late_manual_and_automatic_reply_but_allows_new_send(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("first", _connection=7)
        first = module.TURN_STATE.request_id
        old_scope = dict(module.REPLY_DELIVERY.scope)
        module.plumbmate_dismiss(_connection=7)
        self.assertEqual(module.TURN_STATE.dismissed_id, first)
        self.assertEqual(module.REPLY_DELIVERY.state, "dismissed")
        with mock.patch.object(module, "_latest_response") as read, mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify:
            module.plumbmate_read(_connection=7)
            module._on_reply_expired(old_scope, 7)
            module._receive_reply(dict(old_scope, phase="completed"), 7)
        read.assert_not_called()
        notify.assert_not_called()
        module.plumbmate_chat("second", _connection=7)
        self.assertNotEqual(module.TURN_STATE.request_id, first)
        self.assertIsNone(module._reply_ineligible_reason())

    def test_old_dismiss_callback_cannot_affect_new_request_or_zone(self):
        module = self.boot()
        current, _, _ = self._ready_role_pair(module)
        module.plumbmate_chat("first", _connection=7)
        scope = module._pending_scope(7)
        module.plumbmate_dismiss(_connection=7)
        module.plumbmate_chat("second", _connection=7)
        with self.assertRaises(ValueError):
            module._dismiss_reply(7, scope)
        scope = module._pending_scope(7)
        current["zone"] = object()
        with self.assertRaises(ValueError):
            module._dismiss_reply(7, scope)

    def test_bare_chat_without_roles_routes_to_setup_without_send(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.ROLE_BINDING.clear()
        with mock.patch.object(module.ROLE_SETUP, "open") as setup, mock.patch.object(module, "_write_request") as write:
            module.plumbmate_chat(_connection=7)
        setup.assert_called_once()
        write.assert_not_called()

    def test_reopening_chat_recovers_ready_response_without_new_request(self):
        from plumbmate.engine import CompanionEngine
        from plumbmate.protocol import BridgePaths
        from plumbmate.providers import MockProvider
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("first", _connection=7)
        CompanionEngine(BridgePaths(self.mods_root / "PlumbMateBridge"), MockProvider()).process_one()
        with mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify, mock.patch.object(module.CHAT_UI, "open") as compose:
            module.plumbmate_chat(_connection=7)
        notify.assert_called_once()
        compose.assert_not_called()
        self.assertEqual(module.TURN_STATE.displayed_id, module.TURN_STATE.request_id)

    def test_bare_chat_opens_composer_without_creating_request(self):
        module = self.boot()
        self._ready_role_pair(module)
        with mock.patch.object(module.CHAT_UI, "open") as opened, mock.patch.object(module, "_write_request") as write:
            module.plumbmate_chat(_connection=7)
        opened.assert_called_once()
        write.assert_not_called()

    def test_composer_uses_shared_send_path_and_blocks_pending_console_turn(self):
        module = self.boot()
        self._ready_role_pair(module)
        with mock.patch.object(module.CHAT_UI, "open") as opened:
            module.plumbmate_chat(_connection=7)
        submit = opened.call_args.args[4]
        module.plumbmate_chat("console turn", _connection=7)
        with self.assertRaises(ValueError):
            submit("dialog turn")
        self.assertEqual(len(list((self.mods_root / "PlumbMateBridge/requests").glob("*.json"))), 1)

    def test_console_and_composer_apply_same_unicode_validation(self):
        module = self.boot()
        self._ready_role_pair(module)
        with mock.patch.object(module, "_write_request") as write:
            module.plumbmate_chat("hi\x00", _connection=7)
            with self.assertRaises(ValueError):
                module._submit_chat("hi\x00", 7, lambda _: None)
        write.assert_not_called()

    def test_composer_context_callback_detects_reassignment_and_zone_change(self):
        module = self.boot()
        current, _, _ = self._ready_role_pair(module)
        with mock.patch.object(module.CHAT_UI, "open") as opened:
            module.plumbmate_chat(_connection=7)
        scope, scope_now = opened.call_args.args[2:4]
        self.assertEqual(scope_now(), scope)
        current["zone"] = object()
        with self.assertRaises(ValueError):
            scope_now()

    def test_notification_is_shown_once_for_exact_turn(self):
        from plumbmate.engine import CompanionEngine
        from plumbmate.protocol import BridgePaths
        from plumbmate.providers import MockProvider
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("hello", _connection=7)
        CompanionEngine(BridgePaths(self.mods_root / "PlumbMateBridge"), MockProvider()).process_one()
        with mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify:
            module.plumbmate_read(_connection=7)
            module.plumbmate_read(_connection=7)
            notify.assert_called_once()

    def test_old_zone_reply_is_not_even_read_from_disk(self):
        module = self.boot()
        current, _, _ = self._ready_role_pair(module)
        module.plumbmate_chat("hello", _connection=7)
        current["zone"] = object()
        with mock.patch.object(module, "_latest_response") as read:
            module.plumbmate_read(_connection=7)
            read.assert_not_called()

    def test_automatic_reply_real_socket_to_notification_and_manual_dedup(self):
        from plumbmate.engine import CompanionEngine
        from plumbmate.protocol import BridgePaths
        from plumbmate.providers import MockProvider
        from plumbmate.turn_delivery import TurnDeliveryReader
        paths = BridgePaths(self.mods_root / "PlumbMateBridge")
        self.write_token()
        with EventSocketServer(TOKEN, delivery_reader=TurnDeliveryReader(paths)) as server:
            self.write_endpoint(server.port)
            module = self.boot()
            _, player, partner = self._ready_role_pair(module)
            module.plumbmate_chat("오늘 같이", "산책할까? 🐙", _connection=7)
            provider = mock.Mock(wraps=MockProvider())
            CompanionEngine(paths, provider).process_one()
            with mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify, \
                    mock.patch.object(module.plumbmate_turn_bridge, "_read", side_effect=AssertionError("game tick file IO")), \
                    mock.patch.object(module, "_latest_response", side_effect=AssertionError("manual file read")):
                self.assertTrue(self.tick_until(self.zone_class(), lambda: notify.called, timeout=5))
                notify.assert_called_once()
                self.assertIs(notify.call_args.args[0], partner.sim_info)
                self.assertIs(notify.call_args.kwargs["portrait"], partner.sim_info)
                self.assertIn("산책할까? 🐙", notify.call_args.args[2])
                module.plumbmate_read(_connection=7)
                for _ in range(100):
                    self.zone_class().update(0)
                notify.assert_called_once()
            self.assertEqual(module.TURN_STATE.displayed_id, module.TURN_STATE.request_id)
            provider.respond.assert_called_once()
            self.assertEqual(server.drain(), [])

    def test_pending_auto_watch_has_no_filesystem_or_thread_work_on_game_tick(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("hello", _connection=7)
        client = mock.Mock(supports_turn_delivery=True)
        client.take_turn_delivery.return_value = None
        module.TICK_DRIVER.set_binder(None)
        module.TICK_DRIVER.attach(client)
        module.TICK_DRIVER.resume()
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
                mock.patch("os.stat", side_effect=AssertionError("stat")), \
                mock.patch("os.makedirs", side_effect=AssertionError("mkdir")), \
                mock.patch("threading.Thread", side_effect=AssertionError("thread")):
            for _ in range(1000):
                self.assertTrue(module.TICK_DRIVER.on_tick())
        client.request_turn_delivery.assert_called_once()
        self.assertEqual(module.TICK_DRIVER.stats["errors"], 0)

    def test_automatic_reply_context_and_manual_first_fences(self):
        for case in ("zone", "roles", "off_lot", "expired", "manual_first"):
            module = self.boot()
            current, _, partner = self._ready_role_pair(module)
            module.plumbmate_chat("hello", _connection=7)
            client = mock.Mock(supports_turn_delivery=True)
            client.take_turn_delivery.return_value = dict(module.REPLY_DELIVERY.scope, phase="completed")
            if case == "zone":
                current["zone"] = object()
            elif case == "roles":
                self._ready_role_pair(module)
            elif case == "off_lot":
                partner.sim_info.get_sim_instance = lambda: None
            elif case == "expired":
                module.TURN_STATE.sent_at -= module.TURN_DISPLAY_TTL_SECONDS
            else:
                module.TURN_STATE.displayed_id = module.TURN_STATE.request_id
            with self.subTest(case=case), mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify:
                module._on_reply_tick(client)
                notify.assert_not_called()
            client.take_turn_delivery.assert_not_called()
            self.assertIsNone(module.REPLY_DELIVERY.scope)

    def test_automatic_failure_notice_once_and_new_turn_can_be_submitted(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("hello", _connection=7)
        previous = module.TURN_STATE.request_id
        client = mock.Mock(supports_turn_delivery=True)
        client.take_turn_delivery.return_value = dict(module.REPLY_DELIVERY.scope,
                                                       phase="failed", reason_code="spending_blocked")
        with mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify:
            module._on_reply_tick(client)
            module._on_reply_tick(client)
            notify.assert_called_once()
            self.assertIn("금액 한도", notify.call_args.args[2])
            self.assertNotIn("portrait", notify.call_args.kwargs)
        module.plumbmate_chat("new explicit request", _connection=7)
        self.assertNotEqual(previous, module.TURN_STATE.request_id)

    def test_expiry_notice_is_system_only_once_without_file_io_and_allows_new_send(self):
        module = self.boot()
        _, player, _ = self._ready_role_pair(module)
        module.plumbmate_chat("pending", _connection=7)
        previous = module.TURN_STATE.request_id
        module.TURN_STATE.sent_at -= 180
        module.REPLY_DELIVERY._expires_at = 0
        with mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify, \
                mock.patch("builtins.open", side_effect=AssertionError("tick file IO")):
            for _ in range(100):
                module._on_reply_tick(None)
            notify.assert_called_once()
            self.assertIs(notify.call_args.args[0], player.sim_info)
            self.assertEqual(notify.call_args.args[1], "PlumbMate · 연결 안내")
            self.assertIn("별도 호출", notify.call_args.args[2])
        module.plumbmate_chat("new explicit request", _connection=7)
        self.assertNotEqual(previous, module.TURN_STATE.request_id)

    def test_expiry_notice_never_crosses_context_or_repeats_completed_turn(self):
        for case in ("zone", "roles", "off_lot", "new_turn", "displayed"):
            module = self.boot()
            current, _, partner = self._ready_role_pair(module)
            module.plumbmate_chat("pending", _connection=7)
            module.REPLY_DELIVERY._expires_at = 0
            if case == "zone":
                current["zone"] = object()
            elif case == "roles":
                self._ready_role_pair(module)
            elif case == "off_lot":
                partner.sim_info.get_sim_instance = lambda: None
            elif case == "new_turn":
                module.TURN_STATE.request_id = "f" * 32
            else:
                module.TURN_STATE.displayed_id = module.TURN_STATE.request_id
            with self.subTest(case=case), mock.patch.object(module.plumbmate_role_setup_ui, "notify") as notify:
                module._on_reply_tick(None)
                module._on_reply_tick(None)
                notify.assert_not_called()
            self.assertIsNone(module.REPLY_DELIVERY.scope)

    def test_expiry_ui_and_console_failures_cannot_escape_tick_or_retry(self):
        module = self.boot()
        self._ready_role_pair(module)
        module.plumbmate_chat("pending", _connection=7)
        module.REPLY_DELIVERY._expires_at = 0
        with mock.patch.object(module.plumbmate_role_setup_ui, "notify", side_effect=RuntimeError()) as notify, \
                mock.patch.object(module.sims4.commands, "CheatOutput", side_effect=RuntimeError()):
            module._on_reply_tick(None)
            module._on_reply_tick(None)
            notify.assert_called_once()
        self.assertEqual(module.REPLY_DELIVERY.state, "expiry_notice_failed")

    def test_reply_after_reassignment_or_ttl_is_not_displayed(self):
        for case in ("reassignment", "ttl"):
            module = self.boot()
            self._ready_role_pair(module)
            module.plumbmate_chat("hello", _connection=7)
            if case == "reassignment":
                self._ready_role_pair(module)
            else:
                module.TURN_STATE.sent_at -= module.TURN_DISPLAY_TTL_SECONDS
            with self.subTest(case=case), mock.patch.object(module, "_latest_response") as read:
                module.plumbmate_read(_connection=7)
                read.assert_not_called()

    def test_partner_off_lot_blocks_request_without_clearing_roles(self):
        module = self.boot()
        _, _, partner = self._ready_role_pair(module)
        partner.sim_info.get_sim_instance = lambda: None
        binding_id = module.ROLE_BINDING.binding_id
        with mock.patch.object(module, "_write_request") as write:
            module.plumbmate_chat("hello", _connection=7)
            write.assert_not_called()
        self.assertEqual(module.ROLE_BINDING.binding_id, binding_id)

    def test_chat_without_client_manager_is_a_normal_refusal(self):
        module = self.boot()
        lines = []
        with mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_chat("hello", "there", _connection=7)
        self.assertTrue(any("active Sim not found" in line for line in lines))

    def test_overlong_chat_is_refused_without_silent_truncation(self):
        module = self.boot()
        lines = []
        with mock.patch.object(module, "_active_sim", return_value=object()), \
                mock.patch.object(module, "_write_request") as write, \
                mock.patch.object(module.sims4.commands, "CheatOutput", return_value=lines.append):
            module.plumbmate_chat("가" * 1001, _connection=7)
        write.assert_not_called()
        self.assertTrue(any("1000" in line for line in lines))

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


if __name__ == "__main__":
    unittest.main()
