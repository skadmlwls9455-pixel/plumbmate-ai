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
import re
import time
import uuid

import services
import sims4.commands
try:
    import sims4.resources as _sims4_resources
    _RESOURCES_ERROR = None
except Exception as _exc:  # the statistic manager is optional for a snapshot
    _sims4_resources = None
    _RESOURCES_ERROR = "{}: {}".format(type(_exc).__name__, _exc)
try:
    import plumbmate_turn_bridge
except Exception:
    plumbmate_turn_bridge = None
try:
    import plumbmate_role_binding
    import plumbmate_role_setup_ui
except Exception:
    plumbmate_role_binding = None
    plumbmate_role_setup_ui = None
try:
    import plumbmate_chat_input
except Exception:
    plumbmate_chat_input = None
try:
    import plumbmate_chat_ui
except Exception:
    plumbmate_chat_ui = None
try:
    import plumbmate_reply_delivery
except Exception:
    plumbmate_reply_delivery = None

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
MOD_VERSION = "0.1.1-alpha.5"
DEFAULT_BRIDGE_PORT = 47651
AUTOSTART_DISABLE_FILE = "autostart-disabled"

# Initialized at import; idle until a client is bound, by autostart or by hand.
TICK_DRIVER = plumbmate_tick_driver.TickDriver() if plumbmate_tick_driver else None

# Configuration is read only at mod load or by an explicit start command.
# The tick path never opens/stats files, creates directories, or spawns workers.
_BOUND_ENDPOINT = None
_BOOTSTRAP_CLIENT = None
AUTOSTART_STATE = "not_attempted"
_LAST_REQUEST_ID = None
_LAST_ROLE_BINDING = None
_LAST_DISPLAYED_ID = None
_LAST_SENT_AT = 0.0
TURN_DISPLAY_TTL_SECONDS = 180.0
ROLE_BINDING = plumbmate_role_binding.RoleBinding() if plumbmate_role_binding else None
ROLE_SETUP = plumbmate_role_setup_ui.RoleSetupUI() if plumbmate_role_setup_ui else None
CHAT_UI = plumbmate_chat_ui.ChatUI() if plumbmate_chat_ui else None
REPLY_DELIVERY = plumbmate_reply_delivery.ReplyDelivery() if plumbmate_reply_delivery else None


def _mod_directory():
    source_path = os.path.abspath(__file__)
    # Match on the original string: Unicode lowercasing can change its length.
    marker = re.search(r"\.ts4script(?=[/\\]|$)", source_path, re.IGNORECASE | re.ASCII)
    if marker is not None:
        archive_path = source_path[:marker.end()]
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
    try:
        manager = services.client_manager()
        client = manager.get(connection) if manager is not None else None
        return getattr(client, "active_sim", None) if client is not None else None
    except Exception:
        # Main menu / household transitions have no usable active client.
        return None


# Motive commodities by tuning name. Levels run -100 (crisis) to 100.
# NOT_TESTED in-game yet: every lookup is guarded, and a snapshot without
# motives is still a valid request, so a wrong name costs a field, not a turn.
_MOTIVE_STATISTICS = (
    ("hunger", "motive_Hunger"), ("energy", "motive_Energy"), ("social", "motive_Social"),
    ("fun", "motive_Fun"), ("hygiene", "motive_Hygiene"), ("bladder", "motive_Bladder"),
)
_MOTIVE_TYPES = None


def _motive_types():
    """Resolve motive statistic tunings once they are available.

    Only a non-empty result is cached: the manager may not be populated yet
    when the first snapshot runs, and caching that miss would leave motives
    absent for the whole game session.
    """
    global _MOTIVE_TYPES
    if _MOTIVE_TYPES:
        return _MOTIVE_TYPES
    resolved = {}
    try:
        manager = services.get_instance_manager(_sims4_resources.Types.STATISTIC)
        by_name = {}
        for tuning in manager.types.values():
            by_name[getattr(tuning, "__name__", "")] = tuning
        for key, tuning_name in _MOTIVE_STATISTICS:
            if tuning_name in by_name:
                resolved[key] = by_name[tuning_name]
    except Exception:
        resolved = {}
    if resolved:
        _MOTIVE_TYPES = resolved
    return resolved


