"""Existing-checkout readers share content, with no allocation on rejection."""
import json
from unittest.mock import patch

from blackboard.tasks import test_workflow_preparation as preparation_tests
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.errors import BoardError


class ReadSharingTests(WorkflowTestCase):
    git = preparation_tests.PreparationTests.git
    repository = preparation_tests.PreparationTests.repository
    _repository_template = None

    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace
        self.repo = self.repository("source")

    def reader(self, board, request_id, selectors=(), *, cwd=None):
        return self.submit(board, request_id=request_id, cwd=str(cwd or self.repo),
                           executionWorkspace={"kind": "existing", "access": "read",
                                               "includeUntracked": list(selectors)})

    def manifests(self, board):
        with board.store.db.read() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT workspace_manifest_json FROM workflow_runs ORDER BY rowid")]

    def assert_rejected_without_allocation(self, board, request_id, *, access="read", selectors=(), code="SNAPSHOT_CHANGED", holders=1):
        before_refs = self.git(self.repo, "for-each-ref")
        before_state = sorted(str(path.relative_to(self.directory / "workspaces"))
                              for path in (self.directory / "workspaces").rglob("*"))
        with patch.object(workspace, "_pin", wraps=workspace._pin) as pin, \
                patch.object(workspace, "_workspace_directory", wraps=workspace._workspace_directory) as allocate:
            with self.assertRaises(BoardError) as caught:
                self.submit(board, request_id=request_id, cwd=str(self.repo),
                            executionWorkspace={"kind": "existing", "access": access,
                                                "includeUntracked": list(selectors)})
        self.assertEqual(caught.exception.code, code)
        pin.assert_not_called()
        allocate.assert_not_called()
        self.assertEqual(self.git(self.repo, "for-each-ref"), before_refs)
        self.assertEqual(sorted(str(path.relative_to(self.directory / "workspaces"))
                                for path in (self.directory / "workspaces").rglob("*")), before_state)
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT task_id FROM tasks WHERE request_id=?", (request_id,)).fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_reservations WHERE state='held'").fetchone()[0], holders)

    def test_delivered_unaccepted_reader_shares_unchanged_checkout(self):
        board = self.board()
        first = self.reader(board, "reader-one")
        self.finish_turn(board, self.claim(board, run_id=first["runId"]))
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["state"], "delivered")
        second = self.reader(board, "reader-two")
        manifests = self.manifests(board)
        self.assertEqual(first["workspace"]["checkoutId"], second["workspace"]["checkoutId"])
        self.assertNotEqual(manifests[0]["manifestSha256"], manifests[1]["manifestSha256"])
        with board.store.db.read() as connection:
            holders = connection.execute("SELECT holder_task_id, access FROM workspace_reservations WHERE state='held'").fetchall()
        self.assertEqual({tuple(row) for row in holders}, {(first["runId"], "read"), (second["runId"], "read")})

    def test_changed_worktree_index_head_and_untracked_are_rejected_before_pinning(self):
        board = self.board()
        self.reader(board, "frozen-reader")
        head = self.git(self.repo, "rev-parse", "HEAD")
        tracked = self.repo / "tracked.txt"
        for change in ("worktree", "index", "head", "untracked"):
            with self.subTest(change=change):
                if change == "untracked":
                    (self.repo / "extra.txt").write_text("new input\n", encoding="utf-8", newline="\n")
                elif change == "head":
                    self.git(self.repo, "commit", "--allow-empty", "-qm", "different HEAD, same trees")
                else:
                    tracked.write_text("different input\n", encoding="utf-8", newline="\n")
                    if change == "index":
                        self.git(self.repo, "add", "tracked.txt")
                        tracked.write_text("base\n", encoding="utf-8", newline="\n")
                self.assert_rejected_without_allocation(board, "rejected-" + change)
                self.git(self.repo, "reset", "--hard", head)
                if change == "untracked":
                    (self.repo / "extra.txt").unlink()

    def test_different_untracked_selections_share_the_same_unchanged_checkout(self):
        board = self.board()
        for name in ("one.txt", "two.txt"):
            (self.repo / name).write_text(name, encoding="utf-8", newline="\n")
        self.reader(board, "select-one", ["one.txt"])
        self.reader(board, "select-two", ["two.txt"])
        manifests = self.manifests(board)
        self.assertNotEqual(manifests[0]["inputTree"], manifests[1]["inputTree"])
        self.assertNotEqual(manifests[0]["snapshot"]["executionFingerprint"],
                            manifests[1]["snapshot"]["executionFingerprint"])

    def test_dirty_input_and_staged_trees_share_despite_different_snapshot_commits(self):
        tracked = self.repo / "tracked.txt"
        tracked.write_text("staged input\n", encoding="utf-8", newline="\n")
        self.git(self.repo, "add", "tracked.txt")
        tracked.write_text("working input\n", encoding="utf-8", newline="\n")
        (self.repo / "selected.txt").write_text("selected input\n", encoding="utf-8", newline="\n")
        board = self.board()
        self.reader(board, "dirty-one", ["selected.txt"])
        self.reader(board, "dirty-two", ["selected.txt"])
        first, second = self.manifests(board)
        self.assertEqual(first["inputTree"], second["inputTree"])
        self.assertEqual(first["snapshot"]["stagedTree"], second["snapshot"]["stagedTree"])
        self.assertNotEqual(first["inputCommit"], second["inputCommit"])
        self.assertNotEqual(first["snapshot"]["stagedCommit"], second["snapshot"]["stagedCommit"])

    def test_each_holders_ignored_selection_is_checked(self):
        (self.repo / ".gitignore").write_text("*.ignored\n", encoding="utf-8", newline="\n")
        self.git(self.repo, "add", ".gitignore")
        self.git(self.repo, "commit", "-qm", "ignore caches")
        selected = self.repo / "selected.ignored"
        selected.write_text("frozen\n", encoding="utf-8", newline="\n")
        board = self.board()
        self.reader(board, "omit-ignored")
        self.reader(board, "select-ignored", ["selected.ignored"])
        selected.write_text("changed\n", encoding="utf-8", newline="\n")
        self.assert_rejected_without_allocation(board, "omit-changed-ignored", holders=2)

    def test_unselected_ignored_changes_and_sibling_paths_allow_sharing(self):
        (self.repo / ".gitignore").write_text("cache/\n", encoding="utf-8", newline="\n")
        self.git(self.repo, "add", ".gitignore")
        self.git(self.repo, "commit", "-qm", "ignore caches")
        board = self.board()
        self.reader(board, "before-cache")
        cache = self.repo / "cache"
        cache.mkdir()
        (cache / "runtime.txt").write_text("generated\n", encoding="utf-8", newline="\n")
        self.reader(board, "after-cache", cwd=cache)

    def test_writer_exclusion_is_checked_before_pinning(self):
        board = self.board()
        self.reader(board, "reader")
        self.assert_rejected_without_allocation(board, "writer", access="write", code="PREPARATION_CONFLICT")

    def test_reader_cannot_share_a_writers_checkout(self):
        board = self.board()
        self.submit(board, request_id="writer", cwd=str(self.repo))
        self.assert_rejected_without_allocation(board, "reader")

    def test_competing_writer_admitted_after_precheck_leaves_no_rejected_snapshot(self):
        board = self.board()
        original_submit = board.store.task_submit
        captured = {}

        def competing_writer(*args, **kwargs):
            if args[0]["requestId"] == "racing-reader":
                captured["manifest"] = kwargs["micro_task"]["manifest"]
                self.submit(board, request_id="winning-writer", cwd=str(self.repo))
            return original_submit(*args, **kwargs)

        with patch.object(board.store, "task_submit", side_effect=competing_writer):
            with self.assertRaises(BoardError) as caught:
                self.reader(board, "racing-reader")
        self.assertEqual(caught.exception.code, "SNAPSHOT_CHANGED")
        manifest = captured["manifest"]
        self.assertEqual(self.git(self.repo, "for-each-ref", "refs/hey-my-buddy/workspaces/" + manifest["workspaceId"]), "")
        self.assertFalse((self.directory / "workspaces" / manifest["workspaceId"]).exists())
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM tasks WHERE request_id='racing-reader'").fetchone())

    def test_conflicting_helper_batch_discards_snapshots_after_releasing_shared_locks(self):
        board = self.board()
        parent = self.submit(board, cwd=str(self.repo), kind="worktree")
        self.finish_turn(board, self.claim(board, run_id=parent["runId"]), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        before_refs = self.git(self.repo, "for-each-ref")
        before_state = sorted(str(path.relative_to(self.directory / "workspaces"))
                              for path in (self.directory / "workspaces").rglob("*"))
        with self.assertRaises(BoardError) as caught:
            self.decide(board, view, view["activeRequest"]["requestId"], helpers=[
                {"requestId": "helper-reader", "task": "read source", "cwd": str(self.repo),
                 "executionWorkspace": {"kind": "existing", "access": "read"}},
                {"requestId": "helper-writer", "task": "write source", "cwd": str(self.repo),
                 "executionWorkspace": {"kind": "existing", "access": "write"}},
            ])
        self.assertEqual(caught.exception.code, "PREPARATION_CONFLICT")
        self.assertEqual(self.git(self.repo, "for-each-ref"), before_refs)
        self.assertEqual(sorted(str(path.relative_to(self.directory / "workspaces"))
                                for path in (self.directory / "workspaces").rglob("*")), before_state)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_children").fetchone()[0], 0)

    def test_new_readers_selected_ignored_input_changed_after_capture_discards_snapshot(self):
        (self.repo / ".gitignore").write_text("*.ignored\n", encoding="utf-8", newline="\n")
        self.git(self.repo, "add", ".gitignore")
        self.git(self.repo, "commit", "-qm", "ignore caches")
        selected = self.repo / "new.ignored"
        selected.write_text("captured input\n", encoding="utf-8", newline="\n")
        board = self.board()
        self.reader(board, "old-reader")
        original_submit = board.store.task_submit
        before_refs = self.git(self.repo, "for-each-ref")
        before_allocations = sorted((self.directory / "workspaces").iterdir())

        def change_new_input(*args, **kwargs):
            selected.write_text("changed after capture\n", encoding="utf-8", newline="\n")
            return original_submit(*args, **kwargs)

        with patch.object(board.store, "task_submit", side_effect=change_new_input):
            with self.assertRaises(BoardError) as caught:
                self.reader(board, "new-reader", ["new.ignored"])
        self.assertEqual(caught.exception.code, "SNAPSHOT_CHANGED")
        self.assertEqual(self.git(self.repo, "for-each-ref"), before_refs)
        self.assertEqual(sorted((self.directory / "workspaces").iterdir()), before_allocations)
