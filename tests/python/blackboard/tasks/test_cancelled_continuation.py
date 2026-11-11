"""A stopped queued continuation may be requested again through its bound fact."""
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError


class CancelledContinuationTests(WorkflowTestCase):
    def cancelled_unconsumed(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='attention')
        view = board.call('workflow_get', {'runId': run['runId']})
        params = {'runId': run['runId'], 'requestId': view['activeRequest']['requestId'],
                  'input': 'Continue the original task'}
        first = board.call('workflow_continue', params)
        board.call('workflow_cancel', {'runId': run['runId']})
        return board, run, params, first

    def test_identical_continue_requeues_a_cancelled_unconsumed_response(self):
        board, run, params, first = self.cancelled_unconsumed()
        replay = board.call('workflow_continue', params)
        self.assertEqual(replay['continuationId'], first['continuationId'])
        self.assertTrue(replay['alreadyResponded'])
        again = board.call('workflow_continue', params)
        self.assertEqual(again['continuationId'], first['continuationId'])
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'executing')
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?',
                                                (run['runId'],)).fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT continuation_count FROM workflow_runs WHERE run_id=?',
                                                (run['runId'],)).fetchone()[0], 2)

    def test_changed_input_cannot_requeue_the_cancelled_response(self):
        board, run, params, first = self.cancelled_unconsumed()
        with self.assertRaises(BoardError) as raised:
            board.call('workflow_continue', {**params, 'input': 'Do different work'})
        self.assertEqual(raised.exception.code, 'CONFLICT')
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'cancelled')
        with board.store.db.read() as connection:
            row = connection.execute('SELECT state FROM workflow_continuations WHERE continuation_id=?',
                                     (first['continuationId'],)).fetchone()
        self.assertEqual(row['state'], 'cancelled')

    def test_concluded_task_cannot_reopen_its_cancelled_response(self):
        board, run, params, first = self.cancelled_unconsumed()
        board.call('workflow_conclude', {'runId': run['runId'], 'note': 'Stopped by Host'})
        with self.assertRaises(BoardError) as raised:
            board.call('workflow_continue', params)
        self.assertEqual(raised.exception.code, 'CONFLICT')
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'concluded')
