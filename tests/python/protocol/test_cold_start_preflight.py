"""Cold startup proves native local IPC even with deeply nested temporary roots."""
from __future__ import annotations

import json
import subprocess

from install.test_packaging import launcher_argv
from support import BoardTestCase
from hey_my_buddy.protocol import transport


class ColdStartPreflightTests(BoardTestCase):
    def test_launcher_cold_starts_a_usable_native_endpoint_with_a_long_tmpdir(self):
        self.catalog_fixture()
        temporary = self.directory / ("long-temporary-component-" * 5)
        temporary.mkdir()
        environment = self.child_environment({"TMPDIR": str(temporary), "HMB_DEV_SOURCE": "1",
                                              "HMB_MAX_CONCURRENT": "1", "HMB_CONSOLE_PORT": "0"})
        completed = subprocess.run(launcher_argv("health"), cwd=self.directory, env=environment,
                                   capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        reply = json.loads(completed.stdout)
        endpoint = transport._read_endpoint(self.directory)
        self.assertIsNotNone(endpoint)
        health = transport._request(endpoint, "health", {}, state_dir=self.directory)
        self.assertEqual(health["serviceId"], reply["serviceId"])
        self.assertEqual(health["stateDir"], str(self.directory))
        self.assertEqual(list(temporary.iterdir()), [])

