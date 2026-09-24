"""The ``command`` adapter: one explicit argv process, never an implicit shell."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..errors import BoardError
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs

TERMINATE_GRACE_SECONDS = 3.0


class CommandAdapter(Adapter):
    name = "command"
    capabilities = ("command", "cancel", "artifacts", "deadline", "argv")

    def prepare(self, context: ExecutionContext) -> None:
        argv = context.spec.get("argv")
        if not argv:
            raise BoardError("INVALID_ARGUMENT", "the command adapter requires an explicit argv")
        executable = argv[0]
        resolved = shutil.which(executable) if not os.path.isabs(executable) else executable
        if resolved is None or not Path(resolved).exists():
            raise BoardError(
                "ADAPTER_UNAVAILABLE",
                f"argv[0] {executable!r} is not an executable this worker can find",
                adapter=self.name,
            )
        context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context.task_file().write_text(context.spec["task"])
        os.chmod(context.task_file(), 0o600)

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        log_paths = context.log_paths()
        stdout, stderr = open_logs(log_paths)
        argv = list(context.spec["argv"])
        environment = {
            **context.environment,
            "BUDDY_TASK_ID": context.task_id,
            "BUDDY_ATTEMPT_ID": context.attempt_id,
            "BUDDY_TASK_FILE": str(context.task_file()),
            "BUDDY_LOG_DIR": str(context.directory),
        }
        try:
            # ``shell=False`` is explicit: an operator-provided argv is one program
            # plus arguments, never a shell string.
            process = subprocess.Popen(
                argv,
                cwd=context.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                shell=False,
                close_fds=True,
            )
        finally:
            os.close(stdout)
            os.close(stderr)
        handle = ProcessHandle(process, own_group=True, log_paths=log_paths)
        handle.deadline = time.monotonic() + context.timeout_seconds
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        exit_code = handle.process.returncode
        shutdown_confirmed = handle.shutdown_confirmed()
        if handle.cancel_requested:
            status = "cancelled" if shutdown_confirmed else "failed"
        elif exit_code == 0 and shutdown_confirmed:
            status = "ok"
        else:
            status = "failed"
        stdout_path = Path(handle.log_paths["stdout"])
        stderr_path = Path(handle.log_paths["stderr"])
        result = {
            "status": "ok" if status == "ok" else ("cancelled" if status == "cancelled" else "nonzero"),
            "mode": "command",
            "argv": list(context.spec.get("argv", [])),
            "exitCode": exit_code,
            "signal": _signal_name(handle.process),
            "elapsedSeconds": None,
            "logPaths": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
            "finalText": _head(stdout_path),
            "processState": {"shutdownConfirmed": shutdown_confirmed},
            "note": (
                "exit 0 only means the command exited successfully, not that the task is correct: "
                "inspect the real artifacts and run the relevant checks yourself."
            ),
        }
        error = None
        if status != "ok":
            error = f"command exited with {exit_code}" if exit_code is not None else "command did not start"
        return AdapterOutcome(
            status=status,
            result=result,
            error=error,
            exit_code=exit_code,
            signal=_signal_name(handle.process),
            shutdown_confirmed=shutdown_confirmed,
            artifacts=_artifacts(handle, context),
        )

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = TERMINATE_GRACE_SECONDS) -> None:
        handle.terminate(grace_seconds=grace_seconds)


def _head(path: Path, limit: int = 6000) -> str:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit)
        return raw.decode("utf-8", errors="replace").strip()[:limit]
    except OSError:
        return ""


def _artifacts(handle: ProcessHandle, context: ExecutionContext) -> list[dict]:
    artifacts: list[dict] = []
    for role in ("stdout", "stderr"):
        path = Path(handle.log_paths[role])
        try:
            if not path.is_file():
                continue
            artifacts.append(
                {
                    "kind": f"runner-{role}",
                    "location": str(path.resolve()),
                    "contentHash": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "sizeBytes": path.stat().st_size,
                }
            )
        except OSError:
            continue
    task_file = context.task_file()
    if task_file.is_file():
        artifacts.append(
            {
                "kind": "task-specification",
                "location": str(task_file.resolve()),
                "contentHash": hashlib.sha256(task_file.read_bytes()).hexdigest(),
                "sizeBytes": task_file.stat().st_size,
            }
        )
    return artifacts


def _signal_name(process: subprocess.Popen) -> str | None:
    code = process.returncode
    if code is None or code >= 0:
        return None
    import signal as signal_module

    try:
        return signal_module.Signals(-code).name
    except ValueError:
        return f"signal-{-code}"
