"""Service lifecycle: exclusive ownership, controlled detach, bounded drain.

The daemon is the only writer of authoritative state, but it never owns worker
processes: it may *request* that a worker stops, and only the worker holding a child
handle sends OS signals. A restarted daemon reconciles workers by attempt identity
and capability, never by PID.
"""
from __future__ import annotations

import errno
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import c_two as cc

from . import runtime
from .contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from .dashboard import Dashboard
from .db import SCHEMA_VERSION, utc_now
from .errors import BoardError
from .legacy import LegacyImporter
from .service import BoardService, WaitAdmission, WaitService
from .store import BoardStore
from .transport import ServiceError, get_state_dir

DRAIN_SECONDS_DEFAULT = 10
LEASE_SWEEP_SECONDS = 15


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _unix_address_unrepresentable(error: OSError) -> bool:
    """True only when the real ``bind`` failure proves AF_UNIX cannot name the address.

    CPython refuses an address longer than the platform's ``sun_path`` before the
    kernel ever sees it and raises a bare ``OSError("AF_UNIX path too long")``; a
    kernel that enforces the limit itself reports ``ENAMETOOLONG``. Both verdicts
    come from the actual bind call, never from a guessed path-length limit.
    """
    if error.errno == errno.ENAMETOOLONG:
        return True
    return error.errno is None and "AF_UNIX path too long" in str(error)


