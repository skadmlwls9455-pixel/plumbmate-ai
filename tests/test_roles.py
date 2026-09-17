import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from plumbmate.budget import clamp_request
from plumbmate.cli import make_demo_request
from plumbmate.dialogue import RoleSetupRequired, dialogue_context
from plumbmate.engine import CompanionEngine
from plumbmate.protocol import BridgePaths, atomic_write_json, read_json
from plumbmate.providers import MockProvider

ROOT = Path(__file__).resolve().parents[1]


def load_game_module(filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "game_mod/src/plumbmate_mod" / (filename + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


binding_module = load_game_module("role_binding")
ui_module = load_game_module("role_setup_ui")


class RoleTests(unittest.TestCase):
    def test_mock_confirms_transport_without_agreeing_or_creating_memories(self):
        for text in ("싫어. 오늘은 혼자 있고 싶어", "안녕", "가" * 100):
            with self.subTest(text=text):
                response = MockProvider().respond(make_demo_request(text))
                self.assertIn("실제 AI 대화가 아닌 수신 확인", response["partner_message"])
                self.assertIn(text[:80], response["partner_message"])
                self.assertNotIn("좋아.", response["partner_message"])
                self.assertEqual(response["memory_patch"], [])
                self.assertEqual(response["action"]["name"], "suggest_only")
                if len(text) > 80:
                    self.assertIn("…", response["partner_message"])

    def setUp(self):
        self.zone, self.household = object(), object()
        # Identical display names are intentionally allowed; IDs are distinct.
        self.player = types.SimpleNamespace(sim_id=1, name="Same")
        self.partner = types.SimpleNamespace(sim_id=2, name="Same")
        self.members = [self.player, self.partner]
        self.binding = binding_module.RoleBinding()

    def bind(self):
        self.binding.bind(self.zone, self.household, self.player, self.partner, self.members)

    def test_unassigned_and_same_sim_are_not_partner_play(self):
        with self.assertRaises(ValueError):
            self.binding.resolve(self.zone, self.household, self.members)
        with self.assertRaises(ValueError):
            self.binding.bind(self.zone, self.household, self.player, self.player, self.members)

    def test_same_name_roles_remain_distinct_and_not_selected_sim_dependent(self):
        self.bind()
        roles = self.binding.wire()
        self.assertEqual((roles["player_sim_id"], roles["partner_sim_id"]), (1, 2))
        self.assertEqual(self.binding.resolve(self.zone, self.household, list(reversed(self.members))),
                         (self.player, self.partner))

    def test_zone_or_household_replacement_invalidates_binding(self):
        for zone, household in ((object(), self.household), (self.zone, object())):
            self.bind()
            with self.assertRaises(ValueError):
                self.binding.resolve(zone, household, self.members)
            self.assertIsNone(self.binding.binding_id)

    def test_deleted_recreated_or_changed_sim_is_not_silently_rebound(self):
        for members in ([self.player], [self.player, types.SimpleNamespace(sim_id=2)]):
            self.bind()
            with self.assertRaises(ValueError):
                self.binding.resolve(self.zone, self.household, members)
        self.bind()
        self.partner.sim_id = 3
        with self.assertRaises(ValueError):
            self.binding.resolve(self.zone, self.household, self.members)

    def test_duplicate_ids_and_nonmember_refused(self):
        other = types.SimpleNamespace(sim_id=1)
        with self.assertRaises(ValueError):
            self.binding.bind(self.zone, self.household, self.player, other, [self.player, other])
        with self.assertRaises(ValueError):
            self.binding.bind(self.zone, self.household, self.player, self.partner, [self.player])

    def test_reset_or_reassignment_changes_binding_token(self):
        self.bind()
        old = self.binding.binding_id
        self.bind()
        self.assertNotEqual(old, self.binding.binding_id)
        self.binding.clear()
        with self.assertRaises(ValueError):
            self.binding.wire()

    def test_companion_never_assumes_actor_is_partner(self):
        request = make_demo_request("안녕")
        context = dialogue_context(request)
        self.assertEqual(context["player"]["sim_id"], 2)
        self.assertEqual(context["partner"]["sim_id"], 1)
        self.assertIn("연결 테스트", MockProvider().respond(request)["partner_message"])

    def test_missing_same_or_foreign_roles_fail_before_provider_and_budget(self):
        for case in ("missing", "same", "foreign", "wrong_player", "duplicate", "overflow"):
            request = make_demo_request("hi")
            if case == "missing":
                request.pop("roles")
            elif case == "same":
                request["roles"]["partner_sim_id"] = 2
            elif case == "foreign":
                request["roles"]["partner_sim_id"] = 3
            elif case == "wrong_player":
                request["actor"]["sim_id"] = 1
            elif case == "overflow":
                request["roles"]["partner_sim_id"] = "99999999999999999999"
            else:
                request["household"].append(request["household"][0])
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                paths = BridgePaths(Path(directory))
                paths.ensure()
                atomic_write_json(paths.requests / (request["request_id"] + ".json"), request)
                provider = mock.Mock()
                engine = CompanionEngine(paths, provider)
                self.assertIsNone(engine.process_one())
                provider.respond.assert_not_called()
                self.assertEqual(engine.budget.attempts, 0)
                receipt = read_json(paths.state / ("turn-" + request["request_id"] + ".json"))
                self.assertEqual(receipt["reason_code"], "roles_required")

    def test_context_packing_preserves_both_roles(self):
        request = make_demo_request("🌻" * 1000)
        request["household"] = [{"sim_id": i, "name": "🌻" * 128} for i in range(1, 13)]
        request["recent_events"] = ["🌻" * 240 for _ in range(8)]
        request["memory_summary"] = "🌻" * 1600
        packed = clamp_request(request)
        self.assertEqual({1, 2} & {member["sim_id"] for member in packed["household"]}, {1, 2})
        dialogue_context(packed)


class SetupPickerTests(unittest.TestCase):
    bind = RoleTests.bind

    def setUp(self):
        RoleTests.setUp(self)
        self.ui = ui_module.RoleSetupUI()
        self.dialogs = []
        self.lines = []
        dialogs = self.dialogs

        class Picker:
            def __init__(self, sim_filter=None, *, owner, **kwargs):
                # Native UiSimPicker's first argument is NOT owner. A permissive
                # *args stub would hide the positional-owner integration bug.
                self.owner = owner
                self.rows = []
                dialogs.append(self)

            def add_row(self, row):
                self.rows.append(row)

            def show_dialog(self, on_response):
                self.callback = on_response

            @staticmethod
            def TunableFactory():
                return types.SimpleNamespace(default=Picker)

        package = types.ModuleType("ui")
        picker = types.ModuleType("ui.ui_dialog_picker")
        picker.UiSimPicker = Picker
        picker.SimPickerRow = lambda **kwargs: types.SimpleNamespace(**kwargs)
        self.patches = mock.patch.dict("sys.modules", {"ui": package, "ui.ui_dialog_picker": picker})
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.text_patch = mock.patch.object(ui_module, "_text", side_effect=str)
        self.text_patch.start()
        self.addCleanup(self.text_patch.stop)
        self.notify_patch = mock.patch.object(ui_module, "notify")
        self.notify = self.notify_patch.start()
        self.addCleanup(self.notify_patch.stop)

    def open(self):
        self.ui.open(lambda: (self.zone, self.household, self.members, self.player),
                     self.binding, self.lines.append, lambda member: member.name)

    def choose(self, dialog, member, accepted=True):
        dialog.callback(types.SimpleNamespace(accepted=accepted, get_result_tags=lambda: [member]))

    def test_single_sim_has_actionable_notice_and_no_picker(self):
        self.members = [self.player]
        self.open()
        self.assertEqual(self.dialogs, [])
        self.assertTrue(any("한 명뿐" in line for line in self.lines))

    def test_two_step_selection_excludes_player_then_binds(self):
        self.open()
        self.assertIs(self.dialogs[0].owner, self.player)
        self.choose(self.dialogs[0], self.player)
        self.assertEqual([row.tag for row in self.dialogs[1].rows], [self.partner])
        self.choose(self.dialogs[1], self.partner)
        self.assertEqual(self.binding.wire()["partner_sim_id"], 2)
        self.notify.assert_called_once()

    def test_selection_continues_once_to_chat_without_an_extra_notice(self):
        completed = mock.Mock()
        self.ui.open(lambda: (self.zone, self.household, self.members, self.player),
                     self.binding, self.lines.append, lambda member: member.name, on_complete=completed)
        self.choose(self.dialogs[0], self.player)
        completed.assert_not_called()
        self.choose(self.dialogs[1], self.partner)
        self.choose(self.dialogs[1], self.partner)
        completed.assert_called_once()
        self.notify.assert_not_called()
        self.assertEqual(self.binding.wire()["partner_sim_id"], 2)

    def test_repeated_callbacks_do_not_reopen_or_rebind(self):
        self.open()
        self.choose(self.dialogs[0], self.player)
        self.choose(self.dialogs[0], self.player)
        self.assertEqual(len(self.dialogs), 2)
        self.choose(self.dialogs[1], self.partner)
        token = self.binding.binding_id
        self.choose(self.dialogs[1], self.partner)
        self.assertEqual(self.binding.binding_id, token)
        self.notify.assert_called_once()

    def test_cancel_preserves_previous_binding(self):
        self.bind()
        old = self.binding.binding_id
        self.open()
        self.choose(self.dialogs[0], self.player, accepted=False)
        self.assertEqual(self.binding.binding_id, old)

    def test_stale_dialog_or_roster_change_does_not_assign_roles(self):
        self.open()
        old_dialog = self.dialogs[0]
        self.open()
        self.choose(old_dialog, self.player)
        self.assertEqual(len(self.dialogs), 2)
        self.zone = object()
        self.choose(self.dialogs[1], self.player)
        self.assertIsNone(self.binding.binding_id)


if __name__ == "__main__":
    unittest.main()
