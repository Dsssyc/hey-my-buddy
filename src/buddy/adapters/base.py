"""Adapter contract: how one attempt becomes real external work.

Adapters never receive database handles and never write authoritative state. They
receive a bounded execution context, own the child process handle, enforce their own
deadline, and return a structured result the worker submits through C-Two.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import BoardError


@dataclass
class ExecutionContext:
    """Everything an adapter may know about one attempt."""

    task_id: str
    attempt_id: str
    generation: int
    spec: dict
    directory: Path
    runtime: dict
    environment: dict
    lease_seconds: int = 120
    #: Bounded decision payload the service persisted for this attempt, handed to the
    #: worker in its claim and written to disk by the decision adapter. It is the only
    #: way decision input reaches a model; adapters still never see the database.
    decision_input: dict | None = None
    #: Governed turn claim: the service-owned turn identity plus the bounded context.
    #: Coding adapters write it to the private attempt directory and pass the
    #: runner its absolute path; internal executions can have no governed turn.
    turn: dict | None = None
    #: Attempt-scoped credential handed to the coding child. It is written to a private
    #: file (never into the model-visible turn input) and exported by path.
    agent_credential: str | None = None

    @property
    def cwd(self) -> str:
        """The effective execution directory: the resolved workspace path when present."""
        turn_input = self.turn_input
        if isinstance(turn_input, dict):
            manifest = turn_input.get("executionWorkspace")
            if isinstance(manifest, dict) and manifest.get("path"):
                return str(manifest["path"])
        return self.spec["cwd"]

    @property
    def timeout_seconds(self) -> int:
        return int(self.spec["timeoutSeconds"])

    @property
    def turn_input(self) -> dict | None:
        if not isinstance(self.turn, dict):
            return None
        value = self.turn.get("input")
        return value if isinstance(value, dict) else None

    @property
    def turn_id(self) -> str | None:
        if not isinstance(self.turn, dict):
            return None
        value = self.turn.get("turnId")
        return value if isinstance(value, str) and value else None

    def task_file(self) -> Path:
        return self.directory / "task.txt"

    def turn_input_file(self) -> Path:
        return self.directory / "turn-input.json"

    def turn_output_file(self) -> Path:
        return self.directory / "turn-output.json"

    def credential_file(self) -> Path:
        return self.directory / "agent-credential.json"

    def log_paths(self) -> dict:
        return {
            "stdout": str(self.directory / "runner.stdout.log"),
            "stderr": str(self.directory / "runner.stderr.log"),
        }


@dataclass
class AdapterOutcome:
    """One structured attempt outcome, exactly as the worker reports it."""

    status: str  # ok | failed | cancelled
    result: dict = field(default_factory=dict)
    error: str | None = None
    exit_code: int | None = None
    signal: str | None = None
    shutdown_confirmed: bool = False
    artifacts: list[dict] = field(default_factory=list)

    def to_report(self) -> dict:
        return {
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "exitCode": self.exit_code,
            "signal": self.signal,
            "shutdownConfirmed": self.shutdown_confirmed,
            "artifacts": self.artifacts,
        }


class Adapter:
    """Base class for the built-in adapters."""

    name = "base"
    decision_execution = False
    capabilities: tuple[str, ...] = ()
    native_resume = False
    model_discovery = False
    decision_execution = False

    def discover_models(self) -> dict:
        raise BoardError("CATALOG_UNAVAILABLE", f"{self.name} does not declare model discovery")

    def decision_available(self) -> tuple[bool, str | None]:
        return False, f"{self.name} has no verified tool-free decision execution"

    def start_decision(self, context: ExecutionContext) -> "ProcessHandle":
        raise BoardError("UNSUPPORTED_ADAPTER", f"{self.name} cannot execute a tool-free decision", adapter=self.name)

    @staticmethod
    def validate_turn_provenance(record: dict) -> str | None:
        return "this adapter does not support governed turn provenance"

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def prepare(self, context: ExecutionContext) -> None:  # pragma: no cover - trivial
        """Validate that this adapter can run the context; raise BoardError otherwise."""

    def start(self, context: ExecutionContext) -> "ProcessHandle":
        raise NotImplementedError

    def collect(self, handle: "ProcessHandle", context: ExecutionContext) -> AdapterOutcome:
        raise NotImplementedError

    def cancel(self, handle: "ProcessHandle", *, grace_seconds: float = 3.0) -> None:
        handle.terminate(grace_seconds=grace_seconds)


class ProcessHandle:
    """One owned child process group.

    Only the object that created the child may signal it: stored PIDs are diagnostic
    values and a restarted service never sends signals from them.
    """

    def __init__(self, process: subprocess.Popen, *, own_group: bool, log_paths: dict):
        self.process = process
        self.own_group = own_group
        self.log_paths = log_paths
        self.cancel_requested = False
        self.signalled_at: float | None = None
        # Capture the process-group id while the leader is alive. Looking it up
        # later with os.getpgid(child.pid) would fail as soon as the leader exits,
        # even while its descendants are still running, and would then report a
        # live group as stopped.
        self.pgid: int | None = None
        if own_group and process.pid is not None:
            try:
                self.pgid = os.getpgid(process.pid)
            except OSError:
                self.pgid = process.pid

    @property
    def pid(self) -> int | None:
        return self.process.pid

    @property
    def finished(self) -> bool:
        return self.process.poll() is not None

    def signal_group(self, sig: int) -> None:
        if self.process.pid is None:
            return
        try:
            if self.own_group and self.pgid is not None:
                os.killpg(self.pgid, sig)
            else:
                self.process.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def group_alive(self) -> bool:
        """Remain live unless the owned group is observed gone, including descendants."""
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

    def terminate(self, *, grace_seconds: float = 3.0) -> None:
        """Ask this owned process group to stop, escalating to SIGKILL in bounded time."""
        self.cancel_requested = True
        self.signalled_at = time.monotonic()
        self.signal_group(signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None and not self.group_alive():
                return
            time.sleep(0.05)
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
                return True
            time.sleep(0.025)
        return not self.group_alive()


def open_logs(paths: dict) -> tuple[Any, Any]:
    Path(paths["stdout"]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stdout = os.open(paths["stdout"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    stderr = os.open(paths["stderr"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    return stdout, stderr


def unsupported(adapter: str, reason: str) -> BoardError:
    return BoardError("UNSUPPORTED_ADAPTER", reason, adapter=adapter)
