"""Owned Windows process trees for service and adapter children.

The child starts suspended and joins a uniquely named Job before its primary
thread runs. A replacement service may reopen that Job by its persisted name,
but only after checking the complete saved execution identity. An execution
root's creating handle is duplicated into the created process, so the service
closes its own handle immediately and later queries and stops reopen the Job
briefly by name.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import subprocess
import uuid

from .windows_process import WindowsAPI
from .service_process import CREATE_NEW_PROCESS_GROUP, service_creation_flags

CREATE_SUSPENDED = 0x00000004
WINDOWS_COMMAND_LINE_LIMIT = 32767


class OwnedJob:
    def __init__(self, api, handle, name):
        self.api, self.handle, self.name = api, handle, name
        self.empty_confirmed = False
        self.transferred = False

    @contextmanager
    def _reopened(self):
        """Open the transferred Job by name for one bounded query or stop."""
        handle = self.api.open_job(self.name)
        try:
            yield handle
        finally:
            if handle is not None:
                self.api.close(handle)

    def active(self):
        if self.handle is not None:
            return self.api.active(self.handle)
        if self.empty_confirmed:
            return 0
        if not self.transferred:
            return None
        with self._reopened() as handle:
            # A Job whose name is gone has no members left; kill-on-close
            # ended the whole tree with the last held handle.
            return 0 if handle is None else self.api.active(handle)

    def members(self):
        return self.api.process_ids(self.handle)

    def terminate(self):
        if self.handle is not None:
            self.api.terminate(self.handle)
        elif self.transferred and not self.empty_confirmed:
            with self._reopened() as handle:
                if handle is not None:
                    self.api.terminate(handle)

    def close(self, *, confirmed=False):
        if self.handle is not None:
            try:
                self.api.close(self.handle)
            finally:
                # pywin32 consumes the handle even when CloseHandle fails.
                self.handle = None
            self.empty_confirmed = confirmed

    def __del__(self):
        if self.handle is not None:
            try:
                self.close()
            except OSError:
                pass


def transfer_job_to_child(process, job) -> None:
    """Resume the suspended root and hand its Job's lifetime to the child.

    The copy inside the created process keeps the kill-on-close Job alive for
    exactly as long as that process lives, so an orderly service restart or a
    same-contract install no longer kills the execution. The service's own
    handle closes as soon as the copy exists; the marker precedes the resume,
    so a crash in between is settled by kill-on-close from the recorded root.
    """
    job.api.resume(process)
    job.api.duplicate_job_into(process.pid, job.handle)
    job.transferred = True
    job.close()


@dataclass(frozen=True)
class ProcessCreationEvidence:
    created: bool
    shutdown_confirmed: bool
    exit_code: int | None


class WindowsCommandLineTooLong(ValueError):
    """The final Windows argv cannot fit in one CreateProcess command line."""

    code = "harness.command-line-too-long"
    limit = WINDOWS_COMMAND_LINE_LIMIT

    def __init__(self, utf16_units: int):
        self.utf16_units = utf16_units
        self.owned_popen_evidence = ProcessCreationEvidence(False, True, None)
        super().__init__(f"Windows command line has {utf16_units} UTF-16 units; limit is {self.limit}")


def check_windows_command_line(argv, *, windows: bool | None = None) -> None:
    """Check exactly the command line that Python's Windows Popen will encode.

    The terminating NUL counts towards CreateProcess's UTF-16 limit. Callers
    with their own Popen path use this once before creating the process.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return
    encoded = subprocess.list2cmdline(argv)
    units = len(encoded.encode("utf-16-le", errors="surrogatepass")) // 2 + 1
    if units > WINDOWS_COMMAND_LINE_LIMIT:
        raise WindowsCommandLineTooLong(units)


def owned_popen(*args, windows_api=None, job_name: str | None = None,
                defer_resume: bool = False, **kwargs):
    """Create a process with tree ownership before any user code can run.

    Every failure carries ``owned_popen_evidence`` (ProcessCreationEvidence).
    ``created`` records whether Popen returned an actual process handle;
    ``shutdown_confirmed`` records no child or observed cleanup, independently
    of whether the caller has registered ownership. Confirmed failures retain
    the original exception; unknown cleanup raises RuntimeError with the same
    evidence and the original failure as its cause. Consumers use the evidence,
    never exception text or type, to judge shutdown.

    ``defer_resume`` leaves the assigned child suspended for the execution-root
    launch, which must publish its spawn marker before the child runs.
    """
    from .process_identity import begin_native_process_spawn, discard_confirmed_native_spawn

    check_windows_command_line(args[0], windows=os.name == "nt" or windows_api is not None)
    intent = begin_native_process_spawn()
    process = None
    job = None
    assigned = False
    try:
        if os.name != "nt" and windows_api is None:
            if job_name is not None:
                raise ValueError("a Job name requires Windows process ownership")
            process = subprocess.Popen(*args, **kwargs)
            process._buddy_native_intent = intent
            return process
        api = windows_api if windows_api is not None else WindowsAPI()
        name = job_name if job_name is not None else f"Local\\HeyMyBuddy-{uuid.uuid4()}"
        job = OwnedJob(api, api.create_job(name), name)
        kwargs = {**kwargs, "creationflags": kwargs.get("creationflags", 0) |
                  CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | service_creation_flags(windows=True),
                  "start_new_session": False}
        process = subprocess.Popen(*args, **kwargs)
        api.assign(job.handle, process)
        assigned = True
        if not defer_resume:
            api.resume(process)
        process._buddy_job = job
        process._buddy_native_intent = intent
        return process
    except BaseException as error:
        stopped = True
        if process is not None:
            try:
                if assigned:
                    job.terminate()
                else:
                    process.kill()  # Popen's held process handle, never a persisted PID.
                process.wait(timeout=2)
                if assigned:
                    stopped = job.active() == 0
            except (OSError, subprocess.TimeoutExpired):
                stopped = False
        if job is not None:
            try:
                job.close()
            except OSError:
                if process is not None:
                    stopped = False
        evidence = ProcessCreationEvidence(
            created=process is not None, shutdown_confirmed=stopped,
            exit_code=process.returncode if process is not None else None)
        if stopped:
            discard_confirmed_native_spawn(intent)
        if not stopped:
            failure = RuntimeError("suspended child shutdown is unconfirmed after ownership failure")
            failure.owned_popen_evidence = evidence
            raise failure from error
        error.owned_popen_evidence = evidence
        raise
