"""Account consumption and native bindings against private protocol fixtures."""
import json
from pathlib import Path
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import NoToolStructuredRequest, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses import controller
from hey_my_buddy.buddy.harnesses.codex.home import credential_source
from hey_my_buddy.buddy.roles import structured_call as read_only
from hey_my_buddy.errors import BoardError
import buddy.harnesses.codex.test_codex as codex_tests


class AccountBindingTests(unittest.TestCase):
    context = codex_tests.CodexAdapterTests.context
    execute = codex_tests.CodexAdapterTests.execute

    def setUp(self):
        codex_tests.CodexAdapterTests.setUp(self)
        Path(self.environment['BUDDY_RUNTIME_ROOT']).mkdir(mode=0o700)
        sentinel = mock.patch('hey_my_buddy.buddy.harnesses.discovery.discover',
                              side_effect=AssertionError('Native CLI discovery is forbidden in account fixtures'))
        sentinel.start()
        self.addCleanup(sentinel.stop)

    def account(self, *, source='native', revision=1, credential_revision=0):
        return {'adapter': 'codex', 'source': source, 'revision': revision,
                'credentialRevision': credential_revision}

    def test_selection_revision_does_not_change_credential_binding(self):
        state = Path(self.environment['BUDDY_STATE_DIR'])
        first = credential_source(state, self.environment, account=self.account(revision=1))
        second = credential_source(state, self.environment, account=self.account(revision=9))
        self.assertEqual(first, second)
        changed = credential_source(state, self.environment, account=self.account(credential_revision=1))
        self.assertNotEqual(first, changed)
        self.assertNotIn('revision', first)
        self.assertNotIn('email', first)

    def test_same_native_source_new_credentials_refuses_old_session_binding(self):
        initial = self.context()
        initial.runtime['account'] = self.account()
        first = self.execute(initial)
        self.assertEqual(first.status, 'ok', first.to_report())
        resumed = self.context(index=2, previous=first.result['sessionId'])
        resumed.runtime['account'] = self.account(credential_revision=1)
        second = self.execute(resumed)
        self.assertEqual(second.status, 'failed', second.to_report())
        self.assertEqual(second.result['code'], 'native-resume-unavailable')
        rebuilt = self.context(index=3, previous=first.result['sessionId'], mode='reconstructed-new-session')
        rebuilt.runtime['account'] = self.account(credential_revision=1)
        third = self.execute(rebuilt)
        self.assertEqual(third.status, 'ok', third.to_report())
        self.assertNotEqual(third.result['sessionId'], first.result['sessionId'])
        control = json.loads((rebuilt.directory / 'codex-control.json').read_text())
        self.assertEqual(control['credentialSource']['credentialRevision'], 1)

    def test_service_native_identity_overrides_worker_consumption_seam(self):
        context = self.context()
        context.runtime.update(account=self.account(), workerAccount={'source': 'worker', 'revision': 77})
        result = self.execute(context)
        self.assertEqual(result.status, 'ok', result.to_report())
        control = json.loads((context.directory / 'codex-control.json').read_text())
        self.assertEqual(control['credentialSource']['source'], 'native')

    def test_unverified_worker_refuses_coding_review_and_fast_router_before_spawn(self):
        context = self.context()
        context.runtime['account'] = self.account(source='worker')
        with self.assertRaises(BoardError) as raised:
            self.adapter.start(context)
        self.assertEqual(raised.exception.code, 'ACCOUNT_CAPABILITY_UNVERIFIED')
        self.assertFalse(Path(self.environment['BUDDY_CODEX_FIXTURE_STATE']).exists())
        context.turn = None
        review = ReadOnlyStructuredRequest(str(self.cwd), 'Fixture only', {'type': 'object'},
                                           {'timeoutSeconds': 5})
        fast = NoToolStructuredRequest(str(self.cwd), 'Fixture only', {'type': 'object'}, 5)
        with mock.patch.object(controller, 'owned_popen') as spawn:
            for call, request in ((read_only.start, review), (read_only.start_no_tool, fast)):
                with self.assertRaises(BoardError) as refused:
                    call('codex', context, request)
                self.assertEqual(refused.exception.code, 'ACCOUNT_CAPABILITY_UNVERIFIED')
            spawn.assert_not_called()

    def test_structured_environment_uses_frozen_source_and_purpose(self):
        from hey_my_buddy.blackboard.catalog.accounts import WorkerAccountProvider, using_provider
        from hey_my_buddy.private_dirs import account_root, ensure_private_dir
        worker_root = ensure_private_dir(account_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex'))
        observed = []

        def environment(state, account, original, purpose):
            observed.append((dict(account), purpose))
            return {**original, 'CODEX_HOME': str(worker_root)}

        context = self.context()
        context.runtime['account'] = self.account(source='worker', credential_revision=4)
        with using_provider('codex', WorkerAccountProvider(capabilities={'workerAccount': True}, environment=environment)):
            for purpose in ('review', 'router'):
                selected = read_only._account_environment('codex', context, purpose=purpose)
                self.assertEqual(selected['CODEX_HOME'], str(worker_root))
        self.assertEqual(observed, [(context.runtime['account'], 'review'), (context.runtime['account'], 'router')])
