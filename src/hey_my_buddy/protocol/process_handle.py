"""Owned child process handles and private log descriptors shared by service and buddy."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

from ..private_dirs import ensure_private_dir, open_regular_fd
from .process_identity import COMMAND_STOP_GRACE_SECONDS

class ProcessHandle:
    """One owned child process group or Windows Job.

    The creator owns normal cancellation. A replacement service can stop an
    orphan only after the full durable identity and absent creator are checked.
    """

    def __init__(self, process: subprocess.Popen, *, own_group: bool, log_paths: dict,
                 stop_channel: Literal["stdin", "protocol", "signal"]):
        self.process = process
        self.own_group = own_group
        self.log_paths = log_paths
        self.stop_channel = stop_channel
        self.close_input = process.stdin.close if stop_channel == "stdin" else None
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
        # start_new_session makes the owned child's PID its process-group ID.
        # Keep it even if the leader exits before identity capture.
        self.pgid: int | None = None
        if own_group and self.job is None and os.name != "nt" and process.pid is not None:
            self.pgid = process.pid
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
                stopped, _ = self.terminate(grace_seconds=0)
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

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def identity(self) -> dict:
        from .process_identity import capture_process_identity

        return capture_process_identity(self)

    @property
    def finished(self) -> bool:
        return self.process.poll() is not None

    def _signal_group(self, sig: int) -> None:
        if self.process.pid is None:
            return
        try:
            if self.own_group and self.pgid is not None:
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

    def _wait_for_group(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if self.process.poll() is not None and not self.group_alive():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.025, remaining))

    def terminate(self, *, grace_seconds: float = COMMAND_STOP_GRACE_SECONDS,
                  settle_seconds: float = 2.0) -> tuple[bool, bool]:
        """Request, wait, POSIX TERM, force and observe the entire owned group.

        Protocol owners send their native interrupt (or C-Two stop) before
        calling this primitive. Stdio owners then close input here. A signal
        channel is SIGTERM on POSIX and has no Windows counterpart, so Windows
        children without a channel go straight to Job termination. Returns
        (confirmed, escalated); a reaped leader alone never confirms shutdown.
        """
        with self.stop_lock:
            self.cancel_requested = True
            self.signalled_at = time.monotonic()
            windows = self.job is not None or os.name == "nt"
            if self.stop_channel == "stdin":
                try:
                    self.close_input()
                except OSError:
                    pass
            elif self.stop_channel == "signal" and not windows:
                self._signal_group(signal.SIGTERM)
            if self.stop_channel != "signal" or not windows:
                if self._wait_for_group(grace_seconds):
                    return self.shutdown_confirmed(settle_seconds=0), False
            elif self._wait_for_group(0):
                return self.shutdown_confirmed(settle_seconds=0), False
            if not windows and self.stop_channel != "signal":
                self._signal_group(signal.SIGTERM)
                if self._wait_for_group(1.0):
                    return self.shutdown_confirmed(settle_seconds=0), True
            if self.job is not None:
                self.job.terminate()
            elif windows:
                self.process.kill()
            deadline = time.monotonic() + settle_seconds
            while True:
                if not windows:
                    # A child can join the group while the first KILL is in
                    # flight. Keep stopping that owned group until observed empty.
                    self._signal_group(signal.SIGKILL)
                if self._wait_for_group(min(0.025, max(0.0, deadline - time.monotonic()))):
                    return self.shutdown_confirmed(settle_seconds=0), True
                if time.monotonic() >= deadline:
                    return False, True

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
    ensure_private_dir(Path(paths["stdout"]).parent)
    stdout = open_regular_fd(Path(paths["stdout"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        stderr = open_regular_fd(Path(paths["stderr"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except BaseException as error:
        try:
            os.close(stdout)
        except BaseException as cleanup_error:
            raise error from cleanup_error
        raise
    return stdout, stderr

