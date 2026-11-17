"""A private service drains an admitted HTTP transaction before closing SQLite."""
import json
import subprocess
import sys
from pathlib import Path

from support import BoardTestCase


class ShutdownConnectionTests(BoardTestCase):
    def test_private_service_exits_after_inflight_console_read_and_removes_endpoint(self):
        fixture = Path(__file__).parent / "fixtures" / "daemon_with_blocked_console.py"
        process = subprocess.run([sys.executable, str(fixture)], env=self.child_environment(),
                                 capture_output=True, text=True, timeout=90, start_new_session=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["responses"], [[200, {"pendingCount": 0}]])
        self.assertEqual(report["result"], 0)
        self.assertTrue(report["endpointRemoved"])
        self.assertFalse((self.directory / "control.json").exists())
