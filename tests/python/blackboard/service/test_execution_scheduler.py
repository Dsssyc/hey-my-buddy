"""An unavailable long call leaves a live execution unresolved."""
from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
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
                    "attempt-1",
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

    def test_missing_identity_uses_reaped_creator_and_empty_group(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store))
        handle = Mock()
        handle.process.poll.return_value = 1
        handle.group_alive.return_value = False
        scheduler._handles["attempt-1"] = (handle, None)
        store.task_get.return_value = {"task": {"selectedAttempt": {
            "attemptId": "attempt-1", "executionState": "finished"}}}
        scheduler._settle_unidentified({"attempt": {"attemptId": "attempt-1"},
                                       "task": {"taskId": "task-1"}}, handle)
        store.release_execution.assert_not_called()
        handle.terminate.assert_not_called()
        self.assertNotIn("attempt-1", scheduler._handles)
        store.recover_executions.assert_called_once_with(
            "attempt-1", stopped_evidence={"kind": "creator-reaped", "exitCode": 1})

    def test_known_identity_from_failed_marker_is_used_only_after_absence(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": False, "selectedAttempt": {
                "attemptId": "attempt-1", "executionState": "finished"}}}
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
                       side_effect=["alive", "identity-absent"]):
                scheduler._observe(claim, handle, identity)
            self.assertEqual(store.recover_executions.call_args_list,
                             [call("attempt-1", process_identity=identity,
                                   stopped_evidence={"kind": "identity-absent"})])
            store.release_execution.assert_not_called()

    def test_failed_long_call_waits_for_process_identity_before_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock()
            store.directory = Path(root)
            store.task_get.return_value = {"task": {"cancelRequested": False, "selectedAttempt": {
                "attemptId": "attempt-1", "executionState": "finished"}}}
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
                       side_effect=["alive", "identity-absent"]) as inspect:
                scheduler._observe(claim, handle, {"pid": 1})
            self.assertEqual(inspect.call_count, 2)
            store.recover_executions.assert_called_once_with(
                "attempt-1", process_identity={"pid": 1},
                stopped_evidence={"kind": "identity-absent"})
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
            service.store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "deadline"}
            sent, forced_at = scheduler._apply_stop({"cancelRequested": False}, claim, handle, client,
                                                      None, None)
            service.store.record_execution_deadline.assert_called_once_with("attempt", "deadline")
            client.stop.assert_called_once_with(initiator="deadline", grace_deadline=None, force=True)
            self.assertEqual(sent, ("deadline", None, True))
            self.assertIsNotNone(forced_at)

    def test_grace_is_sent_before_force_and_persisted_only_at_expiry(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store))
        handle = Mock()
        handle.budget_path.exists.return_value = False
        client = Mock()
        claim = {"attempt": {"attemptId": "attempt"}}
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        sent, forced_at = scheduler._apply_stop({"cancelRequested": True, "stopInitiator": "host",
                                                   "graceDeadline": future}, claim, handle, client, None, None)
        self.assertEqual(sent, ("host", future, False))
        self.assertIsNone(forced_at)
        self.assertGreater(client.stop.call_args.kwargs["grace_deadline"], time.monotonic())
        store.record_execution_deadline.assert_not_called()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        store.record_execution_deadline.return_value = {
            "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "grace-expired"}
        sent, forced_at = scheduler._apply_stop({"cancelRequested": True, "stopInitiator": "host",
                                                   "graceDeadline": past}, claim, handle, client, sent, forced_at)
        self.assertEqual(sent, ("host", past, True))
        self.assertIsNotNone(forced_at)
        store.record_execution_deadline.assert_called_once_with("attempt", "grace-expired")
        self.assertEqual(client.stop.call_args.kwargs,
                         {"initiator": "host", "grace_deadline": None, "force": True})

    def test_confirmed_receipt_prevents_force_kill(self):
        handle = Mock()
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_receipt",
                   return_value={"report": {"shutdownConfirmed": True}}):
            ExecutionScheduler._force_if_required(handle, {"pid": 1})
        handle.signal_group.assert_not_called()

    def test_conflict_is_retried_and_other_admission_errors_are_recorded(self):
        store = Mock()
        service = Mock(store=store, control={})
        scheduler = ExecutionScheduler(service)
        scheduler.TICK_SECONDS = .01
        outcomes = iter([
            BoardError("CONFLICT", "temporarily busy"),
            BoardError("INVALID_ARGUMENT", "bad queue entry"),
        ])
        def admit(_nonce):
            outcome = next(outcomes, {"claim": None})
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        service.admit_execution.side_effect = admit
        scheduler.start()
        try:
            deadline = time.monotonic() + 2
            while service.admit_execution.call_count < 3 and time.monotonic() < deadline:
                time.sleep(.01)
        finally:
            scheduler.stop()
        self.assertGreaterEqual(service.admit_execution.call_count, 3)
        store.record_scheduler_issue.assert_called_once()
        stage, error = store.record_scheduler_issue.call_args.args
        self.assertEqual(stage, "admission")
        self.assertEqual(error.code, "INVALID_ARGUMENT")

    def test_reconnected_channel_receives_the_same_stop_again(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service"}}
            service = Mock(store=store)
            scheduler = ExecutionScheduler(service)
            handle = Mock()
            handle.process.poll.return_value = None
            handle.execution_directory = Path(root)
            handle.budget_path = Path(root) / "budget.json"
            clients = [Mock(), Mock()]
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            clients[0].wait.side_effect = BoardError("LIVE_UNAVAILABLE", "gone")
            clients[1].wait.side_effect = lambda: scheduler._stopping.set() or True
            claim = {"attempt": {"attemptId": "attempt"}, "task": {"taskId": "task"}}
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       side_effect=clients), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       return_value="alive"):
                scheduler._observe(claim, handle, {"pid": 1})
            for client in clients:
                client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)

    def test_malformed_endpoint_is_visible_and_does_not_block_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"selectedAttempt": {
                "attemptId": "attempt", "executionState": "finished"}}}
            scheduler = ExecutionScheduler(Mock(store=store))
            handle = Mock()
            handle.execution_directory = Path(root)
            handle.budget_path = Path(root) / "budget.json"
            error = BoardError("LIVE_UNAVAILABLE", "bad private endpoint")
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       side_effect=error), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       return_value="identity-absent"):
                scheduler._observe({"attempt": {"attemptId": "attempt"}, "task": {"taskId": "task"}},
                                   handle, {"pid": 1})
            store.record_execution_issue.assert_called_once_with("attempt", "endpoint", error)
            store.recover_executions.assert_called_once_with(
                "attempt", process_identity={"pid": 1}, stopped_evidence={"kind": "identity-absent"})
            self.assertNotIn("attempt", scheduler._handles)

    def test_force_age_comes_from_persisted_request(self):
        scheduler = ExecutionScheduler(Mock())
        old = (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat()
        forced_at = scheduler._force_requested_at({"selectedAttempt": {"forceRequestedAt": old}})
        self.assertGreaterEqual(time.monotonic() - forced_at, 8)

    def test_zero_drain_sends_stop_to_each_connected_execution(self):
        store = Mock()
        store.task_get.side_effect = lambda params: {"task": {
            "cancelRequested": True, "stopInitiator": "service", "graceDeadline": None}}
        store.record_execution_deadline.return_value = {
            "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
        scheduler = ExecutionScheduler(Mock(store=store))
        clients = {}
        for index in range(2):
            attempt = f"attempt-{index}"
            handle = Mock(execution_binding={"taskId": f"task-{index}"})
            handle.budget_path.exists.return_value = False
            client = Mock()
            scheduler._handles[attempt] = (handle, {"pid": index + 1})
            scheduler._clients[attempt] = client
            clients[attempt] = client
        scheduler.send_pending_stops()
        for client in clients.values():
            client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
        self.assertEqual(store.record_execution_deadline.call_count, 2)
        store.record_execution_issue.reset_mock()
        failure = BoardError("LIVE_UNAVAILABLE", "stop channel disconnected")
        clients["attempt-0"].stop.side_effect = failure
        scheduler.send_pending_stops()
        store.record_execution_issue.assert_called_once_with("attempt-0", "service-stop", failure)

    def test_invalid_budget_is_visible_and_does_not_prevent_stop_delivery(self):
        for material in ('{not json', '{"binding":{},"deadline":1}', '{"binding":{},"deadline":"bad"}'):
            with self.subTest(material=material), tempfile.TemporaryDirectory() as root:
                store = Mock()
                store.record_execution_deadline.return_value = {
                    "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "forced-stop"}
                scheduler = ExecutionScheduler(Mock(store=store))
                handle = Mock(execution_binding={"taskId": "task", "attemptId": "attempt", "generation": 1})
                handle.budget_path = Path(root) / "budget.json"
                handle.budget_path.write_text(material)
                client = Mock()
                sent, forced_at = scheduler._apply_stop({}, {"attempt": {"attemptId": "attempt"}},
                                                       handle, client, None, None)
                store.record_execution_issue.assert_called_once()
                self.assertEqual(store.record_execution_issue.call_args.args[:2], ("attempt", "budget"))
                client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
                self.assertIsNotNone(forced_at)
                self.assertEqual(sent, ("service", None, True))
                store.record_execution_issue.reset_mock()
                scheduler._apply_stop({"selectedAttempt": store.record_execution_deadline.return_value,
                                       "stopInitiator": "service"}, {"attempt": {"attemptId": "attempt"}},
                                      handle, client, sent, forced_at)
                store.record_execution_issue.assert_not_called()

    def test_creator_confirmed_cleanup_before_handle_return_uses_its_stop_evidence(self):
        from hey_my_buddy.protocol.owned_process import ProcessCreationEvidence
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            service = Mock(store=store, control={})
            service.admit_execution.return_value = {"claim": {
                "task": {"taskId": "task"}, "attempt": {"attemptId": "attempt"}}}
            scheduler = ExecutionScheduler(service)
            error = OSError("ownership failed after suspended child creation")
            error.spawn_created = True
            error.execution_handle = None
            error.owned_popen_evidence = ProcessCreationEvidence(True, True, 1)
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution", side_effect=error):
                scheduler._admit_available()
            store.recover_executions.assert_called_once_with(
                "attempt", stopped_evidence={"kind": "creator-reaped", "exitCode": 1})
            store.release_execution.assert_not_called()
