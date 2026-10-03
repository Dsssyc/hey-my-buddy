"""Real cross-process CLI/C-Two integration for execution records and the read-only wait.

Everything runs against an isolated ``BUDDY_STATE_DIR`` with the real daemon, the
real local worker supervisor and the real ``command`` adapter; no model is called.
The CLI is invoked exactly as a user invokes it from an unrelated working directory.

``execution-*`` is the advanced surface the command/external adapters and internal
decision infrastructure own; the governed goal lifecycle is covered by the workflow
tests. ``await`` is read-only: these tests pin that it never starts, retries or
cancels a run, including when its own wait window ends.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase, wait_for


class TestExecutionRecords(BoardTestCase):
    def command_task(self, request_id: str, argv: list[str], *, timeout_seconds: int = 120) -> dict:
        code, task = self.cli(
            "execution-submit",
            json.dumps(
                {
                    "requestId": request_id,
                    "task": "run one command",
                    "cwd": str(self.workdir()),
                    "adapter": "command",
                    "argv": argv,
                    "timeoutSeconds": timeout_seconds,
                }
            ),
        )
        self.assertEqual(code, 0, task)
        return task

    def test_execution_submit_completes_one_real_task_and_await_delivers_its_result(self):
        with self.daemon():
            marker = self.directory / "execution-marker.txt"
            task = self.command_task("exec-1", ["/bin/sh", "-c", f"echo done > {marker}; echo finished"])
            self.assertEqual(task["requestId"], "exec-1")
            self.assertEqual(task["status"], "queued")
            self.assertTrue(task["queueReason"], "a queued task must say why it is waiting")
            self.assertEqual(task["runId"], task["taskId"])

            code, envelope = self.cli("await", json.dumps({"runId": task["runId"], "waitSeconds": 60}), timeout=120)
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["outcome"], "completed")
            self.assertTrue(envelope["ok"])
            self.assertTrue(envelope["resultDelivered"])
            self.assertTrue(envelope["shutdownConfirmed"])
            self.assertEqual(envelope["status"], "completed")
            self.assertEqual(envelope["runId"], task["runId"])
            self.assertIn("argv", envelope["result"])
            self.assertEqual(marker.read_text().strip(), "done")

    def test_execution_submit_is_idempotent_for_the_same_request_id(self):
        with self.daemon():
            payload = {
                "requestId": "exec-recover-1",
                "task": "one task only",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/true"],
                "timeoutSeconds": 120,
            }
            first = self.command_task("exec-recover-1", ["/bin/true"])
            second = self.command_task("exec-recover-1", ["/bin/true"])
            self.assertEqual(second["runId"], first["runId"])
            self.assertEqual(self.cli("list")[1]["total"], 1)

            code, conflict = self.cli("execution-submit", json.dumps({**payload, "task": "changed"}))
            self.assertEqual(code, 1)
            self.assertEqual(conflict["error"]["code"], "CONFLICT")
            self.assertEqual(self.cli("list")[1]["total"], 1)

    def test_await_never_launches_work_and_honours_its_wait_window(self):
        with self.daemon():
            code, missing = self.cli("await", json.dumps({"requestId": "never-admitted", "waitSeconds": 1}))
            self.assertEqual(code, 1)
            self.assertEqual(missing["error"]["code"], "NOT_FOUND")
            message = missing["error"]["message"]
            self.assertIn("execution-submit", message)
            self.assertIn("buddy submit", message)
            self.assertNotIn("buddy run", message)

            task = self.command_task("exec-await-1", ["/bin/sleep", "4"])
            code, envelope = self.cli("await", json.dumps({"runId": task["runId"], "waitSeconds": 2}), timeout=120)
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["outcome"], "wait-timeout")
            self.assertFalse(envelope["ok"])
            self.assertTrue(envelope["timedOut"])
            self.assertFalse(envelope["resultDelivered"])
            self.assertTrue(envelope["recovery"]["commands"], "a wait timeout must name real recovery commands")
            commands = " ".join(envelope["recovery"]["commands"])
            self.assertIn("buddy status", commands)
            self.assertIn("buddy await", commands)
            self.assertNotIn("buddy start", commands)
            self.assertNotIn("buddy run", commands)

            # The timeout stopped only the wait: the SAME run is still there and
            # awaiting it again delivers its result.
            run_id = envelope["runId"]
            code, finished = self.cli("await", json.dumps({"runId": run_id, "waitSeconds": 60}), timeout=120)
            self.assertEqual(code, 0, finished)
            self.assertEqual(finished["outcome"], "completed")
            self.assertEqual(finished["runId"], run_id)
            status = self.cli("status", json.dumps({"runId": run_id}))[1]
            self.assertEqual(status["createdAt"], task["createdAt"])

    def test_await_resolves_an_existing_run_by_request_id_without_starting_it(self):
        with self.daemon():
            task = self.command_task("exec-by-request", ["/bin/echo", "hello"])
            code, envelope = self.cli(
                "await", json.dumps({"requestId": "exec-by-request", "waitSeconds": 60}), timeout=120
            )
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["outcome"], "completed")
            self.assertEqual(envelope["runId"], task["runId"])
            self.assertEqual(self.cli("list")[1]["total"], 1)

    def test_status_result_and_list_keep_the_stable_envelope(self):
        with self.daemon():
            task = self.command_task("exec-envelope-1", ["/bin/echo", "hello"])
            status = self.cli("status", json.dumps({"runId": task["runId"]}))[1]
            self.assertEqual(status["requestId"], "exec-envelope-1")
            self.assertEqual(status["requestId"], "exec-envelope-1")
            self.assertEqual(status["runId"], task["runId"])
            self.assertIn(status["status"], ("queued", "running", "completed"))
            self.assertTrue(wait_for(lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] == "completed", 30))
            result = self.cli("result", json.dumps({"runId": task["runId"]}))[1]
            self.assertTrue(result["resultAvailable"])
            self.assertEqual(result["resultMeta"]["status"], "ok")
            page = self.cli("list", json.dumps({"limit": 10}))[1]
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["runs"][0]["runId"], task["runId"])

    def test_governed_get_and_execution_status_stay_distinct(self):
        with self.daemon():
            task = self.command_task("exec-distinct-1", ["/bin/true"])
            # The governed view answers for an ordinary execution record too, but it
            # says so instead of inventing goal state.
            view = self.cli("get", json.dumps({"runId": task["runId"]}))[1]
            self.assertFalse(view["governed"])
            self.assertEqual(view["runId"], task["runId"])
            self.assertIn("task", view)
            status = self.cli("status", json.dumps({"runId": task["runId"]}))[1]
            self.assertEqual(status["runId"], task["runId"])
            self.assertNotIn("governed", status)

    def test_execution_cancel_then_acknowledge_records_a_reviewed_outcome(self):
        with self.daemon():
            task = self.command_task("exec-cancel-1", ["/bin/sleep", "30"])
            self.assertTrue(wait_for(lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] == "running", 30))
            code, cancelled = self.cli(
                "execution-cancel", json.dumps({"runId": task["runId"], "reason": "operator stopped it"})
            )
            self.assertEqual(code, 0, cancelled)
            done = wait_for(
                lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] in ("cancelled", "failed", "reconciliation-needed"),
                60,
            )
            self.assertTrue(done, "the worker must observe the cancel intent and stop its own child")
            final = self.cli("status", json.dumps({"runId": task["runId"]}))[1]
            self.assertIn(final["status"], ("cancelled", "reconciliation-needed"))
            if final["status"] == "cancelled":
                code, acknowledged = self.cli(
                    "execution-acknowledge",
                    json.dumps({"runId": task["runId"], "note": "reviewed the cancelled run", "verdict": "rejected"}),
                )
                self.assertEqual(code, 0, acknowledged)
                self.assertEqual(acknowledged["acceptanceVerdict"], "rejected")
                self.assertIsNotNone(acknowledged["acceptedAt"])


if __name__ == "__main__":
    unittest.main()
