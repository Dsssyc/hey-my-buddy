"""Recovery only confirms native shutdown with a complete creation journal."""
from pathlib import Path
from support import BoardTestCase
from unittest.mock import patch

from blackboard.store.test_interruption_recovery import InterruptionRecoveryTests, IDENTITY
from hey_my_buddy.protocol import process_identity as processes
from hey_my_buddy.protocol.execution import fsync_json


class NativeTreeTests(BoardTestCase):
    claimed = InterruptionRecoveryTests.claimed
    journal = staticmethod(InterruptionRecoveryTests.journal)
    def test_pending_spawn_stays_unconfirmed_but_recorded_native_processes_are_stopped(self):
        board, client, task, _, actor = self.claimed()
        journal = self.journal(board, actor['attemptId'])
        fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': ['a' * 32],
                                          'identities': [{**IDENTITY, 'pid': 456, 'processGroupId': 456}]})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'stop_orphan_process', return_value='orphan-stopped') as stop:
            board.store.recover_executions()
            stop.assert_called_once()
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])

    def test_orphan_stop_is_named_in_the_persisted_evidence(self):
        board, client, task, _, actor = self.claimed()
        journal = self.journal(board, actor['attemptId'])
        fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': [],
                                          'identities': [{**IDENTITY, 'pid': 456, 'processGroupId': 456}]})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'stop_orphan_process', return_value='orphan-stopped'):
            board.store.recover_executions()
        self.assertEqual(client.get(runId=task['taskId'])['interruptionEvidence']['kind'], 'orphan-stopped')
