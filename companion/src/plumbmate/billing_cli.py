"""Billing setup/reporting, isolated from game and provider credentials."""

import json
import os
from pathlib import Path

from .pricing import SpendingPolicy, usd_units
from .protocol import atomic_write_json
from .spend_ledger import SpendLedger


def register(subparsers):
    parser = subparsers.add_parser("budget", help="Configure USD caps or show durable local usage (no API calls)")
    parser.add_argument("operation", choices=("configure", "status", "reconcile"))
    parser.add_argument("--config", default=os.environ.get("PLUMBMATE_BILLING_CONFIG", "~/.config/plumbmate/billing.json"))
    parser.add_argument("--model")
    parser.add_argument("--valid-until", help="Price review expiry YYYY-MM-DD, UTC")
    for key in ("daily-usd", "total-usd", "per-request-usd"):
        parser.add_argument("--" + key, default="0", help="Explicit USD limit, default zero blocks paid calls")
    for key in ("input", "cached", "cache-write", "output"):
        parser.add_argument("--" + key + "-per-million", help="Reviewed USD price per million tokens")
    parser.add_argument("--max-input-tokens", type=int, default=8192)
    parser.add_argument("--max-output-tokens", type=int, default=220)
    parser.add_argument("--request-id")
    parser.add_argument("--confirmed-usd", help="Actual charge checked with provider, never a guessed refund")
    parser.add_argument("--confirm-no-active-calls", action="store_true", help="Companions stopped and remote call outcome reviewed")


def run(args):
    path = Path(args.config).expanduser()
    if args.operation == "configure":
        amounts = {key: getattr(args, key) for key in (
            "daily_usd", "total_usd", "per_request_usd", "input_per_million", "cached_per_million",
            "cache_write_per_million", "output_per_million")}
        policy = SpendingPolicy(args.model, args.valid_until, *(usd_units(value) for value in amounts.values()),
                                args.max_input_tokens, args.max_output_tokens)
        policy.check()
        atomic_write_json(path, dict(version=1, model=args.model, valid_until=args.valid_until, **amounts,
                                     max_input_tokens=args.max_input_tokens, max_output_tokens=args.max_output_tokens))
        print("Billing policy saved. Existing charges/reservations were NOT reset. No API call made.")
    policy = SpendingPolicy.load(path, check_expiry=False)
    ledger = SpendLedger(path.with_suffix(".sqlite3"), policy)
    if args.operation == "reconcile":
        ledger.reconcile(args.request_id, usd_units(args.confirmed_usd), no_active_calls=args.confirm_no_active_calls)
        print("Operator reconciliation recorded. No provider refund or automatic retry was requested.")
    print(json.dumps(ledger.report(), ensure_ascii=False, indent=2))
    print("Local rate-card accounting, not an invoice. Other apps/keys, taxes and pricing changes are not covered.")
    return 0