class LegacySocketGuard:
    """Occupy the previous Node endpoint so an old service cannot own the same jobs.

    The new service may not start alongside the legacy owner of the same state
    directory. This guard is an exclusion check only: it never dispatches work and
    never touches anything the old implementation wrote.

    A legitimate state directory can be longer than the platform's AF_UNIX address
    capacity. Such an address cannot name a socket at all, so the retired listener is
    omitted there -- but only once the real ``bind`` failure proves it and only when
    nothing exists at the path. An existing socket or file is never bypassed: it may
    belong to a live legacy service reached through a shorter path alias, and an
    unrepresentable address cannot be probed from here.
    """

    def __init__(self, directory: Path):
        self.path = directory / "service.sock"
        self.stopping = threading.Event()
        self.identity: tuple[int, int] | None = None
        self.thread: threading.Thread | None = None
        #: True when AF_UNIX cannot address this path and no entry existed there.
        self.omitted = False
        self.note: str | None = None
        # A socket object is kept even when it is closed early, so `close` is always
        # safe: an omitted guard has no thread, no listener, no path entry and no id.
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(str(self.path))
        except OSError as cause:
            self.listener.close()
            if _unix_address_unrepresentable(cause):
                self._omit_unrepresentable(cause)
                return
            raise self._existing_legacy_endpoint() from cause
        self.path.chmod(0o600)
        stat = self.path.stat()
        self.identity = (stat.st_dev, stat.st_ino)
        self.listener.listen(16)
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self._serve, name="buddy-legacy-guard", daemon=True)
        self.thread.start()

    def _omit_unrepresentable(self, cause: OSError) -> None:
        """Omit the obsolete listener only where no socket could exist and none does."""
        try:
            os.lstat(self.path)
        except FileNotFoundError:
            self.omitted = True
            self.note = (
                f"AF_UNIX cannot represent {self.path}; the retired legacy listener was omitted "
                "and no service.sock exists"
            )
            return
        except OSError as error:
            raise ServiceError(
                "STALE_LEGACY_SOCKET",
                f"Cannot verify the retired legacy endpoint at {self.path}: {error}",
            ) from cause
        raise ServiceError(
            "STALE_LEGACY_SOCKET",
            "A service.sock exists at a state directory path this platform cannot address with "
            "AF_UNIX; inspect the prior service before cleanup",
        ) from cause

    def _existing_legacy_endpoint(self) -> ServiceError:
        """Classify a failed bind at a representable address exactly as before."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(self.path))
            except (ConnectionRefusedError, FileNotFoundError):
                return ServiceError(
                    "STALE_LEGACY_SOCKET",
                    "Existing service.sock was not removed; inspect the prior service before cleanup",
                )
            return ServiceError(
                "LEGACY_SERVICE_RUNNING",
                "A legacy service still owns this state directory; stop it before starting this version",
            )

    def _serve(self):
        while not self.stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.2)
                reply = {
                    "error": {
                        "code": "MIGRATED",
                        "message": "This state directory is owned by the Python blackboard service; use the buddy CLI",
                    }
                }
                try:
                    raw = connection.recv(8192)
                    try:
                        request = json.loads(raw.split(b"\n", 1)[0])
                        if isinstance(request, dict) and "id" in request:
                            reply["id"] = request["id"]
                    except (ValueError, UnicodeDecodeError):
                        pass
                except (OSError, socket.timeout):
                    pass
                try:
                    connection.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        self.stopping.set()
        self.listener.close()
        if self.thread is not None:
            self.thread.join(timeout=1)
        if self.identity is None:
            # An omitted guard never created a path entry, so it owns nothing to remove.
            return
        try:
            stat = self.path.stat()
            if (stat.st_dev, stat.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


class SupervisorHandle:
    """A supervisor process this daemon created, plus a durable cooperative stop."""

    def __init__(self, directory: Path, worker_id: str):
        self.directory = directory / "workers" / worker_id
        self.worker_id = worker_id
        self.lock_path = self.directory / "supervisor.lock"
        self.stop_request = self.directory / "stop.request"
        self.process: subprocess.Popen | None = None
        self.lock_fd: int | None = None

    def running(self) -> bool:
        """Liveness by lock ownership, never by a stored PID."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        return False

    def start(self, state_dir: Path, log_path: Path) -> bool:
        if self.running():
            return False
        self.stop_request.unlink(missing_ok=True)
        target = runtime.launch_target()
        environment = {
            **os.environ,
            "BUDDY_STATE_DIR": str(state_dir),
            "BUDDY_WORKER_ID": self.worker_id,
        }
        if target["pythonPath"]:
            environment["PYTHONPATH"] = target["pythonPath"]
        else:
            environment.pop("PYTHONPATH", None)
        log_fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            self.process = subprocess.Popen(
                [
                    target["python"],
                    "-m",
                    "buddy.worker.supervisor",
                    "--worker-id",
                    self.worker_id,
                    "--state-dir",
                    str(state_dir),
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                close_fds=True,
            )
            threading.Thread(target=self.process.wait, name="buddy-supervisor-reaper", daemon=True).start()
        finally:
            os.close(log_fd)
        return True

    def request_stop(self) -> bool:
        atomic_json(self.stop_request, {"workerId": self.worker_id, "requestedAt": utc_now(), "requestedBy": "daemon"})
        return True


class Daemon:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.stopping = threading.Event()
        self.started_at = utc_now()
        self.service_id = str(uuid.uuid4())
        self.token = secrets.token_urlsafe(32)
        self.wait_admission = WaitAdmission(_env_int("BUDDY_WAIT_CAPACITY", 32))
        self.store = BoardStore(
            self.directory,
            max_concurrent=_env_int("BUDDY_MAX_CONCURRENT", 1),
            lease_seconds=_env_int("BUDDY_LEASE_SECONDS", 120),
        )
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
        self.dashboard = Dashboard(self.store)
        self.legacy_guard: LegacySocketGuard | None = None
        self.supervisor: SupervisorHandle | None = None
        self.lock_fds: list[int] = []
        self.endpoint_path = self.directory / "control.json"
        self.resume_path = self.directory / "restart.resume.json"
        self.stop_path = self.directory / "stop.request.json"

    # -- lifecycle -----------------------------------------------------------
    def acquire_exclusive(self) -> None:
        """Refuse to start alongside any other owner of this state directory."""
        for name in ("control-daemon.lock", "board-owner.lock"):
            fd = os.open(self.directory / name, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise ServiceError(
                    "ALREADY_RUNNING",
                    "Another owner is already running for this state directory",
                    lock=name,
                )
            self.lock_fds.append(fd)

    def service(self) -> BoardService:
        return BoardService(
            self.store,
            token=self.token,
            control=self.control,
            on_stop=self.on_stop,
            on_restart=self.on_restart,
            dashboard_factory=self.on_dashboard,
            legacy_importer=LegacyImporter(self.store).run,
        )

    def run(self) -> int:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.acquire_exclusive()
        try:
            self.store.initialize()
            self.legacy_guard = LegacySocketGuard(self.directory)
            if self.legacy_guard.omitted:
                # Visible in control.log: the obsolete exclusion listener is absent for
                # a reason that is not a missing legacy service.
                sys.stderr.write(f"buddy: legacy socket guard omitted: {self.legacy_guard.note}\n")
            service = self.service()
            wait_service = WaitService(self.store, self.wait_admission, token=self.token)
            cc.register(
                BuddyControl,
                service,
                name=CONTROL_NAME,
                concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL),
            )
            cc.register(
                BuddyWait,
                wait_service,
                name=WAIT_NAME,
                concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL),
            )
            atomic_json(
                self.endpoint_path,
                {
                    "address": cc.server_address(),
                    "token": self.token,
                    "pid": os.getpid(),
                    "protocol": 2,
                    "contractVersion": CONTRACT_VERSION,
                    "schemaVersion": SCHEMA_VERSION,
                    "serviceId": self.service_id,
                    "startedAt": self.started_at,
                    "runtimeIdentity": self.control["runtime"],
                },
            )
            self.resume_path.unlink(missing_ok=True)
            self.stop_path.unlink(missing_ok=True)
            self.supervisor = SupervisorHandle(self.directory, os.environ.get("BUDDY_WORKER_ID", "local"))
            self.supervisor.start(self.directory, self.directory / "worker.log")
            sweeper = threading.Thread(target=self._sweep, name="buddy-lease-sweeper", daemon=True)
            sweeper.start()
            self.stopping.wait()
            return self.finish()
        finally:
            self.cleanup()

    def _sweep(self) -> None:
        while not self.stopping.wait(LEASE_SWEEP_SECONDS):
            try:
                self.store.mark_expired_leases()
            except Exception:  # pragma: no cover - a sweep must never kill the daemon
                pass

    # -- control actions -----------------------------------------------------
    def on_stop(self, params: dict) -> dict:
        drain = int(params.get("drainSeconds", DRAIN_SECONDS_DEFAULT))
        reason = params.get("reason", "service stop requested")
        self.control["stopping"] = True
        atomic_json(self.stop_path, {"requestedAt": utc_now(), "reason": reason, "drainSeconds": drain})
        queued = self.cancel_queued(reason)
        active = self.cancel_active(reason)
        unresolved = self.drain(drain)
        if self.supervisor is not None:
            self.supervisor.request_stop()
        self.stopping.set()
        return {
            "action": "stop",
            "stopped": not unresolved,
            "cancelledQueued": queued,
            "cancelRequested": active,
            "unresolvedAttempts": unresolved,
            "drainSeconds": drain,
            "note": (
                "Buddy-owned work was asked to stop and the service drained for a bounded interval. Unresolved "
                "attempts keep their receipts and resource claims; their shutdown is explicitly unconfirmed and no "
                "surviving process is assumed stopped. Unrelated dsh sessions were never touched."
            ),
        }

    def on_restart(self, params: dict) -> dict:
        """Detach and let a fresh daemon take over, preserving every worker."""
        drain = int(params.get("drainSeconds", DRAIN_SECONDS_DEFAULT))
        reason = params.get("reason", "service restart requested")
        self.control["restart_requested"] = True
        # Deliberately no cancel and no worker stop: workers survive the daemon.
        atomic_json(
            self.resume_path,
            {"requestedAt": utc_now(), "reason": reason, "serviceId": self.service_id, "drainSeconds": drain},
        )
        self.stopping.set()
        return {
            "action": "restart",
            "restarting": True,
            "workersPreserved": True,
            "resumeFile": str(self.resume_path),
            "note": (
                "The daemon detaches without cancelling owned work. Independent workers keep their child handles "
                "and deadlines while it is down, then reattach by attempt identity and capability."
            ),
        }

    def on_dashboard(self, action: str) -> dict:
        if action == "close":
            return self.dashboard.close()
        if action == "status":
            return self.dashboard.status()
        return self.dashboard.start()

    # -- drain ---------------------------------------------------------------
    def cancel_queued(self, reason: str) -> int:
        count = 0
        for task in self.store.active_work()["queuedTasks"]:
            try:
                self.store.task_cancel({"runId": task["taskId"], "reason": f"service stop: {reason}"})
                count += 1
            except BoardError:
                continue
        return count

    def cancel_active(self, reason: str) -> int:
        count = 0
        for attempt in self.store.active_work()["attempts"]:
            try:
                self.store.task_cancel({"runId": attempt["taskId"], "reason": f"service stop: {reason}"})
                count += 1
            except BoardError:
                continue
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
                "workerId": attempt["workerId"],
                "shutdownConfirmed": attempt["shutdownConfirmed"],
            }
            for attempt in self.store.active_work()["attempts"]
        ]

    # -- teardown ------------------------------------------------------------
    def finish(self) -> int:
        return 0

    def cleanup(self) -> None:
        try:
            cc.shutdown()
        except Exception:  # pragma: no cover - teardown best effort
            pass
        if self.legacy_guard is not None:
            self.legacy_guard.close()
        if self.dashboard is not None:
            self.dashboard.close()
        try:
            value = json.loads(self.endpoint_path.read_text())
            if hmac.compare_digest(value.get("serviceId", ""), self.service_id):
                self.endpoint_path.unlink()
        except (OSError, ValueError):
            pass
        for fd in self.lock_fds:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                continue
        self.lock_fds = []


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def main() -> int:
    directory = get_state_dir()
    daemon = Daemon(directory)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_args: daemon.on_stop({"drainSeconds": DRAIN_SECONDS_DEFAULT, "reason": "signal"}))
    try:
        return daemon.run()
    except ServiceError as error:
        # An actionable, non-destructive failure: never fall back to an old
        # implementation and never rewrite records.
        sys.stderr.write(f"buddy: {error.code}: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
