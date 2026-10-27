"""Recovery only confirms native shutdown with a complete creation journal."""
from pathlib import Path
from support import BoardTestCase
from unittest.mock import patch

from blackboard.store import test_interruption_recovery as recovery_fixture
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.paths import native_path
from hey_my_buddy.protocol import process_identity as processes
from hey_my_buddy.protocol.execution import fsync_json

WINDOWS_IDENTITY = {'pid': 301, 'startedAt': 1339247086400000000,
                    'jobName': 'Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000e1'}


class NativeTreeTests(BoardTestCase):
    claimed = recovery_fixture.InterruptionRecoveryTests.claimed
    journal = staticmethod(recovery_fixture.InterruptionRecoveryTests.journal)
    def test_pending_spawn_stays_unconfirmed_but_recorded_native_processes_are_stopped(self):
        board, client, task, _, actor = self.claimed()
        journal = self.journal(board, actor['attemptId'])
        fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': ['a' * 32],
                                          'identities': [{**recovery_fixture.IDENTITY, 'pid': 456, 'processGroupId': 456}]})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'stop_orphan_process', return_value='orphan-stopped') as stop:
            board.store.recover_executions()
            stop.assert_called_once()
        self.assertFalse(client.get(runId=task['runId'])['shutdownConfirmed'])

    def test_orphan_stop_is_named_in_the_persisted_evidence(self):
        board, client, task, _, actor = self.claimed()
        journal = self.journal(board, actor['attemptId'])
        fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': [],
                                          'identities': [{**recovery_fixture.IDENTITY, 'pid': 456, 'processGroupId': 456}]})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'stop_orphan_process', return_value='orphan-stopped'):
            board.store.recover_executions()
        self.assertEqual(client.get(runId=task['runId'])['interruptionEvidence']['kind'], 'orphan-stopped')

    def windows_attempt(self, *, journal_state):
        board, client, task, _, actor = self.claimed(identity=False)
        board.call('execution_progress', {**actor, 'processIdentity': WINDOWS_IDENTITY})
        with board.store.db.read() as connection:
            attempt = dict(board.store._attempt_row(connection, actor['attemptId']))
        journal_path = processes.native_process_journal_path(interruption._directory(board.store, attempt),
                                                             WINDOWS_IDENTITY)
        if journal_state != 'missing':
            native_path(Path(journal_path).parent).mkdir(parents=True, exist_ok=True)
            fsync_json(Path(journal_path), {'binding': {'taskId': task['runId'], 'attemptId': actor['attemptId'],
                                                        'generation': 1},
                                            'pending': [] if journal_state == 'empty' else ['a' * 32],
                                            'identities': []})
        return board, client, task, actor, Path(journal_path)

    def test_windows_root_stop_evidence_covers_pending_or_missing_journal(self):
        for journal_state in ('missing', 'pending', 'empty'):
            with self.subTest(journal_state=journal_state):
                board, client, task, actor, journal_path = self.windows_attempt(journal_state=journal_state)
                # 'missing' is a truly absent file, not an empty or pending one.
                self.assertEqual(native_path(journal_path).exists(), journal_state != 'missing')
                with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                        patch.object(processes, 'stop_orphan_process') as stop:
                    board.store.recover_executions()
                    stop.assert_not_called()
                view = client.get(runId=task['runId'])
                self.assertTrue(view['shutdownConfirmed'])
                self.assertEqual(view['interruptionEvidence']['kind'], 'identity-absent')
