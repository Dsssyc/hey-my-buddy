"""A Host conclusion fences active work and records nothing until stop is proven."""
from datetime import datetime, timedelta
from unittest.mock import patch

from blackboard.tasks.test_lead_owned import LeadOwnedTests
from blackboard.tasks.test_workflow import WorkflowTestCase
from cli.test_blocking import Clock
from hey_my_buddy.cli.blocking import conclude_run
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.transport import ServiceError


class RunningConclusionTests(WorkflowTestCase):
    lead = LeadOwnedTests.lead
    owned = LeadOwnedTests.owned

    def get(self, board, run_id):
        return board.call("workflow_get", {"runId": run_id})

    def cli_service(self, board, on_wait):
        """Use the real private service, retaining the CLI transport's view shape."""
        def service(method, params, directory):
            self.assertEqual(directory, self.directory)
            if method == "task_wait":
                on_wait(params)
                params = {**params, "timeoutMs": 0}
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
        waited = []

        def stop(params):
            waited.append(params)
            current = self.get(board, run_id)
            self.assertEqual(current["state"], "cancelled")
            self.assertIsNone(current["hostConclusion"])
            self.finish_turn(board, claim, runner_status="cancelled", seal=False)

        answer = conclude_run({"runId": run_id, "note": "Finish this work", "waitSeconds": 1},
                              self.directory, service=self.cli_service(board, stop))
        self.assertEqual(answer["state"], "concluded")
        self.assertEqual(len(waited), 1)
        self.assertFalse(waited[0]["consumeReminders"])
        self.assertIsNotNone(self.get(board, run_id)["hostConclusion"])

    def test_cli_timeout_records_only_cancel_and_later_retry_finishes(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        claim = self.claim(board)
        clock = Clock()
        packet = {"runId": run_id, "note": "Stop without inventing proof", "waitSeconds": 1}
        service = self.cli_service(board, lambda params: clock.advance(params["timeoutMs"] / 1000))
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
            connection.execute("UPDATE model_service_concurrency SET concurrency_limit=4")
        child_claim = self.claim(board, claim_request_id="child", run_id=child["runId"])
        self.assertIsNotNone(child_claim["claim"])
        stops = [(lead["runId"], {"claim": lead_claim}), (child["runId"], child_claim)]

        def stop(params):
            self.assertEqual(params["runId"], lead["runId"])
            self.assertIsNone(self.get(board, lead["runId"])["hostConclusion"])
            self.assertIsNone(self.get(board, child["runId"])["hostConclusion"])
            run_id, claim = stops.pop(0)
            self.assertEqual(self.get(board, run_id)["state"], "cancelled")
            self.finish_turn(board, claim, runner_status="cancelled", seal=False)

        answer = conclude_run({"runId": lead["runId"], "note": "End the entire Lead tree", "waitSeconds": 1},
                              self.directory, service=self.cli_service(board, stop))
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

    def test_worker_helper_tree_uses_the_same_stop_and_conclusion_path(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        boundary = self.get(board, run_id)
        approved = self.decide(board, boundary, boundary["activeRequest"]["requestId"], autoContinue=False,
                              helpers=[{"title": "helper", "task": "Inspect a dependency",
                                        "cwd": str(self.workdir("helper")),
                                        "workspace": {"kind": "worktree", "access": "write"}}])
        child_id = approved["children"][0]["taskId"]
        claim = self.claim(board, claim_request_id="child", run_id=child_id)
        packet = {"runId": run_id, "note": "Close the entire dependency"}
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_conclude", packet)
        self.assertEqual(refused.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(refused.exception.details["unconfirmedRunIds"], [child_id])
        self.assertEqual(self.get(board, run_id)["state"], "cancelled")
        self.assertEqual(self.get(board, child_id)["state"], "cancelled")
        self.assertEqual(self.get(board, child_id)["task"]["stopInitiator"], "host")
        self.assertIsNone(self.get(board, child_id)["hostConclusion"])
        self.finish_turn(board, claim, runner_status="cancelled", seal=False)
        concluded = board.call("workflow_conclude", packet)
        self.assertEqual(concluded["state"], "concluded")
        child = self.get(board, child_id)
        self.assertEqual(child["state"], "concluded")
        self.assertEqual(child["hostConclusion"]["note"], packet["note"])
        self.assertTrue(child["shutdown"]["selfConfirmed"])
