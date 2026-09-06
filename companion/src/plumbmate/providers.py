"""Provider adapters. Secrets are accepted from environment variables only."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .budget import TokenBudget, clamp_request
from .drives import assess, dominant
from .contracts import model_output_schema, validate_model_output
from .protocol import PROTOCOL_VERSION, ProtocolError, validate_response


class Provider(Protocol):
    def respond(self, request: dict[str, Any]) -> dict[str, Any]: ...


DRIVE_IDEAS = {
    "hunger": "같이 뭐 좀 만들어 먹자",
    "energy": "오늘은 일찍 쉬자",
    "social": "같이 수다 떨면서 놀자",
    "fun": "같이 게임하거나 춤추자",
    "hygiene": "먼저 씻고 오자",
    "bladder": "잠깐만, 화장실부터 다녀올게",
}
DEFAULT_IDEA = "둘이 사진을 찍고 조용한 저녁 데이트를 한다"


def idea_for(request: dict[str, Any]) -> str:
    """The mock's suggestion follows the most pressing motive, if any."""
    pressing = dominant(assess((request.get("actor") or {}).get("motives")))
    return DRIVE_IDEAS[pressing[0]] if pressing else DEFAULT_IDEA


@dataclass
class MockProvider:
    """Predictable offline provider for development and demos."""

    def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        actor_name = str(request.get("actor", {}).get("name", "에이든"))
        message = str(request.get("player_message", ""))
        budget = TokenBudget.for_request(request)
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "partner_message": f"{actor_name}: 좋아. ‘{message[:80]}’부터 같이 해보자.",
            "action": {
                "name": "suggest_only",
                "arguments": {
                    "target_sim_id": None,
                    "interaction": None,
                    "idea": idea_for(request),
                },
                "reason": "MVP에서는 플레이어 승인 전 행동을 자동 실행하지 않습니다.",
            },
            "memory_patch": [f"플레이어가 제안함: {message[:160]}"],
            "budget": {
                "estimated_input_tokens": budget.estimated_input_tokens,
                "max_output_tokens": budget.max_output_tokens,
            },
        }
        validate_response(response)
        return response


RESPONSE_SCHEMA: dict[str, Any] = model_output_schema()

@dataclass
class OpenAIResponsesProvider:
    api_key: str
    model: str
    max_output_tokens: int = 220
    endpoint: str = "https://api.openai.com/v1/responses"

    @classmethod
    def from_environment(cls) -> "OpenAIResponsesProvider":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        model = os.environ.get("PLUMBMATE_OPENAI_MODEL", "").strip()
        if not api_key:
            raise ProtocolError("OPENAI_API_KEY is required for the OpenAI provider")
        if not model:
            raise ProtocolError("PLUMBMATE_OPENAI_MODEL must name an available model")
        return cls(api_key=api_key, model=model)

    def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        compact = clamp_request(request)
        session_id = str(compact.get("session_id", "local"))
        budget = TokenBudget.for_request(compact, self.max_output_tokens)
        instructions = (
            "You are a warm Korean-speaking co-player controlling a fictional companion Sim. "
            "Reply briefly in Korean. Choose exactly one allow-listed action. Never invent a target "
            "identifier. Prefer suggest_only when required identifiers are absent. "
            "If drive_state is present, let the companion's current needs and mood shape the suggestion and tone. The game will ask "
            "the player before any action executes. Return only the requested JSON schema."
        )
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}],
            "max_output_tokens": self.max_output_tokens,
            "prompt_cache_key": "plumbmate-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24],
            "safety_identifier": hashlib.sha256(("local:" + session_id).encode("utf-8")).hexdigest()[:32],
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "plumbmate_turn",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
                "verbosity": "low",
            },
        }
        encoded = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=45) as http_response:
                raw = json.loads(http_response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ProtocolError(f"OpenAI request failed ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"OpenAI request failed: {exc}") from exc

        output_text = raw.get("output_text") or self._extract_output_text(raw)
        if not output_text:
            raise ProtocolError("OpenAI response did not contain output_text")
        try:
            generated = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise ProtocolError("OpenAI output was not valid JSON") from exc

        validate_model_output(generated)
        response = {
            **generated,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "budget": {
                "estimated_input_tokens": budget.estimated_input_tokens,
                "max_output_tokens": budget.max_output_tokens,
            },
        }
        validate_response(response)
        return response

    @staticmethod
    def _extract_output_text(raw: dict[str, Any]) -> str | None:
        for item in raw.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        return None


def create_provider(name: str) -> Provider:
    if name == "mock":
        return MockProvider()
    if name == "openai":
        return OpenAIResponsesProvider.from_environment()
    raise ProtocolError(f"Unknown provider: {name}")
