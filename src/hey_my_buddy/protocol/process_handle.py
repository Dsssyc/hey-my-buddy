"""Owned child process handles and private log descriptors shared by service and buddy."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Literal

from ..private_dirs import ensure_private_dir, open_regular_fd
from .process_identity import COMMAND_STOP_GRACE_SECONDS
from .process_members import SessionProcesses
from .process_exit import child_exit_code

class ProcessHandle:
    """One owned POSIX session and observed descendants, or Windows Job.

    The creator owns normal cancellation. A replacement service can stop an
    orphan only after the full durable identity and absent creator are checked.
    """

    def __init__(self, process: subprocess.Popen, *, log_paths: dict,
                 stop_channel: Literal["stdin", "protocol", "signal"],
                 close_input: Callable[[], None] | None = None):
        self.process = process
        self.log_paths = log_paths
        self.stop_channel = stop_channel
        self.close_input = close_input
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
        self._members = None
        self.process_started_at = None
        self._exit_code = None
        self._confirmed = False
        # start_new_session makes the owned child's PID its session ID.
        # Keep it even if the leader exits before identity capture.
        self.session_id: int | None = None
        if self.job is None and os.name != "nt" and process.pid is not None:
            self.session_id = process.pid
        try:
            from .process_identity import _creation_time, record_native_process_spawn

            self.process_started_at = (_creation_time(process.pid)
                                       if type(process.pid) is int and process.pid > 0 else None)
            if self.job is None and os.name != "nt" and self.process_started_at is not None:
                self._members = SessionProcesses(process.pid, self.process_started_at,
                                                 session=self.session_id, lock=self.stop_lock)
            if stop_channel == "stdin":
                if process.stdin is None:
                    raise ValueError("stdin stop channel requires a pipe")
                if close_input is None:
                    self.close_input = process.stdin.close
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
        return self.poll() is not None

    def observe(self) -> tuple[int | None, str]:
        """Read the root exit and scan its member boundary once, under one lock."""
        with self.stop_lock:
            if self.job is not None or os.name == "nt":
                code = self.process.poll()
            else:
                if self._confirmed:
                    return self._exit_code, "gone"
                if self._exit_code is None:
                    self._exit_code = (self.process.returncode if self.process.returncode is not None
                                       else child_exit_code(self.pid))
                code = self._exit_code
            observation = self._session_observation()
            if code is not None and observation == "gone" and not self._confirmed:
                if self.job is None and os.name != "nt":
                    self.process.wait()
                self._confirmed = True
            return code, observation

    def poll(self) -> int | None:
        """Observe exit without releasing the PID before membership is empty."""
        return self.observe()[0]

    def _signal_session(self, sig: int) -> None:
        with self.stop_lock:
            if self._members is None:
                # The unreaped child is ours even if creation-time capture failed.
                # Popen checks its held child before signalling; absence of member
                # evidence still prevents shutdown confirmation.
                self.process.send_signal(sig)
            else:
                self._members.signal(sig)

    def session_alive(self) -> bool:
        return self.session_observation() != "gone"

    def session_observation(self) -> str:
        observation = self.observe()[1]
        return "unknown" if observation == "empty" else observation

    def _session_observation(self) -> str:
        if self._confirmed:
            return "gone"
        if self.job is not None:
            try:
                active = self.job.active()
                return "unknown" if active is None else "gone" if active == 0 else "alive"
            except OSError:
                return "unknown"
        if os.name == "nt":
            return "unknown"  # A root PID without a held Job cannot prove tree shutdown.
        if self._members is None:
            return "unknown"
        try:
            observation = self._members.observe()
            return "gone" if observation == "identity-absent" else observation
        except OSError:
            return "unknown"

    def _wait_for_session(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            code, observation = self.observe()
            if code is not None and observation == "gone":
                return True
            if observation == "empty":
                continue  # The next PID list starts after the first scan finished.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.025, remaining))

    def terminate(self, *, grace_seconds: float = COMMAND_STOP_GRACE_SECONDS,
                  settle_seconds: float = 2.0) -> tuple[bool, bool]:
        """Request, wait, POSIX TERM, force and observe the ownership boundary."""
        with self.stop_lock:
            self.cancel_requested = True
            self.signalled_at = time.monotonic()
            windows = self.job is not None or os.name == "nt"
            if not windows:
                self.observe()  # Admit members before the first stop request.
            if self.stop_channel == "stdin" and self.close_input is not None:
                try:
                    self.close_input()
                except OSError:
                    pass
            elif self.stop_channel == "signal" and not windows:
                self._signal_session(signal.SIGTERM)
            if self.stop_channel != "signal" or not windows:
                # A root exit ends its grace even when tools still live.
                self.wait(grace_seconds)
            if self._wait_for_session(0):
                return self.shutdown_confirmed(settle_seconds=0), False
            if not windows and self.stop_channel != "signal":
                self._signal_session(signal.SIGTERM)
                if self._wait_for_session(1.0):
                    return self.shutdown_confirmed(settle_seconds=0), True
            if self.job is not None:
                self.job.terminate()
            elif windows:
                self.process.kill()
            deadline = time.monotonic() + settle_seconds
            while True:
                code, observation = self.observe()
                if code is not None and observation == "gone":
                    return self.shutdown_confirmed(settle_seconds=0), True
                if not windows:
                    self._signal_session(signal.SIGKILL)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, True
                time.sleep(min(0.025, remaining))

    def wait(self, timeout: float | None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(0.025 if deadline is None else min(0.025, max(0, deadline - time.monotonic())))

    def _close_confirmed_job(self) -> bool:
        if self.job is not None:
            try:
                self.job.close(confirmed=True)
            except OSError:
                return False
        return True

    def shutdown_confirmed(self, settle_seconds: float = 2.0) -> bool:
        """An exited root and complete consecutive empty member scans are required."""
        with self.stop_lock:
            if self._wait_for_session(settle_seconds):
                return self._close_confirmed_job()
            return False


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

