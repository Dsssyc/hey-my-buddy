"""Host review of a Lead cannot take over the Lead's independent owned work."""

from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.routing.test_decision import DecisionTestCase
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

    def test_host_control_cannot_target_a_lead_owned_micro_task(self):
        operations = {
            "workflow_accept": lambda child, lead: {
                "artifactId": child["finalArtifactId"], "note": "Host review",
                "notRequired": "The fixture has no integration target",
            },
            "workflow_conclude": lambda child, lead: {"note": "Host closes child"},
            "workflow_continue": lambda child, lead: {
                "commandId": "host-continue-child", "expectedRevision": lead["revision"],
                "input": "Continue the child", "helperPolicy": "keep",
            },
            "workflow_reclaim": lambda child, lead: {},
            "workflow_scope_amend": lambda child, lead: {
                "commandId": "host-amend-child", "expectedRevision": lead["revision"],
                "writeScope": ["review.txt"], "expectedScopeVersion": 1, "reason": "Review scope",
            },
        }
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child)
        for operation, fields in operations.items():
            with self.subTest(operation=operation):
                lead_view = self.view(board, lead)
                before = self.view(board, child)
                with self.assertRaises(BoardError) as caught:
                    board.call(operation, {
                        "runId": lead["runId"], "targetRunId": child["runId"],
                        **lead["control"], **fields(child, lead_view),
                    })
                self.assertEqual(caught.exception.code, "UNAUTHORIZED")
                self.assertEqual(self.view(board, child)["revision"], before["revision"])

    def test_operations_without_target_selector_keep_their_existing_boundary(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child, disposition="assistance")
        request_id = child["activeRequest"]["requestId"]
        for operation, fields in (
            ("workflow_decide", {"requestId": request_id, "commandId": "wrong-decision",
                                 "expectedRevision": child["revision"], "decision": "decline"}),
            ("workflow_cancel", {"reason": "Wrong owner"}),
            ("workflow_takeover", {"commandId": "wrong-takeover", "expectedOwnerGeneration": 1,
                                   "newHostId": "another-host"}),
        ):
            with self.subTest(operation=operation, selector="direct"), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": child["runId"], **lead["control"], **fields})
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
            with self.subTest(operation=operation, selector="unsupported"), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": lead["runId"], "targetRunId": child["runId"],
                                       **lead["control"], **fields})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.view(board, child)["state"], "awaiting-host")

    def test_host_takeover_keeps_an_owned_micro_tasks_running_routing_attempt(self):
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        board = self.board()
        DecisionTestCase.seed(self, board)
        lead, claim = self.lead(board)
        child = board.call("workflow_submit", {
            "requestId": "routed-owned", "task": "Select an executor", "cwd": str(self.workdir("routed-owned")),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "submissionToken": "r" * 32, "credential": claim["agentCredential"],
        })
        self.assertEqual(child["routing"]["status"], "queued")
        route_id = child["routing"]["taskId"]
        pass  # Admission is owned by the service.
        route_claim = self.claim(board, "router", claim_request_id="owned-route", run_id=route_id)["claim"]
        self.assertEqual(route_claim["attempt"]["taskId"], route_id)
        before = board.call("task_get", {"runId": route_id})["task"]
        taken = board.call("workflow_takeover", {
            "runId": lead["runId"], "commandId": "takeover-lead", "expectedOwnerGeneration": 1,
            "newHostId": "successor", **lead["control"],
        })
        self.assertEqual(taken["ownerGeneration"], 2)
        after = board.call("task_get", {"runId": route_id})["task"]
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["selectedAttempt"]["attemptId"], before["selectedAttempt"]["attemptId"])
        self.assertFalse(after["selectedAttempt"]["cancelRequested"])
        self.assertEqual(self.view(board, child)["routing"]["status"], "running")

    def test_host_completion_refuses_unfinished_owned_work_with_bounded_details(self):
        board = self.board()
        lead, claim = self.lead(board)
        children = [self.owned(board, claim, f"owned-{index}") for index in range(workflow.MAX_CHILD_VIEW + 2)]
        children[0], _ = self.finish_owned(board, children[0])
        self.assertEqual(children[0]["state"], "delivered")
        self.finish_turn(board, {"claim": claim})
        view = self.view(board, lead)
        self.assertEqual(view["state"], "awaiting-host")
        with self.assertRaises(BoardError) as caught:
            self.accept_run(board, view, artifact_id=self.output_artifact(board, lead))
        self.assertEqual(caught.exception.code, "NOT_READY")
        self.assertEqual(caught.exception.details["unfinishedOwnedMicroTaskTotal"], len(children))
        unfinished = caught.exception.details["unfinishedOwnedMicroTasks"]
        self.assertEqual(len(unfinished), workflow.MAX_CHILD_VIEW)
        self.assertEqual([item["runId"] for item in unfinished], [child["runId"] for child in children[:workflow.MAX_CHILD_VIEW]])
        self.assertEqual(self.view(board, lead)["state"], "awaiting-host")

    def test_delivered_lead_acceptance_rechecks_owned_settlement(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        child, _ = self.finish_owned(board, child)
        self.accept_run(board, child)
        self.finish_turn(board, {"claim": claim})
        delivered = self.view(board, lead)
        self.assertEqual(delivered["state"], "delivered")
        # A persisted board can be observed after a child changes; acceptance must
        # inspect the current owned rows rather than rely on the Lead turn's result.
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='delivered' WHERE run_id=?", (child["runId"],))
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
        self.accept_run(board, accepted)
        concluded = self.owned(board, claim, "concluded")
        self.finish_owned(board, concluded)
        board.call("workflow_conclude", {"runId": concluded["runId"], "note": "Reviewed and closed",
                                         **self.control(concluded)})
        self.finish_turn(board, {"claim": claim})
        result = self.accept_run(board, self.view(board, lead))
        self.assertEqual(result["state"], "accepted")
