"""Workflow receipt identities replay against an advancing private board."""
from __future__ import annotations

import json
from pathlib import Path

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks import test_scope_recovery as scope_fixtures
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks.command_receipts import replay_response, stored_response
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.errors import BoardError


def replay(board, kind, response):
    stored = stored_response(kind, response)
    with board.store.db.read() as connection:
        committed = [json.loads(row[0]) for row in connection.execute(
            "SELECT response_json FROM commands WHERE kind=?", (kind,)
        )]
        if stored not in committed:
            raise AssertionError(f"The real {kind} producer did not store its submission identity")
        stored = next(item for item in committed if item == stored)
        rebuilt = replay_response(board.store.workflow, connection, kind, stored)
        current = board.store.workflow.compact(
            connection, board.store.workflow._run_row(connection, stored["runId"])
        )
    return stored, rebuilt, current


class WorkflowReceiptTests(WorkflowTestCase):
    def test_deleted_receipt_checks_identity_without_loading_response(self):
        board = self.board()
        params = {"requestId": "deleted-command", "adapter": "command", "task": "Record a command",
                  "cwd": str(self.workdir()), "argv": ["/bin/true"]}
        board.store.task_submit(params, command_id="deleted-command-id")
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions"
                " (deletion_id,target_kind,target_id,state,started_at,deleted_at,micro_task_count)"
                " VALUES('deletion-1','macro-task','macro-old','deleted',?,?,1)",
                (board.store.now(), board.store.now()),
            )
            connection.execute(
                "UPDATE commands SET response_json='{}',deletion_id='deletion-1' WHERE command_id='deleted-command-id'"
            )
        self.assertEqual(board.store.task_submit(params, command_id="deleted-command-id"),
                         {"commandId": "deleted-command-id", "deleted": True})
        with self.assertRaises(BoardError) as caught:
            board.store.task_submit({**params, "task": "Different input"}, command_id="deleted-command-id")
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_decision_continuation_takeover_and_cancel_keep_commits_with_current_view(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="attention")
        boundary = board.call("workflow_get", {"runId": submitted["runId"]})
        decided = self.decide(board, boundary, boundary["activeRequest"]["requestId"],
                              decision="decline", autoContinue=False)
        continued = self.continue_run(board, decided, command_id="receipt-continue")
        taken = board.call("workflow_takeover", {
            "runId": submitted["runId"], "commandId": "receipt-takeover",
            "expectedOwnerGeneration": continued["ownerGeneration"], "newHostId": "host-2",
            **self.control(submitted),
        })
        self.controls[submitted["runId"]] = taken["control"]
        cancelled = board.call("workflow_cancel", {
            "runId": submitted["runId"], "commandId": "receipt-cancel",
            "reason": "Host ended this run", **self.control(submitted),
        })
        for kind, original, facts in (
            ("workflow.decide", decided, ("requestId", "decision")),
            ("workflow.continue", continued, ("continuationId", "inputSha256", "helperPolicy", "targetRunId")),
            ("workflow.takeover", taken, ("previousOwnerGeneration",)),
            ("workflow.cancel", cancelled, ("cancelled", "note")),
        ):
            with self.subTest(kind=kind):
                stored, rebuilt, current = replay(board, kind, original)
                self.assertEqual(rebuilt["revision"], current["revision"])
                self.assertEqual(rebuilt["state"], current["state"])
                for key in facts:
                    self.assertEqual(rebuilt[key], original[key])
                self.assertNotIn("microTask", stored)
        _, takeover_replay, _ = replay(board, "workflow.takeover", taken)
        self.assertEqual(takeover_replay["control"], taken["control"])
        self.assertEqual(takeover_replay["ownerGeneration"], taken["ownerGeneration"])
        takeover_stored = stored_response("workflow.takeover", taken)
        self.assertEqual(takeover_stored["control"], {"hostId": taken["control"]["hostId"]})

    def test_review_and_scope_replay_authoritative_records(self):
        board = self.board()
        submitted = self.submit(board, request_id="receipt-accepted")
        self.finish_turn(board, self.claim(board, claim_request_id="accepted-claim"))
        accepted_id = submitted["runId"]
        delivered = board.call("workflow_get", {"runId": accepted_id})
        amended = board.store.workflow.scope_amend({
            "runId": accepted_id, "commandId": "receipt-scope", "expectedRevision": delivered["revision"],
            "expectedScopeVersion": 1, "writeScope": ["docs"], "reason": "Authorize the next stage",
            **self.control(delivered),
        })
        accepted = self.accept_run(board, amended, keepCheckout=True)
        for kind, original, facts in (
            ("workflow.scope_amend", amended, ("scopeVersion", "writeScope", "targetRunId")),
            ("workflow.accept", accepted, ("verdict", "integrationId", "integration")),
        ):
            with self.subTest(kind=kind):
                stored, rebuilt, current = replay(board, kind, original)
                self.assertEqual(rebuilt["revision"], current["revision"])
                self.assertEqual(rebuilt["state"], current["state"])
                for key in facts:
                    self.assertEqual(rebuilt[key], original[key])
                self.assertNotIn("microTask", stored)

    def test_conclusion_keeps_review_identity_after_takeover(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        concluded = board.call("workflow_conclude", {
            "runId": delivered["runId"], "note": "Close with the recorded output",
            **self.control(delivered),
        })
        board.call("workflow_takeover", {
            "runId": delivered["runId"], "commandId": "after-conclusion",
            "expectedOwnerGeneration": concluded["ownerGeneration"], "newHostId": "host-2",
            **self.control(delivered),
        })
        stored, rebuilt, current = replay(board, "workflow.conclude", concluded)
        self.assertEqual(rebuilt["revision"], current["revision"])
        self.assertEqual(rebuilt["conclusion"], concluded["conclusion"])
        self.assertEqual(rebuilt["partialArtifactId"], concluded["partialArtifactId"])
        self.assertEqual(set(stored), {"runId", "conclusionId"})

    def test_small_answers_remain_exact(self):
        board = self.board()
        params = {"requestId": "small-receipt", "adapter": "command", "task": "Record a command",
                  "cwd": str(self.workdir()), "argv": ["/bin/true"]}
        answer = board.store.task_submit(params, command_id="small-submit")
        board.client().cancel(runId=answer["task"]["runId"])
        self.assertEqual(board.client().get(runId=answer["task"]["runId"])["state"], "cancelled")
        self.assertEqual(board.store.task_submit(params, command_id="small-submit"), answer)
        with self.assertRaises(BoardError) as caught:
            board.store.task_submit({**params, "task": "A different request"}, command_id="small-submit")
        self.assertEqual(caught.exception.code, "CONFLICT")


class ResolutionReceiptTests(RealWorkspaceTestCase):
    # Share the established real-Git scenario without inheriting its full suite.
    register = scope_fixtures.ScopeRecoveryTestCase.register
    micro_task_submit = scope_fixtures.ScopeRecoveryTestCase.micro_task_submit
    control = scope_fixtures.ScopeRecoveryTestCase.control
    claim = scope_fixtures.ScopeRecoveryTestCase.claim
    scope_failure = scope_fixtures.ScopeRecoveryTestCase.scope_failure
    turn_record = scope_fixtures.ScopeRecoveryTestCase.turn_record
    finish_turn = scope_fixtures.ScopeRecoveryTestCase.finish_turn
    view = scope_fixtures.ScopeRecoveryTestCase.view
    resolve = scope_fixtures.ScopeRecoveryTestCase.resolve
    scoped_failure = scope_fixtures.ScopeRecoveryTestCase.scoped_failure
    native_scope_failure = scope_fixtures.ScopeRecoveryTestCase.native_scope_failure
    verified_target = scope_fixtures.ScopeRecoveryTestCase.verified_target

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = "w-scope"
        previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(setattr, workflow_module, "_workspace_module", previous_workspace)

    def test_partial_resolution_replay_keeps_its_outcome_after_followup(self):
        board, submitted, _claimed, _manifest, _checkout = self.scoped_failure()
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        partial = self.resolve(board, view, conflict, "restore", paths=["outside.txt"],
                               reason="Restore one path")
        current = self.view(board, submitted["runId"])
        completed = self.resolve(board, current, current["workspaceConflicts"][0], "restore",
                                 paths=["tracked.txt"], fingerprint=partial["resolvedFingerprint"],
                                 command_id="receipt-resolution-2", reason="Restore remaining path")
        self.assertEqual(completed["resolutionState"], "restored")
        stored, rebuilt, latest = replay(board, "workflow.workspace_resolve", partial)
        self.assertEqual(rebuilt["resolutionState"], "open")
        self.assertEqual(rebuilt["remainingPaths"], ["tracked.txt"])
        self.assertEqual(rebuilt["resolvedFingerprint"], partial["resolvedFingerprint"])
        self.assertEqual(rebuilt["revision"], latest["revision"])
        self.assertNotIn("workspaceConflicts", stored)
        _, completed_replay, latest = replay(board, "workflow.workspace_resolve", completed)
        self.assertEqual(completed_replay["artifactId"], completed["artifactId"])
        self.assertEqual(completed_replay["changedPaths"], completed["changedPaths"])
        self.assertEqual(completed_replay["revision"], latest["revision"])

    def test_acceptance_replay_preserves_host_resolution_delivery(self):
        board, submitted, _claimed, _manifest, _checkout = self.native_scope_failure(
            request_id="receipt-native-resolution")
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                command_id="receipt-native-restore", reason="restore refused paths")
        self.assertTrue(resolved["delivered"])
        target = self.verified_target(resolved["artifact"])
        request = {
            "runId": submitted["runId"], "artifactId": resolved["artifactId"],
            "note": "reviewed the Host-resolved delivery",
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"], "keepCheckout": True,
            **self.control(resolved),
        }
        first = board.call("workflow_accept", request)
        self.assertEqual(first["state"], "accepted")
        self.assertEqual(first["resolutionDelivery"]["conflictId"], conflict["conflictId"])
        self.assertEqual(first["resolutionDelivery"]["artifactId"], resolved["artifactId"])
        stored, rebuilt, _current = replay(board, "workflow.accept", first)
        self.assertEqual(stored["resolutionConflictId"], conflict["conflictId"])
        self.assertEqual(rebuilt["resolutionDelivery"], first["resolutionDelivery"])

        # Treat the committed first response as lost and retry the identical request.
        replayed = board.call("workflow_accept", request)
        self.assertTrue(replayed["duplicate"])
        self.assertEqual(replayed["integrationId"], first["integrationId"])
        self.assertEqual(replayed["resolutionDelivery"], first["resolutionDelivery"])

    def test_cleanup_plan_replay_reads_the_applied_journal(self):
        board = self.board()
        submitted = self.micro_task_submit(board, request_id="receipt-cleanup", scope=(".",), kind="worktree")
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("accepted output\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, run_id)
        accepted = board.call("workflow_accept", {
            "runId": run_id, "artifactId": delivered["finalArtifactId"],
            "note": "Reviewed the output", "notRequired": "No separate target in this fixture",
            "keepCheckout": True, **self.control(delivered),
        })
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "commandId": "receipt-plan", "expectedRevision": accepted["revision"],
            **self.control(accepted),
        })
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id, "planId": planned["plan"]["planId"], "commandId": "receipt-apply",
            "expectedRevision": planned["targetRevision"], "confirmPath": planned["plan"]["path"],
            **self.control(accepted),
        })
        self.assertFalse(checkout.exists())
        for kind, original in (("workflow.cleanup_plan", planned), ("workflow.cleanup_apply", applied)):
            with self.subTest(kind=kind):
                stored, rebuilt, current = replay(board, kind, original)
                self.assertEqual(rebuilt["revision"], current["revision"])
                self.assertEqual(rebuilt["plan"], applied["plan"])
                self.assertEqual(stored["planId"], planned["plan"]["planId"])
                self.assertNotIn("workspace", stored)
        _, applied_replay, _ = replay(board, "workflow.cleanup_apply", applied)
        self.assertTrue(applied_replay["removed"])
        self.assertEqual(applied_replay["retention"], applied["retention"])
