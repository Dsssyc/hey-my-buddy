"""A physical connection wakes control; identity alone proves execution death."""
from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import contextlib
import os
import queue
import threading
import json
import uuid
from datetime import datetime, timedelta, timezone
import tempfile
import time
import unittest
from unittest.mock import Mock, call, patch

import c_two as cc

from admission import admit_execution
from support import BoardTestCase, InProcessBoard
from buddy.runtime.test_execution_delivery import WindowsOSProxy
from buddy.runtime.test_windows_process import FakeAPI, FakeProcess
from hey_my_buddy.blackboard.service.execution_scheduler import (
    ExecutionScheduler, _RecoveredExecution, _ExecutionObservation)
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
from hey_my_buddy.protocol import execution as launch_protocol
from hey_my_buddy.protocol import owned_process, process_identity
from hey_my_buddy.protocol.run_identity import RunIdentity
from blackboard.tasks.test_workflow import WorkflowTestCase


class ConnectionEvents:
    def __init__(self):
        self.events = queue.Queue()
        self.receives = queue.Queue()
        self.closed = threading.Event()

    def recv(self):
        self.receives.put(threading.get_ident())
        event = self.events.get()
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self):
        self.closed.set()
        self.events.put(StopIteration())


class PhysicalConnection:
    def __init__(self, publish):
        self.connection_id = id(self)
        self.publish = publish
        self.closed = None
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        return self.closed

    def close(self, *, emit=True, reason="peer_closed", operation="read", os_error=None):
        self.closed = SimpleNamespace(connection_id=self.connection_id, reason=reason,
                                      operation=operation, os_error=os_error)
        if emit:
            self.publish(SimpleNamespace(side="client", kind="closed",
                                         connection_id=self.connection_id, value=self.closed))


