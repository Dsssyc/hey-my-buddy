"""Discovery controllers receive stops through stdin without altering caller signals."""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.protocol.process_controller import launch_controller
from hey_my_buddy.buddy.harnesses.controller import read_strict_result, stop_confirmed
from hey_my_buddy.paths import native_path

CONTROLLER = "hey_my_buddy.buddy.roles.run_controller"


class DiscoverySignalTests(unittest.TestCase):
    def test_import_preserves_the_callers_signal_handlers(self):
        signals = (signal.SIGTERM, signal.SIGINT,
                   *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ()))
        before = {sig: signal.getsignal(sig) for sig in signals}
        with mock.patch.object(signal, "signal", wraps=signal.signal) as install:
            module = importlib.import_module(CONTROLLER)
            importlib.reload(module)
        install.assert_not_called()
        self.assertEqual({sig: signal.getsignal(sig) for sig in signals}, before)

    def test_controller_eof_delivers_cancel_and_returns_a_confirmed_result(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            control = root / "control.json"
            ready = root / "ready"
            control.write_text(json.dumps({
                "operation": "discover", "arguments": {}, "harness": "codex",
                "cwd": name, "privateRoot": name, "nativeRoot": str(root / "native"),
                "timeoutSeconds": 25}), encoding="utf-8")
            # Model-free seam: exercise the actual controller entry, input
            # reader and result frame rather than substitute an outer receipt.
            source = (
                "import sys,time; from pathlib import Path\n"
                "from hey_my_buddy.buddy.roles import run_controller as controller\n"
                "def discover(**kwargs):\n"
                f"    Path({str(ready)!r}).touch()\n"
                "    while not kwargs['cancelled'](): time.sleep(.01)\n"
                "    return {'cancelled': True}\n"
                "controller.native_operation = lambda *args: discover\n"
                f"sys.argv = ['controller', '--control', {str(control)!r}]\n"
                "raise SystemExit(controller.main())\n")
            logs = {"stdout": str(root / "stdout"), "stderr": str(root / "stderr")}
            environment = {**os.environ, "HMB_STATE_DIR": name}
            handle = launch_controller(prepare=lambda: ([sys.executable, "-c", source], name, environment),
                                       log_paths=logs, stop_channel="stdin")
            try:
                deadline = time.monotonic() + 10
                while not ready.exists():
                    self.assertIsNone(handle.process.poll(), native_path(Path(logs["stderr"])).read_text())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                stopped, escalated = handle.terminate(grace_seconds=8)
                self.assertTrue(stopped)
                payload = read_strict_result(Path(logs["stdout"]))
                self.assertEqual(payload["catalog"], {"cancelled": True})
                self.assertTrue(stop_confirmed(payload, handle.shutdown_confirmed))
                if os.name != "nt" or not escalated:
                    self.assertEqual(handle.process.returncode, 0)
                self.assertIsNotNone(handle.process.returncode)
            finally:
                handle.terminate(grace_seconds=0)


if __name__ == "__main__":
    unittest.main()
