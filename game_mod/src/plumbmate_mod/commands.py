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
    import plumbmate_scene_adapter
    import plumbmate_conversation_state
except Exception:
    plumbmate_scene_adapter = None
    plumbmate_conversation_state = None
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
try:
    import plumbmate_reply_presenter
except Exception:
    plumbmate_reply_presenter = None
try:
    import plumbmate_control_ui
except Exception:
    plumbmate_control_ui = None

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
MOD_VERSION = "0.1.1-alpha.9"
DEFAULT_BRIDGE_PORT = 47651
AUTOSTART_DISABLE_FILE = "autostart-disabled"

# Initialized at import; idle until a client is bound, by autostart or by hand.
TICK_DRIVER = plumbmate_tick_driver.TickDriver() if plumbmate_tick_driver else None

# Configuration is read only at mod load or by an explicit start command.
# The tick path never opens/stats files, creates directories, or spawns workers.
_BOUND_ENDPOINT = None
_BOOTSTRAP_CLIENT = None
AUTOSTART_STATE = "not_attempted"
TURN_STATE = plumbmate_conversation_state.ConversationState() if plumbmate_conversation_state else None
TURN_DISPLAY_TTL_SECONDS = 180.0
ROLE_BINDING = plumbmate_role_binding.RoleBinding() if plumbmate_role_binding else None
ROLE_SETUP = plumbmate_role_setup_ui.RoleSetupUI() if plumbmate_role_setup_ui else None
CHAT_UI = plumbmate_chat_ui.ChatUI() if plumbmate_chat_ui else None
REPLY_DELIVERY = plumbmate_reply_delivery.ReplyDelivery() if plumbmate_reply_delivery else None
CONTROL_UI = plumbmate_control_ui.ControlUI() if plumbmate_control_ui else None


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
    # Diagnostics must still work if a partial archive lacks the scene module.
    try:
        return str(value)
    except Exception:
        return fallback


def _sim_name(sim_info):
    return plumbmate_scene_adapter.sim_name(sim_info)


def _active_sim(connection):
    if plumbmate_scene_adapter is None:
        return None
    return plumbmate_scene_adapter.active_sim(services, connection)


def _snapshot(sim):
    return plumbmate_scene_adapter.snapshot(sim)


def _role_environment(connection):
    return plumbmate_scene_adapter.role_environment(services, _active_sim(connection))


def _bound_player(connection):
    return plumbmate_scene_adapter.bound_player(
        ROLE_BINDING, _role_environment(connection), _active_sim(connection))


def _write_request(sim, message):
    if plumbmate_turn_bridge is None or TURN_STATE is None:
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
    TURN_STATE.sent(request_id, ROLE_BINDING.binding_id, time.monotonic())
    return target


def _latest_response():
    if plumbmate_turn_bridge is None:
        raise ValueError("Turn bridge module missing; reinstall the complete PlumbMate archive")
    return plumbmate_turn_bridge.read_response(_bridge_directory(), TURN_STATE.request_id)

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
    event_id = TICK_DRIVER.client.queue_event("heartbeat", {"source": "cheat_console"})
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
    if not _reply_ineligible_reason():
        status = plumbmate_turn_bridge.read_status(_bridge_directory(), TURN_STATE.request_id)
        if status is None or status["phase"] != "failed":
            raise ValueError("앞서 보낸 대화가 있어요. 대화창을 다시 열면 상태 확인이나 기다리기 종료를 할 수 있어요. 중복 전송하지 않았어요.")
    target = _write_request(sim, message)
    if REPLY_DELIVERY is not None:
        REPLY_DELIVERY.start(TURN_STATE.request_id, ROLE_BINDING.binding_id,
                             ROLE_BINDING.scene_id, connection, TURN_STATE.sent_at)
    try:
        output("PlumbMate request queued: {}".format(os.path.basename(target)))
    except Exception:
        pass  # A failed console receipt must not turn a committed write into a retry.
    return "{} → {} · 전송 요청을 남겼어요. 답변은 알림 담벼락에서 확인해 주세요.".format(_sim_name(sim.sim_info), _sim_name(partner))


def _chat_scope(connection):
    _bound_player(connection)  # Recheck household, both live Sims and zone fence.
    return ROLE_BINDING.binding_id, ROLE_BINDING.scene_id


