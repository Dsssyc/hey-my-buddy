"""Restart seams continue seal, integration and reclamation.

A replacement process resumes each native seam from its durable record: the
accepted target and cleanup plan survive before apply, a copied worktree
administration directory is refused, a scope failure keeps its reason, and an
abandoned continuation returns its input.
"""
from __future__ import annotations
import json
import shutil
import time
import unittest
from pathlib import Path
from blackboard.tasks.test_workflow_real import BUDDY
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles import router_input, turn_io
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git


class RestartLifecycleTests(LifecycleTestCase):
    def test_restart_native_seams_continue_seal_integrate_and_reclaim_allocation(self):
        board = self.board()
        self.register(board)
        submitted, claimed, original, checkout, first_seal, parked, _artifact = self.run_worktree(
            board, request_id="restart-chain", output="base\n", disposition="assistance")
        initial_files = self.fixed_files()
        board = self.board()
        self.assertEqual(self.fixed_files(), initial_files)
        attempt = claimed["claim"]["attempt"]
        context = ExecutionContext(
            task_id=submitted["runId"], attempt_id=attempt["attemptId"], generation=attempt["generation"],
            spec={**BUDDY, "cwd": original["path"], "task": "private identity seam", "expectedSeconds": 30},
            directory=self.directory / "native-seam", runtime={},
            environment={"HMB_STATE_DIR": str(self.directory)}, turn=claimed["claim"]["turn"],
            session_record_path=None,
        )
        # These role functions verify the retained input without a coordinator context.
        turn_io.verify_workspace(context)
        self.assertEqual(context.effective_workspace, original)
        replay_seal, error = turn_io.seal_workspace(context)
        self.assertIsNone(error)
        self.assertEqual(replay_seal, first_seal)
        router_directory = self.directory / "router-seam"
        router_directory.mkdir()
        frozen, digest = router_input.prepare(original, router_directory, deadline=time.monotonic() + 120, state_dir=self.directory)
        self.assertEqual((frozen / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        self.assertTrue(router_input.verify(original, frozen, digest, deadline=time.monotonic() + 120, state_dir=self.directory)["unchanged"])
        self.assertEqual(self.fixed_files(), initial_files)
        run_id = submitted["runId"]
        continued = board.call("workflow_continue", {
            "runId": run_id, "commandId": "restart-chain-continue", "expectedRevision": parked["revision"],
            "input": "finish after the simulated restart", **self.control(parked),
        })
        self.assertEqual(continued["state"], "executing")
        resumed = self.claim(board, run_id, request_id="restart-chain-second", nonce="q" * 16)
        self.assertIsNotNone(resumed["claim"], resumed)
        current = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        actual = workspace_module.inspect(str(checkout))
        self.assertEqual(current["path"], original["path"])
        self.assertEqual(current["checkoutId"], actual["checkoutId"])
        self.assertEqual(current["checkoutId"], original["checkoutId"])
        self.assertEqual(current["baseCommit"], first_seal["commit"])
        (checkout / "tracked.txt").write_text("output after reboot\n", encoding="utf-8", newline="\n")
        sealed = workspace_module.seal(self.directory, current, run_id, resumed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, resumed, sealed, nonce="q" * 16)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        accepted = self.accept(board, delivered, final, target=self.target_with_artifact(final), keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(workspace_module.verify_sealed_output(original, current, sealed)["changedPaths"], ["tracked.txt"])
        removed = self.reclaim(board, self.view(board, run_id))
        self.assertTrue(removed["removed"])
        self.assertEqual(removed["path"], original["checkoutRoot"])
        self.assertFalse(checkout.exists())
        self.assertEqual(self.reclaim(board, self.view(board, run_id))["alreadyRemoved"], removed["alreadyRemoved"])
        for path, raw in initial_files.items():
            self.assertEqual(native_path(self.directory / path).read_bytes(), raw, path)
        with board.store.db.read() as connection:
            saved = connection.execute("SELECT t.input_json,json_extract(a.result_json,'$.result.turn.inputSha256')"
                                       " AS input_sha256 FROM workflow_turns t JOIN attempts a USING(attempt_id)"
                                       " WHERE t.attempt_id=?",
                                       (attempt["attemptId"],)).fetchone()
        self.assertEqual(json.loads(saved["input_json"]), claimed["claim"]["turn"]["input"])
        self.assertEqual(saved["input_sha256"], claimed["claim"]["turn"]["inputSha256"])

    def test_restart_preserves_accepted_target_and_cleanup_plan_before_apply(self):
        board = self.board()
        self.register(board)
        submitted, _claim, original, checkout, seal, delivered, artifact = self.run_worktree(
            board, request_id="restart-accepted")
        target = self.target_with_artifact(artifact, name="restart-target")
        # A retained target input supplies independent directory/inode and
        # fixed-reference proof for optional target identity fields.
        target_run = self.submit(board, request_id="restart-target-input", cwd=target["path"], access="read")
        target_identity = workspace_module.inspect(str(target["path"]))
        target.update({key: target_identity[key] for key in ("repositoryId", "checkoutId")})
        accepted = self.accept(board, delivered, artifact, target=target, keepCheckout=True)
        planned = board.store.workflow.cleanup_plan({
            "runId": submitted["runId"], "commandId": "restart-plan",
            "expectedRevision": accepted["revision"], **self.control(accepted),
        })
        self.assertEqual(planned["plan"]["state"], "planned")
        files = self.fixed_files()
        board = self.board()
        self.assertEqual(self.fixed_files(), files)
        self.assertEqual(workspace_module.inspect(str(target["path"]))["checkoutId"], target["checkoutId"])
        target_check = workspace_module.integration_verify(
            seal, original_input=original, final_input=original,
            path=str(target["path"]), ref="HEAD", before_commit=target["before"],
            repository_id=target["repositoryId"], checkout_id=target["checkoutId"],
        )
        self.assertTrue(target_check["verified"])
        # A receipt-bound replay retains the user's optional target identities.
        repeated = self.accept(board, self.view(board, submitted["runId"]), artifact, target=target, keepCheckout=True)
        self.assertEqual(repeated["state"], "accepted")
        removed = board.store.workflow.cleanup_apply({
            "runId": submitted["runId"], "planId": planned["plan"]["planId"], "commandId": "restart-apply-plan",
            "expectedRevision": planned["targetRevision"], "confirmPath": planned["plan"]["path"],
            **self.control(accepted),
        })
        self.assertTrue(removed["removed"])
        self.assertEqual(removed["plan"]["path"], original["checkoutRoot"])
        self.assertEqual(set(removed["plan"]["result"]), {"alreadyRemoved"})
        self.assertFalse(checkout.exists())
        self.assertTrue(native_path(Path(target["path"])).exists())
        target_view = board.call("task_get", {"runId": target_run["runId"]})["task"]
        self.assertEqual(target_view["lineage"]["project"]["id"],
                         workspace_module.inspect(str(target["path"]))["repositoryId"])
        for path, raw in files.items():
            self.assertEqual(native_path(self.directory / path).read_bytes(), raw, path)

    def test_restart_cannot_remove_a_copied_worktree_git_directory(self):
        board, submitted, original, checkout, _artifact, _view = self.accepted_worktree(
            request_id="restart-replaced-allocation")
        board = self.board()
        actual = workspace_module.inspect(str(checkout))
        git_directory = Path(actual["gitDir"])
        retained = self.directory / "retained-allocation-git-directory"
        native_path(git_directory).rename(native_path(retained))
        shutil.copytree(native_path(retained), native_path(git_directory))
        self.assertNotEqual(native_path(git_directory).stat().st_ino, native_path(retained).stat().st_ino)
        self.assertEqual(native_path(git_directory / "HEAD").read_bytes(), native_path(retained / "HEAD").read_bytes())
        self.assertEqual(git(checkout, "rev-parse", original["snapshot"]["inputRef"]).strip(), original["inputCommit"])
        with self.assertRaises(BoardError):
            workspace_module.verify(original, require_unchanged=False, state_dir=self.directory)
        with self.assertRaises(BoardError) as unsafe:
            workspace_module.cleanup_remove(self.directory, original)
        self.assertEqual(unsafe.exception.code, "WORKSPACE_CHANGED")
        self.assertTrue(checkout.exists())
        self.assertTrue(native_path(git_directory).exists())
        self.assertTrue(retained.exists())
        self.assertNotEqual((self.view(board, submitted["runId"])["cleanup"] or {}).get("state"), "applied")

    def test_restart_scope_restore_keeps_the_failure_and_continues_helper(self):
        flow = self.helper_scope_failure(request_id="restart-restore")
        board, root_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        files = self.fixed_files()
        board = self.board()
        self.assertEqual(self.fixed_files(), files)
        root = self.view(board, root_id)
        resolved = board.store.workflow.workspace_resolve({
            "runId": root_id, "targetRunId": helper_id, "commandId": "restart-restore-resolution",
            "expectedRevision": root["revision"], "conflictId": flow["conflict"]["conflictId"], "action": "restore",
            "paths": ["tracked.txt"], "observedFingerprint": flow["conflict"]["observedFingerprint"],
            'note': "restore the authorized helper input", **self.control(root),
        })
        self.assertEqual(resolved["resolutionState"], "restored")
        self.assertEqual((flow["helperCheckout"] / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        self.assertEqual((flow["helperCheckout"] / "src" / "feature.py").read_text(encoding="utf-8"), "value = 2\n")
        root = self.view(board, root_id)
        board.call("workflow_continue", {
            "runId": root_id, "targetRunId": helper_id, "commandId": "restart-restored-helper-continue",
            "expectedRevision": root["revision"], "input": "continue from the preserved legal output", **self.control(root),
        })
        resumed = self.claim(board, helper_id, request_id="restart-restored-helper-claim")
        self.assertIsNotNone(resumed["claim"], resumed)
        current = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(current["baseCommit"], resolved["outputCommit"])
        self.assertEqual(current["checkoutId"], workspace_module.inspect(current["path"])["checkoutId"])
        for path, raw in files.items():
            self.assertEqual(native_path(self.directory / path).read_bytes(), raw, path)

    def test_restart_abandon_returns_input_and_preserves_the_failed_site(self):
        flow = self.helper_scope_failure(request_id="restart-abandon")
        board, root_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        files = self.fixed_files()
        board = self.board()
        self.assertEqual(self.fixed_files(), files)
        root = self.view(board, root_id)
        abandoned = board.store.workflow.workspace_resolve({
            "runId": root_id, "targetRunId": helper_id, "commandId": "restart-abandon-resolution",
            "expectedRevision": root["revision"], "conflictId": flow["conflict"]["conflictId"], "action": "abandon",
            "observedFingerprint": flow["conflict"]["observedFingerprint"], 'note': "preserve this failed round as evidence",
            **self.control(root),
        })
        self.assertEqual(abandoned["resolutionState"], "abandoned")
        self.assertEqual(abandoned["artifact"]["kind"], "abandoned-site")
        self.assertIn("outside the helper scope", native_path(Path(abandoned["diffPath"])).read_text(encoding="utf-8"))
        self.assertEqual((flow["helperCheckout"] / "src" / "feature.py").read_text(encoding="utf-8"), "value = 1\n")
        root = self.view(board, root_id)
        board.call("workflow_continue", {
            "runId": root_id, "targetRunId": helper_id, "commandId": "restart-abandoned-helper-continue",
            "expectedRevision": root["revision"], "input": "continue from the original authorized input", **self.control(root),
        })
        resumed = self.claim(board, helper_id, request_id="restart-abandoned-helper-claim")
        self.assertIsNotNone(resumed["claim"], resumed)
        current = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(current, flow["helperManifest"])
        for path, raw in files.items():
            self.assertEqual(native_path(self.directory / path).read_bytes(), raw, path)


if __name__ == "__main__":
    unittest.main()
