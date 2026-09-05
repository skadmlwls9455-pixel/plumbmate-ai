"""Python 3.7-compatible game commands for the filesystem bridge.

This module is published as ``plumbmate_boot.pyc`` and the game imports it at
mod load. Importing it installs the tick hook and starts binding the bridge on
its own, so a normal session needs no cheat command at all. The manual
``plumbmate.bridge.*`` commands stay, because they are how a session is
inspected, stopped, or pointed somewhere by hand.
"""

import datetime
import json
import os
import time
import uuid

import services
import sims4.commands

# The build publishes these as root-level modules, like the diagnostic probe.
# A partial or stale install must degrade to the filesystem bridge, not break
# the whole mod at import, so both imports are guarded.
# The import error itself is the answer to "does socket work in the game?",
# so it is kept rather than collapsed into a None check.
try:
    import plumbmate_bridge_client
    _BRIDGE_CLIENT_ERROR = None
except Exception as _exc:
    plumbmate_bridge_client = None
    _BRIDGE_CLIENT_ERROR = "{}: {}".format(type(_exc).__name__, _exc)
try:
    import plumbmate_tick_driver
    _TICK_DRIVER_ERROR = None
except Exception as _exc:
    plumbmate_tick_driver = None
    _TICK_DRIVER_ERROR = "{}: {}".format(type(_exc).__name__, _exc)
try:
    import zone
    _ZONE_ERROR = None
except Exception as _exc:
    zone = None
    _ZONE_ERROR = "{}: {}".format(type(_exc).__name__, _exc)


PROTOCOL_VERSION = "0.1"
ARCHIVE_SUFFIX = ".ts4script"
DEFAULT_BRIDGE_PORT = 47651
AUTOSTART_DISABLE_FILE = "autostart-disabled"

# Initialized at import; idle until a client is bound, by autostart or by hand.
TICK_DRIVER = plumbmate_tick_driver.TickDriver() if plumbmate_tick_driver else None

# The endpoint the bound client was built for, so the binder can tell a live
# client from one aimed at a companion that has since restarted on a new port.
_BOUND_ENDPOINT = None
AUTOSTART_STATE = "not_attempted"


def _mod_directory():
    source_path = os.path.abspath(__file__)
    marker = source_path.lower().find(ARCHIVE_SUFFIX)
    if marker >= 0:
        archive_path = source_path[: marker + len(ARCHIVE_SUFFIX)]
        return os.path.dirname(archive_path)
    return os.path.dirname(os.path.dirname(os.path.dirname(source_path)))


def _bridge_directory():
    return os.path.join(_mod_directory(), "PlumbMateBridge")


def _ensure_directories():
    root = _bridge_directory()
    for name in ("requests", "responses", "archive", "state"):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            os.makedirs(path)
    return root


def _safe_text(value, fallback=""):
    try:
        return str(value)
    except Exception:
        return fallback


def _sim_name(sim_info):
    full_name = getattr(sim_info, "full_name", None)
    if full_name:
        return _safe_text(full_name)
    first_name = _safe_text(getattr(sim_info, "first_name", ""))
    last_name = _safe_text(getattr(sim_info, "last_name", ""))
    return (first_name + " " + last_name).strip() or "Unknown Sim"


def _active_sim(connection):
    client = services.client_manager().get(connection)
    return getattr(client, "active_sim", None) if client is not None else None


def _snapshot(sim):
    sim_info = sim.sim_info
    actor = {
        "sim_id": int(getattr(sim_info, "sim_id", getattr(sim, "id", 0))),
        "name": _sim_name(sim_info),
        "age": _safe_text(getattr(sim_info, "age", "")),
        "gender": _safe_text(getattr(sim_info, "gender", "")),
    }
    try:
        mood = sim.get_mood()
        actor["mood"] = _safe_text(getattr(mood, "__name__", mood))
    except Exception:
        pass

    household = []
    try:
        for member in sim.household.sim_info_gen():
            household.append({
                "sim_id": int(getattr(member, "sim_id", 0)),
                "name": _sim_name(member),
            })
            if len(household) >= 12:
                break
    except Exception:
        household = [{"sim_id": actor["sim_id"], "name": actor["name"]}]
    return actor, household


def _write_request(sim, message):
    root = _ensure_directories()
    actor, household = _snapshot(sim)
    request_id = uuid.uuid4().hex
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "kind": "turn",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "player_message": _safe_text(message)[:1000],
        "session_id": "household-{}".format(getattr(sim.household, "id", "local")),
        "game": {"adapter": "plumbmate-ts4-prototype"},
        "actor": actor,
        "household": household,
        "recent_events": [],
        "memory_summary": "",
    }
    target = os.path.join(root, "requests", request_id + ".json")
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, target)
    return target


