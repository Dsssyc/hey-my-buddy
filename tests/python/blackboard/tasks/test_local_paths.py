"""Native path comparisons at workspace and workflow boundaries."""
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy import paths
from hey_my_buddy.blackboard.store import store
from hey_my_buddy.blackboard.tasks import micro_task, workflow, workspace
from hey_my_buddy.errors import BoardError


class WindowsPath(PureWindowsPath):
    def resolve(self):
        # Git records are absolute; this fixture has no filesystem aliases.
        return self


class LocalPathTests(unittest.TestCase):
    def test_resource_overlap_uses_components_and_native_case_rules(self):
        cases = (
            (PureWindowsPath, r"D:\work\project", "d:/WORK/project/sub", "D:/work/project-other",
             "E:/work/project", r"\\server\share\project", "//SERVER/share/project/sub"),
            (PurePosixPath, "/work/project", "/work/project/sub", "/work/project-other",
             "/WORK/project", "/network/share/project", "/network/share/project/sub"),
        )
        for flavor, root, child, sibling, other, share, share_child in cases:
            with self.subTest(platform=flavor.__name__), patch.object(store, "Path", flavor):
                for left, right in ((root, child), (child, root), (root, root), (share, share_child)):
                    self.assertTrue(store._overlaps(left, right), (left, right))
                for left, right in ((root, sibling), (root, other), (root, share)):
                    self.assertFalse(store._overlaps(left, right), (left, right))

    def test_git_worktree_records_use_windows_path_equality(self):
        listing = b"worktree D:/work/project\0locked owner\0\0worktree //server/share/project\0\0"
        with patch.object(workspace, "_git", return_value=listing), patch.object(workspace, "Path", WindowsPath):
            self.assertEqual(workspace._worktree_record("unused", r"d:\WORK\project")["locked"], "owner")
            self.assertIsNotNone(workspace._worktree_record("unused", r"\\SERVER\share\project"))
            self.assertIsNone(workspace._worktree_record("unused", r"D:\work\project-other"))

    def test_git_worktree_record_resolves_local_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            checkout = directory / "checkout"
            checkout.mkdir()
            alias = directory / "alias"
            alias.symlink_to(checkout, target_is_directory=True)
            listing = f"worktree {checkout}\0locked owner\0\0".encode()
            with patch.object(workspace, "_git", return_value=listing):
                self.assertIsNotNone(workspace._worktree_record(directory, alias))

    def test_project_labels_use_native_components(self):
        for flavor, repository, submitted in (
            (PureWindowsPath, "D:\\work\\project\\.git\\", "D:\\work\\submitted\\"),
            (PurePosixPath, "/work/project/.git/", "/work/submitted/"),
        ):
            with self.subTest(platform=flavor.__name__), patch.object(micro_task, "Path", flavor):
                self.assertEqual(micro_task.project_label(submitted, repository), "project")
                self.assertEqual(micro_task.project_label(submitted, None), "submitted")

    def test_acceptance_validates_target_before_reading_the_board(self):
        board = Mock()
        reached_board = RuntimeError("target path passed admission")
        board.db.read.side_effect = reached_board
        coordinator = workflow.WorkflowCoordinator(board)
        for flavor, accepted, rejected in (
            (PureWindowsPath, (r"D:\work\project", r"\\server\share\project"),
             ("project", "D:project", r"\project", "/project")),
            (PurePosixPath, ("/work/project",), ("project", r"D:\work\project", r"\\server\share\project")),
        ):
            with patch.object(paths, "Path", flavor):
                for value in (*accepted, *rejected):
                    params = {"runId": "run", "artifactId": "artifact", "note": "accept",
                              "target": {"path": value, "ref": "refs/heads/main"}}
                    with self.subTest(platform=flavor.__name__, value=value):
                        if value in accepted:
                            with self.assertRaises(RuntimeError) as caught:
                                coordinator.accept(params)
                            self.assertIs(caught.exception, reached_board)
                        else:
                            with self.assertRaises(BoardError) as caught:
                                coordinator.accept(params)
                            self.assertEqual(caught.exception.message, "target.path must be an absolute checkout path")
