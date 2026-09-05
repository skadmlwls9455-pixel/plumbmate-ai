import importlib.util
from pathlib import Path
import unittest

source = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod/probe_core.py"
spec = importlib.util.spec_from_file_location("probe_core_under_test", source)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Interaction:
    pass


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.probe = core.OwnershipProbe("decision", "session", "epoch", 17)
        self.item = Interaction()
        self.probe.attach(self.item)

    def permit(self, item=None, **changes):
        args = dict(interaction=self.item if item is None else item,
                    session_id="session", epoch="epoch", actor_id=17, observed=[self.item])
        args.update(changes)
        return self.probe.may_cancel(**args)

    def start(self):
        self.probe.enqueued(True, [self.item])
        self.probe.observe_start(self.item)

    def test_pending_cannot_cancel(self):
        self.assertFalse(self.permit())

    def test_enqueued_not_started_cannot_cancel(self):
        self.probe.enqueued(True, [self.item])
        self.assertFalse(self.permit())

    def test_exact_object_after_start_can_cancel(self):
        self.start()
        self.assertTrue(self.permit())

    def test_forged_marker_does_not_confer_ownership(self):
        self.start()
        other = Interaction()
        setattr(other, core.MARKER, "decision")
        self.assertFalse(self.probe.observe_start(other))
        self.assertFalse(self.permit(other, observed=[other]))

    def test_disappeared_marker_does_not_break_exact_ledger(self):
        self.start()
        delattr(self.item, core.MARKER)
        self.assertTrue(self.permit())

    def test_unconfirmed_enqueue_cannot_cancel(self):
        self.probe.enqueued(True, [])
        self.probe.observe_start(self.item)
        self.assertFalse(self.permit())

    def test_rejected_enqueue_cannot_cancel(self):
        self.probe.enqueued(False, [self.item])
        self.assertFalse(self.permit())

    def test_stale_identity_and_scope_cannot_cancel(self):
        self.start()
        for changes in ({"epoch": "new"}, {"session_id": "new"},
                        {"actor_id": 18}, {"observed": []}):
            with self.subTest(changes=changes):
                self.assertFalse(self.permit(**changes))

    def test_invalidation_blocks_late_start(self):
        self.probe.invalidate("reset")
        self.assertFalse(self.probe.observe_start(self.item))
        self.assertFalse(self.permit())

    def test_at_most_one_cancel_request(self):
        self.start()
        self.probe.cancel_requested = True
        self.assertFalse(self.permit())

    def test_record_buffer_bounded(self):
        for _ in range(500):
            self.probe.record("sample", self.item)
        self.assertEqual(256, len(self.probe.records))

    def test_marker_unsupported_is_separate_from_object_ownership(self):
        class Slotted:
            __slots__ = ()
        probe = core.OwnershipProbe("d", "s", "e", 1)
        obj = Slotted()
        probe.attach(obj)
        self.assertEqual("marker_set_failed", probe.records[0]["phase"])
        probe.enqueued(True, [obj])
        probe.observe_start(obj)
        self.assertTrue(probe.may_cancel(obj, "s", "e", 1, [obj]))


if __name__ == "__main__":
    unittest.main()
