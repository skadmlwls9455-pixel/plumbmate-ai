"""Homeostatic drive urgency and the one-line state the model reads."""

import unittest
import uuid

from plumbmate.contracts import ProtocolError, validate_request
from plumbmate.drives import MAX_STATE_CHARS, assess, band, describe, dominant, urgency


def request(**actor_extra):
    actor = {"sim_id": 1, "name": "케이트"}
    actor.update(actor_extra)
    return {
        "protocol_version": "0.1",
        "request_id": uuid.uuid4().hex,
        "kind": "turn",
        "created_at": "2026-09-06T00:00:00+00:00",
        "player_message": "안녕",
        "session_id": "household-1",
        "actor": actor,
    }


class UrgencyTests(unittest.TestCase):
    def test_bounds_and_set_point(self):
        self.assertEqual(urgency(100), 0.0)
        self.assertEqual(urgency(50), 0.0)
        self.assertEqual(urgency(-100), 1.0)
        self.assertAlmostEqual(urgency(-25), 0.5)
        self.assertEqual(urgency("garbage"), 0.0)
        self.assertEqual(urgency(-9999), 1.0)

    def test_urgency_is_monotonic_in_deprivation(self):
        levels = [urgency(value) for value in range(100, -101, -10)]
        self.assertEqual(levels, sorted(levels))

    def test_assess_ignores_unknown_keys_and_non_dicts(self):
        self.assertEqual(assess(None), {})
        self.assertEqual(assess({"hunger": -60, "mana": 5}), {"hunger": urgency(-60)})

    def test_dominant_needs_a_pressing_motive(self):
        self.assertIsNone(dominant({}))
        self.assertIsNone(dominant({"hunger": 0.1, "fun": 0.15}))
        self.assertEqual(dominant({"hunger": 0.6, "fun": 0.9}), ("fun", 0.9))
        # Ties resolve by name, deterministically.
        self.assertEqual(dominant({"social": 0.5, "energy": 0.5}), ("energy", 0.5))

    def test_bands(self):
        self.assertEqual([band(0.1), band(0.3), band(0.6), band(0.95)], ["낮음", "보통", "높음", "매우 높음"])


class DescribeTests(unittest.TestCase):
    def test_empty_snapshot_yields_empty_line(self):
        self.assertEqual(describe(None), "")
        self.assertEqual(describe({}), "")

    def test_calm_motives_say_so(self):
        self.assertEqual(describe({"hunger": 80, "energy": 70}), "욕구: 안정")

    def test_pressing_motives_are_ordered_by_urgency_then_name(self):
        # hunger and energy tie at -60; the tie-break is the English key, so 피로 (energy) leads.
        line = describe({"hunger": -60, "fun": -10, "social": 40, "energy": -60}, mood="Mood_Happy",
                        buffs=["buff_Hungry", "  buff_Tired  "])
        self.assertEqual(line, "욕구: 피로 높음, 배고픔 높음, 지루함 보통 | 기분: Mood_Happy | 버프: buff_Hungry, buff_Tired")

    def test_output_is_bounded(self):
        line = describe({name: -100 for name in ("hunger", "energy", "social", "fun", "hygiene", "bladder")},
                        mood="M" * 500, buffs=["b" * 500] * 12)
        self.assertLessEqual(len(line), MAX_STATE_CHARS)


class ContractTests(unittest.TestCase):
    def test_motives_and_buffs_are_accepted_within_range(self):
        validate_request(request(motives={"hunger": -60, "fun": 100}, buffs=["buff_Hungry"]))

    def test_out_of_range_or_unknown_motives_are_rejected(self):
        with self.assertRaises(ProtocolError):
            validate_request(request(motives={"hunger": 150}))
        with self.assertRaises(ProtocolError):
            validate_request(request(motives={"mana": 10}))
        with self.assertRaises(ProtocolError):
            validate_request(request(buffs=["x" * 61]))

    def test_drive_state_is_a_bounded_top_level_string(self):
        turn = request()
        turn["drive_state"] = "욕구: 배고픔 높음"
        validate_request(turn)
        turn["drive_state"] = "x" * 241
        with self.assertRaises(ProtocolError):
            validate_request(turn)


if __name__ == "__main__":
    unittest.main()
