"""Python 3.7 tick driver and method hook; no game imports, no threads.

The companion client makes progress only when something calls its ``pump()``.
This module supplies that caller and the hook that installs it, both written
against a plain class so the whole thing is unit-testable without the game.

``install_tick_hook`` wraps one method of one class. The wrapper calls the
original first and only then pumps, so gameplay runs exactly as before even if
the bridge is broken, and a raising original is never masked by our code.

The driver is fail-quiet by design. ``on_tick`` swallows everything, because it
runs inside the engine's own call and an exception there is the game's problem,
not the bridge's. Repeated failures disable the driver instead of burning the
tick budget forever; a console command can re-enable it.

``every_n_ticks`` exists because a bounded per-call cost is still a per-tick
cost. Nothing here measures the game's actual tick budget, and no test in this
repository can; the default of 1 pumps on every tick as configured, and the
knob is the honest place to back that off if a profile ever says to.

An optional ``binder`` lets the driver acquire its own client instead of
waiting for someone to call ``attach``. It is what makes a hands-off start
possible: the hook can be installed while nothing is listening yet, and the
driver stays idle until the binder hands it a client. The binder receives the
currently bound client so it can decide to replace it, and its throttle is in
seconds rather than ticks because this module has no idea how fast a tick is.
"""

import time


DEFAULT_EVERY_N_TICKS = 1
DEFAULT_MAX_CONSECUTIVE_ERRORS = 8
DEFAULT_BIND_INTERVAL_SECONDS = 5.0

HOOK_MARKER = "_plumbmate_tick_hook"
ORIGINAL_MARKER = "_plumbmate_original"
REGISTRY_ATTRIBUTE = "_plumbmate_tick_hooks_v1"


class TickDriver(object):
    """Calls ``client.pump()`` on a schedule and never lets it escape."""

    def __init__(
        self,
        client=None,
        every_n_ticks=DEFAULT_EVERY_N_TICKS,
        max_consecutive_errors=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        binder=None,
        bind_interval_seconds=DEFAULT_BIND_INTERVAL_SECONDS,
        now=None,
    ):
        if int(every_n_ticks) < 1:
            raise ValueError("every_n_ticks must be at least 1")
        self._client = client
        self._every = int(every_n_ticks)
        self._max_consecutive_errors = int(max_consecutive_errors)
        self._binder = binder
        self._bind_interval = float(bind_interval_seconds)
        self._now = now or time.monotonic
        self._next_bind_at = 0.0
        self._counter = 0
        self._consecutive_errors = 0
        self._suspended = False
        self.disabled_reason = None
        self.last_state = None
        self.last_error = None
        self.last_bind_error = None
        self.stats = {
            "ticks": 0, "pumps": 0, "skipped": 0, "errors": 0, "disables": 0,
            "bind_attempts": 0, "binds": 0, "bind_errors": 0,
        }

    @property
    def client(self):
        return self._client

    @property
    def active(self):
        return (not self._suspended and self._client is not None
                and self.disabled_reason is None)

    def suspend(self):
        """Stop both binding and pumping, even if another mod retains our hook."""
        self._suspended = True

    def attach(self, client):
        """Bind a client and clear any previous disable."""
        self._client = client
        self._counter = 0
        self._consecutive_errors = 0
        self.disabled_reason = None
        self.last_error = None
        return self

    def detach(self):
        """Unbind without closing; the caller owns the client's lifetime."""
        client = self._client
        self._client = None
        return client

    def resume(self):
        self._suspended = False
        self.disabled_reason = None
        self._consecutive_errors = 0

    def set_binder(self, binder, interval_seconds=None):
        """Install or clear the callable that supplies a client."""
        self._binder = binder
        if interval_seconds is not None:
            self._bind_interval = float(interval_seconds)
        self._next_bind_at = 0.0
        return self

    def _try_bind(self):
        """Ask the binder for a client. Swallows everything; runs on the engine."""
        try:
            now = self._now()
            if now < self._next_bind_at:
                return False
            self._next_bind_at = now + self._bind_interval
            self.stats["bind_attempts"] += 1
            client = self._binder(self._client)
        except BaseException as exc:
            self.stats["bind_errors"] += 1
            self.last_bind_error = "{}: {}".format(type(exc).__name__, exc)
            return False
        if client is None or client is self._client:
            return False
        # attach() also clears a disable, so a rebind revives a dead driver.
        self.attach(client)
        self.stats["binds"] += 1
        self.last_bind_error = None
        return True

    def on_tick(self, absolute_ticks=None):
        """Advance once. Binders must be memory-only; pumps must be nonblocking."""
        self.stats["ticks"] += 1
        if self._suspended:
            self.stats["skipped"] += 1
            return False
        if self._binder is not None:
            self._try_bind()
        if not self.active:
            self.stats["skipped"] += 1
            return False
        self._counter += 1
        if self._counter < self._every:
            self.stats["skipped"] += 1
            return False
        self._counter = 0
        try:
            self.last_state = self._client.pump()
        except BaseException as exc:
            # pump() already fails closed; this is the last line before the engine.
            self._consecutive_errors += 1
            self.stats["errors"] += 1
            self.last_error = "{}: {}".format(type(exc).__name__, exc)
            if self._consecutive_errors >= self._max_consecutive_errors:
                self.disabled_reason = "pump_failed_{}_times".format(self._consecutive_errors)
                self.stats["disables"] += 1
            return False
        self._consecutive_errors = 0
        self.stats["pumps"] += 1
        return True


