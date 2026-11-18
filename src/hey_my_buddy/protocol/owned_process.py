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

from ..text import truncate_utf8
from .reasons import reason
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


class WorkingDirectoryRejected(Exception):
    """The OS refused the requested working directory before creating a child."""

    code = "harness.working-directory-rejected"

    @staticmethod
    def reason_for(cwd: str, system_reason: str) -> dict:
        return reason("harness.working-directory-rejected", cwd=cwd, systemReason=system_reason)

    def __init__(self, cwd: str, system_reason: str):
        self.reason = self.reason_for(cwd, system_reason)
        self.owned_popen_evidence = ProcessCreationEvidence(False, True, None)
        message = f"The OS rejected the working directory ({system_reason}). Use a valid or shorter checkout path."
        super().__init__(truncate_utf8(f"{message} Directory: {cwd!r}", 512, errors="replace"))


def _rejected_cwd(error: OSError, cwd, executable) -> WorkingDirectoryRejected | None:
    if cwd is None:
        return None
    directory = os.fsdecode(cwd)
    winerror = getattr(error, "winerror", None)
    if winerror != 267:  # CreateProcessW's ERROR_DIRECTORY has no filename.
        if not isinstance(error, (NotADirectoryError, FileNotFoundError)):
            return None
        if error.filename is not None:
            # POSIX Popen reports the precise path that failed chdir or exec.
            if os.fsdecode(error.filename) != directory:
                return None
            if os.fsdecode(executable) == directory and os.path.isdir(directory):
                return None
        elif (not os.path.isabs(executable) or not os.path.isfile(executable)
              or os.path.isdir(directory)):
            # An unnamed ENOENT can be the executable or its interpreter.
            # Require separate evidence of an invalid cwd and an existing executable.
            return None
    number = f"WinError {winerror}" if winerror is not None else f"errno {error.errno}"
    return WorkingDirectoryRejected(directory, f"{number}: {error.strerror}")


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


def owned_popen(argv, *, windows_api=None, job_name: str | None = None,
                defer_resume: bool = False, **kwargs):
    """Create a process with tree ownership before any user code can run.

    Every failure carries ``owned_popen_evidence`` (ProcessCreationEvidence).
    ``created`` records whether Popen returned an actual process handle;
    ``shutdown_confirmed`` records no child or observed cleanup, independently
    of whether the caller has registered ownership. A rejected working directory
    raises WorkingDirectoryRejected. Unknown cleanup raises RuntimeError with
    the same evidence and the original failure as its cause. Other confirmed
    failures retain the original exception. Consumers use the evidence to judge
    shutdown.

    ``defer_resume`` leaves the assigned child suspended for the execution-root
    launch, which must publish its spawn marker before the child runs.
    """
    from .process_identity import begin_native_process_spawn, discard_confirmed_native_spawn

    windows = os.name == "nt" or windows_api is not None
    check_windows_command_line(argv, windows=windows)
    intent = begin_native_process_spawn()
    process = None
    job = None
    assigned = False
    try:
        if windows:
            api = windows_api if windows_api is not None else WindowsAPI()
            name = job_name if job_name is not None else f"Local\\HeyMyBuddy-{uuid.uuid4()}"
            job = OwnedJob(api, api.create_job(name), name)
            kwargs["creationflags"] = (kwargs.get("creationflags", 0) | CREATE_SUSPENDED |
                                       CREATE_NEW_PROCESS_GROUP | service_creation_flags(windows=True))
            kwargs["start_new_session"] = False
        elif job_name is not None:
            raise ValueError("a Job name requires Windows process ownership")
        try:
            process = subprocess.Popen(argv, **kwargs)
        except OSError as error:
            executable = kwargs.get("executable") or argv[0]
            rejection = _rejected_cwd(error, kwargs.get("cwd"), executable)
            if rejection is not None:
                raise rejection from error
            raise
        if job is not None:
            job.api.assign(job.handle, process)
            assigned = True
            if not defer_resume:
                job.api.resume(process)
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
            finally:
                for name in ("stdin", "stdout", "stderr"):
                    stream = vars(process).get(name)
                    if stream is not None:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
        if job is not None:
            try:
                job.close()
            except OSError:
                if process is not None:
                    stopped = False
            if assigned and process.returncode is None:
                # Kill-on-close may reap the leader even when TerminateJobObject failed.
                # This wait does not establish that the entire Job stopped.
                try:
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
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
