"""The daemon sizes admission without starting idle execution processes."""
from __future__ import annotations

import json
import os
from unittest.mock import Mock, patch

from support import BoardTestCase
from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.install import upgrade


class DaemonAdmissionTests(BoardTestCase):
    def test_legacy_cleanup_issue_does_not_fence_service_and_resolves_after_retry(self):
        board = self.board()
        daemon = Daemon(self.directory)
        daemon.store = board.store
        workers = self.directory / 'workers'
        workers.mkdir()
        (workers / 'old.json').write_text('{}')
        upgrade.write_journal(self.directory / 'upgrade-last.json', {'phase': 'verified',
            'legacyWorkerCleanup': True, 'verification': {'schemaVersion': 23}})
        with patch.object(upgrade, 'remove_legacy_worker_state', side_effect=OSError('denied')):
            daemon._cleanup_legacy_workers()
            head = board.store.head()
            daemon._cleanup_legacy_workers()
            self.assertEqual(board.store.head(), head, 'An unchanged cleanup failure must be deduplicated')
            with board.store.db.read() as connection:
                issue = dict(connection.execute('SELECT * FROM service_issues').fetchone())
            self.assertEqual(issue['stage'], 'legacy-worker-cleanup')
            self.assertEqual(issue['code'], 'LEGACY_WORKER_CLEANUP_FAILED')
            self.assertIn('denied', issue['message'])
            health = board.call('health', {})
            self.assertEqual(health['status'], 'ok')
            self.assertEqual(health['serviceIssues'], [{
                'stage': issue['stage'], 'code': issue['code'], 'message': issue['message'],
                'observedAt': issue['observed_at']}])
            self.assertEqual(board.call('task_list', {})['tasks'], [])
            daemon.queue_workspace_cleanup('accepted-run')
            with patch('hey_my_buddy.blackboard.tasks.storage.cleanup_accepted_workspace') as cleanup:
                daemon._cleanup_accepted_workspaces()
            cleanup.assert_called_once_with(board.store, 'accepted-run')
        # A replacement service uses the same durable pending cleanup, independent
        # of its in-memory queues or the completed upgrade journal.
        replacement = Daemon(self.directory)
        replacement.store = board.store
        replacement._cleanup_legacy_workers()
        self.assertFalse(workers.exists())
        self.assertFalse(json.loads((self.directory / 'upgrade-last.json').read_text())['legacyWorkerCleanup'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM service_issues').fetchone()[0], 0)
            events = [(row['kind'], json.loads(row['payload_json'])) for row in connection.execute(
                "SELECT kind,payload_json FROM events WHERE kind IN ('service.issue','service.issue_resolved')")]
        self.assertEqual([kind for kind, _ in events], ['service.issue', 'service.issue_resolved'])
        self.assertEqual(board.call('health', {})['serviceIssues'], [])

    def test_scheduling_issues_share_the_service_issue_event_and_health_view(self):
        board = self.board()
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
        scheduler = ExecutionScheduler(Mock(store=board.store))
        scheduler._scheduler_issue('admission', OSError('temporarily unavailable'))
        head = board.store.head()
        scheduler._scheduler_issue('admission', OSError('temporarily unavailable'))
        self.assertEqual(board.store.head(), head)
        self.assertEqual(board.call('health', {})['serviceIssues'][0]['stage'], 'admission')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT kind FROM events ORDER BY seq DESC LIMIT 1').fetchone()[0],
                             'service.issue')

    def test_machine_ceiling_is_store_admission_only(self):
        with patch.dict(os.environ, {"HMB_MAX_CONCURRENT": "3"}):
            daemon = Daemon(self.directory / "state")
        self.assertEqual(daemon.store.max_concurrent, 3)
        self.assertFalse(hasattr(daemon, "pool"))
        self.assertIsNone(daemon.scheduler)

    def test_construction_does_not_start_an_execution(self):
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution") as launch:
            daemon = Daemon(self.directory / "state")
        launch.assert_not_called()
        self.assertFalse(hasattr(daemon, "_sweeper"))

    def test_zero_drain_delivers_connected_stops_before_observers_exit(self):
        daemon = Daemon(self.directory / "state")
        daemon.directory.mkdir(parents=True)
        order = []
        daemon.scheduler = Mock()
        daemon.scheduler.send_pending_stops.side_effect = lambda: order.append("sent")
        daemon.store.workflow.cancel_for_service_stop = Mock(return_value=(
            {"queued": 0, "active": 0}, set()))
        with patch.object(daemon, "cancel_queued", return_value=0), patch.object(
            daemon, "cancel_active", return_value=0), patch.object(
            daemon, "drain", side_effect=lambda _seconds: order.append("drain") or []):
            reply = daemon.on_stop({"drainSeconds": 0})
        self.assertEqual(order, ["sent", "drain"])
        self.assertTrue(reply["stopped"])
        self.assertTrue(daemon.stopping.is_set())
