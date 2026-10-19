"""Adapter contract: how one attempt becomes real external work.

Adapters never receive database handles and never write authoritative state. They
receive an execution context, own the child process handle, enforce a hard
deadline when one is supplied, and return a structured result the worker submits through C-Two.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ...errors import BoardError
from ...protocol.process_identity import COMMAND_STOP_GRACE_SECONDS
from ...protocol.process_handle import ProcessHandle, open_logs


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
    #: Bounded decision payload the service persisted for this attempt, handed to the
    #: worker in its claim and written to disk by the decision adapter. It is the only
    #: way decision input reaches a model; adapters still never see the database.
    decision_input: dict | None = None
    #: MicroTask turn claim: the service-owned turn identity plus the bounded context.
    #: Coding adapters write it to the private attempt directory and pass the
    #: runner its absolute path; internal executions can have no micro task turn.
    turn: dict | None = None
    #: Attempt-scoped credential handed to the coding child. It is written to a private
    #: file (never into the model-visible turn input) and exported by path.
    agent_credential: str | None = None
    session_record_path: str | None = None

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
    def timeout_seconds(self) -> int | None:
        return self.spec.get("routerBudgetSeconds")

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
        from ...private_dirs import context_root
        return context_root(self, getattr(self, "private_adapter", None)) / "agent-credential.json"

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


@dataclass(frozen=True)
class ReadOnlyStructuredRequest:
    """Generic native read-only call; no workflow turn or agent authority."""

    cwd: str
    prompt: str
    output_schema: dict
    budget: dict
    capture_evidence: bool = False


@dataclass(frozen=True)
class NoToolStructuredRequest:
    """One tool-free native call in a Host-created empty private directory."""

    cwd: str
    prompt: str
    output_schema: dict
    timeout_seconds: int = 60
    capture_evidence: bool = False


@dataclass(frozen=True)
class BoundSessionServices:
    """Process-free native binding; the role owns the prompt and business rules.

    Only the description and actual tool names are public values. ``services``
    stays opaque in the controller process and never enters a run frame.
    """

    description: Any
    completion_tool: str
    checkpoint_tool: str | None
    answer_tool: str | None
    services: object


def registered_read_only_check(description) -> dict:
    """Qualification for a description whose execution entries were removed.

    Confirm the project's registered mechanism exists. Native policy readback
    belongs to the run, and no vendor source is examined here.
    """
    from .registry import run_seam

    platforms = description.system_sandbox_platforms
    sandbox = sys.platform in platforms
    from ...protocol.reasons import reason
    result = {"eligible": False, "reason": reason("harness.readonly-not-implemented"), "systemSandbox": sandbox,
              "sameAttemptContinuation": description.read_only_structured_resume}
    if not description.read_only_structured:
        return result
    if platforms and not sandbox:
        return {**result, "reason": reason("harness.readonly-platform-unsupported")}
    categories = getattr(description, "read_only_tool_categories", ())
    if not sandbox and (not categories or set(categories) - {"read", "search"}):
        return {**result, "reason": reason("harness.readonly-tools-unrestricted")}
    if not callable(getattr(run_seam(description.name), "run", None)):
        return {**result, "reason": reason("harness.readonly-resource-missing")}
    return {**result, "eligible": True, "reason": None}


class Adapter:
    """Base class for the built-in adapters."""

    name = "base"
    capabilities: tuple[str, ...] = ()
    native_resume = False
    interrupted_resume = False
    native_resume_permission_change = False
    model_discovery = False
    read_only_structured = False
    read_only_structured_resume = False
    system_sandbox_platforms: tuple[str, ...] = ()
    read_only_tool_categories: tuple[str, ...] = ()
    no_tool_structured = False

    def local_read_only_check(self) -> dict:
        """Confirm only the registered capability; native enforcement is separate."""
        return registered_read_only_check(self)



    def discover_models(self) -> dict:
        raise BoardError("CATALOG_UNAVAILABLE", f"{self.name} does not declare model discovery")

    def delete_sessions(self, session_ids: list[str]) -> dict[str, str]:
        raise BoardError("UNSUPPORTED", f"{self.name} does not declare session deletion")

    def discovery_available(self) -> tuple[bool, str | None]:
        """Whether fresh metadata can be queried; execution readiness may be stricter."""
        return self.available()

    @staticmethod
    def validate_turn_provenance(record: dict) -> str | None:
        return "this adapter does not support micro task turn provenance"

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def prepare(self, context: ExecutionContext) -> None:  # pragma: no cover - trivial
        """Validate that this adapter can run the context; raise BoardError otherwise."""

    def start(self, context: ExecutionContext) -> "ProcessHandle":
        raise NotImplementedError

    def collect(self, handle: "ProcessHandle", context: ExecutionContext) -> AdapterOutcome:
        raise NotImplementedError

    def stop(self, handle: "ProcessHandle", *, stop_initiator: str,
             grace_deadline: float | None) -> None:
        """Stop work using this executor's own cancellation policy."""
        handle.force_stop_requested = True
        self.cancel(handle)

    def cancel(self, handle: "ProcessHandle", *, grace_seconds: float = COMMAND_STOP_GRACE_SECONDS) -> None:
        handle.terminate(grace_seconds=grace_seconds)


def unsupported(adapter: str, reason: str) -> BoardError:
    return BoardError("UNSUPPORTED_ADAPTER", reason, adapter=adapter)
