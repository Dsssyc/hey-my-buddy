"""R3 recovery regressions: restart reattachment, owner fencing and termination truth.

The confirmed defect this file guards: after ``reconcile_startup`` marked the original
attempt uncertain, a live same-worker renewal kept succeeding without restoring the
attempt, so the task stayed uncertain with a stale waiting reason. Recovery must be
explicit, instance-checked and only available to the process that still owns the child
handle; a completion receipt is replayed instead of resuming execution, and a
different instance or a lost handle never adopts the attempt.

Every test uses a private state directory and private child processes; inherited
runtime, worker and credential variables are removed from every test subprocess.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from support import BoardTestCase, run_claim_in_process, wait_for, stop_private_workers

from hey_my_buddy.buddy.harnesses.registry import ExecutionContext, adapter as get_adapter
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.blackboard.store.db import TERMINATION_REASONS
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.store.store import UNCERTAIN_QUEUE_REASON
from hey_my_buddy.private_dirs import attempt_root
from hey_my_buddy.buddy.runtime.worker import (
    TERMINATION_COMPLETED,
    TERMINATION_DEADLINE,
    TERMINATION_HARNESS_ERROR,
    TERMINATION_INTERRUPTED,
    TERMINATION_TRANSPORT_ERROR,
    TERMINATION_UNKNOWN_STOP,
    TERMINATION_WRAP_UP,
    TERMINATION_GRACE_EXPIRED,
    TERMINATION_FORCED_STOP,
    TERMINATION_CONSOLE_STOP,
    TERMINATION_SERVICE_STOP,
    Worker,
    _Renewal,
    classify_termination,
)
from hey_my_buddy.protocol.process_identity import (
    _creation_time, inspect_process_identity, native_process_journal_path,
    read_native_process_journal, stop_orphan_process,
)

SOURCE_ROOT = Path(__file__).resolve().parents[4] / "src"
TEST_ROOT = Path(__file__).resolve().parent


def silent(_message: str) -> None:
    """A private worker log sink; the test asserts state, not log prose."""


def private_environment(directory: Path, **extra: str) -> dict:
    """Explicit overrides for support's private child environment, never a copy of HOME."""
    return {
        "HMB_STATE_DIR": str(directory),
        "HMB_RUNTIME_ROOT": str(directory / "runtime-root"),
        "HMB_DEV_SOURCE": "1",
        "VIRTUAL_ENV": "",
        "PYTHONPATH": os.pathsep.join([str(SOURCE_ROOT), str(TEST_ROOT.parent.parent), str(TEST_ROOT)]),
        **extra,
    }


class _LiveAttempt:
    """One claimed attempt with a real owned command child, as the Worker creates it."""

    def __init__(self, test, board, worker, client, task, claim, nonce, directory, implementation, handle):
        self.test = test
        self.board = board
        self.worker = worker
        self.client = client
        self.task = task
        self.claim = claim
        self.nonce = nonce
        self.directory = directory
        self.implementation = implementation
        self.handle = handle

    @property
    def attempt(self) -> dict:
        return self.claim["attempt"]

    def renewal(self, *, worker=None) -> _Renewal:
        return _Renewal(worker or self.worker, self.claim, self.handle, self.implementation)