def _latest_response():
    root = _ensure_directories()
    response_dir = os.path.join(root, "responses")
    candidates = []
    for name in os.listdir(response_dir):
        if name.endswith(".json"):
            path = os.path.join(response_dir, name)
            candidates.append((os.path.getmtime(path), path))
    if not candidates:
        return None
    path = max(candidates)[1]
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _state_file(name):
    return os.path.join(_ensure_directories(), "state", name)


def _read_token():
    with open(_state_file("bridge-token"), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _read_endpoint():
    """Discover the companion's port; the file carries no secret."""
    try:
        with open(_state_file("bridge-endpoint.json"), "r", encoding="utf-8") as handle:
            endpoint = json.load(handle)
        return endpoint.get("host", "127.0.0.1"), int(endpoint["port"])
    except Exception:
        return "127.0.0.1", DEFAULT_BRIDGE_PORT


def _bridge_reason():
    """Return (available, reason). Importing socket is only half the question."""
    if plumbmate_bridge_client is None:
        return False, "bridge_client import failed: {}".format(_BRIDGE_CLIENT_ERROR)
    if plumbmate_tick_driver is None or TICK_DRIVER is None:
        return False, "tick_driver import failed: {}".format(_TICK_DRIVER_ERROR)
    if zone is None:
        return False, "zone import failed: {}".format(_ZONE_ERROR)
    # A loaded wrapper does not prove the extension can make a socket.
    try:
        network = plumbmate_bridge_client.socket
        probe = network.socket(network.AF_INET, network.SOCK_STREAM)
        probe.close()
    except Exception as exc:
        return False, "socket creation failed: {}: {}".format(type(exc).__name__, exc)
    return True, "socket created and closed"


def _bridge_available():
    return _bridge_reason()[0]


def _hook_installed():
    return bool(zone is not None and plumbmate_tick_driver is not None
                and plumbmate_tick_driver.is_hooked(zone.Zone, "update"))


def _live_endpoint():
    """Return (host, port) only while the companion is publishing an endpoint.

    ``plumbmate listen`` writes this file on start and deletes it on exit, and
    it asks the kernel for a free port by default, so the file is both the
    liveness signal and the only way to learn the current port. Its absence is
    the ordinary case of "the companion is not running", not an error.
    """
    try:
        with open(_state_file("bridge-endpoint.json"), "r", encoding="utf-8") as handle:
            endpoint = json.load(handle)
        return endpoint.get("host", "127.0.0.1"), int(endpoint["port"])
    except Exception:
        return None


def _autostart_disabled():
    """A file beside the token turns autostart off before the game loads."""
    try:
        return os.path.exists(_state_file(AUTOSTART_DISABLE_FILE))
    except Exception:
        return False


def _autostart_binder(current):
    """Supply the tick driver with a client. Returns None to change nothing.

    Called from the tick hook on the engine thread every few seconds, so it
    only stats a couple of files and builds a client when the endpoint it is
    bound to is not the one the companion is currently publishing.
    """
    global _BOUND_ENDPOINT, AUTOSTART_STATE
    endpoint = _live_endpoint()
    if endpoint is None:
        AUTOSTART_STATE = "waiting_for_companion"
        return None
    if current is not None and _BOUND_ENDPOINT == endpoint:
        AUTOSTART_STATE = "bound"
        return None
    try:
        token = _read_token()
    except Exception:
        AUTOSTART_STATE = "waiting_for_token"
        return None
    client = plumbmate_bridge_client.BridgeClient(token, endpoint[1], host=endpoint[0])
    if current is not None:
        # The endpoint moved; the old socket has nothing left to reach.
        current.close()
    _BOUND_ENDPOINT = endpoint
    AUTOSTART_STATE = "bound"
    return client


def start_autostart():
    """Hook Zone.update at mod load and let the binder connect when it can.

    Installing the hook with nothing bound costs one wrapper call per tick and
    connects to nothing; the driver stays idle until the companion appears.
    Any failure here is reported through plumbmate.status rather than raised,
    because an exception during mod import would take the whole mod down.
    """
    global AUTOSTART_STATE
    if _autostart_disabled():
        AUTOSTART_STATE = "disabled_by_file"
        return False
    available, reason = _bridge_reason()
    if not available:
        AUTOSTART_STATE = "unavailable: {}".format(reason)
        return False
    try:
        TICK_DRIVER.set_binder(_autostart_binder)
        plumbmate_tick_driver.install_tick_hook(zone.Zone, TICK_DRIVER, "update")
    except Exception as exc:
        AUTOSTART_STATE = "hook_failed: {}: {}".format(type(exc).__name__, exc)
        return False
    AUTOSTART_STATE = "waiting_for_companion"
    return True


@sims4.commands.Command("plumbmate.bridge.start", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_start(_connection=None):
    """Bind a client now and hook Zone.update so every tick pumps it.

    Autostart normally does this at mod load; this stays for a forced rebind.
    """
    global _BOUND_ENDPOINT
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    try:
        token = _read_token()
    except Exception:
        output("PlumbMate: no bridge token. Run 'plumbmate token' in the companion.")
        return
    host, port = _read_endpoint()
    try:
        previous = TICK_DRIVER.detach()
        if previous is not None:
            previous.close()
        TICK_DRIVER.attach(plumbmate_bridge_client.BridgeClient(token, port, host=host))
        # A hand-bound client is only "current" if it matches the live endpoint;
        # otherwise leave the marker unset so the binder replaces it later.
        _BOUND_ENDPOINT = _live_endpoint()
        # Zone.update(self, absolute_ticks); the wrapper runs the original first.
        plumbmate_tick_driver.install_tick_hook(zone.Zone, TICK_DRIVER, "update")
    except Exception as exc:
        output("PlumbMate bridge start failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge started: {}:{} | tick hook installed".format(host, port))


@sims4.commands.Command("plumbmate.bridge.stop", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_stop(_connection=None):
    """Unhook and unbind. Also stops autostart, which only runs from the hook."""
    global _BOUND_ENDPOINT, AUTOSTART_STATE
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    try:
        plumbmate_tick_driver.remove_tick_hook(zone.Zone, "update")
        client = TICK_DRIVER.detach()
        if client is not None:
            client.close()
        _BOUND_ENDPOINT = None
        AUTOSTART_STATE = "stopped_by_command"
    except Exception as exc:
        output("PlumbMate bridge stop failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge stopped; Zone.update restored. "
           "Autostart resumes on the next game start.")


@sims4.commands.Command("plumbmate.bridge.status", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    client = TICK_DRIVER.client
    output("hook={} active={} autostart={}".format(
        _hook_installed(), TICK_DRIVER.active, AUTOSTART_STATE))
    output("driver={}".format(TICK_DRIVER.stats))
    if TICK_DRIVER.last_bind_error:
        output("last_bind_error={}".format(_safe_text(TICK_DRIVER.last_bind_error)))
    if client is None:
        output("No client bound. Start 'plumbmate listen' in the companion, "
               "or run plumbmate.bridge.start.")
        return
    output("state={} queued={} awaiting_ack={} backoff={}s".format(
        client.state, client.queued, client.awaiting_ack, client.backoff_seconds))
    output("client={}".format(client.stats))
    if client.last_error:
        output("last_error={}".format(_safe_text(client.last_error)))
    if TICK_DRIVER.disabled_reason:
        output("driver disabled: {}".format(TICK_DRIVER.disabled_reason))


@sims4.commands.Command("plumbmate.bridge.ping", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_ping(_connection=None):
    """Queue one heartbeat. The tick hook does the sending."""
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available() or TICK_DRIVER.client is None:
        output("PlumbMate: no client bound. Run plumbmate.bridge.start.")
        return
    event_id = TICK_DRIVER.client.queue_event("heartbeat", {"source": "cheat_console"})
    output("queued heartbeat: {}".format(event_id or "outbox full"))


@sims4.commands.Command("plumbmate.status", command_type=sims4.commands.CommandType.Live)
def plumbmate_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    root = _ensure_directories()
    output("PlumbMate prototype 0.1 | bridge: {}".format(root))
    available, reason = _bridge_reason()
    output("socket bridge: {} ({})".format(
        "available" if available else "unavailable", reason))
    output("tick hook: {}".format("installed" if _hook_installed() else "not installed"))
    output("autostart: {}".format(AUTOSTART_STATE))


@sims4.commands.Command("plumbmate.chat", command_type=sims4.commands.CommandType.Live)
def plumbmate_chat(message: str = "", _connection=None):
    output = sims4.commands.CheatOutput(_connection)
    sim = _active_sim(_connection)
    if sim is None:
        output("PlumbMate: active Sim not found. Load a household first.")
        return
    try:
        target = _write_request(sim, message)
        output("PlumbMate request queued: {}".format(os.path.basename(target)))
    except Exception as exc:
        output("PlumbMate request failed: {}".format(_safe_text(exc)))


@sims4.commands.Command("plumbmate.read", command_type=sims4.commands.CommandType.Live)
def plumbmate_read(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        response = _latest_response()
        if response is None:
            output("PlumbMate: no response yet.")
            return
        action = response.get("action", {})
        output(_safe_text(response.get("partner_message", "")))
        output("Suggested action: {} | {}".format(
            _safe_text(action.get("name", "suggest_only")),
            _safe_text(action.get("reason", "")),
        ))
        output("Automatic execution is disabled in prototype 0.1.")
    except Exception as exc:
        output("PlumbMate response failed: {}".format(_safe_text(exc)))


# Mod load runs this. It must not raise: an exception during import would leave
# the game without the commands above, which are the only way to diagnose it.
try:
    start_autostart()
except Exception as _exc:
    AUTOSTART_STATE = "autostart_raised: {}: {}".format(type(_exc).__name__, _exc)
