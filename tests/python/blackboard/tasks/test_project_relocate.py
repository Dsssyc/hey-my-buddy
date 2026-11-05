"""The operations command records a marked Git location without lifecycle actions."""
from __future__ import annotations

from pathlib import Path
import shutil
from unittest.mock import patch

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.blackboard.tasks import macro_task_deletion, workspace
from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
from hey_my_buddy.errors import BoardError


class ProjectRelocateTests(LifecycleTestCase):
    def test_relocate_enables_preview_and_deletion_without_a_new_submission(self):
        board, submitted, manifest, checkout, _artifact, _accepted = self.accepted_worktree()
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", prefix).splitlines()
        moved = self.directory / "moved-for-deletion"
        self.repo.rename(moved)
        board.call("project_relocate", {"cwd": str(moved)})
        target = "run:" + submitted["runId"]
        preview = macro_task_deletion.preview(board.store, target)
        self.assertEqual(preview["scope"]["gitRefs"], len(refs))
        with patch.object(macro_task_deletion, "_delete_sessions", return_value=[]):
            result = macro_task_deletion.delete(board.store, target, 1)
        self.assertEqual(result["state"], "deleted", result)
        self.assertFalse(checkout.exists())
        self.assertEqual(git(moved, "for-each-ref", "--format=%(refname)", prefix), "")

    def test_missing_allocation_resume_uses_the_relocated_repository(self):
        board, submitted, manifest, checkout, _artifact = self.interrupted_reclaim()
        git(self.repo, "worktree", "lock", "--reason", "hey-my-buddy:" + manifest["workspaceId"], str(checkout))
        shutil.rmtree(checkout.parent)
        moved = self.directory / "moved-during-reclaim"
        self.repo.rename(moved)
        self.assertIsNotNone(workspace._worktree_record(moved, checkout))
        board.call("project_relocate", {"cwd": str(moved)})
        Reclamation(board.store).run()
        with board.store.db.read() as connection:
            state = connection.execute("SELECT state FROM workspace_cleanup_plans WHERE run_id=?",
                                       (submitted["runId"],)).fetchone()[0]
        self.assertEqual(state, "applied")
        self.assertIsNone(workspace._worktree_record(moved, checkout))

    def test_relocate_from_a_subdirectory_records_the_main_root_and_enables_reclaim(self):
        board, submitted, manifest, checkout, _artifact, accepted = self.accepted_worktree()
        manifest_path = Path(workspace.manifest_file_reference(self.directory, manifest)["path"])
        frozen = manifest_path.read_bytes()
        moved = self.directory / "moved"
        self.repo.rename(moved)
        nested = moved / "nested"
        nested.mkdir()
        self.assertEqual(self.reclaim(board, accepted)["reasons"], ["repository-not-found"])
        with patch.object(board.store.workflow, "_reclaim_checkout", side_effect=AssertionError("relocate reclaimed")), \
                patch.object(board.store, "task_cancel", side_effect=AssertionError("relocate stopped")):
            result = board.call("project_relocate", {"cwd": str(nested)})
        self.assertEqual(result, {"project": {"id": manifest["projectId"], "kind": "git",
                                            "path": str(moved), "label": "moved"}})
        self.assertTrue(checkout.exists())
        self.assertEqual(manifest_path.read_bytes(), frozen)
        Reclamation(board.store).run()
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")

    def test_relocation_does_not_change_active_attempt_ownership_or_repair_its_checkout(self):
        board = self.board()
        submitted = self.submit_run(board, {"cwd": str(self.repo), "title": "Active relocation",
                                          "task": "Stay active", "checkout": "worktree",
                                          "access": "write", "writeScope": ["."]})
        claim = self.claim(board, submitted["runId"])["claim"]
        checkout = Path(claim["task"]["cwd"])
        pointer = (checkout / ".git").read_bytes()
        tables = ("tasks", "attempts", "workflow_runs", "agent_credentials", "workspace_reservations")
        with board.store.db.read() as connection:
            before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                      for table in tables}
        moved = self.directory / "active-moved"
        self.repo.rename(moved)
        board.call("project_relocate", {"cwd": str(moved)})
        Reclamation(board.store).run()
        with board.store.db.read() as connection:
            after = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                     for table in tables}
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_cleanup_plans").fetchone()[0], 0)
        self.assertEqual(after, before)
        self.assertTrue(checkout.is_dir())
        self.assertEqual((checkout / ".git").read_bytes(), pointer)

    def test_folder_and_unmarked_git_are_refused_without_creating_a_marker(self):
        board = self.board()
        folder = self.directory / "folder"
        folder.mkdir()
        created = board.call("workflow_create", {"cwd": str(folder), "title": "Folder", "goal": "Use folder"})
        head = board.store.head()
        for cwd in (folder, self.repo):
            with self.subTest(cwd=cwd), self.assertRaises(BoardError) as raised:
                board.call("project_relocate", {"cwd": str(cwd)})
            self.assertEqual(raised.exception.code, "PROJECT_MARKER_REQUIRED")
            self.assertIn("ordinary folder", raised.exception.message)
        self.assertIsNone(workspace.read_project_id(str(self.repo / ".git")))
        self.assertEqual(board.store.head(), head)
        self.assertEqual(board.call("workflow_get", {"macroTaskId": created["macroTaskId"]})["project"]["path"], str(folder))

    def test_a_marked_project_from_another_board_is_refused(self):
        board = self.board()
        identifier = workspace.ensure_project_id(str(self.repo / ".git"))
        with self.assertRaises(BoardError) as raised:
            board.call("project_relocate", {"cwd": str(self.repo)})
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        self.assertEqual(raised.exception.details["projectId"], identifier)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)

    def test_service_validates_cwd_and_rejects_project_id(self):
        board = self.board()
        for params in ({}, {"cwd": "relative"}, {"cwd": None},
                       {"cwd": str(self.repo), "projectId": "forged"}):
            with self.subTest(params=params), self.assertRaises(BoardError) as raised:
                board.call("project_relocate", params)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
