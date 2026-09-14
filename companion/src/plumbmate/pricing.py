"""Explicit USD price/limit policy. No model selection or live price guessing."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

from .protocol import ProtocolError, read_json

NANO_USD = 1_000_000_000


class SpendingBlocked(ProtocolError):
    """A paid call was blocked, or accounting needs attention."""


def usd_units(value: str) -> int:
    try:
        if not isinstance(value, str) or len(value) > 32:
            raise ValueError()
        amount = Decimal(value)
        if not amount.is_finite() or not 0 <= amount <= 100_000:
            raise ValueError()
        return int((amount * NANO_USD).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError):
        raise SpendingBlocked("USD amounts must be finite decimal strings between 0 and 100000") from None


def display_usd(units: int) -> str:
    return format(Decimal(units) / NANO_USD, ".9f")


@dataclass(frozen=True)
class SpendingPolicy:
    model: str
    valid_until: str
    daily: int
    total: int
    per_request: int
    input_rate: int
    cached_rate: int
    cache_write_rate: int
    output_rate: int
    max_input_tokens: int = 8192
    max_output_tokens: int = 220

    @classmethod
    def load(cls, path: Path, *, check_expiry: bool = True) -> "SpendingPolicy":
        try:
            obj = read_json(path)
            required = {"version", "model", "valid_until", "daily_usd", "total_usd", "per_request_usd",
                        "input_per_million", "cached_per_million", "cache_write_per_million", "output_per_million",
                        "max_input_tokens", "max_output_tokens"}
            if set(obj) != required or type(obj["version"]) is not int or obj["version"] != 1:
                raise ValueError()
            policy = cls(obj["model"], obj["valid_until"], *(usd_units(obj[key]) for key in (
                "daily_usd", "total_usd", "per_request_usd", "input_per_million", "cached_per_million",
                "cache_write_per_million", "output_per_million")), obj["max_input_tokens"], obj["max_output_tokens"])
            policy.check(check_expiry=check_expiry)
            return policy
        except (OSError, ProtocolError, TypeError, ValueError, KeyError):
            raise SpendingBlocked("Paid calls disabled: configure a valid, unexpired billing policy first") from None

    def check(self, *, check_expiry: bool = True) -> None:
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 128:
            raise SpendingBlocked("Billing policy needs an exact model ID")
        try:
            expired = date.fromisoformat(self.valid_until) < datetime.now(timezone.utc).date()
        except (TypeError, ValueError):
            raise SpendingBlocked("Invalid price expiry date") from None
        if expired and check_expiry:
            raise SpendingBlocked("Price policy expired; review current rates before paid calls")
        amounts = (self.daily, self.total, self.per_request, self.input_rate, self.cached_rate,
                   self.cache_write_rate, self.output_rate)
        if any(type(value) is not int or not 0 <= value <= 100_000 * NANO_USD for value in amounts):
            raise SpendingBlocked("Invalid spending policy amounts")
        if min(self.input_rate, self.cache_write_rate, self.output_rate) <= 0:
            raise SpendingBlocked("Paid input, cache-write and output prices must be positive")
        if (type(self.max_input_tokens) is not int or not 1 <= self.max_input_tokens <= 32768
                or type(self.max_output_tokens) is not int or not 32 <= self.max_output_tokens <= 4096):
            raise SpendingBlocked("Invalid input/output token ceilings")

    def ceiling(self) -> int:
        numerator = self.max_input_tokens * max(self.input_rate, self.cached_rate, self.cache_write_rate)
        numerator += self.max_output_tokens * self.output_rate
        return (numerator + 999_999) // 1_000_000

    def usage_cost(self, usage: dict) -> tuple[int, int, int]:
        """Cached and cache-write tokens are subsets of input, not extra input."""
        try:
            inp, out = usage["input_tokens"], usage["output_tokens"]
            detail = usage["input_tokens_details"]
            cached = detail["cached_tokens"]
            # Older responses may omit writes, but only equal-price policies
            # can safely use zero without understating the charge.
            writes = detail.get("cache_write_tokens", 0 if self.cache_write_rate == self.input_rate else None)
            if any(type(v) is not int or not 0 <= v <= 10_000_000 for v in (inp, out, cached, writes)):
                raise ValueError()
            if cached + writes > inp:
                raise ValueError()
            numerator = ((inp - cached - writes) * self.input_rate + cached * self.cached_rate
                         + writes * self.cache_write_rate + out * self.output_rate)
            return (numerator + 999_999) // 1_000_000, inp, out
        except (KeyError, TypeError, AttributeError, ValueError):
            raise SpendingBlocked("Provider usage is missing or inconsistent; reservation retained") from None