class RecoveryBase(BoardTestCase):
    def claim_attempt(
        self,
        board,
        *,
        worker_id: str = "w-recover",
        instance: str = "instance-recover",
        request_id: str = "recover-1",
        argv=("/bin/true",),
    ):
        client = board.client()
        task = client.submit(
            requestId=request_id,
            task="recoverable work",
            cwd=str(self.workdir()),
            adapter="command",
            argv=list(argv),
            expectedSeconds=120,
        )["task"]
        client.register_worker(worker_id, adapter="command", capabilities=["command"])
        nonce = "n" * 32
        claim = client.claim(worker_id, f"claim-{request_id}", nonce, worker_instance=instance)["claim"]
        return client, task, claim, nonce

    def live_attempt(
        self,
        *,
        worker_id: str = "w-live",
        lease_seconds: int = 15,
        argv=("/bin/sleep", "60"),
        expected_seconds: int = 120,
        request_id: str = "live-1",
    ) -> _LiveAttempt:
        """Claim an attempt and start the real command child exactly like the Worker does."""
        board = self.board(lease_seconds=lease_seconds)
        client = board.client()
        worker = Worker(worker_id, self.directory, client=client, lease_seconds=lease_seconds, log=silent)
        worker.register()
        task = client.submit(
            requestId=request_id,
            task="live work",
            cwd=str(self.workdir()),
            adapter="command",
            argv=list(argv),
            expectedSeconds=expected_seconds,
        )["task"]
        nonce = "l" * 32
        claim = client.claim(worker_id, f"claim-{request_id}", nonce, worker_instance=worker.instance_id)["claim"]
        attempt = claim["attempt"]
        worker.spool.ensure()
        worker.spool.write_startup(
            {
                "workerId": worker_id,
                "instanceId": worker.instance_id,
                "nonce": nonce,
                "claimRequestId": f"claim-{request_id}",
                "attemptId": attempt["attemptId"],
                "taskId": task["taskId"],
                "generation": attempt["generation"],
                "createdAt": "2026-01-01T00:00:00.000Z",
            }
        )
        directory = worker.attempt_directory(task["taskId"], attempt["attemptId"])
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context = ExecutionContext(
            task_id=task["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=claim["task"]["spec"],
            directory=directory,
            runtime={},
            environment=self.child_environment(private_environment(self.directory)),
        )
        implementation = get_adapter("command")
        implementation.prepare(context)
        handle = implementation.start(context)
        self.children = getattr(self, "children", [])
        self.children.append(handle.process)
        self.addCleanup(lambda: (implementation.cancel(handle), handle.wait(15)))
        return _LiveAttempt(self, board, worker, client, task, claim, nonce, directory, implementation, handle)


class RenewalAndRefusal(RecoveryBase):
    """A renewal may report uncertainty; only the owning instance may clear it."""

    def test_renewal_reports_uncertainty_and_the_owning_instance_restores_it(self):
        board = self.board()
        client, task, claim, nonce = self.claim_attempt(board)
        attempt = claim["attempt"]
        summary = board.store.reconcile_startup()
        self.assertEqual(summary["uncertain"], 1)
        self.assertEqual(summary["retained"], 1)

        renewed = client.renew("w-recover", attempt["attemptId"], attempt["generation"], nonce, phase="executing")
        self.assertTrue(renewed["uncertain"])
        self.assertTrue(renewed["reconciliationRequired"])
        self.assertEqual(
            renewed["attempt"]["executionState"],
            "uncertain",
            "a phase-carrying renewal may not clear authoritative uncertainty",
        )
        self.assertEqual(renewed["task"]["queueReason"], UNCERTAIN_QUEUE_REASON)
        self.assertEqual([entry["state"] for entry in board.store.resource_claims()], ["retained"])

        client.progress("w-recover", attempt["attemptId"], attempt["generation"], nonce,
                        "native work is still active", phase="executing")
        observed = client.get(runId=task["runId"])
        self.assertEqual(observed["attemptState"], "uncertain", "progress cannot bypass instance-bound reconciliation")
        self.assertEqual([entry["state"] for entry in board.store.resource_claims()], ["retained"])

        with self.assertRaises(BoardError) as caught:
            client.reconcile(
                "w-recover", attempt["attemptId"], attempt["generation"], nonce, worker_instance="another-instance"
            )
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        still = client.get(runId=task["runId"])
        self.assertEqual(still["attemptState"], "uncertain")
        self.assertEqual(still["queueReason"], UNCERTAIN_QUEUE_REASON)
        self.assertEqual([entry["state"] for entry in board.store.resource_claims()], ["retained"])

        recovered = client.reconcile(
            "w-recover", attempt["attemptId"], attempt["generation"], nonce, worker_instance="instance-recover"
        )
        self.assertTrue(recovered["restored"])
        self.assertEqual(recovered["attempt"]["executionState"], "executing")
        self.assertEqual(recovered["attempt"]["ownership"], "owned")
        self.assertIsNone(recovered["queueReason"])
        view = client.get(runId=task["runId"])
        self.assertEqual(view["selectedAttemptId"], attempt["attemptId"])
        self.assertEqual(view["attemptGeneration"], 1)
        self.assertEqual(view["status"], "running")
        self.assertIsNone(view["queueReason"])
        self.assertEqual([entry["state"] for entry in board.store.resource_claims()], ["held"])
        reconciled = [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.reconciled"]
        self.assertEqual(len(reconciled), 1)
        self.assertTrue(reconciled[0]["payload"]["restored"])
        self.assertEqual(reconciled[0]["payload"]["previousState"], "uncertain")
        self.assertEqual(len(board.store.active_work()["attempts"]), 1, "no replacement attempt was created")

    def test_a_new_worker_instance_and_a_lost_handle_cannot_adopt_the_live_attempt(self):
        live = self.live_attempt()
        live.board.store.reconcile_startup()
        self.assertEqual(live.client.get(runId=live.task["runId"])["attemptState"], "uncertain")

        # Same worker id, different process instance: it has no handle and no identity
        # proof, so it may not reconcile or release what the first process owns.
        stranger = Worker("w-live", self.directory, client=live.client, lease_seconds=15, log=silent)
        stranger_renewal = live.renewal(worker=stranger)
        self.assertNotEqual(stranger.instance_id, live.worker.instance_id)
        self.assertFalse(stranger_renewal._renew())
        self.assertTrue(stranger_renewal._done.is_set(), "a refused reconciliation stops touching the attempt")
        view = live.client.get(runId=live.task["runId"])
        self.assertEqual(view["attemptState"], "uncertain")
        self.assertEqual(view["queueReason"], UNCERTAIN_QUEUE_REASON)
        self.assertEqual([entry["state"] for entry in live.board.store.resource_claims()], ["retained"])

        # The owning instance is still alive, but with no handle it has no evidence
        # either: the attempt stays uncertain instead of being adopted by PID.
        owner_renewal = live.renewal()
        owner_renewal.handle = None
        self.assertTrue(owner_renewal._renew())
        view = live.client.get(runId=live.task["runId"])
        self.assertEqual(view["attemptState"], "uncertain")
        self.assertEqual(view["selectedAttemptId"], live.attempt["attemptId"])
        self.assertEqual(
            [event for event in live.client.read_events(after=0)["events"] if event["kind"] == "attempt.reconciled"],
            [],
        )

    def test_a_live_handle_reattaches_the_same_attempt_after_the_restart(self):
        live = self.live_attempt()
        live.board.store.reconcile_startup()
        before = live.client.get(runId=live.task["runId"])
        self.assertEqual(before["attemptState"], "uncertain")
        self.assertEqual(before["queueReason"], UNCERTAIN_QUEUE_REASON)

        self.assertTrue(live.renewal()._renew())
        after = live.client.get(runId=live.task["runId"])
        self.assertEqual(after["selectedAttemptId"], before["selectedAttemptId"])
        self.assertEqual(after["attemptGeneration"], before["attemptGeneration"])
        self.assertEqual(after["workerId"], before["workerId"])
        self.assertEqual(after["attemptState"], "executing")
        self.assertIsNone(after["queueReason"])
        self.assertEqual([entry["state"] for entry in live.board.store.resource_claims()], ["held"])
        self.assertTrue(live.handle.group_alive(), "the owned child was never replaced or restarted")

class TerminationTruth(RecoveryBase):
    def test_expected_duration_does_not_stop_command_and_cancel_still_works(self):
        board = self.board(lease_seconds=15)
        client = board.client()
        finished = client.submit(
            requestId="unlimited-complete", task="finish after the estimate", cwd=str(self.workdir()),
            adapter="command", argv=[sys.executable, "-c", "import time; time.sleep(1.4)"],
            expectedSeconds=1,
        )["task"]
        worker = Worker("w-unlimited", self.directory, client=client, lease_seconds=15, log=silent)
        worker.register()
        with run_claim_in_process():
            worker.run_once()
        result = client.result(runId=finished["runId"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultMeta"]["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(client.get(runId=finished["runId"])["expectedSeconds"], 1)

        pending = client.submit(
            requestId="unlimited-cancel", task="keep running until Host cancellation", cwd=str(self.workdir("cancel")),
            adapter="command", argv=["/bin/sh", "-c", "sleep 60"], expectedSeconds=1,
        )["task"]
        outcome: dict = {}
        with run_claim_in_process() as active:
            thread = threading.Thread(target=lambda: outcome.update(result=worker.run_once()), name="unlimited-run")
            thread.start()
            self.assertTrue(wait_for(lambda: active and client.get(runId=pending["runId"])["status"] == "running", 20))
            client.cancel(runId=pending["runId"], reason="operator request")
            active[-1].request_stop("unknown")
            thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "an ordinary execution must remain cancellable")
        cancelled = client.result(runId=pending["runId"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["resultMeta"]["terminationReason"], TERMINATION_UNKNOWN_STOP)
        self.assertEqual(cancelled["stopInitiator"], "unknown")
        self.assertNotIn("stopInitiator", cancelled["resultMeta"])

    def test_the_classification_keeps_every_real_reason_distinct(self):
        completed = AdapterOutcome(status="ok", result={"status": "ok"}, shutdown_confirmed=True)
        failed = AdapterOutcome(status="failed", result={"status": "nonzero"}, error="boom", shutdown_confirmed=True)
        transport = AdapterOutcome(status="failed", result={"status": "error", "failureKind": "transport"}, error="socket")
        self.assertEqual(classify_termination(completed, timed_out=True, cancel_requested=True), TERMINATION_COMPLETED)
        self.assertEqual(classify_termination(failed, timed_out=True, cancel_requested=True), TERMINATION_DEADLINE)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=True), TERMINATION_UNKNOWN_STOP)
        summary = AdapterOutcome(status="cancelled", result={"wrapUpDelivered": True, "graceExpired": False},
                                 shutdown_confirmed=True)
        self.assertEqual(classify_termination(summary, timed_out=False, cancel_requested=True,
                                             stop_initiator="host"), TERMINATION_WRAP_UP)
        self.assertEqual(classify_termination(summary, timed_out=False, cancel_requested=True,
                                             stop_initiator="host", grace_expired=True), TERMINATION_WRAP_UP)
        expired = AdapterOutcome(status="cancelled", result={"wrapUpDelivered": False, "graceExpired": True},
                                 shutdown_confirmed=True)
        self.assertEqual(classify_termination(expired, timed_out=False, cancel_requested=True,
                                             stop_initiator="host"), TERMINATION_GRACE_EXPIRED)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=True,
                                             stop_initiator="host"), TERMINATION_FORCED_STOP)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=True,
                                             stop_initiator="console"), TERMINATION_CONSOLE_STOP)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=True,
                                             stop_initiator="service"), TERMINATION_SERVICE_STOP)
        unconfirmed = AdapterOutcome(status="cancelled", result={}, shutdown_confirmed=False)
        for initiator in ("host", "console", "service", "unknown"):
            with self.subTest(initiator=initiator):
                self.assertIsNone(classify_termination(unconfirmed, timed_out=False, cancel_requested=True,
                                                       stop_initiator=initiator, grace_expired=initiator == "host"))
        self.assertEqual(classify_termination(transport, timed_out=False, cancel_requested=False), TERMINATION_TRANSPORT_ERROR)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=False), TERMINATION_HARNESS_ERROR)
        self.assertEqual(
            {TERMINATION_COMPLETED, TERMINATION_UNKNOWN_STOP, TERMINATION_DEADLINE, TERMINATION_HARNESS_ERROR,
             TERMINATION_TRANSPORT_ERROR, TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED,
             TERMINATION_FORCED_STOP, TERMINATION_CONSOLE_STOP, TERMINATION_SERVICE_STOP,
             TERMINATION_INTERRUPTED},
            set(TERMINATION_REASONS),
        )

    def test_an_unattributed_cancel_is_recorded_as_unknown_stop(self):
        board = self.board(lease_seconds=15)
        client = board.client()
        task = client.submit(
            requestId="cancel-1",
            task="long work",
            cwd=str(self.workdir()),
            adapter="command",
            argv=["/bin/sh", "-c", "sleep 60"],
            expectedSeconds=120,
        )["task"]
        worker = Worker("w-cancel", self.directory, client=client, lease_seconds=15, log=silent)
        worker.register()
        outcome: dict = {}
        with run_claim_in_process() as active:
            thread = threading.Thread(target=lambda: outcome.update(result=worker.run_once()), name="run-once")
            thread.start()
            self.assertTrue(wait_for(lambda: active and client.get(runId=task["runId"])["status"] == "running", 20))
            client.cancel(runId=task["runId"], reason="operator request")
            active[-1].request_stop("unknown")
            thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "the worker must observe the durable cancel intent and stop its child")
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["resultMeta"]["terminationReason"], TERMINATION_UNKNOWN_STOP)
        self.assertEqual(result["stopInitiator"], "unknown")
        self.assertNotIn("stopInitiator", result["resultMeta"])
