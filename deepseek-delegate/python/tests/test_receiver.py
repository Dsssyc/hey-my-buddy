"""Receiver lifecycle tests never submit events or contact a native App pipe."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from buddy.receiver import ReceiverInbox, _endpoint, _healthy, _request, register_notification, stop_receiver
from buddy.transport import ServiceError

THREAD = '00000000-0000-4000-8000-000000000001'


class ReceiverControlTests(unittest.TestCase):
    def test_management_auth_and_binding_boundaries(self):
        native, loop, stopping = Mock(), Mock(), Mock()
        native.bind.return_value = 'run-binding-token'
        inbox = ReceiverInbox(native, 'private-control-token', 'ipc:///tmp/mock', loop, stopping)
        self.assertEqual(json.loads(inbox.ping('wrong'))['error']['code'], 'UNAUTHORIZED')
        self.assertEqual(json.loads(inbox.stop('wrong'))['error']['code'], 'UNAUTHORIZED')
        loop.call_soon_threadsafe.assert_not_called()
        args = {'token': 'private-control-token', 'requestId': 'request-1', 'threadId': THREAD, 'pipePath': '/tmp/fake-app-pipe'}
        self.assertEqual(json.loads(inbox.bind(json.dumps(args))), {'address': 'ipc:///tmp/mock', 'token': 'run-binding-token', 'threadId': THREAD})
        native.bind.assert_called_once_with('request-1', THREAD, pipe_path='/tmp/fake-app-pipe')
        for mutation in ({'pipePath': 'relative'}, {'threadId': 'bad'}, {'requestId': ''}, {'extra': True}, {'token': 'wrong'}):
            self.assertIn('error', json.loads(inbox.bind(json.dumps(args | mutation))))
        self.assertIn('error', json.loads(inbox.bind('x' * 16385)))
        self.assertEqual(native.bind.call_count, 1)

    def test_registration_requires_host_pipe_before_start(self):
        with patch.dict(os.environ, {}, clear=True), patch('buddy.receiver._ensure_receiver') as ensure:
            with self.assertRaises(ServiceError):
                register_notification('request', THREAD)
            ensure.assert_not_called()

    def test_stop_never_starts_receiver(self):
        with tempfile.TemporaryDirectory() as directory, patch('buddy.receiver.subprocess.Popen') as spawn:
            self.assertTrue(stop_receiver(directory)['alreadyStopped'])
            spawn.assert_not_called()

    def test_daemon_survives_client_exit_and_bindings_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, BUDDY_STATE_DIR=directory, CODEX_APP_TOOLS_PIPE_PATH='/tmp/buddy-test-never-connected', C2_RELAY_ANCHOR_ADDRESS='', C2_ENV_FILE='')
            script = 'from buddy.receiver import register_notification; import json; print(json.dumps(register_notification("request-1", "' + THREAD + '")))'
            try:
                child = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=25, check=False)
                self.assertEqual(child.returncode, 0, child.stderr + (Path(directory) / 'notifications/receiver.log').read_text())
                binding = json.loads(child.stdout.strip().splitlines()[-1])
                notifications = Path(directory) / 'notifications'
                endpoint = _healthy(notifications)
                self.assertIsNotNone(endpoint)
                self.assertNotEqual(binding['token'], endpoint['token'])
                self.assertEqual((notifications / 'receiver.json').stat().st_mode & 0o077, 0)
                with self.assertRaises(ServiceError):
                    _request(endpoint | {'token': 'wrong'}, 'ping', 'wrong')
                second = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=25, check=False)
                self.assertEqual(json.loads(second.stdout.strip().splitlines()[-1]), binding)
                self.assertEqual(_endpoint(notifications)['pid'], endpoint['pid'])
            finally:
                stop_receiver(directory)
                deadline = time.monotonic() + 8
                while (Path(directory) / 'notifications/receiver.json').exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse((Path(directory) / 'notifications/receiver.json').exists())


if __name__ == '__main__':
    unittest.main()
