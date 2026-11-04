"""Host notes are validated at the current public boundaries."""
from __future__ import annotations

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas
from support import BoardTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase


class HostNoteContractTests(BoardTestCase):
    def test_current_host_entry_points_accept_note_and_reject_reason(self):
        board = self.board()
        requests = {
            "workflow_continue": {"runId": "missing", "requestId": "request", "decision": "approve"},
            "workflow_cancel": {"runId": "missing"},
            "workflow_accept": {"runId": "missing", "artifactId": "missing-artifact",
                                "notRequired": "no integration target"},
            "workflow_conclude": {"runId": "missing"},
            "macro_task_stop": {"macroTaskId": "macro-missing", "commandId": "stop"},
            "task_cancel": {"runId": "missing"},
        }
        for operation, params in requests.items():
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as retired:
                    board.call(operation, {**params, "reason": "Host explanation", "note": "Host explanation"})
                self.assertEqual(retired.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(retired.exception.details.get("field"), "reason")
                with self.assertRaises(BoardError) as accepted:
                    board.call(operation, {**params, "note": "Host explanation"})
                self.assertNotEqual(accepted.exception.details.get("field"), "note")

    def test_note_limit_counts_utf8_bytes_at_host_boundary(self):
        self.assertEqual(schemas.MAX_NOTE_BYTES, 10000)
        board = self.board()
        for operation, params in (
            ("workflow_cancel", {"runId": "missing"}),
            ("workflow_conclude", {"runId": "missing"}),
            ("workflow_accept", {"runId": "missing", "artifactId": "missing-artifact",
                                 "notRequired": "no integration target"}),
            ("service_control", {"action": "restart", "drainSeconds": 0}),
        ):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as oversized:
                board.call(operation, {**params, "note": "字" * 3334})
            self.assertEqual(oversized.exception.code, "INVALID_ARGUMENT")
            self.assertIn("10000", oversized.exception.message)
        board.call("service_control", {"action": "restart", "drainSeconds": 0,
                                       "note": "x" * schemas.MAX_NOTE_BYTES})
        self.assertEqual(board.restarted[-1]["note"], "x" * schemas.MAX_NOTE_BYTES)

    def test_access_note_has_its_own_name(self):
        board = self.board()
        params = {"runId": "missing", "artifactId": "missing-artifact", "input": "Continue",
                  "access": "full"}
        with self.assertRaises(BoardError) as retired:
            board.call("workflow_continue", {**params, "accessReason": "Required by task"})
        self.assertEqual(retired.exception.details.get("field"), "accessReason")
        with self.assertRaises(BoardError) as accepted:
            board.call("workflow_continue", {**params, "accessNote": "Required by task"})
        self.assertNotEqual(accepted.exception.details.get("field"), "accessNote")

    def test_client_cancel_sends_note(self):
        requests = []

        def call(operation, params):
            requests.append((operation, params))
            return {"task": {"runId": "run-1"}}

        from hey_my_buddy.protocol.client import BoardClient
        client = BoardClient(call=call)
        client.cancel(runId="run-1", note="Stop this command")
        self.assertEqual(requests[0], ("task_cancel", {"runId": "run-1", "note": "Stop this command"}))


class CancellationNoteTests(WorkflowTestCase):
    def test_cancellation_preserves_verbatim_utf8_note_on_replay(self):
        board = self.board()
        run = self.submit(board)
        params = {"runId": run["runId"], "note": "  因用户要求停止  "}
        fresh = board.call("workflow_cancel", params)
        self.assertNotIn("note", fresh)
        replay = board.call("workflow_cancel", params)
        self.assertNotIn("note", replay)
        self.assertTrue(replay["alreadyRequested"])
        self.assertEqual(replay["stopEventId"], fresh["stopEventId"])
        events = [event for event in board.call("events_read", {"after": 0})["events"]
                  if event["kind"] == "workflow.cancelled"]
        self.assertEqual(events[-1]["payload"]["note"], params["note"])

    def test_cancellation_note_limit_counts_utf8_bytes(self):
        board = self.board()
        run = self.submit(board)
        with self.assertRaises(BoardError) as oversized:
            board.call("workflow_cancel", {"runId": run["runId"], "note": "字" * 3334})
        self.assertEqual(oversized.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["state"], run["state"])
