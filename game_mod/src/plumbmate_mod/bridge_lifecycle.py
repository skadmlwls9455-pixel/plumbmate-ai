"""Game-thread bridge lifetime; no game imports, file I/O or background work.

Only autostart/start call the injected configuration loader. The tick binder
uses memory only. Hook ownership stays in tick_driver, not this coordinator.
"""


class BridgeLifecycle(object):
    def __init__(self, driver, load, probe, disabled, install_hook, remove_hook):
        self.driver = driver
        self._load = load
        self._probe = probe
        self._disabled = disabled
        self._install_hook = install_hook
        self._remove_hook = remove_hook
        self.bootstrap_client = None
        self.endpoint = None
        self.state = "not_attempted"
        self.cleanup_errors = []

    def bind(self, current):
        """Called on ticks: never reload configuration or revive a stopped client."""
        if self.bootstrap_client is None:
            return None
        self.state = "bound" if current is not None and current.connected else "waiting_for_companion"
        return self.bootstrap_client if current is None else None

    def _close(self, *clients):
        seen = []
        for client in clients:
            if client is None or any(client is other for other in seen):
                continue
            seen.append(client)
            try:
                client.close()
            except Exception as exc:
                # Do not leak token/configuration text through diagnostics.
                self.cleanup_errors.append(type(exc).__name__)

    def autostart(self):
        """Read configuration once at import; missing token still allows the UI hook."""
        if self._disabled():
            self.state = "disabled_by_file"
            return False
        available, reason = self._probe()
        if not available:
            self.state = "unavailable: {}".format(reason)
            return False
        try:
            self.bootstrap_client, self.endpoint = self._load()
            self.state = "waiting_for_companion"
        except FileNotFoundError:
            self.state = "waiting_for_token"
        except Exception:
            self.state = "invalid_bridge_config"
        try:
            self.driver.set_binder(self.bind)
            self.driver.resume()
            self._install_hook()
        except Exception as exc:
            self.stop()
            self.state = "hook_failed: {}".format(type(exc).__name__)
            return False
        return True

    def start(self):
        """Explicit rebind. Return a result code and optional endpoint/error type."""
        self.cleanup_errors = []
        if not self._probe()[0]:
            return "unavailable", None
        try:
            client, endpoint = self._load()
        except FileNotFoundError:
            return "no_token", None
        except Exception:
            return "invalid_config", None
        try:
            self._install_hook()
        except Exception as exc:
            # A failed replacement must not discard the working connection.
            self._close(client)
            return "start_failed", type(exc).__name__
        previous = self.driver.detach()
        bootstrap = self.bootstrap_client
        self.bootstrap_client, self.endpoint = client, endpoint
        self._close(previous, bootstrap)
        try:
            self.driver.attach(client)
            self.driver.set_binder(self.bind)
            self.driver.resume()
        except Exception as exc:
            self.stop()
            self.state = "start_failed: {}".format(type(exc).__name__)
            return "start_failed", type(exc).__name__
        self.state = "waiting_for_companion"
        return "started", endpoint

    def stop(self):
        """Disable binding before cleanup; close all owned clients even if a hook fails."""
        self.state = "stopped_by_command"
        self.cleanup_errors = []
        self.endpoint = None
        bootstrap, self.bootstrap_client = self.bootstrap_client, None
        client = None
        restored = False
        try:
            if self.driver is not None:
                self.driver.suspend()
                self.driver.set_binder(None)
                client = self.driver.detach()
            restored = bool(self._remove_hook())
        except Exception as exc:
            self.cleanup_errors.append(type(exc).__name__)
        finally:
            self._close(client, bootstrap)
        return restored