class ExecutionObservationTests(unittest.TestCase):
    def setUp(self):
        self.live_registry = Mock(spec=LiveRegistry)
        self.subscriptions = []
        self.subscribed = queue.Queue()
        subscription = patch("hey_my_buddy.blackboard.service.execution_scheduler.cc.connection_events",
                             side_effect=self.subscribe)
        self.subscribe_events = subscription.start()
        self.addCleanup(subscription.stop)

    def subscribe(self):
        events = ConnectionEvents()
        self.subscriptions.append(events)
        self.subscribed.put(events)
        return events

    def publish(self, event):
        if self.subscriptions:
            self.subscriptions[-1].events.put(event)

    def start_connections(self, scheduler):
        self.addCleanup(scheduler.stop)
        with patch.object(scheduler._thread, "run"):
            scheduler.start()
            scheduler._thread.join()

    @staticmethod
    def watch(scheduler, observation, attempt_id="attempt"):
        observer = _ExecutionObservation(attempt_id, threading.Condition(scheduler._lock))
        scheduler._observed[attempt_id] = observer
        scheduler._watch_connection(observation, observer)
        return observer

    def recovered(self, root, *, task_id="task", attempt_id="attempt"):
        claim = {"attempt": {"attemptId": attempt_id, "generation": 1}, "task": {"taskId": task_id}}
        location = _RecoveredExecution(Path(root), {"taskId": task_id, "attemptId": attempt_id, "generation": 1},
            RunIdentity(task_id=task_id, attempt_id=attempt_id, generation=1, invocation_id="execution"))
        return claim, location

    def client(self):
        physical = PhysicalConnection(self.publish)
        client = Mock(spec=["connect", "stop", "close", "connection_observation"])
        client.connection_observation = physical
        return client

    @staticmethod
    def monitor(scheduler, claim, handle, identity=None):
        scheduler._observed[claim["attempt"]["attemptId"]] = _ExecutionObservation(
            claim["task"]["taskId"], scheduler._condition)
        scheduler._monitor(claim, handle, identity)

    @contextmanager
    def observing(self, scheduler, claim, location, client, *, endpoint_reads=None):
        self.start_connections(scheduler)
        identity = {"pid": 1}
        attempt_id = claim["attempt"]["attemptId"]
        waits = queue.Queue()
        original_wait = threading.Condition.wait_for
        def wait(condition, predicate, timeout=None):
            if threading.current_thread().name == f"execution-{attempt_id}":
                waits.put(timeout)
            return original_wait(condition, predicate, timeout)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                   return_value=client, side_effect=endpoint_reads), \
             patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
             patch.object(threading.Condition, "wait_for", autospec=True, side_effect=wait):
            scheduler._handles[attempt_id] = (location, identity)
            scheduler._start_observer(claim, location, identity)
            try:
                yield waits
            finally:
                scheduler.stop()

    def test_unavailable_endpoint_keeps_work_unknown_without_creating_a_stop(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.execution_identity_confirmed.return_value = True
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            scheduler._condition.wait_for = lambda _predicate, timeout=None: scheduler._stopping.set()
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=None), \
                    patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                    patch("hey_my_buddy.blackboard.service.execution_scheduler.kill_verified_process_group") as kill:
                self.monitor(scheduler, claim, location, {"pid": 1})
            store.execution_connection.assert_called_once_with("attempt", connected=False)
            self.assertEqual(store.record_execution_issue.call_args.args[2].code, "EXECUTION_UNKNOWN")
            store.record_execution_deadline.assert_not_called()
            store.recover_executions.assert_not_called()
            kill.assert_not_called()

    def test_failed_stop_delivery_still_enforces_the_durable_external_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service",
                "selectedAttempt": {"forceRequestedAt": (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat(),
                                    "forceReason": "service-stop"}}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            client.stop.side_effect = BoardError("LIVE_UNAVAILABLE", "frozen endpoint")
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                    patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                    patch("hey_my_buddy.blackboard.service.execution_scheduler.kill_verified_process_group",
                          side_effect=lambda _identity: scheduler._stopping.set()) as kill:
                self.monitor(scheduler, claim, location, {"pid": 1})
            kill.assert_called_once_with({"pid": 1})
            client.connect.assert_called_once()
            self.live_registry.unbind.assert_called_once_with("attempt", client)
            store.recover_executions.assert_not_called()

    def test_unknown_process_identity_prevents_connection_and_signalling_after_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service",
                "selectedAttempt": {"forceRequestedAt": (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat(),
                                    "forceReason": "service-stop"}}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            scheduler._condition.wait_for = lambda _predicate, timeout=None: scheduler._stopping.set()
            claim, location = self.recovered(root)
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint") as read, \
                    patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="unknown"), \
                    patch("hey_my_buddy.protocol.process_identity.inspect_process_identity", return_value="unknown"), \
                    patch("hey_my_buddy.protocol.process_identity._SystemSource.signal_group") as send:
                self.monitor(scheduler, claim, location, {"pid": 1})
            read.assert_not_called()
            send.assert_not_called()
            store.recover_executions.assert_not_called()
            store.execution_connection.assert_called_once_with("attempt", connected=False)

    def test_failed_creation_releases_attempt_and_next_admission_runs(self):
        with tempfile.TemporaryDirectory() as root:
            first = {"sessionRecordPath": None, "attempt": {"attemptId": "attempt-1"},
                     "task": {"taskId": "task-1", "spec": {"adapter": "command"}}}
            second = {"sessionRecordPath": None, "attempt": {"attemptId": "attempt-2"},
                      "task": {"taskId": "task-2", "spec": {"adapter": "command"}}}
            store = Mock(directory=Path(root))
            service = Mock(store=store, control={})
            service.admit_execution.side_effect = [
                {"claim": first}, {"claim": second}, {"claim": None}]
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            creation_error = OSError("Popen did not create a child")
            creation_error.execution_handle = None
            creation_error.spawn_created = False
            handle = Mock(process_identity={"pid": 2})
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution",
                       side_effect=[creation_error, handle]), patch.object(scheduler, "_observe") as observe:
                scheduler._admit_available()
                store.release_execution.assert_called_once_with(
                    "attempt-1",
                    reason="execution-launch-failed")
                scheduler._admit_available()
                for thread in tuple(scheduler._threads):
                    thread.join(1)
            observe.assert_called_once_with(second, handle, {"pid": 2})
            self.assertEqual(service.admit_execution.call_count, 3)

    def test_failed_parent_capture_is_closed_by_a_verified_child_report(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
        handle = Mock(pid=123, process_started_at=456, pgid=123, job=None)
        identity = {"bootId": "test-boot", "pid": 123, "startedAt": 456,
                    "processGroupId": 123, "jobName": None}
        scheduler._handles["attempt-1"] = (handle, None)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.boot_id", return_value="test-boot"):
            self.assertIsNone(scheduler.verify_identity("attempt-1", {**identity, "startedAt": 111}, None))
            self.assertEqual(scheduler.verify_identity("attempt-1", identity, None), identity)
        claim = {"attempt": {"attemptId": "attempt-1"}, "task": {"taskId": "task-1"}}
        with patch.object(scheduler, "_observe") as observe:
            self.monitor(scheduler, claim, handle)
        observe.assert_called_once_with(claim, handle, identity)
        store.release_execution.assert_not_called()
        handle.terminate.assert_not_called()

    def test_missing_identity_uses_reaped_creator_and_empty_group(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
        handle = Mock()
        handle.process.poll.return_value = 1
        handle.group_alive.return_value = False
        scheduler._handles["attempt-1"] = (handle, None)
        store.task_get.return_value = {"task": {"selectedAttempt": {
            "attemptId": "attempt-1", "executionState": "finished"}}}
        self.monitor(scheduler, {"attempt": {"attemptId": "attempt-1"},
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
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
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
                self.monitor(scheduler, claim, handle, identity)
            self.assertEqual(store.recover_executions.call_args_list,
                             [call("attempt-1", process_identity=identity,
                                   stopped_evidence={"kind": "identity-absent"})])
            store.release_execution.assert_not_called()

    def test_closed_physical_connection_waits_for_identity_before_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock()
            store.directory = Path(root)
            store.task_get.return_value = {"task": {"cancelRequested": False, "selectedAttempt": {
                "attemptId": "attempt-1", "executionState": "finished"}}}
            service = Mock(store=store)
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            client = self.client()
            client.connection_observation.close()
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
                self.monitor(scheduler, claim, handle, {"pid": 1})
            self.assertEqual(inspect.call_count, 2)
            store.recover_executions.assert_called_once_with(
                "attempt-1", process_identity={"pid": 1},
                stopped_evidence={"kind": "identity-absent"})
            handle.signal_group.assert_not_called()
            self.assertEqual(self.live_registry.unbind.call_count, 1)

    def test_identity_report_checks_captured_identity_or_live_process_after_restart(self):
        service = Mock()
        scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
        handle = Mock()
        captured = {"bootId": "test-boot", "pid": 1, "startedAt": 11,
                    "processGroupId": 1, "jobName": None}
        with scheduler._lock:
            scheduler._handles["owned"] = (handle, captured)
        report = {**captured, "pid": 2, "processGroupId": 2}
        self.assertIsNone(scheduler.verify_identity("owned", report, None))
        self.assertEqual(scheduler.verify_identity("owned", captured, None), captured)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                   side_effect=["alive", "unknown", "alive"]):
            self.assertEqual(scheduler.verify_identity("restarted", report, report), report)
            self.assertIsNone(scheduler.verify_identity("unverified", report, report))
            # Before the creator's marker write completes, a live reporter is the root.
            self.assertEqual(scheduler.verify_identity("unrecorded", report, None), report)

    def test_windows_report_from_another_pid_of_the_root_job_verifies_and_records_the_root(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        root = {"pid": 301, "startedAt": 1339247086400000000,
                "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000c1"}
        reporter = {"pid": 402, "startedAt": 815, "jobName": root["jobName"]}
        handle = Mock(pid=301, process_started_at=root["startedAt"],
                      job=SimpleNamespace(name=root["jobName"]))
        scheduler._handles["attempt-1"] = (handle, None)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.verify_job_member",
                   return_value=True) as member:
            self.assertEqual(scheduler.verify_identity("attempt-1", reporter, None), root)
            self.assertEqual(scheduler.verify_identity("attempt-1", reporter, root), root)
            # The child's report closes the failed parent-side capture with the root.
            self.assertEqual(scheduler.verify_identity("attempt-1", reporter, None), root)
        member.assert_called_with(reporter, root["jobName"])
        adopted = scheduler._handles["attempt-1"]
        self.assertIs(adopted[0], handle)
        self.assertEqual(adopted[1], root)

    def test_windows_report_outside_the_root_job_or_without_a_root_is_refused(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        root = {"pid": 301, "startedAt": 1339247086400000000,
                "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000c2"}
        reporter = {"pid": 402, "startedAt": 815, "jobName": root["jobName"]}
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.verify_job_member",
                   return_value=False) as member:
            self.assertIsNone(scheduler.verify_identity("attempt-1", reporter, root))
        member.assert_called_once_with(reporter, root["jobName"])
        # A reporter naming another Job never reaches the membership check.
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.verify_job_member") as member:
            self.assertIsNone(scheduler.verify_identity(
                "attempt-1", {**reporter, "jobName": root["jobName"] + "0"}, root))
            member.assert_not_called()
        # Without a handle, a recorded root, or a marker there is nothing to verify against.
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.verify_job_member") as member:
            self.assertIsNone(scheduler.verify_identity("attempt-1", reporter, None))
            member.assert_not_called()

    def test_windows_report_after_restart_uses_the_recorded_root_without_a_handle(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        root = {"pid": 301, "startedAt": 1339247086400000000,
                "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000c3"}
        reporter = {"pid": 402, "startedAt": 815, "jobName": root["jobName"]}
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.verify_job_member",
                   side_effect=[True, False]):
            self.assertIs(scheduler.verify_identity("restarted", reporter, root), root)
            self.assertIsNone(scheduler.verify_identity("restarted", reporter, root))

    def test_router_budget_is_durable_before_forced_stop(self):
        with tempfile.TemporaryDirectory() as root:
            service = Mock()
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            handle = Mock()
            handle.execution_binding = {"taskId": "task", "attemptId": "attempt", "generation": 1}
            handle.budget_path = Path(root) / "execution-budget.json"
            handle.budget_path.write_text(json.dumps({"binding": handle.execution_binding,
                                                       "deadline": time.monotonic() - 1}), encoding="utf-8", newline="\n")
            client = Mock()
            claim = {"attempt": {"attemptId": "attempt"}}
            service.store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "deadline"}
            sent, forced_at, _deadline = scheduler._apply_stop({"cancelRequested": False}, claim, handle, client,
                                                      None, None)
            service.store.record_execution_deadline.assert_called_once_with("attempt", "deadline")
            client.stop.assert_called_once_with(initiator="deadline", grace_deadline=None, force=True)
            self.assertEqual(sent, ("deadline", None, True))
            self.assertIsNotNone(forced_at)

    def test_grace_is_sent_before_force_and_persisted_only_at_expiry(self):
        store = Mock()
        scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
        handle = Mock()
        handle.budget_path = Path(tempfile.gettempdir()) / ("missing-budget-" + uuid.uuid4().hex)
        client = Mock()
        claim = {"attempt": {"attemptId": "attempt"}}
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        sent, forced_at, _deadline = scheduler._apply_stop({"cancelRequested": True, "stopInitiator": "host",
                                                   "graceDeadline": future}, claim, handle, client, None, None)
        self.assertEqual(sent, ("host", future, False))
        self.assertIsNone(forced_at)
        self.assertGreater(client.stop.call_args.kwargs["grace_deadline"], time.monotonic())
        store.record_execution_deadline.assert_not_called()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        store.record_execution_deadline.return_value = {
            "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "grace-expired"}
        sent, forced_at, _deadline = scheduler._apply_stop({"cancelRequested": True, "stopInitiator": "host",
                                                   "graceDeadline": past}, claim, handle, client, sent, forced_at)
        self.assertEqual(sent, ("host", past, True))
        self.assertIsNotNone(forced_at)
        store.record_execution_deadline.assert_called_once_with("attempt", "grace-expired")
        self.assertEqual(client.stop.call_args.kwargs,
                         {"initiator": "host", "grace_deadline": None, "force": True})


    def test_conflict_is_retried_and_other_admission_errors_are_recorded(self):
        store = Mock()
        store.execution_observations.return_value = []
        service = Mock(store=store, control={})
        scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
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
        store.record_service_issue.assert_called_once()
        stage, error = store.record_service_issue.call_args.args
        self.assertEqual(stage, "admission")
        self.assertEqual(error.code, "INVALID_ARGUMENT")

    def test_reconnected_channel_receives_the_same_stop_again(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service"}}
            service = Mock(store=store)
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            handle = Mock()
            handle.process.poll.return_value = None
            handle.execution_directory = Path(root)
            handle.budget_path = Path(root) / "budget.json"
            clients = [self.client(), self.client()]
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            clients[0].connection_observation.close()
            clients[1].connect.side_effect = scheduler._stopping.set
            claim = {"attempt": {"attemptId": "attempt"}, "task": {"taskId": "task"}}
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       side_effect=clients), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       return_value="alive"):
                self.monitor(scheduler, claim, handle, {"pid": 1})
            for client in clients:
                client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)

    def test_malformed_endpoint_is_visible_and_does_not_block_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"selectedAttempt": {
                "attemptId": "attempt", "executionState": "finished"}}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            handle = Mock()
            handle.execution_directory = Path(root)
            handle.budget_path = Path(root) / "budget.json"
            error = BoardError("LIVE_UNAVAILABLE", "bad private endpoint")
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       side_effect=error), patch(
                       "hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                       side_effect=["alive", "identity-absent"]):
                self.monitor(scheduler, {"attempt": {"attemptId": "attempt"}, "task": {"taskId": "task"}},
                                   handle, {"pid": 1})
            store.record_execution_issue.assert_called_once()
            args = store.record_execution_issue.call_args.args
            self.assertEqual(args[:2], ("attempt", "connection"))
            self.assertEqual(args[2].code, "EXECUTION_UNKNOWN")
            store.record_execution_deadline.assert_not_called()
            store.recover_executions.assert_called_once_with(
                "attempt", process_identity={"pid": 1}, stopped_evidence={"kind": "identity-absent"})
            self.assertNotIn("attempt", scheduler._handles)

    def test_force_age_comes_from_persisted_request(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        old = (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat()
        forced_at = scheduler._force_requested_at({"selectedAttempt": {"forceRequestedAt": old}})
        self.assertGreaterEqual(time.monotonic() - forced_at, 8)

    def test_zero_drain_sends_stop_before_detaching_without_an_extra_connection(self):
        with tempfile.TemporaryDirectory() as root:
            view = {"cancelRequested": False}
            store = Mock(directory=Path(root))
            store.task_get.side_effect = lambda _params: {"task": dict(view)}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            with self.observing(scheduler, claim, location, client) as waits:
                self.assertIsNone(waits.get(timeout=2))
                view.update(cancelRequested=True, stopInitiator="service", graceDeadline=None)
                scheduler.send_pending_stops()
                client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
                client.connect.assert_called_once()
                self.assertIsNotNone(waits.get(timeout=2))
                scheduler.stop()
            self.assertEqual(client.stop.call_count, 1)
            store.record_execution_deadline.assert_called_once_with("attempt", "service-stop")
            store.recover_executions.assert_not_called()

    def test_zero_drain_stop_bypasses_an_unconnected_observers_reconnect_backoff(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.execution_identity_confirmed.return_value = False
            view = {'cancelRequested': False}
            store.task_get.side_effect = lambda _params: {'task': dict(view)}
            store.record_execution_deadline.return_value = {
                'forceRequestedAt': datetime.now(timezone.utc).isoformat(), 'forceReason': 'service-stop'}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            with patch('hey_my_buddy.blackboard.service.execution_scheduler.time.monotonic', return_value=100.0), \
                 self.observing(scheduler, claim, location, client, endpoint_reads=[None, client]) as waits:
                self.assertAlmostEqual(waits.get(timeout=2), .1)
                client.connect.assert_not_called()
                view.update(cancelRequested=True, stopInitiator='service')
                scheduler.send_pending_stops()
                client.connect.assert_called_once()
                client.stop.assert_called_once_with(initiator='service', grace_deadline=None, force=True)
            store.recover_executions.assert_not_called()

    def test_task_progress_and_connection_wakes_never_make_another_observer_read(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.side_effect = lambda _params: {'task': {'cancelRequested': False}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            self.start_connections(scheduler)
            pairs = [self.recovered(Path(root) / name, task_id=name, attempt_id=name) for name in ('a', 'b')]
            clients = {name: self.client() for name in ('a', 'b')}
            waits = queue.Queue()
            original_wait = threading.Condition.wait_for
            def wait(condition, predicate, timeout=None):
                name = threading.current_thread().name
                if name in ('execution-a', 'execution-b'):
                    waits.put((name, timeout))
                return original_wait(condition, predicate, timeout)
            with patch('hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity', return_value='alive'), \
                 patch('hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint',
                       side_effect=lambda directory, *_args, **_kwargs: clients[directory.name]), \
                 patch.object(threading.Condition, 'wait_for', autospec=True, side_effect=wait):
                for claim, location in pairs:
                    scheduler._handles[claim['attempt']['attemptId']] = (location, {'pid': 1})
                    scheduler._start_observer(claim, location, {'pid': 1})
                try:
                    self.assertEqual({waits.get(timeout=2), waits.get(timeout=2)},
                                     {('execution-a', None), ('execution-b', None)})
                    other_reads = store.task_get.call_args_list.count(call({'runId': 'b'}))
                    scheduler.wake(frozenset({'a'}))
                    self.assertEqual(waits.get(timeout=2), ('execution-a', None))
                    scheduler.wake_attempt('a')
                    self.assertEqual(waits.get(timeout=2), ('execution-a', None))
                    clients['a'].connection_observation.close()
                    name, timeout = waits.get(timeout=2)
                    self.assertEqual(name, 'execution-a')
                    self.assertGreater(timeout, 0)
                    self.assertEqual(store.task_get.call_args_list.count(call({'runId': 'b'})), other_reads)
                    with self.assertRaises(queue.Empty):
                        waits.get(timeout=.03)
                finally:
                    scheduler.stop()

    def test_stop_arriving_during_authentication_uses_that_same_connection(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            view = {"cancelRequested": False}
            store.task_get.side_effect = lambda _params: {"task": dict(view)}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            authenticating, release, delivered = threading.Event(), threading.Event(), threading.Event()
            def authenticate():
                authenticating.set()
                if not release.wait(2):
                    raise RuntimeError("fixture authentication remained gated")
            client.connect.side_effect = authenticate
            with self.observing(scheduler, claim, location, client):
                self.assertTrue(authenticating.wait(2))
                view.update(cancelRequested=True, stopInitiator="service")
                sender = threading.Thread(target=lambda: (scheduler.send_pending_stops(), delivered.set()), daemon=True)
                sender.start()
                try:
                    self.assertFalse(delivered.wait(.03))
                    client.stop.assert_not_called()
                    release.set()
                    self.assertTrue(delivered.wait(2))
                    client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
                    client.connect.assert_called_once()
                finally:
                    release.set()
                    sender.join(2)

    def test_stop_barrier_includes_an_observer_before_its_thread_body_starts(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            view = {"cancelRequested": False}
            store.task_get.side_effect = lambda _params: {"task": dict(view)}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
            monitor = scheduler._monitor
            def gated(*args):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("fixture observer remained gated")
                monitor(*args)
            with patch.object(scheduler, "_monitor", side_effect=gated), \
                 self.observing(scheduler, claim, location, client):
                self.assertTrue(entered.wait(2))
                view.update(cancelRequested=True, stopInitiator="service")
                sender = threading.Thread(target=lambda: (scheduler.send_pending_stops(), delivered.set()), daemon=True)
                sender.start()
                try:
                    self.assertFalse(delivered.wait(.03))
                    client.stop.assert_not_called()
                    release.set()
                    self.assertTrue(delivered.wait(2))
                    client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
                finally:
                    release.set()
                    sender.join(2)

    def test_stop_barrier_covers_an_execution_still_being_created(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            view = {"cancelRequested": True, "stopInitiator": "service"}
            store.task_get.side_effect = lambda _params: {"task": dict(view)}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            service = Mock(store=store, control={})
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            claim, location = self.recovered(root)
            claim["sessionRecordPath"] = None
            service.admit_execution.return_value = {"claim": claim}
            handle = SimpleNamespace(**location.__dict__, process_identity={"pid": 1},
                                     budget_path=location.budget_path, process=None)
            client = self.client()
            creating, release, delivered = threading.Event(), threading.Event(), threading.Event()
            def launch(*_args, **_kwargs):
                creating.set()
                if not release.wait(2):
                    raise RuntimeError("fixture creation remained gated")
                return handle
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution", side_effect=launch), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"):
                admission = threading.Thread(target=scheduler._admit_available, daemon=True)
                admission.start()
                sender = threading.Thread(target=lambda: (scheduler.send_pending_stops(), delivered.set()), daemon=True)
                try:
                    self.assertTrue(creating.wait(2))
                    service.control["stopping"] = True
                    sender.start()
                    self.assertFalse(delivered.wait(.03))
                    release.set()
                    self.assertTrue(delivered.wait(2))
                    client.stop.assert_called_once_with(initiator="service", grace_deadline=None, force=True)
                finally:
                    release.set()
                    admission.join(2)
                    sender.join(2)
                    scheduler.stop()

    def test_stop_barrier_includes_unidentified_owned_children(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            view = {"cancelRequested": False}
            store.task_get.side_effect = lambda _params: {"task": dict(view)}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, _location = self.recovered(root)
            handle = Mock(budget_path=Path(root) / "budget.json")
            handle.process.poll.return_value = None
            scheduler._handles["attempt"] = (handle, None)
            scheduler._start_observer(claim, handle, None)
            try:
                view.update(cancelRequested=True, stopInitiator="service")
                scheduler.send_pending_stops()
                handle.terminate.assert_called_with(grace_seconds=0)
                store.record_execution_deadline.assert_called_with("attempt", "service-stop")
                store.recover_executions.assert_not_called()
            finally:
                scheduler.stop()

    def test_failed_observer_start_retains_creator_evidence_and_retries_on_resume(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            service = Mock(store=store, control={})
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            claim, _location = self.recovered(root)
            claim["sessionRecordPath"] = None
            service.admit_execution.return_value = {"claim": claim}
            store.execution_observations.return_value = [claim]
            handle = Mock(process_identity={"pid": 1})
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution", return_value=handle), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.threading.Thread.start",
                       side_effect=RuntimeError("thread creation failed")):
                with self.assertRaisesRegex(RuntimeError, "thread creation failed"):
                    scheduler._admit_available()
            self.assertEqual(scheduler._observed, {})
            self.assertEqual(scheduler._threads, set())
            self.assertEqual(scheduler._handles, {"attempt": (handle, {"pid": 1})})
            scheduler.send_pending_stops()
            resumed = threading.Event()
            with patch.object(scheduler, "_observe", side_effect=lambda *_args: resumed.set()) as observe:
                scheduler._resume_executions()
                self.assertTrue(resumed.wait(2))
                scheduler.stop()
            observe.assert_called_once_with(claim, handle, {"pid": 1})
            store.release_execution.assert_not_called()
            store.recover_executions.assert_not_called()

    def test_idle_observer_waits_indefinitely_and_restart_detaches_without_a_stop(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            with self.observing(scheduler, claim, location, client) as waits:
                self.assertIsNone(waits.get(timeout=2))
                self.subscriptions[0].receives.get(timeout=2)
                reads = store.task_get.call_count
                with self.assertRaises(queue.Empty):
                    waits.get(timeout=.04)
                self.assertEqual(store.task_get.call_count, reads)
                scheduler.stop()
                self.assertEqual(scheduler._observed, {})
                self.assertIsNone(client.connection_observation.closed)
                self.assertFalse(scheduler._connection_thread.is_alive())
                self.assertTrue(self.subscriptions[0].closed.is_set())
            self.assertEqual(client.connection_observation.snapshots, 1)
            self.subscribe_events.assert_called_once_with()
            client.stop.assert_not_called()
            store.recover_executions.assert_not_called()

    def test_late_budget_wakes_an_indefinite_wait_and_arms_its_deadline(self):
        from hey_my_buddy.protocol.execution import fsync_json
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            with self.observing(scheduler, claim, location, client) as waits:
                self.assertIsNone(waits.get(timeout=2))
                fsync_json(location.budget_path, {"binding": location.execution_binding,
                                                "deadline": time.monotonic() + 60})
                scheduler.wake()
                timeout = waits.get(timeout=2)
                self.assertGreater(timeout, 58)
                self.assertLessEqual(timeout, 60)
                client.connect.assert_called_once()
                client.stop.assert_not_called()

    def test_grace_deadlines_precede_any_hard_stop_retry(self):
        with tempfile.TemporaryDirectory() as root:
            clock = [100.0]
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {
                "cancelRequested": True, "stopInitiator": "host", "graceDeadline": "grace"}}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": "forced", "forceReason": "grace-expired"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            waits, killed_at = [], []
            def advance(_predicate, timeout=None):
                waits.append(timeout)
                if len(waits) == 3:
                    scheduler._stopping.set()
                else:
                    clock[0] += timeout
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.time.monotonic", side_effect=lambda: clock[0]), \
                 patch.object(scheduler, "_deadline", return_value=102.0), \
                 patch.object(scheduler, "_timestamp", side_effect=lambda value: 102.0 if value else None), \
                 patch.object(scheduler._condition, "wait_for", side_effect=advance), \
                 patch.object(scheduler, "_force_if_required", side_effect=lambda *_: killed_at.append(clock[0])):
                self.monitor(scheduler, claim, location, {"pid": 1})
            self.assertEqual(waits[:2], [2.0, 8.0])
            self.assertAlmostEqual(waits[2], .1)
            self.assertEqual(killed_at, [110.0])
            self.assertEqual(client.stop.call_args_list, [
                call(initiator="host", grace_deadline=102.0, force=False),
                call(initiator="host", grace_deadline=None, force=True)])
            store.record_execution_deadline.assert_called_once_with("attempt", "grace-expired")

    def test_unconfirmed_hard_stop_retries_after_unknown_identity_or_signalling_failure(self):
        for first in ("unknown", "signal-error"):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as root:
                clock = [108.0]
                store = Mock(directory=Path(root))
                store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service",
                    "selectedAttempt": {"forceRequestedAt": "forced", "forceReason": "service-stop"}}}
                scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
                claim, location = self.recovered(root)
                client = self.client()
                identity = {"pid": 1, "jobName": None, "processGroupId": 1}
                waits = []
                def advance(_predicate, timeout=None):
                    waits.append(timeout)
                    if len(waits) == 2:
                        scheduler._stopping.set()
                    else:
                        clock[0] += timeout
                with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                     patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                     patch("hey_my_buddy.blackboard.service.execution_scheduler.time.monotonic", side_effect=lambda: clock[0]), \
                     patch.object(scheduler, "_timestamp", return_value=100.0), \
                     patch.object(scheduler._condition, "wait_for", side_effect=advance), \
                     patch("hey_my_buddy.protocol.process_identity.inspect_process_identity",
                           side_effect=["unknown", "alive"] if first == "unknown" else ["alive", "alive"]), \
                     patch("hey_my_buddy.protocol.process_identity._SystemSource.signal_group",
                           side_effect=[OSError("signal failed"), None] if first == "signal-error" else None) as signal:
                    self.monitor(scheduler, claim, location, identity)
                self.assertAlmostEqual(waits[0], .1)
                self.assertAlmostEqual(waits[1], .2)
                self.assertEqual(signal.call_count, 1 if first == "unknown" else 2)
                store.recover_executions.assert_not_called()
                store.release_execution.assert_not_called()
                if first == "signal-error":
                    self.assertEqual(store.record_execution_issue.call_args.args[1], "force-stop")

    def test_budget_is_read_on_attachment_and_drives_force_without_a_notification(self):
        from hey_my_buddy.protocol.execution import fsync_json
        with tempfile.TemporaryDirectory() as root:
            clock = [100.0]
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": False}}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": "forced", "forceReason": "deadline"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            fsync_json(location.budget_path, {"binding": location.execution_binding, "deadline": 104.0})
            client = self.client()
            waits = []
            def advance(_predicate, timeout=None):
                waits.append(timeout)
                if len(waits) == 2:
                    scheduler._stopping.set()
                else:
                    clock[0] += timeout
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.time.monotonic", side_effect=lambda: clock[0]), \
                 patch.object(scheduler, "_timestamp", side_effect=lambda value: 104.0 if value else None), \
                 patch.object(scheduler._condition, "wait_for", side_effect=advance):
                self.monitor(scheduler, claim, location, {"pid": 1})
            self.assertEqual(waits, [4.0, 8.0])
            client.stop.assert_called_once_with(initiator="deadline", grace_deadline=None, force=True)
            store.record_execution_deadline.assert_called_once_with("attempt", "deadline")

    def test_notification_between_read_and_wait_is_consumed_without_sleeping(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            client = self.client()
            views = [{"cancelRequested": False}, {"cancelRequested": True,
                     "stopInitiator": "host", "graceDeadline": "grace"}]
            def read(_params):
                if store.task_get.call_count == 1:
                    scheduler.wake()
                    return {"task": views[0]}
                return {"task": views[1]}
            store.task_get.side_effect = read
            waits = []
            def stop_wait(_predicate, timeout=None):
                waits.append(timeout)
                scheduler._stopping.set()
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                 patch.object(scheduler, "_deadline", side_effect=lambda _: time.monotonic() + 60), \
                 patch.object(scheduler._condition, "wait_for", side_effect=stop_wait):
                self.monitor(scheduler, claim, location, {"pid": 1})
            self.assertEqual(store.task_get.call_count, 2)
            self.assertEqual(len(waits), 1)
            self.assertGreater(waits[0], 58)
            client.stop.assert_called_once()

    def test_closed_connection_with_live_or_unknown_identity_never_settles(self):
        for remaining in ("alive", "unknown"):
            with self.subTest(identity=remaining), tempfile.TemporaryDirectory() as root:
                store = Mock(directory=Path(root))
                store.task_get.return_value = {"task": {"cancelRequested": False}}
                scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
                claim, location = self.recovered(root)
                client = self.client()
                client.connection_observation.close()
                original_wait = scheduler._condition.wait_for
                waits = []
                def stop_at_backoff(predicate, timeout=None):
                    if timeout is None:
                        return original_wait(predicate)
                    waits.append(timeout)
                    scheduler._stopping.set()
                with patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint", return_value=client), \
                     patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                           side_effect=["alive", remaining]), \
                     patch.object(scheduler._condition, "wait_for", side_effect=stop_at_backoff), \
                     patch("hey_my_buddy.blackboard.service.execution_scheduler.kill_verified_process_group") as kill:
                    self.monitor(scheduler, claim, location, {"pid": 1})
                self.assertEqual(len(waits), 1)
                self.assertGreater(waits[0], 0)
                store.recover_executions.assert_not_called()
                store.release_execution.assert_not_called()
                kill.assert_not_called()
                issue = store.record_execution_issue.call_args.args[2]
                self.assertEqual(issue.code, "EXECUTION_UNKNOWN")
                self.assertEqual(issue.message, "The execution connection is unavailable")
                self.assertEqual(issue.details, {})

    def test_logical_reconnects_share_one_physical_watch_until_close(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        self.start_connections(scheduler)
        events = self.subscribed.get(timeout=2)
        events.receives.get(timeout=2)
        client = self.client()
        observation = client.connection_observation
        first = self.watch(scheduler, observation, "first")
        client.close()
        # A new logical client can have a new observation of the same stream.
        same_connection = PhysicalConnection(self.publish)
        same_connection.connection_id = observation.connection_id
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.threading.Thread.start") as start:
            second = self.watch(scheduler, same_connection, "second")
        start.assert_not_called()
        self.assertIs(first.watch, second.watch)
        self.assertIsNone(first.watch.error)
        self.assertEqual(observation.snapshots, 1)
        self.assertEqual(same_connection.snapshots, 0)
        self.assertEqual(len(scheduler._connections), 1)
        observation.close(os_error=32)
        events.receives.get(timeout=2)
        self.assertEqual((first.revision, second.revision), (1, 1))
        self.assertEqual(first.watch.error.code, "LIVE_UNAVAILABLE")
        self.assertEqual(first.watch.error.details, {
            "connectionId": observation.connection_id, "reason": "peer_closed",
            "io_operation": "read", "raw_os_error": 32})
        self.assertEqual(scheduler._connections, {})

    def test_server_opened_and_untracked_events_do_not_wake_observers(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        self.start_connections(scheduler)
        events = self.subscribed.get(timeout=2)
        events.receives.get(timeout=2)
        observation = self.client().connection_observation
        observer = self.watch(scheduler, observation)
        for side, kind, key in (("server", "closed", observation.connection_id),
                                ("client", "opened", observation.connection_id),
                                ("client", "closed", -1)):
            self.publish(SimpleNamespace(side=side, kind=kind, connection_id=key, value=None))
            events.receives.get(timeout=2)
            self.assertEqual(observer.revision, 0)
            self.assertIsNone(observer.watch.error)
        self.assertEqual(observation.snapshots, 1)

    def test_close_before_registration_is_found_by_one_snapshot(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        self.start_connections(scheduler)
        events = self.subscribed.get(timeout=2)
        events.receives.get(timeout=2)
        observation = self.client().connection_observation
        observation.close()
        events.receives.get(timeout=2)  # The event has passed without a watch.
        observer = self.watch(scheduler, observation)
        self.assertEqual(observation.snapshots, 1)
        self.assertEqual(observer.revision, 1)
        self.assertEqual(observer.watch.error.code, "LIVE_UNAVAILABLE")
        self.assertEqual(observer.watch.error.details["connectionId"], observation.connection_id)
        self.assertEqual(scheduler._connections, {})

    def test_lag_resubscribes_and_snapshots_all_tracked_physical_connections(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        self.start_connections(scheduler)
        events = self.subscribed.get(timeout=2)
        consumer = events.receives.get(timeout=2)
        closed, live = [self.client().connection_observation for _ in range(2)]
        first, shared = self.watch(scheduler, closed, "first"), self.watch(scheduler, closed, "shared")
        other = self.watch(scheduler, live, "other")
        closed.close(emit=False, reason="io_error", operation="read_header", os_error=5)
        events.events.put(cc.ConnectionEventsLagged("fixture lag"))
        replacement = self.subscribed.get(timeout=2)
        self.assertEqual(replacement.receives.get(timeout=2), consumer)
        self.assertIsNot(events, replacement)
        self.assertEqual(self.subscribe_events.call_count, 2)
        self.assertEqual((closed.snapshots, live.snapshots), (2, 2))
        self.assertEqual((first.revision, shared.revision, other.revision), (1, 1, 0))
        self.assertEqual(first.watch.error.details, {
            "connectionId": closed.connection_id, "reason": "io_error",
            "io_operation": "read_header", "raw_os_error": 5})
        self.assertIsNone(other.watch.error)
        self.assertEqual(set(scheduler._connections), {live.connection_id})
        live.close()
        replacement.receives.get(timeout=2)
        self.assertEqual(other.revision, 1)
        self.assertEqual(scheduler._connections, {})
        scheduler.stop()
        self.assertTrue(replacement.closed.is_set())
        self.assertFalse(scheduler._connection_thread.is_alive())

    def test_close_during_resubscription_is_found_by_the_following_snapshot(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        self.start_connections(scheduler)
        events = self.subscribed.get(timeout=2)
        events.receives.get(timeout=2)
        observation = self.client().connection_observation
        observer = self.watch(scheduler, observation)
        def subscribe_after_close():
            observation.close(emit=False)
            return self.subscribe()
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.cc.connection_events",
                   side_effect=subscribe_after_close) as subscribe:
            events.events.put(cc.ConnectionEventsLagged("fixture lag"))
            replacement = self.subscribed.get(timeout=2)
            replacement.receives.get(timeout=2)
        subscribe.assert_called_once_with()
        self.assertEqual(observation.snapshots, 2)
        self.assertEqual(observer.revision, 1)
        self.assertEqual(observer.watch.error.code, "LIVE_UNAVAILABLE")
        self.assertEqual(scheduler._connections, {})

    def test_shutdown_during_lag_does_not_create_a_replacement_subscription(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        events = ConnectionEvents()
        receiving = threading.Event()
        def lag_on_close():
            receiving.set()
            events.closed.wait()
            raise cc.ConnectionEventsLagged("fixture lag at shutdown")
        with patch.object(events, "recv", side_effect=lag_on_close), \
             patch("hey_my_buddy.blackboard.service.execution_scheduler.cc.connection_events",
                   return_value=events) as subscribe:
            self.start_connections(scheduler)
            self.assertTrue(receiving.wait(2))
            scheduler.stop()
        subscribe.assert_called_once_with()
        self.assertFalse(scheduler._connection_thread.is_alive())

    def test_failed_consumer_start_closes_its_subscription_and_does_not_admit(self):
        scheduler = ExecutionScheduler(Mock(), live_registry=self.live_registry)
        with patch.object(scheduler._connection_thread, "start", side_effect=RuntimeError("thread creation failed")), \
             patch.object(scheduler._thread, "start") as admit:
            with self.assertRaisesRegex(RuntimeError, "thread creation failed"):
                scheduler.start()
        self.assertTrue(self.subscriptions[0].closed.is_set())
        admit.assert_not_called()

    def test_unreadable_stop_endpoint_still_persists_the_external_stop_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock(directory=Path(root))
            store.task_get.return_value = {"task": {"cancelRequested": True, "stopInitiator": "service"}}
            store.record_execution_deadline.return_value = {
                "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "service-stop"}
            scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
            claim, location = self.recovered(root)
            scheduler._condition.wait_for = lambda predicate, timeout=None: predicate() or scheduler._stopping.set()
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity", return_value="alive"), \
                 patch("hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint",
                       side_effect=PermissionError("unreadable material")):
                scheduler.send_pending_stops()
                self.monitor(scheduler, claim, location, {"pid": 1})
            store.record_execution_deadline.assert_called_once_with("attempt", "service-stop")
            store.execution_connection.assert_called_once_with("attempt", connected=False)
            store.recover_executions.assert_not_called()

    def test_invalid_budget_is_visible_and_does_not_prevent_stop_delivery(self):
        for material in ('{not json', '{"binding":{},"deadline":1}', '{"binding":{},"deadline":"bad"}'):
            with self.subTest(material=material), tempfile.TemporaryDirectory() as root:
                store = Mock()
                store.record_execution_deadline.return_value = {
                    "forceRequestedAt": datetime.now(timezone.utc).isoformat(), "forceReason": "forced-stop"}
                scheduler = ExecutionScheduler(Mock(store=store), live_registry=self.live_registry)
                handle = Mock(execution_binding={"taskId": "task", "attemptId": "attempt", "generation": 1})
                handle.budget_path = Path(root) / "budget.json"
                handle.budget_path.write_text(material, encoding="utf-8", newline="\n")
                client = Mock()
                sent, forced_at, _deadline = scheduler._apply_stop({}, {"attempt": {"attemptId": "attempt"}},
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
                "sessionRecordPath": None, "task": {"taskId": "task", "spec": {"adapter": "command"}},
                "attempt": {"attemptId": "attempt"}}}
            scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            error = OSError("ownership failed after suspended child creation")
            error.spawn_created = True
            error.execution_handle = None
            error.owned_popen_evidence = ProcessCreationEvidence(True, True, 1)
            with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution", side_effect=error):
                scheduler._admit_available()
            store.recover_executions.assert_called_once_with(
                "attempt", stopped_evidence={"kind": "creator-reaped", "exitCode": 1})
            store.release_execution.assert_not_called()

    def test_launch_uses_admitted_record_path_without_reading_the_database(self):
        claim = {"task": {"taskId": "task"}, "attempt": {"attemptId": "attempt"},
                 "sessionRecordPath": "/private/fixture/session.jsonl"}
        store = Mock(directory=Path("/private/fixture"))
        service = Mock(store=store, control={})
        service.admit_execution.side_effect = [{"claim": claim}, {"claim": None}]
        scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution") as launch, \
                patch.object(scheduler, "_start_observer"):
            scheduler._admit_available()
        self.assertEqual(launch.call_args.kwargs["session_record_path"], claim["sessionRecordPath"])
        store.db.read.assert_not_called()


class RootCreationTeardownTests(BoardTestCase):
    """A failure inside the extended creation boundary never returns a root.

    The real launch_execution runs its Windows path over a faked OS boundary
    (Popen and the Win32 API), and the real scheduler and store settle the
    attempt: the root is stopped on the Job handle the service still holds and
    the failure is reported as creation evidence, never as a live handle.
    """

    def setUp(self):
        super().setUp()
        self.case_index = 0

    def launched_windows_root(self, *, fail_at=None, fail_capture=False, fail_marker=False):
        self.case_index += 1
        board = InProcessBoard(self.directory / f"case-{self.case_index}")
        self._stack.append(board)
        client = board.client()
        task = board.submit_execution(requestId="creation-failure", task="work", adapter="command",
                                      cwd=str(self.workdir()), argv=["echo", "work"])["task"]
        claim = admit_execution(client, "n" * 32)["claim"]
        scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = fail_at
        original = launch_protocol.fsync_json

        def write(path, value):
            if fail_marker and path.name == "spawn.marker":
                raise OSError("marker write failed")
            original(path, value)

        with contextlib.ExitStack() as stack:
            for boundary in (
                    patch.object(board.service, "admit_execution", return_value={"claim": claim}),
                    patch.object(launch_protocol, "os", WindowsOSProxy()),
                    patch.object(owned_process, "os", WindowsOSProxy()),
                    patch.object(owned_process, "WindowsAPI", return_value=api),
                    patch.object(owned_process.subprocess, "Popen", return_value=process),
                    patch.object(process_identity, "_creation_time", return_value=10),
                    patch.object(launch_protocol, "fsync_json", side_effect=write)):
                stack.enter_context(boundary)
            if fail_capture:
                stack.enter_context(patch.object(launch_protocol, "capture_process_identity",
                                                 side_effect=OSError("identity capture failed")))
            scheduler._admit_available()
        return board, client, task, claim, api, process, scheduler

    def test_failure_before_the_resume_stops_the_suspended_root_and_settles_creator_reaped(self):
        for fail_capture, fail_marker in ((True, False), (False, True)):
            with self.subTest(fail_capture=fail_capture, fail_marker=fail_marker):
                board, client, task, claim, api, process, scheduler = self.launched_windows_root(
                    fail_capture=fail_capture, fail_marker=fail_marker)
                attempt_id = claim["attempt"]["attemptId"]
                self.assertEqual(scheduler._handles, {})
                # The suspended root is stopped on the service-held Job handle
                # and observed empty; no resume or duplication ever happens.
                self.assertEqual(api.events, ["create", ("assign", 77, 42), ("terminate", 77),
                                              ("active", 77), ("active", 77), ("close", 77)])
                self.assertEqual(process.returncode, 1)
                self.assertFalse((board.directory / "attempts" / task["runId"] / attempt_id
                                  / "spawn.marker").exists())
                view = client.get(runId=task["runId"])
                self.assertEqual(view["attemptState"], "finished")
                self.assertTrue(view["shutdownConfirmed"])
                self.assertEqual(board.store.capacity_report()["totalActive"], 0)

    def test_duplicate_failure_after_the_resume_stops_the_root_and_settles_creator_reaped(self):
        board, client, task, claim, api, process, scheduler = self.launched_windows_root(fail_at="duplicate")
        attempt_id = claim["attempt"]["attemptId"]
        self.assertEqual(scheduler._handles, {})
        self.assertEqual(api.events, ["create", ("assign", 77, 42), ("resume", 42),
                                      ("duplicate", 321, 77), ("terminate", 77),
                                      ("active", 77), ("active", 77), ("close", 77)])
        self.assertEqual(process.returncode, 1)
        self.assertTrue((board.directory / "attempts" / task["runId"] / attempt_id
                         / "spawn.marker").is_file())
        view = client.get(runId=task["runId"])
        self.assertEqual(view["attemptState"], "finished")
        self.assertTrue(view["shutdownConfirmed"])
        self.assertEqual(board.store.capacity_report()["totalActive"], 0)

    def test_unconfirmed_root_stop_keeps_the_attempt_unknown_and_occupied(self):
        board, client, task, claim, api, _process, scheduler = self.launched_windows_root(
            fail_at="active", fail_marker=True)
        self.assertEqual(scheduler._handles, {})
        self.assertIn(("terminate", 77), api.events)  # A kill request is not stop evidence.
        view = client.get(runId=task["runId"])
        self.assertNotEqual(view["attemptState"], "finished")
        self.assertFalse(view["shutdownConfirmed"])
        self.assertEqual(board.store.capacity_report()["totalActive"], 1)
        self.assertTrue(any(issue["stage"] == "launch" for issue in view["selectedAttempt"]["issues"]))


class SessionRecordGroupingTests(WorkflowTestCase):
    def test_admission_has_no_record_path_for_command_or_ungrouped_execution(self):
        board = self.board()
        for adapter in ('command', 'dsh'):
            claim = {'task': {'taskId': 'ungrouped', 'spec': {'adapter': adapter}},
                     'attempt': {'attemptId': 'attempt'}}
            with self.subTest(adapter=adapter), \
                    patch.object(board.store, 'admit_execution', return_value={'claim': claim}), \
                    patch.object(board.store.workflow, 'prepare_continuation_workspace'):
                admitted = board.service.admit_execution('fixture-nonce-00000000000000000')['claim']
            self.assertIsNone(admitted['sessionRecordPath'])

    def test_launch_path_uses_the_consoles_group_for_macro_standalone_and_helper(self):
        from hey_my_buddy.blackboard.tasks.session_content import identity as console_identity
        from hey_my_buddy.private_dirs import session_record_path

        board = self.board()
        for named in (True, False):
            with self.subTest(named=named):
                root = self.submit(board, request_id=f"session-group-{named}", kind="worktree",
                                   cwd=str(self.workdir(f"session-group-{named}")),
                                   **({"macroTask": {"title": "Session group"}} if named else {}))
                claim = self.claim(board, run_id=root["runId"],
                                   claim_request_id=f"session-group-claim-{named}")["claim"]
                group = console_identity(board.store, claim["task"]["taskId"],
                                         claim["attempt"]["attemptId"])["group_id"]
                self.assertEqual(group, root.get("macroTaskId") or f"run:{root['runId']}")
                self.assertEqual(claim["sessionRecordPath"], str(session_record_path(
                    board.store.directory, group, claim["attempt"]["attemptId"])))

                self.finish_turn(board, {"claim": claim}, disposition="assistance")
                view = board.call("workflow_get", {"runId": root["runId"]})
                decision = self.decide(board, view, view["activeRequest"]["requestId"],
                                       command_id=f"session-helper-{named}", helpers=[{
                                           "requestId": f"session-child-{named}", "task": "assist",
                                           "cwd": str(self.workdir(f"session-child-{named}")),
                                           "executionWorkspace": {"kind": "existing", "access": "write"}}])
                child_id = decision["children"][0]["taskId"]
                helper = self.claim(board, run_id=child_id,
                                    claim_request_id=f"session-child-claim-{named}")["claim"]
                self.assertEqual(helper["sessionRecordPath"], str(session_record_path(
                    board.store.directory, group, helper["attempt"]["attemptId"])))
                self.finish_turn(board, {"claim": helper}, disposition="assistance")
