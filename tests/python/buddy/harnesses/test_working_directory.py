"""The four native drivers retain a working directory rejection and stop."""
from contextlib import ExitStack
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
from hey_my_buddy.protocol.owned_process import WorkingDirectoryRejected


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
