"""Creation receipts are the only authority for selecting user-store sessions."""
from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace
import unittest

from hey_my_buddy.blackboard.tasks.worker_sessions import deletable_sessions, handle


class WorkerDeletionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("CREATE TABLE attempts (attempt_id TEXT, task_id TEXT, adapter TEXT, "
                                "execution_state TEXT, shutdown_confirmed INTEGER, result_json TEXT)")
        self.store = SimpleNamespace(db=self)

    @contextmanager
    def read(self):
        yield self.connection

    def add(self, task="selected", attempt="a1", session="owned", *, owner="harness-user-store",
            mode="initial", state="finished", stopped=1, checkpoint=True, completed=False,
            captured=True, adapter="codex", native_harness="codex", binding_task=None, binding_attempt=None):
        result = {"nativeSession": {"harness": native_harness, "sessionId": session, "captured": captured,
                                   "storageOwner": owner, "resumeMode": mode}}
        if checkpoint:
            result["nativeCheckpoint"] = {"version": 1, "sessionId": session,
                                          "taskId": binding_task or task, "attemptId": binding_attempt or attempt}
        if completed:
            result["turn"] = {"taskId": binding_task or task, "attemptId": binding_attempt or attempt,
                              "sessionId": session, "resumeMode": mode, "provenance": {
                                  "harness": "codex", "nativeThreadId": session, "nativeTurnStarted": True,
                                  "nativeTurnCompleted": True, "turnEnd": "completed"}}
        self.connection.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?)",
                                (attempt, task, adapter, state, stopped, json.dumps({"result": result})))

    def test_filters_task_set_and_storage_owner_without_changing_listing(self):
        self.add()
        self.add(task="second", attempt="a2", session="second-owned")
        self.add(task="unselected", attempt="a3", session="unselected-owned")
        self.add(attempt="a4", session="private", owner="hey-my-buddy-micro-task")
        selected = deletable_sessions(self.store, {"selected", "second"})
        self.assertEqual([row["sessionId"] for row in selected], ["owned", "second-owned"])
        self.assertEqual({row["taskId"] for row in selected}, {"selected", "second"})
        self.assertTrue(all(row["storageOwner"] == "harness-user-store" for row in selected))
        self.assertEqual(handle(self.store, {})["count"], 4)
        self.assertEqual(set(handle(self.store, {})), {"harness", "sessions", "count"})
        self.assertEqual(deletable_sessions(self.store, set()), [])

    def test_resumed_sessions_require_creation_receipt_and_merge_attempts(self):
        self.add(attempt="a2", mode="native-session")
        self.add(attempt="a3", session="foreign-resume", mode="native-session")
        self.add(task="other", attempt="a4", session="foreign-resume")
        self.assertEqual(deletable_sessions(self.store, {"selected"}), [])
        self.add(attempt="a1")
        self.add(attempt="a5", session="recreated", mode="reconstructed-new-session")
        selected = deletable_sessions(self.store, {"selected"})
        self.assertEqual([row["sessionId"] for row in selected], ["owned", "recreated"])
        self.assertEqual(selected[0]["attemptIds"], ["a1", "a2"])

    def test_unproven_or_unstopped_receipts_are_excluded(self):
        cases = [{"state": "executing"}, {"stopped": 0}, {"checkpoint": False},
                 {"captured": False}, {"binding_task": "foreign"}, {"binding_attempt": "foreign"},
                 {"mode": "unknown"}, {"owner": "unknown"}, {"adapter": "dsh"},
                 {"native_harness": "dsh"}, {"session": ""}]
        for index, changes in enumerate(cases):
            self.add(attempt=f"a{index}", **changes)
        self.assertEqual(deletable_sessions(self.store, {"selected"}), [])

    def test_completed_turn_binding_is_shared_with_history_listing(self):
        self.add(checkpoint=False, completed=True)
        self.add(attempt="bad", session="unbound", checkpoint=False, completed=True, binding_task="foreign")
        self.assertEqual(deletable_sessions(self.store, {"selected"}), handle(self.store, {})["sessions"])
        self.assertEqual([row["sessionId"] for row in deletable_sessions(self.store, {"selected"})], ["owned"])
