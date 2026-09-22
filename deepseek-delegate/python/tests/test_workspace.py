"""Workspace boundaries tested against disposable real Git repositories."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from buddy.errors import BoardError
from buddy import workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Workspace Test")
        self.git("config", "user.email", "workspace@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "docs").mkdir()
        (self.repo / "src/file.txt").write_text("base\n")
        (self.repo / "docs/readme.md").write_text("base docs\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args, cwd=None, data=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(cwd or self.repo), *args], input=data,
                                capture_output=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.payload())
        return caught.exception

    def test_identity_unifies_siblings_and_distinguishes_linked_checkout(self):
        left = workspace.inspect(str(self.repo / "src"))
        right = workspace.inspect(str(self.repo / "docs"))
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked), self.base)
        other = workspace.inspect(str(linked / "src"))
        self.assertEqual(left["checkoutId"], right["checkoutId"])
        self.assertNotEqual(left["checkoutId"], other["checkoutId"])
        self.assertEqual(left["repositoryId"], other["repositoryId"])
        self.assertEqual(left["checkoutRoot"], str(self.repo))
        self.assertEqual(left["path"], str(self.repo / "src"))

    def test_non_git_and_unborn_checkout_are_explicit_errors(self):
        self.assert_error("WORKSPACE_UNSUPPORTED", workspace.inspect, str(self.root))
        unborn = self.root / "unborn"
        unborn.mkdir()
        self.git("init", "-q", cwd=unborn)
        self.assert_error("WORKSPACE_REF_INVALID", workspace.inspect, str(unborn))


if __name__ == "__main__":
    unittest.main()
