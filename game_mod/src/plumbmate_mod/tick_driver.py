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
DRIVER_MARKER = "_plumbmate_driver_ref"

# Wrappers THIS module load actually installed, by (class, method). Marker
# attributes alone cannot prove ownership: functools.wraps in another mod
# copies them onto that mod's wrapper, and a reloaded PlumbMate sees the
# previous load's wrapper carrying markers it did not set.
_INSTALLED_WRAPPERS = {}


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
        return self._client is not None and self.disabled_reason is None

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
        now = self._now()
        if now < self._next_bind_at:
            return False
        self._next_bind_at = now + self._bind_interval
        self.stats["bind_attempts"] += 1
        try:
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
        """Advance the client by at most one pump. Never raises, never blocks."""
        self.stats["ticks"] += 1
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
    return getattr(getattr(target_class, method_name, None), HOOK_MARKER, False)


def install_tick_hook(target_class, driver, method_name="update"):
    """Wrap ``method_name`` so the driver ticks after the original returns.

    Idempotent: a second call on an already hooked class installs no new
    wrapper and returns False — but it REBINDS the surviving wrapper to the
    given driver, so a reloaded mod's fresh driver actually receives ticks
    instead of the invisible first-load driver keeping them forever.
    """
    original = getattr(target_class, method_name, None)
    if original is None:
        raise AttributeError(
            "{} has no method {}".format(getattr(target_class, "__name__", target_class), method_name)
        )
    if getattr(original, HOOK_MARKER, False):
        holder = getattr(original, DRIVER_MARKER, None)
        if holder is not None:
            holder[0] = driver
        return False

    holder = [driver]

    def plumbmate_tick_wrapper(self, *args, **kwargs):
        # Gameplay first. A failure in the original must not be hidden by ours.
        result = original(self, *args, **kwargs)
        active = holder[0]
        if active is not None:
            active.on_tick(args[0] if args else None)
        return result

    setattr(plumbmate_tick_wrapper, HOOK_MARKER, True)
    setattr(plumbmate_tick_wrapper, ORIGINAL_MARKER, original)
    setattr(plumbmate_tick_wrapper, DRIVER_MARKER, holder)
    plumbmate_tick_wrapper.__name__ = getattr(original, "__name__", method_name)
    setattr(target_class, method_name, plumbmate_tick_wrapper)
    _INSTALLED_WRAPPERS[(target_class, method_name)] = plumbmate_tick_wrapper
    return True


def remove_tick_hook(target_class, method_name="update"):
    """Restore the original method, or disable the hook in place.

    Returns True only when the chain was actually restored. When something
    else wrapped Zone.update on top of us (the normal TS4 injection pattern),
    or a functools.wraps copy carries our markers, or the wrapper belongs to
    a previous module load, unwinding the chain would rip out other mods'
    hooks — so instead the PlumbMate layer is deactivated where it stands
    (its driver reference is cleared) and False is returned. Either way,
    after this call no PlumbMate driver receives ticks until a new install.
    """
    current = getattr(target_class, method_name, None)
    ours = _INSTALLED_WRAPPERS.get((target_class, method_name))
    if current is not None and current is ours:
        setattr(target_class, method_name, getattr(current, ORIGINAL_MARKER))
        holder = getattr(current, DRIVER_MARKER, None)
        if holder is not None:
            holder[0] = None
        _INSTALLED_WRAPPERS.pop((target_class, method_name), None)
        return True
    # Deactivate in place: the holder is shared even by wraps-copies, so
    # clearing it silences our layer without touching anyone else's chain.
    if current is not None:
        holder = getattr(current, DRIVER_MARKER, None)
        if holder is not None:
            holder[0] = None
    if ours is not None:
        holder = getattr(ours, DRIVER_MARKER, None)
        if holder is not None:
            holder[0] = None
        _INSTALLED_WRAPPERS.pop((target_class, method_name), None)
    return False
