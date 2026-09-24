"""The ``decision`` adapter: one bounded, tool-free decision call per attempt.

The adapter is deliberately narrow. It owns exactly one helper process group, hands
it one bounded input document that the Python service already persisted, and reads
one structured envelope back. It never opens the database, never chooses a model,
never retries, and never writes authoritative state: the Worker submits the envelope
through C-Two and the service publishes (or refuses) the decision inside the same
result transaction.

Shutdown evidence is the helper's own statement plus the observed exit of the
process this adapter started. The helper spawns its detached child group, so this
adapter's group alone cannot prove that child stopped: a killed helper with no
verified envelope is *uncertain*, never reported as stopped.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..errors import BoardError
from ..runtime import resource_path
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs

#: The helper escalates its own child after 5 s and waits another 5 s for it, so a
#: SIGTERM cleanup needs more room than the base 3 s adapter grace. Killing the
#: helper earlier would destroy the shutdown evidence we require.
TERMINATE_GRACE_SECONDS = 12.0
#: Override for packaging and tests: one executable invoked directly with the same
#: ``--input-file/--output-file/--timeout`` arguments as the Node helper.
HELPER_ENV = "BUDDY_DECISION_HELPER"
INPUT_FILE = "decision-input.json"
OUTPUT_FILE = "decision-output.json"
MAX_INPUT_BYTES = 256 * 1024


def node_binary() -> str | None:
    return os.environ.get("BUDDY_NODE") or shutil.which("node")


class DecisionAdapter(Adapter):
    """Runs the bounded decision helper for one attempt."""

    name = "decision"
    capabilities = ("decision",)

    def helper_path(self) -> Path:
        """The declared ``dsh.decision`` resource; never a guessed directory."""
        return resource_path("dsh.decision")

    def helper_argv(self) -> list[str]:
        override = os.environ.get(HELPER_ENV)
        if override:
            return [str(Path(override).expanduser())]
        node = node_binary()
        if node is None:  # pragma: no cover - guarded by available()
            raise BoardError("ADAPTER_UNAVAILABLE", "the bounded decision helper is not available", adapter=self.name)
        return [node, str(self.helper_path())]

    def available(self) -> tuple[bool, str | None]:
        override = os.environ.get(HELPER_ENV)
        if override:
            path = Path(override).expanduser()
            if not path.is_file():
                return False, f"the configured decision helper {path} is missing"
            if not os.access(path, os.X_OK):
                return False, f"the configured decision helper {path} is not executable"
            return True, None
        if node_binary() is None:
            return False, "Node.js is required for the bounded decision helper; set BUDDY_NODE"
        try:
            script = self.helper_path()
        except BoardError as error:
            return False, error.message
        if not script.is_file():
            return False, "the bounded decision helper is missing from this distribution"
        return True, None

    # -- paths ---------------------------------------------------------------
    def input_path(self, context: ExecutionContext) -> Path:
        return context.directory / INPUT_FILE

    def output_path(self, context: ExecutionContext) -> Path:
        return context.directory / OUTPUT_FILE

    def decision_timeout(self, context: ExecutionContext) -> int:
        decision = context.spec.get("decision")
        value = decision.get("timeoutSeconds") if isinstance(decision, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            raise BoardError("INVALID_ARGUMENT", "this attempt has no bounded decision timeout", adapter=self.name)
        return value

    # -- lifecycle -----------------------------------------------------------
    def prepare(self, context: ExecutionContext) -> None:
        usable, reason = self.available()
        if not usable:
            raise BoardError("ADAPTER_UNAVAILABLE", reason or "the decision adapter is unavailable", adapter=self.name)
        payload = context.decision_input
        if not isinstance(payload, dict):
            raise BoardError(
                "INVALID_ARGUMENT",
                "this attempt carries no bounded decision input; a decision task is only created by "
                "selection_request or evaluation_maintain",
                adapter=self.name,
            )
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"the persisted decision input exceeds the {MAX_INPUT_BYTES}-byte bound",
                adapter=self.name,
            )
        context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(context.directory, 0o700)
        # The private per-decision working directory is created here, by the process
        # that owns this attempt: the service records the path but never does file work
        # inside a database transaction.
        Path(context.cwd).mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.input_path(context)
        path.write_text(encoded)
        os.chmod(path, 0o600)

    def arguments(self, context: ExecutionContext) -> list[str]:
        return [
            *self.helper_argv(),
            "--input-file",
            str(self.input_path(context)),
            "--output-file",
            str(self.output_path(context)),
            "--timeout",
            str(self.decision_timeout(context)),
        ]

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        log_paths = context.log_paths()
        stdout, stderr = open_logs(log_paths)
        try:
            process = subprocess.Popen(
                self.arguments(context),
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
        handle = ProcessHandle(process, own_group=True, log_paths=log_paths)
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        payload = _read_envelope(self.output_path(context))
        exit_code = handle.process.returncode
        # The helper's own statement *and* the observed exit of the process this
        # adapter started. Its detached child group cannot be proven stopped by this
        # adapter's group alone, so a killed helper without a verified envelope stays
        # unconfirmed.
        helper_confirmed = payload.get("shutdownConfirmed") is True if isinstance(payload, dict) else False
        shutdown_confirmed = helper_confirmed and handle.shutdown_confirmed()
        if payload is None:
            result: dict = {
                "status": "invalid-result",
                "error": "the decision helper produced no parseable result envelope",
                "helperExitCode": exit_code,
            }
            error = "the decision helper produced no parseable result envelope"
        else:
            result = {**payload, "helperExitCode": exit_code}
            error = None
            if payload.get("status") == "error":
                code = payload.get("code") if isinstance(payload.get("code"), str) else "helper-failed"
                message = payload.get("message") if isinstance(payload.get("message"), str) else "the helper failed"
                error = f"{code}: {message}"[:2000]
        if handle.cancel_requested:
            status = "cancelled" if shutdown_confirmed else "failed"
        elif payload is None or payload.get("status") == "error":
            status = "failed"
        elif exit_code == 0 and payload.get("status") == "ok" and shutdown_confirmed:
            status = "ok"
        else:
            status = "failed"
            if error is None:
                error = (
                    "the decision helper did not confirm shutdown"
                    if not shutdown_confirmed
                    else f"the decision helper exited with {exit_code}"
                )
        return AdapterOutcome(
            status=status,
            result=result,
            error=error,
            exit_code=exit_code,
            signal=_signal_name(handle.process),
            shutdown_confirmed=shutdown_confirmed,
            artifacts=[],
        )

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = TERMINATE_GRACE_SECONDS) -> None:
        handle.terminate(grace_seconds=grace_seconds)


def _read_envelope(path: Path) -> dict | None:
    """One JSON object from the helper's output file, or ``None`` when unusable."""
    try:
        raw = path.read_text(errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        try:
            value = json.loads(raw.splitlines()[-1])
        except (ValueError, IndexError):
            return None
    return value if isinstance(value, dict) else None


def _signal_name(process: subprocess.Popen) -> str | None:
    code = process.returncode
    if code is None or code >= 0:
        return None
    import signal as signal_module

    try:
        return signal_module.Signals(-code).name
    except ValueError:
        return f"signal-{-code}"


__all__ = ["DecisionAdapter", "HELPER_ENV", "node_binary"]
