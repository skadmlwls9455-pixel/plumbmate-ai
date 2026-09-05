"""Python 3.7-compatible game commands for the filesystem bridge."""

import datetime
import json
import os
import time
import uuid

import services
import sims4.commands


PROTOCOL_VERSION = "0.1"
ARCHIVE_SUFFIX = ".ts4script"


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


@sims4.commands.Command("plumbmate.status", command_type=sims4.commands.CommandType.Live)
def plumbmate_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    root = _ensure_directories()
    output("PlumbMate prototype 0.1 | bridge: {}".format(root))


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

