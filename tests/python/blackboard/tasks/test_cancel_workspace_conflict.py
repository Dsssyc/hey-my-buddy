"""Cancellation and end paths never deadlock an open workspace scope conflict."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admission import admit_board
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from support import enable_fixture_buddy


class CancelWorkspaceConflictTests(RealWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(patch.object(workflow_module, "_workspace_module", workspace))

    def _board(self):
        board = self.board(max_concurrent=4)
        enable_fixture_buddy(board.store, BUDDY)
        with board.store.db.write() as db:
            db.execute(
                "INSERT INTO model_services(harness,provider,model,concurrency_limit)"
                " VALUES(?,?,?,8) ON CONFLICT(harness,provider,model)"
                " DO UPDATE SET concurrency_limit=8",
                (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]),
            )
        return board

    def _submit(self, board, title: str, *, macro_task_id: str = "standalone",
                checkout: str = "worktree", scope=("src",), credential: str | None = None,
                delegation: bool = False) -> dict:
        params = {
            "cwd": str(self.repo), "title": title, "task": "Scoped workspace task",
            "buddy": " / ".join(BUDDY.values()), "checkout": checkout,
            "access": "full" if delegation else "write",
            "writeScope": ["."] if delegation else list(scope),
            "inputs": [], "expectedSeconds": 1800,
        }
        if delegation:
            params["accessNote"] = "Lead review"
            params["delegationAuthority"] = True
        if credential is not None:
            params["credential"] = credential
        else:
            params["macroTaskId"] = macro_task_id
        return board.call("workflow_submit", params)

    def _open_scope_conflict(self, board, run_id: str, *, worker_id: str = "w-conflict",
                             nonce: str = "c" * 16) -> tuple[dict, Path]:
        claimed = admit_board(board, {
            "workerId": worker_id, "claimRequestId": f"claim-{run_id}",
            "nonce": nonce, "runId": run_id,
        })["claim"]
        self.assertIsNotNone(claimed)
        manifest = turn_manifest(self.directory, claimed["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "outside.txt").write_text("out-of-scope edit\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            workspace.seal(self.directory, manifest, run_id, claimed["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        seal_error = "WORKSPACE_SCOPE_VIOLATION: outside write scope"
        board.call("execution_result", {
            "attemptId": claimed["attempt"]["attemptId"],
            "generation": claimed["attempt"]["generation"],
            "nonce": nonce, "status": "failed", "shutdownConfirmed": True, "exitCode": 1,
            "error": seal_error,
            "result": {"status": "failed", "workspaceSealError": seal_error,
                       "turnResultPath": "/tmp/turn-result.json"},
        })
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["reason"]["code"], "workspace-scope-violation")
        self.assertEqual(view["workspaceConflicts"][0]["state"], "open")
        return view, checkout

    def test_direct_cancel_refuses_stopped_run_with_open_conflict_and_preserves_resolution(self):
        board = self._board()
        submitted = self._submit(board, "direct-cancel-conflict")
        run_id = submitted["runId"]
        view, checkout = self._open_scope_conflict(board, run_id)
        request_id = view["activeRequest"]["requestId"]
        conflict = view["workspaceConflicts"][0]

        self.assertEqual(view["actions"], ["continue", "get"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_cancel", {"runId": run_id, "note": "Host tried cancel"})
        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertEqual(raised.exception.details["conflictId"], conflict["conflictId"])

        after = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(after["state"], "awaiting-host")
        self.assertEqual(after["activeRequest"]["requestId"], request_id)
        self.assertEqual(after["actions"], ["continue", "get"])

        abandoned = board.call("workflow_continue", {
            "runId": run_id, "requestId": request_id, "resolution": "abandon",
            "observedFingerprint": conflict["observedFingerprint"], "note": "Discard out-of-scope site",
        })
        self.assertEqual(abandoned["resolutionState"], "abandoned")
        concluded = board.call("workflow_conclude", {"runId": run_id, "note": "Conclude after abandon"})
        self.assertEqual(concluded["state"], "concluded")
        self.assertTrue(concluded["reclaim"]["removed"])
        self.assertFalse(checkout.exists())

    def test_end_macro_task_concludes_clean_runs_without_deadlocking_conflict_run(self):
        board = self._board()
        macro = board.call("workflow_create", {
            "cwd": str(self.repo), "title": "Mixed end macro task", "goal": "Verify end preserves conflict",
        })
        conflict_run = self._submit(board, "conflict-member", macro_task_id=macro["macroTaskId"])
        clean_run = self._submit(board, "clean-member", macro_task_id=macro["macroTaskId"])
        conflict_view, conflict_checkout = self._open_scope_conflict(board, conflict_run["runId"])
        request_id = conflict_view["activeRequest"]["requestId"]
        conflict = conflict_view["workspaceConflicts"][0]

        board.store.workflow.end_macro_task(
            {"macroTaskId": macro["macroTaskId"], "commandId": "cmd-end-mixed", "note": "End macro task"},
            console_authority={"sessionId": "browser"},
        )

        self.assertEqual(board.call("workflow_get", {"runId": clean_run["runId"]})["state"], "concluded")
        after = board.call("workflow_get", {"runId": conflict_run["runId"]})
        self.assertEqual(after["state"], "awaiting-host")
        self.assertEqual(after["activeRequest"]["requestId"], request_id)
        self.assertEqual(after["workspaceConflicts"][0]["state"], "open")

        board.call("workflow_continue", {
            "runId": conflict_run["runId"], "requestId": request_id, "resolution": "abandon",
            "observedFingerprint": conflict["observedFingerprint"], "note": "Resolve after macro end",
        })
        concluded = board.call("workflow_conclude", {
            "runId": conflict_run["runId"], "note": "Conclude conflict member",
        })
        self.assertEqual(concluded["state"], "concluded")
        self.assertTrue(concluded["reclaim"]["removed"])
        self.assertFalse(conflict_checkout.exists())

    def test_cancelling_running_lead_stops_lead_and_keeps_child_conflict_resolvable_by_console(self):
        board = self._board()
        lead = self._submit(board, "running-lead", checkout="existing", delegation=True)
        lead_claim = admit_board(board, {
            "workerId": "lead-worker", "claimRequestId": "claim-lead",
            "nonce": "l" * 16, "runId": lead["runId"],
        })["claim"]
        child = self._submit(board, "conflict-child", credential=lead_claim["agentCredential"])
        child_view, child_checkout = self._open_scope_conflict(board, child["runId"])
        request_id = child_view["activeRequest"]["requestId"]
        conflict = child_view["workspaceConflicts"][0]

        # Stopping a live Lead must always succeed even when a child has an open conflict.
        stopped = board.call("workflow_cancel", {"runId": lead["runId"], "note": "Stop live Lead"})
        self.assertEqual(stopped["state"], "cancelled")
        board.call("execution_result", {
            "attemptId": lead_claim["attempt"]["attemptId"],
            "generation": lead_claim["attempt"]["generation"],
            "nonce": "l" * 16, "status": "cancelled", "shutdownConfirmed": True,
            "result": {"status": "cancelled"},
        })

        child_after_stop = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(child_after_stop["state"], "awaiting-host")
        self.assertEqual(child_after_stop["activeRequest"]["requestId"], request_id)
        self.assertEqual(child_after_stop["workspaceConflicts"][0]["state"], "open")

        # Console resolves the child's conflict beneath the cancelled Lead without leaving stale state.
        board.store.workflow.continue_run({
            "runId": child["runId"], "requestId": request_id, "resolution": "abandon",
            "observedFingerprint": conflict["observedFingerprint"], "note": "Console abandons child site",
        }, console_authority={"sessionId": "browser"})

        child_resolved = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(child_resolved["state"], "cancelled")
        self.assertIsNone(child_resolved["activeRequest"])
        self.assertEqual(child_resolved["workspaceConflicts"][0]["state"], "abandoned")

        # Lead tree can now conclude cleanly and reclaim the child worktree.
        concluded = board.call("workflow_conclude", {"runId": lead["runId"], "note": "Conclude stopped Lead"})
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(board.call("workflow_get", {"runId": child["runId"]})["state"], "concluded")
        self.assertFalse(child_checkout.exists())

    def test_service_stop_preserves_open_child_conflict_while_stopping_running_lead(self):
        board = self._board()
        lead = self._submit(board, "service-stop-lead", checkout="existing", delegation=True)
        lead_claim = admit_board(board, {
            "workerId": "lead-worker", "claimRequestId": "claim-service-lead",
            "nonce": "s" * 16, "runId": lead["runId"],
        })["claim"]
        child = self._submit(board, "service-stop-child", credential=lead_claim["agentCredential"])
        child_view, _checkout = self._open_scope_conflict(board, child["runId"])
        request_id = child_view["activeRequest"]["requestId"]

        board.store.workflow.cancel_for_service_stop("service shutting down")

        self.assertEqual(board.call("workflow_get", {"runId": lead["runId"]})["state"], "cancelled")
        child_after = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(child_after["state"], "awaiting-host")
        self.assertEqual(child_after["activeRequest"]["requestId"], request_id)
        self.assertEqual(child_after["workspaceConflicts"][0]["state"], "open")

    def test_existing_checkout_reservation_stays_held_across_cancel_until_conflict_resolved(self):
        board = self._board()
        macro = board.call("workflow_create", {
            "cwd": str(self.repo), "title": "Existing conflict reservation", "goal": "Protect dirty checkout",
        })
        run = self._submit(board, "existing-conflict", macro_task_id=macro["macroTaskId"], checkout="existing")
        view, _checkout = self._open_scope_conflict(board, run["runId"])

        # end_macro_task must neither cancel the conflict request nor release the existing checkout reservation.
        board.store.workflow.end_macro_task(
            {"macroTaskId": macro["macroTaskId"], "commandId": "cmd-end-existing", "note": "End macro task"},
            console_authority={"sessionId": "browser"},
        )
        with board.store.db.read() as connection:
            held = connection.execute(
                "SELECT state FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC LIMIT 1",
                (run["runId"],),
            ).fetchone()
        self.assertEqual(held["state"], "held")
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["activeRequest"]["requestId"],
                         view["activeRequest"]["requestId"])

    def test_partial_restore_beneath_cancelled_lead_advances_boundary_then_cancels_without_delivering(self):
        board = self._board()
        lead = self._submit(board, "partial-restore-lead", checkout="existing", delegation=True)
        lead_claim = admit_board(board, {
            "workerId": "lead-worker", "claimRequestId": "claim-partial-lead",
            "nonce": "p" * 16, "runId": lead["runId"],
        })["claim"]
        child = self._submit(board, "partial-restore-child", credential=lead_claim["agentCredential"])
        child_claim = admit_board(board, {
            "workerId": "child-worker", "claimRequestId": f"claim-{child['runId']}",
            "nonce": "q" * 16, "runId": child["runId"],
        })["claim"]
        manifest = turn_manifest(self.directory, child_claim["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "outside-a.txt").write_text("a\n", encoding="utf-8", newline="\n")
        (checkout / "outside-b.txt").write_text("b\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError):
            workspace.seal(self.directory, manifest, child["runId"], child_claim["attempt"]["attemptId"])
        seal_error = "WORKSPACE_SCOPE_VIOLATION: outside write scope"
        board.call("execution_result", {
            "attemptId": child_claim["attempt"]["attemptId"],
            "generation": child_claim["attempt"]["generation"],
            "nonce": "q" * 16, "status": "failed", "shutdownConfirmed": True, "exitCode": 1,
            "error": seal_error,
            "result": {"status": "failed", "workspaceSealError": seal_error,
                       "turnResultPath": "/tmp/turn-result.json"},
        })

        board.call("workflow_cancel", {"runId": lead["runId"], "note": "Stop Lead"})
        board.call("execution_result", {
            "attemptId": lead_claim["attempt"]["attemptId"],
            "generation": lead_claim["attempt"]["generation"],
            "nonce": "p" * 16, "status": "cancelled", "shutdownConfirmed": True,
            "result": {"status": "cancelled"},
        })

        view = board.call("workflow_get", {"runId": child["runId"]})
        first_req = view["activeRequest"]["requestId"]
        first_fp = view["workspaceConflicts"][0]["observedFingerprint"]

        # Partial restore leaves outside-b.txt open and must advance activeRequest cleanly beneath cancelled Lead.
        partial = board.store.workflow.continue_run({
            "runId": child["runId"], "requestId": first_req, "resolution": "restore",
            "paths": ["outside-a.txt"], "observedFingerprint": first_fp, "note": "Restore first path",
        }, console_authority={"sessionId": "browser"})
        self.assertEqual(partial["resolutionState"], "open")
        mid = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(mid["state"], "awaiting-host")
        second_req = mid["activeRequest"]["requestId"]
        self.assertNotEqual(second_req, first_req)
        second_fp = mid["workspaceConflicts"][0]["observedFingerprint"]

        # Resolving the last path must transition the child to cancelled (never delivered) beneath cancelled Lead.
        done = board.store.workflow.continue_run({
            "runId": child["runId"], "requestId": second_req, "resolution": "restore",
            "paths": ["outside-b.txt"], "observedFingerprint": second_fp, "note": "Restore second path",
        }, console_authority={"sessionId": "browser"})
        self.assertEqual(done["resolutionState"], "restored")
        final_child = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(final_child["state"], "cancelled")
        self.assertIsNone(final_child["activeRequest"])

        concluded = board.call("workflow_conclude", {"runId": lead["runId"], "note": "Conclude Lead tree"})
        self.assertEqual(concluded["state"], "concluded")
        self.assertFalse(checkout.exists())

    def test_schema_42_to_43_recovers_deadlocked_cancelled_conflict_to_conclude_and_reclaim(self):
        from hey_my_buddy.blackboard.store.upgrade import upgrade_state

        board = self._board()
        submitted = self._submit(board, "legacy-deadlocked-conflict")
        run_id = submitted["runId"]
        view, checkout = self._open_scope_conflict(board, run_id)
        old_request_id = view["activeRequest"]["requestId"]
        conflict = view["workspaceConflicts"][0]

        # Reproduce the exact schema-42 deadlocked row produced by prior cancel-on-open-conflict.
        with board.store.db.write() as db:
            db.execute("UPDATE meta SET value='42' WHERE key='schema_version'")
            db.execute("UPDATE workflow_requests SET state='cancelled' WHERE request_id=?", (old_request_id,))
            db.execute("UPDATE workflow_runs SET state='cancelled', active_request_id=NULL WHERE run_id=?", (run_id,))
            db.execute("UPDATE workspace_reservations SET state='released' WHERE holder_task_id=?", (run_id,))
            board.store._append_event(
                db, "workflow.cancelled", task_id=run_id, revision=None,
                payload={"note": "Prior schema-42 cancel", "actor": {"role": "host"},
                         "stopInitiator": "host", "honestShutdown": True},
            )
        board.store.db.close()

        self.assertEqual(upgrade_state(self.directory), {"sourceSchema": 42, "targetSchema": 43})

        reopened = self._board()
        after = reopened.call("workflow_get", {"runId": run_id, "includeAudit": True})
        self.assertEqual(after["state"], "awaiting-host")
        self.assertEqual(after["actions"], ["continue", "get"])
        new_request_id = after["activeRequest"]["requestId"]
        self.assertNotEqual(new_request_id, old_request_id)
        statuses = {item["requestId"]: item["state"] for item in after["audit"]["requests"]}
        self.assertEqual(statuses[old_request_id], "cancelled")
        self.assertEqual(statuses[new_request_id], "open")

        abandoned = reopened.call("workflow_continue", {
            "runId": run_id, "requestId": new_request_id, "resolution": "abandon",
            "observedFingerprint": conflict["observedFingerprint"], "note": "Abandon recovered schema-42 site",
        })
        self.assertEqual(abandoned["resolutionState"], "abandoned")
        concluded = reopened.call("workflow_conclude", {"runId": run_id, "note": "Conclude recovered run"})
        self.assertEqual(concluded["state"], "concluded")
        self.assertTrue(concluded["reclaim"]["removed"])
        self.assertFalse(checkout.exists())
