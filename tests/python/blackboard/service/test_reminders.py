"""Reminder delivery through direct execution admission and a private service."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.tasks import reminders
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.blackboard.tasks import workflow
from mock_workspace import MockWorkspace
from unittest.mock import patch


DSH_FIXTURE = Path(__file__).resolve().parents[2] / "buddy/harnesses/dsh/fixtures/workflow_agent.py"
DSH_BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


class ReminderBoardTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board_instance = self.board(clock=self.clock)
        self.client = self.board_instance.client()

    def claim_command(self, *, expected_seconds=2):
        submitted = self.client.submit(
            requestId="reminder-command", task="Run a private command", cwd=str(self.workdir()),
            adapter="command", argv=["/bin/sleep", "1"], expectedSeconds=expected_seconds,
        )
        run_id = submitted["task"]["runId"]
        claim = self.board_instance.store.admit_execution("a" * 64)
        return run_id, claim["claim"]["attempt"]

    def claim_native(self, *, expected_seconds=100):
        self.catalog_fixture()
        with patch.object(workflow, "_workspace_module", MockWorkspace(self.directory)):
            submitted = self.client.workflow_submit(
                requestId="reminder-native", hostId="reminder-host", task="Observe native activity",
                cwd=str(self.workdir()), executionWorkspace={"kind": "existing", "access": "read"},
                expectedSeconds=expected_seconds, **DSH_BUDDY,
            )
        claim = self.board_instance.store.admit_execution("a" * 64, task_id=submitted["runId"])
        return submitted["runId"], claim["claim"]["attempt"]

    def wait(self, run_id):
        return self.board_instance.call("task_wait", {
            "runId": run_id, "timeoutMs": 0, "consumeReminders": True,
            "reminderWaitId": uuid.uuid4().hex,
        })

    def test_idle_scheduler_has_no_deadline_or_side_effect(self):
        before = self.board_instance.store.head()
        self.assertIsNone(reminders.collect(self.board_instance.store))
        self.assertEqual(self.board_instance.store.head(), before)

    def test_expected_multiples_are_durable_and_consumed_once(self):
        run_id, _attempt = self.claim_command(expected_seconds=2)
        self.clock.advance(5)
        reminders.collect(self.board_instance.store)
        # A status read never consumes a reminder, and collecting the same instant
        # again does not manufacture another copy of either elapsed multiple.
        self.assertEqual(self.client.get(runId=run_id)["status"], "running")
        reminders.collect(self.board_instance.store)
        first = self.wait(run_id)["reminder"]
        second = self.wait(run_id)["reminder"]
        self.assertEqual([first["kind"], second["kind"]], ["expected-duration"] * 2)
        self.assertEqual([first["expectedMultiple"], second["expectedMultiple"]], [1, 2])
        self.assertEqual([first["elapsedSeconds"], second["elapsedSeconds"]], [5.0, 5.0])
        self.assertNotEqual(first["reminderId"], second["reminderId"])
        self.assertNotIn("reminder", self.wait(run_id))
        self.clock.advance(1)
        reminders.collect(self.board_instance.store)
        self.assertEqual(self.wait(run_id)["reminder"]["expectedMultiple"], 3)

    def test_native_silence_records_tool_evidence_then_resets_after_native_event(self):
        run_id, attempt = self.claim_native()
        with self.board_instance.store.db.write() as connection:
            connection.execute("UPDATE reminder_settings SET silence_seconds=2 WHERE id=1")
        self.clock.advance(1)
        first_native_at = self.clock()
        self.client.progress(
            attempt["attemptId"], attempt["generation"], "a" * 64,
            phase="tool-running",
            data={"activity": {"phase": "tool-running", "observedAt": first_native_at,
                               "lastNativeActivityAt": first_native_at, "lastToolActivityAt": first_native_at,
                               "eventSeq": 1, "toolName": "private-tool"}},
        )
        self.clock.advance(2)
        reminders.collect(self.board_instance.store)
        reminder = self.wait(run_id)["reminder"]
        self.assertEqual(reminder["kind"], "native-silence")
        self.assertEqual(reminder["lastNativeEventAt"], first_native_at)
        self.assertTrue(reminder["toolInProgress"])
        self.assertNotIn("reminder", self.wait(run_id))
        self.clock.advance(1)
        second_native_at = self.clock()
        self.client.progress(
            attempt["attemptId"], attempt["generation"], "a" * 64,
            phase="waiting-model",
            data={"activity": {"phase": "waiting-model", "observedAt": second_native_at,
                               "lastNativeActivityAt": second_native_at, "eventSeq": 2}},
        )
        self.clock.advance(2)
        reminders.collect(self.board_instance.store)
        next_reminder = self.wait(run_id)["reminder"]
        self.assertEqual(next_reminder["lastNativeEventAt"], second_native_at)
        self.assertFalse(next_reminder["toolInProgress"])
        self.assertNotEqual(next_reminder["reminderId"], reminder["reminderId"])


class ReminderPrivateServiceTests(BoardTestCase):
    def private_silence_threshold(self, seconds):
        store = BoardStore(self.directory)
        store.initialize()
        with store.db.write() as connection:
            connection.execute("UPDATE reminder_settings SET silence_seconds=? WHERE id=1", (seconds,))
        store.db.close()

    def test_command_worker_reminder_returns_from_cli_await_without_a_model(self):
        # A command execution is a real process, and this daemon/CLI path has no
        # native harness or model credentials. The duration is short enough to
        # prove await wakes before the command's own completion.
        self.private_silence_threshold(1)
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            code, submitted = self.cli("execution-submit", json.dumps({
                "requestId": "private-reminder-command", "task": "Sleep in a private execution",
                "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/sleep", "12"],
                "expectedSeconds": 1,
            }))
            self.assertEqual(code, 0, submitted)
            run_id = submitted["runId"]
            code, first = self.cli("await", json.dumps({"runId": run_id, "waitSeconds": 6}), timeout=20)
            self.assertEqual(code, 0, first)
            self.assertEqual(first["outcome"], "expected-duration")
            self.assertEqual(first["reminder"]["expectedMultiple"], 1)
            self.assertFalse(first["ok"])
            self.assertFalse(first["shutdownConfirmed"])
            code, second = self.cli("await", json.dumps({"runId": run_id, "waitSeconds": 6}), timeout=20)
            self.assertEqual(code, 0, second)
            self.assertEqual(second["outcome"], "expected-duration")
            self.assertEqual(second["reminder"]["expectedMultiple"], 2)
            self.assertNotEqual(first["reminder"]["reminderId"], second["reminder"]["reminderId"])
            code, status = self.cli("status", json.dumps({"runId": run_id}))
            self.assertEqual(code, 0, status)
            self.assertEqual(status["status"], "running")
            attempt = BoardClient(self.directory).get(runId=run_id)["selectedAttempt"]
            self.assertFalse(attempt["reminders"]["possiblyStuck"])
            store = BoardStore(self.directory)
            with store.db.read() as connection:
                kinds = [row[0] for row in connection.execute(
                    "SELECT kind FROM attempt_reminders WHERE attempt_id=?", (attempt["attemptId"],))]
            store.db.close()
            self.assertGreaterEqual(len(kinds), 2)
            self.assertEqual(set(kinds), {"expected-duration"})

