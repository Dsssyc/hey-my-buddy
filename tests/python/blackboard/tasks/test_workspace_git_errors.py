"""Bounded diagnostics for every workspace Git subprocess failure path."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import service_process


class GitErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-workspace-git-errors-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stdout = b"o" * 3000 + b"stdout-tail\xff"
        self.stderr = b"e" * 3000 + b"stderr-tail\xfe"

    def failure(self, callback):
        with self.assertRaises(BoardError) as caught:
            callback()
        self.assertEqual(caught.exception.code, "WORKSPACE_GIT_ERROR")
        return caught.exception

    def diagnostics(self, error, command, operation, *, output=True):
        details = error.details
        self.assertIn("argv", details)
        self.assertEqual(details["argv"], command)
        self.assertEqual(details["operation"], operation)
        for field in ("stderr", "stdout", "reason"):
            self.assertIn(field, details)
            self.assertIn(field + "Truncated", details)
            self.assertLessEqual(len(details[field]), 2000)
            self.assertIsInstance(details[field + "Truncated"], bool)
        if output:
            self.assertEqual(details["stdout"], self.stdout.decode(errors="replace")[-2000:])
            self.assertEqual(details["stderr"], self.stderr.decode(errors="replace")[-2000:])
            self.assertTrue(details["stdoutTruncated"])
            self.assertTrue(details["stderrTruncated"])
            self.assertTrue(details["reasonTruncated"])

    def run_entries(self):
        return (workspace._git, workspace._git_in)

    def test_service_git_children_all_use_the_shared_no_window_policy(self):
        with patch.object(service_process, "os", SimpleNamespace(name="nt")):
            for entry in self.run_entries():
                with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run") as run:
                    run.return_value = subprocess.CompletedProcess([], 0, b"ok", b"")
                    self.assertEqual(entry(self.root, "status"), b"ok")
                    self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)

    def test_nonzero_entries_return_actual_argv_and_both_bounded_outputs(self):
        for entry in self.run_entries():
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 128, self.stdout, self.stderr)
                error = self.failure(lambda: entry(self.root, "status", "--porcelain=v1"))
                self.diagnostics(error, run.call_args.args[0], "status")
                self.assertEqual(error.details["returncode"], 128)
                self.assertEqual(run.call_args.args[0][-2:], ["status", "--porcelain=v1"])

    def test_timeout_entries_keep_unknown_and_bounded_diagnostics(self):
        for entry in self.run_entries():
            failure = subprocess.TimeoutExpired(["placeholder"], 60, output=self.stdout, stderr=self.stderr)
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run", side_effect=failure) as run, \
                 self.assertRaises(BoardError) as caught:
                entry(self.root, 'rev-parse', 'HEAD')
            self.assertEqual(caught.exception.code, 'WORKSPACE_GIT_UNKNOWN')
            self.diagnostics(caught.exception, run.call_args.args[0], "rev-parse")
            self.assertIs(caught.exception.__cause__, failure)
            self.assertEqual(run.call_args.kwargs["timeout"], workspace._GIT_TIMEOUT_SECONDS)
            self.assertEqual(run.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")

    def test_commit_and_inspect_do_not_reclassify_unknown_git_read(self):
        unknown = BoardError("WORKSPACE_GIT_UNKNOWN", "controlled timeout")
        with patch.object(workspace, "_line", side_effect=unknown):
            with self.assertRaises(BoardError) as commit:
                workspace._commit(self.root, "HEAD")
            with self.assertRaises(BoardError) as inspect:
                workspace.inspect(str(self.root))
        self.assertIs(commit.exception, unknown)
        self.assertIs(inspect.exception, unknown)

    def test_oserror_entries_preserve_cause_and_bound_the_exception_reason(self):
        for entry in self.run_entries():
            failure = OSError("unavailable " + "x" * 3000)
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run", side_effect=failure) as run:
                error = self.failure(lambda: entry(self.root, "worktree", "list"))
                self.diagnostics(error, run.call_args.args[0], "worktree", output=False)
                self.assertIs(error.__cause__, failure)
                self.assertTrue(error.details["reasonTruncated"])
                self.assertEqual(error.details["stdout"], "")
                self.assertEqual(error.details["stderr"], "")

    def test_real_git_failures_identify_the_exact_operation(self):
        for entry in self.run_entries():
            with self.subTest(entry=entry.__name__):
                error = self.failure(lambda: entry(self.root, "not-a-buddy-git-command"))
                self.assertEqual(error.details["argv"][-1], "not-a-buddy-git-command")
                self.assertEqual(error.details["argv"][0], "git")
                self.assertIn("not-a-buddy-git-command", error.details["stderr"])
                self.assertFalse(error.details["stderrTruncated"])
                self.assertNotEqual(error.details["returncode"], 0)

    def test_allowed_codes_remain_successful_and_do_not_export_input_or_environment(self):
        for entry in self.run_entries():
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 1, b"allowed", b"expected")
                self.assertEqual(entry(self.root, "diff", "--quiet", allowed=(0, 1)), b"allowed")
        with patch.object(workspace.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, b"", b"failure")
            error = self.failure(lambda: workspace._git(self.root, "cat-file", "--batch", data=b"private-input",
                                                        env={"PRIVATE_TEST_VALUE": "private-environment"}))
            self.assertNotIn("private-input", str(error.payload()))
            self.assertNotIn("private-environment", str(error.payload()))



if __name__ == "__main__":
    unittest.main()
