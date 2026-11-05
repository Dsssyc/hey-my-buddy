"""Deletion marking fences Host Git writes before allocation records disappear."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow_real import BUDDY
from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path


class DeletionAdmissionTests(LifecycleTestCase):
    def submit_case(self, board, *, title, task, cwd, checkout, write_scope, macro_task_id="standalone"):
        return board.call("workflow_submit", {
            "cwd": str(cwd), "macroTaskId": macro_task_id, "title": title, "task": task,
            "buddy": " / ".join(BUDDY.values()), "checkout": checkout, "access": "write",
            "writeScope": list(write_scope), "inputs": [], "expectedSeconds": 1800,
        })

    def test_new_micro_task_cannot_borrow_checkout_after_owner_deletion_is_marked(self):
        board = self.board()
        created = board.call("workflow_create", {
            "cwd": str(self.repo), "title": "Deleting owner", "goal": "Deleting owner",
        })
        owner = self.submit_case(board, title="deleting-managed-owner", task="Own the managed checkout",
                                 cwd=self.repo, checkout="worktree", write_scope=(".",),
                                 macro_task_id=created["macroTaskId"])
        claimed = self.claim(board, owner["runId"])
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, claimed["claim"]["turn"]["input"]["executionWorkspace"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory, manifest, owner["runId"],
                              claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        view = self.view(board, owner["runId"])
        artifact = next(item for item in view["artifacts"] if item["kind"] == "output")
        with patch.object(board.store.workflow, "_reclaim_checkout",
                          return_value={"removed": False, "reasons": ["fixture-retained"]}):
            self.accept(board, view, artifact, not_required=True)

        with patch.object(deletion, "_delete_workspaces", side_effect=OSError("fixture physical failure")):
            interrupted = deletion.delete(board.store, created["macroTaskId"], 1)
        self.assertEqual(interrupted["state"], "deleting")
        self.assertTrue(checkout.is_dir())
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM macro_task_deletions WHERE target_id=?", (created["macroTaskId"],),
            ).fetchone())

        def assert_refused(suffix, cwd):
            request_id = "deleting-managed-borrower-" + suffix
            with self.subTest(cwd=cwd), self.assertRaises(BoardError) as caught:
                self.submit_case(board, title=request_id, task="Borrow the deleting managed checkout",
                                 cwd=cwd, checkout="existing", write_scope=(".",))
            self.assertEqual(caught.exception.code, "PREPARATION_CONFLICT")
            with board.store.db.read() as connection:
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM workflow_runs WHERE title=?", (request_id,),
                ).fetchone())

        for suffix, cwd in (("root", checkout), ("child", checkout / "src"),
                            ("normalized", checkout / "src" / "..")):
            assert_refused(suffix, cwd)

        (checkout / ".git").unlink()
        self.git("-C", str(checkout), "init", "-q", "-b", "main")
        self.git("-C", str(checkout), "config", "user.email", "buddy@example.invalid")
        self.git("-C", str(checkout), "config", "user.name", "Buddy Test")
        self.git("-C", str(checkout), "add", ".")
        self.git("-C", str(checkout), "commit", "-qm", "replacement repository")
        self.assertNotEqual(workspace.inspect(str(checkout))["projectId"], manifest["projectId"])
        assert_refused("replacement-repository", checkout)

        nested = checkout / "src"
        self.git("-C", str(nested), "init", "-q", "-b", "main")
        self.git("-C", str(nested), "config", "user.email", "buddy@example.invalid")
        self.git("-C", str(nested), "config", "user.name", "Buddy Test")
        self.git("-C", str(nested), "add", ".")
        self.git("-C", str(nested), "commit", "-qm", "nested repository")
        self.assertNotEqual(workspace.inspect(str(nested))["projectId"], manifest["projectId"])
        assert_refused("nested-repository", nested)

    def failed_run(self, *, conflict=False):
        board = self.board()
        submitted = self.submit_case(board, title="deleting-host-write",
                                     task="Private deletion admission fixture", cwd=self.repo,
                                     checkout="existing", write_scope=("src",))
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        attempt = claimed["claim"]["attempt"]
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, claimed["claim"]["turn"]["input"]["executionWorkspace"])
        result = {"status": "failed"}
        if conflict:
            (self.repo / "tracked.txt").write_text("outside the scope\n", encoding="utf-8", newline="\n")
            with self.assertRaises(BoardError) as caught:
                workspace.seal(self.directory, manifest, run_id, attempt["attemptId"])
            self.assertEqual(caught.exception.code, "WORKSPACE_SCOPE_VIOLATION")
            result["workspaceSealError"] = "WORKSPACE_SCOPE_VIOLATION: refused fixture change"
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 16,
            "status": "failed", "result": result, "shutdownConfirmed": True, "exitCode": 1,
            "error": result.get("workspaceSealError", "Private fixture failure"),
        })
        # A failed run may conclude after cancellation; a scope request stays open
        # so the resolution action still identifies the exact recorded site.
        if not conflict:
            board.call("workflow_cancel", {"runId": run_id})
        view = self.view(board, run_id)
        return board, manifest, view

    def host_write(self, board, view, *, conflict):
        if not conflict:
            return board.call("workflow_conclude", {
                "runId": view["runId"], "note": "Review this failed execution",
            })
        site = view["workspaceConflicts"][0]
        return board.call("workflow_continue", {
            "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
            "resolution": "adopt",
            "observedFingerprint": site["observedFingerprint"], "note": "Review this exact failed site",
        })

    def refs(self, manifest):
        return self.git("for-each-ref", "--format=%(refname) %(objectname)",
                        "refs/hey-my-buddy/workspaces/" + manifest["workspaceId"] + "/")

    def deletion_window(self, *, conflict):
        board, manifest, view = self.failed_run(conflict=conflict)
        directory = self.directory / "workspaces" / manifest["workspaceId"]
        target = "run:" + view["runId"]
        original = Path.rename
        observed = []

        def before_rename(path, destination):
            if path == native_path(directory):
                # The real deletion engine has completed its second ref pass
                # and released .lock, but has not renamed the record directory.
                with board.store.db.read() as connection:
                    self.assertIsNotNone(connection.execute(
                        "SELECT 1 FROM macro_task_deletions WHERE target_id=?", (target,),
                    ).fetchone())
                self.assertEqual(self.refs(manifest), "")
                with self.assertRaises(BoardError) as caught:
                    self.host_write(board, view, conflict=conflict)
                self.assertEqual(caught.exception.code, "TASK_DELETING")
                self.assertEqual(self.refs(manifest), "")
                observed.append(True)
            return original(path, destination)

        with patch.object(Path, "rename", autospec=True, side_effect=before_rename):
            deleted = deletion.delete(board.store, target, 1)
        self.assertEqual(observed, [True])
        self.assertEqual(deleted["state"], "deleted")
        self.assertNotIn("skipped", deleted)
        self.assertFalse(native_path(directory).exists())
        self.assertEqual(self.refs(manifest), "")

    def test_conclude_is_refused_after_the_second_deletion_pass_without_pinning(self):
        self.deletion_window(conflict=False)

    def mark_at_lock(self, *, conflict):
        board, manifest, view = self.failed_run(conflict=conflict)
        directory = self.directory / "workspaces" / manifest["workspaceId"]
        before = self.refs(manifest)
        original = workspace._lock
        marked = []

        @contextmanager
        def mark_before_acquiring(path, **kwargs):
            if path == directory and not marked:
                # Model deletion marking after the public operation's initial
                # precheck, before it acquires the lock for its Git write.
                with original(path, create=False):
                    with board.store.db.write() as connection:
                        connection.execute(
                            "INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id) VALUES(?,?,?)",
                            ("deleting-at-lock", "micro-task-tree", "run:" + view["runId"]),
                        )
                marked.append(True)
            with original(path, **kwargs):
                yield

        with patch.object(workspace, "_lock", side_effect=mark_before_acquiring):
            with self.assertRaises(BoardError) as caught:
                self.host_write(board, view, conflict=conflict)
        self.assertEqual(marked, [True])
        self.assertEqual(caught.exception.code, "TASK_DELETING")
        self.assertEqual(self.refs(manifest), before)

    def test_conclude_rechecks_marking_after_precheck_under_the_workspace_lock(self):
        self.mark_at_lock(conflict=False)

    def test_continue_resolution_rechecks_marking_after_precheck_under_the_workspace_lock(self):
        self.mark_at_lock(conflict=True)
