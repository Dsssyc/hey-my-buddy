"""Service lifecycle: exclusive ownership, controlled detach, bounded drain.

The daemon is the only writer of authoritative state, but it never owns worker
processes: it may *request* that a worker stops, and only the worker holding a child
handle sends OS signals. A restarted daemon reconciles workers by attempt identity
and capability, never by PID.
"""
from __future__ import annotations

import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid

import c_two as cc

from . import rpc_config
from . import runtime
from . import scheduling
from .console import Console
from .contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from .db import SCHEMA_VERSION, utc_now
from .errors import BoardError
from .service import BoardService, WaitAdmission, WaitService
from .store import BoardStore
from .transport import ServiceError, get_state_dir
from .worker.worker import RETIRE_REQUEST_NAME, ReceiptSpool

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


class SupervisorHandle:
    """A supervisor process this daemon created, plus durable cooperative intents."""

    def __init__(self, directory: Path, worker_id: str):
        self.directory = directory / "workers" / worker_id
        self.worker_id = worker_id
        self.lock_path = self.directory / "supervisor.lock"
        self.stop_request = self.directory / "stop.request"
        #: Scale-down intent: observed by the Worker between attempts only, so it can
        #: never cancel an owned child or drop an undelivered receipt.
        self.retire_request = self.directory / RETIRE_REQUEST_NAME
        self.process: subprocess.Popen | None = None
        self.lock_fd: int | None = None

    def prepare(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def running(self) -> bool:
        """Liveness by lock ownership, never by a stored PID."""
        self.prepare()
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

    def alive(self) -> bool:
        """True while this slot is served, or its spawned process has not exited.

        The spawned ``Popen`` closes the window between ``Popen`` returning and the
        supervisor acquiring its lifetime lock, so a reconcile can never mistake a
        just-started supervisor for a missing one and spawn a duplicate.
        """
        if self.process is not None and self.process.poll() is None:
            return True
        return self.running()

    def withdraw_retire(self) -> bool:
        """Cancel a pending scale-down intent for a slot that is wanted again."""
        self.retire_request.unlink(missing_ok=True)
        return True

    def start(self, state_dir: Path, log_path: Path, *, retirement: bool = False) -> bool:
        if not retirement:
            # An explicit start/new daemon supersedes the previous stop even if
            # that owner still holds its lock. It may finish exiting afterward;
            # reconcile must then be able to refill the slot without a duplicate.
            self.stop_request.unlink(missing_ok=True)
        if self.running():
            # The slot is already served. If this pool had asked it to retire under a
            # lower limit, that intent is withdrawn here instead of losing the worker.
            if not retirement:
                self.withdraw_retire()
            return False
        if retirement:
            # Replay-only mode: the retire intent is durable before the process
            # exists, so the restarted Worker reconciles what it owes and can never
            # claim new work. A pending stop request is left in place on purpose.
            if not self.retire_request.exists():
                self.request_retire()
        else:
            self.retire_request.unlink(missing_ok=True)
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

    def request_retire(self) -> bool:
        atomic_json(
            self.retire_request,
            {"workerId": self.worker_id, "requestedAt": utc_now(), "requestedBy": "daemon-pool"},
        )
        return True


class WorkerPool:
    """The daemon-managed generic worker pool.

    One independent supervisor per configured execution slot: the machine-wide
    ``BUDDY_MAX_CONCURRENT`` ceiling, under stable IDs derived from the configured
    worker prefix (``local``, ``local-2``, ``local-3``). This is ordinary
    queue/worker infrastructure, not a second scheduler: the pool only starts
    processes, recognizes its own already-running supervisors by their lifetime
    lock, and asks them to stop through durable request files. It never signals a
    process it does not own.

    Ownership is recorded in a private manifest of the exact IDs this pool started,
    durably *before* the first process exists. A ``local-99`` folder an operator
    created by hand is not inferred from its name, is never retired and never
    appears in the pool report. Manifest values pass the registry's own worker-ID
    validation; a malformed JSON value is dropped instead of becoming a path.

    Lowering the configured limit never cancels live work. A surplus supervisor
    receives a *retire* intent only while it owns no unresolved attempt and holds no
    receipt or startup intent still waiting for reconciliation; the owning Worker
    observes that intent between attempts and runs replay-only retirement, so an
    attempt that raced the intent still finishes and delivers. A missing surplus
    owner with unresolved evidence is retained, reported and restarted in that
    replay-only mode; a missing process is never treated as stopped work.

    A desired slot that disappears is restarted after a bounded backoff, deduplicated
    by the supervisor lifetime lock and by the spawned process state. A durable stop
    request is honored instead: an explicitly stopped ID stays down and is reported
    as unavailable until a deliberate ``worker-start`` or the next daemon startup.
    """

    #: A missing desired supervisor is re-spawned after this delay, doubling per
    #: consecutive failed attempt up to the cap. The lifetime lock and the spawned
    #: ``Popen`` are the dedupe facts; the delay only bounds a crash-restart storm.
    SPAWN_BACKOFF_SECONDS = 5.0
    MAX_SPAWN_BACKOFF_SECONDS = 60.0
    #: How long a missing surplus owner waits between replay-only restarts.
    REPLAY_BACKOFF_SECONDS = 5.0

    def __init__(self, directory: Path, *, prefix: object, total_limit: int):
        self.directory = Path(directory)
        self.total_limit = max(1, int(total_limit))
        self.worker_ids = scheduling.pool_worker_ids(prefix, self.total_limit)
        #: The effective base ID: an invalid configured prefix falls back to the
        #: default instead of ever reaching a directory name or an argv value.
        self.prefix = self.worker_ids[0]
        self.configured_prefix_valid = scheduling.valid_worker_id(prefix) is not None
        self.log_path = self.directory / "worker.log"
        self.manifest_path = self.directory / "worker-pool.json"
        self._manifest_dirty = False
        self._manifest = self._read_manifest()
        self._handles: dict[str, SupervisorHandle] = {}
        self._missing: dict[str, int] = {}
        self._reclaimable: set[str] = set()
        self._spawn_backoff: dict[str, float] = {}
        self._spawn_failures: dict[str, int] = {}
        self._replay_backoff: dict[str, float] = {}
        self._surplus_running: list[str] = []
        self._report: dict = self._compose_report([], [], [], [], [])

    def handle(self, worker_id: str) -> SupervisorHandle:
        handle = self._handles.get(worker_id)
        if handle is None:
            handle = SupervisorHandle(self.directory, worker_id)
            self._handles[worker_id] = handle
        return handle

    # -- ownership manifest --------------------------------------------------
    def _read_manifest(self) -> list[str]:
        try:
            value = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            return []
        ids = value.get("workerIds") if isinstance(value, dict) else None
        if not isinstance(ids, list):
            return []
        # Only values the registry itself would accept become pool identities. A
        # number, object, list or malformed string is dropped, never stringified.
        cleaned = list(
            dict.fromkeys(worker_id for item in ids if (worker_id := scheduling.valid_worker_id(item)) is not None)
        )
        self._manifest_dirty = cleaned != ids
        return cleaned

    def _persist_manifest(self) -> None:
        atomic_json(
            self.manifest_path,
            {"prefix": self.prefix, "workerIds": list(self._manifest), "updatedAt": utc_now()},
        )
        self._manifest_dirty = False

    def _adopt_manifest(self) -> None:
        """Record the exact owned IDs durably before any supervisor is spawned."""
        merged = list(dict.fromkeys([*self.worker_ids, *self._manifest]))
        if merged != self._manifest or self._manifest_dirty or not self.manifest_path.exists():
            self._manifest = merged
            self._persist_manifest()

    def managed_ids(self) -> list[str]:
        """Desired slots plus the pool members recorded in the manifest."""
        return list(dict.fromkeys([*self.worker_ids, *self._manifest]))

    def _compose_report(self, surplus_running, draining, retained, unstarted, stopped) -> dict:
        return {
            "prefix": self.prefix,
            "configuredPrefixValid": self.configured_prefix_valid,
            "totalLimit": self.total_limit,
            "workerIds": list(self.worker_ids),
            "unstartedWorkerIds": list(unstarted),
            "stoppedWorkerIds": list(stopped),
            "surplusWorkerIds": list(surplus_running),
            "surplusDraining": list(draining),
            "surplusRetained": [dict(row) for row in retained],
        }

    def report(self) -> dict:
        return {**self._report, "surplusRetained": [dict(row) for row in self._report["surplusRetained"]]}

    # -- evidence ------------------------------------------------------------
    def _local_evidence(self, worker_id: str) -> dict:
        """Replayable local evidence: durable receipts and named startup intents."""
        spool = ReceiptSpool(self.directory, worker_id)
        receipts = spool.pending()
        intent = spool.read_startup()
        attempts = [intent["attemptId"]] if isinstance(intent, dict) and intent.get("attemptId") else []
        return {
            "pendingReceipts": len(receipts),
            "startupIntents": attempts,
            "recoverable": bool(receipts or attempts),
        }

    @staticmethod
    def _retained_row(worker_id: str, attempts: list[str], evidence: dict, *, running: bool) -> dict:
        return {
            "workerId": worker_id,
            "attemptId": attempts[0] if attempts else None,
            "unresolvedAttempts": len(attempts),
            "pendingReceipts": evidence["pendingReceipts"],
            "startupIntents": len(evidence["startupIntents"]),
            "replayable": evidence["recoverable"],
            "running": running,
        }

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> list[str]:
        """Start one supervisor per configured slot; already-running IDs are kept."""
        self._adopt_manifest()
        started = []
        for worker_id in self.worker_ids:
            handle = self.handle(worker_id)
            if handle.retire_request.exists() and handle.alive():
                # The slot was asked to retire under a lower limit and may already be
                # exiting: withdrawing the intent keeps it reclaimable until reconcile
                # sees it running again, so a wanted slot never silently disappears.
                self._reclaimable.add(worker_id)
            if handle.start(self.directory, self.log_path):
                started.append(worker_id)
                self._spawn_backoff[worker_id] = time.monotonic() + self.SPAWN_BACKOFF_SECONDS
        return started

    def _spawn_delay(self, worker_id: str) -> float:
        failures = self._spawn_failures.get(worker_id, 0) + 1
        self._spawn_failures[worker_id] = failures
        return min(self.MAX_SPAWN_BACKOFF_SECONDS, self.SPAWN_BACKOFF_SECONDS * (2 ** (failures - 1)))

    def _ensure_desired(self) -> tuple[list[str], list[str], list[str]]:
        """Keep every configured slot alive; report the ones not yet serving.

        A durable stop request is honored, never overwritten: an explicitly stopped
        ID stays down for the lifetime of this daemon instead of being silently
        resurrected on the next sweep. Only a deliberate ``worker-start`` (which
        clears the request before spawning) or the next daemon startup resumes it.
        A genuine crash leaves no stop request, so it is still restarted.
        """
        started: list[str] = []
        unstarted: list[str] = []
        stopped: list[str] = []
        now = time.monotonic()
        for worker_id in self.worker_ids:
            handle = self.handle(worker_id)
            if handle.alive():
                # Scale-up: a pending scale-down intent for a wanted slot is withdrawn.
                handle.withdraw_retire()
                self._reclaimable.discard(worker_id)
                self._spawn_failures.pop(worker_id, None)
                self._spawn_backoff.pop(worker_id, None)
                continue
            if handle.stop_request.exists():
                # Explicit stop: keep the request, report the configured slot as
                # unavailable and do not spawn a replacement for it.
                unstarted.append(worker_id)
                stopped.append(worker_id)
                continue
            # A configured slot that is not running is wanted again: a crashed
            # supervisor, a retire that raced the scale-up, or a start that failed
            # before the lock was taken. Never inferred dead from a stored PID.
            if now >= self._spawn_backoff.get(worker_id, 0.0):
                if handle.start(self.directory, self.log_path):
                    started.append(worker_id)
                self._reclaimable.discard(worker_id)
                self._spawn_backoff[worker_id] = now + self._spawn_delay(worker_id)
            if not handle.alive():
                unstarted.append(worker_id)
        return started, unstarted, stopped

    def _reconcile_manifest(self, store: BoardStore) -> None:
        desired = set(self.worker_ids)
        kept: list[str] = []
        for worker_id in self.managed_ids():
            if worker_id in desired:
                self._missing.pop(worker_id, None)
                kept.append(worker_id)
                continue
            evidence = self._local_evidence(worker_id)
            unresolved = store.unresolved_worker_attempt(worker_id)
            if self.handle(worker_id).alive() or unresolved is not None or evidence["recoverable"]:
                # A live owner or unresolved evidence keeps the entry. Missing process
                # never means attempts stopped, so it is never forgotten while the
                # board or this worker's own disk still holds recoverable facts.
                self._missing.pop(worker_id, None)
                kept.append(worker_id)
                continue
            # One missing sample can be a supervisor between processes. A genuinely
            # gone member with nothing recoverable is forgotten on the next reconcile,
            # so its folder name is never treated as ours after the fact.
            misses = self._missing.get(worker_id, 0) + 1
            self._missing[worker_id] = misses
            if misses < 2:
                kept.append(worker_id)
            else:
                self._missing.pop(worker_id, None)
        if kept != self._manifest or not self.manifest_path.exists():
            self._manifest = kept
            self._persist_manifest()

    def _replay_surplus(self, worker_id: str) -> bool:
        """Restart a missing surplus owner in replay-only retirement mode."""
        handle = self.handle(worker_id)
        if handle.stop_request.exists():
            # A stop was requested for this slot; a replay must not resurrect it.
            return False
        now = time.monotonic()
        if now < self._replay_backoff.get(worker_id, 0.0) or handle.alive():
            return False
        # The retire intent is durable before the process exists, so the restarted
        # Worker only replays/reconciles and can never claim new work.
        handle.request_retire()
        started = handle.start(self.directory, self.log_path, retirement=True)
        self._replay_backoff[worker_id] = now + self.REPLAY_BACKOFF_SECONDS
        return started

    def reconcile(self, store: BoardStore) -> dict:
        """Report the pool, keep desired slots alive and drain surplus safely."""
        self._adopt_manifest()
        started, unstarted, stopped = self._ensure_desired()
        self._reconcile_manifest(store)
        surplus = [worker_id for worker_id in self.managed_ids() if worker_id not in self.worker_ids]
        running, draining, retained = [], [], []
        for worker_id in surplus:
            handle = self.handle(worker_id)
            attempts = store.unresolved_worker_attempts(worker_id)
            evidence = self._local_evidence(worker_id)
            if handle.alive():
                running.append(worker_id)
                if not attempts and not evidence["recoverable"]:
                    # A retire intent, never a stop: an attempt that raced this check
                    # keeps its child and its receipt.
                    handle.request_retire()
                    draining.append(worker_id)
                else:
                    retained.append(self._retained_row(worker_id, attempts, evidence, running=True))
                continue
            if attempts or evidence["recoverable"]:
                # Missing process, but the board or this worker's own disk still holds
                # facts: retain and report the slot. Only locally replayable evidence
                # (a receipt or a named startup intent) starts a replay-only
                # supervisor; a DB-only unresolved attempt with no child handle keeps
                # its unknown state instead of launching a doomed process forever.
                retained.append(self._retained_row(worker_id, attempts, evidence, running=False))
                if evidence["recoverable"]:
                    self._replay_surplus(worker_id)
        self._surplus_running = running
        self._report = self._compose_report(running, draining, retained, unstarted, stopped)
        return self.report()

    def request_stop(self) -> list[str]:
        """Ask every managed worker — desired slots and tracked surplus — to stop."""
        requested = []
        for worker_id in self.managed_ids():
            handle = self.handle(worker_id)
            handle.prepare()
            handle.request_stop()
            requested.append(worker_id)
        return requested


class Daemon:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.stopping = threading.Event()
        self.started_at = utc_now()
        self.service_id = str(uuid.uuid4())
        self.token = secrets.token_urlsafe(32)
        wait_capacity = _env_int("BUDDY_WAIT_CAPACITY", 32)
        if wait_capacity > rpc_config.MAX_WAIT_CAPACITY:
            raise BoardError("INVALID_ARGUMENT", "BUDDY_WAIT_CAPACITY cannot exceed 48; control operations reserve the remaining RPC callbacks")
        self.wait_admission = WaitAdmission(wait_capacity)
        self._cleanup_lock = threading.Lock()
        self._cleanup_pending: set[str] = set()
        self.store = BoardStore(
            self.directory,
            max_concurrent=_env_int("BUDDY_MAX_CONCURRENT", scheduling.TOTAL_CONCURRENCY_DEFAULT),
            lease_seconds=_env_int("BUDDY_LEASE_SECONDS", 120),
        )
        #: One generic supervisor per slot of the machine-wide ceiling. Routing and
        #: execution share it and the same per-family counters. Existing supervisors
        #: are reused by lock ownership.
        self.pool = WorkerPool(
            self.directory,
            prefix=os.environ.get("BUDDY_WORKER_ID", "local"),
            total_limit=self.store.max_concurrent,
        )
        self.control = {
            "service_id": self.service_id,
            "contract_version": CONTRACT_VERSION,
            "protocol": 2,
            "schema_version": SCHEMA_VERSION,
            "wait_capacity": self.wait_admission.capacity,
            "wait_admission": self.wait_admission,
            "worker_pool": self.pool.report(),
            "stopping": False,
            "restart_requested": False,
            "runtime": runtime.runtime_identity(),
        }
        #: Created in ``run`` once the control resource exists; started lazily.
        self.console: Console | None = None
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

    def queue_workspace_cleanup(self, run_id: str) -> None:
        # The queue is only an optimization. Existing accepted workspaces remain
        # discoverable by storage plan after a crash or daemon restart.
        with self._cleanup_lock:
            self._cleanup_pending.add(run_id)

    def _cleanup_accepted_workspaces(self) -> None:
        if (self.directory / "upgrade.json").exists():
            return
        from .storage import cleanup_accepted_workspace
        with self._cleanup_lock:
            pending = tuple(self._cleanup_pending)
            self._cleanup_pending.clear()
        for run_id in pending:
            try:
                cleanup_accepted_workspace(self.store, run_id)
            except (BoardError, OSError):
                # Guard refusals preserve the workspace and its durable plan.
                pass

    def service(self) -> BoardService:
        return BoardService(
            self.store,
            token=self.token,
            control=self.control,
            on_stop=self.on_stop,
            on_restart=self.on_restart,
            console_factory=self.on_console,
            on_accepted=self.queue_workspace_cleanup,
        )

    def run(self) -> int:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.acquire_exclusive()
        try:
            self.store.initialize()
            service = self.service()
            self.console = Console(self.store, service)
            self.console.start(issue_ticket=False)
            wait_service = WaitService(self.store, self.wait_admission, token=self.token)
            # Buddy's private C-Two profile goes in before the first register: the
            # pool, reassembly and execution capacity are set through C-Two's public
            # overrides, never through inherited environment variables.
            self.control["rpc_profile"] = rpc_config.configure_server()
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
            # A fresh daemon reserves the configured slots; a restarted daemon finds
            # the same supervisor IDs already running and never duplicates them.
            self.pool.start()
            self._reconcile_pool()
            sweeper = threading.Thread(target=self._sweep, name="buddy-lease-sweeper", daemon=True)
            sweeper.start()
            self.stopping.wait()
            return self.finish()
        finally:
            self.cleanup()

    def _reconcile_pool(self) -> None:
        """Refresh pool reporting and drain surplus supervisors that are safe."""
        if self.stopping.is_set() or self.control.get("stopping") or self.control.get("restart_requested"):
            # A stop/restart was requested: never resurrect a managed slot while the
            # service is leaving, or a detached supervisor would outlive the owner.
            return
        try:
            self.control["worker_pool"] = self.pool.reconcile(self.store)
        except Exception:  # pragma: no cover - reporting must never stop the daemon
            pass

    def _sweep(self) -> None:
        while not self.stopping.wait(LEASE_SWEEP_SECONDS):
            try:
                self.store.mark_expired_leases()
            except Exception:  # pragma: no cover - a sweep must never kill the daemon
                pass
            self._reconcile_pool()
            self._cleanup_accepted_workspaces()

    # -- control actions -----------------------------------------------------
    def on_stop(self, params: dict) -> dict:
        drain = int(params.get("drainSeconds", DRAIN_SECONDS_DEFAULT))
        reason = params.get("reason", "service stop requested")
        self.control["stopping"] = True
        atomic_json(self.stop_path, {"requestedAt": utc_now(), "reason": reason, "drainSeconds": drain})
        governed, governed_task_ids = self.store.workflow.cancel_for_service_stop(f"service stop: {reason}")
        queued = governed["queued"] + self.cancel_queued(reason, exclude=governed_task_ids)
        active = governed["active"] + self.cancel_active(reason, exclude=governed_task_ids)
        unresolved = self.drain(drain)
        # Every managed pool worker — including a surplus supervisor retained from a
        # higher limit — receives a cooperative stop request; none is signalled.
        workers_asked = self.pool.request_stop()
        self.stopping.set()
        return {
            "action": "stop",
            "stopped": not unresolved,
            "cancelledQueued": queued,
            "cancelRequested": active,
            "unresolvedAttempts": unresolved,
            "workersAskedToStop": workers_asked,
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

    def on_console(self, params: dict) -> dict:
        if self.console is None:  # pragma: no cover - the console exists once run() registered the resource
            raise ServiceError("SERVICE_UNAVAILABLE", "The console is not available in this service state")
        if params["action"] == "close":
            return self.console.close(expected_console_id=params.get("expectedConsoleId"))
        if params["action"] == "status":
            return self.console.status()
        return self.console.start()

    # -- drain ---------------------------------------------------------------
    def cancel_queued(self, reason: str, *, exclude: set[str] | None = None) -> int:
        count = 0
        for task in self.store.active_work()["queuedTasks"]:
            if exclude and task["taskId"] in exclude:
                continue
            try:
                self.store.task_cancel({"runId": task["taskId"], "reason": f"service stop: {reason}"})
                count += 1
            except BoardError:
                continue
        return count

    def cancel_active(self, reason: str, *, exclude: set[str] | None = None) -> int:
        count = 0
        for attempt in self.store.active_work()["attempts"]:
            if exclude and attempt["taskId"] in exclude:
                continue
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
        if self.console is not None:
            self.console.close()
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
    except BoardError as error:
        atomic_json(directory / "startup-error.json", {"pid":os.getpid(), "code":error.code, "message":str(error)})
        # An actionable, non-destructive failure: never fall back to an old
        # implementation and never rewrite records.
        sys.stderr.write(f"buddy: {error.code}: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
