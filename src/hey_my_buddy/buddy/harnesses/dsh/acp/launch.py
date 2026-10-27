"""Launch an owned ACP process with the user's native configuration."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .....errors import BoardError
from .....protocol.owned_process import owned_popen
from .....protocol.process_handle import ProcessHandle
from ... import discovery
from ...native_support import halt_owned_group
from .connection import stop_evidence


class LaunchRejected(BoardError):
    """A launch was refused before any process was started."""

    def __init__(self, message: str, **details):
        super().__init__("ACP_LAUNCH_REJECTED", message, **details)


class LaunchOwnershipError(BoardError):
    """A failure after spawn retains the child and its observed stop evidence."""

    def __init__(self, message: str, *, process, handle, evidence: dict):
        super().__init__("ACP_LAUNCH_OWNERSHIP", message)
        self.process = process
        self.handle = handle
        self.evidence = evidence


def child_environment(extra_env: dict | None = None, *, source: dict | None = None,
                      launch_environment: dict | None = None) -> dict:
    """Inherit native configuration and add the caller's permission controls."""
    environment = discovery.native_environment(dict(os.environ if source is None else source))
    if launch_environment:
        environment.update(launch_environment)
    if extra_env:
        environment.update({str(key): str(value) for key, value in extra_env.items()})
    return environment


def stop_after_spawn_failure(process, handle, error: BaseException) -> LaunchOwnershipError:
    """Use the shared owned-group stop, then record the actual observation."""
    try:
        halt_owned_group(handle, time.monotonic() + 5.0)
    except BaseException as stop_error:
        error.add_note(f"owned-group stop raised {type(stop_error).__name__}: {stop_error}")
    try:
        evidence = stop_evidence(handle)
    except BaseException as observation_error:
        error.add_note(f"stop observation raised {type(observation_error).__name__}: {observation_error}")
        evidence = {"leaderExited": process.poll() is not None,
                    "leaderExitCode": process.poll(), "groupObserved": "unknown",
                    "shutdownConfirmed": False}
    return LaunchOwnershipError(f"ACP startup failed after spawn: {error}",
                                process=process, handle=handle, evidence=evidence)


def launch(argv: list[str], *, private_root: Path, extra_env: dict | None = None,
           cwd: Path | None = None, source_environment: dict | None = None,
           launch_environment: dict | None = None,
           stdin_lock=None,
           merge_stderr: bool = False) -> tuple[subprocess.Popen, ProcessHandle]:
    """Spawn in the caller's private directory and retain ownership on failure."""
    if not argv:
        raise LaunchRejected("launch requires a non-empty argv")
    if private_root is None:
        raise LaunchRejected("launch requires an explicit private root for this run")
    root = Path(private_root)
    spawn_cwd = Path(cwd) if cwd is not None else root
    environment = child_environment(extra_env, source=source_environment,
                                    launch_environment=launch_environment)
    process = owned_popen([str(part) for part in argv], env=environment, cwd=str(spawn_cwd),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                          start_new_session=True, close_fds=True)
    input_lock = threading.Lock() if stdin_lock is None else stdin_lock

    def close_input():
        # A timed-out ACP write may still own the buffered stdin lock. Use the
        # writer's lock to bound the EOF request before the shared stop proceeds.
        if not input_lock.acquire(timeout=5.0):
            return
        try:
            process.stdin.close()
        finally:
            input_lock.release()

    try:
        handle = ProcessHandle(process, stop_channel="stdin", close_input=close_input,
                               own_group=True, log_paths={})
    except BaseException as error:
        receipt = error.owned_popen_evidence
        evidence = {"leaderExited": receipt.exit_code is not None,
                    "leaderExitCode": receipt.exit_code,
                    "groupObserved": "gone" if receipt.shutdown_confirmed else "unknown",
                    "shutdownConfirmed": receipt.shutdown_confirmed}
        raise LaunchOwnershipError(f"ACP startup failed after spawn: {error}",
                                   process=process, handle=error.execution_handle,
                                   evidence=evidence) from error
    return process, handle
