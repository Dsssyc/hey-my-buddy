"""Service lifecycle and direct execution scheduling."""
from __future__ import annotations

from ... import locking
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
import uuid

import c_two as cc

from ...protocol import rpc_config
from ...install import launcher, runtime
from ..tasks import scheduling
from ...console.server import Console
from ...protocol.contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from ..store.db import SCHEMA_VERSION, utc_now
from ...private_files import publish_bytes, read_text
from ...errors import BoardError
from .service import BoardService, WaitAdmission, WaitService
from .execution_scheduler import ExecutionScheduler
from .live_registry import LiveRegistry
from ..store.store import BoardStore
from ...protocol.transport import ServiceError
from ...state_root import check_state_root, get_state_dir

DRAIN_SECONDS_DEFAULT = 10


def atomic_json(path: Path, value: dict) -> None:
    publish_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


class Daemon:
    def __init__(self, directory: Path):
        self.directory = get_state_dir(directory)
        self.stopping = threading.Event()
        self._maintenance: threading.Thread | None = None
        self.started_at = utc_now()
        self.service_id = str(uuid.uuid4())
        self.token = secrets.token_urlsafe(32)
        wait_capacity = _env_int("HMB_WAIT_CAPACITY", 32)
        if wait_capacity > rpc_config.MAX_WAIT_CAPACITY:
            raise BoardError("INVALID_ARGUMENT", "HMB_WAIT_CAPACITY exceeds RPC callback capacity")
        self.wait_admission = WaitAdmission(wait_capacity)
        self._admission_lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_pending: set[str] = set()
        max_concurrent = _env_int("HMB_MAX_CONCURRENT", scheduling.TOTAL_CONCURRENCY_DEFAULT)
        minimum, maximum = launcher.LAUNCH_KEYS["HMB_MAX_CONCURRENT"]
        if not minimum <= max_concurrent <= maximum:
            raise BoardError("INVALID_ARGUMENT", f"HMB_MAX_CONCURRENT must be between {minimum} and {maximum}")
        self.store = BoardStore(self.directory, max_concurrent=max_concurrent)
        self.live_registry = LiveRegistry()
        self.control = {
            "service_id": self.service_id,
            "contract_version": CONTRACT_VERSION,
            "protocol": 2,
            "schema_version": SCHEMA_VERSION,
            "wait_capacity": self.wait_admission.capacity,
            "wait_admission": self.wait_admission,
            "stopping": False,
            "restart_requested": False,
            "runtime": runtime.runtime_identity(),
        }
        self.console: Console | None = None
        self.lock_fds: list[int] = []
        self.endpoint_path = self.directory / "control.json"
        self.resume_path = self.directory / "restart.resume.json"
        self.stop_path = self.directory / "stop.request.json"
        self.scheduler: ExecutionScheduler | None = None

    def acquire_exclusive(self) -> None:
        for name in ("control-daemon.lock", "board-owner.lock"):
            from ...private_dirs import open_regular_fd
            fd = open_regular_fd(self.directory / name, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                locking.lock(fd, blocking=False)
            except BlockingIOError:
                os.close(fd)
                raise ServiceError("ALREADY_RUNNING", "Another owner is already running for this state directory", lock=name)
            self.lock_fds.append(fd)

    def queue_workspace_cleanup(self, run_id: str) -> None:
        # The queue is only an optimization. Existing accepted workspaces remain
        # discoverable by storage plan after a crash or daemon restart.
        with self._cleanup_lock:
            self._cleanup_pending.add(run_id)

    def _cleanup_accepted_workspaces(self) -> None:
        if self.store.upgrading():
            return
        from ..tasks.storage import cleanup_accepted_workspace
        with self._cleanup_lock:
            pending = tuple(self._cleanup_pending)
            self._cleanup_pending.clear()
        failed = False
        for run_id in pending:
            try:
                cleanup_accepted_workspace(self.store, run_id)
            except (BoardError, OSError) as error:
                # Expected retention preserves the workspace and its durable plan.
                if isinstance(error, BoardError) and error.code == "NOT_READY":
                    continue
                failed = True
                self.store.record_service_issue("workspace-cleanup", error)
        if pending and not failed:
            self.store.clear_service_issue("workspace-cleanup")

    def service(self) -> BoardService:
        return BoardService(
            self.store, live_registry=self.live_registry, token=self.token, control=self.control,
            on_stop=self.on_stop, on_restart=self.on_restart,
            console_factory=self.on_console,
            on_accepted=self.queue_workspace_cleanup,
            automatic_discovery=True,
            admission_lock=self._admission_lock,
        )

    def run(self) -> int:
        self.directory = check_state_root(self.directory, create=True)
        self.control["rpc_profile"] = rpc_config.configure_server(self.directory)
        self.acquire_exclusive()
        try:
            self.store.initialize()
            service = self.service()
            self.harnesses = service.harnesses
            self.scheduler = ExecutionScheduler(service, live_registry=self.live_registry)
            service.execution_scheduler = self.scheduler
            self.store.on_change = self.scheduler.wake
            self.console = Console(self.store, service)
            self.console.start(issue_ticket=False)
            wait_service = WaitService(self.store, self.wait_admission, token=self.token)
            cc.register(BuddyControl, service, name=CONTROL_NAME,
                        concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
            cc.register(BuddyWait, wait_service, name=WAIT_NAME,
                        concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
            self.resume_path.unlink(missing_ok=True)
            self.stop_path.unlink(missing_ok=True)
            atomic_json(self.endpoint_path, {
                "address": cc.server_address(), "token": self.token,
                "pid": os.getpid(), "protocol": 2,
                "contractVersion": CONTRACT_VERSION, "schemaVersion": SCHEMA_VERSION,
                "serviceId": self.service_id, "startedAt": self.started_at,
                "runtimeIdentity": self.control["runtime"],
            })
            self.scheduler.start()
            from ..tasks.reminders import Scheduler
            self._reminders = Scheduler(self.store)
            self._reminders.thread.start()
            self._maintenance = threading.Thread(target=self._maintain, name="workspace-maintenance", daemon=True)
            self._maintenance.start()
            self.stopping.wait()
            return 0
        finally:
            self.cleanup()

    def _maintain(self) -> None:
        next_recovery = time.monotonic() + 15
        while not self.stopping.wait(2):
            self._cleanup_accepted_workspaces()
            if time.monotonic() >= next_recovery:
                try:
                    self.store.recover_executions()
                except Exception as error:
                    self.store.persistence_error = f"Execution recovery: {type(error).__name__}: {error}"
                next_recovery = time.monotonic() + 15

    def on_stop(self, params: dict) -> dict:
        drain = int(params.get("drainSeconds", DRAIN_SECONDS_DEFAULT))
        note = params.get("note")
        with self._admission_lock:
            self.control["stopping"] = True
        atomic_json(self.stop_path, {"requestedAt": utc_now(), "note": note, "drainSeconds": drain})
        micro_task, micro_task_task_ids = self.store.workflow.cancel_for_service_stop(note)
        queued = micro_task["queued"] + self.cancel_queued(note, exclude=micro_task_task_ids)
        active = micro_task["active"] + self.cancel_active(note, exclude=micro_task_task_ids)
        if self.scheduler is not None:
            self.scheduler.send_pending_stops()
        unresolved = self.drain(drain)
        self.stopping.set()
        self.store.stop_waiters()
        return {"action": "stop", "stopped": not unresolved, "cancelledQueued": queued,
                "cancelRequested": active, "unresolvedAttempts": unresolved, "drainSeconds": drain}

    def on_restart(self, params: dict) -> dict:
        drain = int(params.get("drainSeconds", DRAIN_SECONDS_DEFAULT))
        note = params.get("note")
        with self._admission_lock:
            self.control["restart_requested"] = True
        atomic_json(self.resume_path, {"requestedAt": utc_now(), "note": note,
                                       "serviceId": self.service_id, "drainSeconds": drain})
        self.stopping.set()
        self.store.stop_waiters()
        return {"action": "restart", "restarting": True, "executionsPreserved": True,
                "resumeFile": str(self.resume_path)}

    def on_console(self, params: dict) -> dict:
        if params["action"] == "close":
            return self.console.close(expected_console_id=params.get("expectedConsoleId"))
        if params["action"] == "status":
            return self.console.status()
        return self.console.start()

    def cancel_queued(self, note: str | None, *, exclude: set[str] | None = None) -> int:
        count = 0
        failed = False
        for task in self.store.active_work()["queuedTasks"]:
            if exclude and task["taskId"] in exclude:
                continue
            try:
                self.store.task_cancel({"runId": task["taskId"], **({"note": note} if note else {})},
                                       initiator="service")
                count += 1
            except BoardError as error:
                failed = True
                self.store.record_service_issue("cancel-queued", error)
                continue
        if not failed:
            self.store.clear_service_issue("cancel-queued")
        return count

    def cancel_active(self, note: str | None, *, exclude: set[str] | None = None) -> int:
        count = 0
        failed = False
        for attempt in self.store.active_work()["attempts"]:
            if exclude and attempt["taskId"] in exclude:
                continue
            try:
                self.store.task_cancel({"runId": attempt["taskId"], **({"note": note} if note else {})},
                                       initiator="service")
                count += 1
            except BoardError as error:
                failed = True
                self.store.record_service_issue("cancel-active", error)
                continue
        if not failed:
            self.store.clear_service_issue("cancel-active")
        return count

    def drain(self, seconds: int) -> list[dict]:
        """Wait a bounded interval and report exactly what is still unresolved."""
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            work = self.store.active_work()
            if not work["attempts"]:
                return []
            time.sleep(0.25)
        return [
            {
                "attemptId": attempt["attemptId"],
                "taskId": attempt["taskId"],
                "generation": attempt["generation"],
                "executionState": attempt["executionState"],
                "ownership": attempt["ownership"],
                "shutdownConfirmed": attempt["shutdownConfirmed"],
            }
            for attempt in self.store.active_work()["attempts"]
        ]

    def cleanup(self) -> None:
        self.stopping.set()
        self.store.stop_waiters()
        if self.scheduler is not None:
            self.scheduler.stop()
        if getattr(self, '_reminders', None) is not None:
            self._reminders.thread.join()
        if self._maintenance is not None:
            self._maintenance.join()
        if getattr(self, 'harnesses', None) is not None:
            self.harnesses.close()
        self.live_registry.close()
        try:
            cc.shutdown()
        except Exception:
            pass
        if self.console is not None:
            self.console.close()
        self.store.db.close()
        try:
            value = json.loads(read_text(self.endpoint_path))
            if hmac.compare_digest(value.get("serviceId", ""), self.service_id):
                self.endpoint_path.unlink()
        except (OSError, ValueError, BoardError):
            pass
        for fd in self.lock_fds:
            try:
                locking.unlock(fd)
                os.close(fd)
            except OSError:
                continue
        self.lock_fds = []


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError) as error:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be an integer") from error


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
    selected = os.environ.get("HMB_STATE_DIR")
    if not selected:
        sys.stderr.write("hey-my-buddy daemon: HMB_STATE_DIR is required\n")
        return 2
    try:
        directory = check_state_root(get_state_dir(selected), create=True)
    except BoardError as error:
        sys.stderr.write(f"hey-my-buddy: {error.code}: {error}\n")
        return 2
    try:
        daemon = Daemon(directory)
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_args: daemon.on_stop({"drainSeconds": DRAIN_SECONDS_DEFAULT}))
        return daemon.run()
    except RuntimeError as error:
        if not isinstance(error, BoardError):
            from ...protocol.transport import CoreError, _local_rpc_error

            if isinstance(error, CoreError) and getattr(error, "io_kind", None) is not None:
                error = _local_rpc_error(error)
            elif (getattr(error, "category", None) == "lifecycle"
                    and getattr(error, "lifecycle_kind", None) == "startup_timeout"):
                error = ServiceError("SERVICE_START_TIMEOUT", str(error))
            else:
                raise
        atomic_json(directory / "startup-error.json", {"pid": os.getpid(), **error.payload()})
        # An actionable, non-destructive failure: never fall back to an old
        # implementation and never rewrite records.
        sys.stderr.write(f"hey-my-buddy: {error.code}: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
