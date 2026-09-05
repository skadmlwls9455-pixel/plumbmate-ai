"""Python 3.7 socket capability probe; no game imports and no external network.

The open question this answers is narrow: the game ships ``lib/socket.pyc``, but
a pure-Python wrapper is not proof that the ``_socket`` extension loads inside
the game process. An earlier isolated diagnostic failed to import ``_socket``
for an ``@executable_path`` reason that says nothing about the game itself.

So this probe runs the whole chain the bridge actually depends on: import the
extension, import the wrapper, create a socket, bind loopback, and complete a
real connect/accept/send/recv round trip using the exact non-blocking idiom
``bridge_client`` relies on. Each stage is recorded separately, so a failure
still reports everything that worked before it.

Only 127.0.0.1 with a kernel-assigned port is ever touched, both sockets are
closed in a finally block, and nothing sleeps. Completion is polled with a
bounded spin because a diagnostic must not stall the game's startup. A spin
that runs out is reported as TIMEOUT, never as a failure of the socket layer.
"""

MAX_SPINS = 2000
LOOPBACK_HOST = "127.0.0.1"
PROBE_PAYLOAD = b"plumbmate-loopback\n"

# Imported by the companion bridge client, in the order it needs them.
REQUIRED_MODULES = ("_socket", "socket", "errno", "time", "json", "hmac", "hashlib", "uuid")
# Deliberately unused by bridge_client; recorded to document why.
OPTIONAL_MODULES = ("select", "selectors", "ssl")


def _import_report(name):
    try:
        module = __import__(name)
    except Exception as exc:
        return {"result": "IMPORT_FAILED", "error_type": type(exc).__name__, "error": str(exc)}
    return {"result": "IMPORTED", "origin": getattr(module, "__file__", "builtin")}


def probe_modules():
    report = {}
    for name in REQUIRED_MODULES:
        report[name] = _import_report(name)
        report[name]["required_by_bridge"] = True
    for name in OPTIONAL_MODULES:
        report[name] = _import_report(name)
        report[name]["required_by_bridge"] = False
    return report


def _is_connected(connection, socket_module):
    """The same select-free idiom bridge_client uses to finish a connect."""
    try:
        connection.getpeername()
        return True, None
    except OSError:
        code = connection.getsockopt(socket_module.SOL_SOCKET, socket_module.SO_ERROR)
        return False, (code or None)


def probe_loopback(socket_module):
    """Complete one 127.0.0.1 TCP round trip without blocking or sleeping."""
    stages = []
    result = {"stages": stages, "port": None, "spins": 0}
    listener = client = accepted = None
    try:
        listener = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        stages.append("listener_created")
        listener.setblocking(False)
        listener.bind((LOOPBACK_HOST, 0))
        port = int(listener.getsockname()[1])
        result["port"] = port
        stages.append("bound")
        listener.listen(1)
        stages.append("listening")

        client = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        client.setblocking(False)
        code = client.connect_ex((LOOPBACK_HOST, port))
        stages.append("connect_ex_{}".format(code))

        received = bytearray()
        for spin in range(MAX_SPINS):
            result["spins"] = spin + 1
            if accepted is None:
                try:
                    accepted, _peer = listener.accept()
                    accepted.setblocking(False)
                    stages.append("accepted")
                except OSError:
                    pass
            if accepted is not None and "connected" not in stages:
                connected, error_code = _is_connected(client, socket_module)
                if error_code:
                    result["result"] = "CONNECT_FAILED"
                    result["error"] = "SO_ERROR={}".format(error_code)
                    return result
                if connected:
                    stages.append("connected")
                    accepted.sendall(PROBE_PAYLOAD)
                    stages.append("sent")
            if "sent" in stages:
                try:
                    chunk = client.recv(len(PROBE_PAYLOAD))
                except OSError:
                    chunk = b""
                if chunk:
                    received.extend(chunk)
                    if bytes(received) == PROBE_PAYLOAD:
                        stages.append("received")
                        result["result"] = "ROUND_TRIP_OK"
                        return result
        result["result"] = "TIMEOUT"
        result["error"] = "did not finish within {} spins".format(MAX_SPINS)
        return result
    except Exception as exc:
        result["result"] = "FAILED"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        return result
    finally:
        for connection in (accepted, client, listener):
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def probe_socket_capability():
    """Full report plus one verdict a human can read off the console."""
    payload = {"probe": "socket_capability", "modules": probe_modules()}
    missing = [
        name for name in REQUIRED_MODULES
        if payload["modules"][name]["result"] != "IMPORTED"
    ]
    payload["missing_required"] = missing
    if missing:
        payload["loopback"] = {"result": "NOT_RUN", "reason": "required imports missing"}
        payload["verdict"] = "IMPORT_FAILED"
        return payload
    import socket as socket_module
    payload["loopback"] = probe_loopback(socket_module)
    payload["verdict"] = (
        "AVAILABLE" if payload["loopback"].get("result") == "ROUND_TRIP_OK"
        else "LOOPBACK_FAILED"
    )
    return payload


def summary_line(payload):
    """One console line: the answer, then why."""
    verdict = payload.get("verdict", "UNKNOWN")
    if verdict == "AVAILABLE":
        return "socket bridge: available (loopback round trip ok, port {})".format(
            payload.get("loopback", {}).get("port")
        )
    if verdict == "IMPORT_FAILED":
        missing = payload.get("missing_required", [])
        first = payload.get("modules", {}).get(missing[0], {}) if missing else {}
        return "socket bridge: unavailable (missing {}: {})".format(
            ", ".join(missing) or "?", first.get("error", "unknown"),
        )
    loopback = payload.get("loopback", {})
    return "socket bridge: unavailable ({}: {})".format(
        loopback.get("result", "?"), loopback.get("error", "unknown"),
    )
