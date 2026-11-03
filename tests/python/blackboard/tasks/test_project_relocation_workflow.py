"""Real Git coverage for repository relocation across Host lifecycle operations."""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, manifest_ref, record_reference, artifact_manifest

from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow_real import BUDDY
from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.errors import BoardError


class ProjectRelocationWorkflowTests(LifecycleTestCase):
    def _move_repository(self, name="moved-repository") -> Path:
        moved = self.directory / name
        self.repo.rename(moved)
        self.repo = moved
        return moved

    def test_continue_requires_a_repository_candidate_before_recording(self):
        board = self.board()
        self.register(board)
        submitted, _claim, manifest, checkout, _seal, awaiting, _artifact = self.run_worktree(
            board, request_id="relocate-continue", disposition="assistance")
        original_manifest = manifest.copy()
        moved = self._move_repository()
        request = {"runId": submitted["runId"], "commandId": "continue-after-move",
                   "expectedRevision": awaiting["revision"], "input": "finish the work",
                   **self.control(awaiting)}
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_continue", request)
        self.assertEqual(caught.exception.code, "WORKSPACE_REPOSITORY_NOT_FOUND")
        with board.store.db.read() as connection:
            row = connection.execute("SELECT revision FROM workflow_runs WHERE run_id=?",
                                     (submitted["runId"],)).fetchone()
            self.assertEqual(row["revision"], awaiting["revision"])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?", (submitted["runId"],)
            ).fetchone()[0], 0)
        continued = board.call("workflow_continue", {**request, "cwd": str(moved)})
        self.assertEqual(continued["state"], "executing")
        self.assertTrue(checkout.exists())
        self.assertEqual(original_manifest["manifestSha256"], manifest["manifestSha256"])
        other_location = self.workdir("unused-replay-location")
        with patch("hey_my_buddy.blackboard.tasks.workspace.locate_repository",
                   side_effect=AssertionError("receipt replay performed Git relocation")):
            replay = board.call("workflow_continue", {**request, "cwd": str(other_location)})
        self.assertTrue(replay["duplicate"])

    def test_reclaim_missing_repository_preserves_plan_then_uses_request_candidate(self):
        board, submitted, manifest, checkout, _artifact, accepted = self.accepted_worktree(
            request_id="relocate-reclaim")
        moved = self._move_repository()
        with self.assertRaises(BoardError) as caught:
            self.reclaim(board, accepted)
        self.assertEqual(caught.exception.code, "WORKSPACE_REPOSITORY_NOT_FOUND")
        self.assertEqual(caught.exception.details["reasons"], ["repository-not-found"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workspace_cleanup_plans WHERE run_id=?", (submitted["runId"],)
            ).fetchone()[0], 0)
        self.assertEqual(self.view(board, submitted["runId"])["revision"], accepted["revision"])
        planned = board.call("storage_plan", {})
        blocked = next(row for row in planned["candidates"] if row["path"] == str(checkout))
        self.assertEqual(blocked["reasons"], ["repository-not-found"])
        self.assertFalse(blocked["eligible"])
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        Reclamation(board.store).run()
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM service_issues").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workspace_cleanup_plans WHERE run_id=?", (submitted["runId"],)
            ).fetchone()[0], 0)
        result = self.reclaim(board, accepted, cwd=str(moved))
        self.assertTrue(result["removed"])
        self.assertFalse(checkout.exists())

    def test_accept_commits_then_reports_missing_repository_and_retries(self):
        board = self.board()
        self.register(board)
        submitted, _claim, _manifest, checkout, _seal, delivered, artifact = self.run_worktree(
            board, request_id="relocate-accept-reclaim")
        moved = self._move_repository()
        accepted = self.accept(board, delivered, artifact, not_required=True)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["reclaim"]["reasons"], ["repository-not-found"])
        self.assertTrue(checkout.exists())
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM tasks WHERE task_id=? AND accepted_at IS NOT NULL", (submitted["runId"],)).fetchone())
        reclaimed = self.reclaim(board, self.view(board, submitted["runId"]), cwd=str(moved))
        self.assertTrue(reclaimed["removed"])
        self.assertFalse(checkout.exists())

    def test_known_project_location_reclaims_without_request_candidate(self):
        board, submitted, manifest, checkout, _artifact, accepted = self.accepted_worktree(
            request_id="relocate-known")
        moved = self._move_repository()
        later = board.call("workflow_submit", {
            **BUDDY, "requestId": "new-location-evidence", "hostId": "host-1",
            "task": "record the new project location", "cwd": str(moved),
            "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]},
        })
        self.controls[later["runId"]] = later["control"]
        from hey_my_buddy.blackboard.tasks.workflow import known_repository_paths
        with board.store.db.write() as connection:
            connection.execute("UPDATE events SET created_at='2000-01-01T00:00:00Z' WHERE task_id=?",
                               (later["runId"],))
        with board.store.db.read() as connection:
            self.assertEqual(known_repository_paths(connection, manifest)[0], str(moved / ".git"))
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        Reclamation(board.store).run()
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")
        self.assertFalse(checkout.exists())

    def test_macro_deletion_uses_the_projects_new_sealed_location(self):
        from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion, workspace
        board = self.board()
        self.register(board)
        owner = board.call("workflow_submit", {
            **BUDDY, "requestId": "moved-macro-owner", "cwd": str(self.repo), "hostId": "host-1",
            "task": "Original project task", "submissionToken": "o" * 32,
            "macroTask": {"title": "Original project task", 'goal': "Original project task"},
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[owner["runId"]] = owner["control"]
        claimed = self.claim(board, owner["runId"])
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "tracked.txt").write_text("macro output\n", encoding="utf-8")
        seal = workspace.seal(self.directory, manifest, owner["runId"], claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, owner["runId"])
        artifact = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        with patch.object(board.store.workflow, "_reclaim_checkout",
                          return_value={"removed": False, "reasons": ["fixture-retained"]}):
            self.accept(board, delivered, artifact, not_required=True)
        moved = self._move_repository()
        later = board.call("workflow_submit", {
            **BUDDY, "requestId": "new-location-for-macro-delete", "cwd": str(moved), "hostId": "host-1",
            "task": "Use the project at its new location", "submissionToken": "l" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read", "writeScope": ["."]},
        })
        # Native session deletion is outside this Git/blackboard integration.
        with patch.object(deletion, "_delete_sessions", return_value=[]):
            result = deletion.delete(board.store, owner["macroTaskId"], 1)
        self.assertEqual(result["state"], "deleted", result)
        self.assertFalse(checkout.exists())
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        self.assertFalse(git(moved, "for-each-ref", "--format=%(refname)", prefix))
        self.assertEqual(self.view(board, later["runId"])["state"], "executing")
        self.assertEqual(workspace.read_project_id(moved / ".git"), manifest["projectId"])

    def test_accept_target_at_moved_location_and_join_original_macro_task(self):
        board = self.board()
        self.register(board)
        submitted, _claim, _manifest, _checkout, _seal, delivered, artifact = self.run_worktree(
            board, request_id="relocate-target")
        target = self.target_with_artifact(artifact)
        target_path = self.directory / "moved-target"
        target["path"].rename(target_path)
        target["path"] = target_path
        with patch.object(board.store.workflow, "_reclaim_checkout",
                          return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = self.accept(board, delivered, artifact, target=target)
        self.assertEqual(accepted["integration"]["target"]["path"], str(target_path))
        self.assertIsNone(accepted["integration"]["target"]["projectId"])

        first = board.call("workflow_submit", {
            **BUDDY, "requestId": "macro-before-move", "hostId": "host-1",
            "task": "first member", "cwd": str(self.repo), "submissionToken": "u" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read", "writeScope": ["."]},
            "macroTask": {"title": "Relocated project", 'goal': "Relocated project"},
        })
        self.controls[first["runId"]] = first["control"]
        moved = self._move_repository()
        second = board.call("workflow_submit", {
            **BUDDY, "requestId": "macro-after-move", "hostId": "host-1",
            "task": "second member", "cwd": str(moved), "submissionToken": "v" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read", "writeScope": ["."]},
            "macroTaskId": first["macroTaskId"],
        })
        self.assertEqual(second["macroTaskId"], first["macroTaskId"])

    def test_accept_repairs_source_link_from_moved_project_target(self):
        board = self.board()
        self.register(board)
        submitted, _claim, manifest, checkout, _seal, delivered, artifact = self.run_worktree(
            board, request_id="relocate-accept-source")
        moved = self._move_repository()
        git(moved, "apply", "--index", "--binary", artifact["diffPath"])
        git(moved, "commit", "-qm", "integrate managed output")
        with patch.object(board.store.workflow, "_reclaim_checkout",
                          return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = self.accept(board, delivered, artifact,
                                   target={"path": moved, "ref": "HEAD"})
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["target"]["projectId"], manifest["projectId"])
        self.assertTrue(checkout.exists())

    def test_accept_existing_source_after_repository_moves_to_target_path(self):
        from hey_my_buddy.blackboard.tasks import workspace
        board = self.board()
        self.register(board)
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "relocate-existing-source", "hostId": "host-1",
            "task": "Change the existing checkout", "cwd": str(self.repo),
            "submissionToken": "e" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"])
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        (self.repo / "tracked.txt").write_text("integrated existing output\n", encoding="utf-8")
        seal = workspace.seal(self.directory, manifest, submitted["runId"],
                              claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, submitted["runId"])
        artifact = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-qm", "integrate existing output")
        moved = self._move_repository()
        accepted = self.accept(board, delivered, artifact, target={"path": moved, "ref": "HEAD"})
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["target"]["path"], str(moved))
        self.assertEqual(accepted["integration"]["target"]["projectId"], manifest["projectId"])

