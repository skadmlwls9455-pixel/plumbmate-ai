"""Companion-only wall-clock boundary for blocking provider IO.

One short-lived spawn process, no EA imports, retry, ledger access or files.
The parent owns accounting. Terminating this local worker is NOT remote API
cancellation: an unknown charge must remain reserved. A fixed shared buffer
avoids blocking on a partially written pipe message after a deadline.
"""

import json
import multiprocessing
import time

from .protocol import ProtocolError

MAX_RESULT_BYTES = 1_048_576
_orphan = None


def _worker(function, args, buffer, length, complete):
    try:
        value = function(*args)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) <= MAX_RESULT_BYTES:
            buffer[:len(raw)] = raw
            length.value = len(raw)
    except Exception:
        pass  # Do not transfer remote bodies, credentials or exception strings.
    finally:
        complete.set()


def run_bounded(function, args, *, timeout):
    """Internal trusted callables only. Deadline includes worker startup.

    Cleanup gets two additional one-second waits; never silently abandon an
    unkillable worker and create more workers behind it.
    """
    global _orphan
    if _orphan is not None:
        if _orphan.is_alive():
            raise ProtocolError("Previous provider worker is still stopping; no new call allowed")
        _orphan.join(timeout=0)
        _orphan.close()
        _orphan = None
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 90:
        raise ProtocolError("Invalid provider deadline")
    context = multiprocessing.get_context("spawn")
    buffer = context.RawArray("B", MAX_RESULT_BYTES)
    length = context.RawValue("i", 0)
    complete = context.Event()
    worker = context.Process(target=_worker, args=(function, args, buffer, length, complete),
                             name="PlumbMate-provider", daemon=True)
    deadline = time.monotonic() + timeout
    try:
        worker.start()
        if not complete.wait(max(0.0, deadline - time.monotonic())):
            raise ProtocolError("Provider deadline exceeded; no retry; remote charge may be unknown")
        size = length.value
        if not 0 < size <= MAX_RESULT_BYTES:
            raise ProtocolError("Provider transport failed; no retry; remote charge may be unknown")
        result = json.loads(bytes(buffer[:size]).decode("utf-8"))
        if not isinstance(result, dict):
            raise ProtocolError("Provider returned an invalid object")
        return result
    except ProtocolError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ProtocolError("Provider worker unavailable; no automatic retry") from None
    finally:
        if worker.pid is not None:
            # The buffer is complete before signalling; no reason to wait for
            # Python interpreter teardown or any inherited background cleanup.
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
            if worker.is_alive():
                _orphan = worker
            else:
                worker.close()
        else:
            worker.close()


def post_openai_bounded(path, body, api_key):
    from .provider_http import post_openai
    if path not in ("/responses", "/responses/input_tokens"):
        raise ProtocolError("Unsupported provider endpoint")
    # Input count + generation <= 90 seconds, excluding bounded cleanup.
    return run_bounded(post_openai, (path, body, api_key),
                       timeout=30 if path == "/responses/input_tokens" else 60)
