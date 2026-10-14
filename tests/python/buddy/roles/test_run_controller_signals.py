"""Entry-time cancellation keeps the native receipt and the role's stop proof."""
from __future__ import annotations

import importlib
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_result
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.roles.controller import worker_executor

CONTROLLER = "hey_my_buddy.buddy.roles.run_controller"
SOURCE = Path(__file__).resolve().parents[4] / "src"
NATIVE_FIXTURE = Path(__file__).resolve().parents[1] / "harnesses/codex/fixtures/mock_codex.py"

# The import boundary is synchronous: this signal lands before the controller's
# first heavy import, with no elapsed-time assumption or readiness handshake.
EARLY_CANCEL_ENTRY = """
import builtins
import os
import runpy
import signal
import sys
from pathlib import Path

original_import = builtins.__import__
def import_and_cancel(name, globals=None, locals=None, fromlist=(), level=0):
    if (name == 'argparse' and globals is not None
            and globals.get('__name__') == '__main__'
            and globals['__spec__'].name == 'hey_my_buddy.buddy.roles.run_controller'):
        builtins.__import__ = original_import
        Path(sys.argv[2] + '.signal').write_text('SIGTERM before argparse')
        os.kill(os.getpid(), signal.SIGTERM)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_and_cancel
runpy.run_module('hey_my_buddy.buddy.roles.run_controller', run_name='__main__')
"""


class ControllerSignalTests(unittest.TestCase):
    def test_import_preserves_the_callers_signal_handlers(self):
        signals = (signal.SIGTERM, signal.SIGINT,
                   *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ()))
        before = {sig: signal.getsignal(sig) for sig in signals}
        with mock.patch.object(signal, "signal", wraps=signal.signal) as install:
            module = importlib.import_module(CONTROLLER)
            importlib.reload(module)
        install.assert_not_called()
        self.assertEqual({sig: signal.getsignal(sig) for sig in signals}, before)

    @unittest.skipIf(os.name == "nt", "self SIGTERM delivery and PID observation use POSIX")
    def test_cancel_before_heavy_import_has_a_collected_native_stop_receipt(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-controller-signals-",
                                         dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")) as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir(mode=0o700)
            native_pids = root / "native-pids"
            native = root / "fake-codex"
            # Record both the version probe and app-server. The real native run
            # still owns their processes and must stop them before its receipt.
            native.write_text(
                f"#!{sys.executable}\n"
                "import os, runpy\n"
                f"with open({str(native_pids)!r}, 'a') as log:\n"
                "    log.write(str(os.getpid()) + '\\n')\n"
                f"runpy.run_path({str(NATIVE_FIXTURE)!r}, run_name='__main__')\n")
            native.chmod(0o755)
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HMB_")
                           and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
            environment.update(HOME=str(root), CODEX_HOME=str(root / "codex-home"),
                               HMB_DEV_SOURCE="1", HMB_STATE_DIR=str(state),
                               HMB_RUNTIME_ROOT=str(root / "runtime"),
                               HMB_CODEX_CLI=str(native), HMB_CODEX_FIXTURE_CASE="ok",
                               HMB_CODEX_FIXTURE_STATE=str(root / "fixture.json"),
                               PYTHONPATH=str(SOURCE))
            turn_input = {"version": 1, "taskId": "micro-task-1", "attemptId": "attempt-1",
                          "generation": 1, "turnId": "turn-1", "resumeMode": "initial",
                          "previousSessionId": None, "context": {}, "executionWorkspace": {}}
            context = ExecutionContext(
                task_id="micro-task-1", attempt_id="attempt-1", generation=1,
                spec={"cwd": str(root), "task": "Write a fixture result", "timeoutSeconds": 8,
                      "provider": "openai", "model": "fixture-model", "effort": "low"},
                directory=root / "attempt", runtime={}, environment=environment,
                turn={"turnId": "turn-1", "input": turn_input})
            executor = worker_executor("codex")
            launch = run_execution.launch_controller

            def launch_early_cancel(*, prepare, **kwargs):
                def prepare_entry():
                    argv, cwd, env = prepare()
                    return [argv[0], "-c", EARLY_CANCEL_ENTRY, *argv[3:]], cwd, env
                return launch(prepare=prepare_entry, **kwargs)

            with mock.patch.object(run_execution, "launch_controller", side_effect=launch_early_cancel):
                handle = executor.start(context)
            try:
                self.assertEqual(handle.wait(20), 1, Path(handle.log_paths["stderr"]).read_text())
                control = handle.role_run_control
                self.assertEqual((Path(control["privateRoot"]) / "role-run-control.json.signal")
                                 .read_text(), "SIGTERM before argparse")
                result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
                self.assertEqual(result.end.status, "cancelled")
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                self.assertTrue(Path(control["requestFile"]).is_file())
                self.assertTrue(Path(control["verdictFile"]).is_file())
                outcome = executor.collect(handle, context)
                self.assertEqual(outcome.status, "cancelled", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
                pids = [int(pid) for pid in native_pids.read_text().splitlines()]
                self.assertGreaterEqual(len(pids), 2, "version probe and app-server must both run")
                for pid in pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
            finally:
                if handle.group_alive():
                    handle.terminate(grace_seconds=0.2)


if __name__ == "__main__":
    unittest.main()
