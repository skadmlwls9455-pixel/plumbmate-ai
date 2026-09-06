"""Turn contracts only: no game imports, bridge I/O, or provider calls.

Package resources are the canonical schemas. Load and compile them once at
companion startup, not per turn. The repository's protocol/ copies are exports.
"""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


class ProtocolError(ValueError):
    pass


def _schema(name: str) -> dict[str, Any]:
    resource = files("plumbmate").joinpath("schemas", name + ".schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


REQUEST_SCHEMA = _schema("request")
RESPONSE_SCHEMA = _schema("response")
PROTOCOL_VERSION = REQUEST_SCHEMA["properties"]["protocol_version"]["const"]
ALLOWED_ACTIONS = frozenset(RESPONSE_SCHEMA["properties"]["action"]["properties"]["name"]["enum"])
_REQUEST_VALIDATOR = Draft202012Validator(REQUEST_SCHEMA)
_RESPONSE_VALIDATOR = Draft202012Validator(RESPONSE_SCHEMA)
_ID_VALIDATOR = Draft202012Validator(REQUEST_SCHEMA["properties"]["request_id"])


def _validate(validator: Draft202012Validator, value: Any, label: str) -> None:
    error = next(validator.iter_errors(value), None)
    if error is not None:
        # Do not echo rejected values or arbitrary property names (private input).
        raise ProtocolError(label + " violates the turn contract (" + str(error.validator) + ")")
    try:
        # JSON escapes can decode to lone surrogates; reject them before UTF-8
        # packing or provider serialization turns one request into a poison pill.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError(label + " is not UTF-8 JSON") from exc


def validate_request_id(value: Any) -> None:
    _validate(_ID_VALIDATOR, value, "request_id")


def validate_request(value: Any) -> None:
    _validate(_REQUEST_VALIDATOR, value, "Request")


def validate_response(value: Any, *, expected_request_id: str | None = None) -> None:
    _validate(_RESPONSE_VALIDATOR, value, "Response")
    if expected_request_id is not None and value["request_id"] != expected_request_id:
        raise ProtocolError("Response request_id does not match the pending turn")


def model_output_schema() -> dict[str, Any]:
    """Only prose/action proposals are model-owned; envelope IDs are local."""
    fields = ("partner_message", "action", "memory_patch")
    return {
        "type": "object", "additionalProperties": False, "required": list(fields),
        "properties": {key: copy.deepcopy(RESPONSE_SCHEMA["properties"][key]) for key in fields},
    }


_MODEL_VALIDATOR = Draft202012Validator(model_output_schema())


def validate_model_output(value: Any) -> None:
    _validate(_MODEL_VALIDATOR, value, "Model output")
