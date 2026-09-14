"""Bounded HTTPS JSON transport. No redirects, retries, or remote error echo."""

import json
import urllib.error
import urllib.request

from .protocol import ProtocolError

MAX_PROVIDER_BYTES = 1_048_576


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_openai(path: str, body: dict, api_key: str) -> dict:
    if path not in ("/responses", "/responses/input_tokens"):
        raise ProtocolError("Unsupported provider endpoint")
    request = urllib.request.Request("https://api.openai.com/v1" + path,
                                     data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(MAX_PROVIDER_BYTES + 1)
        if len(raw) > MAX_PROVIDER_BYTES:
            raise ProtocolError("Provider response exceeds byte limit")
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: None)
        if not isinstance(value, dict):
            raise ProtocolError("Provider response must be an object")
        return value
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ProtocolError(f"OpenAI HTTP {exc.code}; no automatic retry") from None
    except (urllib.error.URLError, TimeoutError, UnicodeError, ValueError, OSError, RecursionError):
        raise ProtocolError("OpenAI transport failed; charge status may be unknown") from None
