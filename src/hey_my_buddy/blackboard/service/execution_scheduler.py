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
from ...private_dirs import session_record_path
from ..tasks.macro_tasks import MEMBERS_SQL
from .live_registry import LiveRegistry
from ...protocol.execution import BUDGET, execution_directory, launch_execution, read_execution_endpoint, read_execution_receipt
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
        """Deliver durable stops even while the first long call is authenticating."""
        with self._lock:
            observed = tuple((attempt_id, held[0], held[1], self._clients.get(attempt_id))
                             for attempt_id, held in self._handles.items())
        for attempt_id, handle, identity, client in observed:
            temporary = None
            try:
                view = self.store.task_get({"runId": handle.execution_binding["taskId"]})["task"]
                if client is None and identity is not None and inspect_process_identity(identity) == "alive":
                    try:
                        temporary = self._endpoint(attempt_id, handle, identity)
                        client = temporary
                    except Exception as error:
                        self._unknown(attempt_id, error)
                self._apply_stop(view, {"attempt": {"attemptId": attempt_id}}, handle, client, None, None)
            except BoardError as error:
                if error.code == "LIVE_UNAVAILABLE":
                    self._unknown(attempt_id, error)
                else:
                    self._issue(attempt_id, "service-stop", error)
            except Exception as error:
                self._issue(attempt_id, "service-stop", error)
            finally:
                if temporary is not None:
                    temporary.close(reason="service-stop-delivered")

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
            with self._lock:
                if claim["attempt"]["attemptId"] in self._handles:
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
        target = self._observe if identity is not None else self._settle_unidentified
        monitor = threading.Thread(target=target, args=(claim, handle, identity) if identity is not None
                                   else (claim, handle),
                                   name=f"execution-{claim['attempt']['attemptId']}", daemon=True)
        with self._lock:
            self._threads.add(monitor)
        monitor.start()

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
                                             reason="service-stopped-before-launch")
                return
            failure = None
            with self._lock:
                try:
                    record_path = self._session_record_path(claim)
                    handle = launch_execution(claim, self.store.directory, nonce,
                                              session_record_path=record_path)
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
                                                 reason="execution-launch-failed")
                elif failure is not None and getattr(
                        getattr(failure, "owned_popen_evidence", None), "shutdown_confirmed", False):
                    self.store.recover_executions(attempt_id, stopped_evidence={
                        "kind": "creator-reaped", "exitCode": failure.owned_popen_evidence.exit_code})
                else:
                    self.store.recover_executions(attempt_id)
                return
            self._start_observer(claim, handle, identity)

    def _session_record_path(self, claim: dict) -> Path | None:
        """Resolve the same group key the console uses for this execution."""
        if claim["task"]["spec"]["adapter"] == "command":
            return None
        with self.store.db.read() as connection:
            member = connection.execute(
                f"SELECT group_id FROM ({MEMBERS_SQL}) WHERE task_id=?",
                (claim["task"]["taskId"],)).fetchone()
        if member is None:
            raise BoardError("INVALID_ARGUMENT", "Execution has no session group")
        return session_record_path(self.store.directory, member["group_id"],
                                   claim["attempt"]["attemptId"])

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

    def _endpoint(self, attempt_id: str, handle, identity: dict):
        # Publication precedes the identity acknowledgement. Sample the latter
        # first so publication between these two reads cannot look like a loss.
        confirmed = self.store.execution_identity_confirmed(attempt_id)
        client = read_execution_endpoint(handle.execution_directory, handle.role_run_identity,
                                         state_dir=self.store.directory, process_identity=identity)
        if client is None and confirmed:
            raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint material is missing")
        return client

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
                    # Creating handles only reap children. A long call and the
                    # recorded identity determine liveness for every execution.
                    if handle.process is not None:
                        handle.process.poll()
                    connecting = client is None
                    if client is None:
                        observation = inspect_process_identity(identity)
                        if observation in ("identity-absent", "boot-changed"):
                            if self._recover_if_gone(attempt_id, identity, observation):
                                settled = True
                                return
                        elif observation == "alive":
                            try:
                                client = self._endpoint(attempt_id, handle, identity)
                                if client is not None:
                                    with self._lock:
                                        self._clients[attempt_id] = client
                                    stop_sent = None  # Every new channel needs the durable intent.
                            except Exception as error:
                                self._unknown(attempt_id, error)
                        else:
                            self._unknown(attempt_id, "The recorded process identity cannot be verified")
                    view = self.store.task_get({"runId": task_id})["task"]
                    forced_at = forced_at or self._force_requested_at(view)
                    # The deadline is durable even when stop delivery is impossible.
                    stop_sent, forced_at = self._apply_stop(view, claim, handle, client, stop_sent, forced_at)
                    if client is not None:
                        try:
                            if client.wait():
                                delay = 0.1
                                if connecting:
                                    self.store.execution_connection(attempt_id, connected=True)
                                    self.store.clear_execution_issue(attempt_id, "connection")
                                    self.live_registry.bind(attempt_id, client)
                                    with self._lock:
                                        self._clients[attempt_id] = client
                        except BoardError as error:
                            if error.code != "LIVE_UNAVAILABLE":
                                raise
                            self._unknown(attempt_id, error)
                            self.live_registry.unbind(attempt_id, client)
                            with self._lock:
                                self._clients.pop(attempt_id, None)
                            client = None
                    if client is None:
                        if self._recover_if_gone(attempt_id, identity):
                            settled = True
                            return
                except Exception as error:
                    if isinstance(error, BoardError) and error.code == "LIVE_UNAVAILABLE":
                        self._unknown(attempt_id, error)
                    else:
                        self._issue(attempt_id, "observation", error)
                    if client is not None:
                        self.live_registry.unbind(attempt_id, client)
                        with self._lock:
                            self._clients.pop(attempt_id, None)
                        client = None
                    try:
                        if self._recover_if_gone(attempt_id, identity):
                            settled = True
                            return
                    except Exception as recovery_error:
                        self._issue(attempt_id, "recovery", recovery_error)
                if forced_at is not None and time.monotonic() - forced_at >= self.STOP_GRACE_SECONDS:
                    try:
                        self._force_if_required(handle, identity)
                    except Exception as error:
                        self._issue(attempt_id, "force-stop", error)
                if client is None:
                    self._stopping.wait(delay)
                    delay = min(delay * 2, self.TICK_SECONDS)
        finally:
            if client is not None:
                self.live_registry.unbind(attempt_id, client)
            with self._lock:
                self._clients.pop(attempt_id, None)
                if settled:
                    self._handles.pop(attempt_id, None)
                self._threads.discard(threading.current_thread())
            self.wake()

    def _unknown(self, attempt_id: str, error) -> None:
        self.store.execution_connection(attempt_id, connected=False)
        detail = error.message if isinstance(error, BoardError) else str(error)
        self._issue(attempt_id, "connection", BoardError("EXECUTION_UNKNOWN", detail))

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
        if force and forced_at is None:
            record = self.store.record_execution_deadline(claim["attempt"]["attemptId"], reason)
            forced_at = self._timestamp(record["forceRequestedAt"])
        if client is not None and sent != command:
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
        kill_verified_process_group(identity)
