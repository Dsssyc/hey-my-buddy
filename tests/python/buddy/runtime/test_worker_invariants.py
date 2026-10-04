"""Worker invariants that must survive the role seam (ADR-025 step 1-C).

The seam routes the runtime's prepare/start/collect/cancel through
``hey_my_buddy.buddy.roles.controller``; these tests pin the protections the
micro-task names explicitly: a spawn-marker write failure after start must
never drop the live handle or the attempt, and preparation results keep their
exact receipt mapping. Real private state, real ``command`` children, no board
daemon, no harness and no model.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.worker import TERMINATION_COMPLETED, TERMINATION_HARNESS_ERROR, Worker

#: Inherited variables that would point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


def silent(_message: str) -> None:
    """A private worker log sink; the tests assert state, not log prose."""


class MarkerWriteFailureTests(unittest.TestCase):
    """A marker write that fails after the spawn keeps the live handle supervising."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-marker-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        self.worker = Worker("w-marker", self.state, client=BoardClient(self.state, autostart=False), log=silent)

    def claim(self, attempt_id: str, task_id: str) -> dict:
        return {
            "attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "command", "cwd": str(self.work), "task": "marker invariant",
                              "timeoutSeconds": 30,
                              "argv": [sys.executable, "-c", "print('marker-ok')"]}},
        }

    def test_marker_failure_after_spawn_never_drops_the_live_handle(self):
        attempt_id, task_id = "attempt-marker", "task-marker"
        directory = self.state / "attempts" / task_id / attempt_id
        original = worker_module.fsync_json

        def failing(path, value):
            if Path(path).name == "spawn.marker":
                raise OSError("injected marker write failure")
            return original(path, value)

        with mock.patch("hey_my_buddy.buddy.runtime.worker.fsync_json", side_effect=failing):
            receipt = self.worker.execute(self.claim(attempt_id, task_id))
        report = receipt["report"]
        # The durable intent exists, the marker write failed, and the attempt
        # still ran to a completed receipt: the live handle supervised it.
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(report["result"]["finalText"], "marker-ok")
        self.assertIs(report["shutdownConfirmed"], True)

    def test_a_clean_attempt_still_writes_both_marker_files(self):
        receipt = self.worker.execute(self.claim("attempt-clean", "task-clean"))
        directory = self.state / "attempts" / "task-clean" / "attempt-clean"
        report = receipt["report"]
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertTrue((directory / "spawn.marker").is_file())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)

    def test_preparation_refusals_keep_their_exact_receipts(self):
        unavailable = self.claim("attempt-unavailable", "task-unavailable")
        unavailable["task"]["spec"]["argv"] = ["/buddy-tests/no-such-executable"]
        receipt = self.worker.execute(unavailable)
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["result"])
        self.assertIn("not an executable this worker can find", report["error"])
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)
        directory = self.state / "attempts" / "task-unavailable" / "attempt-unavailable"
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())


class PreparationBoardErrorTests(unittest.TestCase):
    """The captured prepare() BoardError keeps its object, code and retry boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-prep-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"

    def worker(self, client=None):
        return Worker("w-prep", self.state, client=client, log=silent)

    @staticmethod
    def claim(task_id: str = "task-prep") -> dict:
        return {
            "attempt": {"attemptId": "attempt-prep", "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "dsh", "cwd": "/buddy-tests/nowhere", "task": "prep",
                              "timeoutSeconds": 30}},
        }

    class FailingExecutor:
        name = "fake"

        def __init__(self, error):
            self.error = error
            self.prepared = 0

        def available(self):
            return True, None

        def prepare(self, context):
            self.prepared += 1
            raise self.error

    def test_a_harness_history_reraises_the_original_board_error_object(self):
        error = BoardError("ADAPTER_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            holder = {"harnessHistory": [{"retry": False, "harness": {"adapter": "dsh", "revision": 3}}]}
            with self.assertRaises(BoardError) as caught:
                worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                         claim["attempt"], directory, holder)
        # The very exception object the adapter raised reaches the guarded retry:
        # same identity and code, never a bare-raise RuntimeError.
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertEqual(executor.prepared, 1)

    def test_without_a_harness_history_the_refusal_becomes_its_exact_receipt(self):
        error = BoardError("ADAPTER_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            receipt = worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                               claim["attempt"], directory, {})
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "ADAPTER_UNAVAILABLE: the native binary is missing")
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)

    def test_the_guarded_retry_reruns_a_flaky_prepare_and_recovers(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome

        record = {"adapter": "dsh", "available": True, "revision": 7, "status": "ready"}

        class StubClient:
            def __init__(self):
                self.calls = []

            def call(self, method, params):
                self.calls.append((method, params))
                if method == "harness_prepare":
                    return {"harness": record}
                return {"task": {}}

            def renew(self, *args, **kwargs):
                return {}

        class Handle:
            cancel_requested = False
            pid = 4242
            pgid = None

            def wait(self, timeout=None):
                return 0

            def shutdown_confirmed(self):
                return True

        class FlakyExecutor:
            name = "fake"

            def __init__(self):
                self.prepares = 0
                self.handle = Handle()

            def available(self):
                return True, None

            def prepare(self, context):
                self.prepares += 1
                if self.prepares == 1:
                    raise BoardError("ADAPTER_UNAVAILABLE", "first attempt fails")
                return None

            def start(self, context):
                return self.handle

            def collect(self, handle, context):
                return AdapterOutcome(status="ok", result={"status": "ok", "modelStarted": True},
                                      shutdown_confirmed=True)

            def cancel(self, handle, *, grace_seconds=None):
                self.handle.cancel_requested = True

        executor = FlakyExecutor()
        client = StubClient()
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            receipt = self.worker(client).execute(self.claim("task-retry"))
        report = receipt["report"]
        # The re-raised BoardError from the first prepare is exactly what sends
        # _execute_guarded around its second harness_prepare attempt, where the
        # same executor's prepare succeeds and the attempt completes.
        self.assertEqual(executor.prepares, 2)
        prepare_calls = [params for method, params in client.calls if method == "harness_prepare"]
        self.assertEqual(len(prepare_calls), 2)
        self.assertTrue(prepare_calls[1]["commandId"].endswith("-1"))
        self.assertTrue(prepare_calls[1]["retry"] is True)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(len(report["result"]["harnessAttempts"]), 2)


if __name__ == "__main__":
    unittest.main()
