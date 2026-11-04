"""Host review of a Lead cannot control the Lead's independent owned work."""

from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.tasks import workflow
from hey_my_buddy.errors import BoardError


class LeadReviewBoundaryTests(WorkflowTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned

    def view(self, board, run):
        return board.call("workflow_get", {"runId": run["runId"]})

    def output_artifact(self, board, run):
        with board.store.db.read() as db:
            row = db.execute(
                "SELECT artifact_id FROM workflow_artifacts WHERE run_id=? AND kind='output' ORDER BY rowid DESC LIMIT 1",
                (run["runId"],),
            ).fetchone()
        self.assertIsNotNone(row)
        return row["artifact_id"]

    def test_host_cannot_target_independent_lead_owned_work_through_the_lead(self):
        operations = {
            "workflow_accept": lambda child: {
                "artifactId": child["finalArtifactId"], "note": "Host review",
                "notRequired": "The fixture has no integration target",
            },
            "workflow_conclude": lambda child: {
                "artifactId": child["finalArtifactId"], "note": "Host closes child",
            },
            "workflow_continue": lambda child: {
                "artifactId": child["finalArtifactId"], "input": "Continue the child",
            },
            "workflow_cancel": lambda child: {"note": "Host stops child"},
        }
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child)
        for operation, fields in operations.items():
            with self.subTest(operation=operation):
                before = self.view(board, child)
                with self.assertRaises(BoardError) as caught:
                    board.call(operation, {
                        "runId": lead["runId"], "targetRunId": child["runId"],
                        **fields(child),
                    })
                self.assertEqual(caught.exception.code, "UNAUTHORIZED")
                after = self.view(board, child)
                self.assertEqual(after["state"], before["state"])
                self.assertEqual(after["finalArtifactId"], before["finalArtifactId"])

    def test_direct_host_actions_cannot_mutate_lead_owned_work(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child, disposition="assistance")
        request_id = child["activeRequest"]["requestId"]
        for operation, fields in (
            ("workflow_continue", {"requestId": request_id, "decision": "decline"}),
            ("workflow_cancel", {"note": "Wrong owner"}),
        ):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": child["runId"], **fields})
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertEqual(self.view(board, child)["state"], "awaiting-host")
        self.assertEqual(self.view(board, child)["activeRequest"]["requestId"], request_id)

    def test_host_reads_and_rejected_cancel_leave_owned_execution_running(self):
        board = self.board(max_concurrent=4)
        with board.store.db.write() as db:
            db.execute(
                "INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                " VALUES(?,?,?,8)",
                (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]),
            )
        lead, claim = self.lead(board)
        child = self.owned(board, claim, "running-owned")
        child_claim = self.claim(board, "w2", claim_request_id="owned-execution", run_id=child["runId"])["claim"]
        self.assertEqual(child_claim["attempt"]["taskId"], child["runId"])
        before = board.call("task_get", {"runId": child["runId"]})["task"]
        self.view(board, lead)
        self.view(board, child)
        for packet in ({"runId": child["runId"]},
                       {"runId": lead["runId"], "targetRunId": child["runId"]}):
            with self.subTest(packet=packet), self.assertRaises(BoardError) as caught:
                board.call("workflow_cancel", packet)
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        after = board.call("task_get", {"runId": child["runId"]})["task"]
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["selectedAttempt"]["attemptId"], before["selectedAttempt"]["attemptId"])
        self.assertFalse(after["selectedAttempt"]["cancelRequested"])
        self.assertEqual(self.view(board, child)["state"], "executing")

    def test_host_completion_refuses_unfinished_owned_work_with_bounded_details(self):
        board = self.board()
        lead, claim = self.lead(board)
        children = [self.owned(board, claim, f"owned-{index}") for index in range(workflow.MAX_CHILD_VIEW + 2)]
        children[0], _ = self.finish_owned(board, children[0])
        self.assertEqual(children[0]["state"], "delivered")
        self.finish_turn(board, {"claim": claim})
        view = self.view(board, lead)
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["counts"]["ownedMicroTasks"], len(children))
        self.assertEqual(len(view["ownedMicroTasks"]), workflow.MAX_CHILD_VIEW)
        self.assertEqual(view["truncated"]["ownedMicroTasks"], 2)
        with self.assertRaises(BoardError) as caught:
            self.accept_run(board, view, artifact_id=self.output_artifact(board, lead))
        self.assertEqual(caught.exception.code, "NOT_READY")
        self.assertEqual(caught.exception.details["unfinishedOwnedMicroTaskTotal"], len(children))
        unfinished = caught.exception.details["unfinishedOwnedMicroTasks"]
        self.assertEqual(len(unfinished), workflow.MAX_CHILD_VIEW)
        self.assertEqual([item["runId"] for item in unfinished], [child["runId"] for child in children[:workflow.MAX_CHILD_VIEW]])
        self.assertEqual(self.view(board, lead)["state"], "awaiting-host")

    def test_authenticated_console_can_accept_lead_owned_delivery(self):
        board = self.board()
        _, claim = self.lead(board)
        child = self.owned(board, claim)
        delivered, _ = self.finish_owned(board, child)
        accepted = board.console_call("workflow_accept", {
            "runId": child["runId"], "artifactId": delivered["finalArtifactId"],
            "note": "Reviewed in console", "notRequired": "The fixture has no separate target",
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(self.view(board, child)["state"], "accepted")

    def test_delivered_lead_acceptance_rechecks_owned_settlement(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child)
        self.accept_run(board, child, credential=claim["agentCredential"])
        self.finish_turn(board, {"claim": claim})
        delivered = self.view(board, lead)
        self.assertEqual(delivered["state"], "delivered")
        # A persisted board can be observed after a child changes; acceptance must
        # inspect the current owned rows rather than rely on the Lead turn's result.
        with board.store.db.write() as db:
            input_row = db.execute("SELECT workspace_id,input_manifest_sha256 FROM workflow_artifacts"
                                   " WHERE run_id=? AND kind='input'", (child["runId"],)).fetchone()
            db.execute("UPDATE workflow_runs SET state='delivered',workspace_id=?,workspace_manifest_sha256=?"
                       " WHERE run_id=?", (*input_row, child["runId"]))
        with self.assertRaises(BoardError) as caught:
            self.accept_run(board, delivered)
        self.assertEqual(caught.exception.code, "NOT_READY")
        self.assertEqual(caught.exception.details["unfinishedOwnedMicroTaskTotal"], 1)
        self.assertEqual(caught.exception.details["unfinishedOwnedMicroTasks"],
                         [{"runId": child["runId"], "state": "delivered"}])

    def test_delivered_lead_accepts_after_every_owned_task_is_settled(self):
        board = self.board()
        lead, claim = self.lead(board)
        accepted = self.owned(board, claim, "accepted")
        accepted, _ = self.finish_owned(board, accepted)
        self.accept_run(board, accepted, credential=claim["agentCredential"])
        concluded = self.owned(board, claim, "concluded")
        concluded, _ = self.finish_owned(board, concluded)
        board.call("workflow_conclude", {"runId": concluded["runId"], "artifactId": concluded["finalArtifactId"],
                                         "note": "Reviewed and closed", "credential": claim["agentCredential"]})
        self.finish_turn(board, {"claim": claim})
        result = self.accept_run(board, self.view(board, lead))
        self.assertEqual(result["state"], "accepted")
