"""An interrupted started reclaim resumes without removing twice.

Each test interrupts one phase of a started removal and proves the reclaim
continues from its durable record, refuses what the record cannot prove, and
reports the branch or registration diagnosis it found.
"""
from __future__ import annotations
import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path
from unittest import mock
from support import FakeClock
from blackboard.tasks.test_workflow_real import GIT_ENV
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.errors import BoardError

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git


class ReclaimResumeLifecycleTests(LifecycleTestCase):
    def test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id="expiry-1")
        target = self.target_with_artifact(artifact)
        accepted = self.accept(board, view, artifact, target=target, reason="verified before cleanup",
                               keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        view = self.view(board, submitted["runId"])
        (checkout / "tracked.txt").write_text("edited after the seal\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        blocked = self.view(board, submitted["runId"])["cleanup"]
        self.assertEqual(blocked["state"], "blocked")
        self.assertTrue(checkout.exists())
        # Removing the blocker runs one fresh removal authorization for the same
        # exact registered path.
        (checkout / "tracked.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        applied = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], manifest["checkoutRoot"])
        self.assertNotEqual(self.view(board, submitted["runId"])["cleanup"]["planId"], blocked["planId"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_recovers_after_interruption_without_removing_twice(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.register(board)
        submitted, _claim, _manifest, checkout, _seal, view, artifact = self.run_worktree(board)
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact), keepCheckout=True)
        remove = workspace_module.cleanup_remove

        def crash_after_removal(*args, **kwargs):
            remove(*args, **kwargs)
            raise RuntimeError("simulated loss before the completion transaction")

        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=crash_after_removal):
            with self.assertRaises(Exception):
                self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applying")
        # Even after the internal admission window has lapsed, an applying reclaim
        # resumes to its recorded completion instead of deleting anything twice.
        clock.value = "2099-01-01T00:00:00.000Z"
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("second deletion")):
            recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertTrue(recovered["alreadyRemoved"])
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")
        replay = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(replay["removed"])
        self.assertTrue(replay["alreadyRemoved"])
        self.assertTrue(Path(artifact["diffPath"]).is_file())

    def test_started_reclaim_resumes_after_unlock_before_removal(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim()
        with board.store.db.read() as connection:
            before = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=?", (submitted["runId"],),
            ).fetchone())
        recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertFalse(recovered["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("second deletion")):
            replay = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertEqual(replay["planId"], recovered["planId"])
        for key in ("removed", "alreadyRemoved", "path", "planId", "retention"):
            self.assertEqual(replay[key], recovered[key])
        with board.store.db.read() as connection:
            after = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (before["plan_id"],),
            ).fetchone())
            events = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.cleanup_applied'",
                                        (submitted["runId"],)).fetchall()
        self.assertEqual(json.loads(after["result_json"]), {"alreadyRemoved": False})
        self.assertNotIn("commandReceipts", json.loads(after["retention_json"]))
        self.assertNotIn("commandReceipts", recovered["retention"])
        for column in before.keys() - {"state", "revision", "applied_at", "result_json"}:
            self.assertEqual(after[column], before[column], column)
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual([json.loads(row["payload_json"])["alreadyRemoved"] for row in events], [False])
        self.assertTrue(Path(artifact["diffPath"]).is_file())

    def test_started_reclaim_resumes_with_only_missing_sealed_files(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(missing=("tracked.txt",))
        self.assertTrue((checkout / ".git").is_file())
        recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertFalse(recovered["alreadyRemoved"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_resumes_with_missing_gitfile_and_sealed_files(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(
            missing=("tracked.txt",), remove_git=True)
        self.assertFalse((checkout / ".git").exists())
        recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertFalse(recovered["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        self.assertIsNone(workspace_module._worktree_record(self.repo, checkout))

    def test_refused_started_reclaim_preserves_every_plan_column_and_the_capture_fence(self):
        (self.repo / "kept.txt").write_text("retained tracked file\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", "kept.txt")
        git(self.repo, "commit", "-qm", "another retained file")
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(missing=("tracked.txt",))
        run_id = submitted["runId"]
        with board.store.db.read() as connection:
            before = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone())
        self.assertEqual(before["state"], "applying")
        (checkout / "kept.txt").write_bytes(b"unsealed edit\x00\xff")
        retained = {path.relative_to(checkout): path.read_bytes()
                    for path in checkout.rglob("*") if path.is_file()}
        refs = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/")
        with self.assertRaises(BoardError) as refused:
            self.reclaim(board, self.view(board, run_id))
        self.assertEqual(refused.exception.code, "NOT_READY")
        self.assertEqual(refused.exception.details["reasons"], ["unsealed-changes"])
        self.assertEqual(refused.exception.details["reclaim"]["unsealedPaths"], ["kept.txt"])
        with board.store.db.read() as connection:
            after = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (before["plan_id"],),
            ).fetchone())
        self.assertEqual(after, before)
        with self.assertRaises(BoardError) as fenced:
            board.store.workflow._cleanup_fence(str(checkout))
        self.assertEqual(fenced.exception.code, "PREPARATION_CONFLICT")
        self.assertEqual(fenced.exception.details["planId"], before["plan_id"])
        self.assertEqual((checkout / "tracked.txt").read_text(encoding="utf-8"), "sealed output\n")
        retained[Path("tracked.txt")] = (checkout / "tracked.txt").read_bytes()
        self.assertEqual({path.relative_to(checkout): path.read_bytes()
                          for path in checkout.rglob("*") if path.is_file()}, retained)
        self.assertEqual(workspace_module._worktree_record(self.repo, checkout)["locked"],
                         "hey-my-buddy:" + manifest["workspaceId"])
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/"), refs)
        (checkout / "kept.txt").write_text("retained tracked file\n", encoding="utf-8", newline="\n")
        recovered = self.reclaim(board, self.view(board, run_id))
        self.assertTrue(recovered["removed"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_refuses_an_added_file_without_a_gitfile_then_resumes(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(
            missing=("tracked.txt",), remove_git=True)
        added = checkout / "new" / "payload.bin"
        added.parent.mkdir()
        added.write_bytes(b"not sealed\x00\xff")
        self.assert_started_reclaim_refused(board, submitted["runId"], checkout, "unsealed-changes",
                                           paths=["new/payload.bin"], restored=(".git", "tracked.txt"))
        self.assertEqual(workspace_module._worktree_record(self.repo, checkout)["locked"],
                         "hey-my-buddy:" + manifest["workspaceId"])
        self.assertEqual((checkout / "tracked.txt").read_text(encoding="utf-8"), "sealed output\n")
        self.assertEqual(workspace_module.inspect(str(checkout))["checkoutId"], manifest["checkoutId"])
        git(self.repo, "worktree", "prune", "--expire", "now")
        self.assertIsNotNone(workspace_module._worktree_record(self.repo, checkout))
        added.unlink()
        recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_removes_an_ignored_cache_present_before_the_first_delete(self):
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignored cache")
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        cache = checkout / "__pycache__" / "mod.cpython-313.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"ignored cache\x00\xff")
        self.interrupt_reclaim(board, view, checkout, missing=("tracked.txt",), remove_git=True)
        self.assertTrue(cache.is_file())
        self.assertTrue(self.reclaim(board, self.view(board, submitted["runId"]))["removed"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_resumes_under_the_current_owner_after_takeover(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(
            missing=("tracked.txt",), remove_git=True)
        view = self.view(board, submitted["runId"])
        old_control = self.control(view)
        with board.store.db.read() as connection:
            before = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=?", (view["runId"],),
            ).fetchone())
        taken = board.call("workflow_takeover", {
            "runId": view["runId"], "commandId": "reclaim-takeover",
            "expectedOwnerGeneration": view["ownerGeneration"], "newHostId": "host-2", **old_control,
        })
        self.controls[view["runId"]] = dict(taken["control"])
        with self.assertRaises(BoardError) as stale:
            board.call("workflow_reclaim", {"runId": view["runId"], **old_control})
        self.assertEqual(stale.exception.code, "STALE_GENERATION")
        result = self.reclaim(board, self.view(board, view["runId"]))
        self.assertTrue(result["removed"])
        self.assertFalse(result["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        with board.store.db.read() as connection:
            after = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (before["plan_id"],),
            ).fetchone())
        for column in before.keys() - {"state", "revision", "applied_at", "result_json"}:
            self.assertEqual(after[column], before[column], column)
        self.assertEqual(after["state"], "applied")
        self.assertEqual(after["revision"], before["revision"] + 1)

    def test_planned_reclaim_still_refuses_owner_generation_changes(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        planned = board.store.workflow.cleanup_plan({
            "runId": view["runId"], "commandId": "plan-before-takeover",
            "expectedRevision": view["revision"], **self.control(view),
        })
        taken = board.call("workflow_takeover", {
            "runId": view["runId"], "commandId": "planned-reclaim-takeover",
            "expectedOwnerGeneration": view["ownerGeneration"], "newHostId": "host-2", **self.control(view),
        })
        current = self.view(board, view["runId"])
        with self.assertRaises(BoardError) as refused:
            board.store.workflow.cleanup_apply({
                "runId": view["runId"], "commandId": "apply-after-takeover",
                "planId": planned["plan"]["planId"], "confirmPath": str(checkout),
                "expectedRevision": current["revision"], **taken["control"],
            })
        self.assertEqual(refused.exception.details["reasons"], ["review-changed"])
        self.assertTrue(checkout.is_dir())
        self.assertEqual(self.view(board, view["runId"])["cleanup"]["state"], "planned")

    def test_started_reclaim_accepts_relative_worktree_backlinks(self):
        git(self.repo, "config", "worktree.useRelativePaths", "true")
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(
            missing=("tracked.txt",), remove_git=True)
        git_dir = next(path for path in (self.repo / ".git" / "worktrees").iterdir()
                       if workspace_module._identity(path) == manifest["checkoutId"])
        backlink = Path((git_dir / "gitdir").read_text(encoding="utf-8").strip())
        self.assertFalse(backlink.is_absolute())
        self.assertEqual((git_dir / backlink).resolve(), checkout / ".git")
        self.assertTrue(self.reclaim(board, self.view(board, submitted["runId"]))["removed"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_refuses_a_foreign_lock_then_resumes_after_unlock(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim(missing=("tracked.txt",))
        git(self.repo, "worktree", "lock", "--reason", "someone else's lock", str(checkout))
        self.assert_started_reclaim_refused(board, submitted["runId"], checkout, "lock-mismatch")
        self.assertEqual(workspace_module._worktree_record(self.repo, checkout)["locked"], "someone else's lock")
        git(self.repo, "worktree", "unlock", str(checkout))
        self.assertTrue(self.reclaim(board, self.view(board, submitted["runId"]))["removed"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_refuses_files_without_a_registration(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim()
        git_dir = Path(workspace_module.inspect(str(checkout))["gitDir"])
        shutil.rmtree(git_dir)
        self.assertIsNone(workspace_module._worktree_record(self.repo, checkout))
        self.assert_started_reclaim_refused(board, submitted["runId"], checkout, "unregistered-checkout")

    def test_started_reclaim_removes_an_empty_directory_without_a_registration(self):
        board, submitted, manifest, checkout, artifact = self.interrupted_reclaim()
        git_dir = Path(workspace_module.inspect(str(checkout))["gitDir"])
        shutil.rmtree(git_dir)
        shutil.rmtree(checkout)
        checkout.mkdir()
        self.assertIsNone(workspace_module._worktree_record(self.repo, checkout))
        result = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(result["removed"])
        self.assertFalse(result["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")
        self.assertEqual(self.reclaim(board, self.view(board, submitted["runId"])), result)

    def test_a_planned_reclaim_still_refuses_missing_sealed_files(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        planned = board.store.workflow.cleanup_plan({
            "runId": submitted["runId"], "commandId": "plan-before-missing",
            "expectedRevision": view["revision"], **self.control(view),
        })
        self.assertEqual(planned["plan"]["state"], "planned")
        (checkout / "tracked.txt").unlink()
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("refused deletion")):
            with self.assertRaises(BoardError) as refused:
                board.store.workflow.cleanup_apply({
                    "runId": submitted["runId"], "commandId": "apply-after-missing",
                    "planId": planned["plan"]["planId"], "confirmPath": str(checkout),
                    "expectedRevision": planned["targetRevision"], **self.control(view),
                })
        self.assertEqual(refused.exception.code, "NOT_READY")
        self.assertEqual(refused.exception.details["reasons"], ["unsealed-changes"])
        self.assertEqual(refused.exception.details["unsealedPaths"], ["tracked.txt"])
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "blocked")
        self.assertTrue(checkout.is_dir())
        self.assertEqual(workspace_module._worktree_record(self.repo, checkout)["locked"],
                         "hey-my-buddy:" + manifest["workspaceId"])

    def test_an_attached_branch_blocks_removal_with_the_branch_and_detach_command(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="attached-branch")
        run_id = submitted["runId"]
        # What the Worker did in the incident: created and switched to a branch in
        # the managed checkout. Registration and the allocation lock stay correct.
        git(checkout, "switch", "-q", "-c", "socu/worker-branch")
        branch_commit = self.branch_ref("socu/worker-branch")
        head = git(checkout, "rev-parse", "HEAD").strip()

        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "commandId": "plan-attached", "expectedRevision": view["revision"],
            **self.control(view),
        })
        plan = planned["plan"]
        self.assertEqual(plan["state"], "planned")
        self.assertNotIn("attached-branch", plan["reasons"])
        # The actual branch is first examined by apply; its diagnosis is then
        # persisted on the plan that this planning call created.
        with self.assertRaises(BoardError) as blocked:
            self.reclaim(board, self.view(board, run_id))
        self.assertEqual(blocked.exception.code, "NOT_READY")
        self.assertIn("attached-branch", blocked.exception.details["reasons"])
        self.assertEqual(blocked.exception.details["reclaim"]["attachedBranch"]["branch"], "socu/worker-branch")
        blocked_plan = self.view(board, run_id)["cleanup"]
        self.assertEqual(blocked_plan["planId"], plan["planId"])
        self.assertEqual(blocked_plan["state"], "blocked")
        self.assertIn("attached-branch", blocked_plan["reasons"])
        diagnosis = blocked_plan["evidence"]["workspace"]["attachedBranch"]
        self.assertEqual(diagnosis["branch"], "socu/worker-branch")
        self.assertEqual(diagnosis["ref"], "refs/heads/socu/worker-branch")
        self.assertEqual(diagnosis["headCommit"], head)
        self.assertTrue(diagnosis["detachCommand"].startswith("git -C '"))
        self.assertIn("switch --detach", diagnosis["detachCommand"])
        self.assertIn(head, diagnosis["detachCommand"])
        self.assertNotIn("note", diagnosis)
        self.assertNotIn("retry", diagnosis)
        # The direct removal refuses with the same facts and touches nothing.
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        self.assertIn("socu/worker-branch", raised.exception.message)
        self.assertEqual(raised.exception.details["attachedBranch"]["branch"], "socu/worker-branch")
        # The board never detached, deleted or moved anything.
        self.assertTrue(checkout.exists())
        self.assertEqual(self.branch_ref("socu/worker-branch"), branch_commit)
        self.assertEqual(git(checkout, "rev-parse", "--abbrev-ref", "HEAD").strip(), "socu/worker-branch")

    def test_after_the_recorded_detach_the_reclaim_completes(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="detach-then-reclaim")
        run_id = submitted["runId"]
        git(checkout, "switch", "-q", "-c", "socu/worker-branch")
        with self.assertRaises(BoardError) as blocked:
            self.reclaim(board, self.view(board, run_id))
        self.assertIn("attached-branch", blocked.exception.details["reasons"])
        command = blocked.exception.details["reclaim"]["attachedBranch"]["detachCommand"]
        completed = subprocess.run(shlex.split(command), cwd=self.repo,
                                   env={**os.environ, **GIT_ENV}, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(git(checkout, "rev-parse", "HEAD").strip(),
                         blocked.exception.details["reclaim"]["attachedBranch"]["headCommit"])
        reclaimed = self.reclaim(board, self.view(board, run_id))
        self.assertTrue(reclaimed["removed"])
        self.assertFalse(checkout.exists())
        # The branch and its commit stay exactly where they were: deleting them is
        # the Host's decision, never the board's.
        self.assertTrue(self.branch_ref("socu/worker-branch"))

    def test_apply_reports_the_same_branch_diagnostic_as_the_inspection(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="apply-attached")
        run_id = submitted["runId"]
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "commandId": "plan-before-branch", "expectedRevision": view["revision"],
            **self.control(view),
        })
        self.assertEqual(planned["plan"]["state"], "planned")
        # The branch appears between planning and applying.
        git(checkout, "switch", "-q", "-c", "socu/late-branch")
        branch_commit = self.branch_ref("socu/late-branch")
        refs_before = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/")
        checkout_files_before = {path.relative_to(checkout): path.read_bytes()
                                 for path in checkout.rglob("*") if path.is_file() and ".git" not in path.parts}
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.cleanup_apply({
                "runId": run_id, "planId": planned["plan"]["planId"], "commandId": "apply-attached",
                "expectedRevision": planned["targetRevision"], "confirmPath": planned["plan"]["path"],
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("attached-branch", raised.exception.details["reasons"])
        self.assertEqual(raised.exception.details["attachedBranch"]["branch"], "socu/late-branch")
        blocked = self.view(board, run_id)["cleanup"]
        self.assertEqual(blocked["planId"], planned["plan"]["planId"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("attached-branch", blocked["reasons"])
        self.assertEqual(blocked["evidence"]["workspace"]["attachedBranch"]["branch"], "socu/late-branch")
        self.assertTrue(blocked["retention"]["fixedRefs"])
        self.assertTrue(checkout.exists())
        self.assertEqual(self.branch_ref("socu/late-branch"), branch_commit)
        self.assertEqual(git(checkout, "rev-parse", "--abbrev-ref", "HEAD").strip(), "socu/late-branch")
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/"),
                         refs_before)
        self.assertEqual({path.relative_to(checkout): path.read_bytes()
                          for path in checkout.rglob("*") if path.is_file() and ".git" not in path.parts},
                         checkout_files_before)

    def test_a_missing_registration_and_a_foreign_lock_keep_their_own_reasons(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="own-reasons")
        run_id = submitted["runId"]
        # A lock that belongs to someone else is a lock mismatch, not an
        # "unregistered" claim and not a branch claim.
        git(self.repo, "worktree", "unlock", str(checkout))
        with self.assertRaises(BoardError) as mismatch:
            self.reclaim(board, self.view(board, run_id))
        self.assertIn("lock-mismatch", mismatch.exception.details["reasons"])
        self.assertNotIn("unregistered-checkout", mismatch.exception.details["reasons"])
        self.assertNotIn("attached-branch", mismatch.exception.details["reasons"])
        git(self.repo, "worktree", "lock", "--reason", "hey-my-buddy:someone-else", str(checkout))
        with self.assertRaises(BoardError) as foreign:
            self.reclaim(board, self.view(board, run_id))
        self.assertIn("lock-mismatch", foreign.exception.details["reasons"])
        # A registration Git no longer lists at all keeps its own reason too.
        git(self.repo, "worktree", "unlock", str(checkout))
        with mock.patch.object(workspace_module, "_worktree_record", return_value=None):
            inspection = workspace_module.cleanup_inspect(self.directory, manifest)
        self.assertIn("unregistered-checkout", inspection["reasons"])
        self.assertNotIn("lock-mismatch", inspection["reasons"])
        self.assertNotIn("attached-branch", inspection["reasons"])
        self.assertIsNone(inspection["attachedBranch"])