def _motives(sim):
    """Current motive levels clamped to the contract's [-100, 100]."""
    levels = {}
    try:
        tracker = getattr(sim, "commodity_tracker", None)
        if tracker is None:
            return levels
        for key, tuning in _motive_types().items():
            try:
                statistic = tracker.get_statistic(tuning)
                if statistic is None:
                    continue
                value = float(statistic.get_value())
            except Exception:
                continue
            if value != value:  # NaN would clamp to +100 and read as no need
                continue
            levels[key] = max(-100.0, min(100.0, value))
    except Exception:
        return {}
    return levels


def _buffs(sim):
    """Names of active buffs, bounded to what the contract allows."""
    names = []
    try:
        component = getattr(sim, "Buffs", None) or getattr(getattr(sim, "sim_info", None), "Buffs", None)
        if component is None:
            return names
        for buff_type in component.get_active_buff_types():
            name = _safe_text(getattr(buff_type, "__name__", buff_type))[:60]
            if name and name not in names:
                names.append(name)
            if len(names) >= 12:
                break
    except Exception:
        return []
    return names


def _snapshot(sim):
    sim_info = sim.sim_info
    actor = {
        "sim_id": int(getattr(sim_info, "sim_id", getattr(sim, "id", 0))),
        "name": _sim_name(sim_info)[:128],
        "age": _safe_text(getattr(sim_info, "age", ""))[:40],
        "gender": _safe_text(getattr(sim_info, "gender", ""))[:40],
    }
    try:
        mood = sim.get_mood()
        actor["mood"] = _safe_text(getattr(mood, "__name__", mood))[:80]
    except Exception:
        pass
    motives = _motives(sim)
    if motives:
        actor["motives"] = motives
    buffs = _buffs(sim)
    if buffs:
        actor["buffs"] = buffs

    household = []
    try:
        for member in sim.household.sim_info_gen():
            household.append({
                "sim_id": int(getattr(member, "sim_id", 0)),
                "name": _sim_name(member)[:128],
            })
            if len(household) >= 12:
                break
    except Exception:
        household = [{"sim_id": actor["sim_id"], "name": actor["name"]}]
    return actor, household


def _role_environment(connection):
    sim = _active_sim(connection)
    if sim is None:
        raise ValueError("가족을 먼저 불러와 주세요.")
    household = getattr(sim, "household", None)
    if household is None:
        raise ValueError("현재 가족 정보를 확인할 수 없어요.")
    members = list(household.sim_info_gen())
    if len(members) > 12:
        raise ValueError("이 시험 버전은 가족 구성원 12명까지 지원해요.")
    return services.current_zone(), household, members, sim.sim_info


def _bound_player(connection):
    if ROLE_BINDING is None:
        raise ValueError("역할 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    zone_obj, household, members, _ = _role_environment(connection)
    player, partner = ROLE_BINDING.resolve(zone_obj, household, members)
    selected = _active_sim(connection)
    sim = selected if selected.sim_info is player else player.get_sim_instance()
    if sim is None:
        raise ValueError("플레이어 심이 현재 로트에 없어요. 돌아온 뒤 대화해 주세요.")
    if partner.get_sim_instance() is None:
        raise ValueError("AI 파트너가 현재 로트에 없어요. 돌아온 뒤 대화해 주세요.")
    return sim, partner


def _write_request(sim, message):
    global _LAST_REQUEST_ID, _LAST_ROLE_BINDING, _LAST_SENT_AT
    if plumbmate_turn_bridge is None:
        raise ValueError("Turn bridge module missing; reinstall the complete PlumbMate archive")
    root = _ensure_directories()
    actor, household = _snapshot(sim)
    request_id = uuid.uuid4().hex
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "kind": "turn",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "player_message": _safe_text(message)[:1000],
        # Until save lineage receipts are implemented, a household ID cannot
        # authorize cross-save memory reuse. This alpha isolates each turn.
        "session_id": "turn-" + request_id,
        "game": {"adapter": "plumbmate-ts4-prototype"},
        "actor": actor,
        "roles": ROLE_BINDING.wire(),
        "household": household,
        "recent_events": [],
        "memory_summary": "",
    }
    target = plumbmate_turn_bridge.write_request(root, payload)
    _LAST_REQUEST_ID = request_id
    _LAST_ROLE_BINDING = ROLE_BINDING.binding_id
    _LAST_SENT_AT = time.monotonic()
    return target


