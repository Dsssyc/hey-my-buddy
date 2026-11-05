"""Folder identity survives Git initialization at the same canonical path."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.tasks import projects, workspace
from hey_my_buddy.blackboard.tasks import macro_task_deletion
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
    def test_get_unknown_folder_or_unmarked_git_is_read_only(self):
        folder = self.workdir('unknown-project')
        board = self.board()
        expected = {'project': None, 'items': [], 'nextCursor': None}
        with mock.patch.object(workspace, 'ensure_project_id', side_effect=AssertionError('get must not write a marker')):
            self.assertEqual(board.call('workflow_get', {'cwd': str(folder)}), expected)
            subprocess.run(['git', '-C', str(folder), 'init', '-q'], check=True, capture_output=True)
            self.assertEqual(board.call('workflow_get', {'cwd': str(folder)}), expected)
        self.assertIsNone(workspace.read_project_id(workspace.repository_at(folder, allow_parent=True)))
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM macro_tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_get_reads_existing_marker_without_registering_project(self):
        folder = self.workdir('marked-project')
        subprocess.run(['git', '-C', str(folder), 'init', '-q'], check=True, capture_output=True)
        repository = workspace.repository_at(folder, allow_parent=True)
        identifier = workspace.ensure_project_id(repository)
        board = self.board()
        with mock.patch.object(workspace, 'ensure_project_id', side_effect=AssertionError('get must not rewrite a marker')):
            read = board.call('workflow_get', {'cwd': str(folder)})
        self.assertEqual(read['project']['id'], identifier)
        self.assertEqual(read['items'], [])
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)

    def test_git_conversion_updates_project_but_keeps_old_macro_kind(self):
        folder = self.workdir("drafts")
        board = self.board()

        def create(title: str):
            request = {"cwd": str(folder), "title": title, "goal": title}
            return board.call("workflow_create", request)["macroTaskId"]

        before = create("Before Git")
        folder_project = board.call("workflow_get", {"macroTaskId": before})["project"]
        self.assertEqual(folder_project["kind"], "folder")

        subprocess.run(["git", "-C", str(folder), "init", "-q"], check=True, capture_output=True)
        with board.store.db.read() as db:
            project_rows_before = [tuple(row) for row in db.execute('SELECT * FROM projects')]
            event_head = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        converted = board.call('workflow_get', {'cwd': str(folder)})['project']
        self.assertEqual(converted['kind'], 'folder')
        self.assertEqual(converted['id'], folder_project['id'])
        self.assertIsNone(workspace.read_project_id(workspace.repository_at(folder, allow_parent=True)))
        with board.store.db.read() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM projects')], project_rows_before)
            self.assertEqual(db.execute('SELECT MAX(seq) FROM events').fetchone()[0], event_head)
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
        created = board.call("workflow_create", request)
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
