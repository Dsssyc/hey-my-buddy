"""An unavailable long call leaves a live execution unresolved."""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import Mock, call, patch

from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
from hey_my_buddy.errors import BoardError


class ExecutionObservationTests(unittest.TestCase):
    def test_failed_creation_releases_attempt_and_next_admission_runs(self):
        with tempfile.TemporaryDirectory() as root:
            first = {"attempt": {"attemptId": "attempt-1"}, "task": {"taskId": "task-1"}}
            second = {"attempt": {"attemptId": "attempt-2"}, "task": {"taskId": "task-2"}}
            store = Mock(directory=Path(root))
            service = Mock(store=store, control={})
            service.admit_execution.side_effect = [
                {"claim": first}, {"claim": second}, {"claim": None}]
            scheduler = ExecutionScheduler(service)
            creation_error = OSError("Popen did not create a child")
            creation_error.execution_handle = None
            creation_error.spawn_created = False
            handle = Mock(process_identity={"pid": 2})
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution",
                       side_effect=[creation_error, handle]), patch.object(scheduler, "_observe") as observe:
                scheduler._admit_available()
                store.release_execution.assert_called_once_with(
                    "attempt-1", spawn_intent_written=False,
                    reason="execution launch failed before process creation")
                scheduler._admit_available()
                for thread in tuple(scheduler._threads):
                    thread.join(1)
            observe.assert_called_once_with(second, handle, {"pid": 2})
            self.assertEqual(service.admit_execution.call_count, 3)

    def test_failed_parent_capture_is_closed_by_a_verified_child_report(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store))
        handle = Mock(pid=123, process_started_at=456, pgid=123, job=None)
        identity = {"bootId": "test-boot", "pid": 123, "startedAt": 456,
                    "processGroupId": 123, "jobName": None}
        scheduler._handles["attempt-1"] = (handle, None)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.boot_id", return_value="test-boot"):
            self.assertFalse(scheduler.verify_identity("attempt-1", {**identity, "startedAt": 111}))
            self.assertTrue(scheduler.verify_identity("attempt-1", identity))
        claim = {"attempt": {"attemptId": "attempt-1"}, "task": {"taskId": "task-1"}}
        with patch.object(scheduler, "_observe") as observe:
            scheduler._settle_unidentified(claim, handle)
        observe.assert_called_once_with(claim, handle, identity)
        store.release_execution.assert_not_called()
        handle.terminate.assert_not_called()

    def test_missing_identity_retains_handle_and_claim_even_after_child_exit(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store))
        handle = Mock()
        handle.process.poll.return_value = 1
        scheduler._handles["attempt-1"] = (handle, None)
        def stop_waiting(_params):
            scheduler._stopping.set()
            return {"task": {"cancelRequested": False}}
        store.task_get.side_effect = stop_waiting
        scheduler._settle_unidentified({"attempt": {"attemptId": "attempt-1"},
                                       "task": {"taskId": "task-1"}}, handle)
        store.release_execution.assert_not_called()
        handle.terminate.assert_not_called()
        self.assertIs(scheduler._handles["attempt-1"][0], handle)

    def test_known_identity_from_failed_marker_is_used_only_after_absence(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            scheduler = ExecutionScheduler(Mock(store=store))
            scheduler.TICK_SECONDS = 0
            handle = Mock()
            handle.process.poll.return_value = 1
            handle.execution_directory = Path(root)
            handle.budget_path = Path(root) / "execution-budget.json"
            claim = {"attempt": {"attemptId": "attempt-1"}, "task": {"taskId": "task-1"}}
            identity = {"pid": 1}
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       return_value=None), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       side_effect=["alive", "identity-absent"]), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.cleanup_execution_endpoint"):
                scheduler._observe(claim, handle, identity)
            self.assertEqual(store.recover_executions.call_args_list,
                             [call("attempt-1"), call("attempt-1", process_identity=identity)])
            store.release_execution.assert_not_called()

    def test_failed_long_call_waits_for_process_identity_before_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock()
            store.directory = Path(root)
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            service = Mock(store=store)
            scheduler = ExecutionScheduler(service)
            client = Mock()
            client.wait.side_effect = BoardError("LIVE_UNAVAILABLE", "connection lost")
            handle = Mock()
            handle.process.poll.return_value = None
            handle.execution_directory = Path(root)
            handle.role_run_identity = Mock()
            handle.budget_path = Path(root) / "execution-budget.json"
            claim = {"attempt": {"attemptId": "attempt-1"}, "task": {"taskId": "task-1"}}
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       return_value=client), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       side_effect=["alive", "identity-absent"]) as inspect, patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.cleanup_execution_endpoint"):
                scheduler._observe(claim, handle, {"pid": 1})
            self.assertEqual(inspect.call_count, 2)
            store.recover_executions.assert_called_once_with("attempt-1", process_identity={"pid": 1})
            handle.signal_group.assert_not_called()
            self.assertEqual(store.live_registry.unbind.call_count, 2)

    def test_identity_mismatch_is_refused_only_for_a_service_owned_handle(self):
        service = Mock()
        scheduler = ExecutionScheduler(service)
        handle = Mock()
        with scheduler._lock:
            scheduler._handles["owned"] = (handle, {"pid": 1})
        self.assertFalse(scheduler.verify_identity("owned", {"pid": 2}))
        self.assertTrue(scheduler.verify_identity("owned", {"pid": 1}))
        self.assertTrue(scheduler.verify_identity("restarted", {"pid": 2}))

    def test_router_budget_is_durable_before_forced_stop(self):
        with tempfile.TemporaryDirectory() as root:
            service = Mock()
            scheduler = ExecutionScheduler(service)
            handle = Mock()
            handle.execution_binding = {"taskId": "task", "attemptId": "attempt", "generation": 1}
            handle.budget_path = Path(root) / "execution-budget.json"
            handle.budget_path.write_text(json.dumps({"binding": handle.execution_binding,
                                                       "deadline": time.monotonic() - 1}))
            client = Mock()
            claim = {"attempt": {"attemptId": "attempt"}}
            sent, forced_at = scheduler._apply_stop({"cancelRequested": False}, claim, handle, client,
                                                      None, None)
            service.store.record_execution_deadline.assert_called_once_with("attempt", "deadline")
            client.stop.assert_called_once_with(initiator="deadline", grace_deadline=None, force=True)
            self.assertEqual(sent, ("deadline", None, True))
            self.assertIsNotNone(forced_at)

    def test_confirmed_receipt_prevents_force_kill(self):
        handle = Mock()
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_receipt",
                   return_value={"report": {"shutdownConfirmed": True}}):
            ExecutionScheduler._force_if_required(handle, {"pid": 1})
        handle.signal_group.assert_not_called()
