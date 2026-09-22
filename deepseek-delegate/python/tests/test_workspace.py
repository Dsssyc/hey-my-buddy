"""Workspace boundaries tested against disposable real Git repositories."""
import os
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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

    def intent(self, **updates):
        return {"kind": "worktree", "cwd": str(self.repo), "access": "write", "base": {"kind": "working-tree"},
                "writeScope": ["src"], "integrator": "host", **updates}

    def prepare(self, request="request", **updates):
        return workspace.prepare(self.state, request, self.intent(**updates))

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

    def test_dirty_input_preserves_staging_binary_modes_symlinks_and_source(self):
        source = self.repo / "src/file.txt"
        source.write_text("staged\n")
        self.git("add", "src/file.txt")
        source.write_bytes(b"unstaged\x00binary\xff\n")
        source.chmod(0o755)
        selected = self.repo / "src/selected.bin"
        selected.write_bytes(b"selected\x00\xfe")
        (self.repo / "src/excluded.txt").write_text("must not be copied")
        outside = self.root / "outside-secret"
        outside.write_text("do not follow")
        (self.repo / "src/link").symlink_to(outside)
        original_index = (self.repo / ".git/index").read_bytes()
        original_status = self.git("status", "--porcelain=v1", "-z")
        manifest = self.prepare(includeUntracked=["src/selected.bin", "src/link"])
        target = Path(manifest["checkoutRoot"])
        self.assertEqual((target / "src/file.txt").read_bytes(), source.read_bytes())
        self.assertTrue((target / "src/file.txt").stat().st_mode & 0o111)
        self.assertEqual((target / "src/selected.bin").read_bytes(), selected.read_bytes())
        self.assertEqual(os.readlink(target / "src/link"), str(outside))
        self.assertFalse((target / "src/excluded.txt").exists())
        self.assertEqual(self.git("show", manifest["snapshot"]["stagedCommit"] + ":src/file.txt"), b"staged\n")
        self.assertEqual(original_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(original_status, self.git("status", "--porcelain=v1", "-z"))
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(outside.read_text(), "do not follow")
        self.assertEqual(manifest["snapshot"]["excludedUntracked"], ["src/excluded.txt"])
        empty_hash = hashlib.sha256(b"").hexdigest()
        self.assertNotEqual(manifest["snapshot"]["stagedSha256"], empty_hash)
        self.assertNotEqual(manifest["snapshot"]["unstagedSha256"], empty_hash)
        self.assertTrue(workspace.verify(manifest)["unchanged"])

    def test_prepare_replay_freezes_source_and_changed_intent_conflicts(self):
        intent = self.intent()
        first = workspace.prepare(self.state, "same", intent)
        (self.repo / "src/file.txt").write_text("new input\n")
        self.git("commit", "-qam", "source advanced")
        self.assertEqual(workspace.prepare(self.state, "same", intent), first)
        second = workspace.prepare(self.state, "new-request", intent)
        self.assertNotEqual(first["inputCommit"], second["inputCommit"])
        self.assertEqual((Path(first["path"]) / "src/file.txt").read_text(), "base\n")
        self.assert_error("WORKSPACE_CONFLICT", workspace.prepare, self.state, "same", self.intent(writeScope=["docs"]))

    def test_existing_commit_requires_exact_checkout(self):
        manifest = self.prepare(kind="existing", base={"kind": "commit", "ref": self.base})
        self.assertEqual(manifest["inputCommit"], self.base)
        (self.repo / "src/file.txt").write_text("dirty\n")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "dirty", kind="existing", base={"kind": "commit", "ref": self.base})
        self.git("add", ".")
        self.git("commit", "-qm", "new head")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "different", kind="existing", base={"kind": "commit", "ref": self.base})

    def test_verify_detects_drift_and_read_cannot_opt_out(self):
        manifest = self.prepare(access="read", writeScope=[])
        (Path(manifest["path"]) / "src/file.txt").write_text("changed\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest)
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, require_unchanged=False)

    def test_unknown_worktree_target_is_preserved(self):
        _, directory = workspace._workspace_directory(self.state, "collision")
        target = directory / "checkout"
        target.mkdir()
        (target / "precious.txt").write_text("preserve\n")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare, "collision")
        self.assertEqual((target / "precious.txt").read_text(), "preserve\n")

    def test_completed_checkout_recovers_after_interrupted_manifest_write(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(workspace, "_write_once", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        (self.repo / "src/file.txt").write_text("source advanced after interruption")
        recovered = self.prepare()
        self.assertEqual((Path(recovered["path"]) / "src/file.txt").read_text(), "base\n")
        self.assertTrue(workspace.verify(recovered)["unchanged"])

    def test_changed_interrupted_checkout_is_not_overwritten(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(workspace, "_write_once", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        _, directory = workspace._workspace_directory(self.state, "request")
        changed = directory / "checkout/src/file.txt"
        changed.write_text("valuable unfinished work")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        self.assertEqual(changed.read_text(), "valuable unfinished work")


if __name__ == "__main__":
    unittest.main()
