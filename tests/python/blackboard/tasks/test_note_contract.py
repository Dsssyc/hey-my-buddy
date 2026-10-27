"""Host operation notes are validated by their public board entry points."""
from __future__ import annotations

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas
from support import BoardTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase


class HostNoteContractTests(BoardTestCase):
    def test_host_entry_points_accept_note_and_reject_retired_reason(self):
        board = self.board()
        requests = {
            "workflow_decide": {"runId": "missing", "requestId": "request", "commandId": "decide",
                                "expectedRevision": 1, "decision": "approve"},
            "workflow_continue": {"runId": "missing", "commandId": "continue", "expectedRevision": 1,
                                  "input": "Continue"},
            "workflow_cancel": {"runId": "missing", "commandId": "cancel"},
            "macro_task_stop": {"macroTaskId": "macro-missing", "commandId": "stop"},
            "workflow_scope_amend": {"runId": "missing", "commandId": "amend", "expectedRevision": 1,
                                     "expectedScopeVersion": 1, "writeScope": ["src"]},
            "workflow_workspace_resolve": {"runId": "missing", "commandId": "resolve",
                                           "expectedRevision": 1, "conflictId": "missing", "action": "adopt",
                                           "observedFingerprint": "a" * 64},
            "task_cancel": {"runId": "missing"},
            "task_retry": {"runId": "missing"},
        }
        for operation, params in requests.items():
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as retired:
                    board.call(operation, {**params, "reason": "Host explanation"})
                self.assertEqual(retired.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(retired.exception.details.get("field"), "reason")

                with self.assertRaises(BoardError) as accepted:
                    board.call(operation, {**params, "note": "Host explanation"})
                self.assertNotEqual(accepted.exception.details.get("field"), "note")

    def test_note_limit_is_shared_across_host_operations(self):
        self.assertEqual(schemas.MAX_NOTE_BYTES, 10000)
        board = self.board()
        with self.assertRaises(BoardError) as retired:
            board.call("service_control", {"action": "restart", "reason": "Host explanation"})
        self.assertEqual(retired.exception.details.get("field"), "reason")
        for operation, params in (
            ("service_control", {"action": "restart", "drainSeconds": 0}),
            ("task_cancel", {"runId": "missing"}),
            ("task_retry", {"runId": "missing"}),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as oversized:
                    board.call(operation, {**params, "note": "x" * (schemas.MAX_NOTE_BYTES + 1)})
                self.assertEqual(oversized.exception.code, "INVALID_ARGUMENT")
                self.assertIn("10000", oversized.exception.message)

        board.call("service_control", {"action": "restart", "drainSeconds": 0,
                                       "note": "x" * schemas.MAX_NOTE_BYTES})
        self.assertEqual(board.restarted[-1]["note"], "x" * schemas.MAX_NOTE_BYTES)

    def test_access_note_has_its_own_name_and_rejects_access_reason(self):
        board = self.board()
        params = {"runId": "missing", "commandId": "continue", "expectedRevision": 1,
                  "input": "Continue", "access": "full"}
        with self.assertRaises(BoardError) as retired:
            board.call("workflow_continue", {**params, "accessReason": "Required by task"})
        self.assertEqual(retired.exception.details.get("field"), "accessReason")
        with self.assertRaises(BoardError) as accepted:
            board.call("workflow_continue", {**params, "accessNote": "Required by task"})
        self.assertNotEqual(accepted.exception.details.get("field"), "accessNote")

    def test_client_retry_sends_note(self):
        requests = []

        def call(operation, params):
            requests.append((operation, params))
            return {"task": {"runId": "run-1"}}

        from hey_my_buddy.protocol.client import BoardClient
        client = BoardClient(call=call)
        client.retry(runId="run-1", note="Try again")
        self.assertEqual(requests[0], ("task_retry", {"runId": "run-1", "note": "Try again"}))


class CancellationNoteTests(WorkflowTestCase):
    def test_cancellation_preserves_verbatim_utf8_note_in_fresh_and_replayed_receipts(self):
        board = self.board()
        run = self.submit(board)
        params = {"runId": run["runId"], "commandId": "cancel-with-note",
                  "note": "  因用户要求停止  ", **self.control(run)}
        fresh = board.call("workflow_cancel", params)
        self.assertNotIn("note", fresh)
        replay = board.call("workflow_cancel", params)
        self.assertNotIn("note", replay)
        self.assertTrue(replay["duplicate"])
        events = [event for event in board.call("events_read", {"after": 0})["events"]
                  if event["kind"] == "workflow.cancelled"]
        self.assertEqual(events[-1]["payload"]["note"], params["note"])

    def test_cancellation_note_limit_counts_utf8_bytes(self):
        board = self.board()
        run = self.submit(board)
        params = {"runId": run["runId"], "commandId": "cancel-oversized",
                  "note": "字" * 3334, **self.control(run)}
        with self.assertRaises(BoardError) as oversized:
            board.call("workflow_cancel", params)
        self.assertEqual(oversized.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["state"], run["state"])
