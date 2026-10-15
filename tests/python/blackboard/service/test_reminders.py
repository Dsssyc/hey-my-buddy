"""Reminder delivery through the private board and the real no-model command Worker."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.tasks import reminders
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.protocol.client import BoardClient


class ReminderBoardTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board_instance = self.board(clock=self.clock)
        self.client = self.board_instance.client()

    def claim_command(self, *, expected_seconds=2):
        self.client.register_worker("command-worker", adapter="command", capabilities=["command"])
        submitted = self.client.submit(
            requestId="reminder-command", task="Run a private command", cwd=str(self.workdir()),
            adapter="command", argv=["/bin/sleep", "1"], expectedSeconds=expected_seconds,
        )
        run_id = submitted["task"]["runId"]
        claim = self.client.claim("command-worker", "reminder-claim", "a" * 64)
        return run_id, claim["claim"]["attempt"]

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
        run_id, attempt = self.claim_command(expected_seconds=100)
        with self.board_instance.store.db.write() as connection:
            connection.execute("UPDATE reminder_settings SET silence_seconds=2 WHERE id=1")
        self.clock.advance(1)
        first_native_at = self.clock()
        self.client.progress(
            "command-worker", attempt["attemptId"], attempt["generation"], "a" * 64,
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
            "command-worker", attempt["attemptId"], attempt["generation"], "a" * 64,
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
        # A command Worker is a real process, and this daemon/CLI path has no
        # native harness or model credentials. The duration is short enough to
        # prove await wakes before the command's own completion.
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            code, submitted = self.cli("execution-submit", json.dumps({
                "requestId": "private-reminder-command", "task": "Sleep in a private Worker",
                "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/sleep", "4"],
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

    def test_private_service_silence_is_once_per_native_activity_period(self):
        self.private_silence_threshold(1)
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            code, submitted = self.cli("execution-submit", json.dumps({
                "requestId": "private-silent-command", "task": "Sleep in a private Worker",
                "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/sleep", "4"],
                "expectedSeconds": 100,
            }))
            self.assertEqual(code, 0, submitted)
            selector = json.dumps({"runId": submitted["runId"], "waitSeconds": 5})
            code, first = self.cli("await", selector, timeout=15)
            self.assertEqual(code, 0, first)
            self.assertEqual(first["outcome"], "native-silence")
            self.assertIsNone(first["reminder"]["lastNativeEventAt"])
            self.assertFalse(first["reminder"]["toolInProgress"])
            code, next_wait = self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 1}), timeout=15)
            self.assertEqual(code, 0, next_wait)
            self.assertEqual(next_wait["outcome"], "wait-timeout")
            self.assertNotIn("reminder", next_wait)

    def test_private_service_silence_reports_a_native_tool_in_progress(self):
        self.private_silence_threshold(1)
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory)
            client.register_worker("manual-native-worker", adapter="command",
                                   capabilities=["command", "manual-native"])
            code, submitted = self.cli("execution-submit", json.dumps({
                "requestId": "private-native-command", "task": "Manual Worker activity",
                "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/true"],
                "requiredCapabilities": ["manual-native"], "expectedSeconds": 100,
            }))
            self.assertEqual(code, 0, submitted)
            claim = client.claim("manual-native-worker", "manual-native-claim", "b" * 64,
                                 task_id=submitted["runId"])["claim"]["attempt"]
            native_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            client.progress(
                "manual-native-worker", claim["attemptId"], claim["generation"], "b" * 64,
                phase="tool-running", data={"activity": {
                    "phase": "tool-running", "observedAt": native_at,
                    "lastNativeActivityAt": native_at, "lastToolActivityAt": native_at,
                    "eventSeq": 1, "toolName": "private-tool",
                }},
            )
            code, result = self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 5}), timeout=15)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["outcome"], "native-silence")
            self.assertEqual(result["reminder"]["lastNativeEventAt"], native_at)
            self.assertTrue(result["reminder"]["toolInProgress"])


