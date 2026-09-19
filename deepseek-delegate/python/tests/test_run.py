"""Real cross-process CLI/C-Two integration for blocking `buddy run` and `buddy await`.

Everything runs against an isolated ``BUDDY_STATE_DIR`` with the real daemon, the
real local worker supervisor and the real ``command`` adapter; no model is called.
The CLI is invoked exactly as a user invokes it from an unrelated working directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from support import BoardTestCase, wait_for

PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestBlockingRun(BoardTestCase):
    def test_run_completes_one_real_task_and_delivers_its_result(self):
        with self.daemon():
            marker = self.directory / "run-marker.txt"
            code, envelope = self.cli(
                "run",
                json.dumps(
                    {
                        "requestId": "run-1",
                        "task": "write a marker",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/sh", "-c", f"echo done > {marker}; echo finished"],
                        "timeoutSeconds": 120,
                        "waitSeconds": 60,
                    }
                ),
                timeout=120,
            )
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["outcome"], "completed")
            self.assertTrue(envelope["ok"])
            self.assertTrue(envelope["resultDelivered"])
            self.assertTrue(envelope["shutdownConfirmed"])
            self.assertEqual(envelope["status"], "completed")
            self.assertEqual(marker.read_text().strip(), "done")
            self.assertIn("argv", envelope["result"])

    def test_run_recovers_the_same_task_for_the_same_request_id(self):
        with self.daemon():
            payload = json.dumps(
                {
                    "requestId": "recover-1",
                    "task": "one task only",
                    "cwd": str(self.workdir()),
                    "adapter": "command",
                    "argv": ["/bin/true"],
                    "timeoutSeconds": 120,
                    "waitSeconds": 60,
                }
            )
            self.cli("start", json.dumps(json.loads(payload) | {"__drop": True}))
            code, started = self.cli(
                "start",
                json.dumps(
                    {
                        "requestId": "recover-1",
                        "task": "one task only",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/true"],
                        "timeoutSeconds": 120,
                    }
                ),
            )
            self.assertEqual(code, 0, started)
            second = self.cli(
                "start",
                json.dumps(
                    {
                        "requestId": "recover-1",
                        "task": "one task only",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/true"],
                        "timeoutSeconds": 120,
                    }
                ),
            )
            self.assertEqual(second[0], 0, second)
            self.assertEqual(second[1]["runId"], started["runId"])
            self.assertEqual(self.cli("list")[1]["total"], 1)

    def test_a_changed_input_for_the_same_request_id_is_a_conflict(self):
        with self.daemon():
            base = {
                "requestId": "conflict-1",
                "task": "original",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/true"],
            }
            self.assertEqual(self.cli("start", json.dumps(base))[0], 0)
            code, envelope = self.cli("start", json.dumps({**base, "task": "changed"}))
            self.assertEqual(code, 1)
            self.assertEqual(envelope["error"]["code"], "CONFLICT")

    def test_await_never_launches_work_and_honours_its_wait_window(self):
        with self.daemon():
            code, missing = self.cli("await", json.dumps({"requestId": "never-started", "waitSeconds": 1}))
            self.assertEqual(code, 1)
            self.assertEqual(missing["error"]["code"], "NOT_FOUND")
            code, envelope = self.cli(
                "run",
                json.dumps(
                    {
                        "requestId": "await-1",
                        "task": "sleep briefly",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/sleep", "4"],
                        "timeoutSeconds": 120,
                        "waitSeconds": 2,
                    }
                ),
                timeout=120,
            )
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["outcome"], "wait-timeout")
            self.assertFalse(envelope["ok"])
            self.assertTrue(envelope["timedOut"])
            self.assertFalse(envelope["resultDelivered"])
            self.assertTrue(envelope["recovery"]["commands"], "a wait timeout must name real recovery commands")
            self.assertIn("await", " ".join(envelope["recovery"]["commands"]))
            # Awaiting the same run delivers the result of the SAME task.
            run_id = envelope["runId"]
            code, finished = self.cli("await", json.dumps({"runId": run_id, "waitSeconds": 60}), timeout=120)
            self.assertEqual(code, 0, finished)
            self.assertEqual(finished["outcome"], "completed")
            self.assertEqual(finished["runId"], run_id)
            status = self.cli("status", json.dumps({"runId": run_id}))[1]
            self.assertEqual(status["createdAt"], envelope["createdAt"])

    def test_status_result_and_list_keep_the_stable_envelope(self):
        with self.daemon():
            code, task = self.cli(
                "start",
                json.dumps(
                    {
                        "requestId": "envelope-1",
                        "task": "hello",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/echo", "hello"],
                    }
                ),
            )
            self.assertEqual(code, 0, task)
            self.assertEqual(task["requestId"], "envelope-1")
            self.assertEqual(task["status"], "queued")
            self.assertTrue(task["queueReason"], "a queued task must say why it is waiting")
            self.assertTrue(task["runId"] == task["taskId"])
            self.assertTrue(wait_for(lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] == "completed", 30))
            result = self.cli("result", json.dumps({"runId": task["runId"]}))[1]
            self.assertTrue(result["resultAvailable"])
            self.assertEqual(result["resultMeta"]["status"], "ok")
            page = self.cli("list", json.dumps({"limit": 10}))[1]
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["runs"][0]["runId"], task["runId"])

    def test_cancel_then_acknowledge_records_a_reviewed_outcome(self):
        with self.daemon():
            code, task = self.cli(
                "start",
                json.dumps(
                    {
                        "requestId": "cancel-1",
                        "task": "sleep",
                        "cwd": str(self.workdir()),
                        "adapter": "command",
                        "argv": ["/bin/sleep", "30"],
                    }
                ),
            )
            self.assertEqual(code, 0, task)
            self.assertTrue(wait_for(lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] == "running", 30))
            self.cli("cancel", json.dumps({"runId": task["runId"]}))
            done = wait_for(
                lambda: self.cli("status", json.dumps({"runId": task["runId"]}))[1]["status"] in ("cancelled", "failed", "reconciliation-needed"),
                60,
            )
            self.assertTrue(done, "the worker must observe the cancel intent and stop its own child")
            final = self.cli("status", json.dumps({"runId": task["runId"]}))[1]
            self.assertIn(final["status"], ("cancelled", "reconciliation-needed"))
            if final["status"] == "cancelled":
                code, acknowledged = self.cli(
                    "acknowledge",
                    json.dumps({"runId": task["runId"], "note": "reviewed the cancelled run", "verdict": "rejected"}),
                )
                self.assertEqual(code, 0, acknowledged)
                self.assertEqual(acknowledged["acceptanceVerdict"], "rejected")
                self.assertIsNotNone(acknowledged["acceptedAt"])


if __name__ == "__main__":
    unittest.main()
