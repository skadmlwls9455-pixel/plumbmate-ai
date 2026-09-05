"""Opt-in TS4 development probe. No network, save access, or gameplay at import.

This file is packaged separately from the production bridge. A one-shot startup
probe checks the real game process, not a host Python or isolated helper. It
records module import capability only and never opens a SQLite connection.

The socket section additionally completes a 127.0.0.1 round trip, because
importing the wrapper does not prove the extension loads or that the loopback
stack works. No external address is contacted and nothing sleeps.
"""

import datetime
import json
import os
import sys
import uuid

try:
    import plumbmate_socket_probe
except Exception:
    plumbmate_socket_probe = None

PROBE_REVISION = "runtime-2"
SESSION_ID = uuid.uuid4().hex


def _diagnostic_directory():
    source = os.path.abspath(__file__)
    marker = source.lower().find(".ts4script")
    if marker < 0:
        raise RuntimeError("Diagnostic must run from its dedicated .ts4script")
    archive = source[:marker + len(".ts4script")]
    return os.path.join(os.path.dirname(archive), "PlumbMateDiagnostics")


def runtime_probe():
    payload = {
        "probe": "in_game_runtime",
        "revision": PROBE_REVISION,
        "session_id": SESSION_ID,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "pid": os.getpid(),
        "executable": sys.executable,
        "sys_version": sys.version,
        "version_info": list(sys.version_info),
        "module_file": __file__,
    }
    try:
        import sqlite3
        payload["sqlite3"] = {
            "result": "IMPORTED",
            "origin": getattr(sqlite3, "__file__", None),
            "sqlite_version": getattr(sqlite3, "sqlite_version", None),
            "connection_test": "NOT_RUN",
        }
    except Exception as exc:
        payload["sqlite3"] = {
            "result": "IMPORT_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    if plumbmate_socket_probe is None:
        payload["socket"] = {"verdict": "NOT_PACKAGED"}
    else:
        try:
            payload["socket"] = plumbmate_socket_probe.probe_socket_capability()
        except Exception as exc:
            payload["socket"] = {"verdict": "PROBE_FAILED",
                                 "error_type": type(exc).__name__, "error": str(exc)}
    root = _diagnostic_directory()
    if not os.path.isdir(root):
        os.makedirs(root)
    target = os.path.join(root, "runtime-{}.json".format(SESSION_ID))
    with open(target + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(target + ".tmp", target)
    return payload


try:
    RUNTIME_RESULT = runtime_probe()
except Exception as probe_error:
    # Development diagnostics must never prevent the game's startup.
    RUNTIME_RESULT = {"probe": "FAILED", "error": str(probe_error)}
    print("PlumbMate diagnostic failed: {}".format(probe_error))
else:
    if plumbmate_socket_probe is not None:
        try:
            print("PlumbMate {}".format(
                plumbmate_socket_probe.summary_line(RUNTIME_RESULT.get("socket", {}))))
        except Exception:
            pass


try:
    import plumbmate_ownership_probe
except Exception as ownership_error:
    # Import capability does not imply that any action has been tested.
    print("PlumbMate ownership commands unavailable: {}".format(ownership_error))
