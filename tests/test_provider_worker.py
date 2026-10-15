"""Real spawn/cleanup tests with local callables; no network or paid models."""
import multiprocessing
import os
import time
import unittest
from unittest import mock

from plumbmate.protocol import ProtocolError
from plumbmate.provider_worker import MAX_RESULT_BYTES, post_openai_bounded, run_bounded


def successful():
    return {"text": "한글 응답 🌻", "pid": os.getpid()}


def stalled():
    time.sleep(10)
    return {"late": True}


def fails():
    raise RuntimeError("PRIVATE_SECRET_REMOTE_BODY")


def oversized():
    return {"text": "x" * (MAX_RESULT_BYTES + 1)}


class WorkerTests(unittest.TestCase):
    def test_worker_runs_outside_parent_and_leaves_no_child(self):
        result = run_bounded(successful, (), timeout=5)
        self.assertEqual(result["text"], "한글 응답 🌻")
        self.assertNotEqual(result["pid"], os.getpid())
        self.assertFalse(any(p.name == "PlumbMate-provider" for p in multiprocessing.active_children()))

    def test_deadline_cleanup_then_next_request_without_restart(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ProtocolError, "deadline"):
            run_bounded(stalled, (), timeout=0.5)
        self.assertLess(time.monotonic() - start, 4)
        self.assertIn("text", run_bounded(successful, (), timeout=5))
        self.assertFalse(any(p.name == "PlumbMate-provider" for p in multiprocessing.active_children()))

    def test_exception_and_oversize_never_echo_body_or_poison_next_turn(self):
        for target in (fails, oversized):
            with self.assertRaises(ProtocolError) as caught:
                run_bounded(target, (), timeout=5)
            self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertIn("text", run_bounded(successful, (), timeout=5))

    def test_provider_paths_deadlines_and_no_retry(self):
        with mock.patch("plumbmate.provider_worker.run_bounded", side_effect=ProtocolError()) as run:
            for path, deadline in (("/responses", 60), ("/responses/input_tokens", 30)):
                with self.assertRaises(ProtocolError):
                    post_openai_bounded(path, {}, "fake")
                self.assertEqual(run.call_args.kwargs["timeout"], deadline)
            with self.assertRaises(ProtocolError):
                post_openai_bounded("https://other.invalid", {}, "fake")
            self.assertEqual(run.call_count, 2)

    def test_spawn_failure_is_contained(self):
        with mock.patch("multiprocessing.context.SpawnProcess.start", side_effect=OSError("private")):
            with self.assertRaises(ProtocolError) as caught:
                run_bounded(successful, (), timeout=5)
        self.assertNotIn("private", str(caught.exception))