def _latest_response():
    if plumbmate_turn_bridge is None:
        raise ValueError("Turn bridge module missing; reinstall the complete PlumbMate archive")
    return plumbmate_turn_bridge.read_response(_bridge_directory(), _LAST_REQUEST_ID)

def _state_file(name):
    return os.path.join(_bridge_directory(), "state", name)


def _read_token():
    with open(_state_file("bridge-token"), "r", encoding="utf-8") as handle:
        token = handle.read(4097).strip()
    if len(token) > 4096:
        raise ValueError("Bridge token file is too large")
    return token


def _read_endpoint():
    """Read persistent configuration, NOT a file-based liveness signal."""
    try:
        with open(_state_file("bridge-endpoint.json"), "r", encoding="utf-8") as handle:
            endpoint = json.loads(handle.read(4097))
        return endpoint.get("host", "127.0.0.1"), int(endpoint["port"])
    except FileNotFoundError:
        return "127.0.0.1", DEFAULT_BRIDGE_PORT


def _load_bootstrap():
    token = _read_token()
    host, port = _read_endpoint()
    return plumbmate_bridge_client.BridgeClient(token, port, host=host), (host, port)


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


def _autostart_disabled():
    """A file beside the token turns autostart off before the game loads."""
    try:
        return os.path.exists(_state_file(AUTOSTART_DISABLE_FILE))
    except Exception:
        return False


def _autostart_binder(current):
    """Memory-only binding. Socket state, not a file, determines liveness."""
    global AUTOSTART_STATE
    if _BOOTSTRAP_CLIENT is None:
        return None
    AUTOSTART_STATE = "bound" if current is not None and current.connected else "waiting_for_companion"
    return _BOOTSTRAP_CLIENT if current is None else None


def start_autostart():
    """Hook Zone.update at mod load and let the binder connect when it can.

    Read configuration once here. A configured client retries the same numeric
    loopback endpoint with backoff even when the companion starts later.
    Creating/rotating the token or changing the port later requires an explicit
    plumbmate.bridge.start (or a game restart), never filesystem polling.
    Any failure here is reported through plumbmate.status rather than raised,
    because an exception during mod import would take the whole mod down.
    """
    global AUTOSTART_STATE, _BOOTSTRAP_CLIENT, _BOUND_ENDPOINT
    if _autostart_disabled():
        AUTOSTART_STATE = "disabled_by_file"
        return False
    available, reason = _bridge_reason()
    if not available:
        AUTOSTART_STATE = "unavailable: {}".format(reason)
        return False
    try:
        _BOOTSTRAP_CLIENT, _BOUND_ENDPOINT = _load_bootstrap()
        AUTOSTART_STATE = "waiting_for_companion"
    except FileNotFoundError:
        AUTOSTART_STATE = "waiting_for_token"
    except Exception:
        AUTOSTART_STATE = "invalid_bridge_config"
    try:
        TICK_DRIVER.set_binder(_autostart_binder)
        TICK_DRIVER.resume()
        plumbmate_tick_driver.install_tick_hook(zone.Zone, TICK_DRIVER, "update")
    except Exception as exc:
        AUTOSTART_STATE = "hook_failed: {}: {}".format(type(exc).__name__, exc)
        return False
    return True


