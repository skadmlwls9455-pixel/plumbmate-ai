"""Python 3.7-compatible game commands for the filesystem bridge."""

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
try:
    import plumbmate_bridge_client
except Exception:
    plumbmate_bridge_client = None
try:
    import plumbmate_tick_driver
except Exception:
    plumbmate_tick_driver = None
try:
    import zone
except Exception:
    zone = None


PROTOCOL_VERSION = "0.1"
ARCHIVE_SUFFIX = ".ts4script"
DEFAULT_BRIDGE_PORT = 47651

# Initialized at import; idle until plumbmate.bridge.start binds a client.
TICK_DRIVER = plumbmate_tick_driver.TickDriver() if plumbmate_tick_driver else None


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


def _bridge_available():
    return (plumbmate_bridge_client is not None and plumbmate_tick_driver is not None
            and zone is not None and TICK_DRIVER is not None)


def _hook_installed():
    return bool(zone is not None and plumbmate_tick_driver is not None
                and plumbmate_tick_driver.is_hooked(zone.Zone, "update"))


@sims4.commands.Command("plumbmate.bridge.start", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_start(_connection=None):
    """Bind a client and hook Zone.update so every tick pumps it."""
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
        # Zone.update(self, absolute_ticks); the wrapper runs the original first.
        plumbmate_tick_driver.install_tick_hook(zone.Zone, TICK_DRIVER, "update")
    except Exception as exc:
        output("PlumbMate bridge start failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge started: {}:{} | tick hook installed".format(host, port))


@sims4.commands.Command("plumbmate.bridge.stop", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_stop(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    try:
        plumbmate_tick_driver.remove_tick_hook(zone.Zone, "update")
        client = TICK_DRIVER.detach()
        if client is not None:
            client.close()
    except Exception as exc:
        output("PlumbMate bridge stop failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge stopped; Zone.update restored.")


@sims4.commands.Command("plumbmate.bridge.status", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    client = TICK_DRIVER.client
    output("hook={} active={} driver={}".format(
        _hook_installed(), TICK_DRIVER.active, TICK_DRIVER.stats))
    if client is None:
        output("No client bound. Run plumbmate.bridge.start.")
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
    output("socket bridge: {} | tick hook: {}".format(
        "available" if _bridge_available() else "unavailable", _hook_installed()))


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

