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
    def failed_run(self, *, conflict=False):
        board = self.board()
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "deleting-host-write", "hostId": "host-1",
            "task": "Private deletion admission fixture", "cwd": str(self.repo),
            "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["src"]},
        })
        run_id = submitted["runId"]
        self.controls[run_id] = submitted["control"]
        claimed = self.claim(board, run_id)
        attempt = claimed["claim"]["attempt"]
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
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
        # Cancellation settles any attention request while preserving the failed
        # site's evidence, so both public Host operations remain meaningful.
        board.call("workflow_cancel", {"runId": run_id, **self.control(submitted)})
        view = self.view(board, run_id)
        return board, manifest, view

    def host_write(self, board, view, *, conflict):
        if not conflict:
            return board.call("workflow_conclude", {
                "runId": view["runId"], "note": "Review this failed execution", **self.control(view),
            })
        site = view["workspaceConflicts"][0]
        return board.store.workflow.workspace_resolve({
            "runId": view["runId"], "commandId": "deleting-resolution", "expectedRevision": view["revision"],
            "conflictId": site["conflictId"], "action": "adopt", "paths": [],
            "observedFingerprint": site["observedFingerprint"], "note": "Review this exact failed site",
            **self.control(view),
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

    def test_workspace_resolve_is_refused_after_the_second_deletion_pass_without_pinning(self):
        self.deletion_window(conflict=True)

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

    def test_workspace_resolve_rechecks_marking_after_precheck_under_the_workspace_lock(self):
        self.mark_at_lock(conflict=True)
