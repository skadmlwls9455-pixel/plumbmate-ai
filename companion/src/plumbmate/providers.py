"""Provider adapters. Secrets are accepted from environment variables only."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .budget import TokenBudget, clamp_request, estimate_tokens
from .drives import assess, dominant
from .contracts import model_output_schema, validate_model_output
from .protocol import PROTOCOL_VERSION, ProtocolError, validate_response
from .pricing import SpendingBlocked, SpendingPolicy
from .spend_ledger import SpendLedger
from .provider_http import post_openai
from .dialogue import dialogue_context


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
    actor = request.get("actor")
    pressing = dominant(assess(actor.get("motives") if isinstance(actor, dict) else None))
    return DRIVE_IDEAS[pressing[0]] if pressing else DEFAULT_IDEA


@dataclass
class MockProvider:
    """Predictable offline provider for development and demos."""

    def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        context = dialogue_context(request)
        actor_name = context["partner"]["name"]
        message = str(request.get("player_message", ""))
        budget = TokenBudget.for_request(request)
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "partner_message": f"[연결 테스트·mock] {actor_name}: 좋아. ‘{message[:80]}’부터 같이 해보자.",
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
    api_key: str = field(repr=False)
    model: str
    max_output_tokens: int = 220
    endpoint: str = "https://api.openai.com/v1/responses"
    ledger: SpendLedger | None = None

    @classmethod
    def from_environment(cls) -> "OpenAIResponsesProvider":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        model = os.environ.get("PLUMBMATE_OPENAI_MODEL", "").strip()
        if not api_key:
            raise ProtocolError("OPENAI_API_KEY is required for the OpenAI provider")
        if not model:
            raise ProtocolError("PLUMBMATE_OPENAI_MODEL must name an available model")
        config = Path(os.environ.get("PLUMBMATE_BILLING_CONFIG", "~/.config/plumbmate/billing.json")).expanduser()
        policy = SpendingPolicy.load(config)
        if policy.model != model:
            raise SpendingBlocked("Provider model must match the reviewed billing policy exactly")
        return cls(api_key=api_key, model=model, max_output_tokens=policy.max_output_tokens,
                   ledger=SpendLedger(config.with_suffix(".sqlite3"), policy))

    def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.ledger is None:
            raise SpendingBlocked("Paid calls disabled without a persistent spending ledger")
        if (self.endpoint != "https://api.openai.com/v1/responses" or self.model != self.ledger.policy.model
                or self.max_output_tokens != self.ledger.policy.max_output_tokens):
            raise SpendingBlocked("Provider configuration differs from the billing policy")
        compact = clamp_request(request)
        # Transport IDs/timestamps are local bookkeeping, not roleplay context.
        context = dialogue_context(compact)
        budget = TokenBudget(estimate_tokens(context), self.max_output_tokens)
        instructions = (
            "Roleplay ONLY the explicitly designated partner, never the player Sim. "
            "You are a warm Korean-speaking fictional companion. "
            "Reply briefly in Korean. Choose exactly one allow-listed action. Never invent a target "
            "identifier. Prefer suggest_only when required identifiers are absent. "
            "If drive_state is present, let the companion's current needs and mood shape the suggestion and tone. The game will ask "
            "the player before any action executes. Return only the requested JSON schema."
        )
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))}],
            "max_output_tokens": self.max_output_tokens,
            "prompt_cache_key": "plumbmate-" + hashlib.sha256((instructions + json.dumps(RESPONSE_SCHEMA, sort_keys=True)).encode("utf-8")).hexdigest()[:24],
            "service_tier": "default",
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
        self.ledger.reserve(request["request_id"])
        # Reserve worst-case input (including cache writes) + capped output
        # BEFORE any HTTP. The count call includes instructions and JSON schema.
        count_body = {key: body[key] for key in ("model", "input", "instructions", "text")}
        counted = post_openai("/responses/input_tokens", count_body, self.api_key)
        count = counted.get("input_tokens")
        if counted.get("object") != "response.input_tokens" or type(count) is not int or count < 0:
            raise SpendingBlocked("Invalid input count; reservation retained")
        if count > self.ledger.policy.max_input_tokens:
            self.ledger.settle(request["request_id"], 0)
            raise SpendingBlocked("Input token ceiling exceeded; generation was not sent")
        raw = post_openai("/responses", body, self.api_key)
        if raw.get("model") != self.model:
            raise SpendingBlocked("Response model differs from reviewed price card; reservation retained")
        if raw.get("service_tier", "default") != "default":
            raise SpendingBlocked("Unexpected service tier; reservation retained for billing review")
        cost, inp, out = self.ledger.policy.usage_cost(raw.get("usage"))
        # Even invalid JSON or a truncated model answer can be billable.
        self.ledger.settle(request["request_id"], cost, inp, out)
        if raw.get("status") != "completed":
            raise ProtocolError("OpenAI answer incomplete; usage recorded, no automatic retry")

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
