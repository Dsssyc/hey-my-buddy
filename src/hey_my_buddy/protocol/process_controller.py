"""Shared launch mechanics for one owned controller process."""
from __future__ import annotations

import os
import subprocess
import time

from .owned_process import ProcessCreationEvidence, owned_popen
from .process_handle import ProcessHandle, open_logs

def launch_controller(*, prepare, log_paths: dict, timeout_seconds: int | None = None,
                      grace_seconds: float = 0, job_name: str | None = None) -> ProcessHandle:
    """Open logs, prepare and spawn one owned child, closing both log FDs.

    Every failure carries the creating boundary's ``owned_popen_evidence``.
    That receipt describes this controller only, not a separately owned native
    child. Consumers combine it with the controller's native stop receipt.
    Log opening and preparation precede child creation. A successful spawn
    transfers ownership to its handle before log descriptors are closed, so
    later failures terminate and observe that owned group. An ownership
    failure consumes owned_popen's receipt even when no handle was returned.
    Positive budgets stamp a deadline plus the supplied grace. An absent
    budget leaves the controller unbounded; discovery enforces its own limit.
    """
    evidence = ProcessCreationEvidence(created=False, shutdown_confirmed=True, exit_code=None)
    handle = None
    try:
        stdout, stderr = open_logs(log_paths)
        try:
            command, cwd, environment = prepare()
            try:
                process = owned_popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=stderr, start_new_session=True,
                                      close_fds=True, **({"job_name": job_name} if job_name is not None else {}))
            except BaseException as error:
                evidence = error.owned_popen_evidence
                raise
            evidence = ProcessCreationEvidence(created=True, shutdown_confirmed=False,
                                               exit_code=process.returncode)
            try:
                handle = ProcessHandle(process, own_group=True, log_paths=log_paths)
            except BaseException as error:
                # Popen has returned. Preserve its held process even if a
                # replacement or faulty constructor raises before returning.
                prior = getattr(error, "owned_popen_evidence", None)
                from .process_handle import ProcessHandle as OwnedProcessHandle

                partial = (getattr(error, "execution_handle", None)
                           or OwnedProcessHandle.adopt_unregistered(
                               process, own_group=True, log_paths=log_paths))
                error.execution_handle = partial
                if prior is not None and prior.shutdown_confirmed:
                    raise
                try:
                    partial.terminate(grace_seconds=0)
                    process.wait(timeout=2)
                    stopped = partial.shutdown_confirmed(settle_seconds=0)
                except BaseException:
                    stopped = False
                receipt = ProcessCreationEvidence(created=True, shutdown_confirmed=stopped,
                                                  exit_code=process.returncode)
                if not stopped:
                    failure = RuntimeError("controller child shutdown is unconfirmed after handle construction failure")
                    failure.owned_popen_evidence = receipt
                    failure.execution_handle = partial
                    raise failure from error
                error.owned_popen_evidence = receipt
                raise
        finally:
            try:
                os.close(stdout)
            finally:
                os.close(stderr)
        if timeout_seconds is not None:
            handle.deadline = time.monotonic() + timeout_seconds + grace_seconds
        return handle
    except BaseException as error:
        if handle is not None:
            error.execution_handle = handle
            try:
                handle.terminate(grace_seconds=0)
                stopped = handle.shutdown_confirmed()
            except BaseException as cleanup_error:
                cleanup_error.owned_popen_evidence = ProcessCreationEvidence(
                    created=True, shutdown_confirmed=False, exit_code=process.returncode)
                cleanup_error.execution_handle = handle
                raise
            evidence = ProcessCreationEvidence(created=True, shutdown_confirmed=stopped,
                                               exit_code=process.returncode)
        error.owned_popen_evidence = getattr(error, "owned_popen_evidence", evidence)
        raise