class RealDaemonRestart(RecoveryBase):
    """The same regression over the real daemon and a real detached supervisor."""

    @unittest.skipUnless(os.name == "posix", "real Worker crash injection uses POSIX processes")
    def test_killed_worker_with_surviving_execution_stops_it_without_reexecution(self):
        self._crash_worker_with_child(kill_child=False)

    @unittest.skipUnless(os.name == "posix", "real Worker crash injection uses POSIX processes")
    def test_killed_worker_and_child_are_recorded_as_identity_absent(self):
        self._crash_worker_with_child(kill_child=True)

    def _crash_worker_with_child(self, *, kill_child: bool) -> None:
        import psutil

        suffix = "both" if kill_child else "worker"
        environment = private_environment(self.directory, HMB_LEASE_SECONDS="2", HMB_MAX_CONCURRENT="1")
        child_environment = self.child_environment(environment)
        supervisor = None
        replacement = None
        identity = None
        try:
            with self.daemon(env=environment):
                # Only the replacement must find its orphan: an unrelated idle
                # pool slot would conceal a WORKER_BUSY retry loop here.
                stop_private_workers(self.directory)
                supervisor = subprocess.Popen(
                    [sys.executable, "-m", "hey_my_buddy.buddy.runtime.supervisor",
                     "--worker-id", f"crash-{suffix}", "--state-dir", str(self.directory),
                     "--capabilities", f"crash-{suffix}", "--lease-seconds", "2"],
                    env=child_environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
                self.children = getattr(self, "children", [])
                self.children.append(supervisor)
                starts = self.directory / f"crash-{suffix}-starts.txt"
                code, task = self.cli("execution-submit", json.dumps({
                    "requestId": f"crash-{suffix}-1", "task": "recover a crashed Worker",
                    "cwd": str(self.workdir()), "adapter": "command",
                    "argv": ["/bin/sh", "-c", f"echo start >> {starts}; sleep 60"],
                    "expectedSeconds": 120, "requiredCapabilities": [f"crash-{suffix}"],
                }), env=environment)
                self.assertEqual(code, 0, task)
                run_id = task["runId"]
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["status"] == "running", 30))
                attempt_id = self._status(run_id, environment)["selectedAttemptId"]
                marker = self.directory / "attempts" / run_id / attempt_id / "spawn.marker"
                self.assertTrue(wait_for(marker.exists, 20))
                identity = json.loads(marker.read_text())["processIdentity"]
                self.assertEqual(inspect_process_identity(identity), "alive")
                self.assertTrue(wait_for(starts.exists, 5))
                worker = psutil.Process(identity["worker"]["pid"])
                self.assertEqual(_creation_time(worker.pid), identity["worker"]["startedAt"])
                self.assertEqual(worker.pid, supervisor.pid)
                if kill_child:
                    worker.suspend()
                    child = psutil.Process(identity["pid"])
                    self.assertEqual(_creation_time(child.pid), identity["startedAt"])
                    descendants = child.children(recursive=True)
                    for process in descendants:
                        process.kill()
                    child.kill()
                worker.kill()
                supervisor.wait(timeout=10)
                replacement = subprocess.Popen(
                    [sys.executable, "-m", "hey_my_buddy.buddy.runtime.supervisor",
                     "--worker-id", f"crash-{suffix}", "--state-dir", str(self.directory),
                     "--capabilities", f"crash-{suffix}", "--lease-seconds", "2"],
                    env=child_environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
                self.children.append(replacement)
                # A lease sweep first exposes uncertainty; the replacement then
                # stops/reports, and only the following service sweep settles.
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["attemptState"] == "finished", 70),
                                "lease expiry, orphan observation and the next service sweep must settle the attempt")
                self.assertEqual(starts.read_text().splitlines(), ["start"])
                result = self.cli("result", json.dumps({"runId": run_id}), env=environment)[1]
                self.assertEqual(result["attemptId"], attempt_id)
                if kill_child:
                    self.assertEqual(result["resultMeta"]["terminationReason"], "interrupted")
                    self.assertEqual(result["result"]["interruptionEvidence"]["kind"], "identity-absent")
                else:
                    self.assertEqual(result["resultMeta"]["terminationReason"], "unknown-stop")
                    self.assertTrue(result["resultMeta"]["shutdownConfirmed"])
                    self.assertEqual(inspect_process_identity(identity), "identity-absent")
                    journal_root = attempt_root(self.directory, "command", run_id, attempt_id)
                    journal = read_native_process_journal(
                        native_process_journal_path(journal_root, identity),
                        {"taskId": run_id, "attemptId": attempt_id, "generation": 1})
                    self.assertIsNotNone(journal)
                    self.assertEqual(journal["pending"], 0)
                    self.assertTrue(journal["identities"])
                    self.assertTrue(all(inspect_process_identity(native) == "identity-absent"
                                        for native in journal["identities"]))
                client = BoardClient(self.directory, autostart=False)
                self.assertTrue(wait_for(lambda: any(
                    row["workerId"] == f"crash-{suffix}" and row["pid"] == replacement.pid
                    for row in client.workers()["workers"]), 20))
                self.assertIsNone(replacement.poll(), "the replacement Worker must still be alive")
                worker = next(row for row in client.workers()["workers"] if row["workerId"] == f"crash-{suffix}")
                self.assertEqual(worker["state"], "idle")
                self.assertIsNone(worker["currentAttemptId"])
                self.assertEqual(worker["supervisor"]["state"], "running")
        finally:
            if replacement is not None and replacement.poll() is None:
                replacement.terminate()
                try:
                    replacement.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    replacement.kill()
                    replacement.wait(timeout=10)
            if supervisor is not None and supervisor.poll() is None:
                supervisor.terminate()
                try:
                    supervisor.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    supervisor.kill()
                    supervisor.wait(timeout=10)
            if identity is not None and inspect_process_identity(identity) == "alive":
                stop_orphan_process(identity)

    @unittest.skipUnless(os.name == "posix", "pausing a real Worker uses POSIX signals")
    def test_a_paused_worker_outlives_its_lease_then_reattaches_and_completes(self):
        import psutil

        environment = private_environment(self.directory, HMB_LEASE_SECONDS="2", HMB_MAX_CONCURRENT="1")
        child_environment = self.child_environment(environment)
        supervisor = None
        worker_process = None
        paused = False
        try:
            with self.daemon(env=environment):
                supervisor = subprocess.Popen(
                    [sys.executable, "-m", "hey_my_buddy.buddy.runtime.supervisor",
                     "--worker-id", "sleep-recover", "--state-dir", str(self.directory),
                     "--capabilities", "sleep-recover", "--lease-seconds", "2"],
                    env=child_environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
                self.children = getattr(self, "children", [])
                self.children.append(supervisor)
                starts = self.directory / "sleep-starts.txt"
                code, task = self.cli("execution-submit", json.dumps({
                    "requestId": "sleep-recover-1", "task": "complete after Worker suspension",
                    "cwd": str(self.workdir()), "adapter": "command",
                    "argv": ["/bin/sh", "-c", f"echo start >> {starts}; sleep 50"],
                    "expectedSeconds": 120, "requiredCapabilities": ["sleep-recover"],
                }), env=environment)
                self.assertEqual(code, 0, task)
                run_id = task["runId"]
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["status"] == "running", 30))
                attempt_id = self._status(run_id, environment)["selectedAttemptId"]
                marker = self.directory / "attempts" / run_id / attempt_id / "spawn.marker"
                self.assertTrue(wait_for(marker.exists, 20))
                identity = json.loads(marker.read_text())["processIdentity"]
                worker_identity = identity["worker"]
                worker_process = psutil.Process(worker_identity["pid"])
                self.assertEqual(_creation_time(worker_process.pid), worker_identity["startedAt"])
                self.assertEqual(worker_process.pid, supervisor.pid)
                worker_process.suspend()
                paused = True
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["attemptState"] == "uncertain", 35),
                                "the lease sweep must mark the paused attempt unknown")
                self.assertEqual(self._status(run_id, environment)["status"], "running")
                self.assertEqual(_creation_time(worker_process.pid), worker_identity["startedAt"])
                worker_process.resume()
                paused = False
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["attemptState"] == "executing", 12),
                                "the original Worker must reattach after its pause")
                self.assertTrue(wait_for(lambda: self._status(run_id, environment)["status"] == "completed", 35))
                self.assertEqual(starts.read_text().splitlines(), ["start"])
                result = self.cli("result", json.dumps({"runId": run_id}), env=environment)[1]
                self.assertEqual(result["attemptId"], attempt_id)
                self.assertEqual(result["resultMeta"]["terminationReason"], "completed")
        finally:
            if paused and worker_process is not None and worker_process.is_running():
                worker_process.resume()
            if supervisor is not None and supervisor.poll() is None:
                supervisor.terminate()
                try:
                    supervisor.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    supervisor.kill()
                    supervisor.wait(timeout=10)

    def test_a_real_supervisor_keeps_its_child_and_reattaches_after_a_restart(self):
        environment = private_environment(
            self.directory,
            HMB_LEASE_SECONDS="15",
            HMB_MAX_CONCURRENT="1",
        )
        self.assertFalse("HOME" in environment, "daemon overrides must leave HOME to the private child environment")
        child_environment = self.child_environment(environment)
        self.assertEqual(Path(child_environment["HOME"]).resolve(), self.directory / "home")
        self.assertEqual(child_environment["PYTHONPATH"], environment["PYTHONPATH"])
        supervisor = None
        process_identity = None
        try:
            with self.daemon(env=environment) as first:
                supervisor = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "hey_my_buddy.buddy.runtime.supervisor",
                        "--worker-id",
                        "recover-live",
                        "--state-dir",
                        str(self.directory),
                        "--capabilities",
                        "recover-live",
                        "--lease-seconds",
                        "15",
                    ],
                    env=child_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.children = getattr(self, "children", [])
                self.children.append(supervisor)
                starts = self.directory / "starts.txt"
                code, task = self.cli(
                    "execution-submit",
                    json.dumps(
                        {
                            "requestId": "real-restart-1",
                            "task": "keep the same attempt across a daemon restart",
                            "cwd": str(self.workdir()),
                            "adapter": "command",
                            "argv": ["/bin/sh", "-c", f"echo start >> {starts}; sleep 20"],
                            "expectedSeconds": 120,
                            "requiredCapabilities": ["recover-live"],
                        }
                    ),
                    env=environment,
                )
                self.assertEqual(code, 0, task)
                run_id = task["runId"]
                self.assertTrue(
                    wait_for(lambda: self._status(run_id, environment)["status"] == "running", 30),
                    "the detached supervisor must claim the task",
                )
                attempt_id = self._status(run_id, environment)["selectedAttemptId"]
                marker = self.directory / "attempts" / run_id / attempt_id / "spawn.marker"
                self.assertTrue(wait_for(lambda: marker.exists(), 20), "the worker must record its real spawn")
                process_identity = json.loads(marker.read_text())["processIdentity"]
                self.assertEqual(inspect_process_identity(process_identity), "alive")
                code, restarted = self.cli("restart", env=environment)
                self.assertEqual(code, 0, restarted)
                self.assertTrue(restarted["workersPreserved"])
                first.wait(timeout=25)
            # A fresh daemon marks the in-flight attempt uncertain and preserves the
            # same worker; its renewal must reattach the identical attempt.
            with self.daemon(env=environment):
                def reattached():
                    view = self._status(run_id, environment)
                    if view.get("selectedAttemptId") == attempt_id and view.get("attemptState") == "executing":
                        return view if view.get("queueReason") is None else None
                    return None

                view = wait_for(reattached, 40)
                self.assertIsNotNone(
                    view,
                    "the live same-instance worker must reattach; last="
                    + json.dumps(self._status(run_id, environment)),
                )
                self.assertEqual(view["attemptGeneration"], 1, "no replacement generation may exist")
                self.assertEqual(view["status"], "running")
                self.assertEqual(inspect_process_identity(process_identity), "alive",
                                 "the owned child survived the restart")
                events = self.cli("events", json.dumps({"after": 0, "limit": 200}), env=environment)[1]["events"]
                kinds = [event["kind"] for event in events]
                self.assertIn("attempt.uncertain", kinds)
                reconciled = [event for event in events if event["kind"] == "attempt.reconciled"]
                self.assertTrue(reconciled, "reattachment is recorded as an event")
                self.assertTrue(reconciled[-1]["payload"]["restored"])
                completed = wait_for(lambda: self._status(run_id, environment)["status"] == "completed", 45)
                self.assertTrue(completed, "the same attempt must finish after the restart")
                self.assertEqual(
                    starts.read_text().splitlines(),
                    ["start"],
                    "the restart must never start a second child for the same attempt",
                )
                result = self.cli("result", json.dumps({"runId": run_id}), env=environment)[1]
                self.assertEqual(result["attemptId"], attempt_id)
                self.assertEqual(result["resultMeta"]["terminationReason"], "completed")
        finally:
            if supervisor is not None and supervisor.poll() is None:
                supervisor.terminate()
                try:
                    supervisor.wait(timeout=20)
                except subprocess.TimeoutExpired:  # pragma: no cover - test watchdog
                    supervisor.kill()
                    supervisor.wait(timeout=10)
            if process_identity is not None and inspect_process_identity(process_identity) == "alive":
                stop_orphan_process(process_identity)

    def _status(self, run_id: str, environment: dict) -> dict:
        code, view = self.cli("status", json.dumps({"runId": run_id}), env=environment)
        self.assertEqual(code, 0, view)
        return view

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True
