import unittest

from plumbmate.budget import estimate_tokens, state_delta


class BudgetTests(unittest.TestCase):
    def test_delta_contains_changed_and_removed_fields(self):
        previous = {"mood": "HAPPY", "room": "kitchen", "temporary": True}
        current = {"mood": "FLIRTY", "room": "kitchen"}
        self.assertEqual(state_delta(previous, current), {"mood": "FLIRTY", "_removed": ["temporary"]})

    def test_token_estimate_is_positive(self):
        self.assertGreater(estimate_tokens({"message": "안녕"}), 0)


if __name__ == "__main__":
    unittest.main()

