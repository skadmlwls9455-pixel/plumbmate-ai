"""Non-destructive installation/connection diagnostics. No model requests."""

import errno

from .event_socket import AuthenticationError, BridgeClient, SocketBridgeError, configured_port, load_token
from .protocol import BridgePaths, ProtocolError


def inspect_bridge(paths: BridgePaths, *, connect: bool = False) -> dict:
    checks = []
    for name in ("requests", "responses", "archive", "state"):
        path = paths.root / name
        checks.append({"check": "folder_" + name, "ok": path.is_dir() and not path.is_symlink(),
                       "remedy": "missing_or_symlinked_folder" if not path.is_dir() or path.is_symlink() else "none"})
    try:
        token = load_token(paths)
        port = configured_port(paths)
        checks.append({"check": "connection_configuration", "ok": True, "port": port})
    except (ProtocolError, SocketBridgeError, OSError):
        checks.append({"check": "connection_configuration", "ok": False, "remedy": "check_token_and_endpoint"})
        return {"ok": False, "api_calls": 0, "checks": checks}
    if connect:
        try:
            # Authenticate only: no heartbeat, game event, mutation or API call.
            with BridgeClient(token, port, timeout=2):
                pass
            checks.append({"check": "authenticated_loopback", "ok": True})
        except AuthenticationError:
            checks.append({"check": "authenticated_loopback", "ok": False, "remedy": "token_mismatch"})
        except OSError as exc:
            remedy = "start_companion" if exc.errno == errno.ECONNREFUSED else "check_local_connection"
            checks.append({"check": "authenticated_loopback", "ok": False, "remedy": remedy})
        except SocketBridgeError:
            checks.append({"check": "authenticated_loopback", "ok": False, "remedy": "check_protocol_or_port_owner"})
    return {"ok": all(check["ok"] for check in checks), "api_calls": 0, "checks": checks}
