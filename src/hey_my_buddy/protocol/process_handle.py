"""Owned child process handles and private log descriptors shared by service and buddy."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .process_identity import COMMAND_STOP_GRACE_SECONDS

class ProcessHandle:
    """One owned child process group or Windows Job.

    The creator owns normal cancellation. A replacement service can stop an
    orphan only after the full durable identity and absent creator are checked.
    """

    def __init__(self, process: subprocess.Popen, *, own_group: bool, log_paths: dict):
        self._initialize_owned(process, own_group=own_group, log_paths=log_paths)
        try:
            # Capture this before an adapter can poll and reap a fast child.
            from .process_identity import _creation_time, record_native_process_spawn

            self.process_started_at = (_creation_time(process.pid)
                                       if type(process.pid) is int and process.pid > 0 else None)
            intent = vars(process).get("_buddy_native_intent")
            if intent is not None:
                record_native_process_spawn(intent, self)
        except BaseException as error:
            # Popen has returned: this object still owns the child even when
            # identity capture or durable journaling fails before __init__ returns.
            from .owned_process import ProcessCreationEvidence

            try:
                self.terminate(grace_seconds=0)
                process.wait(timeout=2)
                stopped = self.shutdown_confirmed(settle_seconds=0)
            except BaseException:
                stopped = False
            evidence = ProcessCreationEvidence(created=True, shutdown_confirmed=stopped,
                                               exit_code=process.returncode)
            error.execution_handle = self
            if not stopped:
                failure = RuntimeError("owned child shutdown is unconfirmed after handle construction failure")
                failure.owned_popen_evidence = evidence
                failure.execution_handle = self
                raise failure from error
            error.owned_popen_evidence = evidence
            raise

    def _initialize_owned(self, process: subprocess.Popen, *, own_group: bool,
                          log_paths: dict) -> None:
        self.process = process
        self.own_group = own_group
        self.log_paths = log_paths
        self.cancel_requested = False
        self.signalled_at: float | None = None
        self.stop_lock = threading.RLock()
        self.stop_initiator: str | None = None
        self.grace_deadline: float | None = None
        self.grace_expired = False
        self.force_stop_requested = False
        self.wrap_up_started = False
        self.role_run_control: dict | None = None
        self.role_run_identity = None
        self.role_run_active_control: dict | None = None
        self.role_run_active_identity = None
        self.job = vars(process).get("_buddy_job")
        # Capture the process-group id while the leader is alive. Looking it up
        # later with os.getpgid(child.pid) would fail as soon as the leader exits,
        # even while its descendants are still running, and would then report a
        # live group as stopped.
        self.pgid: int | None = None
        if own_group and self.job is None and os.name != "nt" and process.pid is not None:
            try:
                self.pgid = os.getpgid(process.pid)
            except OSError:
                self.pgid = process.pid

    @classmethod
    def adopt_unregistered(cls, process: subprocess.Popen, *, own_group: bool,
                           log_paths: dict) -> "ProcessHandle":
        """Keep a Popen reachable when constructing its normal handle failed."""
        handle = cls.__new__(cls)
        handle._initialize_owned(process, own_group=own_group, log_paths=log_paths)
        handle.process_started_at = None
        return handle

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def identity(self) -> dict:
        from .process_identity import capture_process_identity

        return capture_process_identity(self)

    @property
    def finished(self) -> bool:
        return self.process.poll() is not None

    def signal_group(self, sig: int) -> None:
        if self.process.pid is None:
            return
        try:
            if self.job is not None:
                if sig == signal.SIGTERM and hasattr(signal, "CTRL_BREAK_EVENT"):
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                elif sig != signal.SIGTERM:
                    self.job.terminate()
            elif self.own_group and self.pgid is not None:
                os.killpg(self.pgid, sig)
            else:
                self.process.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError, ValueError):
            pass

    def group_alive(self) -> bool:
        """Remain live unless the owned group is observed gone, including descendants."""
        if self.job is not None:
            try:
                return self.job.active() != 0
            except OSError:
                return True
        if self.own_group and os.name == 'nt':
            return True  # A root PID without a held Job cannot prove tree shutdown.
        if self.process.pid is None:
            return False
        try:
            if self.own_group and self.pgid is not None:
                os.killpg(self.pgid, 0)
            else:
                os.kill(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # The group exists but belongs to another user: still alive.
            return True
        except OSError:
            # An unavailable process-group observation is not termination evidence.
            return True

    def terminate(self, *, grace_seconds: float = COMMAND_STOP_GRACE_SECONDS) -> None:
        """Ask the child to stop, then terminate only its owned group or Job."""
        self.cancel_requested = True
        self.signalled_at = time.monotonic()
        self.signal_group(signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None and not self.group_alive():
                return
            time.sleep(0.05)
        if self.job is not None:
            self.signal_group(-1)
        elif os.name == 'nt':
            self.process.kill()
        else:
            self.signal_group(signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None and not self.group_alive():
                return
            time.sleep(0.05)

    def wait(self, timeout: float | None) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def shutdown_confirmed(self, settle_seconds: float = 2.0) -> bool:
        """True only when the owned group is confirmed gone, never inferred from a PID."""
        if self.process.poll() is None:
            return False
        deadline = time.monotonic() + settle_seconds
        while time.monotonic() < deadline:
            if not self.group_alive():
                if self.job is not None:
                    try:
                        self.job.close(confirmed=True)
                    except OSError:
                        return False
                return True
            time.sleep(0.025)
        stopped = not self.group_alive()
        if stopped and self.job is not None:
            try:
                self.job.close(confirmed=True)
            except OSError:
                return False
        return stopped


def open_logs(paths: dict) -> tuple[int, int]:
    Path(paths["stdout"]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stdout = os.open(paths["stdout"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        stderr = os.open(paths["stderr"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except BaseException as error:
        try:
            os.close(stdout)
        except BaseException as cleanup_error:
            raise error from cleanup_error
        raise
    return stdout, stderr

