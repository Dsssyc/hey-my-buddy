"""Worker supervisor: the independent process that owns one worker's lifetime.

The supervisor is not the daemon and does not depend on the daemon's stdio. It
creates workers, restarts them after an unexpected exit, and stops cooperatively
through a durable request file so a service stop never needs to signal a process it
does not own.
"""
from __future__ import annotations

import argparse
from ... import locking
import os
import signal
import sys
import threading
import time
from pathlib import Path

from ...protocol.client import BoardClient
from .worker import RETIRE_REQUEST_NAME, Worker, fsync_json, supervisor_start_stop_path

RESTART_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 30.0


class Supervisor:
    def __init__(
        self, worker_id: str, state_dir: Path, *, lease_seconds: int = 120, capabilities: tuple[str, ...] = ()
    ):
        self.worker_id = worker_id
        self.state_dir = state_dir
        self.lease_seconds = lease_seconds
        self.capabilities = tuple(capabilities)
        self.stop = threading.Event()
        self.directory = state_dir / "workers" / worker_id
        self.stop_request = self.directory / "stop.request"
        self.start_stop_request = supervisor_start_stop_path(self.directory)
        #: Scale-down intent owned by the pool: this supervisor exits only after the
        #: Worker returns, which happens between attempts and after receipt replay.
        self.retire_request = self.directory / RETIRE_REQUEST_NAME
        self.status_path = self.directory / "supervisor.json"

    def request_stop(self) -> None:
        self.stop.set()

    def stop_file_requested(self) -> bool:
        return (self.stop_request.exists()
                or bool(self.start_stop_request and self.start_stop_request.exists()))

    def publish(self, state: str, **extra) -> None:
        fsync_json(
            self.status_path,
            {
                "workerId": self.worker_id,
                "supervisorPid": os.getpid(),
                "state": state,
                "startedAt": self.started_at,
                "updatedAt": _now(),
                **extra,
            },
        )

    def serve(self, *, max_restarts: int | None = None) -> int:
        self.started_at = _now()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # One supervisor per worker id, decided by lock ownership rather than by a
        # stored PID: a recycled PID can never make a live supervisor look dead or
        # a dead one look alive.
        lock_fd = os.open(self.directory / "supervisor.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            locking.lock(lock_fd, blocking=False)
        except BlockingIOError:
            os.close(lock_fd)
            return 0
        try:
            return self._loop(max_restarts=max_restarts)
        finally:
            locking.unlock(lock_fd)
            os.close(lock_fd)
            if self.start_stop_request:
                self.start_stop_request.unlink(missing_ok=True)

    def _loop(self, *, max_restarts: int | None) -> int:
        self.publish("starting")
        backoff = RESTART_BACKOFF_SECONDS
        restarts = 0
        while not self.stop.is_set() and not self.stop_file_requested():
            # A retire intent does not skip Worker creation: a restarted surplus
            # owner must still replay its receipts and reconcile its startup intents
            # before it may leave. The Worker itself observes the intent only between
            # attempts, so no owned child is ever interrupted.
            worker = Worker(
                self.worker_id,
                self.state_dir,
                client=BoardClient(self.state_dir, autostart=False),
                lease_seconds=self.lease_seconds,
                extra_capabilities=self.capabilities,
                stop=self.stop,
            )
            self.publish("running", workerPid=os.getpid())
            try:
                worker.run()
            except Exception as error:  # a worker crash must not take the supervisor down
                self.publish("restarting", lastError=repr(error))
                backoff = min(MAX_BACKOFF_SECONDS, backoff * 2)
            else:
                backoff = RESTART_BACKOFF_SECONDS
            if self.stop.is_set() or self.stop_file_requested():
                break
            if self.retire_request.exists():
                pending = worker.recovery_pending()
                if not pending:
                    break
                # A crash before reconciliation must not abandon durable evidence: a
                # retain/replay intent keeps retrying until the receipt or startup
                # intent is settled, however long that takes.
                self.publish("retiring", pendingRecovery=pending)
                self.stop.wait(backoff)
                continue
            restarts += 1
            if max_restarts is not None and restarts >= max_restarts:
                break
            self.stop.wait(backoff)
        if self.retire_request.exists() and not self.stop_file_requested():
            self.publish("retired")
        else:
            self.publish("stopped")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy independent worker supervisor")
    parser.add_argument("--worker-id", default=os.environ.get("BUDDY_WORKER_ID", "local"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--max-restarts", type=int, default=None)
    parser.add_argument(
        "--capabilities",
        default="",
        help="Comma-separated extra capabilities this worker advertises, on top of the built-in adapters",
    )
    arguments = parser.parse_args(argv)
    state_dir = Path(arguments.state_dir)
    capabilities = tuple(item.strip() for item in arguments.capabilities.split(",") if item.strip())
    supervisor = Supervisor(
        arguments.worker_id, state_dir, lease_seconds=arguments.lease_seconds, capabilities=capabilities
    )
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_args: supervisor.request_stop())
    return supervisor.serve(max_restarts=arguments.max_restarts)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
