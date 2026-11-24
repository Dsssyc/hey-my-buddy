"""A Host conclusion fences active work and records nothing until stop is proven."""
from datetime import datetime, timedelta
import json
from unittest.mock import patch

from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import WorkflowTestCase
from cli.test_blocking import Clock
from hey_my_buddy.cli.blocking import conclude_run
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.transport import ServiceError


class RunningConclusionTests(WorkflowTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned

    def get(self, board, run_id):
        return board.call("workflow_get", {"runId": run_id})

    def open_conflict(self, board, run_id):
        view = self.get(board, run_id)
        evidence = {"manifestSha256": "fixture", "blockingPaths": ["outside.txt"], "entries": []}
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO workflow_workspace_conflicts(conflict_id,run_id,attempt_id,turn_id,"
                "observed_fingerprint,evidence_json,state,resolved_paths_json,conflicting_paths_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,'open','[]','[]',?,?)",
                ("conflict-" + run_id, run_id, view["currentTurn"]["attemptId"], view["currentTurn"]["turnId"],
                 "fixture", json.dumps(evidence), board.store.now(), board.store.now()),
            )

    def assert_no_cancellation(self, board, run_ids, before):
        self.assertEqual(board.store.head(), before)
        for run_id in run_ids:
            self.assertIsNone(self.get(board, run_id)["cancellation"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE kind IN ('workflow.cancelled',"
                "'workflow.lead_task_cancelled')").fetchone()[0], 0)

    def test_descendant_conflict_refuses_before_cancelling_lead_tree(self):
        board = self.board()
        root, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        self.finish_turn(board, self.claim(board, run_id=child["runId"]), disposition="attention")
        self.open_conflict(board, child["runId"])
        requests = self.get(board, child["runId"])["requests"]
        before = board.store.head()
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_conclude", {"runId": root["runId"], "note": "Close the tree"})
        self.assertEqual(refused.exception.code, "CONFLICT")
        self.assert_no_cancellation(board, (root["runId"], child["runId"]), before)
        self.assertEqual(self.get(board, child["runId"])["requests"], requests)

    def test_pending_continuation_refuses_before_cancelling_any_run(self):
        for pending in ("root", "child"):
            with self.subTest(pending=pending):
                board = self.board()
                root, claim = self.lead(board, "pending-lead-" + pending)
                child = self.owned(board, claim)
                self.finish_turn(board, {"claim": claim}, disposition="attention")
                run_id = root["runId"] if pending == "root" else child["runId"]
                with board.store.db.write() as connection:
                    connection.execute(
                        "INSERT INTO workflow_continuations(continuation_id,run_id,kind,input_text,"
                        "workspace_facts_role,state,created_at) VALUES(?,?,'manual','Continue','pending','recorded',?)",
                        ("pending-" + pending, run_id, board.store.now()),
                    )
                before = board.store.head()
                with self.assertRaises(BoardError) as refused:
                    board.call("workflow_conclude", {"runId": root["runId"], "note": "Close the tree"})
                self.assertEqual(refused.exception.code, "CONFLICT")
                self.assert_no_cancellation(board, (root["runId"], child["runId"]), before)
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute("SELECT state FROM workflow_continuations WHERE continuation_id=?", ("pending-" + pending,)).fetchone()[0], "recorded")

    def test_failed_target_keeps_failure_while_waiting_for_shutdown(self):
        board = self.board()
        root = self.submit(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, runner_status="failed", exit_code=1, seal=False)
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET shutdown_confirmed=0 WHERE attempt_id=?", (attempt_id,))
        packet = {"runId": root["runId"], "note": "Record the actual failure"}
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_conclude", packet)
        self.assertEqual(refused.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(self.get(board, root["runId"])["state"], "failed")
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET shutdown_confirmed=1 WHERE attempt_id=?", (attempt_id,))
        board.call("workflow_conclude", packet)
        self.assertEqual(self.get(board, root["runId"])["hostConclusion"]["executionStatus"], "failed")

    def test_retries_do_not_repeat_descendant_cancellation_events(self):
        board = self.board()
        root, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        self.claim(board, run_id=child["runId"])
        for _ in range(3):
            with self.assertRaises(BoardError) as refused:
                board.call("workflow_conclude", {"runId": root["runId"], "note": "Close the tree"})
            self.assertEqual(refused.exception.code, "SHUTDOWN_UNCONFIRMED")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.lead_task_cancelled'",
                (child["runId"],)).fetchone()[0], 1)

    def test_conflict_boundary_does_not_advertise_conclude(self):
        board = self.board()
        root = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="attention")
        self.open_conflict(board, root["runId"])
        self.assertNotIn("conclude", self.get(board, root["runId"])["actions"])

    def cli_service(self, board, on_wait):
        """Use the real private service, retaining the CLI transport's view shape."""
        def service(method, params, directory):
            self.assertEqual(directory, self.directory)
            if method == "task_observe":
                on_wait(params)
            try:
                result = board.call("workflow_conclude" if method == "conclude" else method, params)
            except BoardError as error:
                raise ServiceError(error.code, error.message, **error.details) from error
            return result["task"] if method == "task_get" else result
        return service

    def test_one_cli_call_cancels_waits_and_records_the_conclusion(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        claim = self.claim(board)
        clock = Clock()
        waited = []

        def stop(params):
            waited.append(params)
            clock.advance(0.1)
            current = self.get(board, run_id)
            self.assertEqual(current["state"], "cancelled")
            self.assertIsNone(current["hostConclusion"])
            self.finish_turn(board, claim, runner_status="cancelled", seal=False)

        answer = conclude_run({"runId": run_id, "note": "Finish this work", "waitSeconds": 1},
                              self.directory, service=self.cli_service(board, stop), clock=clock)
        self.assertEqual(answer["state"], "concluded")
        self.assertEqual(len(waited), 1)
        self.assertNotIn("reminderWaitId", waited[0])
        self.assertIsNotNone(self.get(board, run_id)["hostConclusion"])

    def test_cli_timeout_records_only_cancel_and_later_retry_finishes(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        claim = self.claim(board)
        clock = Clock()
        packet = {"runId": run_id, "note": "Stop without inventing proof", "waitSeconds": 1}
        service = self.cli_service(board, lambda params: clock.advance(1))
        answer = conclude_run(packet, self.directory, service=service, clock=clock)
        self.assertEqual(answer["error"]["code"], "SHUTDOWN_UNCONFIRMED")
        self.assertTrue(answer["timedOut"])
        self.assertEqual(answer["waitedSeconds"], 1)
        self.assertTrue(answer["cancellationRecorded"])
        self.assertFalse(answer["conclusionRecorded"])
        current = self.get(board, run_id)
        self.assertIsNotNone(current["cancellation"])
        self.assertIsNone(current["hostConclusion"])
        self.finish_turn(board, claim, runner_status="cancelled", seal=False)
        answer = conclude_run(packet, self.directory, service=service, clock=clock)
        self.assertEqual(answer["state"], "concluded")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)

    def test_one_cli_call_waits_for_every_lead_owned_stop(self):
        board = self.board(max_concurrent=4)
        lead, lead_claim = self.lead(board)
        child = self.owned(board, lead_claim)
        # The two fixtures represent independent execution processes.
        with board.store.db.write() as connection:
            connection.execute("UPDATE model_services SET concurrency_limit=4")
        child_claim = self.claim(board, claim_request_id="child", run_id=child["runId"])
        self.assertIsNotNone(child_claim["claim"])
        clock = Clock()
        stops = [(lead["runId"], {"claim": lead_claim}), (child["runId"], child_claim)]

        def stop(params):
            self.assertEqual(params["runId"], lead["runId"])
            self.assertIsNone(self.get(board, lead["runId"])["hostConclusion"])
            self.assertIsNone(self.get(board, child["runId"])["hostConclusion"])
            clock.advance(0.1)
            run_id, claim = stops.pop(0)
            self.assertEqual(self.get(board, run_id)["state"], "cancelled")
            self.finish_turn(board, claim, runner_status="cancelled", seal=False)

        answer = conclude_run({"runId": lead["runId"], "note": "End the entire Lead tree", "waitSeconds": 1},
                              self.directory, service=self.cli_service(board, stop), clock=clock)
        self.assertEqual(answer["state"], "concluded")
        self.assertEqual(stops, [])
        self.assertEqual(self.get(board, child["runId"])["state"], "concluded")

    def test_running_conclusion_requests_wrap_up_and_retry_keeps_the_original_stop(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        claim = self.claim(board)
        packet = {"runId": run_id, "note": "No further work is needed"}
        self.assertIn("conclude", self.get(board, run_id)["actions"])
        with patch.object(self.workspace, "host_seal", side_effect=AssertionError("Stop must be proven before sealing")):
            with self.assertRaises(BoardError) as refused:
                board.call("workflow_conclude", packet)
            self.assertEqual(refused.exception.code, "SHUTDOWN_UNCONFIRMED")
            self.assertTrue(refused.exception.details["cancellationRecorded"])
            self.assertFalse(refused.exception.details["conclusionRecorded"])
            first = self.get(board, run_id)
            self.assertEqual(first["state"], "cancelled")
            self.assertIsNone(first["hostConclusion"])
            attempt = board.call("task_get", {"runId": run_id})["task"]["selectedAttempt"]
            self.assertTrue(attempt["cancelRequested"])
            self.assertEqual(attempt["stopInitiator"], "host")
            self.assertEqual(datetime.fromisoformat(attempt["graceDeadline"].replace("Z", "+00:00")),
                             datetime.fromisoformat(attempt["cancelRequestedAt"].replace("Z", "+00:00"))
                             + timedelta(seconds=300))
            with self.assertRaises(BoardError) as repeated:
                board.call("workflow_conclude", packet)
            self.assertEqual(repeated.exception.code, "SHUTDOWN_UNCONFIRMED")
            again = self.get(board, run_id)
            self.assertEqual(again["cancellation"], first["cancellation"])
            self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["selectedAttempt"]["graceDeadline"],
                             attempt["graceDeadline"])
        self.finish_turn(board, claim, runner_status="cancelled", seal=False)
        concluded = board.call("workflow_conclude", packet)
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(self.get(board, run_id)["hostConclusion"]["executionStatus"], "cancelled")
        head = board.store.head()
        replay = board.call("workflow_conclude", packet)
        self.assertEqual(replay["conclusionId"], concluded["conclusionId"])
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(board.store.head(), head)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='workflow.cancelled'").fetchone()[0], 1)

    def test_queued_conclusion_cancels_without_admitting_a_turn(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        result = board.call("workflow_conclude", {"runId": run_id, "note": "Superseded before admission"})
        view = self.get(board, run_id)
        self.assertEqual(result["state"], "concluded")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertEqual(view["hostConclusion"]["executionStatus"], "cancelled")
        self.assertTrue(view["shutdown"]["selfConfirmed"])
        self.assertIsNone(view["hostConclusion"]["attemptId"])

    def test_host_boundary_can_be_concluded_without_an_intermediate_cancel(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        self.finish_turn(board, self.claim(board), disposition="attention")
        before = self.get(board, run_id)
        self.assertEqual(before["state"], "awaiting-host")
        self.assertIn("conclude", before["actions"])
        result = board.call("workflow_conclude", {"runId": run_id, "note": "The boundary needs no continuation"})
        view = self.get(board, run_id)
        self.assertEqual(result["state"], "concluded")
        self.assertIsNone(view["activeRequest"])
        self.assertFalse(any(request["state"] == "open" for request in view["requests"]))
        self.assertIsNotNone(view["hostConclusion"])

    def test_stopped_outcomes_keep_their_execution_status(self):
        board = self.board()
        for status in ("failed", "cancelled", "delivered"):
            with self.subTest(status=status):
                run_id = self.submit(board, request_id=status, kind="worktree")["runId"]
                claim = self.claim(board, run_id=run_id)
                if status == "cancelled":
                    board.call("workflow_cancel", {"runId": run_id})
                self.finish_turn(board, claim, runner_status="ok" if status == "delivered" else status,
                                 exit_code=1 if status == "failed" else 0, seal=status == "delivered")
                before = self.get(board, run_id)
                packet = {"runId": run_id, "note": "Reviewed the stopped outcome"}
                if status == "delivered":
                    packet["artifactId"] = before["finalArtifactId"]
                board.call("workflow_conclude", packet)
                self.assertEqual(self.get(board, run_id)["hostConclusion"]["executionStatus"], status)
