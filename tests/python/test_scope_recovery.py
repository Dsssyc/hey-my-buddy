"""Real-Git scope recovery: failure evidence, compare-and-swap and resealing.

These tests drive the real ``buddy.workspace`` module, real Git checkouts and the
real governed workflow transactions. Only the model process is absent: the scope
failure, its durable evidence, the conflict record, the restore/adopt/abandon
decision and the resealed handoff are all real. A failed site is never promoted
to an authorized baseline, and a mechanical resolution never needs a model turn.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from test_workflow_real import CONFIGURATION, RealWorkspaceTestCase

from buddy import workflow as workflow_module
from buddy import workspace as workspace_module
from buddy.errors import BoardError


class ScopeRecoveryTestCase(RealWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.controls: dict[str, dict] = {}
        self.worker = "w-scope"
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(self._restore_workspace)

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    # -- governed helpers ----------------------------------------------------
    def register(self, board, worker_id=None):
        return board.call("worker_register", {"workerId": worker_id or self.worker, "capabilities": ["dsh"]})

    def governed_submit(self, board, *, request_id="scope-run", scope=("src",), kind="existing", cwd=None):
        response = board.call("workflow_submit", {
            **CONFIGURATION,
            "requestId": request_id,
            "hostId": "host-1",
            "task": "scoped real workspace task",
            "cwd": str(cwd or self.repo),
            "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": kind, "access": "write", "writeScope": list(scope)},
        })
        self.controls[response["runId"]] = response["control"]
        return response

    def control(self, view):
        return dict(self.controls[view["runId"]])

    def claim(self, board, run_id, *, request_id="claim-1", nonce="n" * 16, worker_id=None):
        return board.call("worker_claim", {
            "workerId": worker_id or self.worker, "claimRequestId": request_id, "nonce": nonce, "runId": run_id,
        })

    def scope_failure(self, board, claimed, *, worker_id=None):
        """The exact failed result a refused seal produces, with real evidence on disk."""
        claim = claimed["claim"]
        attempt = claim["attempt"]
        return board.call("worker_result", {
            "workerId": worker_id or self.worker,
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": "n" * 16,
            "status": "failed",
            "result": {
                "status": "failed",
                "workspaceSealError": "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope",
                "turnResultPath": "/tmp/turn-result.json",
            },
            "shutdownConfirmed": True,
            "exitCode": 1,
            "error": "the workspace seal was refused",
        })

    def turn_record(self, claim, *, disposition="completed"):
        attempt, turn = claim["attempt"], claim["turn"]
        return {
            "version": 1,
            "taskId": attempt["taskId"],
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "turnId": turn["turnId"],
            "resumeMode": turn["resumeMode"],
            "previousSessionId": turn["input"].get("previousSessionId"),
            "sessionId": f"sess-{turn['turnId'][:8]}",
            "promptSha256": "a" * 64,
            "inputSha256": turn["inputSha256"],
            "outcome": {"disposition": disposition, "summary": "did the work", "remaining": [], "decisions": [],
                        "artifacts": [], "request": None},
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited",
                           "rootSessionMatched": True},
        }

    def finish_turn(self, board, claimed, seal, *, worker_id=None):
        claim = claimed["claim"]
        attempt = claim["attempt"]
        return board.call("worker_result", {
            "workerId": worker_id or self.worker,
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": "n" * 16,
            "status": "ok",
            "result": {"status": "ok", "processState": {"shutdownConfirmed": True},
                       "turn": self.turn_record(claim), "turnResultPath": "/tmp/turn-result.json",
                       "workspaceSeal": seal},
            "shutdownConfirmed": True,
            "exitCode": 0,
        })

    def view(self, board, run_id, **extra):
        return board.call("workflow_get", {"runId": run_id, **extra})

    def resolve(self, board, view, conflict, action, *, paths=None, fingerprint=None, command_id="resolve-1",
                reason=None):
        params = {
            "runId": view["runId"],
            "commandId": command_id,
            "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"],
            "action": action,
            "observedFingerprint": fingerprint or conflict["observedFingerprint"],
            **self.control(view),
        }
        if paths is not None:
            params["paths"] = paths
        if reason is not None:
            params["reason"] = reason
        return board.store.workflow.workspace_resolve(params)

    def continue_run(self, board, view, *, command_id="continue-1"):
        return board.call("workflow_continue", {
            "runId": view["runId"], "commandId": command_id, "expectedRevision": view["revision"],
            "input": "continue from the resolved state", **self.control(view),
        })

    def amend(self, board, view, *, command_id="amend-1", expected_scope=1, write_scope=("docs", "src"),
              reason="the next stage may touch docs"):
        return board.store.workflow.scope_amend({
            "runId": view["runId"], "commandId": command_id, "expectedRevision": view["revision"],
            "expectedScopeVersion": expected_scope, "writeScope": list(write_scope), "reason": reason,
            **self.control(view),
        })

    # -- scenarios -----------------------------------------------------------
    def scoped_failure(self, *, request_id="scope-run", scope=("src",), kind="existing"):
        """One governed run whose seal refused tracked and untracked outside changes."""
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id=request_id, scope=scope, kind=kind)
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        self.assertIsNotNone(claimed["claim"], claimed)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n")
        (checkout / "outside.txt").write_text("untracked outside\n")
        error = None
        try:
            workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        except BoardError as caught:
            error = caught
        self.assertIsNotNone(error, "the seal must refuse an out-of-scope change")
        self.assertEqual(error.code, "WORKSPACE_SCOPE_VIOLATION")
        self.scope_failure(board, claimed)
        return board, submitted, claimed, manifest, checkout

    def test_scope_failure_records_evidence_and_opens_a_host_attention(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        conflict = view["workspaceConflicts"][0]
        self.assertEqual(conflict["state"], "open")
        self.assertEqual(conflict["blockingPaths"], ["outside.txt", "tracked.txt"])
        self.assertEqual(conflict["attemptId"], claimed["claim"]["attempt"]["attemptId"])
        by_path = {entry["path"]: entry for entry in conflict["paths"]}
        self.assertIsNone(by_path["outside.txt"]["authorized"])
        self.assertEqual(by_path["tracked.txt"]["authorized"]["mode"], "100644")
        self.assertIn("sha256", by_path["tracked.txt"]["observed"])
        audit = self.view(board, run_id, includeAudit=True)
        request = [row for row in audit["audit"]["requests"] if row["state"] == "open"][0]
        self.assertEqual(request["payload"]["reason"], "workspace-scope-violation")
        self.assertEqual(request["payload"]["paths"], ["outside.txt", "tracked.txt"])
        self.assertEqual(request["payload"]["conflictIds"], [conflict["conflictId"]])
        # The failed turn keeps its honest receipt; nothing was sealed as output.
        with board.store.db.read() as connection:
            turn = connection.execute(
                "SELECT state FROM workflow_turns WHERE attempt_id=?", (claimed["claim"]["attempt"]["attemptId"],)
            ).fetchone()
            outputs = connection.execute(
                "SELECT COUNT(*) FROM workflow_artifacts WHERE run_id=? AND kind IN ('output','resolved-output')",
                (run_id,),
            ).fetchone()[0]
        self.assertEqual(turn["state"], "failed")
        self.assertEqual(outputs, 0)
        self.assertEqual((checkout / "outside.txt").read_text(), "untracked outside\n")

    def test_continuation_never_absorbs_the_unauthorized_failed_site(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        self.continue_run(board, view, command_id="continue-blocked")
        blocked = self.claim(board, run_id, request_id="claim-blocked", nonce="b" * 16)
        self.assertIsNone(blocked["claim"], blocked)
        self.assertEqual(blocked["reason"], "awaiting-host")
        attention = self.view(board, run_id)
        self.assertEqual(attention["state"], "awaiting-host")
        self.assertEqual(attention["activeRequest"]["preparationError"]["code"], "WORKSPACE_SCOPE_CONFLICT")
        with board.store.db.read() as connection:
            continuation = connection.execute(
                "SELECT state, workspace_manifest_json FROM workflow_continuations WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        self.assertEqual(continuation["state"], "invalidated")
        self.assertIsNone(continuation["workspace_manifest_json"])
        # The failure site is preserved exactly, not captured into a new baseline.
        self.assertEqual((checkout / "outside.txt").read_text(), "untracked outside\n")
        self.assertEqual((checkout / "tracked.txt").read_text(), "outside the authorized scope\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")

    def test_restore_preserves_legal_changes_and_reseals_without_a_model_turn(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore",
                                paths=["outside.txt", "tracked.txt"], reason="restore the authorized state")
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["resolutionState"], "restored")
        self.assertEqual(resolved["resolvedPaths"], ["outside.txt", "tracked.txt"])
        self.assertIsNotNone(resolved["artifactId"])
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "tracked.txt").read_text(), "base\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")
        record = resolved
        self.assertEqual(record["artifact"]["action"], "restore")
        self.assertEqual(record["changedPaths"], ["src/feature.py"])
        self.assertEqual(record["adoptedPaths"], [])
        self.assertEqual(resolved["workspaceConflicts"][0]["state"], "restored")
        self.assertEqual(resolved["workspaceConflicts"][0]["artifactId"], resolved["artifactId"])
        # The recorded resolution is the handoff of the next continuation.
        continued = self.continue_run(board, resolved, command_id="continue-after-restore")
        self.assertEqual(continued["state"], "executing")
        resumed = self.claim(board, run_id, request_id="claim-after-restore", nonce="m" * 16)
        self.assertIsNotNone(resumed["claim"], resumed)
        execution = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(execution["baseCommit"], record["outputCommit"])
        self.assertEqual(execution["inputCommit"], record["outputCommit"])
        self.assertEqual(resumed["claim"]["turn"]["turnIndex"], 2)
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")

    def test_restore_compare_and_swap_preserves_a_later_change(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        (checkout / "tracked.txt").write_text("changed after the failure\n")
        before = self.state()
        # The Host re-observes the changed site; the compare-and-swap then refuses
        # only the path that moved and preserves everything else.
        current_fingerprint = workspace_module._stable_observation(checkout, [])["fingerprint"]
        with self.assertRaises(BoardError) as raised:
            self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                         fingerprint=current_fingerprint)
        self.assertEqual(raised.exception.code, "WORKSPACE_CONFLICT")
        self.assertEqual(raised.exception.details["conflictingPaths"], ["tracked.txt"])
        # Nothing was partially applied: the later change and the whole site survive.
        self.assertEqual((checkout / "tracked.txt").read_text(), "changed after the failure\n")
        self.assertEqual((checkout / "outside.txt").read_text(), "untracked outside\n")
        self.assertEqual(self.state(), before)
        current = self.view(board, run_id)
        self.assertEqual(current["workspaceConflicts"][0]["conflictingPaths"], ["tracked.txt"])
        self.assertEqual(current["workspaceConflicts"][0]["state"], "open")
        # A stale Host observation is refused before any file is touched.
        with self.assertRaises(BoardError) as raised:
            self.resolve(board, view, conflict, "restore", paths=["tracked.txt"], fingerprint="0" * 64)
        self.assertEqual(raised.exception.code, "WORKSPACE_CHANGED")

    def test_restore_is_idempotent_and_the_failed_receipt_is_unchanged(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        first = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                             reason="restore once")
        # The same decision replays through the durable conflict record.
        replay = board.store.workflow.workspace_resolve({
            "runId": run_id, "commandId": "resolve-replay", "expectedRevision": first["revision"],
            "conflictId": conflict["conflictId"], "action": "restore", "paths": ["outside.txt", "tracked.txt"],
            "observedFingerprint": first["resolvedFingerprint"], "reason": "restore once",
            **self.control(first),
        })
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["artifactId"], first["artifactId"])
        with board.store.db.read() as connection:
            attempt_id = claimed["claim"]["attempt"]["attemptId"]
            turn = connection.execute("SELECT state, disposition FROM workflow_turns WHERE attempt_id=?", (attempt_id,)).fetchone()
            artifact = connection.execute(
                "SELECT kind, manifest_json FROM workflow_artifacts WHERE attempt_id=? AND kind IN ('output','resolved-output')",
                (attempt_id,),
            ).fetchone()
            receipts = connection.execute(
                "SELECT kind, request_hash FROM commands WHERE attempt_id=?", (attempt_id,)
            ).fetchall()
        self.assertEqual(turn["state"], "failed")
        self.assertIsNone(turn["disposition"])
        self.assertEqual(artifact["kind"], "resolved-output")
        self.assertEqual(json.loads(artifact["manifest_json"])["action"], "restore")

    def test_partial_restore_reports_the_remaining_paths(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        partial = self.resolve(board, view, conflict, "restore", paths=["outside.txt"], reason="restore one path")
        self.assertFalse(partial["resolved"])
        self.assertEqual(partial["resolutionState"], "open")
        self.assertIsNone(partial["artifactId"])
        self.assertIsNone(partial["artifact"])
        self.assertEqual(partial["resolvedPaths"], ["outside.txt"])
        self.assertEqual(partial["remainingPaths"], ["tracked.txt"])
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "tracked.txt").read_text(), "outside the authorized scope\n")
        current = self.view(board, run_id)
        self.assertEqual(current["workspaceConflicts"][0]["state"], "open")
        self.assertEqual(current["workspaceConflicts"][0]["resolvedPaths"], ["outside.txt"])
        completed = self.resolve(board, current, current["workspaceConflicts"][0], "restore",
                                 paths=["tracked.txt"], fingerprint=partial["resolvedFingerprint"],
                                 command_id="resolve-2", reason="restore the rest")
        self.assertTrue(completed["resolved"])
        self.assertEqual(completed["resolutionState"], "restored")
        self.assertEqual((checkout / "tracked.txt").read_text(), "base\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")

    def test_adopt_binds_the_exact_recorded_site(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        with self.assertRaises(BoardError) as raised:
            self.resolve(board, view, conflict, "adopt")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            self.resolve(board, view, conflict, "adopt", paths=["outside.txt"], reason="partial adoption")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        adopted = self.resolve(board, view, conflict, "adopt", reason="the Host accepts these files")
        self.assertTrue(adopted["resolved"])
        self.assertEqual(adopted["resolutionState"], "adopted")
        self.assertEqual(adopted["adoptedPaths"], ["outside.txt", "tracked.txt"])
        self.assertEqual(adopted["artifact"]["action"], "adopt")
        self.assertEqual(adopted["artifact"]["changedPaths"], ["outside.txt", "src/feature.py", "tracked.txt"])
        continued = self.continue_run(board, adopted, command_id="continue-after-adoption")
        self.assertEqual(continued["state"], "executing")
        resumed = self.claim(board, run_id, request_id="claim-adopted", nonce="k" * 16)
        execution = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(execution["baseCommit"], adopted["outputCommit"])
        self.assertEqual((checkout / "outside.txt").read_text(), "untracked outside\n")
        self.assertEqual((checkout / "tracked.txt").read_text(), "outside the authorized scope\n")

    def test_abandon_preserves_the_site_and_returns_the_authorized_input(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure()
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        abandoned = self.resolve(board, view, conflict, "abandon", reason="discard this failed round")
        self.assertTrue(abandoned["resolved"])
        self.assertEqual(abandoned["resolutionState"], "abandoned")
        self.assertEqual(abandoned["artifact"]["kind"], "abandoned-site")
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "tracked.txt").read_text(), "base\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 1\n")
        patch = Path(abandoned["diffPath"]).read_text()
        self.assertIn("outside.txt", patch)
        self.assertIn("value = 2", patch)
        continued = self.continue_run(board, abandoned, command_id="continue-after-abandon")
        self.assertEqual(continued["state"], "executing")
        resumed = self.claim(board, run_id, request_id="claim-abandoned", nonce="j" * 16)
        self.assertIsNotNone(resumed["claim"], resumed)
        execution = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(execution["manifestSha256"], manifest["manifestSha256"])
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 1\n")

    def test_scope_amendment_is_versioned_and_fenced_by_an_active_attempt(self):
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id="amend-run", scope=("src",))
        run_id = submitted["runId"]
        first = self.claim(board, run_id, request_id="claim-amend-first")
        manifest = first["claim"]["turn"]["input"]["executionWorkspace"]
        active = self.view(board, run_id)
        with self.assertRaises(BoardError) as raised:
            self.amend(board, active, write_scope=(".", "docs"), reason="retroactive")
        self.assertEqual(raised.exception.code, "SHUTDOWN_UNCONFIRMED")
        seal = workspace_module.seal(self.directory, manifest, run_id, first["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, first, seal)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        amended = self.amend(board, delivered)
        self.assertEqual(amended["scopeVersion"], 2)
        self.assertEqual(amended["writeScope"], ["docs", "src"])
        with board.store.db.read() as connection:
            versions = connection.execute(
                "SELECT scope_version, write_scope_json, stopped_evidence_json FROM workflow_scope_versions"
                " WHERE run_id=? ORDER BY scope_version",
                (run_id,),
            ).fetchall()
        self.assertEqual([row["scope_version"] for row in versions], [1, 2])
        self.assertEqual(json.loads(versions[0]["write_scope_json"]), ["src"])
        self.assertEqual(json.loads(versions[1]["stopped_evidence_json"])["selfConfirmed"], True)
        with self.assertRaises(BoardError) as raised:
            self.amend(board, amended, command_id="amend-stale", expected_scope=1, write_scope=(".",))
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")

    def test_amended_scope_applies_only_to_the_next_prepared_stage(self):
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id="amend-stage", scope=("src",))
        run_id = submitted["runId"]
        first = self.claim(board, run_id, request_id="claim-first")
        manifest = first["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(manifest["writeScope"], ["src"])
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, first["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, first, seal)
        view = self.view(board, run_id)
        self.assertEqual(view["state"], "delivered")
        amended = self.amend(board, view)
        self.continue_run(board, amended, command_id="continue-amended")
        resumed = self.claim(board, run_id, request_id="claim-amended", nonce="d" * 16)
        self.assertIsNotNone(resumed["claim"], resumed)
        execution = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(execution["writeScope"], ["docs", "src"])
        self.assertEqual(execution["baseCommit"], seal["commit"])
        # The earlier turn input keeps its original scope; only the new stage moved.
        with board.store.db.read() as connection:
            original = connection.execute(
                "SELECT input_json FROM workflow_turns WHERE attempt_id=?", (first["claim"]["attempt"]["attemptId"],)
            ).fetchone()
        self.assertEqual(json.loads(original["input_json"])["executionWorkspace"]["writeScope"], ["src"])


if __name__ == "__main__":
    unittest.main()
