"""Real service/HTTP boundaries with private protocol and key-store substitutes."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from buddy import accounts
from buddy.db import canonical_json, utc_now
from buddy.errors import BoardError
from buddy.private_dirs import account_root
from test_workflow import WorkflowTestCase

FIXTURE = Path(__file__).parent / 'fixtures' / 'mock_account_codex.py'
FLAGS = {name: True for name in accounts.CAPABILITIES}


class FakeStore:
    def __init__(self):
        self.values = {}

    def put(self, service, account, value):
        self.values[(service, account)] = value

    def get(self, service, account):
        return self.values.get((service, account))

    def delete(self, service, account):
        self.values.pop((service, account), None)


class AccountOperationTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        cleared = {'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY', 'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID',
            'BUDDY_AGENT_CREDENTIAL', 'BUDDY_AGENT_CREDENTIAL_FILE', 'BUDDY_ACCOUNT_SELECTION', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}
        values = {key: value for key, value in os.environ.items() if key not in cleared}
        runtime = self.directory / 'runtime'
        runtime.mkdir(mode=0o700)
        values.update(BUDDY_STATE_DIR=str(self.directory), BUDDY_RUNTIME_ROOT=str(runtime), BUDDY_DEV_SOURCE='1',
            BUDDY_CODEX_CLI=str(self.directory / 'no-real-codex'), BUDDY_CLAUDE_CLI=str(self.directory / 'no-real-claude'))
        self.enterContext(patch.dict(os.environ, values, clear=True))
        self.enterContext(patch('buddy.harness_discovery.discover', side_effect=AssertionError('Real native CLI forbidden')))
        self.store = FakeStore()
        self.enterContext(patch('buddy.account_keystore.open_store', return_value=self.store))
        provider = accounts.WorkerAccountProvider(environment=lambda state, account, env, purpose: dict(env), capabilities=FLAGS)
        for adapter in ('codex', 'claude'):
            self.enterContext(accounts.using_provider(adapter, provider))

    def selected(self, adapter='codex', *, complete=False):
        board = self.board()
        self.addCleanup(board.service.account_operations.close)
        command = [sys.executable, str(FIXTURE), *(['--complete'] if complete else [])]
        with board.store.db.write() as db:
            record = {'version': 'fixture', 'command': command, 'account': {'source': 'native', 'credentialRevision': 0}}
            db.execute('UPDATE harness_health SET status=?,record_json=? WHERE adapter=?',
                ('ready', canonical_json(record), adapter))
        result = board.call('account_set', {'adapter': adapter, 'source': 'worker', 'expectedRevision': 0})
        self.assertEqual(result['account']['revision'], 1)
        return board

    def assert_secret_free(self, board, secret):
        with board.store.db.read() as db:
            for table in ('meta', 'commands', 'events', 'attempts', 'workflow_artifacts'):
                rows = [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                self.assertNotIn(secret, json.dumps(rows))
        from buddy.backup import preflight
        self.assertNotIn(secret, json.dumps(preflight(board.store.directory)))
        self.assertNotIn('harnesses/codex/accounts', json.dumps(preflight(board.store.directory)))

    def test_stdin_native_key_roundtrip_and_logout_have_no_secret_receipts(self):
        board = self.selected()
        secret = 'fixture-private-key-cannot-be-replayed'
        reply = board.call('account_login', {'adapter': 'codex', 'mode': 'api-key', 'expectedRevision': 1, 'apiKey': secret})
        self.assertEqual(reply['account']['credentialRevision'], 1)
        self.assertEqual(reply['account']['status'], 'ready')
        self.assertNotIn(secret, json.dumps(reply))
        self.assert_secret_free(board, secret)
        logged_out = board.call('account_logout', {'adapter': 'codex', 'expectedRevision': 1})
        self.assertEqual(logged_out['account']['status'], 'logged-out')
        self.assertEqual(logged_out['account']['credentialRevision'], 2)

    def test_oauth_link_is_ephemeral_and_cancel_retains_actual_stop_evidence(self):
        board = self.selected()
        started = board.call('account_login', {'adapter': 'codex', 'mode': 'oauth', 'expectedRevision': 1})
        ticket = started['login']
        self.assertTrue(ticket['authUrl'].startswith('https://auth.openai.com/'))
        current = board.call('account_status', {'adapter': 'codex', 'loginId': ticket['loginId']})
        self.assertNotIn('authUrl', current['login'])
        self.assert_secret_free(board, ticket['authUrl'])
        cancelled = board.call('account_cancel', {'adapter': 'codex', 'loginId': ticket['loginId']})
        self.assertEqual(cancelled['login']['state'], 'cancelled')
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT value FROM meta WHERE key LIKE 'account-mutation:%'").fetchone())

    def test_oauth_completion_is_adopted_after_native_stop_and_no_link_in_records(self):
        board = self.selected(complete=True)
        started = board.call('account_login', {'adapter': 'codex', 'mode': 'oauth', 'expectedRevision': 1})
        login_id = started['login']['loginId']
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = board.call('account_status', {'adapter': 'codex', 'loginId': login_id})
            if status['login']['state'] == 'completed' and status['account']['status'] == 'ready':
                break
            time.sleep(0.01)
        self.assertEqual(status['account']['status'], 'ready')
        self.assert_secret_free(board, started['login']['authUrl'])

    def test_shared_login_mutation_and_wrong_revision_are_refused_before_native_spawn(self):
        board = self.selected()
        board.call('account_set', {'adapter': 'codex', 'source': 'native', 'expectedRevision': 1})
        with patch('buddy.account_operations.CodexAccountProcess') as native:
            with self.assertRaises(BoardError) as refused:
                board.call('account_login', {'adapter': 'codex', 'mode': 'oauth', 'expectedRevision': 2})
            self.assertEqual(refused.exception.code, 'ACCOUNT_NATIVE_READ_ONLY')
            native.assert_not_called()

    def test_claude_store_key_only_enters_the_actual_native_environment(self):
        board = self.selected('claude')
        secret = 'fixture-claude-key-systems-only'
        reply = board.call('account_login', {'adapter': 'claude', 'mode': 'api-key', 'expectedRevision': 1, 'apiKey': secret})
        self.assertEqual(reply['account']['accountType'], 'metered')
        from buddy.harness_discovery import native_environment
        marker = {'adapter': 'claude', 'source': 'worker', 'revision': 1, 'credentialRevision': 1}
        env = {**os.environ, 'BUDDY_ACCOUNT_SELECTION': canonical_json(marker),
            'CLAUDE_CONFIG_DIR': str(account_root(board.store.directory, 'claude')), 'ANTHROPIC_API_KEY': 'inherited-key-must-drop'}
        self.assertNotIn('ANTHROPIC_API_KEY', native_environment(env))
        native = native_environment(env, adapter='claude')
        self.assertEqual(native['ANTHROPIC_API_KEY'], secret)
        self.assertNotIn('BUDDY_ACCOUNT_SELECTION', native)
        self.assert_secret_free(board, secret)
        board.call('account_remove', {'adapter': 'claude', 'expectedRevision': 1})
        self.assertFalse(self.store.values)

    def test_debug_native_failure_never_echoes_key_or_saves_a_replay(self):
        board = self.selected('claude')
        secret = 'fixture-debug-secret-never-public'
        with patch.dict(os.environ, {'BUDDY_DEBUG': '1'}), patch.object(self.store, 'put', side_effect=RuntimeError(secret)):
            with self.assertRaises(BoardError) as failed:
                board.call('account_login', {'adapter': 'claude', 'mode': 'api-key', 'expectedRevision': 1, 'apiKey': secret})
        self.assertNotIn(secret, json.dumps(failed.exception.payload()))
        self.assert_secret_free(board, secret)

    def test_cancel_during_synchronous_key_store_write_keeps_the_admission_fence(self):
        board = self.selected('claude')
        started, finish = threading.Event(), threading.Event()
        errors = []
        def blocked(*args):
            started.set()
            finish.wait(3)
        def write():
            try:
                board.call('account_login', {'adapter': 'claude', 'mode': 'api-key', 'expectedRevision': 1, 'apiKey': 'fixture'})
            except Exception as error:
                errors.append(type(error).__name__)
        with patch.object(self.store, 'put', side_effect=blocked):
            thread = threading.Thread(target=write)
            thread.start()
            self.assertTrue(started.wait(2))
            operation = next(iter(board.service.account_operations.operations.values()))
            board.call('account_cancel', {'adapter': 'claude', 'loginId': operation.id})
            with board.store.db.read() as db:
                self.assertIsNotNone(db.execute("SELECT value FROM meta WHERE key LIKE 'account-mutation:%'").fetchone())
            finish.set()
            thread.join(3)
        self.assertFalse(errors)

    def test_cli_refuses_key_in_arguments_and_delivers_only_stdin_key_to_rpc(self):
        from buddy import cli
        secret = 'fixture-cli-private-key'
        params = {'adapter': 'codex', 'mode': 'api-key', 'expectedRevision': 1}
        with patch.object(cli, 'call_service', return_value={'account': {}}) as rpc, patch('sys.stdin', io.StringIO(secret)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['account-login', json.dumps(params)]), 0)
            self.assertEqual(rpc.call_args.args[1]['apiKey'], secret)
        output = io.StringIO()
        with patch.object(cli, 'call_service') as rpc, contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['account-login', json.dumps({**params, 'apiKey': secret})]), 1)
            rpc.assert_not_called()
        self.assertNotIn(secret, output.getvalue())
