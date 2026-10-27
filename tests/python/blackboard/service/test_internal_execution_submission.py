"""Private fixtures seed command work without a Host submission operation."""
from __future__ import annotations

import sys
import time
from unittest.mock import patch

from support import BoardTestCase, _lock_held, wait_for
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.protocol.client import BoardClient


class InternalExecutionSubmissionTests(BoardTestCase):
    def test_private_store_write_is_picked_up_while_daemon_owns_board(self):
        with self.daemon():
            self.assertTrue(_lock_held(self.directory / "board-owner.lock"))
            self.assertTrue(_lock_held(self.directory / "control-daemon.lock"))
            params = {
                "requestId": "internal-command", "task": "Private execution fixture",
                "cwd": str(self.workdir()), "adapter": "command",
                "argv": [sys.executable, "-c", "print('internal execution')"],
            }
            started = time.monotonic()
            with patch.object(BoardStore, "initialize", side_effect=AssertionError("second startup")):
                submitted = self.submit_execution(**params)
                replay = self.submit_execution(**params)
            task = submitted["task"]
            self.assertEqual(task["state"], "queued")
            self.assertTrue(replay["duplicate"])
            self.assertEqual(replay["task"]["runId"], task["runId"])
            client = BoardClient(self.directory, autostart=False)
            self.assertEqual(client.get(runId=task["runId"])["adapter"], "command")
            finished = wait_for(lambda: self._finished(client, task["runId"]), timeout=20)
            self.assertIsNotNone(finished, client.get(runId=task["runId"]))
            self.assertTrue(finished["shutdownConfirmed"])
            self.assertEqual(finished["state"], "completed")
            self.assertLess(time.monotonic() - started, 20)
            self.assertEqual(client.result(runId=task["runId"])["result"]["finalText"].strip(),
                             "internal execution")
            self.assertEqual(finished["lineage"]["kind"], "execution")
            listed = client.list_tasks()
            self.assertIn(task["runId"], [row["runId"] for row in listed["runs"]])

    @staticmethod
    def _finished(client, run_id):
        view = client.get(runId=run_id)
        return view if view.get("attemptState") == "finished" else None
