"""Durable stop intent and observed termination at the blackboard boundary."""
from __future__ import annotations

from datetime import datetime, timedelta

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.store.db import TERMINATION_REASONS
from hey_my_buddy.errors import BoardError


class GracefulStopTests(WorkflowTestCase):
    def attempt(self, board, claim):
        return board.call("task_get", {"runId": claim["claim"]["attempt"]["taskId"]})["task"]["selectedAttempt"]

    def result(self, board, claim, *, reason, initiator=None, status="cancelled", shutdown=True):
        return board.call("worker_result", self.result_params(claim, reason=reason, initiator=initiator,
                                                              status=status, shutdown=shutdown))

    def result_params(self, claim, *, reason, initiator=None, status="cancelled", shutdown=True):
        attempt = claim["claim"]["attempt"]
        record = self.turn_record(claim)
        payload = {
            "workerId": "w1", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "n" * 16, "status": status, "shutdownConfirmed": shutdown,
            "terminationReason": reason, "stopInitiator": initiator,
            "result": {
                "status": status, "mode": "run", "processState": {"shutdownConfirmed": shutdown},
                "logPaths": {"stdout": "/tmp/out", "stderr": "/tmp/err"},
                "turn": record, "turnResultPath": "/tmp/turn-output.json",
                "workspaceSeal": self.workspace.seal(
                    self.directory, claim["claim"]["turn"]["input"]["executionWorkspace"],
                    attempt["taskId"], attempt["attemptId"],
                ),
            },
        }
        return payload

    def test_console_escalation_during_host_receipt_preserves_shutdown_and_replay(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        params = self.result_params(claim, reason="graceful-stop", initiator="host")
        board.store.workflow.cancel({"runId": run["runId"]}, console_authority={"sessionId": "browser"})
        receipt = board.call("worker_result", params)
        self.assertTrue(receipt["attempt"]["shutdownConfirmed"])
        self.assertEqual(receipt["attempt"]["stopInitiator"], "console")
        self.assertEqual(receipt["attempt"]["result"]["stopInitiator"], "console")
        self.assertIsNone(receipt["attempt"]["result"]["terminationReason"])
        self.assertEqual(receipt["task"]["state"], "cancelled")
        self.assertTrue(board.call("worker_result", params)["duplicate"])

    def test_local_unattributed_worker_stop_records_unknown_without_host_intent(self):
        board = self.board()
        self.register(board)
        self.submit(board)
        claim = self.claim(board)
        receipt = self.result(board, claim, reason="unknown-stop", initiator="unknown")
        self.assertEqual(receipt["attempt"]["stopInitiator"], "unknown")
        self.assertEqual(receipt["attempt"]["result"]["terminationReason"], "unknown-stop")

    def test_invalid_summary_identity_never_keeps_a_graceful_completion_reason(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        params = self.result_params(claim, reason="graceful-stop", initiator="host")
        params["result"]["turn"]["generation"] += 1
        receipt = board.call("worker_result", params)
        self.assertEqual(receipt["attempt"]["result"]["status"], "failed")
        self.assertIsNone(receipt["attempt"]["result"]["terminationReason"])
        self.assertTrue(receipt["attempt"]["shutdownConfirmed"])
        self.assertEqual(receipt["task"]["workflowState"], "cancelled")

    def test_unconfirmed_stop_keeps_uncertain_ownership_and_no_termination_reason(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        receipt = self.result(board, claim, reason=None, initiator="host", status="failed", shutdown=False)
        self.assertEqual(receipt["task"]["state"], "reconciliation-needed")
        self.assertEqual(receipt["attempt"]["executionState"], "uncertain")
        self.assertIsNone(receipt["attempt"]["result"]["terminationReason"])
        self.assertFalse(receipt["attempt"]["shutdownConfirmed"])

    def test_host_cancel_persists_five_minute_deadline_and_replay_keeps_it(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        self.assertIsNone(self.attempt(board, claim)["graceDeadline"])
        board.call("workflow_cancel", {"runId": run["runId"], "commandId": "first-stop", **self.control(run)})
        stopped = self.attempt(board, claim)
        self.assertEqual(stopped["stopInitiator"], "host")
        self.assertTrue(stopped["cancelRequested"])
        self.assertEqual(
            datetime.fromisoformat(stopped["graceDeadline"].replace("Z", "+00:00")),
            datetime.fromisoformat(stopped["cancelRequestedAt"].replace("Z", "+00:00")) + timedelta(seconds=300),
        )
        self.assertEqual(board.call("task_get", {"runId": run["runId"]})["task"]["graceDeadline"],
                         stopped["graceDeadline"])
        board.call("workflow_cancel", {"runId": run["runId"], "commandId": "second-stop", **self.control(run)})
        self.assertEqual(self.attempt(board, claim)["graceDeadline"], stopped["graceDeadline"])
        self.assertEqual(self.attempt(board, claim)["cancelRequestedAt"], stopped["cancelRequestedAt"])

    def test_console_cancel_and_macro_stop_override_host_grace(self):
        board = self.board(max_concurrent=2)
        self.register(board)
        self.register(board, "w2")
        first = self.submit(board, kind="worktree")
        first_claim = self.claim(board)
        board.call("workflow_cancel", {"runId": first["runId"], **self.control(first)})
        self.assertIsNotNone(self.attempt(board, first_claim)["graceDeadline"])
        board.store.workflow.cancel({"runId": first["runId"]}, console_authority={"sessionId": "browser"})
        upgraded = self.attempt(board, first_claim)
        self.assertEqual(upgraded["stopInitiator"], "console")
        self.assertIsNone(upgraded["graceDeadline"])

        second = self.submit(board, kind="worktree", request_id="second")
        second_claim = self.claim(board, worker_id="w2", claim_request_id="second-claim", run_id=second["runId"])
        stopped = board.store.workflow.stop_macro_task(
            {"macroTaskId": "run:" + second["runId"], "commandId": "browser-stop"},
            console_authority={"sessionId": "browser"},
        )
        self.assertEqual(stopped["runIds"], [second["runId"]])
        immediate = self.attempt(board, second_claim)
        self.assertEqual(immediate["stopInitiator"], "console")
        self.assertIsNone(immediate["graceDeadline"])

    def test_host_cancel_passes_grace_to_active_helper(self):
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper", "task": "help", "cwd": str(self.workdir("helper")),
            "executionWorkspace": {"kind": "worktree", "access": "write"},
        }])
        child = approved["children"][0]["taskId"]
        claim = self.claim(board, claim_request_id="child-claim", run_id=child)
        board.call("workflow_cancel", {"runId": parent["runId"], **self.control(approved)})
        attempt = self.attempt(board, claim)
        self.assertEqual(attempt["stopInitiator"], "host")
        self.assertIsNotNone(attempt["graceDeadline"])

    def test_service_stop_and_uncertain_attempt_have_no_grace(self):
        board = self.board(max_concurrent=2)
        self.register(board)
        self.register(board, "w2")
        run = self.submit(board)
        claim = self.claim(board)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='uncertain' WHERE attempt_id=?",
                               (claim["claim"]["attempt"]["attemptId"],))
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        uncertain = self.attempt(board, claim)
        self.assertEqual(uncertain["stopInitiator"], "host")
        self.assertIsNone(uncertain["graceDeadline"])

        other = self.submit(board, kind="worktree", request_id="other")
        other_claim = self.claim(board, worker_id="w2", claim_request_id="other-claim", run_id=other["runId"])
        board.store.workflow.cancel_for_service_stop("service is shutting down")
        service = self.attempt(board, other_claim)
        self.assertEqual(service["stopInitiator"], "unknown")
        self.assertIsNone(service["graceDeadline"])

    def test_result_reason_requires_matching_durable_stop_and_confirmed_graceful_result(self):
        self.assertEqual(set(TERMINATION_REASONS), {
            "completed", "graceful-stop", "grace-expired", "forced-stop", "console-stop",
            "unknown-stop", "deadline", "harness-error", "transport-error",
        })
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        for reason, initiator, status, shutdown in (
            ("invented", "host", "cancelled", True),
            ("graceful-stop", None, "cancelled", True),
            ("graceful-stop", "console", "cancelled", True),
            ("graceful-stop", "host", "ok", True),
            ("graceful-stop", "host", "cancelled", False),
            ("console-stop", "host", "cancelled", True),
            ("unknown-stop", "console", "cancelled", True),
            ("deadline", None, "cancelled", True),
        ):
            with self.subTest(reason=reason, initiator=initiator, status=status, shutdown=shutdown):
                with self.assertRaises(BoardError) as rejected:
                    self.result(board, claim, reason=reason, initiator=initiator,
                                status=status, shutdown=shutdown)
                self.assertEqual(rejected.exception.code, "INVALID_ARGUMENT")
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        with self.assertRaises(BoardError) as mismatch:
            self.result(board, claim, reason="console-stop", initiator="console")
        self.assertEqual(mismatch.exception.code, "INVALID_ARGUMENT")
        settled = self.result(board, claim, reason="graceful-stop", initiator="host")
        self.assertEqual(settled["attempt"]["result"]["terminationReason"], "graceful-stop")
        self.assertEqual(settled["attempt"]["result"]["stopInitiator"], "host")
