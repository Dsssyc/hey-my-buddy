"""Service-owned admission and observation of one process per execution."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import secrets
import signal
import threading
import time

from ...errors import BoardError
from ...protocol.execution import launch_execution, read_execution_endpoint, read_execution_receipt
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
        self._clients: dict[str, object] = {}
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

    def send_pending_stops(self) -> None:
        """Deliver durable stop intents while the service still owns its channels."""
        with self._lock:
            connected = tuple((attempt_id, self._handles[attempt_id][0], client)
                              for attempt_id, client in self._clients.items()
                              if attempt_id in self._handles)
        for attempt_id, handle, client in connected:
            try:
                view = self.store.task_get({"runId": handle.execution_binding["taskId"]})["task"]
                self._apply_stop(view, {"attempt": {"attemptId": attempt_id}}, handle, client, None, None)
            except BoardError as error:
                self._issue(attempt_id, "service-stop", error)
            except Exception as error:
                self._issue(attempt_id, "service-stop", error)

    def _issue(self, attempt_id: str, stage: str, error: Exception) -> None:
        try:
            self.store.record_execution_issue(attempt_id, stage, error)
        except Exception as reporting_error:
            self.store.persistence_error = (f"Execution {attempt_id} {stage}: {type(error).__name__}: {error}; "
                                            f"issue recording failed: {reporting_error}")

    def _scheduler_issue(self, stage: str, error: Exception) -> None:
        try:
            self.store.record_scheduler_issue(stage, error)
        except Exception as reporting_error:
            self.store.persistence_error = (f"Scheduler {stage}: {type(error).__name__}: {error}; "
                                            f"issue recording failed: {reporting_error}")

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
        try:
            self.store.recover_executions()
        except Exception as error:
            self._scheduler_issue("startup-recovery", error)
        while not self._stopping.is_set():
            if not self.service.control.get("stopping") and not self.service.control.get("restart_requested"):
                try:
                    self._admit_available()
                except Exception as error:
                    self._scheduler_issue("admission", error)
            self._wake.wait(self.TICK_SECONDS)
            self._wake.clear()

    def _admit_available(self) -> None:
        while not self._stopping.is_set():
            if self.service.control.get("stopping") or self.service.control.get("restart_requested"):
                return
            nonce = secrets.token_urlsafe(32)
            try:
                response = self.service.admit_execution(nonce)
            except BoardError as error:
                if error.code == "CONFLICT":
                    return
                raise
            claim = response.get("claim")
            if claim is None:
                return
            attempt = claim["attempt"]
            attempt_id = attempt["attemptId"]
            if self.service.control.get("stopping") or self.service.control.get("restart_requested"):
                self.store.release_execution(attempt_id,
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
            if failure is not None:
                self._issue(attempt_id, "launch", failure)
            if handle is None:
                if failure is not None and not getattr(failure, "spawn_created", True):
                    self.store.release_execution(attempt_id,
                                                 reason="execution launch failed before process creation")
                elif failure is not None and getattr(
                        getattr(failure, "owned_popen_evidence", None), "shutdown_confirmed", False):
                    self.store.recover_executions(attempt_id, stopped_evidence={
                        "kind": "creator-reaped", "exitCode": failure.owned_popen_evidence.exit_code})
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
        """Use the creator's reaped child and empty group when no identity was published."""
        attempt_id = claim["attempt"]["attemptId"]
        try:
            while not self._stopping.is_set():
                with self._lock:
                    identity = self._handles[attempt_id][1]
                if identity is not None:
                    self._observe(claim, handle, identity)
                    return
                try:
                    exit_code = handle.process.poll()
                    if exit_code is not None and not handle.group_alive():
                        self.store.recover_executions(attempt_id, stopped_evidence={
                            "kind": "creator-reaped", "exitCode": exit_code})
                        if self.store.execution_finished(attempt_id):
                            with self._lock:
                                self._handles.pop(attempt_id, None)
                            return
                    view = self.store.task_get({"runId": claim["task"]["taskId"]})["task"]
                    intent = self._stop_intent(view, handle)
                    if intent is not None and intent[2]:
                        self.store.record_execution_deadline(attempt_id, intent[3])
                        handle.terminate(grace_seconds=0)
                except Exception as error:
                    self._issue(attempt_id, "unidentified-observation", error)
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
        settled = False
        try:
            while not self._stopping.is_set():
                try:
                    long_call_ended = False
                    if client is None:
                        try:
                            client = read_execution_endpoint(handle.execution_directory, handle.role_run_identity,
                                                             state_dir=self.store.directory,
                                                             process_identity=identity)
                        except Exception as error:
                            self._issue(attempt_id, "endpoint", error)
                            self.store.record_execution_deadline(attempt_id, "forced-stop")
                        if client is not None:
                            self.store.live_registry.bind(attempt_id, client)
                            with self._lock:
                                self._clients[attempt_id] = client
                            stop_sent = None  # Every new channel needs the durable intent.
                    view = self.store.task_get({"runId": task_id})["task"]
                    forced_at = forced_at or self._force_requested_at(view)
                    if client is not None:
                        try:
                            stop_sent, forced_at = self._apply_stop(view, claim, handle, client, stop_sent, forced_at)
                            if client.wait():
                                delay = 0.1
                            else:
                                long_call_ended = True
                        except BoardError as error:
                            if error.code != "LIVE_UNAVAILABLE":
                                raise
                            long_call_ended = True
                        if long_call_ended:
                            self.store.live_registry.unbind(attempt_id, client)
                            with self._lock:
                                self._clients.pop(attempt_id, None)
                            client = None
                    elif forced_at is None:
                        intent = self._stop_intent(view, handle)
                        if intent is not None and intent[2]:
                            record = self.store.record_execution_deadline(attempt_id, intent[3])
                            forced_at = self._timestamp(record["forceRequestedAt"])
                    exited = handle.process.poll() is not None
                    if long_call_ended or exited or client is None:
                        if self._recover_if_gone(attempt_id, identity):
                            settled = True
                            return
                    if forced_at is not None and time.monotonic() - forced_at >= self.STOP_GRACE_SECONDS:
                        self._force_if_required(handle, identity)
                    if client is None:
                        self._stopping.wait(delay)
                        delay = min(delay * 2, self.TICK_SECONDS)
                except Exception as error:
                    self._issue(attempt_id, "observation", error)
                    try:
                        if self._recover_if_gone(attempt_id, identity):
                            settled = True
                            return
                    except Exception as recovery_error:
                        self._issue(attempt_id, "recovery", recovery_error)
                    self._stopping.wait(min(delay, self.TICK_SECONDS))
        finally:
            if client is not None:
                self.store.live_registry.unbind(attempt_id, client)
            with self._lock:
                self._clients.pop(attempt_id, None)
                if settled:
                    self._handles.pop(attempt_id, None)
                self._threads.discard(threading.current_thread())
            self.wake()

    def _recover_if_gone(self, attempt_id: str, identity: dict) -> bool:
        observation = inspect_process_identity(identity)
        if observation not in ("identity-absent", "boot-changed"):
            return False
        self.store.recover_executions(attempt_id, process_identity=identity,
                                      stopped_evidence={"kind": observation})
        return self.store.execution_finished(attempt_id)

    def _force_requested_at(self, view: dict) -> float | None:
        attempt = view.get("selectedAttempt") or {}
        return self._timestamp(attempt.get("forceRequestedAt"))

    @staticmethod
    def _timestamp(value: str | None) -> float | None:
        if value is None:
            return None
        return time.monotonic() + (datetime.fromisoformat(value.replace("Z", "+00:00")) -
                                   datetime.now(timezone.utc)).total_seconds()

    @staticmethod
    def _deadline(value: str | None) -> float | None:
        if value is None:
            return None
        remaining = (datetime.fromisoformat(value.replace("Z", "+00:00")) -
                     datetime.now(timezone.utc)).total_seconds()
        return time.monotonic() + max(0, remaining)

    def _apply_stop(self, view: dict, claim: dict, handle, client, sent: tuple | None,
                    forced_at: float | None) -> tuple[tuple | None, float | None]:
        intent = self._stop_intent(view, handle)
        if intent is None:
            return sent, forced_at
        initiator, deadline, force, reason = intent
        command = (initiator, None if initiator == "deadline" else view.get("graceDeadline"), force)
        if sent != command:
            if force:
                record = self.store.record_execution_deadline(claim["attempt"]["attemptId"], reason)
                forced_at = forced_at or self._timestamp(record["forceRequestedAt"])
            client.stop(initiator=initiator, grace_deadline=None if force else deadline, force=force)
            sent = command
        return sent, forced_at

    def _stop_intent(self, view: dict, handle) -> tuple[str, float | None, bool, str] | None:
        attempt = view.get("selectedAttempt") or {}
        if attempt.get("forceRequestedAt") is not None:
            reason = attempt["forceReason"]
            initiator = "deadline" if reason == "deadline" else view.get("stopInitiator") or "service"
            return initiator, None, True, reason
        initiator = view.get("stopInitiator") if view.get("cancelRequested") else None
        deadline = self._deadline(view.get("graceDeadline")) if initiator else None
        try:
            expired = self._budget_expired(handle)
        except (BoardError, OSError, ValueError, TypeError, KeyError) as error:
            attempt_id = handle.execution_binding["attemptId"]
            self._issue(attempt_id, "budget", error)
            return "service", None, True, "forced-stop"
        if expired:
            initiator, deadline = "deadline", None
        if initiator is None:
            return None
        force = deadline is None or time.monotonic() >= deadline
        return initiator, deadline, force, self._forced_reason(initiator, view.get("graceDeadline"))

    @staticmethod
    def _budget_expired(handle) -> bool:
        path = handle.budget_path
        from ...private_dirs import open_regular_fd
        from ...json_codec import decode_strict_json
        if not path.exists():
            return False
        try:
            descriptor = open_regular_fd(path, os.O_RDONLY)
        except FileNotFoundError:
            return False
        with os.fdopen(descriptor, "rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise BoardError("INVALID_ARGUMENT", "Execution budget exceeds its bound")
        budget = decode_strict_json(raw)
        if (not isinstance(budget, dict) or set(budget) != {"binding", "deadline"}
                or type(budget["deadline"]) not in (int, float) or not math.isfinite(budget["deadline"])):
            raise BoardError("INVALID_ARGUMENT", "Execution budget has an invalid shape")
        if budget["binding"] != handle.execution_binding:
            raise BoardError("CONFLICT", "Execution budget belongs to another attempt")
        return time.monotonic() >= budget["deadline"]

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
            receipt = read_execution_receipt(handle.execution_directory, binding=handle.execution_binding)
        except (BoardError, OSError, ValueError, RecursionError):
            receipt = None
        if receipt is not None and receipt["report"].get("shutdownConfirmed") is True:
            return
        if inspect_process_identity(identity) != "alive":
            return
        handle.signal_group(signal.SIGKILL if os.name != "nt" else -1)
