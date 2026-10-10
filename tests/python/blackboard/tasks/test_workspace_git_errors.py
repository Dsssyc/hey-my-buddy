"""Bounded diagnostics for every workspace Git subprocess failure path."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class GitErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-workspace-git-errors-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stdout = b"o" * 3000 + b"stdout-tail\xff"
        self.stderr = b"e" * 3000 + b"stderr-tail\xfe"
        self.oid = workspace._blob_oid(b"payload", "sha1")

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

    def test_nonzero_entries_return_actual_argv_and_both_bounded_outputs(self):
        for entry in self.run_entries():
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 128, self.stdout, self.stderr)
                error = self.failure(lambda: entry(self.root, "status", "--porcelain=v1"))
                self.diagnostics(error, run.call_args.args[0], "status")
                self.assertEqual(error.details["returncode"], 128)
                self.assertEqual(run.call_args.args[0][-2:], ["status", "--porcelain=v1"])

    def test_timeout_entries_preserve_cause_and_captured_binary_outputs(self):
        for entry in self.run_entries():
            failure = subprocess.TimeoutExpired(["placeholder"], 60, output=self.stdout, stderr=self.stderr)
            with self.subTest(entry=entry.__name__), patch.object(workspace.subprocess, "run", side_effect=failure) as run:
                error = self.failure(lambda: entry(self.root, "rev-parse", "HEAD"))
                self.diagnostics(error, run.call_args.args[0], "rev-parse")
                self.assertIs(error.__cause__, failure)

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

    def importer(self, *, code=128, broken=False, timed_out=False, marks=None):
        """Drive the real stream/capture code with a controlled child handle."""
        state = {}

        class BrokenInput(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError("controlled broken pipe")

        class Child:
            returncode = None
            killed = False

            def __init__(self):
                self.stdin = BrokenInput() if broken else io.BytesIO()
                self.wait_calls = 0

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                self.wait_calls += 1
                if timed_out and timeout is not None:
                    failure = subprocess.TimeoutExpired(state["command"], timeout)
                    state["failure"] = failure
                    raise failure
                self.returncode = -9 if self.killed else code
                return self.returncode

        child = Child()
        state["child"] = child

        def start(command, *, stdout, stderr, **kwargs):
            state["command"] = command
            stdout.write(self.stdout)
            stderr.write(self.stderr)
            if marks is not None:
                marks_path = next(item.split("=", 1)[1] for item in command if item.startswith("--export-marks="))
                Path(marks_path).write_bytes(marks)
            return child

        return state, start

    def test_fast_import_nonzero_precedes_missing_marks_and_reports_separate_outputs(self):
        state, start = self.importer()
        with patch.object(workspace.subprocess, "Popen", side_effect=start):
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, state["command"], "fast-import")
        self.assertEqual(error.details["returncode"], 128)
        self.assertIsNone(error.__cause__)

    def test_fast_import_launch_oserror_reports_argv_and_preserves_cause(self):
        failure = OSError("controlled spawn failure")
        with patch.object(workspace.subprocess, "Popen", side_effect=failure) as start:
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, start.call_args.args[0], "fast-import", output=False)
        self.assertIs(error.__cause__, failure)
        self.assertIn("controlled spawn failure", error.details["reason"])

    def test_fast_import_broken_pipe_reaps_child_and_keeps_diagnostics_and_cause(self):
        state, start = self.importer(broken=True)
        with patch.object(workspace.subprocess, "Popen", side_effect=start):
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, state["command"], "fast-import")
        self.assertIsInstance(error.__cause__, BrokenPipeError)
        self.assertTrue(state["child"].killed)
        self.assertEqual(state["child"].wait_calls, 1)
        self.assertEqual(error.details["returncode"], -9)

    def test_fast_import_timeout_reaps_child_and_keeps_diagnostics_and_cause(self):
        state, start = self.importer(timed_out=True)
        with patch.object(workspace.subprocess, "Popen", side_effect=start):
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, state["command"], "fast-import")
        self.assertIs(error.__cause__, state["failure"])
        self.assertTrue(state["child"].killed)
        self.assertEqual(state["child"].wait_calls, 2)

    def test_fast_import_missing_marks_after_success_still_names_the_command(self):
        state, start = self.importer(code=0)
        with patch.object(workspace.subprocess, "Popen", side_effect=start):
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, state["command"], "fast-import")
        self.assertIsInstance(error.__cause__, FileNotFoundError)

    def test_fast_import_incorrect_marks_still_names_the_command(self):
        state, start = self.importer(code=0, marks=b":1 wrong-object-id\n")
        with patch.object(workspace.subprocess, "Popen", side_effect=start):
            error = self.failure(lambda: workspace._write_objects(self.root, {self.oid: b"payload"}))
        self.diagnostics(error, state["command"], "fast-import")
        self.assertEqual(error.details["expected"], self.oid)

    def test_batch_object_response_failures_report_argv_and_bounded_stdout(self):
        payloads = (self.oid.encode() + b" missing\n" + b"x" * 3000,
                    self.oid.encode() + b" blob 1\nx\n" + b"x" * 3000)
        for payload in payloads:
            with self.subTest(payload=payload[:70]), patch.object(workspace, "_git", return_value=payload):
                error = self.failure(lambda: workspace._batch_objects(self.root, [self.oid]))
                command, _env = workspace._git_command(self.root, "cat-file", "--batch")
                self.diagnostics(error, command, "cat-file", output=False)
                self.assertEqual(error.details["stdout"], payload.decode(errors="replace")[-2000:])
                self.assertTrue(error.details["stdoutTruncated"])
                self.assertEqual(error.details["returncode"], 0)

    def test_stream_diagnostics_read_only_a_bounded_tail(self):
        class RecordingStream(io.BytesIO):
            read_size = None

            def read(self, *args):
                value = super().read(*args)
                self.read_size = len(value)
                return value

        stream = RecordingStream(b"a" * 100000 + "尾".encode() * 2000)
        text, truncated = workspace._git_diagnostic(stream)
        self.assertEqual(text, "尾" * 2000)
        self.assertTrue(truncated)
        self.assertLessEqual(stream.read_size, 8000)


if __name__ == "__main__":
    unittest.main()
