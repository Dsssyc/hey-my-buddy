"""A service sweep finishing an orphan must also unblock its replacement's retirement."""
from unittest.mock import patch

from support import BoardTestCase
from blackboard.store import test_interruption_recovery as helpers
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity


class OrphanSettlementTests(BoardTestCase):
    claimed = helpers.InterruptionRecoveryTests.claimed

    def test_interrupted_receipt_requires_confirmed_stop_and_recognized_evidence(self):
        board, client, task, claim, actor = self.claimed()
        valid = {**actor, 'status': 'failed', 'terminationReason': 'interrupted', 'shutdownConfirmed': True,
                 'result': {'interruptionEvidence': {'kind': 'identity-absent'}}}
        for changes in ({'shutdownConfirmed': False}, {'status': 'ok'}, {'result': {}},
                        {'result': {'interruptionEvidence': {'kind': 'lease-expired'}}}):
            with self.subTest(changes=changes), self.assertRaises(BoardError) as caught:
                board.call('worker_result', {**valid, **changes})
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])

    def test_foreign_local_orphan_observation_is_refused_before_recovery_mutates(self):
        board, client, task, claim, actor = self.claimed()
        client.register_worker('foreign', adapter='command', capabilities=['command'])
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='uncertain',ownership='uncertain'")
        with self.assertRaises(BoardError) as caught:
            board.call('worker_recover', {'workerId': 'foreign', 'observeAttemptId': actor['attemptId']})
        self.assertEqual(caught.exception.code, 'UNAUTHORIZED')
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])

    def test_service_finishes_before_candidate_publication_and_worker_clears_local_orphan(self):
        board, client, task, claim, actor = self.claimed()
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='uncertain',ownership='uncertain'")
            board.store._retain_claims(connection, actor['attemptId'])
        replacement = Worker('original', board.directory, client=client)
        replacement.spool.ensure()
        orphan = replacement.spool.directory / 'orphaned.json'
        worker_module.fsync_json(orphan, {'workerId': 'original', 'attemptId': actor['attemptId'],
                                          'taskId': task['taskId'], 'identityPublished': True})
        with patch.object(process_identity, 'worker_identity_alive', return_value=False), \
             patch.object(process_identity, 'inspect_process_identity', return_value='identity-absent'):
            replacement.recover_orphans()
            self.assertTrue(orphan.exists())
            self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])
            self.assertEqual(board.store.recover_interrupted_attempts(), 1)
            replacement.recover_orphans()
        self.assertFalse(orphan.exists())
        self.assertEqual(replacement.recovery_pending(), [])
        self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
        self.assertEqual(client.get(runId=task['taskId'])['interruptionEvidence']['kind'], 'identity-absent')
