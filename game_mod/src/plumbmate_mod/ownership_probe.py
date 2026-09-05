"""Manual development-only stand-action probe; no automatic gameplay.

Callbacks only append bounded in-memory evidence. Explicit console commands
flush local diagnostic logs. No patching, network, save access, or queue reset.
"""

import json
import os
import time
import uuid

import services
import sims4.commands
import sims4.resources
from event_testing.test_events import TestEvent
from interactions.aop import AffordanceObjectPair
from interactions.context import InteractionContext, InteractionSource, QueueInsertStrategy
from interactions.interaction_finisher import FinishingType
from interactions.priority import Priority
from postures.stand import StandSuperInteraction
from plumbmate_probe_core import MARKER, OwnershipProbe

SESSION = uuid.uuid4().hex
# These names are candidates, not evidence that live tuning/queue tests pass.
ALLOWED_NAMES = frozenset(("stand", "sim-stand", "Stand"))
ACTIVE = None
LISTENER = None


def _root():
    source = os.path.abspath(__file__)
    marker = source.lower().find(".ts4script")
    if marker < 0:
        raise RuntimeError("Not running from the diagnostic archive")
    return os.path.join(os.path.dirname(source[:marker + len(".ts4script")]), "PlumbMateDiagnostics")


def _write(name, payload):
    root = _root()
    if not os.path.isdir(root):
        os.makedirs(root)
    target = os.path.join(root, name + ".json")
    with open(target + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
    os.replace(target + ".tmp", target)


def _actor(connection):
    client = services.client_manager().get(connection)
    return getattr(client, "active_sim", None) if client is not None else None


def _observed(sim):
    return list(sim.get_all_running_and_queued_interactions())


def _current():
    if ACTIVE is None:
        raise RuntimeError("No active probe")
    if (services.current_zone() is not ACTIVE["zone"]
            or ACTIVE["sim"].sim_info.get_sim_instance() is not ACTIVE["sim"]
            or ACTIVE["sim"].queue is not ACTIVE["queue"]):
        ACTIVE["ledger"].invalidate("scene_or_instance_changed")
        raise RuntimeError("Scene or Sim changed; cancellation forbidden")
    if time.monotonic() - ACTIVE["created"] > 120:
        ACTIVE["ledger"].invalidate("probe_timeout")
        raise RuntimeError("Probe expired; no automatic retry or cancellation")
    return ACTIVE


def _flush():
    if ACTIVE is not None:
        ledger = ACTIVE["ledger"]
        _write("ownership-" + ledger.decision_id, {
            "probe": "in_game_ownership", "session_id": SESSION,
            "state": ledger.state, "records": ledger.records,
            "affordance": ACTIVE["affordance"],
            "limits": {"continuation": "NOT_TESTED", "reset": "NOT_TESTED",
                       "load": "NOT_TESTED", "same_action_control": "NOT_TESTED"},
        })


class _Listener:
    def handle_event(self, sim_info, event_type, resolver):
        try:
            state = _current()
            ledger = state["ledger"]
            if sim_info is not state["sim"].sim_info:
                return
            if event_type == TestEvent.OnSimReset:
                ledger.invalidate("sim_reset")
            elif event_type == TestEvent.InteractionStart:
                ledger.observe_start(resolver.get_resolved_arg("interaction"))
        except Exception:
            # No I/O, queue mutation or propagated exception in event callbacks.
            return


def _command(name):
    def decorate(fn):
        def wrapped(_connection=None):
            output = sims4.commands.CheatOutput(_connection)
            try:
                fn(_connection, output)
            except Exception as exc:
                output("PlumbMate probe stopped: {}: {}".format(type(exc).__name__, exc))
                try:
                    _write("probe-error-" + SESSION, {"command": name,
                           "error_type": type(exc).__name__, "error": str(exc)})
                except Exception:
                    output("Could not write diagnostic error log")
            finally:
                try:
                    _flush()
                except Exception:
                    output("Could not flush diagnostic evidence")
        return sims4.commands.Command(name, command_type=sims4.commands.CommandType.Live)(wrapped)
    return decorate


@_command("plumbmate.probe.catalog")
def catalog(connection, output):
    manager = services.get_instance_manager(sims4.resources.Types.INTERACTION)
    candidates = []
    for cls in manager.types.values():
        if isinstance(cls, type) and issubclass(cls, StandSuperInteraction):
            candidates.append({"name": cls.__name__, "id": int(cls.guid64),
                               "allowed": cls.__name__ in ALLOWED_NAMES})
    options = services.misc_options_service()
    autosave = {name: str(getattr(options, name, "UNKNOWN")) for name in (
        "autosave_real_time_frequency", "autosave_sim_time_frequency", "autosave_on_build_buy_exit")}
    _write("catalog-" + SESSION, {"candidates": candidates, "autosave": autosave})
    output("Stand candidates: {}".format(candidates))


@_command("plumbmate.probe.enqueue")
def enqueue(connection, output):
    global ACTIVE, LISTENER
    if ACTIVE is not None:
        raise RuntimeError("One probe only; inspect/end the previous probe first")
    sim = _actor(connection)
    if sim is None or sim.queue is None:
        raise RuntimeError("Load a household and select the approved test Sim")
    # No displacement/cleanup of existing actions, even native autonomy.
    if sim.has_any_pending_or_running_interactions():
        raise RuntimeError("Sim is busy; wait for idle, do not clear the queue")
    manager = services.get_instance_manager(sims4.resources.Types.INTERACTION)
    candidates = [cls for cls in manager.types.values()
                  if isinstance(cls, type) and issubclass(cls, StandSuperInteraction)
                  and cls.__name__ in ALLOWED_NAMES]
    if len(candidates) != 1:
        raise RuntimeError("Expected one allow-listed stand tuning; run catalog")
    affordance = candidates[0]
    context = InteractionContext(sim, InteractionSource.SCRIPT, Priority.Low,
                                 insert_strategy=QueueInsertStrategy.LAST)
    aop = AffordanceObjectPair(affordance, None, affordance, None)
    if not aop.test(context):
        raise RuntimeError("Game rejected stand tests; no injection")
    factory = aop.interaction_factory(context)
    if not factory or factory.interaction is None:
        raise RuntimeError("Game did not create a concrete interaction")
    interaction = factory.interaction
    ledger = OwnershipProbe(uuid.uuid4().hex, SESSION, uuid.uuid4().hex, int(sim.id))
    ACTIVE = {"sim": sim, "queue": sim.queue, "zone": services.current_zone(),
              "ledger": ledger, "affordance": affordance.__name__, "created": time.monotonic()}
    ledger.attach(interaction)
    LISTENER = _Listener()
    services.get_event_manager().register(LISTENER, (TestEvent.InteractionStart, TestEvent.OnSimReset))
    interaction.register_on_cancelled_callback(_cancelled)
    result = aop.execute_interaction(interaction)
    ledger.enqueued(bool(result), _observed(sim))
    output("Enqueue: {} | decision={}".format(ledger.state, ledger.decision_id))


def _cancelled(interaction):
    if ACTIVE is not None and interaction is ACTIVE["ledger"].interaction:
        ledger = ACTIVE["ledger"]
        ledger.record("cancel_callback", interaction,
                      cancellation_requested_by="plumbmate_probe" if ledger.cancel_requested else "external")


@_command("plumbmate.probe.sample")
def sample(connection, output):
    state = _current()
    ledger = state["ledger"]
    observed = _observed(state["sim"])
    ledger.record("sample", ledger.interaction,
                  exact_object_present=any(i is ledger.interaction for i in observed),
                  start_event_observed=ledger.started,
                  queue_running_identity=id(state["sim"].queue.running))
    output("State={} | start={} | marker={}".format(
        ledger.state, ledger.started, getattr(ledger.interaction, MARKER, None)))


@_command("plumbmate.probe.cancel")
def cancel(connection, output):
    state = _current()
    ledger = state["ledger"]
    interaction = ledger.interaction
    before = _observed(state["sim"])
    if not ledger.may_cancel(interaction, SESSION, ledger.epoch, int(state["sim"].id), before):
        raise RuntimeError("Exact ownership and actual start not proven; cancel forbidden")
    if interaction.is_finishing:
        raise RuntimeError("Interaction already finishing; cancel not tested")
    from interactions.utils.user_cancelable_chain_liability import UserCancelableChainLiability
    if interaction.get_liability(UserCancelableChainLiability.LIABILITY_TOKEN) is not None:
        raise RuntimeError("Chain cancellation liability present; cancel forbidden")
    ledger.cancel_requested = True
    ledger.record("cancel_request", interaction, cancellation_requested_by="plumbmate_probe")
    accepted = interaction.cancel(FinishingType.USER_CANCEL,
                                  cancel_reason_msg="PlumbMate isolated ownership probe",
                                  ignore_must_run=False)
    after = _observed(state["sim"])
    ledger.record("cancel_result", interaction, accepted=bool(accepted),
                  other_objects_before=[id(i) for i in before if i is not interaction],
                  other_objects_after=[id(i) for i in after if i is not interaction])
    output("Cancel accepted={} (check callback and later sample separately)".format(bool(accepted)))


@_command("plumbmate.probe.end")
def end(connection, output):
    global ACTIVE, LISTENER
    if LISTENER is not None:
        services.get_event_manager().unregister(LISTENER, (TestEvent.InteractionStart, TestEvent.OnSimReset))
        LISTENER = None
    if ACTIVE is not None:
        ACTIVE["ledger"].release()
        _flush()
        ACTIVE = None
    output("Probe released; no queue cleared or saved")
