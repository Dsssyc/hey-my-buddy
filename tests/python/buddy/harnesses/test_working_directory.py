"""The four native drivers retain a working directory rejection and stop."""
from contextlib import ExitStack
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run as claude
from hey_my_buddy.buddy.harnesses.codex import native_run as codex
from hey_my_buddy.buddy.harnesses.dsh import native_run as dsh
from hey_my_buddy.buddy.harnesses.zcode import native_run as zcode
from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE, PrivateStatePaths, RunBudget, RunBuddy, RunIdentity, RunRequest,
)
from hey_my_buddy.protocol.owned_process import WorkingDirectoryRejected, _rejected_cwd, owned_popen


class WorkingDirectoryClassificationTests(unittest.TestCase):
    def test_named_os_errors_distinguish_the_directory_from_the_executable(self):
        with tempfile.TemporaryDirectory() as root:
            cwd = os.path.join(root, "cwd")
            executable = os.path.join(root, "harness")
            for number in (errno.ENOENT, errno.ENOTDIR, errno.ENAMETOOLONG,
                           errno.EACCES, errno.EPERM, errno.ELOOP):
                with self.subTest(errno=number):
                    error = OSError(number, os.strerror(number), os.fsencode(cwd))
                    rejection = _rejected_cwd(error, Path(cwd), executable)
                    self.assertEqual(rejection.reason, WorkingDirectoryRejected.reason_for(
                        cwd, f"errno {number}: {error.strerror}"))
                    error = OSError(number, os.strerror(number), executable)
                    self.assertIsNone(_rejected_cwd(error, cwd, executable))
            self.assertIsNone(_rejected_cwd(PermissionError(errno.EACCES, "permission denied"),
                                            cwd, sys.executable))


@unittest.skipUnless(os.name == "posix", "requires POSIX chdir and exec")
class PosixWorkingDirectoryTests(unittest.TestCase):
    def assert_rejected_cwd(self, cwd, number, *, executable=sys.executable):
        with self.assertRaises(WorkingDirectoryRejected) as caught:
            owned_popen([executable], cwd=cwd)
        error = caught.exception
        self.assertEqual(error.__cause__.errno, number)
        self.assertEqual(error.reason, WorkingDirectoryRejected.reason_for(
            str(cwd), f"errno {number}: {error.__cause__.strerror}"))
        self.assertFalse(error.owned_popen_evidence.created)
        self.assertTrue(error.owned_popen_evidence.shutdown_confirmed)

    def assert_executable_error(self, cwd, executable, number):
        with self.assertRaises(OSError) as caught:
            owned_popen([str(executable)], cwd=cwd)
        self.assertEqual(caught.exception.errno, number)
        self.assertEqual(caught.exception.filename, str(executable))

    def test_long_component_and_symlink_loop_are_attributed_to_the_failing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            long_path = root / ("x" * (os.pathconf(root, "PC_NAME_MAX") + 1))
            loop = root / "loop"
            loop.symlink_to(loop.name)
            for path, number in ((long_path, errno.ENAMETOOLONG), (loop, errno.ELOOP)):
                with self.subTest(errno=number):
                    self.assert_rejected_cwd(path, number)
                    self.assert_executable_error(root, path, number)

    @unittest.skipIf(getattr(os, "geteuid", lambda: -1)() == 0, "root can traverse a mode-0600 directory")
    def test_directory_without_search_permission_is_rejected_even_if_named_as_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "blocked"
            blocked.mkdir(mode=0o600)
            try:
                self.assert_rejected_cwd(blocked, errno.EACCES)
                self.assert_rejected_cwd(blocked, errno.EACCES, executable=str(blocked))
            finally:
                blocked.chmod(0o700)

    def test_nonexecutable_file_and_searchable_directory_remain_exec_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "nonexecutable"
            executable.touch(mode=0o600)
            self.assert_executable_error(root, executable, errno.EACCES)
            self.assert_executable_error(root, root, errno.EACCES)


class WorkingDirectoryTests(unittest.TestCase):
    def check_run(self, harness, module, *, version_probe):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cwd = str(root / "missing-working-directory")
            request = RunRequest(
                identity=RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                     invocation_id="invocation"),
                harness=harness, buddy=RunBuddy(provider="fixture", model="fixture", effort="default"),
                cwd=cwd, input_text="fixture", permission="read", output_schema={"type": "object"},
                private_state=PrivateStatePaths(state_dir=str(root), invocation_root=str(root / "run"),
                                                native_root=str(root / "native")),
                budget=RunBudget(timeout_seconds=30),
            )
            entry = "launch_for" if harness == "dsh" else "launch"
            stack.enter_context(mock.patch.object(module, entry,
                                                  return_value=([sys.executable], dict(os.environ))))
            if harness == "zcode":
                stack.enter_context(mock.patch.object(zcode, "provider_paths", return_value=(root / "provider", None)))
                stack.enter_context(mock.patch.object(zcode, "load_runtime_preferences", return_value={}))
            if harness != "dsh" and not version_probe:
                stack.enter_context(mock.patch.object(module, "probe_version", return_value="fixture"))
            popen = stack.enter_context(mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen))
            result = module.run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                                services=None, cancelled=lambda: False)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs["cwd"], cwd)
            self.assertEqual("--version" in popen.call_args.args[0], version_probe)
            self.assertEqual(result.end.reason_code, WorkingDirectoryRejected.code)
            self.assertEqual(result.end.working_directory_rejection.cwd, cwd)
            self.assertIn("WinError 267" if os.name == "nt" else "errno",
                          result.end.working_directory_rejection.system_reason)
            self.assertEqual(result.stop_evidence.native.group_state, "gone")
            self.assertIs(result.native_request_sent, False)

    def test_working_directory_rejection_stops_the_run_at_its_version_probe(self):
        for harness, module in (("codex", codex), ("claude", claude), ("zcode", zcode)):
            with self.subTest(harness=harness):
                self.check_run(harness, module, version_probe=True)

    def test_working_directory_rejection_is_reported_at_native_spawn(self):
        for harness, module in (("codex", codex), ("claude", claude), ("zcode", zcode), ("dsh", dsh)):
            with self.subTest(harness=harness):
                self.check_run(harness, module, version_probe=False)
