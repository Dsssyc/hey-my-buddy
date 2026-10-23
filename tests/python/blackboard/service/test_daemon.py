"""The daemon sizes admission without starting idle execution processes."""
from __future__ import annotations

import os
import io
import json
from unittest.mock import Mock, patch

from c_two._native import CoreError

from support import BoardTestCase
from hey_my_buddy.blackboard.service.daemon import Daemon, _env_int, main
from hey_my_buddy.errors import BoardError


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

    def test_invalid_environment_values_are_refused_instead_of_defaulted_or_clamped(self):
        for name in ("HMB_MAX_CONCURRENT", "HMB_WAIT_CAPACITY"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "invalid"}):
                with self.assertRaises(BoardError) as caught:
                    _env_int(name, 8)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        for value in ("0", "33", "-1"):
            with self.subTest(value=value), patch.dict(os.environ, {"HMB_MAX_CONCURRENT": value}):
                with self.assertRaises(BoardError) as caught:
                    Daemon(self.directory / "state")
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_workspace_cleanup_failure_is_visible_and_other_work_continues(self):
        daemon = Daemon(self.directory / "state")
        self.addCleanup(daemon.store.db.close)
        daemon.store.initialize()
        daemon.queue_workspace_cleanup("one")
        daemon.queue_workspace_cleanup("two")
        with patch("hey_my_buddy.blackboard.tasks.storage.cleanup_accepted_workspace",
                   side_effect=[OSError("fixture cleanup refused"), None]) as cleanup:
            daemon._cleanup_accepted_workspaces()
        self.assertEqual(cleanup.call_count, 2)
        issue = daemon.store.service_issues()[0]
        self.assertEqual((issue["stage"], issue["code"], issue["message"]),
                         ("workspace-cleanup", "OSError", "fixture cleanup refused"))
        daemon._cleanup_accepted_workspaces()
        self.assertEqual(daemon.store.service_issues(), [issue])
        daemon.store.db.close()
        recovered = Daemon(daemon.directory)
        self.addCleanup(recovered.store.db.close)
        recovered.store.initialize()
        self.assertEqual(recovered.store.service_issues(), [issue])
        recovered.queue_workspace_cleanup("one")
        with patch("hey_my_buddy.blackboard.tasks.storage.cleanup_accepted_workspace", return_value={"removed": True}):
            recovered._cleanup_accepted_workspaces()
        self.assertEqual(recovered.store.service_issues(), [])
        with recovered.store.db.read() as db:
            self.assertEqual(db.execute("SELECT kind FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0],
                             "service.issue_resolved")

    def test_expected_workspace_retention_is_not_a_service_issue(self):
        daemon = Daemon(self.directory / "state")
        self.addCleanup(daemon.store.db.close)
        daemon.store.initialize()
        retained = BoardError("NOT_READY", "The accepted checkout must be retained", reasons=["unsealed-changes"])
        for previous_failure in (False, True):
            with self.subTest(previous_failure=previous_failure):
                if previous_failure:
                    daemon.store.record_service_issue("workspace-cleanup", OSError("earlier cleanup failure"))
                daemon.queue_workspace_cleanup("retained")
                with patch("hey_my_buddy.blackboard.tasks.storage.cleanup_accepted_workspace", side_effect=retained):
                    daemon._cleanup_accepted_workspaces()
                self.assertEqual(daemon.store.service_issues(), [])
        with daemon.store.db.read() as db:
            recorded = [json.loads(row[0])["code"] for row in db.execute(
                "SELECT payload_json FROM events WHERE kind='service.issue'")]
        self.assertNotIn("NOT_READY", recorded)

    def test_cancel_failure_is_visible_and_remaining_tasks_are_cancelled(self):
        daemon = Daemon(self.directory / "state")
        self.addCleanup(daemon.store.db.close)
        daemon.store.initialize()
        work = {"queuedTasks": [{"taskId": "one"}, {"taskId": "two"}],
                "attempts": [{"taskId": "three"}, {"taskId": "four"}]}
        for method, stage in ((daemon.cancel_queued, "cancel-queued"), (daemon.cancel_active, "cancel-active")):
            with self.subTest(stage=stage), patch.object(daemon.store, "active_work", return_value=work), \
                 patch.object(daemon.store, "task_cancel", side_effect=[BoardError("CONFLICT", "fixture cancellation refused"), {}]) as cancel:
                self.assertEqual(method(None), 1)
                self.assertEqual(cancel.call_count, 2)
            issue = next(item for item in daemon.store.service_issues() if item["stage"] == stage)
            self.assertEqual(issue["code"], "CONFLICT")
            with patch.object(daemon.store, "active_work", return_value=work), \
                 patch.object(daemon.store, "task_cancel", return_value={}):
                self.assertEqual(method(None), 2)
            self.assertNotIn(stage, [item["stage"] for item in daemon.store.service_issues()])
            daemon.store.record_service_issue(stage, BoardError("CONFLICT", "earlier cancellation refused"))
            with patch.object(daemon.store, "active_work", return_value={"queuedTasks": [], "attempts": []}):
                self.assertEqual(method(None), 0)
            self.assertNotIn(stage, [item["stage"] for item in daemon.store.service_issues()])

    def test_invalid_startup_setting_uses_the_existing_startup_error_record(self):
        with patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory), "HMB_MAX_CONCURRENT": "invalid"}), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(), 2)
        error = json.loads((self.directory / "startup-error.json").read_text(encoding="utf-8"))
        self.assertEqual(error["code"], "INVALID_ARGUMENT")
        self.assertIn("HMB_MAX_CONCURRENT", error["message"])
        self.assertIn("INVALID_ARGUMENT", stderr.getvalue())

    def test_native_startup_timeout_uses_the_existing_startup_error_record(self):
        error = CoreError("fixture lifecycle failure without timeout wording")
        error.category = "lifecycle"
        error.lifecycle_kind = "startup_timeout"
        with patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory)}), \
             patch.object(Daemon, "run", side_effect=error), \
             patch("hey_my_buddy.blackboard.service.daemon.signal.signal"), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(), 2)
        failure = json.loads((self.directory / "startup-error.json").read_text(encoding="utf-8"))
        self.assertEqual(failure, {"pid": os.getpid(), "code": "SERVICE_START_TIMEOUT", "message": str(error)})
        self.assertIn("SERVICE_START_TIMEOUT", stderr.getvalue())

    def test_other_native_errors_are_reraised_even_with_timeout_wording(self):
        for category, kind in (("lifecycle", "shutdown_timeout"), ("transport", "startup_timeout"), (None, None)):
            with self.subTest(category=category, kind=kind):
                error = CoreError("server did not become ready within 5.000s")
                error.category = category
                error.lifecycle_kind = kind
                with patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory)}), \
                     patch.object(Daemon, "run", side_effect=error), \
                     patch("hey_my_buddy.blackboard.service.daemon.signal.signal"):
                    with self.assertRaises(CoreError) as caught:
                        main()
                self.assertIs(caught.exception, error)
        self.assertFalse((self.directory / "startup-error.json").exists())
