"""Folder identity survives Git initialization at the same canonical path."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.blackboard.tasks import projects, workspace
from hey_my_buddy.blackboard.tasks import macro_task_deletion
from hey_my_buddy.cli.main import _creation_key
from support import BoardTestCase, macro_summaries


class ProjectIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def git(self, cwd: Path, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    def test_folder_identity_survives_git_init_and_git_move(self):
        original = self.directory / "papers"
        original.mkdir()
        folder = projects.identify(str(original))
        self.assertEqual(folder["projectKind"], "folder")
        self.assertEqual(folder["projectId"], projects.folder_id(str(original.resolve())))

        self.git(original, "init", "-q")
        converted = projects.identify(str(original), folder_project_id=folder["projectId"])
        self.assertEqual(converted["projectKind"], "git")
        self.assertEqual(converted["projectId"], folder["projectId"])
        self.assertEqual(workspace.read_project_id(converted["repositoryPath"]), folder["projectId"])

        moved = self.directory / "renamed"
        original.rename(moved)
        self.assertEqual(projects.identify(str(moved))["projectId"], folder["projectId"])

    def test_unmarked_folder_move_is_a_new_project_and_existing_git_marker_wins(self):
        original = self.directory / "notes"
        original.mkdir()
        before = projects.identify(str(original))
        moved = self.directory / "notes-renamed"
        original.rename(moved)
        self.assertNotEqual(projects.identify(str(moved))["projectId"], before["projectId"])

        self.git(moved, "init", "-q")
        repository = workspace.repository_at(moved, allow_parent=True)
        marked = workspace.ensure_project_id(repository, preferred="a" * 64)
        self.assertEqual(marked, "a" * 64)
        self.assertEqual(projects.identify(str(moved))["projectId"], marked)

    def test_new_git_repository_does_not_reuse_an_unrecorded_folder_id(self):
        folder = self.directory / "new-repository"
        folder.mkdir()
        former_folder_id = projects.identify(str(folder))["projectId"]
        self.git(folder, "init", "-q")
        self.assertNotEqual(projects.identify(str(folder))["projectId"], former_folder_id)


class FolderMacroTaskTests(BoardTestCase):
    def test_git_conversion_updates_project_but_keeps_old_macro_kind(self):
        folder = self.workdir("drafts")
        board = self.board()

        def create(title: str):
            request = {"cwd": str(folder), "title": title, "goal": title}
            project_id = board.call("workflow_get", {"cwd": str(folder)})["project"]["id"]
            return board.call("workflow_create", {**request,
                               "creationKey": _creation_key(["adr032-create-v1", project_id, title])})["macroTaskId"]

        before = create("Before Git")
        folder_project = board.call("workflow_get", {"macroTaskId": before})["project"]
        self.assertEqual(folder_project["kind"], "folder")

        subprocess.run(["git", "-C", str(folder), "init", "-q"], check=True, capture_output=True)
        converted = board.call('workflow_get', {'cwd': str(folder)})['project']
        self.assertEqual(converted['kind'], 'git')
        self.assertEqual(converted['id'], folder_project['id'])
        after = create("After Git")
        self.assertEqual(board.call("workflow_get", {"macroTaskId": before})["project"]["kind"], "folder")
        self.assertEqual(board.call("workflow_get", {"macroTaskId": after})["project"]["kind"], "git")
        project = board.call("workflow_get", {"cwd": str(folder)})["project"]
        self.assertEqual(project["id"], folder_project["id"])
        self.assertEqual(project["kind"], "git")

    def test_empty_folder_macro_is_readable_and_deletable_without_touching_cwd(self):
        folder = self.workdir("paper-project")
        source = folder / "source.txt"
        source.write_text("preserve this source\n", encoding="utf-8")
        board = self.board()
        request = {"cwd": str(folder), "title": "Revise paper", "goal": "Review evidence"}
        project_id = board.call("workflow_get", {"cwd": str(folder)})["project"]["id"]
        created = board.call("workflow_create", {**request,
                         "creationKey": _creation_key(["adr032-create-v1", project_id, request["title"]])})
        identifier = created["macroTaskId"]

        macro = board.call("workflow_get", {"macroTaskId": identifier})
        self.assertEqual(macro["memberCount"], 0)
        self.assertEqual(macro["project"]["kind"], "folder")
        project = board.call("workflow_get", {"cwd": str(folder)})
        self.assertEqual(project["project"]["id"], macro["project"]["id"])
        self.assertEqual(project["items"][0]["macroTaskId"], identifier)
        self.assertIn(identifier, [item["macroTaskId"] for item in macro_summaries(
            board.call("macro_task_list", {}))])

        preview = macro_task_deletion.preview(board.store, identifier)
        self.assertEqual(preview["microTaskCount"], 0)
        deleted = macro_task_deletion.delete(board.store, identifier, 0)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(source.read_text(encoding="utf-8"), "preserve this source\n")
