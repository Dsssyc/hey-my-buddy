"""Cold startup proves native local IPC even with deeply nested temporary roots."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from install.test_packaging import launcher_argv
from support import BoardTestCase
from hey_my_buddy.protocol import transport
from hey_my_buddy.protocol.contracts import BuddyControl, CONTROL_NAME


class ColdStartPreflightTests(BoardTestCase):
    def test_launcher_cold_starts_a_usable_native_endpoint_with_a_long_tmpdir(self):
        self.catalog_fixture()
        temporary = self.directory / ("long-temporary-component-" * 5)
        temporary.mkdir()
        with patch.dict(os.environ, {'TMPDIR': str(temporary)}):
            transport._cold_start_preflight(self.directory)
        self.assertEqual(list(temporary.iterdir()), [])
        self.assertFalse(list(self.directory.glob('.launch-ipc-*')))
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
        self.assertFalse(list(self.directory.glob('.launch-ipc-*')))

    @unittest.skipIf(os.name == 'nt', 'POSIX independent probe namespace')
    def test_probe_preserves_an_existing_native_connection_and_leaves_no_files(self):
        self.catalog_fixture()
        with self.daemon():
            endpoint = transport._read_endpoint(self.directory)
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint['address']) as peer:
                before = sorted(path.name for path in self.directory.iterdir())
                transport._cold_start_preflight(self.directory)
                reply = json.loads(peer.health(json.dumps({'token': endpoint['token']})))
                self.assertNotIn('error', reply)
                self.assertEqual(reply['serviceId'], endpoint['serviceId'])
                self.assertEqual(sorted(path.name for path in self.directory.iterdir()), before)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS sandbox bind denial')
    def test_sandbox_bind_denial_reaches_the_launcher_before_install_or_spawn(self):
        self.catalog_fixture()
        environment = self.child_environment()
        completed = subprocess.run(['/usr/bin/sandbox-exec', '-p',
            '(version 1)(allow default)(deny network-bind)', *launcher_argv('health')],
            cwd=self.directory, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=180)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        error = json.loads(completed.stdout)['error']
        self.assertEqual(error['code'], 'LAUNCH_ACCESS_DENIED', error)
        self.assertEqual(error['details']['io_operation'], 'bind', error)
        self.assertIn('Operation not permitted', error['message'])
        self.assertFalse((self.directory / 'board.sqlite3').exists())
        self.assertFalse((self.directory / 'control.log').exists())
        self.assertFalse((self.directory / 'control-start.lock').exists())
        self.assertFalse(list(self.directory.glob('.launch-ipc-*')))
        self.assertFalse(list(self.directory.rglob('READY.json')))

