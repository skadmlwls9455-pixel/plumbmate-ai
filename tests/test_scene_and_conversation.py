"""Pure extracted policies, separate from command registration/native UI."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock


def load(name):
    source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scene = load("scene_adapter")
conversation = load("conversation_state")


class ConversationTests(unittest.TestCase):
    def test_empty_and_changed_binding_never_reveal_previous_turn(self):
        state = conversation.ConversationState()
        self.assertIsNotNone(state.ineligible_reason(None, 10))
        state.sent("r1", "b1", 10)
        self.assertIsNone(state.ineligible_reason("b1", 10))
        self.assertIsNotNone(state.ineligible_reason("b2", 10))

    def test_exact_expiry_boundary_and_custom_timeout(self):
        state = conversation.ConversationState()
        state.sent("r1", "b1", 10)
        self.assertIsNone(state.ineligible_reason("b1", 189.999))
        self.assertIsNotNone(state.ineligible_reason("b1", 190))
        self.assertIsNotNone(state.ineligible_reason("b1", 11, ttl_seconds=1))

    def test_display_and_dismiss_are_distinct_and_do_not_block_next_request(self):
        for method, text in (("display", "이미 표시"), ("dismiss", "기다리기를 끝낸")):
            with self.subTest(method=method):
                state = conversation.ConversationState()
                state.sent("r1", "b1", 0)
                getattr(state, method)()
                self.assertIn(text, state.ineligible_reason("b1", 1))
                state.sent("r2", "b1", 2)
                self.assertIsNone(state.ineligible_reason("b1", 3))

    def test_state_storage_remains_constant_and_contains_no_dialogue_history(self):
        state = conversation.ConversationState()
        fields = set(vars(state))
        for index in range(10000):
            state.sent(str(index), "binding", index)
            state.display()
        self.assertEqual(set(vars(state)), fields)
        self.assertEqual(state.request_id, "9999")
        self.assertEqual(len(fields), 5)


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.player = NS(sim_id=1, full_name="Player")
        self.partner = NS(sim_id=2, full_name="Partner")
        self.members = [self.player, self.partner]
        self.household = NS(sim_info_gen=lambda: iter(self.members))
        self.player_sim = NS(sim_info=self.player, household=self.household)
        self.partner_sim = NS(sim_info=self.partner, household=self.household)
        self.player.get_sim_instance = lambda: self.player_sim
        self.partner.get_sim_instance = lambda: self.partner_sim

    def test_console_free_and_explicit_zero_connection_use_different_resolution(self):
        manager = mock.Mock()
        manager.get_first_client.return_value = NS(active_sim=self.player_sim)
        manager.get.return_value = NS(active_sim=self.partner_sim)
        services = NS(client_manager=lambda: manager)
        self.assertIs(scene.active_sim(services, None), self.player_sim)
        self.assertIs(scene.active_sim(services, 0), self.partner_sim)
        manager.get.assert_called_once_with(0)
        manager.get_first_client.assert_called_once_with()

    def test_missing_and_transitioning_client_fail_quietly(self):
        self.assertIsNone(scene.active_sim(NS(client_manager=lambda: None), None))
        manager = mock.Mock(side_effect=RuntimeError("teardown"))
        self.assertIsNone(scene.active_sim(NS(client_manager=manager), None))

    def test_snapshot_is_bounded_and_optional_mood_failure_is_contained(self):
        self.player.full_name = "가" * 200
        self.player_sim.get_mood = mock.Mock(side_effect=RuntimeError("unknown"))
        self.members = [NS(sim_id=n, full_name="심") for n in range(20)]
        actor, household = scene.snapshot(self.player_sim)
        self.assertEqual(len(actor["name"]), 128)
        self.assertNotIn("mood", actor)
        self.assertEqual(len(household), 12)

    def test_roster_failure_does_not_invent_other_sims(self):
        self.household.sim_info_gen = mock.Mock(side_effect=RuntimeError("unloaded"))
        actor, household = scene.snapshot(self.player_sim)
        self.assertEqual(household, [{"sim_id": actor["sim_id"], "name": actor["name"]}])

    def test_role_environment_rejects_missing_or_oversized_household(self):
        services = NS(current_zone=lambda: object())
        for sim in (None, NS(household=None)):
            with self.assertRaises(ValueError):
                scene.role_environment(services, sim)
        self.members = [self.player] * 13
        with self.assertRaises(ValueError):
            scene.role_environment(services, self.player_sim)

    def test_selected_partner_never_changes_bound_player(self):
        binding = mock.Mock()
        binding.resolve.return_value = self.player, self.partner
        environment = object(), self.household, self.members, self.partner
        player, partner = scene.bound_player(binding, environment, self.partner_sim)
        self.assertIs(player, self.player_sim)
        self.assertIs(partner, self.partner)
        self.partner_sim = None
        with self.assertRaises(ValueError):
            scene.bound_player(binding, environment, self.player_sim)


if __name__ == "__main__":
    unittest.main()
