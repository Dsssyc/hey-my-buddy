"""Frozen tree refusals retain their cause and remove partially written input."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.roles import router_input
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path
from hey_my_buddy.private_files import read_bytes


class FrozenTreeEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.update(GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--quiet")
        self.object_id = self.git("hash-object", "-w", "--stdin", input=b"frozen bytes").strip()
        self.tree = self.git("mktree", input=b"100644 blob " + self.object_id + b"\tfirst.txt\n").strip().decode()
        self.manifest = {"checkoutRoot": str(self.checkout), "inputTree": self.tree}
        self.enterContext(mock.patch.object(router_input.workspace, "verify"))

    def git(self, *arguments, input=None):
        return subprocess.run(["git", "-C", str(self.checkout), *arguments],
                              input=input, env=self.environment, capture_output=True,
                              check=True).stdout

    def test_long_mirror_is_verified_without_windows_readonly_and_discarded(self):
        name = 'deep/' + '/'.join(['directory-' + 'a' * 45] * 4) + '/tool.py'
        tree = self.object_id
        subtree = self.git('mktree', input=b'100755 blob ' + tree + b'\ttool.py\n').strip()
        for part in reversed(name.split('/')[:-1]):
            subtree = self.git('mktree', input=b'040000 tree ' + subtree + b'\t' + part.encode() + b'\n').strip()
        self.manifest['inputTree'] = subtree.decode()
        directory = self.root / 'long-mirror'
        directory.mkdir()
        frozen, expected = router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
        path = frozen / name
        self.assertGreater(len(str(path)), 260)
        self.assertEqual(read_bytes(path), b'frozen bytes')
        metadata = native_path(path).stat()
        if os.name == 'nt':
            self.assertFalse(metadata.st_file_attributes & 1)
        else:
            self.assertEqual(metadata.st_mode & 0o777, 0o555)
        self.assertTrue(router_input.verify(self.manifest, frozen, expected, deadline=time.monotonic() + 120)['unchanged'])
        if os.name != 'nt':
            native_path(path).chmod(0o644)
        native_path(path).write_bytes(b'changed')
        self.assertFalse(router_input.verify(self.manifest, frozen, expected, deadline=time.monotonic() + 120)['unchanged'])
        router_input.discard(frozen)
        self.assertFalse(frozen.exists())

    def test_mode_change_invalidates_the_snapshot(self):
        directory = self.root / 'mode-mirror'
        directory.mkdir()
        frozen, expected = router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
        path = frozen / 'first.txt'
        path.chmod(0o444 if os.name == 'nt' else 0o644)
        try:
            self.assertFalse(router_input.verify(self.manifest, frozen, expected, deadline=time.monotonic() + 120)['unchanged'])
        finally:
            if os.name == 'nt':
                path.chmod(0o666)


    def assert_refused_after_materializing_a_file(self, entry, message):
        directory = self.root / "review"
        directory.mkdir()
        frozen = directory / "frozen-input"
        first = b"100644 blob " + self.object_id + b"\tfirst.txt\0"
        listing = subprocess.CompletedProcess([], 0, stdout=first + entry + b"\0")
        discard = router_input.discard

        def check_partial_input(root):
            self.assertEqual(root, frozen)
            self.assertEqual((root / "first.txt").read_bytes(), b"frozen bytes")
            discard(root)

        with mock.patch.object(router_input.subprocess, "run", return_value=listing), \
                mock.patch.object(router_input, "discard", side_effect=check_partial_input) as cleanup:
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
        self.assertEqual(refused.exception.code, "router-input-unavailable")
        self.assertEqual(refused.exception.message, message)
        cleanup.assert_called_once_with(frozen)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_absolute_and_parent_paths_are_refused_as_unsafe_and_cleaned_up(self):
        message = "A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden"
        for path in (str(self.root / "escaped.txt"), "../../escaped.txt"):
            with self.subTest(path=path):
                self.assert_refused_after_materializing_a_file(
                    b"100644 blob " + self.object_id + b"\t" + os.fsencode(path), message)
                (self.root / "review").rmdir()

    def test_git_metadata_is_refused_as_unsafe_and_cleaned_up(self):
        self.assert_refused_after_materializing_a_file(
            b"100644 blob " + self.object_id + b"\tnested/.git/config",
            "A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden")

    def test_submodule_is_refused_as_unsupported_and_cleaned_up(self):
        self.assert_refused_after_materializing_a_file(
            b"160000 commit " + b"a" * 40 + b"\tsubmodule",
            "Frozen Router input with submodules is not supported")

    def test_expired_call_deadline_stops_preparation_before_git(self):
        directory = self.root / "expired"
        directory.mkdir()
        with mock.patch.object(router_input.time, "monotonic", side_effect=[100, 221]), \
             mock.patch.object(router_input.subprocess, "run") as git:
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory, deadline=220)
        self.assertEqual(refused.exception.code, "router-input-unknown")
        git.assert_not_called()

    def test_tree_failure_keeps_budget_shared_git_policy_and_bounded_stderr(self):
        directory = self.root / "tree-budget"
        directory.mkdir()
        diagnostic = b"e" * 3000 + b"git-tree-tail\xff"
        failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=diagnostic)
        with mock.patch.object(router_input.time, "monotonic", return_value=100), \
             mock.patch.object(router_input.subprocess, "run", return_value=failed) as git:
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory, deadline=340)
        self.assertEqual(refused.exception.code, "router-input-unavailable")
        self.assertEqual(git.call_args.kwargs["timeout"], 240)
        command, environment = router_input.workspace._git_command(
            self.checkout, "ls-tree", "-rz", "--full-tree", self.tree)
        self.assertEqual(git.call_args.args[0], command)
        self.assertEqual(git.call_args.kwargs["env"], environment)
        self.assertEqual(refused.exception.details["argv"], command)
        self.assertEqual(refused.exception.details["returncode"], 1)
        self.assertEqual(refused.exception.details["stderr"], diagnostic.decode(errors="replace")[-2000:])
        self.assertTrue(refused.exception.details["stderrTruncated"])

    def test_tree_timeout_retains_bounded_git_stderr_and_unknown_code(self):
        directory = self.root / "tree-timeout"
        directory.mkdir()
        diagnostic = b"e" * 3000 + b"timeout-tail"
        expired = subprocess.TimeoutExpired(["git"], 10, stderr=diagnostic)
        with mock.patch.object(router_input.subprocess, "run", side_effect=expired):
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
        self.assertEqual(refused.exception.code, "router-input-unknown")
        self.assertEqual(refused.exception.details["stderr"], diagnostic.decode()[-2000:])
        self.assertTrue(refused.exception.details["stderrTruncated"])
        self.assertFalse((directory / "frozen-input").exists())

    def test_batch_failure_captures_large_stderr_without_blocking_or_leaking_the_reader(self):
        diagnostic = b"e" * 262144 + b"batch-tail\xff"
        listing = subprocess.CompletedProcess([], 0, stdout=(
            b"100644 blob " + self.object_id + b"\tfirst.txt\0"))
        original_popen = subprocess.Popen
        for response, message in ((b"missing\n", "A frozen Git object is unavailable"),
                                  (self.object_id + b" blob 8\nshort", "A frozen Git object is truncated")):
            with self.subTest(message=message):
                directory = self.root / ("truncated" if b"short" in response else "missing")
                directory.mkdir()
                process = None

                def failed_reader(command, **kwargs):
                    nonlocal process
                    expected, environment = router_input.workspace._git_command(self.checkout, "cat-file", "--batch")
                    self.assertEqual(command, expected)
                    self.assertEqual(kwargs["env"], environment)
                    process = original_popen([
                        sys.executable, "-c", "import sys; sys.stdin.buffer.readline(); "
                        "sys.stderr.buffer.write(b'e' * 262144 + b'batch-tail\\xff'); sys.stderr.buffer.flush(); "
                        f"sys.stdout.buffer.write({response!r}); sys.stdout.buffer.flush(); sys.exit(128)",
                    ], **kwargs)
                    return process

                with mock.patch.object(router_input.subprocess, "run", return_value=listing), \
                        mock.patch.object(router_input.subprocess, "Popen", side_effect=failed_reader):
                    with self.assertRaises(BoardError) as refused:
                        router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
                self.assertEqual(refused.exception.code, "router-input-unavailable")
                self.assertEqual(refused.exception.message, message)
                self.assertEqual(refused.exception.details["stderr"], diagnostic.decode(errors="replace")[-2000:])
                self.assertTrue(refused.exception.details["stderrTruncated"])
                self.assertIsNotNone(process.returncode)
                self.assertEqual(list(directory.iterdir()), [])

    def test_blocked_blob_reader_is_reaped_at_call_deadline(self):
        directory = self.root / "batch"
        directory.mkdir()
        listing = subprocess.CompletedProcess([], 0, stdout=(
            b"100644 blob " + self.object_id + b"\tfirst.txt\0"))
        original_popen = subprocess.Popen
        process = None

        def blocked_reader(_command, **kwargs):
            nonlocal process
            process = original_popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
            return process

        with mock.patch.object(router_input.subprocess, "run", return_value=listing), \
             mock.patch.object(router_input.subprocess, "Popen", side_effect=blocked_reader):
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory,
                                     deadline=router_input.time.monotonic() + 0.1)
        self.assertEqual(refused.exception.code, "router-input-unknown")
        self.assertIsNotNone(process.returncode)
        self.assertFalse((directory / "frozen-input").exists())

    def test_indeterminate_workspace_verification_stays_unknown(self):
        self.enterContext(mock.patch.object(router_input.workspace, "verify",
                          side_effect=BoardError("WORKSPACE_GIT_UNKNOWN", "Git read timed out")))
        result = router_input.verify(self.manifest, self.checkout, "expected", deadline=time.monotonic() + 120)
        self.assertIsNone(result["unchanged"])
        self.assertEqual(result["code"], "router-input-unknown")

    def test_indeterminate_workspace_preparation_keeps_unknown_cause(self):
        directory = self.root / "unknown-workspace"
        directory.mkdir()
        self.enterContext(mock.patch.object(router_input.workspace, "verify",
                          side_effect=BoardError("WORKSPACE_GIT_UNKNOWN", "Git read timed out")))
        with mock.patch.object(router_input.subprocess, "run") as git:
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory, deadline=time.monotonic() + 120)
        self.assertEqual(refused.exception.code, "router-input-unknown")
        git.assert_not_called()

    def test_expired_local_verification_stays_unknown(self):
        with mock.patch.object(router_input.time, "monotonic", return_value=221), \
             mock.patch.object(router_input, "_digest") as digest:
            result = router_input.verify(None, self.checkout, "expected", deadline=220)
        self.assertIsNone(result["unchanged"])
        self.assertEqual(result["code"], "router-input-unknown")
        digest.assert_not_called()

    def test_digest_and_verification_use_the_caller_deadline(self):
        with mock.patch.object(router_input.time, "monotonic", return_value=100):
            expected = router_input.digest(self.checkout, deadline=340)
            result = router_input.verify(None, self.checkout, expected, deadline=340)
        self.assertTrue(result["unchanged"])

    def test_completed_digest_mismatch_is_changed(self):
        result = router_input.verify(None, self.checkout, "incorrect-digest", deadline=time.monotonic() + 120)
        self.assertIs(result["unchanged"], False)
        self.assertEqual(result["code"], "router-input-changed")


if __name__ == "__main__":
    unittest.main()
