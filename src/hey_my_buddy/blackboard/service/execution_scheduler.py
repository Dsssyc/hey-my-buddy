"""Service-owned admission and observation of one process per execution."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import secrets
import signal
import threading
import time

from ...errors import BoardError
from ...protocol.execution import launch_execution, read_execution_endpoint, read_execution_receipt, cleanup_execution_endpoint
from ...protocol.process_identity import inspect_process_identity, boot_id


class ExecutionScheduler:
    """Admit within store capacity and observe every process through its endpoint."""

    TICK_SECONDS = 2.0
    STOP_GRACE_SECONDS = 8.0

    def __init__(self, service):
        self.service = service
        self.store = service.store
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._handles: dict[str, tuple[object, dict | None]] = {}
        self._threads: set[threading.Thread] = set()
        self._thread = threading.Thread(target=self._run, name="execution-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self.wake()
        if self._thread.is_alive():
            self._thread.join()
        with self._lock:
            threads = tuple(self._threads)
        deadline = time.monotonic() + 7
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))

    def verify_identity(self, attempt_id: str, identity: dict) -> bool:
        with self._lock:
            held = self._handles.get(attempt_id)
            if held is None:
                return True  # A restarted service authenticates the nonce and leaves ownership uncertain.
            handle, captured = held
            if captured is None:
                # The child's own report can close a failed parent-side capture.
                # Compare it with the creation facts retained in our owned handle.
                expected = {"bootId": boot_id(), "pid": handle.pid,
                            "startedAt": handle.process_started_at,
                            "processGroupId": handle.pgid if handle.job is None else None,
                            "jobName": handle.job.name if handle.job is not None else None}
                if identity != expected:
                    return False
                handle.process_identity = identity
                self._handles[attempt_id] = (handle, identity)
                return True
            return captured == identity

    def _run(self) -> None:
        # A restart may settle absent executions here. Live reconnection is M3.
        self.store.recover_executions()
        while not self._stopping.is_set():
            if not self.service.control.get("stopping") and not self.service.control.get("restart_requested"):
                self._admit_available()
            self._wake.wait(self.TICK_SECONDS)
            self._wake.clear()

    def _admit_available(self) -> None:
        while not self._stopping.is_set():
            if self.service.control.get("stopping") or self.service.control.get("restart_requested"):
                return
            nonce = secrets.token_urlsafe(32)
            response = self.service.admit_execution(nonce)
            claim = response.get("claim")
            if claim is None:
                return
            attempt = claim["attempt"]
            attempt_id = attempt["attemptId"]
            if self.service.control.get("stopping") or self.service.control.get("restart_requested"):
                self.store.release_execution(attempt_id, spawn_intent_written=False,
                                             reason="service stopped before execution launch")
                return
            failure = None
            with self._lock:
                try:
                    handle = launch_execution(claim, self.store.directory, nonce)
                except Exception as error:
                    failure = error
                    handle = getattr(error, "execution_handle", None)
                if handle is not None:
                    identity = getattr(handle, "process_identity", None)
                    self._handles[attempt_id] = (handle, identity)
            if handle is None:
                if failure is not None and not getattr(failure, "spawn_created", True):
                    self.store.release_execution(attempt_id, spawn_intent_written=False,
                                                 reason="execution launch failed before process creation")
                else:
                    self.store.recover_executions(attempt_id)
                return
            target = self._observe if identity is not None else self._settle_unidentified
            monitor = threading.Thread(target=target, args=(claim, handle, identity) if identity is not None
                                       else (claim, handle),
                                       name=f"execution-{attempt_id}", daemon=True)
            with self._lock:
                self._threads.add(monitor)
            monitor.start()

    def _settle_unidentified(self, claim: dict, handle) -> None:
        """Wait for a verified self-report; absent identity never releases a slot."""
        attempt_id = claim["attempt"]["attemptId"]
        try:
            while not self._stopping.is_set():
                with self._lock:
                    identity = self._handles[attempt_id][1]
                if identity is not None:
                    self._observe(claim, handle, identity)
                    return
                # A held creator can stop its own still-live child, but cannot
                # convert missing identity into durable shutdown evidence.
                view = self.store.task_get({"runId": claim["task"]["taskId"]})["task"]
                if view.get("cancelRequested") and handle.process.poll() is None:
                    handle.terminate(grace_seconds=0)
                self._stopping.wait(0.1)
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())
            self.wake()

    def _observe(self, claim: dict, handle, identity: dict) -> None:
        attempt_id = claim["attempt"]["attemptId"]
        task_id = claim["task"]["taskId"]
        client = None
        stop_sent: tuple | None = None
        forced_at: float | None = None
        delay = 0.1
        try:
            while not self._stopping.is_set():
                long_call_ended = False
                if client is None:
                    try:
                        client = read_execution_endpoint(handle.execution_directory, handle.role_run_identity,
                                                         state_dir=self.store.directory,
                                                         process_identity=identity)
                    except BoardError:
                        client = None
                    if client is not None:
                        self.store.live_registry.bind(attempt_id, client)
                view = self.store.task_get({"runId": task_id})["task"]
                if client is not None:
                    try:
                        stop_sent, forced_at = self._apply_stop(view, claim, handle, client, stop_sent, forced_at)
                        if client.wait():
                            delay = 0.1
                        else:
                            self.store.live_registry.unbind(attempt_id, client)
                            client = None
                            long_call_ended = True
                    except BoardError:
                        self.store.live_registry.unbind(attempt_id, client)
                        client = None
                        long_call_ended = True
                elif forced_at is None:
                    reason = self._fallback_stop_due(view, handle)
                    if reason is not None:
                        self.store.record_execution_deadline(attempt_id, reason)
                        forced_at = time.monotonic()
                # Reap our child, but the long call and full identity determine loss.
                exited = handle.process.poll() is not None
                if long_call_ended or exited:
                    observation = inspect_process_identity(identity)
                    if observation in ("identity-absent", "boot-changed"):
                        cleanup_execution_endpoint(handle.execution_directory, identity, state_dir=self.store.directory)
                        self.store.recover_executions(attempt_id, process_identity=identity)
                        return
                    if exited:
                        self.store.recover_executions(attempt_id)
                if forced_at is not None and time.monotonic() - forced_at >= self.STOP_GRACE_SECONDS:
                    self._force_if_required(handle, identity)
                    forced_at = time.monotonic()
                if client is None:
                    self._stopping.wait(delay)
                    delay = min(delay * 2, self.TICK_SECONDS)
        finally:
            if client is not None:
                self.store.live_registry.unbind(attempt_id, client)
            with self._lock:
                self._handles.pop(attempt_id, None)
                self._threads.discard(threading.current_thread())
            self.wake()

    @staticmethod
    def _deadline(value: str | None) -> float | None:
        if value is None:
            return None
        remaining = (datetime.fromisoformat(value.replace("Z", "+00:00")) -
                     datetime.now(timezone.utc)).total_seconds()
        return time.monotonic() + max(0, remaining)

    def _apply_stop(self, view: dict, claim: dict, handle, client, sent: tuple | None,
                    forced_at: float | None) -> tuple[tuple | None, float | None]:
        initiator = view.get("stopInitiator") if view.get("cancelRequested") else None
        deadline = self._deadline(view.get("graceDeadline")) if initiator else None
        if self._budget_expired(handle):
            initiator, deadline = "deadline", None
        if initiator is None:
            return sent, forced_at
        force = deadline is None or time.monotonic() >= deadline
        command = (initiator, view.get("graceDeadline"), force)
        if sent != command:
            if force:
                reason = self._forced_reason(initiator, view.get("graceDeadline"))
                self.store.record_execution_deadline(claim["attempt"]["attemptId"], reason)
            client.stop(initiator=initiator, grace_deadline=None if force else deadline, force=force)
            sent = command
            if force:
                forced_at = time.monotonic()
        return sent, forced_at

    @staticmethod
    def _budget_expired(handle) -> bool:
        path = handle.budget_path
        if not path.exists():
            return False
        budget = json.loads(path.read_text())
        if budget["binding"] != handle.execution_binding:
            raise BoardError("CONFLICT", "Execution budget belongs to another attempt")
        return time.monotonic() >= budget["deadline"]

    def _fallback_stop_due(self, view: dict, handle) -> str | None:
        if self._budget_expired(handle):
            return "deadline"
        if not view.get("cancelRequested"):
            return None
        deadline = self._deadline(view.get("graceDeadline"))
        if deadline is not None and time.monotonic() < deadline:
            return None
        return self._forced_reason(view.get("stopInitiator"), view.get("graceDeadline"))

    @staticmethod
    def _forced_reason(initiator: str | None, grace_deadline: str | None) -> str:
        if initiator == "deadline":
            return "deadline"
        if grace_deadline is not None:
            return "grace-expired"
        if initiator in ("console", "service"):
            return f"{initiator}-stop"
        return "forced-stop"

    @staticmethod
    def _force_if_required(handle, identity: dict) -> None:
        try:
            receipt = read_execution_receipt(handle.execution_directory)
        except (BoardError, OSError):
            receipt = None
        if receipt is not None and receipt["report"]["shutdownConfirmed"] is True:
            return
        if inspect_process_identity(identity) != "alive":
            return
        handle.signal_group(signal.SIGKILL if os.name != "nt" else -1)
