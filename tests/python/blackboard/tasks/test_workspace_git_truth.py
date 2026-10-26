"""Git checkout rules are authoritative for workspace input and output."""
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from temporary import TemporaryDirectory

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class WorkspaceGitTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="hey-my-buddy-workspace-git-truth-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Git Truth Test")
        self.git("config", "user.email", "truth@example.invalid")
        (self.repo / "src").mkdir()

    def git(self, *args, cwd=None, data=None):
        command, environment = workspace._git_command(cwd or self.repo, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.payload())
        return caught.exception

    def intent(self, *, kind="worktree", selected=(), base=None):
        return {"kind": kind, "cwd": str(self.repo), "access": "write",
                "base": base or {"kind": "working-tree"}, "writeScope": ["src"],
                "integrator": {"role": "host", "id": "host-1"}, "includeUntracked": list(selected)}

    def git_link_and_crlf_fixture(self):
        self.git("config", "core.symlinks", "false")
        self.git("config", "core.autocrlf", "true")
        (self.repo / ".gitattributes").write_text("src/*.txt text\n", encoding="utf-8", newline="\n")
        (self.repo / "src/text.txt").write_bytes(b"line one\r\nline two\r\n")
        self.git("add", ".gitattributes", "src/text.txt")
        target = b"text.txt"
        link_oid = self.git("hash-object", "-w", "--stdin", data=target).strip().decode()
        self.git("update-index", "--index-info", data=f"120000 {link_oid}\tsrc/link\n".encode())
        self.git("commit", "-qm", "link and text")
        # Force Git to render the index through the checkout settings. With
        # core.symlinks=false, src/link is a regular placeholder for mode 120000.
        self.git("reset", "--hard", "HEAD")
        self.assertEqual((self.repo / "src/text.txt").read_bytes(), b"line one\r\nline two\r\n")
        self.assertEqual((self.repo / "src/link").read_bytes(), target)
        self.assertFalse((self.repo / "src/link").is_symlink())
        self.assertEqual(self.git("ls-files", "-s", "--", "src/link").split()[0], b"120000")
        return self.git("rev-parse", "HEAD").strip().decode()

    def test_source_snapshot_normalizes_crlf_and_retains_link_mode(self):
        self.git_link_and_crlf_fixture()
        (self.repo / "src/text.txt").write_bytes(b"source edit\r\n")
        before_index = (self.repo / ".git/index").read_bytes()
        before_head = self.git("rev-parse", "HEAD")
        manifest = workspace.prepare(self.state, "source", self.intent())
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/text.txt"), b"source edit\n")
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/link"), b"text.txt")
        self.assertEqual(self.git("ls-tree", "-r", manifest["inputCommit"], "src/link").split()[0], b"120000")
        self.assertEqual((self.repo / ".git/index").read_bytes(), before_index)
        self.assertEqual(self.git("rev-parse", "HEAD"), before_head)

    def test_existing_require_unchanged_uses_normalized_git_content(self):
        base = self.git_link_and_crlf_fixture()
        manifest = workspace.prepare(self.state, "existing", self.intent(kind="existing", base={"kind": "commit", "ref": base}))
        self.assertTrue(workspace.verify(manifest, require_unchanged=True, state_dir=self.state)["unchanged"])
        # A disk line-ending change with the same Git blob is not content drift.
        (self.repo / "src/text.txt").write_bytes(b"line one\nline two\n")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True, state_dir=self.state)["unchanged"])
        (self.repo / "src/text.txt").write_bytes(b"different\r\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, require_unchanged=True, state_dir=self.state)

    def test_worker_checkout_uses_git_smudge_and_link_placeholder(self):
        self.git_link_and_crlf_fixture()
        manifest = workspace.prepare(self.state, "worker", self.intent())
        checkout = Path(manifest["checkoutRoot"])
        self.assertEqual((checkout / "src/text.txt").read_bytes(), b"line one\r\nline two\r\n")
        self.assertEqual((checkout / "src/link").read_bytes(), b"text.txt")
        self.assertFalse((checkout / "src/link").is_symlink())
        self.assertEqual(self.git("ls-files", "-s", "--", "src/link", cwd=checkout).split()[0], b"120000")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True, state_dir=self.state)["unchanged"])

    def test_seal_uses_clean_filter_for_crlf_and_link_placeholder(self):
        self.git_link_and_crlf_fixture()
        manifest = workspace.prepare(self.state, "seal", self.intent())
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/text.txt").write_bytes(b"worker edit\r\n")
        (checkout / "src/link").write_bytes(b"elsewhere.txt")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("show", output["commit"] + ":src/text.txt"), b"worker edit\n")
        self.assertEqual(self.git("show", output["commit"] + ":src/link"), b"elsewhere.txt")
        self.assertEqual(self.git("ls-tree", "-r", output["commit"], "src/link").split()[0], b"120000")
        self.assertEqual(output["changedPaths"], ["src/link", "src/text.txt"])

    def test_text_eol_attribute_controls_source_checkout_and_seal(self):
        self.git("config", "core.autocrlf", "false")
        (self.repo / ".gitattributes").write_text("src/forced.txt text eol=crlf\n", encoding="utf-8", newline="\n")
        (self.repo / "src/forced.txt").write_bytes(b"base\r\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "forced CRLF")
        (self.repo / "src/forced.txt").unlink()
        self.git("reset", "--hard", "HEAD")
        self.assertEqual((self.repo / "src/forced.txt").read_bytes(), b"base\r\n")
        manifest = workspace.prepare(self.state, "eol", self.intent())
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/forced.txt"), b"base\n")
        checkout = Path(manifest["checkoutRoot"])
        self.assertEqual((checkout / "src/forced.txt").read_bytes(), b"base\r\n")
        (checkout / "src/forced.txt").write_bytes(b"changed\r\n")
        output = workspace.seal(self.state, manifest, "task", "eol")
        self.assertEqual(self.git("show", output["commit"] + ":src/forced.txt"), b"changed\n")

    def test_clean_smudge_filter_and_core_filemode_false(self):
        script = self.root / "filter.py"
        script.write_text(
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.buffer.write(data.replace(b'work:', b'git:') if sys.argv[1] == 'clean' "
            "else data.replace(b'git:', b'work:'))\n",
            encoding="utf-8", newline="\n")
        # Git runs filters through its shell on Windows as well.
        quote = lambda parts: " ".join(shlex.quote(part.replace("\\", "/") if os.name == "nt" else part) for part in parts)
        self.git("config", "filter.truth.clean", quote([sys.executable, str(script), "clean"]))
        self.git("config", "filter.truth.smudge", quote([sys.executable, str(script), "smudge"]))
        self.git("config", "filter.truth.required", "true")
        self.git("config", "core.filemode", "false")
        (self.repo / ".gitattributes").write_text("src/filter.txt filter=truth text eol=lf\n", encoding="utf-8", newline="\n")
        (self.repo / "src/filter.txt").write_bytes(b"work:base\n")
        (self.repo / "src/script.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
        self.git("add", "-A")
        self.git("update-index", "--chmod=+x", "src/script.sh")
        self.git("commit", "-qm", "filtered executable")
        (self.repo / "src/filter.txt").unlink()
        self.git("reset", "--hard", "HEAD")
        self.assertEqual((self.repo / "src/filter.txt").read_bytes(), b"work:base\n")
        manifest = workspace.prepare(self.state, "filter", self.intent())
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/filter.txt"), b"git:base\n")
        self.assertEqual(self.git("ls-tree", "-r", manifest["inputCommit"], "src/script.sh").split()[0], b"100755")
        checkout = Path(manifest["checkoutRoot"])
        self.assertEqual((checkout / "src/filter.txt").read_bytes(), b"work:base\n")
        (checkout / "src/filter.txt").write_bytes(b"work:output\n")
        output = workspace.seal(self.state, manifest, "task", "filter")
        self.assertEqual(self.git("show", output["commit"] + ":src/filter.txt"), b"git:output\n")
        self.assertEqual(self.git("ls-tree", "-r", output["commit"], "src/script.sh").split()[0], b"100755")

    def test_recent_same_size_edit_is_captured_without_refreshing_source_index(self):
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.trustctime", "false")
        source = self.repo / "src/text.txt"
        source.write_bytes(b"old\n")
        old = source.stat().st_mtime_ns - 10_000_000_000
        os.utime(source, ns=(old, old))
        self.git("add", "-A")
        self.git("commit", "-qm", "old content")
        source.write_bytes(b"new\n")
        os.utime(source, ns=(old, old))
        index = self.repo / ".git/index"
        os.utime(index, ns=(old, old))
        before = index.read_bytes()
        manifest = workspace.prepare(self.state, "racy-index", self.intent())
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/text.txt"), b"new\n")
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(index.stat().st_mtime_ns, old)

    def test_git_operations_do_not_run_repository_hooks(self):
        (self.repo / "src/text.txt").write_bytes(b"base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        marker = self.root / "hook-ran"
        hook = self.repo / ".git/hooks/post-index-change"
        hook.write_text("#!/bin/sh\nprintf hook > " + shlex.quote(marker.as_posix()) + "\n",
                        encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        (self.repo / "src/text.txt").write_bytes(b"source edit\n")
        manifest = workspace.prepare(self.state, "hooks", self.intent())
        (Path(manifest["path"]) / "src/text.txt").write_bytes(b"worker edit\n")
        workspace.seal(self.state, manifest, "task", "hooks")
        self.assertFalse(marker.exists())

    def test_case_collisions_are_rejected_after_git_checkout(self):
        probe = self.root / "case-probe"
        probe.write_bytes(b"case sensitivity probe")
        if not (self.root / "CASE-PROBE").exists():
            self.skipTest("filesystem distinguishes case")
        # Identical blobs also collide: Git status and add -u can retain both
        # cached names while the disk holds only one. Differing blobs do too.
        for same in (True, False):
            with self.subTest(same=same):
                one = self.git("hash-object", "-w", "--stdin", data=b"one\n").strip().decode()
                two = one if same else self.git("hash-object", "-w", "--stdin", data=b"two\n").strip().decode()
                self.git("read-tree", "--empty")
                self.git("update-index", "--index-info", data=f"100644 {one}\tName\n100644 {two}\tname\n".encode())
                tree = self.git("write-tree").strip().decode()
                commit = self.git("commit-tree", tree, data=b"case collision\n").strip().decode()
                self.git("update-ref", "HEAD", commit)
                error = self.assert_error("WORKSPACE_UNSUPPORTED", workspace.prepare, self.state,
                                          "case-" + str(same), self.intent(base={"kind": "commit", "ref": commit}))
                self.assertTrue(set(error.details["paths"]) & {"Name", "name"})
                self.assertTrue(error.details["reason"])

    def test_checkout_rejects_extra_file_created_by_smudge_filter(self):
        script = self.root / "mismatch_filter.py"
        script.write_text(
            "import pathlib, sys\n"
            "data = sys.stdin.buffer.read()\n"
            "if sys.argv[1] == 'smudge':\n"
            "    pathlib.Path('unexpected.txt').write_bytes(b'filter side effect')\n"
            "sys.stdout.buffer.write(data)\n",
            encoding="utf-8", newline="\n")
        # Git runs filters through its shell on Windows as well.
        quote = lambda parts: " ".join(shlex.quote(part.replace("\\", "/") if os.name == "nt" else part) for part in parts)
        self.git("config", "filter.mismatch.clean", quote([sys.executable, str(script), "clean"]))
        self.git("config", "filter.mismatch.smudge", quote([sys.executable, str(script), "smudge"]))
        self.git("config", "filter.mismatch.required", "true")
        (self.repo / ".gitattributes").write_text("src/filtered.txt filter=mismatch\n", encoding="utf-8", newline="\n")
        (self.repo / "src/filtered.txt").write_bytes(b"base")
        self.git("add", "-A")
        self.git("commit", "-qm", "filter fixture")
        error = self.assert_error("WORKSPACE_UNSUPPORTED", workspace.prepare, self.state, "mismatch", self.intent())
        self.assertIn("unexpected.txt", error.details["paths"])
        self.assertTrue(error.details["reason"])


if __name__ == "__main__":
    unittest.main()
