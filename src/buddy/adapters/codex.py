"""Codex account-plan coding adapter, backed by its native App Server."""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import usage
from ..errors import BoardError
from . import turn_io
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs
from .windows_process import owned_popen
from .codex_config import CodexUnavailable, cli_command
from .codex_protocol import decode_json, validated_checkpoint, checkpoint_resumable


class CodexAdapter(Adapter):
    name = "codex"
    capabilities = ("codex", "workspace", "cancel", "artifacts", "deadline", "native-session")
    native_resume = True
    model_discovery = True
    read_only_structured = True
    read_only_structured_resume = True
    no_tool_structured = True

    def start_no_tool_structured(self, context, request):
        from .read_only import start_no_tool
        return start_no_tool(self.name, context, request)
    @property
    def read_only_structured_verified(self):
        from ..harness_runtime import selected
        from ..harness_review import verified
        return verified(self.name, selected(self.name))

    def start_read_only_structured(self, context, request):
        from .read_only import start
        return start(self.name, context, request)

    def available(self) -> tuple[bool, str | None]:
        from ..harness_runtime import selected
        record = selected("codex")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        try:
            cli_command()
            return True, None
        except CodexUnavailable as error:
            return False, str(error)

    def prepare(self, context: ExecutionContext) -> None:
        if any(not isinstance(context.spec.get(key), str) or not context.spec[key].strip()
               for key in ("provider", "model", "effort")):
            raise BoardError("INVALID_ARGUMENT", "Codex requires a complete provider, model and effort after routing", adapter=self.name)
        if context.spec["provider"] != "openai":
            raise BoardError("INVALID_ARGUMENT", "Codex account-plan execution requires the native OpenAI provider", adapter=self.name)
        if not context.turn_id or context.turn_input is None:
            raise BoardError("INVALID_ARGUMENT", "Codex requires a governed turn", adapter=self.name)
        if context.turn_input.get("resumeMode") not in ("initial", "native-session", "reconstructed-new-session"):
            raise BoardError("INVALID_ARGUMENT", "Codex requires an explicit native or reconstructed turn mode", adapter=self.name)
        if not context.environment.get("BUDDY_STATE_DIR"):
            raise BoardError("INVALID_ARGUMENT", "Codex requires the private Buddy state directory", adapter=self.name)
        if context.turn_output_file().exists():
            raise BoardError("CONFLICT", "the attempt already has a turn result", adapter=self.name)
        try:
            cli_command(context.environment)
        except CodexUnavailable as error:
            raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter=self.name) from None
        turn_io.prepare_turn(context)
        root = Path(context.environment["BUDDY_STATE_DIR"]) / "harnesses" / "codex" / hashlib.sha256(context.task_id.encode()).hexdigest()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        turn_io.private_json(context.directory / "codex-control.json", {
            "directory": str(context.directory.resolve()), "nativeRoot": str(root.resolve()),
            "cwd": str(Path(turn_io.workspace_cwd(context)).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()), "activityFile": str(context.directory / "activity.json"),
            "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
            "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        })

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        from ..harness_runtime import controller_environment
        paths = context.log_paths()
        stdout, stderr = open_logs(paths)
        try:
            process = owned_popen([sys.executable, "-m", "buddy.adapters.codex_runner", "--control",
                                        str(context.directory / "codex-control.json")],
                                       cwd=turn_io.workspace_cwd(context), env=controller_environment(context.directory, context.environment),
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
        finally:
            os.close(stdout)
            os.close(stderr)
        handle = ProcessHandle(process, own_group=True, log_paths=paths)
        # timeout_seconds == 0 requests an unlimited execution; now + 0 must not
        # become an immediate handle deadline, so the unlimited case is infinite.
        handle.deadline = math.inf if context.timeout_seconds == 0 else time.monotonic() + context.timeout_seconds
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        payload = _read_result(Path(handle.log_paths["stdout"]))
        exit_code = handle.process.returncode
        shutdown = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
        if payload is None:
            payload = {"status": "invalid-result", "code": "invalid-result", "error": "Codex controller produced no complete result"}
        status = "failed"
        if shutdown and payload.get("status") == "cancelled":
            status = "cancelled"
        elif exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        record, error = _read_native_turn(context, shutdown, exit_code)
        checkpoint = validated_checkpoint(payload, context.turn_input) if shutdown else None
        if checkpoint is None:
            payload.pop("nativeCheckpoint", None)
        # Native usage, quota and the retained root assistant text are attempt
        # observations. The canonical shapes come from ``buddy.usage``; a value
        # that cannot be proven stays unknown instead of being estimated.
        payload["tokenUsage"] = usage.normalize_token_usage(payload.get("tokenUsage"))
        payload["quota"] = usage.normalize_quota(payload.get("quota"))
        payload["quotaFailure"] = usage.normalize_quota_failure(payload.get("quotaFailure"))
        payload["lastAssistantMessage"] = _last_assistant_message(checkpoint)
        session_id = payload.get("sessionId")
        payload["nativeSession"] = {
            "adapter": "codex",
            "sessionId": session_id if isinstance(session_id, str) else None,
            "captured": isinstance(session_id, str) and bool(session_id),
            "storageScope": "harness-user-store",
            "storageOwner": "harness-user-store",
            "nativeAppVisibility": "unknown",
            "resumeMode": (context.turn_input or {}).get("resumeMode"),
            "resumable": bool(shutdown and (record is not None or checkpoint and checkpoint_resumable(payload, checkpoint))),
            "note": "Codex owns the native thread in its configured home; App indexing visibility is unverified. Native continuation rechecks the goal, checkout, configuration and last completed turn binding.",
        }
        payload["turnResultPath"] = str(context.turn_output_file())
        if isinstance(getattr(context, "effective_workspace", None), dict):
            payload["workspaceManifest"] = context.effective_workspace
        seal_error = None
        if error:
            payload["turnError"] = error
            if status == "ok":
                status = "failed"
        elif record is not None and status == "ok":
            payload["turn"] = record
            seal, seal_error = turn_io.seal_workspace(context)
            if seal_error:
                payload["workspaceSealError"] = seal_error
                status = "failed"
            elif seal:
                payload["workspaceSeal"] = seal
        return AdapterOutcome(status=status, result=payload, error=payload.get("error") or error or seal_error,
                              exit_code=exit_code, signal=_signal_name(exit_code), shutdown_confirmed=shutdown,
                              artifacts=_artifacts(context, handle) if shutdown else [])

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = 8.0) -> None:
        handle.terminate(grace_seconds=grace_seconds)

    @staticmethod
    def validate_turn_provenance(record: dict) -> str | None:
        p = record.get("provenance")
        expected = {"adapter": "codex", "turnEnd": "completed", "outputSchemaValidated": True,
                    "finalMessageCompleted": True, "nativeTurnStarted": True, "nativeTurnCompleted": True}
        if not isinstance(p, dict):
            return "the Codex turn lacks native provenance"
        controller_attention = p.get("controllerAttention") is True
        if controller_attention:
            expected = {"adapter": "codex", "turnEnd": "completed", "outputSchemaValidated": False,
                        "nativeTurnStarted": True, "nativeTurnCompleted": True, "controllerAttention": True}
            outcome = record.get("outcome")
            if (not isinstance(outcome, dict) or outcome.get("disposition") != "attention"
                    or not isinstance(p.get("nativeRequestMethod"), str) or not p["nativeRequestMethod"]
                    or p.get("nativeRequestThreadId") != record.get("sessionId")
                    or p.get("nativeRequestTurnId") != p.get("nativeTurnId")):
                return "the Codex controller attention is not bound to a native request"
        if any(type(p.get(key)) is not type(value) or p.get(key) != value for key, value in expected.items()):
            return "the Codex turn lacks native completion and structured-result evidence"
        if p.get("nativeThreadId") != record.get("sessionId") or not isinstance(p.get("nativeTurnId"), str) or not p["nativeTurnId"]:
            return "the Codex native thread or turn identity is missing"
        if p.get("nativeRequestMethod") is not None and (
            p.get("nativeRequestThreadId") != record.get("sessionId") or p.get("nativeRequestTurnId") != p["nativeTurnId"]
        ):
            return "the Codex native request is not correlated with this thread and turn"
        if not controller_attention and (not isinstance(p.get("finalItemId"), str) or not p["finalItemId"]):
            return "the Codex final message has no native item identity"
        if type(p.get("eventSeq")) is not int or p["eventSeq"] < 2:
            return "the Codex final message has no native item or event sequence"
        mode, previous = record.get("resumeMode"), record.get("previousSessionId")
        if mode == "native-session" and isinstance(previous, str) and record["sessionId"] == previous:
            return None
        if mode == "initial" and previous is None:
            return None
        if mode == "reconstructed-new-session" and record["sessionId"] != previous:
            return None
        return "the Codex native resume identity is invalid"

    def discover_models(self) -> dict:
        from ..harness_runtime import controller_environment
        directory = Path(tempfile.mkdtemp(prefix="buddy-codex-catalog-"))
        stopped = False
        try:
            control = directory / "control.json"
            turn_io.private_json(control, {"discover": True, "directory": str(directory), "cwd": str(directory),
                                           "timeoutSeconds": 25})
            logs = {"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")}
            stdout, stderr = open_logs(logs)
            try:
                process = owned_popen([sys.executable, "-m", "buddy.adapters.codex_runner", "--control", str(control)],
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True, env=controller_environment(directory))
            finally:
                os.close(stdout)
                os.close(stderr)
            handle = ProcessHandle(process, own_group=True, log_paths=logs)
            if handle.wait(30) is None:
                handle.terminate(grace_seconds=8)
            payload = _read_result(Path(logs["stdout"]))
            stopped = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
            if not payload or process.returncode != 0 or payload.get("status") != "ok" or not stopped:
                reason = payload.get("error") if isinstance(payload, dict) else None
                raise BoardError("ADAPTER_UNAVAILABLE", reason or "Codex native model discovery did not settle", adapter=self.name)
            return payload["catalog"]
        finally:
            if stopped:
                shutil.rmtree(directory)


def _last_assistant_message(checkpoint: dict | None) -> dict | None:
    """The bounded native root assistant text of a stopped attempt, or None."""
    message = checkpoint.get("lastAssistantMessage") if isinstance(checkpoint, dict) else None
    if not isinstance(message, dict):
        return None
    return usage.normalize_last_assistant_message(
        {"text": message.get("text"), "itemId": message.get("itemId"), "phase": message.get("phase"),
         "sourceBytes": message.get("sourceBytes"), "sha256": message.get("sha256"),
         "truncated": message.get("truncated")},
        source="codex/app-server-root-assistant-message")


def _read_native_turn(context: ExecutionContext, shutdown: bool, exit_code: int | None):
    if exit_code != 0 or not shutdown:
        return None, "the Codex controller did not exit zero with confirmed shutdown"
    try:
        raw = context.turn_output_file().read_bytes()
        if len(raw) > turn_io.MAX_RECORD_BYTES:
            return None, "the Codex turn record exceeds its byte bound"
        record = decode_json(raw)
    except (OSError, ValueError, RecursionError):
        return None, "the Codex controller wrote no valid turn record"
    expected = {"version": 1, "taskId": context.task_id, "attemptId": context.attempt_id,
                "generation": context.generation, "turnId": context.turn_id,
                "resumeMode": context.turn_input.get("resumeMode"),
                "previousSessionId": context.turn_input.get("previousSessionId"),
                "inputSha256": turn_io.input_hash(context.turn_input)}
    if not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()):
        return None, "the Codex turn record differs from the service-owned attempt"
    error = turn_io.validate_outcome(record.get("outcome")) or CodexAdapter.validate_turn_provenance(record)
    return (None, error) if error else (record, None)


def _read_result(path: Path) -> dict | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        value = decode_json(raw) if len(raw) <= 512 * 1024 else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def _signal_name(code: int | None) -> str | None:
    if code is None or code >= 0:
        return None
    import signal
    try:
        return signal.Signals(-code).name
    except ValueError:
        return f"signal-{-code}"


def _artifacts(context: ExecutionContext, handle: ProcessHandle) -> list[dict]:
    paths = [("task-specification", context.task_file()), ("turn-result", context.turn_output_file())]
    result = []
    for kind, path in paths:
        if path.is_file():
            result.append({"kind": kind, "location": str(path.resolve()), "sizeBytes": path.stat().st_size,
                           "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result