def is_hooked(target_class, method_name="update"):
    record = target_class.__dict__.get(REGISTRY_ATTRIBUTE, {}).get(method_name)
    return bool(record and record["enabled"])


def install_tick_hook(target_class, driver, method_name="update"):
    """Wrap ``method_name`` so the driver ticks after the original returns.

    Ownership lives on the target class, not in copyable function attributes.
    On reload the exact owned wrapper can be reused with the new driver. If
    someone wrapped/replaced it, disable the old inner wrapper before adding
    an outer one. Never attempt to splice an unknown third-party wrapper chain.
    """
    original = getattr(target_class, method_name, None)
    if original is None:
        raise AttributeError(
            "{} has no method {}".format(getattr(target_class, "__name__", target_class), method_name)
        )
    registry = target_class.__dict__.get(REGISTRY_ATTRIBUTE)
    if registry is None:
        registry = {}
        setattr(target_class, REGISTRY_ATTRIBUTE, registry)
    previous = registry.get(method_name)
    if previous is not None and previous["driver"] is not driver:
        previous["driver"].suspend()
        old_client = previous["driver"].detach()
        if old_client is not None:
            old_client.close()
    if previous is not None and original is previous["wrapper"]:
        previous["driver"] = driver
        previous["enabled"] = True
        return False
    if previous is not None:
        previous["enabled"] = False
    record = {"driver": driver, "original": original, "enabled": True}

    def plumbmate_tick_wrapper(self, *args, **kwargs):
        # Gameplay first. A failure in the original must not be hidden by ours.
        result = original(self, *args, **kwargs)
        if record["enabled"]:
            try:
                record["driver"].on_tick(args[0] if args else None)
            except BaseException:
                # Contain even a broken driver replacement, not engine errors.
                record["enabled"] = False
        return result

    setattr(plumbmate_tick_wrapper, HOOK_MARKER, True)
    setattr(plumbmate_tick_wrapper, ORIGINAL_MARKER, original)
    plumbmate_tick_wrapper.__name__ = getattr(original, "__name__", method_name)
    record["wrapper"] = plumbmate_tick_wrapper
    registry[method_name] = record
    setattr(target_class, method_name, plumbmate_tick_wrapper)
    return True


def remove_tick_hook(target_class, method_name="update"):
    """Disable ours; restore only if the outermost function is exactly ours.

    False means no restoration occurred (absent hook or another mod outside
    ours). In that case the foreign chain is preserved and ours stays inert.
    """
    current = getattr(target_class, method_name, None)
    registry = target_class.__dict__.get(REGISTRY_ATTRIBUTE, {})
    record = registry.get(method_name)
    if record is None:
        return False
    record["enabled"] = False
    if current is not record["wrapper"]:
        return False
    setattr(target_class, method_name, record["original"])
    del registry[method_name]
    return True