def _open_chat(connection, output):
    if CHAT_UI is None:
        raise ValueError("대화창 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    try:
        sim, partner = _bound_player(connection)
    except ValueError:
        if ROLE_BINDING is not None and ROLE_BINDING.binding_id is None and ROLE_SETUP is not None:
            _open_role_setup(connection, output, lambda: _open_chat(connection, output))
            return  # Setup never submits the user's message or invents a partner.
        raise
    if not _reply_ineligible_reason():
        unreadable = False
        try:
            response = _latest_response()
            status = plumbmate_turn_bridge.read_status(_bridge_directory(), TURN_STATE.request_id)
        except (ValueError, OSError):
            # Corrupt receipts must not trap users outside the dismiss UI.
            response, status, unreadable = None, None, True
        if response is not None:
            _show_response(response, partner, output)
            return
        if status is None or status["phase"] != "failed":
            client = TICK_DRIVER.client if TICK_DRIVER else None
            automatic = bool(client and getattr(client, "supports_turn_delivery", False)
                             and REPLY_DELIVERY and REPLY_DELIVERY.scope)
            scope = (TURN_STATE.request_id,) + _chat_scope(connection)
            message = plumbmate_reply_delivery.pending_message(
                status, time.monotonic() - TURN_STATE.sent_at, automatic)
            if unreadable:
                message = "응답 기록을 읽지 못했어요. 전송 실패나 과금 취소가 확인된 것은 아니에요.\n\n" + message
            CHAT_UI.show_status(sim.sim_info, _sim_name(partner), message, scope,
                                lambda: _pending_scope(connection),
                                lambda: _dismiss_reply(connection, scope), output)
            return
        # Preserve the failure reason before offering a fresh composer.
        _receive_reply(dict(status), connection)
    CHAT_UI.open(sim.sim_info, _sim_name(partner), _chat_scope(connection),
                 lambda: _chat_scope(connection),
                 lambda message: _submit_chat(message, connection, output), output)


def _pending_scope(connection):
    scope = _chat_scope(connection)
    if _reply_ineligible_reason():
        raise ValueError("This turn is no longer pending")
    return (TURN_STATE.request_id,) + scope


def _dismiss_reply(connection, expected_scope):
    if _pending_scope(connection) != expected_scope:
        raise ValueError("Conversation changed")
    TURN_STATE.dismiss()
    if REPLY_DELIVERY is not None:
        REPLY_DELIVERY.stop("dismissed")
    return ("기다리기를 끝냈어요. 이 답변은 나중에도 표시하지 않아요. "
            "처리·과금 취소는 아니에요. 새로 말하려면 대화창을 다시 열어 주세요.")


@sims4.commands.Command("plumbmate.dismiss", command_type=sims4.commands.CommandType.Live)
def plumbmate_dismiss(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        output(_dismiss_reply(_connection, _pending_scope(_connection)))
    except Exception:
        output("이 역할·장소에서 끝낼 대기가 없어요. 새 대화나 다른 심의 답변은 변경하지 않았어요.")


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
            status = plumbmate_turn_bridge.read_status(_bridge_directory(), TURN_STATE.request_id)
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
    """Shared display policy; no file reads or game object access."""
    if TURN_STATE is None:
        raise ValueError("대화 상태 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    return TURN_STATE.ineligible_reason(
        ROLE_BINDING.binding_id if ROLE_BINDING else None,
        time.monotonic(), TURN_DISPLAY_TTL_SECONDS)


def _failure_message(reason):
    return (plumbmate_reply_delivery.failure_message(reason) if plumbmate_reply_delivery
            else "응답을 처리하지 못했어요. 동반 앱 상태를 확인해 주세요.")


def _reply_presenter():
    if plumbmate_reply_presenter is None:
        raise ValueError("답변 표시 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    return plumbmate_reply_presenter.ReplyPresenter(
        TURN_STATE, REPLY_DELIVERY,
        lambda *args, **kwargs: plumbmate_role_setup_ui.notify(*args, **kwargs),
        lambda response, request_id: plumbmate_turn_bridge.validate_response(response, request_id),
        _sim_name, _safe_text)


def _show_response(response, partner, output):
    _reply_presenter().show(response, partner, output)


def _reply_scope(connection):
    scope = _chat_scope(connection)
    reason = _reply_ineligible_reason()
    if reason:
        raise ValueError(reason)
    return scope


def _receive_reply(reply, connection):
    sim, partner = _bound_player(connection)
    if _reply_ineligible_reason():
        return
    output = sims4.commands.CheatOutput(connection)
    if reply["phase"] == "completed":
        _show_response(reply.get("response"), partner, output)
    else:
        # System failure is not character dialogue or a fictional excuse.
        _reply_presenter().failed(sim.sim_info, _failure_message(reply.get("reason_code")), output)


def _on_reply_expired(scope, connection):
    # Expiry is not a fictional rejection, a cancellation, or a refund. Never
    # surface an old turn's notice in a new family, role binding, or scene.
    sim, _ = _bound_player(connection)
    if (scope != {"request_id": TURN_STATE.request_id,
                  "binding_id": ROLE_BINDING.binding_id,
                  "scene_id": ROLE_BINDING.scene_id}
            or TURN_STATE.displayed_id == TURN_STATE.request_id
            or TURN_STATE.dismissed_id == TURN_STATE.request_id):
        return
    _reply_presenter().expired(sim.sim_info, lambda message: sims4.commands.CheatOutput(connection)(message))


def _on_reply_tick(client):
    if REPLY_DELIVERY is None or REPLY_DELIVERY.scope is None:
        return
    connection = REPLY_DELIVERY.connection
    REPLY_DELIVERY.advance(client, lambda: _reply_scope(connection),
                            lambda reply: _receive_reply(reply, connection),
                            lambda scope: _on_reply_expired(scope, connection))


def _open_role_setup(connection, output, on_complete=None):
    if ROLE_SETUP is None or ROLE_BINDING is None:
        raise ValueError("역할 선택 모듈이 없어요. 모드 전체를 다시 설치해 주세요.")
    ROLE_SETUP.open(lambda: _role_environment(connection), ROLE_BINDING, output, _sim_name,
                    on_complete=on_complete)


def _open_control(connection=None):
    if CONTROL_UI is None:
        return
    console = sims4.commands.CheatOutput(connection)

    def describe_roles():
        player, partner = _bound_player(connection)
        receipt = CHAT_UI.receipt(_chat_scope(connection)) if CHAT_UI is not None else ""
        return _sim_name(player.sim_info), _sim_name(partner), receipt

    CONTROL_UI.open_home(
        lambda: _role_environment(connection), describe_roles,
        lambda: _open_chat(connection, console),
        lambda: _open_role_setup(connection, console, lambda: _open_chat(connection, console)),
        console, lambda *args: plumbmate_role_setup_ui.notify(*args))


def _on_ui_tick():
    if CONTROL_UI is not None:
        CONTROL_UI.pulse(lambda: _role_environment(None), _open_control)


@sims4.commands.Command("plumbmate.ui", command_type=sims4.commands.CommandType.Live)
def plumbmate_ui(_connection=None):
    """Diagnostic/recovery entry; normal launch is the native wall button."""
    try:
        _open_control(_connection)
    except Exception:
        sims4.commands.CheatOutput(_connection)("PlumbMate 창을 열 수 없어요. 가족을 불러온 뒤 다시 시도해 주세요.")


@sims4.commands.Command("plumbmate.setup", command_type=sims4.commands.CommandType.Live)
def plumbmate_setup(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        _open_role_setup(_connection, output)
    except Exception:
        output("역할 선택창을 열 수 없어요. 가족을 불러왔는지 확인해 주세요. 이 게임 빌드의 UI 호환성 확인이 필요할 수 있어요.")


# Mod load runs this. It must not raise: an exception during import would leave
# the game without the commands above, which are the only way to diagnose it.
try:
    if TICK_DRIVER is not None:
        TICK_DRIVER.after_pump = _on_reply_tick
        TICK_DRIVER.before_pump = _on_ui_tick
        TICK_DRIVER.on_suspend = CONTROL_UI.close if CONTROL_UI else None
    start_autostart()
except Exception as _exc:
    AUTOSTART_STATE = "autostart_raised: {}: {}".format(type(_exc).__name__, _exc)
