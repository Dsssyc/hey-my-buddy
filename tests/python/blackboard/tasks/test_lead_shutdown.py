"""Lead shutdown and Host conclusion preserve the whole owned lineage."""
from unittest.mock import patch

from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas


class LeadShutdownTests(WorkflowTestCase):
    # Reuse the fixture builders without inheriting or collecting their tests.
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned

    def allow_parallel_claims(self, board):
        with board.store.db.write() as db:
            db.execute(
                "INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                " VALUES(?,?,?,8)",
                (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]),
            )

    def get(self, board, run):
        return board.call("workflow_get", {"runId": run["runId"]})

    def attempt(self, board, run):
        return board.call("task_get", {"runId": run["runId"]})["task"]["selectedAttempt"]

    def conclude(self, board, run, note="Host closes Lead work"):
        return board.call("workflow_conclude", {"runId": run["runId"], "note": note, **self.control(run)})

    def test_host_stop_fences_running_lead_owned_run_and_nested_helper(self):
        board = self.board(max_concurrent=8)
        self.allow_parallel_claims(board)
        lead, lead_claim = self.lead(board)
        owned = self.owned(board, lead_claim)
        self.register(board, "w2")
        owned_claim = self.claim(board, "w2", claim_request_id="owned-running", run_id=owned["runId"])
        waiting = self.owned(board, lead_claim, "owned-with-helper")
        waiting_view, _ = self.finish_owned(board, waiting, "w3", disposition="assistance")
        decided = self.decide(board, waiting_view, waiting_view["activeRequest"]["requestId"],
                              helpers=[{"requestId": "nested-helper", "task": "Help owned work",
                                        "cwd": str(self.workdir("nested-helper")),
                                        "executionWorkspace": {"kind": "existing", "access": "write"}}],
                              autoContinue=False, credential=lead_claim["agentCredential"])
        helper = self.get(board, {"runId": decided["children"][0]["taskId"]})
        self.register(board, "w4")
        helper_claim = self.claim(board, "w4", claim_request_id="helper-running", run_id=helper["runId"])

        stopped = board.call("workflow_cancel", {"runId": lead["runId"],
                                                 'note': "Host stops Lead", **self.control(lead)})
        self.assertEqual(stopped["state"], "cancelled")
        for run in (lead, owned, waiting, helper):
            self.assertEqual(self.get(board, run)["state"], "cancelled")
        for run in (lead, owned, helper):
            attempt = self.attempt(board, run)
            self.assertEqual(attempt["stopInitiator"], "host")
            self.assertTrue(attempt["cancelRequested"])
        summary = self.get(board, lead)["shutdown"]
        self.assertFalse(summary["selfConfirmed"])
        self.assertFalse(summary["descendantsConfirmed"])
        self.assertEqual(set(summary["unconfirmedRunIds"]),
                         {lead["runId"], owned["runId"], helper["runId"]})
        with self.assertRaises(BoardError) as caught:
            self.owned(board, lead_claim, "after-stop")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")

        for claim, worker in (({"claim": lead_claim}, "w1"), (owned_claim, "w2"), (helper_claim, "w4")):
            self.finish_turn(board, claim, worker_id=worker, runner_status="cancelled", seal=False)
        summary = self.get(board, lead)["shutdown"]
        self.assertTrue(summary["selfConfirmed"])
        self.assertTrue(summary["descendantsConfirmed"])
        self.assertEqual(summary["unconfirmedCount"], 0)

    def test_stop_waiting_owned_lead_and_revoke_its_old_credential(self):
        board = self.board(max_concurrent=4)
        self.allow_parallel_claims(board)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.register(board, "w2")
        child_claim = self.claim(board, "w2", claim_request_id="waiting-child", run_id=child["runId"])
        waiting = {"disposition": "waiting", "summary": "Waiting for owned work", "remaining": [],
                   "decisions": [], "artifacts": [], "request": None}
        self.finish_turn(board, {"claim": claim}, outcome=waiting)
        self.assertEqual(self.get(board, lead)["state"], "waiting-owned")
        board.call("workflow_cancel", {"runId": lead["runId"], **self.control(lead)})
        self.assertEqual(self.get(board, child)["state"], "cancelled")
        self.assertEqual(self.attempt(board, child)["stopInitiator"], "host")
        self.assertFalse(self.get(board, lead)["shutdown"]["descendantsConfirmed"])
        with self.assertRaises(BoardError) as caught:
            self.owned(board, claim, "old-turn-after-stop")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.finish_turn(board, child_claim, worker_id="w2", runner_status="cancelled", seal=False)
        self.assertTrue(self.get(board, lead)["shutdown"]["descendantsConfirmed"])

    def test_new_active_credential_is_revoked_and_prepared_submit_cannot_commit(self):
        board = self.board()
        lead, first = self.lead(board)
        self.finish_turn(board, {"claim": first}, disposition="attention")
        self.continue_run(board, self.get(board, lead))
        next_claim = self.claim(board, claim_request_id="lead-wrap-up", run_id=lead["runId"])["claim"]
        self.assertNotEqual(first["agentCredential"], next_claim["agentCredential"])
        validate = board.store.workflow._validated_buddy

        def stop_during_preparation(spec):
            result = validate(spec)
            board.call("workflow_cancel", {"runId": lead["runId"], **self.control(lead)})
            return result

        with patch.object(board.store.workflow, "_validated_buddy", side_effect=stop_during_preparation):
            with self.assertRaises(BoardError) as caught:
                self.owned(board, next_claim, "prepared-before-stop")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT task_id FROM tasks WHERE request_id='prepared-before-stop'").fetchone())
        with self.assertRaises(BoardError) as caught:
            self.owned(board, next_claim, "new-credential-after-stop")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")

    def test_lead_failure_does_not_cancel_owned_work(self):
        board = self.board(max_concurrent=4)
        self.allow_parallel_claims(board)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.register(board, "w2")
        child_claim = self.claim(board, "w2", claim_request_id="surviving-child", run_id=child["runId"])
        self.finish_turn(board, {"claim": claim}, runner_status="failed", exit_code=1, seal=False)
        self.assertEqual(self.get(board, lead)["state"], "failed")
        self.assertEqual(self.get(board, child)["state"], "executing")
        self.assertFalse(self.attempt(board, child)["cancelRequested"])
        self.finish_turn(board, child_claim, worker_id="w2")
        self.assertEqual(self.get(board, child)["state"], "delivered")

    def test_wrap_up_cannot_submit_with_the_original_turn_credential_after_stop_started(self):
        board = self.board()
        lead, claim = self.lead(board)
        board.call("workflow_cancel", {"runId": lead["runId"], **self.control(lead)})
        self.assertIsNotNone(self.attempt(board, lead)["graceDeadline"])
        with self.assertRaises(BoardError) as caught:
            self.owned(board, claim, "wrap-up-submission")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT task_id FROM tasks WHERE request_id='wrap-up-submission'").fetchone())

    def test_service_stop_uses_lead_root_even_when_only_owned_work_is_running(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_turn(board, {"claim": claim}, outcome={
            "disposition": "waiting", "summary": "Waiting", "remaining": [], "decisions": [], "artifacts": [], "request": None})
        self.register(board, "w2")
        self.claim(board, "w2", claim_request_id="child-running", run_id=child["runId"])
        board.store.workflow.cancel_for_service_stop("service is stopping")
        self.assertEqual(self.get(board, lead)["state"], "cancelled")
        self.assertEqual(self.get(board, child)["state"], "cancelled")
        self.assertEqual(self.attempt(board, child)["stopInitiator"], "service")

    def test_maximum_host_note_does_not_overflow_auto_conclusion_reason(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_owned(board, child)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        result = self.conclude(board, lead, note="x" * schemas.MAX_NOTE_BYTES)
        self.assertEqual(result["state"], "concluded")
        self.assertEqual(self.get(board, child)["state"], "concluded")

    def test_known_lead_conflict_refuses_conclusion_before_owned_work_is_concluded(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_owned(board, child)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        coordinator = board.store.workflow
        real_conflict = coordinator._open_conflict
        with patch.object(coordinator, "_open_conflict", side_effect=lambda db, run_id: (
                {"conflict_id": "open-lead-conflict", "evidence_json": '{"blockingPaths":["outside.txt"]}'}
                if run_id == lead["runId"] else real_conflict(db, run_id))):
            with self.assertRaises(BoardError) as caught:
                self.conclude(board, lead)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(self.get(board, child)["state"], "delivered")

    def test_lead_seal_failure_does_not_commit_owned_conclusions(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_owned(board, child)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        original = self.workspace.host_seal
        def seal(state_dir, manifest, task_id, attempt_id, **kwargs):
            if task_id == lead["runId"]:
                raise BoardError("WORKSPACE_INVALID", "Lead seal failed")
            return original(state_dir, manifest, task_id, attempt_id, **kwargs)
        with patch.object(self.workspace, "host_seal", side_effect=seal):
            with self.assertRaises(BoardError) as caught:
                self.conclude(board, lead)
        self.assertEqual(caught.exception.code, "WORKSPACE_INVALID")
        self.assertEqual(self.get(board, child)["state"], "delivered")
        self.assertIsNone(self.get(board, child)["hostConclusion"])

    def test_lead_conclusion_event_failure_rolls_back_owned_conclusions_in_the_same_transaction(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_owned(board, child)
        self.finish_turn(board, {"claim": claim}, disposition="attention")
        original = board.store._append_event
        def event(db, kind, **kwargs):
            if kind == "workflow.host_concluded" and kwargs.get("task_id") == lead["runId"]:
                raise BoardError("CONFLICT", "Conclusion event failed")
            return original(db, kind, **kwargs)
        with patch.object(board.store, "_append_event", side_effect=event):
            with self.assertRaises(BoardError) as caught:
                self.conclude(board, lead)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(self.get(board, child)["state"], "delivered")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='workflow.host_concluded'").fetchone()[0], 0)

    def test_conclude_stops_running_lineage_and_requires_confirmed_shutdown(self):
        board = self.board(max_concurrent=4)
        self.allow_parallel_claims(board)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.register(board, "w2")
        child_claim = self.claim(board, "w2", claim_request_id="conclude-child", run_id=child["runId"])
        with self.assertRaises(BoardError) as caught:
            self.conclude(board, lead)
        self.assertEqual(caught.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(set(caught.exception.details["unconfirmedRunIds"]), {lead["runId"], child["runId"]})
        self.assertEqual(self.get(board, child)["state"], "cancelled")
        self.assertEqual(self.attempt(board, child)["stopInitiator"], "host")
        self.finish_turn(board, {"claim": claim}, runner_status="cancelled", seal=False)
        with self.assertRaises(BoardError) as caught:
            self.conclude(board, lead)
        self.assertEqual(caught.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(caught.exception.details["unconfirmedRunIds"], [child["runId"]])
        self.finish_turn(board, child_claim, worker_id="w2", runner_status="cancelled", seal=False)
        concluded = self.conclude(board, lead)
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(self.get(board, child)["state"], "concluded")
        self.assertIsNone(self.get(board, child)["hostConclusion"]["note"])
        self.assertEqual(self.get(board, child)["hostConclusion"]["reason"], {"code": "lead.owned-concluded", "params": {"leadRunId": lead["runId"]}})

    def test_lead_conclusion_records_all_unaccepted_outcomes_and_preserves_acceptance(self):
        board = self.board(max_concurrent=8)
        self.allow_parallel_claims(board)
        lead, claim = self.lead(board)
        cases = {}
        for name, finish in (("delivered", {}), ("awaiting", {"disposition": "attention"}),
                             ("failed", {"runner_status": "failed", "exit_code": 1, "seal": False})):
            child = self.owned(board, claim, name)
            view, _ = self.finish_owned(board, child, worker="w2", **finish)
            cases[name] = view
        cancelled = self.owned(board, claim, "cancelled")
        board.call("workflow_cancel", {"runId": cancelled["runId"], "credential": claim["agentCredential"]})
        cases["cancelled"] = cancelled
        accepted_child = self.owned(board, claim, "accepted")
        accepted_view, _ = self.finish_owned(board, accepted_child, worker="w2")
        board.call("workflow_accept", {"runId": accepted_child["runId"],
                                       "artifactId": accepted_view["finalArtifactId"],
                                       "note": "Reviewed owned output", "notRequired": "Fixture has no integration target",
                                       "credential": claim["agentCredential"]})

        with self.assertRaises(BoardError) as caught:
            self.conclude(board, lead, "Lead has no further work")
        self.assertEqual(caught.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.finish_turn(board, {"claim": claim}, runner_status="cancelled", seal=False)
        self.conclude(board, lead, "Lead has no further work")
        for name, child in cases.items():
            with self.subTest(outcome=name):
                view = self.get(board, child)
                self.assertEqual(view["state"], "concluded")
                self.assertIsNone(view["hostConclusion"]["note"])
                self.assertEqual(view["hostConclusion"]["reason"], {"code": "lead.owned-concluded", "params": {"leadRunId": lead["runId"]}})
                self.assertNotIn("not-accepted", view["cleanup"]["reasons"])
                self.assertEqual(view["cleanup"]["evidence"]["acceptance"]["state"], "concluded")
        accepted = self.get(board, accepted_child)
        self.assertEqual(accepted["state"], "accepted")
        self.assertIsNone(accepted["hostConclusion"])

    def test_structured_selectors_and_foreign_control_scope_are_rejected(self):
        board = self.board(max_concurrent=4)
        self.allow_parallel_claims(board)
        lead, claim = self.lead(board)
        owned = self.owned(board, claim)
        foreign = self.submit(board, request_id="foreign-stop-scope", cwd=str(self.workdir("foreign-stop-scope")))
        for field in ("runId", "taskId"):
            for value in ({"value": owned["runId"]}, [owned["runId"]]):
                with self.subTest(field=field, value=value), self.assertRaises(BoardError) as caught:
                    board.call("workflow_get", {field: value, "credential": claim["agentCredential"]})
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_cancel", {"runId": foreign["runId"], **self.control(foreign),
                                           "credential": claim["agentCredential"]})
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual(self.get(board, foreign)["state"], "executing")


def load_tests(loader, _tests, _pattern):
    return loader.loadTestsFromTestCase(LeadShutdownTests)
