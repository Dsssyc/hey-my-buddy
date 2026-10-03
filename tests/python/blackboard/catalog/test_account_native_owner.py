import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.account_native import CodexAccountProcess, harden_home, key_value, write_codex_key
from hey_my_buddy.blackboard.catalog.account_keystore import MacKeychain, open_store
from hey_my_buddy.errors import BoardError

FIXTURE = Path(__file__).resolve().parents[2] / 'buddy/harnesses/codex/fixtures/mock_account_codex.py'


class NativeAccountOwnerTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix='buddy-account-native-fixture-')
        self.addCleanup(self.root.cleanup)
        self.home = Path(self.root.name).resolve() / 'harnesses' / 'codex' / 'accounts' / 'worker'
        self.environment = {'HOME': self.root.name, 'PATH': os.environ.get('PATH', ''),
                            'BUDDY_STATE_DIR': self.root.name, 'BUDDY_RUNTIME_ROOT': str(Path(self.root.name) / 'runtime')}
        self.command = [sys.executable, str(FIXTURE)]

    def test_key_pipe_and_native_logout_keep_shared_home_untouched(self):
        shared = Path(self.root.name) / '.codex'
        shared.mkdir()
        (shared / 'auth.json').write_text('shared sentinel')
        handles = []
        write_codex_key(self.command, self.home, self.environment, 'test-key-private-only', on_spawn=handles.append)
        self.assertTrue(handles[0].shutdown_confirmed())
        self.assertFalse((self.home / 'tmp' / 'arg0').exists())
        self.assertNotIn('test-key-private-only', handles[0].process.args)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.home / 'auth.json').stat().st_mode), 0o600)
        owner = CodexAccountProcess(self.command, self.home, self.environment)
        self.addCleanup(owner.stop)
        owner.initialize()
        self.assertEqual(owner.read()['accountType'], 'apiKey')
        self.assertEqual(owner.logout()['status'], 'logged-out')
        self.assertEqual((shared / 'auth.json').read_text(), 'shared sentinel')

    def test_cancel_correlates_native_id_and_returns_no_browser_link(self):
        owner = CodexAccountProcess(self.command, self.home, self.environment)
        self.addCleanup(owner.stop)
        owner.initialize()
        started = owner.start_login()
        self.assertIn('authUrl', started)
        self.assertNotIn('native-fixture-id', str(owner.view()))
        cancelled = owner.cancel()
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertNotIn('authUrl', cancelled)
        self.assertTrue(owner.handle.shutdown_confirmed())

    def test_completion_is_native_and_stopped_before_adoption(self):
        owner = CodexAccountProcess([*self.command, '--complete'], self.home, self.environment)
        self.addCleanup(owner.stop)
        owner.initialize()
        owner.start_login()
        facts = owner.wait()
        self.assertEqual(owner.state, 'completed')
        self.assertEqual(facts['accountType'], 'chatgpt')
        self.assertTrue(owner.handle.shutdown_confirmed())
        self.assertNotIn('authUrl', str(facts))

    def test_unconfirmed_stop_never_becomes_cancelled(self):
        owner = CodexAccountProcess(self.command, self.home, self.environment)
        self.addCleanup(owner.stop)
        owner.initialize()
        owner.start_login()
        with patch.object(owner, 'stop', return_value=False):
            self.assertEqual(owner.cancel()['state'], 'unconfirmed')

    def test_permission_hardening_refuses_links_without_changing_target(self):
        self.home.mkdir(parents=True)
        target = Path(self.root.name) / 'unrelated'
        target.write_text('sentinel')
        target.chmod(0o644)
        (self.home / 'auth.json').symlink_to(target)
        with self.assertRaises(BoardError):
            harden_home(self.home)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(target.read_text(), 'sentinel')

    def test_unsupported_store_and_invalid_key_never_fall_back_or_echo(self):
        with patch('hey_my_buddy.blackboard.catalog.account_keystore.sys.platform', 'linux'):
            with self.assertRaises(BoardError) as unavailable:
                open_store()
        self.assertEqual(unavailable.exception.code, 'ACCOUNT_SECRET_STORE_UNAVAILABLE')
        secret = 'private-secret\nextra'
        with self.assertRaises(BoardError) as invalid:
            key_value(secret)
        self.assertNotIn('private-secret', str(invalid.exception.payload()))
        with self.assertRaises(BoardError):
            MacKeychain._query('native-shared-service', 'worker')


if __name__ == '__main__':
    unittest.main()
