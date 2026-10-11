"""A failed pre-model start gets one retry; a child that may be working gets none."""
from unittest.mock import Mock, patch

from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.runtime.worker import Worker
from support import BoardTestCase


class HarnessStartupTests(BoardTestCase):
    def test_controllers_use_a_whitelist_and_router_has_no_attempt_authority(self):
        from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment
        class NoEnvironmentDump(dict):
            def items(self):
                raise AssertionError('Do not collect arbitrary environment values')
        source = NoEnvironmentDump(HOME='/private/home', PATH='/usr/bin', BUDDY_STATE_DIR='/private/state',
                                   BUDDY_AGENT_CREDENTIAL_FILE='/private/credential', OPENAI_API_KEY='fixture-secret',
                                   UNKNOWN_SECRET='fixture-secret', OPENAI_BASE_URL='https://fixture.invalid')
        controller = controller_environment(self.directory, source)
        router = controller_environment(self.directory, source, read_only=True)
        self.assertEqual(controller['BUDDY_AGENT_CREDENTIAL_FILE'], '/private/credential')
        self.assertNotIn('BUDDY_AGENT_CREDENTIAL_FILE', router)
        for key in ('OPENAI_API_KEY', 'UNKNOWN_SECRET', 'OPENAI_BASE_URL'):
            self.assertNotIn(key, controller)
            self.assertNotIn(key, router)

    def setUp(self):
        super().setUp()
        self.client = Mock(state_dir=self.directory / 'state')
        self.record = {'adapter': 'codex', 'status': 'ready', 'available': True, 'revision': 1, 'command': ['/native/codex']}
        self.client.call.return_value = {'harness': self.record}
        self.worker = Worker('fixture', self.directory / 'state', client=self.client)
        self.assertEqual(self.client.state_dir, self.worker.state_dir,
                         'Injected client must carry the private Worker state path')
        self.worker.spool.write_startup({'nonce': 'fixture-owned-nonce'})
        self.attempt = {'attemptId': 'attempt-1', 'generation': 1}
        self.claim = {'attempt': self.attempt}
        self.directory.joinpath('attempt').mkdir()

    def execute(self, holder):
        return self.worker._execute_guarded(self.claim, {}, {'adapter': 'codex'}, self.attempt, self.directory / 'attempt', holder)

    def test_pre_model_failure_rechecks_once_and_retains_both_choices(self):
        holder = {}
        with patch.object(self.worker, '_execute_selected', side_effect=[BoardError('HARNESS_PREMODEL_FAILED', 'before input'), {'done': True}]):
            self.assertEqual(self.execute(holder), {'done': True})
        self.assertEqual([call.args[1]['retry'] for call in self.client.call.call_args_list], [False, True])
        self.assertEqual(len(holder['harnessHistory']), 2)

    def test_second_pre_model_failure_marks_unavailable_without_a_third_start(self):
        with patch.object(self.worker, '_execute_selected', side_effect=BoardError('HARNESS_PREMODEL_FAILED', 'before input')) as start:
            with self.assertRaises(BoardError):
                self.execute({})
        self.assertEqual(start.call_count, 2)
        self.assertTrue(self.client.call.call_args_list[-1].args[1]['failedOnly'])

    def test_unknown_working_child_is_never_restarted(self):
        holder = {}
        def working(*args):
            holder['started'] = True
            raise BoardError('HARNESS_PREMODEL_FAILED', 'cannot prove pre-model shutdown')
        with patch.object(self.worker, '_execute_selected', side_effect=working) as start:
            with self.assertRaises(BoardError):
                self.execute(holder)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(self.client.call.call_count, 1)
