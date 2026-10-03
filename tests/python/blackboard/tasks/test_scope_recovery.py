"""Real-Git scope recovery: failure evidence, compare-and-swap and resealing.

These tests drive the real ``hey_my_buddy.blackboard.tasks.workspace`` module, real Git checkouts and the
real governed workflow transactions. Only the model process is absent: the scope
failure, its durable evidence, the conflict record, the restore/adopt/abandon
decision and the resealed handoff are all real. A failed site is never promoted
to an authorized baseline, and a mechanical resolution never needs a model turn.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from blackboard.tasks.test_workflow_real import CONFIGURATION, GIT_ENV, RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.errors import BoardError


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

    def scope_failure(self, board, claimed, *, native=None, worker_id=None):
        """The exact failed result a refused seal produces, with real evidence on disk.

        ``native`` selects the disposition of the attempt's own native turn record, so a
        scope-only failure can be checked to still carry its untouched outcome. The
        default carries no turn record at all, exactly like a genuine harness failure.
        """
        claim = claimed["claim"]
        attempt = claim["attempt"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        result = {
            "status": "failed",
            "workspaceSealError": seal_error,
            "turnResultPath": "/tmp/turn-result.json",
        }
        if native is not None:
            result["processState"] = {"shutdownConfirmed": True}
            result["turn"] = self.turn_record(claim, disposition=native)
        return board.call("worker_result", {
            "workerId": worker_id or self.worker,
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": "n" * 16,
            "status": "failed",
            "result": result,
            "shutdownConfirmed": True,
            "exitCode": 1,
            "error": seal_error,
        })

    def turn_record(self, claim, *, disposition="completed"):
        attempt, turn = claim["attempt"], claim["turn"]
        outcome = {"disposition": disposition, "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        if disposition in ("assistance", "attention"):
            outcome["request"] = {
                "summary": "please review the failed seal",
                "attempted": "tried the obvious fix",
                "neededWork": ["decide the scope"],
                "expectedArtifacts": ["a reviewed decision"],
                "acceptance": "the scope decision is recorded",
            }
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
            "outcome": outcome,
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited",
                           "rootSessionMatched": True},
        }

    def finish_turn(self, board, claimed, seal, *, disposition="completed", worker_id=None):
        claim = claimed["claim"]
        attempt = claim["attempt"]
        return board.call("worker_result", {
            "workerId": worker_id or self.worker,
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": "n" * 16,
            "status": "ok",
            "result": {"status": "ok", "processState": {"shutdownConfirmed": True},
                       "turn": self.turn_record(claim, disposition=disposition),
                       "turnResultPath": "/tmp/turn-result.json", "workspaceSeal": seal},
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

    def native_scope_failure(self, *, request_id="native-run", disposition="completed"):
        """A scope-only failure whose attempt still carries its native turn outcome."""
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id=request_id, scope=("src",))
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n")
        (checkout / "outside.txt").write_text("untracked outside\n")
        with self.assertRaises(BoardError) as raised:
            workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self.scope_failure(board, claimed, native=disposition)
        return board, submitted, claimed, manifest, checkout

    def ledger(self, board, run_id):
        """The durable identity counts and the failed result receipt of one run."""
        with board.store.db.read() as connection:
            attempt_id = connection.execute(
                "SELECT attempt_id FROM attempts WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()["attempt_id"]
            receipt = connection.execute(
                "SELECT * FROM commands WHERE command_id=?", (f"result:{attempt_id}",)
            ).fetchone()
            return {
                "attempts": connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE task_id=?", (run_id,)).fetchone()[0],
                "turns": connection.execute(
                    "SELECT COUNT(*) FROM workflow_turns WHERE run_id=?", (run_id,)).fetchone()[0],
                "receipt": dict(receipt) if receipt is not None else None,
            }

    def verified_target(self, artifact, *, name="native-target"):
        """A real clone that actually received the resolved artifact's patch."""
        environment = {**os.environ, **GIT_ENV}
        target = self.directory / name
        subprocess.run(["git", "clone", "-q", str(self.repo), str(target)], env=environment, check=True)
        before = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "HEAD"], env=environment, capture_output=True, text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(target), "apply", "--binary", artifact["diffPath"]], env=environment, check=True,
        )
        subprocess.run(["git", "-C", str(target), "add", "."], env=environment, check=True)
        subprocess.run(["git", "-C", str(target), "commit", "-qm", "integrate"], env=environment, check=True)
        return {"path": target, "before": before}

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

    # -- Host-resolution delivery of a scope-only failure ---------------------
    def test_native_scope_only_failure_is_delivered_by_a_full_restore(self):
        board, submitted, claimed, manifest, checkout = self.native_scope_failure()
        run_id = submitted["runId"]
        attempt_id = claimed["claim"]["attempt"]["attemptId"]
        turn_id = claimed["claim"]["turn"]["turnId"]
        before = self.ledger(board, run_id)
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                reason="only the refused seal failed")
        self.assertTrue(resolved["delivered"])
        self.assertEqual(resolved["state"], "delivered")
        self.assertEqual(resolved["finalAttemptId"], attempt_id)
        self.assertEqual(resolved["finalArtifactId"], resolved["artifactId"])
        self.assertEqual(resolved["delivery"]["kind"], "host-resolution")
        self.assertEqual(resolved["delivery"]["action"], "restore")
        self.assertEqual(resolved["delivery"]["turnId"], turn_id)
        cumulative = resolved["artifact"]["cumulativePatch"]
        self.assertEqual(cumulative["baseCommit"], submitted["workspace"]["inputCommit"])
        self.assertEqual(cumulative["outputCommit"], resolved["outputCommit"])
        self.assertEqual(cumulative["changedPaths"], ["src/feature.py"])
        self.assertTrue(Path(cumulative["path"]).is_file())
        self.assertIsNone(resolved["nextBoundaryRequestId"])
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "tracked.txt").read_text(), "base\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")
        # The failed attempt, its failed turn and the original result receipt stay
        # exactly as they were: the delivery is recorded separately, not rewritten.
        self.assertEqual(self.ledger(board, run_id), before)
        with board.store.db.read() as connection:
            attempt = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            turn = connection.execute(
                "SELECT * FROM workflow_turns WHERE turn_id=?", (turn_id,)).fetchone()
            delivery = json.loads(connection.execute(
                "SELECT delivery_json FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (conflict["conflictId"],),
            ).fetchone()["delivery_json"])
        self.assertEqual(attempt["shutdown_confirmed"], 1)
        self.assertEqual(turn["state"], "failed")
        self.assertIsNone(turn["disposition"])
        payload = json.loads(attempt["result_json"])
        self.assertEqual(payload["status"], "failed")
        self.assertIn("workspaceSealError", payload["result"])
        self.assertIn("turn", payload["result"])
        self.assertEqual(delivery["state"], "delivered")
        self.assertEqual(delivery["artifactId"], resolved["artifactId"])
        self.assertEqual(resolved["workspaceConflicts"][0]["delivery"]["state"], "delivered")
        # Acceptance needs no further model turn. The resolved artifact is verified
        # against a real target checkout that actually received it.
        target = self.verified_target(resolved["artifact"])
        accepted = board.call("workflow_accept", {
            "runId": run_id, "artifactId": resolved["artifactId"],
            "note": "reviewed the Host-resolved delivery",
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"], **self.control(resolved),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["state"], "verified")
        self.assertEqual(accepted["integration"]["sourceCommit"], resolved["outputCommit"])
        self.assertEqual(accepted["integration"]["verification"]["matchingPaths"], ["src/feature.py"])
        self.assertEqual(accepted["resolutionDelivery"]["conflictId"], conflict["conflictId"])
        self.assertEqual(accepted["resolutionDelivery"]["artifactId"], resolved["artifactId"])
        self.assertEqual(self.ledger(board, run_id), before)

    def test_native_scope_only_failure_is_delivered_by_adoption(self):
        board, submitted, claimed, manifest, checkout = self.native_scope_failure(request_id="native-adopt")
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        adopted = self.resolve(board, view, conflict, "adopt", reason="the Host accepts this exact site")
        self.assertTrue(adopted["delivered"])
        self.assertEqual(adopted["state"], "delivered")
        self.assertEqual(adopted["delivery"]["action"], "adopt")
        self.assertEqual(adopted["artifact"]["action"], "adopt")
        cumulative = adopted["artifact"]["cumulativePatch"]
        self.assertEqual(cumulative["baseCommit"], submitted["workspace"]["inputCommit"])
        self.assertIn("outside.txt", cumulative["changedPaths"])
        self.assertTrue(Path(cumulative["path"]).is_file())
        self.assertEqual((checkout / "outside.txt").read_text(), "untracked outside\n")
        self.assertEqual((checkout / "tracked.txt").read_text(), "outside the authorized scope\n")

    def test_partial_restore_and_abandon_never_deliver(self):
        board, submitted, claimed, manifest, checkout = self.native_scope_failure(request_id="native-partial")
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        partial = self.resolve(board, view, conflict, "restore", paths=["outside.txt"],
                               reason="only one path is safe to restore")
        self.assertFalse(partial["delivered"])
        self.assertEqual(partial["resolutionState"], "open")
        self.assertIsNone(partial["artifactId"])
        self.assertEqual(partial["state"], "awaiting-host")
        current = self.view(board, run_id)
        abandoned = self.resolve(board, current, current["workspaceConflicts"][0], "abandon",
                                 command_id="resolve-abandon", fingerprint=partial["resolvedFingerprint"],
                                 reason="discard this failed round")
        self.assertFalse(abandoned["delivered"])
        self.assertEqual(abandoned["resolutionState"], "abandoned")
        self.assertEqual(abandoned["state"], "awaiting-host")
        self.assertIsNone(abandoned["delivery"])
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 1\n")

    def test_a_scope_failure_without_a_native_turn_is_never_delivered(self):
        board, submitted, claimed, manifest, checkout = self.scoped_failure(request_id="no-native")
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                reason="restore a genuine harness failure")
        self.assertTrue(resolved["resolved"])
        self.assertFalse(resolved["delivered"])
        self.assertIsNone(resolved["delivery"])
        self.assertEqual(resolved["state"], "awaiting-host")
        with board.store.db.read() as connection:
            stored = connection.execute(
                "SELECT delivery_json FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (conflict["conflictId"],),
            ).fetchone()["delivery_json"]
        self.assertIsNone(stored)

    def test_a_native_record_that_no_longer_validates_is_not_delivered(self):
        board, submitted, claimed, manifest, checkout = self.native_scope_failure(request_id="native-invalid")
        run_id = submitted["runId"]
        attempt_id = claimed["claim"]["attempt"]["attemptId"]
        with board.store.db.write() as connection:
            row = connection.execute(
                "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            stored = json.loads(row["result_json"])
            stored["result"]["turn"]["provenance"]["rootSessionMatched"] = False
            connection.execute(
                "UPDATE attempts SET result_json=? WHERE attempt_id=?", (json.dumps(stored), attempt_id))
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                reason="the native record is broken")
        self.assertFalse(resolved["delivered"])
        self.assertEqual(resolved["state"], "awaiting-host")

    def test_native_assistance_request_survives_as_the_next_boundary(self):
        board, submitted, claimed, manifest, checkout = self.native_scope_failure(
            request_id="native-assist", disposition="assistance")
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                reason="settle the scope, keep the native question")
        self.assertTrue(resolved["resolved"])
        self.assertFalse(resolved["delivered"])
        self.assertEqual(resolved["state"], "awaiting-host")
        self.assertIsNotNone(resolved["nextBoundaryRequestId"])
        self.assertEqual(resolved["activeRequest"]["requestId"], resolved["nextBoundaryRequestId"])
        self.assertEqual(resolved["activeRequest"]["kind"], "assistance")
        self.assertEqual(resolved["activeRequest"]["summary"], "please review the failed seal")
        self.assertEqual(resolved["activeRequest"]["attemptId"], claimed["claim"]["attempt"]["attemptId"])
        with board.store.db.read() as connection:
            delivery = connection.execute(
                "SELECT delivery_json FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (conflict["conflictId"],),
            ).fetchone()["delivery_json"]
        self.assertIsNone(delivery)

    # -- scope v1 rows for every creation path --------------------------------
    def test_current_contract_runs_require_a_real_scope_v1_row(self):
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id="scope-row", scope=("src",))
        run_id = submitted["runId"]
        view = self.view(board, run_id)
        self.assertEqual(view["scope"]["scopeVersion"], 1)
        self.assertTrue(view["scope"]["recorded"])
        self.assertEqual(view["scope"]["writeScope"], ["src"])
        with board.store.db.read() as connection:
            rows = connection.execute(
                "SELECT scope_version FROM workflow_scope_versions WHERE run_id=? ORDER BY scope_version",
                (run_id,),
            ).fetchall()
        self.assertEqual([row["scope_version"] for row in rows], [1])
        # Absent state is an integrity error, never a legacy fallback that would
        # reauthorize a pre-slice run from its manifest.
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_scope_versions WHERE run_id=?", (run_id,))
        with self.assertRaises(BoardError) as raised:
            self.view(board, run_id)
        self.assertEqual(raised.exception.code, "STATE_INTEGRITY")
        self.assertEqual(raised.exception.details["runId"], run_id)

    def test_helper_admission_records_its_own_scope_v1(self):
        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id="helper-scope", scope=("src",))
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id, request_id="helper-scope-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        (Path(manifest["path"]) / "src" / "feature.py").write_text("value = 2\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal, disposition="assistance")
        view = self.view(board, run_id)
        self.assertEqual(view["state"], "awaiting-host")
        approved = board.call("workflow_decide", {
            "runId": run_id, "requestId": view["activeRequest"]["requestId"], "commandId": "decide-helper",
            "expectedRevision": view["revision"], "decision": "approve", "reason": "one isolated helper",
            "helpers": [{
                **CONFIGURATION, "requestId": "helper-scope-1", "task": "helper task", "cwd": str(self.repo),
                "executionWorkspace": {"kind": "worktree", "access": "write"},
            }],
            **self.control(view),
        })
        self.assertEqual(len(approved["children"]), 1)
        child = approved["children"][0]["taskId"]
        child_view = self.view(board, child)
        self.assertEqual(child_view["scope"]["scopeVersion"], 1)
        self.assertTrue(child_view["scope"]["recorded"])
        self.assertEqual(child_view["scope"]["writeScope"], ["."])
        with board.store.db.read() as connection:
            rows = connection.execute(
                "SELECT scope_version, reason, stopped_evidence_json FROM workflow_scope_versions"
                " WHERE run_id=? ORDER BY scope_version",
                (child,),
            ).fetchall()
        self.assertEqual([row["scope_version"] for row in rows], [1])
        self.assertEqual(rows[0]["reason"], "helper admission")
        self.assertEqual(json.loads(rows[0]["stopped_evidence_json"])["parentRunId"], run_id)


if __name__ == "__main__":
    unittest.main()
