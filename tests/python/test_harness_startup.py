"""A failed pre-model start gets one retry; a child that may be working gets none."""
from unittest.mock import Mock, patch

from buddy.errors import BoardError
from buddy.worker.worker import Worker
from support import BoardTestCase


class HarnessStartupTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.client = Mock()
        self.record = {'adapter': 'codex', 'status': 'ready', 'available': True, 'revision': 1, 'command': ['/native/codex']}
        self.client.call.return_value = {'harness': self.record}
        self.worker = Worker(self.directory / 'state', 'fixture', client=self.client)
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
