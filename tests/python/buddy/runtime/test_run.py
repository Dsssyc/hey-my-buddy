"""Real cross-process C-Two integration for private command executions.

Everything runs against an isolated ``HMB_STATE_DIR`` with the real daemon, the
real execution processes and the real ``command`` adapter; no model is called.
The command execution records are seeded directly into the private board; the
micro task lifecycle and Host ``await`` are covered by workflow tests. The
internal wait here is read-only even when its own window ends.
"""
from __future__ import annotations

import sys
import unittest

from support import BoardTestCase, wait_for
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.transport import ServiceError


PYTHON = sys._base_executable


class TestExecutionRecords(BoardTestCase):
    def client(self):
        return BoardClient(self.directory, autostart=False)

    def command_task(self, request_id: str, argv: list[str], *, expected_seconds: int = 120) -> dict:
        return self.submit_execution(
            requestId=request_id, task="run one command", cwd=str(self.workdir()),
            adapter="command", argv=argv, expectedSeconds=expected_seconds,
        )["task"]

    def test_internal_execution_completes_one_real_task_and_delivers_its_result(self):
        with self.daemon():
            marker = self.directory / "execution-marker.txt"
            task = self.command_task("exec-1", [
                PYTHON, "-c", "import sys; from pathlib import Path; "
                "Path(sys.argv[1]).write_text('done\\n', encoding='utf-8'); print('finished')", str(marker)])
            self.assertEqual(task["requestId"], "exec-1")
            self.assertEqual(task["state"], "queued")
            self.assertTrue(task["queueReason"], "a queued task must say why it is waiting")
            self.assertNotIn("taskId", task)

            finished = wait_for(lambda: self.client().get(runId=task["runId"])["state"] == "completed", 60)
            self.assertTrue(finished)
            envelope = self.client().result(runId=task["runId"])
            self.assertTrue(envelope["resultDelivered"])
            self.assertTrue(envelope["shutdownConfirmed"])
            self.assertEqual(envelope["state"], "completed")
            self.assertEqual(envelope["runId"], task["runId"])
            self.assertIn("argv", envelope["result"])
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "done")

    def test_internal_execution_submit_is_idempotent_for_the_same_request_id(self):
        with self.daemon():
            payload = {
                "requestId": "exec-recover-1",
                "task": "one task only",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": [PYTHON, "-c", "pass"],
                "expectedSeconds": 120,
            }
            first = self.command_task("exec-recover-1", [PYTHON, "-c", "pass"])
            second = self.command_task("exec-recover-1", [PYTHON, "-c", "pass"])
            self.assertEqual(second["runId"], first["runId"])
            self.assertEqual(self.client().list_tasks()["total"], 1)

            with self.assertRaises(BoardError) as conflict:
                self.submit_execution(**{**payload, "task": "changed"})
            self.assertEqual(conflict.exception.code, "CONFLICT")
            self.assertEqual(self.client().list_tasks()["total"], 1)

    def test_internal_wait_never_launches_work_and_honours_its_wait_window(self):
        with self.daemon():
            with self.assertRaises(ServiceError) as missing:
                self.client().wait_task(1000, runId="never-admitted")
            self.assertEqual(missing.exception.code, "NOT_FOUND")
            self.assertEqual(self.client().list_tasks()["total"], 0)

            task = self.command_task("exec-await-1", [PYTHON, "-c", "import time; time.sleep(4)"])
            self.assertTrue(wait_for(lambda: self.client().get(runId=task["runId"])["state"] == "running", 30))
            running = self.client().get(runId=task["runId"])
            observed = self.client().wait_task(500, runId=task["runId"], afterRevision=running["revision"])
            self.assertFalse(observed["resultAvailable"])
            self.assertEqual(observed["runId"], task["runId"])
            self.assertTrue(wait_for(lambda: self.client().get(runId=task["runId"])["state"] == "completed", 30))
            self.assertEqual(self.client().get(runId=task["runId"])["createdAt"], task["createdAt"])

    def test_internal_get_resolves_an_existing_run_by_request_id_without_starting_it(self):
        with self.daemon():
            task = self.command_task("exec-by-request", [PYTHON, "-c", "print('hello')"])
            envelope = self.client().get(requestId="exec-by-request")
            self.assertEqual(envelope["runId"], task["runId"])
            self.assertEqual(self.client().list_tasks()["total"], 1)

    def test_status_result_and_list_keep_the_stable_envelope(self):
        with self.daemon():
            task = self.command_task("exec-envelope-1", [PYTHON, "-c", "print('hello')"])
            status = self.client().get(runId=task["runId"])
            self.assertEqual(status["requestId"], "exec-envelope-1")
            self.assertEqual(status["runId"], task["runId"])
            self.assertIn(status["state"], ("queued", "running", "completed"))
            self.assertTrue(wait_for(lambda: self.client().get(runId=task["runId"])["state"] == "completed", 30))
            result = self.client().result(runId=task["runId"])
            self.assertTrue(result["resultAvailable"])
            self.assertEqual(result["resultMeta"]["status"], "ok")
            page = self.client().list_tasks(limit=10)
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["runs"][0]["runId"], task["runId"])

    def test_micro_task_get_and_execution_status_stay_distinct(self):
        with self.daemon():
            task = self.command_task("exec-distinct-1", [PYTHON, "-c", "pass"])
            # The micro task view answers for an ordinary execution record too, but it
            # says so instead of inventing micro task state.
            view = self.client().workflow_get(runId=task["runId"])
            self.assertNotIn("microTask", view)
            self.assertEqual(view["runId"], task["runId"])
            self.assertIn("task", view)
            status = self.client().get(runId=task["runId"])
            self.assertEqual(status["runId"], task["runId"])
            self.assertNotIn("microTask", status)

    def test_internal_execution_cancel_settles_with_shutdown_evidence(self):
        with self.daemon():
            task = self.command_task("exec-cancel-1", [PYTHON, "-c", "import time; time.sleep(30)"])
            self.assertTrue(wait_for(lambda: self.client().get(runId=task["runId"])["state"] == "running", 30))
            cancelled = self.client().cancel(runId=task["runId"], note="private test stopped it")
            self.assertTrue(cancelled["cancelRequested"])
            done = wait_for(
                lambda: self.client().get(runId=task["runId"])["state"] in ("cancelled", "failed", "reconciliation-needed"),
                60,
            )
            self.assertTrue(done, "the worker must observe the cancel intent and stop its own child")
            final = self.client().get(runId=task["runId"])
            self.assertIn(final["state"], ("cancelled", "reconciliation-needed"))
            self.assertTrue(final["shutdownConfirmed"])


if __name__ == "__main__":
    unittest.main()
