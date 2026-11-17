"""Folder identity survives Git initialization at the same canonical path."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.tasks import projects, workspace
from hey_my_buddy.blackboard.tasks import macro_task_deletion
from support import BoardTestCase, macro_summaries
from blackboard.tasks.test_workflow import WorkflowTestCase


class ProjectIdentityTests(BoardTestCase):
    def git(self, cwd: Path, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    def create(self, board, cwd: Path, title: str) -> dict:
        created = board.call("workflow_create", {"cwd": str(cwd), "title": title, "goal": title})
        return board.call("workflow_get", {"macroTaskId": created["macroTaskId"]})

    def test_folder_identity_survives_git_init_and_git_move(self):
        original = self.workdir("papers")
        board = self.board()
        folder = self.create(board, original, "Folder")["project"]
        self.assertEqual(folder["kind"], "folder")
        self.assertEqual(folder["id"], projects.folder_id(str(original.resolve())))

        self.git(original, "init", "-q")
        converted = self.create(board, original, "Git")["project"]
        self.assertEqual(converted["kind"], "git")
        self.assertEqual(converted["id"], folder["id"])
        self.assertEqual(workspace.read_project_id(workspace.repository_at(original, allow_parent=True)), folder["id"])

        moved = self.directory / "renamed"
        original.rename(moved)
        self.assertEqual(self.create(board, moved, "Moved")["project"]["id"], folder["id"])

    def test_unmarked_folder_move_is_a_new_project_and_existing_git_marker_wins(self):
        original = self.workdir("notes")
        board = self.board()
        before = self.create(board, original, "Before")["project"]
        moved = self.directory / "notes-renamed"
        original.rename(moved)
        self.assertNotEqual(self.create(board, moved, "Moved")["project"]["id"], before["id"])

        self.git(moved, "init", "-q")
        repository = workspace.repository_at(moved, allow_parent=True)
        marked = workspace.ensure_project_id(repository, preferred="a" * 64)
        self.assertEqual(marked, "a" * 64)
        self.assertEqual(self.create(board, moved, "Marked")["project"]["id"], marked)

    def test_new_git_repository_does_not_reuse_an_unrecorded_folder_id(self):
        folder = self.workdir("new-repository")
        former_folder_id = projects.folder_id(str(folder.resolve()))
        self.git(folder, "init", "-q")
        self.assertNotEqual(self.create(self.board(), folder, "New")["project"]["id"], former_folder_id)

    def test_git_project_path_is_main_repository_across_worktrees(self):
        main = self.workdir("main-project")
        self.git(main, "init", "-q")
        (main / "source.txt").write_text("source\n", encoding="utf-8")
        self.git(main, "add", "source.txt")
        self.git(main, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                 "commit", "-qm", "Initial")
        linked = self.directory / "linked-worktree"
        self.git(main, "worktree", "add", "-qb", "linked", str(linked))
        board = self.board()
        first = self.create(board, main, "Main")["project"]
        second = self.create(board, linked, "Linked")["project"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["label"], main.name)
        self.assertEqual(second["label"], main.name)
        by_cwd = board.call("workflow_get", {"cwd": str(linked)})["project"]
        self.assertEqual(by_cwd["path"], str(main.resolve()))
        self.assertEqual(by_cwd["label"], main.name)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT path FROM projects WHERE project_id=?",
                                        (first["id"],)).fetchone()["path"], str(main.resolve()))


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
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM projects WHERE project_id=?",
                                         (macro["project"]["id"],)).fetchone())


class ProjectDeletionTests(WorkflowTestCase):
    def test_non_last_and_last_grouped_project_rows(self):
        folder = self.workdir("grouped-project")
        board = self.board()
        first = board.call("workflow_create", {"cwd": str(folder), "title": "First", "goal": "First"})
        second = board.call("workflow_create", {"cwd": str(folder), "title": "Second", "goal": "Second"})
        identifier = board.call("workflow_get", {"macroTaskId": first["macroTaskId"]})["project"]["id"]
        self.assertEqual(board.call("workflow_get", {"macroTaskId": second["macroTaskId"]})["project"]["id"],
                         identifier)
        macro_task_deletion.delete(board.store, first["macroTaskId"], 0)
        with board.store.db.read() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM projects WHERE project_id=?",
                                            (identifier,)).fetchone())
        macro_task_deletion.delete(board.store, second["macroTaskId"], 0)
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM projects WHERE project_id=?",
                                         (identifier,)).fetchone())

    def test_standalone_and_grouped_references_prune_after_last_delete(self):
        folder = self.workdir("mixed-project")
        board = self.board()
        macro = board.call("workflow_create", {"cwd": str(folder), "title": "Group", "goal": "Group"})
        run = self.submit(board, request_id="standalone-project", cwd=str(folder))
        identifier = board.call("workflow_get", {"macroTaskId": macro["macroTaskId"]})["project"]["id"]
        self.assertEqual(run["project"]["id"], identifier)
        board.call("workflow_cancel", {"runId": run["runId"]})
        board.call("workflow_conclude", {"runId": run["runId"], "note": "Settled fixture"})
        macro_task_deletion.delete(board.store, macro["macroTaskId"], 0)
        with board.store.db.read() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM projects WHERE project_id=?",
                                            (identifier,)).fetchone())
        with mock.patch.object(macro_task_deletion, "_delete_workspaces", return_value=[]):
            deleted = macro_task_deletion.delete(board.store, "run:" + run["runId"], 1)
        self.assertEqual(deleted["state"], "deleted", deleted)
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM projects WHERE project_id=?",
                                         (identifier,)).fetchone())
