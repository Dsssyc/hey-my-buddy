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
from pathlib import Path

from support import BoardTestCase, wait_for

from hey_my_buddy.buddy.harnesses.registry import ExecutionContext, adapter as get_adapter
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.blackboard.store.db import TERMINATION_REASONS
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.store.store import UNCERTAIN_QUEUE_REASON
from hey_my_buddy.buddy.runtime.worker import (
    TERMINATION_COMPLETED,
    TERMINATION_DEADLINE,
    TERMINATION_HARNESS_ERROR,
    TERMINATION_TRANSPORT_ERROR,
    TERMINATION_USER_CANCEL,
    Worker,
    _Renewal,
    classify_termination,
)

#: Inherited variables that would otherwise point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_TASK_ID",
    "BUDDY_ATTEMPT_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

SOURCE_ROOT = Path(__file__).resolve().parents[4] / "src"
TEST_ROOT = Path(__file__).resolve().parent


def silent(_message: str) -> None:
    """A private worker log sink; the test asserts state, not log prose."""


def private_environment(directory: Path, **extra: str) -> dict:
    values = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES}
    values.update(
        {
            "BUDDY_STATE_DIR": str(directory),
            "BUDDY_RUNTIME_ROOT": str(directory / "runtime-root"),
            "BUDDY_DEV_SOURCE": "1",
            "VIRTUAL_ENV": "",
            "PYTHONPATH": os.pathsep.join([str(SOURCE_ROOT), str(TEST_ROOT)]),
            **extra,
        }
    )
    return values


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
            timeoutSeconds=120,
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
        timeout_seconds: int = 120,
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
            timeoutSeconds=timeout_seconds,
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
            environment=private_environment(self.directory),
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

    def test_a_durable_receipt_is_replayed_instead_of_resuming_execution(self):
        live = self.live_attempt()
        live.implementation.cancel(live.handle)
        live.handle.wait(15)
        attempt = live.attempt
        live.worker.spool.write(
            attempt["attemptId"],
            {
                "attemptId": attempt["attemptId"],
                "taskId": attempt["taskId"],
                "generation": attempt["generation"],
                "workerId": "w-live",
                "nonce": live.nonce,
                "commandId": f"result:{attempt['attemptId']}",
                "report": {
                    "status": "ok",
                    "result": {"status": "ok"},
                    "exitCode": 0,
                    "signal": None,
                    "error": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": "completed",
                },
                "createdAt": "2026-01-01T00:00:01.000Z",
            },
        )
        live.board.store.reconcile_startup()
        self.assertFalse(live.renewal()._renew(), "delivering the receipt ends this renewal thread")
        result = live.client.result(runId=live.task["runId"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultMeta"]["terminationReason"], "completed")
        self.assertEqual(result["attemptId"], attempt["attemptId"])
        self.assertEqual(
            [event for event in live.client.read_events(after=0)["events"] if event["kind"] == "attempt.reconciled"],
            [],
            "a completed attempt is never reconciled back to executing",
        )


class CompletionDuringOutage(RecoveryBase):
    def test_a_completion_during_the_outage_is_replayed_and_never_re_executed(self):
        board = self.board()
        online = {"value": False}
        real_call = board.call

        def flaky(operation: str, params: dict) -> dict:
            if operation == "worker_result" and not online["value"]:
                raise BoardError("SERVICE_UNAVAILABLE", "simulated daemon outage")
            return real_call(operation, params)

        client = BoardClient(self.directory, call=flaky)
        cwd = self.workdir()
        log = cwd / "executions.txt"
        task = client.submit(
            requestId="outage-1",
            task="count executions",
            cwd=str(cwd),
            adapter="command",
            argv=["/bin/sh", "-c", f"echo run >> {log}"],
            timeoutSeconds=60,
        )["task"]
        worker = Worker("w-outage", self.directory, client=client, retry_seconds=2, log=silent)
        worker.register()
        self.assertEqual(worker.run_once(), "replay")
        self.assertEqual(len(log.read_text().splitlines()), 1)
        attempt_id = worker.spool.read_startup()["attemptId"]

        # The service restarts while the receipt cannot be committed: the attempt is
        # authoritatively uncertain, not failed and not re-runnable.
        board.store.reconcile_startup()
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "uncertain")
        self.assertEqual(client.get(runId=task["runId"])["status"], "running")

        online["value"] = True
        self.assertEqual(worker.run_once(), "replayed")
        self.assertEqual(len(log.read_text().splitlines()), 1, "a durable receipt must never execute twice")
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultMeta"]["terminationReason"], "completed")
        self.assertEqual(result["attemptId"], attempt_id)
        self.assertIsNone(worker.spool.read(attempt_id), "the receipt is cleared only after the commit")


class TerminationTruth(RecoveryBase):
    def test_explicit_zero_deadline_runs_to_completion_and_can_still_be_cancelled(self):
        board = self.board(lease_seconds=15)
        client = board.client()
        finished = client.submit(
            requestId="unlimited-complete", task="finish after a short delay", cwd=str(self.workdir()),
            adapter="command", argv=[sys.executable, "-c", "import time; time.sleep(.6)"],
            timeoutSeconds=0,
        )["task"]
        worker = Worker("w-unlimited", self.directory, client=client, lease_seconds=15, log=silent)
        worker.register()
        worker.run_once()
        result = client.result(runId=finished["runId"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultMeta"]["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(client.get(runId=finished["runId"])["timeoutSeconds"], 0)

        pending = client.submit(
            requestId="unlimited-cancel", task="keep running until Host cancellation", cwd=str(self.workdir("cancel")),
            adapter="command", argv=["/bin/sh", "-c", "sleep 60"], timeoutSeconds=0,
        )["task"]
        outcome: dict = {}
        thread = threading.Thread(target=lambda: outcome.update(result=worker.run_once()), name="unlimited-run")
        thread.start()
        self.assertTrue(wait_for(lambda: client.get(runId=pending["runId"])["status"] == "running", 20))
        client.cancel(runId=pending["runId"], reason="operator request")
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "an unlimited execution must remain cancellable")
        cancelled = client.result(runId=pending["runId"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["resultMeta"]["terminationReason"], TERMINATION_USER_CANCEL)

    def test_the_classification_keeps_every_real_reason_distinct(self):
        completed = AdapterOutcome(status="ok", result={"status": "ok"}, shutdown_confirmed=True)
        failed = AdapterOutcome(status="failed", result={"status": "nonzero"}, error="boom")
        transport = AdapterOutcome(status="failed", result={"status": "error", "failureKind": "transport"}, error="socket")
        self.assertEqual(classify_termination(completed, timed_out=True, cancel_requested=True), TERMINATION_COMPLETED)
        self.assertEqual(classify_termination(failed, timed_out=True, cancel_requested=True), TERMINATION_DEADLINE)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=True), TERMINATION_USER_CANCEL)
        self.assertEqual(classify_termination(transport, timed_out=False, cancel_requested=False), TERMINATION_TRANSPORT_ERROR)
        self.assertEqual(classify_termination(failed, timed_out=False, cancel_requested=False), TERMINATION_HARNESS_ERROR)
        self.assertEqual(
            {TERMINATION_COMPLETED, TERMINATION_USER_CANCEL, TERMINATION_DEADLINE, TERMINATION_HARNESS_ERROR,
             TERMINATION_TRANSPORT_ERROR},
            set(TERMINATION_REASONS),
        )

    def test_a_user_cancel_is_recorded_as_user_cancel(self):
        board = self.board(lease_seconds=15)
        client = board.client()
        task = client.submit(
            requestId="cancel-1",
            task="long work",
            cwd=str(self.workdir()),
            adapter="command",
            argv=["/bin/sh", "-c", "sleep 60"],
            timeoutSeconds=120,
        )["task"]
        worker = Worker("w-cancel", self.directory, client=client, lease_seconds=15, log=silent)
        worker.register()
        outcome: dict = {}
        thread = threading.Thread(target=lambda: outcome.update(result=worker.run_once()), name="run-once")
        thread.start()
        self.assertTrue(wait_for(lambda: client.get(runId=task["runId"])["status"] == "running", 20))
        client.cancel(runId=task["runId"], reason="operator request")
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "the worker must observe the durable cancel intent and stop its child")
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["resultMeta"]["terminationReason"], TERMINATION_USER_CANCEL)

    def test_an_execution_deadline_is_recorded_as_deadline(self):
        board = self.board(lease_seconds=15)
        client = board.client()
        task = client.submit(
            requestId="deadline-1",
            task="run past the deadline",
            cwd=str(self.workdir()),
            adapter="command",
            argv=["/bin/sh", "-c", "sleep 60"],
            timeoutSeconds=10,
        )["task"]
        worker = Worker("w-deadline", self.directory, client=client, lease_seconds=15, log=silent)
        worker.register()
        outcome: dict = {}
        started_at = time.monotonic()

        def run():
            outcome.update(result=worker.run_once())

        thread = threading.Thread(target=run, name="run-once")
        thread.start()
        self.assertTrue(wait_for(lambda: client.get(runId=task["runId"])["status"] == "running", 20))
        thread.join(timeout=45)
        self.assertFalse(thread.is_alive(), "the worker's own deadline stops the owned process group")
        self.assertLess(time.monotonic() - started_at, 25, "the deadline was enforced, not extended")
        result = client.result(runId=task["runId"])
        # The task is cancelled because the owned group was stopped, but the reason
        # names the deadline rather than pretending the user cancelled it.
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["resultMeta"]["terminationReason"], TERMINATION_DEADLINE)


class RealDaemonRestart(RecoveryBase):
    """The same regression over the real daemon and a real detached supervisor."""

    def test_a_real_supervisor_keeps_its_child_and_reattaches_after_a_restart(self):
        environment = private_environment(
            self.directory,
            BUDDY_LEASE_SECONDS="15",
            BUDDY_MAX_CONCURRENT="1",
        )
        supervisor = None
        pgid = None
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
                    env=environment,
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
                            "timeoutSeconds": 120,
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
                pgid = json.loads(marker.read_text())["pgid"]
                self.assertTrue(self._group_alive(pgid))
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
                self.assertTrue(self._group_alive(pgid), "the owned child survived the restart")
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
            if pgid is not None and self._group_alive(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                wait_for(lambda: not self._group_alive(pgid), 20)

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
