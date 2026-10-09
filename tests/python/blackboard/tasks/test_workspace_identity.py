"""Restart identity and retained aliases exercised with real private Git facts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.workspace_identity_migration import migrate_workspace_identity
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace, workspace_identity
from hey_my_buddy.errors import BoardError


class WorkspaceIdentityFixture(WorkflowTestCase):
    """Real workspaces with only the retired device-dependent hash substituted.

    The old hash is copied literally from the previous implementation. Preparing
    and recording inputs still runs the real Git and blackboard paths; a device
    change does not rewrite the saved manifests or manufacture inode evidence.
    """

    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace
        self.repo = self.repository("repo")

    def git(self, path, *args):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(path), *args], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(self, name):
        path = self.workdir(name).resolve()
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Workspace Identity Test")
        self.git(path, "config", "user.email", "identity@example.invalid")
        (path / "tracked.txt").write_text("base\n")
        self.git(path, "add", "tracked.txt")
        self.git(path, "commit", "-qm", "base")
        return path

    def intent(self, path=None, **updates):
        return {"kind": "existing", "cwd": str(path or self.repo), "access": "write",
                "base": {"kind": "working-tree"}, "includeUntracked": [],
                "writeScope": ["."], "integrator": "host-1", **updates}

    @contextmanager
    def legacy(self, device_delta=2):
        def old_identity(path):
            facts = path.stat()
            payload = json.dumps([str(path), facts.st_dev + device_delta, facts.st_ino],
                                 sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            return hashlib.sha256(payload).hexdigest()

        # Historical captures predate the new fail-closed reservation gate too.
        # Only capture uses retired admission; every post-upgrade action runs the
        # real current gate, including the unprovable-holder regression.
        with patch.object(workspace, "_identity", side_effect=old_identity), patch.object(
                workflow_module.WorkflowCoordinator, "_unproven_checkout_holder", return_value=None):
            yield

    def legacy_run(self, board=None, *, request_id="legacy", device_delta=2,
                   kind="worktree", access="write", objective=None):
        board = board or self.board()
        with self.legacy(device_delta):
            run = self.submit(board, request_id=request_id, cwd=str(self.repo),
                              executionWorkspace={"kind": kind, "access": access},
                              objective=objective or {"title": request_id + " macro"})
        return board, run, self.manifest(board, run["runId"])

    def manifest(self, board, run_id):
        with board.store.db.read() as connection:
            return json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0])

    def snapshot(self, board):
        with board.store.db.read() as connection:
            result = backup.database_snapshot(connection)
            result["schema"] = int(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0])
        return result

    def migrate(self, board):
        return migrate_workspace_identity(self.directory, self.snapshot(board))

    def all_rows(self, board):
        with board.store.db.read() as connection:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            return {name: tuple(tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid'
            )) for name in names}

    def historical(self, board):
        return {name: rows for name, rows in self.all_rows(board).items()
                if name not in {"meta", "objectives", "workspace_reservations"}}

    def fixed_files(self):
        return {str(path.relative_to(self.directory)): path.read_bytes()
                for path in (self.directory / "workspaces").rglob("*")
                if path.is_file() and path.suffix in {".json", ".patch"}}

    def aliases(self, board):
        with board.store.db.read() as connection:
            return workspace_identity.load_alias_map(connection)


class WorkspaceIdentityTests(WorkspaceIdentityFixture):
    def test_changed_device_keeps_identity_and_changed_inode_changes_it(self):
        original_stat = Path.stat
        directory = self.repo / ".git"
        first = workspace.inspect(str(self.repo))

        def changed_device(path, *args, **kwargs):
            facts = original_stat(path, *args, **kwargs)
            if path == directory:
                values = list(facts)
                values[2] += 2
                return os.stat_result(values)
            return facts

        with patch.object(Path, "stat", new=changed_device):
            restarted = workspace.inspect(str(self.repo))
        self.assertEqual(restarted["checkoutId"], first["checkoutId"])
        self.assertEqual(restarted["repositoryId"], first["repositoryId"])
        retained = self.directory / "retained-git"
        directory.rename(retained)
        shutil.copytree(retained, directory)
        self.assertNotEqual(directory.stat().st_ino, retained.stat().st_ino)
        replaced = workspace.inspect(str(self.repo))
        self.assertNotEqual(replaced["checkoutId"], first["checkoutId"])
        self.assertNotEqual(replaced["repositoryId"], first["repositoryId"])

    def test_old_manifest_verifies_without_a_coordinator_thread_context(self):
        board, _run, manifest = self.legacy_run()
        before_files = self.fixed_files()
        self.migrate(board)
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            verified = workspace.verify(manifest)
        self.assertTrue(verified["unchanged"])
        self.assertEqual(self.fixed_files(), before_files)
        with self.assertRaises(BoardError):
            with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory / "unrelated-board")}):
                workspace.verify(manifest)

    def test_registered_alias_cannot_verify_a_copied_git_directory(self):
        board, _run, manifest = self.legacy_run(kind="existing")
        self.migrate(board)
        mapping = self.aliases(board)
        directory = self.repo / ".git"
        retained = self.directory / "original-git"
        directory.rename(retained)
        shutil.copytree(retained, directory)
        self.assertNotEqual(directory.stat().st_ino, retained.stat().st_ino)
        self.assertEqual(self.git(self.repo, "rev-parse", manifest["snapshot"]["inputRef"]),
                         manifest["inputCommit"])
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with workspace_identity.aliases(mapping):
                with self.assertRaises(BoardError):
                    workspace.verify(manifest, require_unchanged=False)

    def test_valid_alias_cannot_bypass_fixed_manifest_checksum(self):
        board, _run, manifest = self.legacy_run()
        self.migrate(board)
        mapping = self.aliases(board)
        modified = dict(manifest, inputTree="0" * 40)
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with workspace_identity.aliases(mapping):
                with self.assertRaises(BoardError) as caught:
                    workspace.verify(modified, require_unchanged=False)
        self.assertEqual(caught.exception.code, "WORKSPACE_MANIFEST_CHANGED")
