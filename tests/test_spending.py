"""Money safety tests: synthetic prices/usage only, never a billable request."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock

from plumbmate.cli import main, make_demo_request
from plumbmate.pricing import SpendingBlocked, SpendingPolicy, usd_units
from plumbmate.protocol import BridgePaths, ProtocolError, atomic_write_json, read_json
from plumbmate.providers import MockProvider, OpenAIResponsesProvider
from plumbmate.spend_ledger import SpendLedger
from plumbmate.engine import CompanionEngine


def policy(**changes):
    # Invented fixture model/rates; not product recommendations or live prices.
    return replace(SpendingPolicy("fixture-model", "2099-12-31", usd_units("1"), usd_units("2"), usd_units("0.1"),
                                  usd_units("2"), usd_units("0.2"), usd_units("2.5"), usd_units("10")), **changes)


def response(request):
    generated = MockProvider().respond(request)
    return {"status": "completed", "service_tier": "default", "model": "fixture-model",
            "output_text": json.dumps({key: generated[key] for key in ("partner_message", "action", "memory_patch")}),
            "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
                      "output_tokens": 100}}


class SpendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "billing.sqlite3"
        self.ledger = SpendLedger(self.path, policy())

    def provider(self, ledger=None):
        return OpenAIResponsesProvider("NOT-A-REAL-KEY", "fixture-model", ledger=ledger or self.ledger)

    def test_decimal_rounding_and_invalid_money(self):
        self.assertEqual(usd_units("0.0000000001"), 1)
        self.assertEqual(usd_units("0.1"), 100_000_000)
        for value in ("NaN", "Infinity", "-1", "100001", "junk", 0.1, None, True):
            with self.subTest(value=value), self.assertRaises(SpendingBlocked):
                usd_units(value)

    def test_zero_and_each_cap_block_before_any_network(self):
        for field in ("daily", "total", "per_request"):
            ledger = SpendLedger(self.path, policy(**{field: 0}))
            with self.subTest(field=field), mock.patch("plumbmate.providers.post_openai") as http:
                with self.assertRaises(SpendingBlocked):
                    self.provider(ledger).respond(make_demo_request("hi"))
                http.assert_not_called()

    def test_unconfigured_paid_provider_and_expired_policy_block(self):
        with mock.patch("plumbmate.providers.post_openai") as http:
            with self.assertRaises(SpendingBlocked):
                OpenAIResponsesProvider("key", "fixture-model").respond(make_demo_request("hi"))
            with self.assertRaises(SpendingBlocked):
                SpendLedger(self.path, policy(valid_until="2020-01-01")).reserve(uuid.uuid4().hex)
            http.assert_not_called()

    def test_restart_does_not_reset_total_or_uncertain_reservation(self):
        request_id = uuid.uuid4().hex
        self.ledger.reserve(request_id)
        restarted = SpendLedger(self.path, policy())
        self.assertEqual(restarted.report()["unresolved_requests"], 1)
        with self.assertRaises(SpendingBlocked):
            restarted.reserve(uuid.uuid4().hex)
        restarted.settle(request_id, usd_units("0.01"))
        self.assertEqual(self.ledger.report()["recorded_usd"], "0.010000000")

    def test_reconciliation_requires_explicit_operator_confirmation_and_keeps_audit(self):
        ident = uuid.uuid4().hex
        self.ledger.reserve(ident)
        with self.assertRaises(SpendingBlocked):
            self.ledger.reconcile(ident, 0)
        self.ledger.reconcile(ident, usd_units("0.001"), no_active_calls=True)
        self.assertEqual(self.ledger.report()["recorded_usd"], "0.001000000")
        self.assertEqual(self.ledger.report()["unresolved_requests"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT previous_state,confirmed_charge FROM adjustments").fetchone(),
                             ("reserved", usd_units("0.001")))
        with self.assertRaises(SpendingBlocked):
            self.ledger.reserve(ident)  # Still cannot replay the original.

    def test_version_one_migration_preserves_charges(self):
        ident = uuid.uuid4().hex
        self.ledger.reserve(ident)
        self.ledger.settle(ident, usd_units("0.001"))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE adjustments")
            db.execute("PRAGMA user_version=1")
            db.commit()
        self.assertEqual(SpendLedger(self.path, policy()).report()["recorded_usd"], "0.001000000")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_concurrent_companions_cannot_both_reserve(self):
        def attempt(_):
            try:
                SpendLedger(self.path, policy()).reserve(uuid.uuid4().hex)
                return True
            except SpendingBlocked:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(8))), 1)

    def test_replay_and_double_settlement_refused(self):
        request_id = uuid.uuid4().hex
        self.ledger.reserve(request_id)
        self.ledger.settle(request_id, 100)
        with self.assertRaises(SpendingBlocked):
            self.ledger.reserve(request_id)
        with self.assertRaises(SpendingBlocked):
            self.ledger.settle(request_id, 0)
        self.assertEqual(self.ledger.report()["recorded_usd"], "0.000000100")

    def test_overrun_is_recorded_and_latches_block(self):
        request_id = uuid.uuid4().hex
        ceiling = self.ledger.reserve(request_id)
        with self.assertRaises(SpendingBlocked):
            self.ledger.settle(request_id, ceiling + 1)
        self.assertEqual(self.ledger.report()["unresolved_requests"], 1)
        with self.assertRaises(SpendingBlocked):
            self.ledger.reserve(uuid.uuid4().hex)

    def test_day_rollover_does_not_reset_lifetime_cap(self):
        ledger = SpendLedger(self.path, policy(total=usd_units("0.03")))
        with mock.patch.object(ledger, "_day", return_value="2026-09-01"):
            ident = uuid.uuid4().hex
            ledger.reserve(ident)
            ledger.settle(ident, usd_units("0.01"))
        with mock.patch.object(ledger, "_day", return_value="2026-09-02"):
            self.assertEqual(ledger.report()["today_usd"], "0.000000000")
            with self.assertRaises(SpendingBlocked):
                ledger.reserve(uuid.uuid4().hex)

    def test_cache_write_and_reasoning_not_double_counted(self):
        usage = response(make_demo_request("hi"))["usage"]
        usage["output_tokens_details"] = {"reasoning_tokens": 80}
        cost, inp, out = policy().usage_cost(usage)
        self.assertEqual(cost, usd_units("0.00269"))  # 700*2 + 200*.2 + 100*2.5 + 100*10 / million
        self.assertEqual((inp, out), (1000, 100))

    def test_bad_usage_never_releases_reservation(self):
        for usage in (None, {}, {"input_tokens": True},
                      {"input_tokens": 1, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 0}},
                      {"input_tokens": 1, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}}):
            with self.subTest(usage=usage), self.assertRaises(SpendingBlocked):
                policy().usage_cost(usage)

    def test_corrupt_future_and_symlinked_ledgers_fail_closed(self):
        self.path.write_bytes(b"not sqlite")
        with self.assertRaises(SpendingBlocked):
            self.ledger.reserve(uuid.uuid4().hex)
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(SpendingBlocked):
            self.ledger.reserve(uuid.uuid4().hex)
        link = self.path.with_name("link.sqlite3")
        link.symlink_to(self.path)
        with self.assertRaises(SpendingBlocked):
            SpendLedger(link, policy()).report()

    def test_success_counts_full_prompt_then_records_usage(self):
        request = make_demo_request("오늘 함께 사진 찍을까? 🌻")
        raw = response(request)
        with mock.patch("plumbmate.providers.post_openai", side_effect=[{"object": "response.input_tokens", "input_tokens": 1000}, raw]) as http:
            result = self.provider().respond(request)
        self.assertEqual(result["request_id"], request["request_id"])
        count_body, generate_body = (call.args[1] for call in http.call_args_list)
        self.assertEqual(count_body, {key: generate_body[key] for key in ("model", "input", "instructions", "text")})
        context = json.loads(generate_body["input"][0]["content"])
        self.assertNotIn("request_id", context)
        self.assertNotIn("session_id", context)
        self.assertNotIn("created_at", context)
        self.assertNotIn("memory_summary", context)
        self.assertEqual(context["player_message"], request["player_message"])
        self.assertFalse(generate_body["store"])
        self.assertEqual(self.ledger.report()["recorded_usd"], "0.002690000")

    def test_cache_key_stable_across_transport_sessions(self):
        keys = []
        for index in range(2):
            request = make_demo_request("hi")
            request["session_id"] = "random-" + str(index)
            with mock.patch("plumbmate.providers.post_openai", side_effect=[{"object": "response.input_tokens", "input_tokens": 1000}, response(request)]) as http:
                self.provider().respond(request)
                keys.append(http.call_args_list[1].args[1]["prompt_cache_key"])
        self.assertEqual(keys[0], keys[1])

    def test_oversize_count_does_not_generate(self):
        with mock.patch("plumbmate.providers.post_openai", return_value={"object": "response.input_tokens", "input_tokens": 99999}) as http:
            with self.assertRaises(SpendingBlocked):
                self.provider().respond(make_demo_request("hi"))
        self.assertEqual(http.call_count, 1)
        self.assertEqual(self.ledger.report()["reserved_usd"], "0.000000000")

    def test_network_failure_holds_reservation_and_blocks_restart(self):
        with mock.patch("plumbmate.providers.post_openai", side_effect=ProtocolError("timeout")) as http:
            with self.assertRaises(ProtocolError):
                self.provider().respond(make_demo_request("hi"))
            with self.assertRaises(SpendingBlocked):
                self.provider(SpendLedger(self.path, policy())).respond(make_demo_request("again"))
            self.assertEqual(http.call_count, 1)

    def test_invalid_and_incomplete_answers_still_charged(self):
        for change in ({"output_text": "garbage"}, {"status": "incomplete"}, {"output_text": '{"surprise":true}'}):
            request = make_demo_request("hi")
            raw = dict(response(request), **change)
            with self.subTest(change=change), mock.patch("plumbmate.providers.post_openai", side_effect=[{"object": "response.input_tokens", "input_tokens": 1000}, raw]):
                with self.assertRaises(ProtocolError):
                    self.provider().respond(request)
            self.assertEqual(self.ledger.report()["unresolved_requests"], 0)
        self.assertEqual(self.ledger.report()["recorded_usd"], "0.008070000")

    def test_missing_usage_and_wrong_tier_hold_budget(self):
        for index, change in enumerate(({"usage": {}}, {"service_tier": "priority"})):
            ledger = SpendLedger(self.path.with_name(f"case{index}.db"), policy())
            request = make_demo_request("hi")
            with mock.patch("plumbmate.providers.post_openai", side_effect=[{"object": "response.input_tokens", "input_tokens": 1000}, dict(response(request), **change)]):
                with self.assertRaises(SpendingBlocked):
                    self.provider(ledger).respond(request)
            self.assertEqual(ledger.report()["unresolved_requests"], 1)

    def test_blocked_spending_reaches_game_receipt(self):
        paths = BridgePaths(Path(self.tmp.name) / "bridge")
        paths.ensure()
        request = make_demo_request("hi")
        atomic_write_json(paths.requests / (request["request_id"] + ".json"), request)
        engine = CompanionEngine(paths, self.provider(SpendLedger(self.path, policy(total=0))))
        with self.assertRaises(SpendingBlocked):
            engine.process_one()
        self.assertEqual(read_json(paths.state / ("turn-" + request["request_id"] + ".json"))["reason_code"], "spending_blocked")

    def test_cli_config_defaults_to_no_spending_and_keeps_ledger(self):
        config = Path(self.tmp.name) / "billing.json"
        args = ["budget", "configure", "--config", str(config), "--model", "fixture-model", "--valid-until", "2099-12-31",
                "--input-per-million", "2", "--cached-per-million", "0.2", "--cache-write-per-million", "2.5", "--output-per-million", "10"]
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(args), 0)
            self.assertEqual(main(["budget", "status", "--config", str(config)]), 0)
        self.assertEqual(SpendingPolicy.load(config).total, 0)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
