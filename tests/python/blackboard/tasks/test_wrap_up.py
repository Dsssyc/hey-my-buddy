"""Durable stop intent and observed termination at the blackboard boundary."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.store.db import TERMINATION_REASONS
from hey_my_buddy.buddy.roles.controller import stop_termination_reason
from hey_my_buddy.errors import BoardError


class WrapUpStopTests(WorkflowTestCase):
    def attempt(self, board, claim):
        return board.call("task_get", {"runId": claim["claim"]["attempt"]["taskId"]})["task"]["selectedAttempt"]

    def result(self, board, claim, *, reason, status="cancelled", shutdown=True):
        return board.call('execution_result', self.result_params(claim, reason=reason,
                                                              status=status, shutdown=shutdown))

    def result_params(self, claim, *, reason, status="cancelled", shutdown=True):
        attempt = claim["claim"]["attempt"]
        record = self.turn_record(claim)
        payload = {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16, 'status': status, 'shutdownConfirmed': shutdown, 'terminationReason': reason, 'result': {'status': status, 'mode': 'run', 'processState': {'shutdownConfirmed': shutdown}, 'logPaths': {'stdout': '/tmp/out', 'stderr': '/tmp/err'}, 'turn': record, 'turnResultPath': '/tmp/turn-output.json', 'workspaceSeal': self.workspace.seal(self.directory, claim['claim']['turn']['input']['executionWorkspace'], attempt['taskId'], attempt['attemptId'])}}
        return payload

    def test_console_escalation_during_host_receipt_preserves_shutdown_and_replay(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        params = self.result_params(claim, reason="wrap-up")
        board.store.workflow.cancel({"runId": run["runId"]}, console_authority={"sessionId": "browser"})
        receipt = board.call('execution_result', params)
        self.assertTrue(receipt["attempt"]["shutdownConfirmed"])
        self.assertEqual(receipt["attempt"]["stopInitiator"], "console")
        self.assertNotIn("stopInitiator", receipt["attempt"]["result"])
        self.assertEqual(receipt["attempt"]["result"]["terminationReason"], "wrap-up")
        self.assertIsNotNone(receipt["attempt"]["graceDeadline"])
        self.assertEqual(receipt["task"]["state"], "cancelled")
        self.assertEqual(receipt["task"]["terminationReason"], "wrap-up")
        with self.assertRaises(BoardError) as caught:
            board.call("execution_result", params)
        self.assertEqual(caught.exception.code, "ATTEMPT_FINISHED")

    def test_local_unattributed_worker_stop_records_unknown_without_host_intent(self):
        board = self.board()
        self.register(board)
        self.submit(board)
        claim = self.claim(board)
        receipt = self.result(board, claim, reason="unknown-stop")
        self.assertEqual(receipt["attempt"]["stopInitiator"], "unknown")
        self.assertEqual(receipt["attempt"]["result"]["terminationReason"], "unknown-stop")

    def test_invalid_summary_identity_becomes_confirmed_forced_stop(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        params = self.result_params(claim, reason="wrap-up")
        params["result"]["turn"]["generation"] += 1
        params["result"]["partialOutput"] = {"partial": True, "verified": False, "final": False,
                                               "reason": "wrap-up"}
        receipt = board.call('execution_result', params)
        self.assertEqual(receipt["attempt"]["result"]["status"], "failed")
        self.assertEqual(receipt["attempt"]["result"]["result"]["receiptRejection"]["code"], "TURN_INVALID")
        self.assertTrue(receipt["attempt"]["shutdownConfirmed"])
        self.assertEqual(receipt["task"]["state"], "failed")
        self.assertEqual(receipt["task"]["workflowState"], "cancelled")

    def test_unconfirmed_stop_keeps_uncertain_ownership_and_no_termination_reason(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        receipt = self.result(board, claim, reason=None, status="failed", shutdown=False)
        self.assertEqual(receipt["task"]["state"], "cancelling")
        self.assertEqual(receipt["attempt"]["executionState"], "finalizing")
        self.assertFalse(receipt["attempt"]["resultAvailable"])
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

    def test_console_cancel_and_macro_stop_are_immediate_and_keep_prior_deadline(self):
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
        self.assertIsNotNone(upgraded["graceDeadline"])

        second = self.submit(board, kind="worktree", request_id="second")
        second_claim = self.claim(board, worker_id="w2", claim_request_id="second-claim", run_id=second["runId"])
        stopped = board.store.workflow.stop_macro_task(
            {"macroTaskId": "run:" + second["runId"], "commandId": "browser-stop"},
            console_authority={"sessionId": "browser"},
        )
        self.assertEqual(stopped["results"], [{"runId": second["runId"], "state": "cancelled", "stopped": True}])
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
        self.assertEqual(service["stopInitiator"], "service")
        self.assertIsNone(service["graceDeadline"])

    def test_host_request_after_an_existing_forced_stop_does_not_start_wrap_up(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE attempts SET cancel_requested_at=?,stop_initiator='unknown' WHERE attempt_id=?",
                ("2026-01-01T00:00:00Z", attempt_id),
            )
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        stopped = self.attempt(board, claim)
        self.assertEqual(stopped["stopInitiator"], "unknown")
        self.assertIsNone(stopped["graceDeadline"])

    def test_stop_reasons_require_confirmed_shutdown_and_durable_matching_facts(self):
        self.assertEqual(set(TERMINATION_REASONS), {
            "completed", "wrap-up", "grace-expired", "forced-stop", "console-stop",
            "service-stop", "unknown-stop", "deadline", "harness-error", "transport-error", "interrupted",
            "workspace-error",
        })
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        for reason, status, shutdown in (
            ("invented", "cancelled", True),
            ("wrap-up", "ok", True),
            ("wrap-up", "cancelled", False),
            ("service-stop", "cancelled", False),
            ("unknown-stop", "cancelled", False),
            ("forced-stop", "cancelled", False),
        ):
            with self.subTest(reason=reason, status=status, shutdown=shutdown):
                with self.assertRaises(BoardError) as rejected:
                    self.result(board, claim, reason=reason, status=status, shutdown=shutdown)
                self.assertEqual(rejected.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as forbidden:
            board.call('execution_result', {**self.result_params(claim, reason=None), "stopInitiator": "host"})
        self.assertEqual(forbidden.exception.code, "INVALID_ARGUMENT")
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        rejected = self.result(board, claim, reason="console-stop")
        self.assertEqual(rejected["attempt"]["result"]["result"]["receiptRejection"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn("stopInitiator", rejected["attempt"]["result"])

    def test_harness_capability_controls_grace_without_a_name_list(self):
        board = self.board(max_concurrent=4)
        for index, (name, supported) in enumerate((("claude", False), ("zcode", False),
                                                  ("codex", False), ("claude", True))):
            with self.subTest(harness=name, interrupted_resume=supported):
                self.executors[name].interrupted_resume = supported
                worker_id = f"capability-worker-{index}"
                pass  # Admission is owned by the service.
                run = self.submit(board, request_id=f'capability-run-{index}', harness=name, kind='worktree')
                claim = self.claim(board, worker_id=worker_id, claim_request_id=f'capability-claim-{index}',
                                   run_id=run['runId'])
                self.assertIsNotNone(claim['claim'])
                board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
                self.assertEqual(self.attempt(board, claim)["graceDeadline"] is not None, supported)

    def test_expired_wrap_up_receipt_does_not_import_late_turn(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        settled = self.result(board, claim, reason="grace-expired")
        self.assertEqual(settled["attempt"]["result"]["terminationReason"], "grace-expired")
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.assertNotEqual(view["currentTurn"]["state"], "returned")

    def test_get_exposes_stop_reason_origin_and_grace(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(view["task"]["stopInitiator"], "host")
        self.assertIsNotNone(view["task"]["graceDeadline"])
        self.assertIsNone(view["task"]["terminationReason"])
        receipt = self.result(board, claim, reason="wrap-up")
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(view["task"]["terminationReason"], "wrap-up")
        self.assertEqual(view["task"]["stopInitiator"], "host")
        self.assertIsNotNone(view["task"]["graceDeadline"])
        self.assertEqual(receipt["attempt"]["result"]["terminationReason"], "wrap-up")

    def test_console_and_service_stops_keep_their_distinct_causes(self):
        board = self.board()
        self.register(board)
        for origin, reason in (("console", "console-stop"), ("service", "service-stop")):
            with self.subTest(origin=origin):
                run = self.submit(board, request_id=origin, kind="worktree", cwd=str(self.workdir(origin)))
                claim = self.claim(board, claim_request_id=origin + "-claim", run_id=run["runId"])
                if origin == "console":
                    board.store.workflow.cancel({"runId": run["runId"]},
                                                console_authority={"sessionId": "browser"})
                else:
                    board.store.workflow.cancel_for_service_stop("service is shutting down")
                receipt = self.result(board, claim, reason=reason)
                self.assertEqual(receipt["attempt"]["stopInitiator"], origin)
                self.assertEqual(receipt["attempt"]["result"]["terminationReason"], reason)

    def test_console_stop_receipt_survives_a_later_service_intent(self):
        self._assert_stop_order("console", "service", "console", "console-stop")

    def test_service_stop_receipt_survives_a_later_console_intent(self):
        self._assert_stop_order("service", "console", "service", "service-stop")

    def test_console_stop_escalates_an_existing_host_wrap_up(self):
        self._assert_stop_order("host", "console", "console", "console-stop")

    def test_host_stop_preserves_an_existing_console_stop(self):
        self._assert_stop_order("console", "host", "console", "console-stop")

    def _assert_stop_order(self, first, second, expected_initiator, expected_reason):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        first_at = board.store.now()
        second_at = (datetime.fromisoformat(first_at.replace("Z", "+00:00"))
                     + timedelta(seconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        for initiator, requested_at in ((first, first_at), (second, second_at)):
            with patch.object(board.store.workflow, "now", return_value=requested_at):
                if initiator == "console":
                    board.store.workflow.cancel({"runId": run["runId"]},
                                                console_authority={"sessionId": "browser"})
                elif initiator == "service":
                    with board.store.db.write() as connection:
                        board.store.workflow._request_attempt_stop(
                            connection, run["runId"], requested_at, initiator="service", wrap_up=False)
                else:
                    board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
            if initiator == first:
                first_stop = self.attempt(board, claim)

        cancel_view = board.call("task_get", {"runId": run["runId"]})["task"]
        self.assertEqual(cancel_view["stopInitiator"], expected_initiator)
        with board.store.db.read() as connection:
            stored = connection.execute(
                "SELECT stop_initiator,cancel_requested_at,grace_deadline FROM attempts WHERE attempt_id=?",
                (claim["claim"]["attempt"]["attemptId"],),
            ).fetchone()
        self.assertEqual(tuple(stored), (expected_initiator, first_at, first_stop["graceDeadline"]))
        self.assertEqual(cancel_view["graceDeadline"], first_stop["graceDeadline"])
        reason = stop_termination_reason(shutdown_confirmed=True,
                                         stop_initiator=cancel_view["stopInitiator"])
        self.assertEqual(reason, expected_reason)
        receipt = self.result(board, claim, reason=reason)
        self.assertEqual(receipt["attempt"]["stopInitiator"], expected_initiator)
        self.assertEqual(receipt["attempt"]["result"]["terminationReason"], expected_reason)
        self.assertEqual(receipt["task"]["stopInitiator"], expected_initiator)
        self.assertEqual(receipt["task"]["terminationReason"], expected_reason)
        host_view = board.call("workflow_get", {"runId": run["runId"]})["task"]
        self.assertEqual(host_view["stopInitiator"], expected_initiator)
        self.assertEqual(host_view["terminationReason"], expected_reason)

    def test_console_stop_receipt_requires_column_even_with_console_event(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.store.workflow.cancel({"runId": run["runId"]},
                                    console_authority={"sessionId": "browser"})
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET stop_initiator='service' WHERE attempt_id=?",
                               (claim["claim"]["attempt"]["attemptId"],))
        rejected = self.result(board, claim, reason="console-stop")
        self.assertEqual(rejected["attempt"]["result"]["result"]["receiptRejection"]["code"], "INVALID_ARGUMENT")
        self.assertTrue(self.attempt(board, claim)["resultAvailable"])

    def test_regular_endings_keep_their_own_causes(self):
        board = self.board()
        self.register(board)
        for reason, status in (("completed", "ok"), ("harness-error", "failed"),
                               ("transport-error", "failed"), ("workspace-error", "failed")):
            with self.subTest(reason=reason):
                run = self.submit(board, request_id=reason, kind="worktree", cwd=str(self.workdir(reason)))
                claim = self.claim(board, claim_request_id=reason + "-claim", run_id=run["runId"])
                receipt = self.result(board, claim, reason=reason, status=status)
                self.assertEqual(receipt["attempt"]["result"]["terminationReason"], reason)
                self.assertIsNone(receipt["attempt"]["stopInitiator"])
