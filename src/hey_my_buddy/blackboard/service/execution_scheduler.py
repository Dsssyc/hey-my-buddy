"""Service-owned admission and observation of one process per execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import secrets
import threading
import time

from ...errors import BoardError
from ...json_codec import canonical_json
from .live_registry import LiveRegistry
from ...protocol.execution import (BUDGET, execution_directory, execution_live_error,
                                  launch_execution, read_execution_endpoint, read_execution_receipt)
from ...protocol.process_identity import inspect_process_identity, boot_id, kill_verified_process_group
from ...protocol.run_identity import RunIdentity


@dataclass(frozen=True)
class _RecoveredExecution:
    """Endpoint locations retained by the store; no creating process handle."""

    execution_directory: Path
    execution_binding: dict
    role_run_identity: RunIdentity
    process = None

    @property
    def budget_path(self) -> Path:
        return self.execution_directory / BUDGET


@dataclass
class _ConnectionWatch:
    error: Exception | None = None


class ExecutionScheduler:
    """Admit within store capacity and observe every process through its endpoint."""

    TICK_SECONDS = 2.0
    STOP_GRACE_SECONDS = 8.0

    def __init__(self, service, *, live_registry: LiveRegistry):
        self.service = service
        self.live_registry = live_registry
        self.store = service.store
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._revision = 0
        self._connections: dict[int, _ConnectionWatch] = {}
        self._observed: dict[str, int] = {}
        self._handles: dict[str, tuple[object, dict | None]] = {}
        self._threads: set[threading.Thread] = set()
        self._thread = threading.Thread(target=self._run, name="execution-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()
        with self._condition:
            self._revision += 1
            self._condition.notify_all()

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
        """Push durable stop intent through the observers' existing connections."""
        with self._condition:
            self.wake()
            revision = self._revision
            observed = tuple(self._observed)
            # A zero-drain stop can begin detaching immediately after this
            # returns. Each observer must first consume the committed intent.
            self._condition.wait_for(lambda: all(
                self._observed.get(attempt_id, revision) >= revision for attempt_id in observed))

    def _issue(self, attempt_id: str, stage: str, error: Exception) -> None:
        try:
            self.store.record_execution_issue(attempt_id, stage, error)
        except Exception as reporting_error:
            self.store.persistence_error = (f"Execution {attempt_id} {stage}: {type(error).__name__}: {error}; "
                                            f"issue recording failed: {reporting_error}")

    def _scheduler_issue(self, stage: str, error: Exception) -> None:
        try:
            self.store.record_service_issue(stage, error)
        except Exception as reporting_error:
            self.store.persistence_error = (f"Scheduler {stage}: {type(error).__name__}: {error}; "
                                            f"issue recording failed: {reporting_error}")

    def verify_identity(self, attempt_id: str, identity: dict) -> bool:
        with self._lock:
            held = self._handles.get(attempt_id)
            if held is None:
                return inspect_process_identity(identity) == "alive"
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
        try:
            self.store.recover_executions()
            self.store.clear_service_issue("startup-recovery")
        except Exception as error:
            self._scheduler_issue("startup-recovery", error)
        while not self._stopping.is_set():
            try:
                self._resume_executions()
                self.store.clear_service_issue("reconnection")
            except Exception as error:
                self._scheduler_issue("reconnection", error)
            if not self.service.control.get("stopping") and not self.service.control.get("restart_requested"):
                try:
                    self._admit_available()
                    self.store.clear_service_issue("admission")
                except Exception as error:
                    self._scheduler_issue("admission", error)
            self._wake.wait(self.TICK_SECONDS)
            self._wake.clear()

    def _resume_executions(self) -> None:
        # Recovery captures spawn markers and settles identities already absent.
        # The remaining identities use the same observer as newly created children.
        for claim in self.store.execution_observations():
            attempt_id = claim["attempt"]["attemptId"]
            with self._lock:
                if attempt_id in self._observed:
                    continue
                owned = self._handles.get(attempt_id)
                if owned is not None:
                    # Retain creator evidence if starting its monitor failed.
                    self._start_observer(claim, *owned)
                    continue
            identity = claim["processIdentity"]
            if identity is None:
                continue
            attempt = claim["attempt"]
            binding = {"taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
                       "generation": attempt["generation"]}
            if claim["task"]["spec"]["adapter"] == "decision":
                claim["decisionInput"] = {"buddy": {"harness": claim["harness"]}}
            location = _RecoveredExecution(execution_directory(claim, self.store.directory), binding,
                RunIdentity(task_id=binding["taskId"], attempt_id=binding["attemptId"],
                            generation=binding["generation"], invocation_id="execution", turn_id=claim["turnId"]))
            with self._lock:
                self._handles[attempt["attemptId"]] = (location, identity)
                self._start_observer(claim, location, identity)

    def _start_observer(self, claim: dict, handle, identity: dict | None) -> None:
        attempt_id = claim["attempt"]["attemptId"]
        monitor = threading.Thread(target=self._monitor, args=(claim, handle, identity),
                                   name=f"execution-{claim['attempt']['attemptId']}", daemon=True)
        with self._condition:
            self._threads.add(monitor)
            self._observed[attempt_id] = -1
            try:
                monitor.start()
            except BaseException:
                self._threads.remove(monitor)
                del self._observed[attempt_id]
                self._condition.notify_all()
                raise

    def _monitor(self, claim: dict, handle, identity: dict | None) -> None:
        try:
            if identity is None:
                self._settle_unidentified(claim, handle)
            else:
                self._observe(claim, handle, identity)
        finally:
            with self._condition:
                self._threads.discard(threading.current_thread())
                del self._observed[claim["attempt"]["attemptId"]]
                self.wake()

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
            failure = None
            with self._lock:
                stopped = self.service.control.get("stopping") or self.service.control.get("restart_requested")
                if stopped:
                    handle = None
                else:
                    try:
                        handle = launch_execution(claim, self.store.directory, nonce,
                                                  session_record_path=claim["sessionRecordPath"])
                    except Exception as error:
                        failure = error
                        handle = getattr(error, "execution_handle", None)
                    if handle is not None:
                        identity = getattr(handle, "process_identity", None)
                        self._handles[attempt_id] = (handle, identity)
                        self._start_observer(claim, handle, identity)
            if stopped:
                self.store.release_execution(attempt_id,
                                             reason="service-stopped-before-launch")
                return
            if failure is not None:
                self._issue(attempt_id, "launch", failure)
            if handle is None:
                if failure is not None and not getattr(failure, "spawn_created", True):
                    self.store.release_execution(attempt_id,
                                                 reason="execution-launch-failed")
                elif failure is not None and getattr(
                        getattr(failure, "owned_popen_evidence", None), "shutdown_confirmed", False):
                    self.store.recover_executions(attempt_id, stopped_evidence={
                        "kind": "creator-reaped", "exitCode": failure.owned_popen_evidence.exit_code})
                else:
                    self.store.recover_executions(attempt_id)
                return

    def _settle_unidentified(self, claim: dict, handle) -> None:
        """Use the creator's reaped child and empty group when no identity was published."""
        attempt_id = claim["attempt"]["attemptId"]
        while not self._stopping.is_set():
            with self._condition:
                revision = self._revision
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
                intent, _deadline = self._stop_intent(view, handle)
                if intent is not None and intent[2]:
                    self.store.record_execution_deadline(attempt_id, intent[3])
                    handle.terminate(grace_seconds=0)
            except Exception as error:
                self._issue(attempt_id, "unidentified-observation", error)
            with self._condition:
                self._observed[attempt_id] = revision
                self._condition.notify_all()
                self._condition.wait_for(
                    lambda: revision != self._revision or self._stopping.is_set(), 0.1)

    def _endpoint(self, attempt_id: str, handle, identity: dict):
        # Publication precedes the identity acknowledgement. Sample the latter
        # first so publication between these two reads cannot look like a loss.
        confirmed = self.store.execution_identity_confirmed(attempt_id)
        client = read_execution_endpoint(handle.execution_directory, handle.role_run_identity,
                                         state_dir=self.store.directory, process_identity=identity)
        if client is None and confirmed:
            raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint material is missing")
        if client is not None:
            try:
                client.connect()
            except BaseException:
                client.close(reason="authentication-failed")
                raise
        return client

    def _watch_connection(self, observation) -> _ConnectionWatch:
        # Logical reconnects can share one physical stream. Its one waiter
        # remains useful until the physical connection actually terminates.
        key = observation.connection_id
        with self._condition:
            watch = self._connections.get(key)
            if watch is None:
                watch = _ConnectionWatch()
                threading.Thread(target=self._connection_closed, args=(key, observation, watch),
                                 name=f"execution-connection-{key}", daemon=True).start()
                self._connections[key] = watch
            return watch

    def _connection_closed(self, key, observation, watch: _ConnectionWatch) -> None:
        try:
            closed = observation.wait_closed(timeout=None)
            error = BoardError("LIVE_UNAVAILABLE", "The execution physical connection closed",
                               connectionId=closed.connection_id, reason=closed.reason,
                               io_operation=closed.operation, raw_os_error=closed.os_error)
        except Exception as failure:
            error = execution_live_error(failure)
        with self._condition:
            watch.error = error
            del self._connections[key]
            self._revision += 1
            self._condition.notify_all()

    def _observe(self, claim: dict, handle, identity: dict) -> None:
        attempt_id = claim["attempt"]["attemptId"]
        task_id = claim["task"]["taskId"]
        client = watch = None
        stop_sent: tuple | None = None
        forced_at: float | None = None
        force_checked = False
        force_retry_at = None
        force_delay = 0.1
        retry_at = None
        delay = 0.1
        settled = False
        try:
            while not self._stopping.is_set():
                # A notification during any read/call below must prevent sleep.
                with self._condition:
                    revision = self._revision
                next_deadline = None
                try:
                    if handle.process is not None:
                        handle.process.poll()  # Reap the creator's child only.
                    if watch is not None and watch.error is not None:
                        raise watch.error
                    if client is None and (retry_at is None or time.monotonic() >= retry_at):
                        observation = inspect_process_identity(identity)
                        if observation in ("identity-absent", "boot-changed"):
                            if self._recover_if_gone(attempt_id, identity, observation):
                                settled = True
                                return
                        elif observation == "alive":
                            try:
                                client = self._endpoint(attempt_id, handle, identity)
                            except Exception as error:
                                self._unknown(attempt_id, error)
                            if client is not None:
                                self.live_registry.bind(attempt_id, client)
                                watch = self._watch_connection(client.connection_observation)
                                stop_sent = None  # Re-send durable intent on every new channel.
                                delay, retry_at = 0.1, None
                                self.store.execution_connection(attempt_id, connected=True)
                                self.store.clear_execution_issue(attempt_id, "connection")
                        else:
                            self._unknown(attempt_id, "The recorded process identity cannot be verified")
                        if client is None:
                            retry_at = time.monotonic() + delay
                            delay = min(delay * 2, self.TICK_SECONDS)
                    # This reads the budget on first attachment and every wake,
                    # including a late budget publication or a deadline expiry.
                    view = self.store.task_get({"runId": task_id})["task"]
                    if forced_at is None:
                        forced_at = self._force_requested_at(view)
                    stop_sent, forced_at, next_deadline = self._apply_stop(
                        view, claim, handle, client, stop_sent, forced_at)
                except Exception as error:
                    if isinstance(error, BoardError) and error.code == "LIVE_UNAVAILABLE":
                        self._unknown(attempt_id, error)
                    else:
                        self._issue(attempt_id, "observation", error)
                    if client is not None:
                        self.live_registry.unbind(attempt_id, client)
                        client = watch = None
                    retry_at = time.monotonic() + delay
                    delay = min(delay * 2, self.TICK_SECONDS)
                if forced_at is not None and not force_checked and (
                        time.monotonic() >= (force_retry_at or forced_at + self.STOP_GRACE_SECONDS)):
                    try:
                        force_checked = self._force_if_required(handle, identity)
                    except Exception as error:
                        self._issue(attempt_id, "force-stop", error)
                    if not force_checked:
                        force_retry_at = time.monotonic() + force_delay
                        force_delay = min(force_delay * 2, self.TICK_SECONDS)
                if client is None:
                    try:
                        if self._recover_if_gone(attempt_id, identity):
                            settled = True
                            return
                    except Exception as error:
                        self._issue(attempt_id, "recovery", error)
                deadlines = [value for value in (
                    next_deadline, retry_at if client is None else None,
                    (force_retry_at or forced_at + self.STOP_GRACE_SECONDS)
                        if forced_at is not None and not force_checked else None,
                ) if value is not None]
                timeout = max(0, min(deadlines) - time.monotonic()) if deadlines else None
                with self._condition:
                    if self._observed[attempt_id] < revision:
                        self._observed[attempt_id] = revision
                        self._condition.notify_all()
                    if revision == self._revision and not self._stopping.is_set():
                        self._condition.wait_for(
                            lambda: revision != self._revision or self._stopping.is_set(), timeout)
        finally:
            if client is not None:
                self.live_registry.unbind(attempt_id, client)
            with self._lock:
                if settled:
                    self._handles.pop(attempt_id, None)

    def _unknown(self, attempt_id: str, error) -> None:
        self.store.execution_connection(attempt_id, connected=False)
        detail = error.message if isinstance(error, BoardError) else str(error)
        details = error.details if isinstance(error, BoardError) else {}
        if details:
            # execution_issues stores the message; retain the structured cause
            # there as well as on the error passed to the recording boundary.
            detail += f"; {canonical_json(details)}"
        self._issue(attempt_id, "connection", BoardError("EXECUTION_UNKNOWN", detail, **details))

    def _recover_if_gone(self, attempt_id: str, identity: dict, observation: str | None = None) -> bool:
        observation = observation or inspect_process_identity(identity)
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
        return time.monotonic() + (datetime.fromisoformat(value) -
                                   datetime.now(timezone.utc)).total_seconds()

    @staticmethod
    def _deadline(value: str | None) -> float | None:
        if value is None:
            return None
        remaining = (datetime.fromisoformat(value) -
                     datetime.now(timezone.utc)).total_seconds()
        return time.monotonic() + max(0, remaining)

    def _apply_stop(self, view: dict, claim: dict, handle, client, sent: tuple | None,
                    forced_at: float | None) -> tuple[tuple | None, float | None, float | None]:
        intent, next_deadline = self._stop_intent(view, handle)
        if intent is None:
            return sent, forced_at, next_deadline
        initiator, deadline, force, reason = intent
        command = (initiator, None if initiator == "deadline" else view.get("graceDeadline"), force)
        if force and forced_at is None:
            record = self.store.record_execution_deadline(claim["attempt"]["attemptId"], reason)
            forced_at = self._timestamp(record["forceRequestedAt"])
        if client is not None and sent != command:
            client.stop(initiator=initiator, grace_deadline=None if force else deadline, force=force)
            sent = command
        return sent, forced_at, next_deadline

    def _stop_intent(self, view: dict, handle) -> tuple[tuple | None, float | None]:
        attempt = view.get("selectedAttempt") or {}
        if attempt.get("forceRequestedAt") is not None:
            reason = attempt["forceReason"]
            initiator = "deadline" if reason == "deadline" else view.get("stopInitiator") or "service"
            return (initiator, None, True, reason), None
        initiator = view.get("stopInitiator") if view.get("cancelRequested") else None
        deadline = self._deadline(view.get("graceDeadline")) if initiator else None
        try:
            budget_deadline = self._budget_deadline(handle)
        except (BoardError, OSError, ValueError, TypeError, KeyError) as error:
            attempt_id = handle.execution_binding["attemptId"]
            self._issue(attempt_id, "budget", error)
            return ("service", None, True, "forced-stop"), None
        expired = budget_deadline is not None and time.monotonic() >= budget_deadline
        if expired:
            initiator, deadline = "deadline", None
        if initiator is None:
            return None, budget_deadline
        force = deadline is None or time.monotonic() >= deadline
        deadlines = [value for value in (deadline, budget_deadline) if value is not None]
        next_deadline = min(deadlines) if deadlines and not force else None
        return (initiator, deadline, force, self._forced_reason(initiator, view.get("graceDeadline"))), next_deadline

    @staticmethod
    def _budget_deadline(handle) -> float | None:
        path = handle.budget_path
        from ...private_dirs import open_regular_fd
        from ...json_codec import decode_strict_json
        try:
            descriptor = open_regular_fd(path, os.O_RDONLY)
        except FileNotFoundError:
            return None
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
        return budget["deadline"]

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
    def _force_if_required(handle, identity: dict) -> bool:
        try:
            receipt = read_execution_receipt(handle.execution_directory, binding=handle.execution_binding)
        except (BoardError, OSError, ValueError, RecursionError):
            receipt = None
        if receipt is not None and receipt["report"].get("shutdownConfirmed") is True:
            return True
        kill_verified_process_group(identity)
        # A signal, or an identity that could not be verified, proves no stop.
        return False
