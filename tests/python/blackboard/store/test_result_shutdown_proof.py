"""A late stop proof completes the original structured workflow outcome."""
from unittest.mock import patch

from blackboard.tasks import test_workflow as workflow_fixture
from blackboard.store import test_interruption_recovery as recovery_fixture
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.protocol import process_identity as processes


class DeferredWorkflowResultTests(workflow_fixture.WorkflowTestCase):
    def test_successful_turn_is_imported_after_stop_proof_without_replacing_outcome(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        actor = {'attemptId': claim['claim']['attempt']['attemptId'], 'generation': 1,
                 'nonce': workflow_fixture.NONCE}
        board.call('execution_progress', {**actor, 'processIdentity': recovery_fixture.IDENTITY})
        with board.store.db.read() as db:
            attempt = dict(board.store._attempt_row(db, actor['attemptId']))
        journal_path = processes.native_process_journal_path(interruption._directory(board.store, attempt),
                                                              recovery_fixture.IDENTITY)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        processes.NativeProcessJournal(journal_path, {'taskId': submitted['runId'],
            'attemptId': actor['attemptId'], 'generation': 1})
        result = self.finish_turn(board, claim, shutdown=False)
        original = result['attempt']['result']
        self.assertEqual(result['task']['state'], 'reconciliation-needed')
        self.assertTrue(board.store.resource_claims())
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
        final = board.call('task_result', {'runId': submitted['runId']})
        self.assertEqual(final['state'], 'completed')
        self.assertEqual(final['selectedAttempt']['result'], {**original, 'shutdownConfirmed': True})
        self.assertEqual(final['workflow']['state'], 'delivered')
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT state FROM workflow_turns WHERE attempt_id=?',
                                       (actor['attemptId'],)).fetchone()[0], 'returned')
        self.assertEqual(board.store.resource_claims(), [])
        head = board.store.head()
        self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(board.store.head(), head)
