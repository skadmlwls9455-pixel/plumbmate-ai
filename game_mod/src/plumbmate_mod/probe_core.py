"""Python 3.7, engine-independent exact-object ownership test ledger.

Unit tests validate fail-closed policy, not Sims 4 marker preservation.
"""

MARKER = "_plumbmate_decision_id"


class OwnershipProbe:
    def __init__(self, decision_id, session_id, epoch, actor_id):
        self.decision_id = decision_id
        self.session_id = session_id
        self.epoch = epoch
        self.actor_id = actor_id
        self.state = "PENDING"
        self.interaction = None
        self.started = False
        self.cancel_requested = False
        self.records = []

    def record(self, phase, interaction=None, **extra):
        item = {
            "session_uuid": self.session_id, "scene_epoch": self.epoch,
            "decision_id": self.decision_id, "actor_id": self.actor_id,
            "phase": phase, "ownership_state": self.state,
            "runtime_identity": id(interaction) if interaction is not None else None,
            "marker": getattr(interaction, MARKER, None),
            "parent_identity": None,
        }
        item.update(extra)
        if len(self.records) < 256:
            self.records.append(item)

    def attach(self, interaction):
        if self.interaction is not None or self.state != "PENDING":
            raise ValueError("Only one new exact interaction may be attached")
        self.interaction = interaction
        try:
            setattr(interaction, MARKER, self.decision_id)
        except Exception as exc:
            self.record("marker_set_failed", interaction, error_type=type(exc).__name__)
        self.record("created", interaction)

    def enqueued(self, accepted, observed):
        exact = any(item is self.interaction for item in observed)
        self.state = "BOUND" if accepted and exact else "UNCONFIRMED"
        self.record("enqueue", self.interaction, accepted=bool(accepted),
                    exact_object_in_queue=exact)

    def observe_start(self, interaction):
        if interaction is not self.interaction:
            if getattr(interaction, MARKER, None) == self.decision_id:
                self.record("unowned_marker_match", interaction)
            return False
        if self.state not in ("PENDING", "BOUND"):
            return False
        self.started = True
        self.record("start", interaction)
        return True

    def may_cancel(self, interaction, session_id, epoch, actor_id, observed):
        return bool(
            self.state == "BOUND" and self.started and not self.cancel_requested
            and interaction is self.interaction and interaction is not None
            and session_id == self.session_id and epoch == self.epoch
            and actor_id == self.actor_id
            and any(item is interaction for item in observed)
        )

    def invalidate(self, reason):
        self.state = "INVALIDATED"
        self.record("invalidate", self.interaction, reason=reason)

    def release(self):
        self.record("release", self.interaction)
        self.interaction = None
        self.state = "RELEASED"
