"""The daemon sizes admission without starting idle execution processes."""
from __future__ import annotations

import os
from unittest.mock import Mock, patch

from support import BoardTestCase
from hey_my_buddy.blackboard.service.daemon import Daemon


class DaemonAdmissionTests(BoardTestCase):
    def test_scheduling_issues_share_the_service_issue_event_and_health_view(self):
        board = self.board()
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
        scheduler = ExecutionScheduler(Mock(store=board.store), live_registry=board.live_registry)
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
