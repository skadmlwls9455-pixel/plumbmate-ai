import unittest
import copy

from plumbmate.budget import MAX_CONTEXT_BYTES, SessionBudget, canonical_json, clamp_request, estimate_tokens, state_delta
from plumbmate.cli import make_demo_request
from plumbmate.contracts import ProtocolError, validate_request


class BudgetTests(unittest.TestCase):
    def test_delta_contains_changed_and_removed_fields(self):
        previous = {"mood": "HAPPY", "room": "kitchen", "temporary": True}
        current = {"mood": "FLIRTY", "room": "kitchen"}
        self.assertEqual(state_delta(previous, current), {"mood": "FLIRTY", "_removed": ["temporary"]})

    def test_token_estimate_is_positive(self):
        self.assertGreater(estimate_tokens({"message": "안녕"}), 0)

    def test_worst_case_context_fits_without_changing_live_message(self):
        request = make_demo_request("🌻" * 1000)
        request["actor"]["name"] = "🌻" * 128
        request["household"] = [{"sim_id": i, "name": "🌻" * 128} for i in range(12)]
        request["recent_events"] = ["🌻" * 240 for _ in range(8)]
        request["memory_summary"] = "🌻" * 1600
        original = copy.deepcopy(request)
        self.assertGreater(len(canonical_json(request).encode("utf-8")), MAX_CONTEXT_BYTES)
        compact = clamp_request(request)
        self.assertLessEqual(len(canonical_json(compact).encode("utf-8")), MAX_CONTEXT_BYTES)
        self.assertEqual(compact["player_message"], request["player_message"])
        self.assertEqual(compact["actor"], request["actor"])
        self.assertEqual(request, original)
        validate_request(compact)

    def test_impossible_budget_fails_instead_of_hiding_current_message(self):
        with self.assertRaises(ProtocolError):
            clamp_request(make_demo_request("hello"), max_context_bytes=1)

    def test_one_budget_counts_each_reserved_attempt_once(self):
        budget = SessionBudget(max_attempts=2)
        request = make_demo_request("hello")
        self.assertTrue(budget.reserve(request))
        self.assertTrue(budget.reserve(request))
        self.assertFalse(budget.reserve(request))
        self.assertEqual(budget.attempts, 2)
        self.assertEqual(budget.estimated_context_tokens, estimate_tokens(request) * 2)


if __name__ == "__main__":
    unittest.main()
