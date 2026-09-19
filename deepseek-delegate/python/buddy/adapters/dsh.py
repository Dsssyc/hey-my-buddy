"""The ``dsh`` adapter: run one delegated task through the existing Node runner.

Node is needed only here and inside the upstream dsh plugins. The runner
(``scripts/run.mjs``) keeps ownership of the dsh process group and of the in-run
inquiry bridge socket; this adapter owns the runner process group, the child
handle, the deadline and the log files.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from ..errors import BoardError
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs

UNIX_SOCKET_PATH_BUDGET = 105 if os.uname().sysname == "Linux" else 101
TERMINATE_GRACE_SECONDS = 3.0


def node_binary() -> str | None:
    return os.environ.get("BUDDY_NODE") or shutil.which("node")


class DshAdapter(Adapter):
    name = "dsh"
    capabilities = ("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline")

    def available(self) -> tuple[bool, str | None]:
        if not node_binary():
            return False, "Node.js is required for the dsh runner; set BUDDY_NODE"
        runner = self.runner_path()
        if runner is None or not Path(runner).is_file():
            return False, "the dsh runner entrypoint is missing from this distribution"
        return True, None

    def runner_path(self) -> str | None:
        override = os.environ.get("BUDDY_RUNNER_PATH")
        if override:
            return override
        for anchor in (Path(__file__).resolve().parents[3], Path(__file__).resolve().parents[4]):
            candidate = anchor / "scripts" / "run.mjs"
            if candidate.is_file():
                return str(candidate)
        return None

    def prepare(self, context: ExecutionContext) -> None:
        usable, reason = self.available()
        if not usable:
            raise BoardError("ADAPTER_UNAVAILABLE", reason or "the dsh adapter is unavailable", adapter=self.name)
        context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context.task_file().write_text(context.spec["task"])
        os.chmod(context.task_file(), 0o600)

    def inquiry_paths(self, context: ExecutionContext) -> dict:
        candidates = [
            context.directory,
            Path("/tmp") / "hey-my-buddy-inquiry" / context.attempt_id,
            Path(os.environ.get("TMPDIR", "/tmp")) / "hey-my-buddy-inquiry" / context.attempt_id,
        ]
        directory = next(
            (
                candidate
                for candidate in candidates
                if len(str(candidate / "inquiry.sock").encode()) <= UNIX_SOCKET_PATH_BUDGET
            ),
            candidates[-1],
        )
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        credentials = {
            "socketPath": str(directory / "inquiry.sock"),
            "resultsPath": str(directory / "inquiry.results.jsonl"),
            "errorPath": str(directory / "inquiry.sock.error.json"),
            # Hex, never base64url: a leading '-' would be parsed as an option.
            "token": secrets.token_hex(32),
        }
        # The service reads these credentials from disk; they are never part of a
        # public task view, so the token cannot leak through a status/result read.
        credentials_path = context.directory / "inquiry.json"
        credentials_path.write_text(json.dumps(credentials))
        os.chmod(credentials_path, 0o600)
        return {"directory": directory, **credentials}

    def arguments(self, context: ExecutionContext, inquiry: dict) -> list[str]:
        spec = context.spec
        log_paths = context.log_paths()
        args = [
            node_binary() or "node",
            self.runner_path() or "",
            "--cwd",
            spec["cwd"],
            "--task-file",
            str(context.task_file()),
            "--timeout",
            str(spec["timeoutSeconds"]),
            "--log-dir",
            str(context.directory),
            f"--inquiry-socket={inquiry['socketPath']}",
            f"--inquiry-token={inquiry['token']}",
            f"--inquiry-results={inquiry['resultsPath']}",
        ]
        if not spec.get("workspace", True):
            args.append("--no-workspace")
        for key in ("model", "provider", "effort"):
            if spec.get(key):
                args.append(f"--{key}={spec[key]}")
        return args

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        inquiry = self.inquiry_paths(context)
        context.inquiry = inquiry  # type: ignore[attr-defined]
        log_paths = context.log_paths()
        stdout, stderr = open_logs(log_paths)
        try:
            process = subprocess.Popen(
                self.arguments(context, inquiry),
                cwd=context.cwd,
                env=context.environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(stdout)
            os.close(stderr)
        handle = ProcessHandle(process, own_group=True, log_paths={**log_paths, **{
            "inquirySocket": inquiry["socketPath"],
            "inquiryResults": inquiry["resultsPath"],
            "inquiryError": inquiry["errorPath"],
        }})
        handle.inquiry = inquiry  # type: ignore[attr-defined]
        handle.deadline = time.monotonic() + context.timeout_seconds
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        stdout_path = Path(handle.log_paths["stdout"])
        try:
            raw = stdout_path.read_text(errors="replace")
            payload = json.loads(raw.strip().splitlines()[-1]) if raw.strip() else None
            if not isinstance(payload, dict):
                payload = None
        except (OSError, ValueError, IndexError):
            payload = None
        exit_code = handle.process.returncode
        inquiry = getattr(handle, "inquiry", {})
        if payload is None:
            return AdapterOutcome(
                status="cancelled" if handle.cancel_requested else "failed",
                result={"status": "invalid-result", "error": "the dsh runner produced no parseable result"},
                error="the dsh runner produced no parseable result",
                exit_code=exit_code,
                signal=_signal_name(handle.process),
                shutdown_confirmed=handle.shutdown_confirmed(),
            )
        payload = {**payload, "inquiryBridge": {k: inquiry.get(k) for k in ("socketPath", "resultsPath", "errorPath")}}
        shutdown_confirmed = bool(payload.get("processState", {}).get("shutdownConfirmed")) or _preflight_failed(
            exit_code, stdout_path
        )
        status = payload.get("status")
        if handle.cancel_requested or status == "cancelled":
            final = "cancelled" if shutdown_confirmed else "failed"
        elif exit_code == 0 and status == "ok" and shutdown_confirmed:
            final = "ok"
        else:
            final = "failed"
        artifacts = _discovered_artifacts(handle, payload)
        return AdapterOutcome(
            status=final,
            result=payload,
            error=payload.get("error"),
            exit_code=exit_code,
            signal=_signal_name(handle.process),
            shutdown_confirmed=shutdown_confirmed,
            artifacts=artifacts,
        )

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = TERMINATE_GRACE_SECONDS) -> None:
        handle.terminate(grace_seconds=grace_seconds)

    def inquiry_credentials(self, handle: ProcessHandle) -> dict | None:
        inquiry = getattr(handle, "inquiry", None)
        if not inquiry:
            return None
        return dict(inquiry)


def _preflight_failed(exit_code: int | None, stdout_path: Path) -> bool:
    """``run.mjs`` reserves exit 2 for usage/preflight failures before any dsh spawn."""
    try:
        return exit_code == 2 and stdout_path.stat().st_size == 0
    except OSError:
        return False


def _signal_name(process: subprocess.Popen) -> str | None:
    code = process.returncode
    if code is None or code >= 0:
        return None
    import signal as signal_module

    try:
        return signal_module.Signals(-code).name
    except ValueError:
        return f"signal-{-code}"


def _discovered_artifacts(handle: ProcessHandle, payload: dict) -> list[dict]:
    """Artifacts this run genuinely produced, validated by size and hash.

    Only files the runner itself names are offered: the capture path and the two
    runner logs. Nothing else is guessed from the working tree.
    """
    candidates: list[tuple[str, Path]] = []
    capture = (payload.get("logPaths") or {}).get("capture")
    if isinstance(capture, str):
        candidates.append(("capture", Path(capture)))
    for role in ("stdout", "stderr"):
        value = handle.log_paths.get(role)
        if isinstance(value, str):
            candidates.append((f"runner-{role}", Path(value)))
    artifacts: list[dict] = []
    for kind, path in candidates:
        try:
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            artifacts.append(
                {
                    "kind": kind,
                    "location": str(path.resolve()),
                    "contentHash": digest,
                    "sizeBytes": path.stat().st_size,
                }
            )
        except OSError:
            continue
    return artifacts
