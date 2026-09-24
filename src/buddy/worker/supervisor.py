"""Worker supervisor: the independent process that owns one worker's lifetime.

The supervisor is not the daemon and does not depend on the daemon's stdio. It
creates workers, restarts them after an unexpected exit, and stops cooperatively
through a durable request file so a service stop never needs to signal a process it
does not own.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import threading
import time
from pathlib import Path

from ..client import BoardClient
from ..transport import get_state_dir
from .worker import Worker, fsync_json

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
        self.status_path = self.directory / "supervisor.json"

    def request_stop(self) -> None:
        self.stop.set()

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
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return 0
        try:
            return self._loop(max_restarts=max_restarts)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _loop(self, *, max_restarts: int | None) -> int:
        self.publish("starting")
        backoff = RESTART_BACKOFF_SECONDS
        restarts = 0
        while not self.stop.is_set() and not self.stop_request.exists():
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
                self.stop.wait(backoff)
                backoff = min(MAX_BACKOFF_SECONDS, backoff * 2)
            else:
                backoff = RESTART_BACKOFF_SECONDS
            if self.stop.is_set() or self.stop_request.exists():
                break
            restarts += 1
            if max_restarts is not None and restarts >= max_restarts:
                break
            self.stop.wait(backoff)
        self.publish("stopped")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy independent worker supervisor")
    parser.add_argument("--worker-id", default=os.environ.get("BUDDY_WORKER_ID", "local"))
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--max-restarts", type=int, default=None)
    parser.add_argument(
        "--capabilities",
        default="",
        help="Comma-separated extra capabilities this worker advertises, on top of the built-in adapters",
    )
    arguments = parser.parse_args(argv)
    state_dir = get_state_dir(arguments.state_dir)
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
