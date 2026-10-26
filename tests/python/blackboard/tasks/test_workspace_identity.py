"""Restart-stable identity exercised with real private Git facts."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.paths import native_path


class WorkspaceIdentityFixture(WorkflowTestCase):
    """Real Git workspaces under a private board."""

    reuse_repository_template = False
    _repository_template = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.reuse_repository_template:
            return
        # Only immutable initial Git objects/config are shared. Each method gets
        # a new directory inode and board, and captures its own real manifests.
        temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-identity-repository-")
        cls.addClassCleanup(temporary.cleanup)
        fixture = cls(methodName="runTest")
        fixture.directory = Path(temporary.name)
        cls._repository_template = None
        cls._repository_template = fixture.repository("template")

    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace
        self.repo = self.repository("repo")

    def git(self, path, *args):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(path), *args], env=environment,
                                capture_output=True, text=True, timeout=30, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(self, name):
        path = self.workdir(name).resolve()
        if self._repository_template is not None:
            shutil.copytree(self._repository_template, path, dirs_exist_ok=True)
            return path
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Workspace Identity Test")
        self.git(path, "config", "user.email", "identity@example.invalid")
        (path / "tracked.txt").write_text("base\n", encoding="utf-8", newline="\n")
        self.git(path, "add", "tracked.txt")
        self.git(path, "commit", "-qm", "base")
        return path

    def fixed_files(self):
        return {str(path.relative_to(native_path(self.directory))): path.read_bytes()
                for path in native_path(self.directory / "workspaces").rglob("*")
                if path.is_file() and path.suffix in {".json", ".patch"}}



class WorkspaceIdentityTests(WorkspaceIdentityFixture):
    reuse_repository_template = True

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
