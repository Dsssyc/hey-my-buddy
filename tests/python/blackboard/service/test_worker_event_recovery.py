"""An idle Worker sees uncertainty anywhere in a wake's event range."""
from unittest.mock import patch

from support import BoardTestCase
from blackboard.store import test_interruption_recovery as recovery_helpers
from hey_my_buddy.blackboard.tasks import interruption


class WorkerRecoveryEventTests(BoardTestCase):
    claimed = recovery_helpers.InterruptionRecoveryTests.claimed

    def test_unknown_event_after_first_page_entry_triggers_recovery(self):
        for startup in (False, True):
            with self.subTest(startup=startup):
                board, client, task, _, actor = self.claimed()
                cursor = board.store.head()
                client.register_worker('idle', adapter='command', capabilities=['command'])
                with patch.object(interruption.processes, 'inspect_process_identity', return_value='unknown'):
                    if startup:
                        board.store.reconcile_startup()
                    else:
                        with board.store.db.write() as connection:
                            connection.execute("UPDATE attempts SET lease_expires_at='2000-01-01T00:00:00Z'")
                        board.store.mark_expired_leases()
                page = board.call('events_wait', {'workerId': 'idle', 'after': cursor,
                                                  'limit': 1, 'timeoutMs': 0})
                self.assertEqual(page['events'][0]['kind'], 'worker.registered')
                self.assertGreater(page['head'], page['cursor'])
                self.assertTrue(page['recoveryNeeded'])
                next_page = board.call('events_wait', {'workerId': 'idle', 'after': page['head'],
                                                       'limit': 1, 'timeoutMs': 0})
                self.assertEqual(next_page['events'], [])
                self.assertFalse(next_page['recoveryNeeded'])

    def test_ordinary_events_do_not_request_orphan_recovery(self):
        board, client, task, _, actor = self.claimed()
        cursor = board.store.head()
        client.register_worker('idle', adapter='command', capabilities=['command'])
        page = board.call('events_wait', {'workerId': 'idle', 'after': cursor, 'limit': 1, 'timeoutMs': 0})
        self.assertFalse(page['recoveryNeeded'])
        public = board.call('events_wait', {'after': cursor, 'limit': 1, 'timeoutMs': 0})
        self.assertNotIn('recoveryNeeded', public)