@sims4.commands.Command("plumbmate.bridge.start", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_start(_connection=None):
    """Bind a client now and hook Zone.update so every tick pumps it.

    Autostart normally does this at mod load; this stays for a forced rebind.
    """
    global _BOUND_ENDPOINT, _BOOTSTRAP_CLIENT, AUTOSTART_STATE
    output = sims4.commands.CheatOutput(_connection)
    if not _bridge_available():
        output("PlumbMate: socket bridge unavailable in this build.")
        return
    try:
        client, endpoint = _load_bootstrap()
    except FileNotFoundError:
        output("PlumbMate: no bridge token. Run 'plumbmate token' in the companion.")
        return
    except Exception:
        output("PlumbMate: invalid bridge configuration. Check the local token and loopback port.")
        return
    try:
        # Install/reuse only our wrapper; do not modify another mod's chain.
        plumbmate_tick_driver.install_tick_hook(zone.Zone, TICK_DRIVER, "update")
        previous = TICK_DRIVER.detach()
        if previous is not None:
            # Events queued but never sent carry no duplication risk; they
            # follow the replacement instead of vanishing with the old client.
            client.adopt_outbox(previous)
            previous.close()
        _BOOTSTRAP_CLIENT, _BOUND_ENDPOINT = client, endpoint
        TICK_DRIVER.attach(client)
        TICK_DRIVER.set_binder(_autostart_binder)
        TICK_DRIVER.resume()
        AUTOSTART_STATE = "waiting_for_companion"
    except Exception as exc:
        output("PlumbMate bridge start failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge started: {}:{} | tick hook installed".format(*endpoint))


@sims4.commands.Command("plumbmate.bridge.stop", command_type=sims4.commands.CommandType.Live)
def plumbmate_bridge_stop(_connection=None):
    """Stop independently of socket availability or third-party wrapping."""
    global _BOUND_ENDPOINT, _BOOTSTRAP_CLIENT, AUTOSTART_STATE
    output = sims4.commands.CheatOutput(_connection)
    AUTOSTART_STATE = "stopped_by_command"
    if REPLY_DELIVERY is not None:
        REPLY_DELIVERY.stop("stopped")
    _BOUND_ENDPOINT = None
    bootstrap, _BOOTSTRAP_CLIENT = _BOOTSTRAP_CLIENT, None
    try:
        client = None
        if TICK_DRIVER is not None:
            TICK_DRIVER.suspend()
            TICK_DRIVER.set_binder(None)
            client = TICK_DRIVER.detach()
        restored = bool(zone is not None and plumbmate_tick_driver is not None
                        and plumbmate_tick_driver.remove_tick_hook(zone.Zone, "update"))
        if client is not None:
            client.close()
        if bootstrap is not None and bootstrap is not client:
            bootstrap.close()
    except Exception as exc:
        output("PlumbMate bridge stop failed: {}".format(_safe_text(exc)))
        return
    output("PlumbMate bridge stopped; {}. Use plumbmate.bridge.start to resume, "
           "or restart the game.".format(
               "owned hook restored" if restored else "foreign hooks left unchanged"))


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
    payload = {"source": "cheat_console"}
    sim = _active_sim(_connection)
    if sim is not None:
        # Audit metadata only: heartbeats are never stored as memories, and a
        # household id cannot authorize cross-save memory reuse (see _write_request).
        try:
            household_id = getattr(getattr(sim, "household", None), "id", "local")
        except Exception:  # properties can raise during zone/household transitions
            household_id = "local"
        payload["session_id"] = "household-{}".format(household_id)
    event_id = TICK_DRIVER.client.queue_event("heartbeat", payload)
    output("queued heartbeat: {}".format(event_id or "outbox full"))


@sims4.commands.Command("plumbmate.status", command_type=sims4.commands.CommandType.Live)
def plumbmate_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    root = _ensure_directories()
    output("PlumbMate {} | bridge: {}".format(MOD_VERSION, root))
    available, reason = _bridge_reason()
    output("socket bridge: {} ({})".format(
        "available" if available else "unavailable", reason))
    output("tick hook: {}".format("installed" if _hook_installed() else "not installed"))
    output("autostart: {}".format(AUTOSTART_STATE))


def _submit_chat(message, connection, output):
    """The only send path for both UI and console. No hidden UI-only bypass."""
    if plumbmate_chat_input is None:
        raise ValueError("대화 입력 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    message = plumbmate_chat_input.validate_message(message)
    sim, partner = _bound_player(connection)
    if (_LAST_REQUEST_ID is not None and _LAST_ROLE_BINDING == ROLE_BINDING.binding_id
            and _LAST_DISPLAYED_ID != _LAST_REQUEST_ID
            and time.monotonic() - _LAST_SENT_AT < TURN_DISPLAY_TTL_SECONDS):
        status = plumbmate_turn_bridge.read_status(_bridge_directory(), _LAST_REQUEST_ID)
        if status is None or status["phase"] != "failed":
            raise ValueError("앞서 보낸 대화가 있어요. plumbmate.read로 확인해 주세요. 중복 전송하지 않았어요.")
    target = _write_request(sim, message)
    if REPLY_DELIVERY is not None:
        REPLY_DELIVERY.start(_LAST_REQUEST_ID, ROLE_BINDING.binding_id,
                             ROLE_BINDING.scene_id, connection, _LAST_SENT_AT)
    try:
        output("PlumbMate request queued: {}".format(os.path.basename(target)))
    except Exception:
        pass  # A failed console receipt must not turn a committed write into a retry.
    return "{} → {} · 요청을 남겼어요. 자동 수신 연결이 있으면 알림 담벼락에 답변이 도착해요. 수동 확인: plumbmate.read".format(_sim_name(sim.sim_info), _sim_name(partner))


def _chat_scope(connection):
    _bound_player(connection)  # Recheck household, both live Sims and zone fence.
    return ROLE_BINDING.binding_id, ROLE_BINDING.scene_id


def _open_chat(connection, output):
    if CHAT_UI is None:
        raise ValueError("대화창 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    sim, partner = _bound_player(connection)
    CHAT_UI.open(sim.sim_info, _sim_name(partner), _chat_scope(connection),
                 lambda: _chat_scope(connection),
                 lambda message: _submit_chat(message, connection, output), output)


@sims4.commands.Command("plumbmate.chat", command_type=sims4.commands.CommandType.Live)
def plumbmate_chat(*message_parts, _connection=None):
    output = sims4.commands.CheatOutput(_connection)
    if _active_sim(_connection) is None:
        output("PlumbMate: active Sim not found. Load a household first.")
        return
    try:
        if not message_parts:
            _open_chat(_connection, output)
        else:
            output(_submit_chat(" ".join(_safe_text(part) for part in message_parts), _connection, output))
    except Exception as exc:
        output("PlumbMate request failed: {}".format(_safe_text(exc)))


@sims4.commands.Command("plumbmate.read", command_type=sims4.commands.CommandType.Live)
def plumbmate_read(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        sim, partner = _bound_player(_connection)
        reason = _reply_ineligible_reason()
        if reason:
            output(reason)
            return
        response = _latest_response()
        if response is None:
            status = plumbmate_turn_bridge.read_status(_bridge_directory(), _LAST_REQUEST_ID)
            if status is None:
                output("PlumbMate: 응답 대기 중. 동반 앱이 켜져 있는지 확인해 주세요.")
            elif status["phase"] == "failed":
                output("PlumbMate: " + _failure_message(status["reason_code"]))
            else:
                output("PlumbMate: 동반 앱에서 요청을 처리하고 있어요.")
            return
        _show_response(response, partner, output)
    except Exception as exc:
        output("PlumbMate response failed: {}".format(_safe_text(exc)))


def _reply_ineligible_reason():
    """One display gate shared by manual reads and automatic socket delivery."""
    if _LAST_REQUEST_ID is None or _LAST_ROLE_BINDING != ROLE_BINDING.binding_id:
        return "이 역할 설정에서 보낸 대화가 없어요. 이전 설정의 답변은 표시하지 않아요."
    if _LAST_DISPLAYED_ID == _LAST_REQUEST_ID:
        return "이미 표시한 답변이에요. 알림 담벼락 또는 콘솔에서 확인해 주세요."
    if time.monotonic() - _LAST_SENT_AT >= TURN_DISPLAY_TTL_SECONDS:
        return "보낸 지 오래된 답변은 표시하지 않아요. 필요하면 새로 대화해 주세요."
    return None


def _failure_message(reason):
    return (plumbmate_reply_delivery.failure_message(reason) if plumbmate_reply_delivery
            else "응답을 처리하지 못했어요. 동반 앱 상태를 확인해 주세요.")


def _show_response(response, partner, output):
    global _LAST_DISPLAYED_ID
    response = plumbmate_turn_bridge.validate_response(response, _LAST_REQUEST_ID)
    try:
        plumbmate_role_setup_ui.notify(partner, "PlumbMate · " + _sim_name(partner), response["partner_message"])
    except Exception:
        output(response["partner_message"])
        output("알림을 표시하지 못해 콘솔에 답변을 표시했어요.")
    _LAST_DISPLAYED_ID = _LAST_REQUEST_ID
    if REPLY_DELIVERY is not None:
        REPLY_DELIVERY.stop()
    # Suggestions remain inert, regardless of which delivery channel was used.
    output("Suggested action: {} | {}".format(response["action"]["name"],
                                               _safe_text(response["action"].get("reason", ""))))
    output("Automatic execution is disabled in prototype 0.1.")


def _reply_scope(connection):
    scope = _chat_scope(connection)
    reason = _reply_ineligible_reason()
    if reason:
        raise ValueError(reason)
    return scope


def _receive_reply(reply, connection):
    global _LAST_DISPLAYED_ID
    sim, partner = _bound_player(connection)
    if _reply_ineligible_reason():
        return
    output = sims4.commands.CheatOutput(connection)
    if reply["phase"] == "completed":
        _show_response(reply.get("response"), partner, output)
    else:
        # System failure is not character dialogue or a fictional excuse.
        message = _failure_message(reply.get("reason_code"))
        try:
            plumbmate_role_setup_ui.notify(sim.sim_info, "PlumbMate · 연결 안내", message)
        except Exception:
            output(message)
        _LAST_DISPLAYED_ID = _LAST_REQUEST_ID


def _on_reply_tick(client):
    if REPLY_DELIVERY is None or REPLY_DELIVERY.scope is None:
        return
    connection = REPLY_DELIVERY.connection
    REPLY_DELIVERY.advance(client, lambda: _reply_scope(connection),
                            lambda reply: _receive_reply(reply, connection))


@sims4.commands.Command("plumbmate.setup", command_type=sims4.commands.CommandType.Live)
def plumbmate_setup(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        if ROLE_SETUP is None or ROLE_BINDING is None:
            raise ValueError("역할 선택 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
        ROLE_SETUP.open(lambda: _role_environment(_connection), ROLE_BINDING, output, _sim_name)
    except Exception:
        output("역할 선택창을 열 수 없어요. 가족을 불러왔는지 확인해 주세요. 이 게임 빌드의 UI 호환성 확인이 필요할 수 있어요.")


# Mod load runs this. It must not raise: an exception during import would leave
# the game without the commands above, which are the only way to diagnose it.
try:
    if TICK_DRIVER is not None:
        TICK_DRIVER.after_pump = _on_reply_tick
    start_autostart()
except Exception as _exc:
    AUTOSTART_STATE = "autostart_raised: {}: {}".format(type(_exc).__name__, _exc)
