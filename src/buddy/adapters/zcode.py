"""ZCode coding adapter using its native app-server and a private finish MCP."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..errors import BoardError
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs
from . import turn_io
from .zcode_config import SUPPORTED_ACCESS, cli_command, provider_access_types, provider_paths
from .zcode_protocol import NativeError, decode_json


class ZcodeAdapter(Adapter):
    name = "zcode"
    capabilities = ("zcode", "workspace", "cancel", "artifacts", "deadline", "native-session")
    native_resume = True
    model_discovery = True

    def available(self) -> tuple[bool, str | None]:
        try:
            cli_command()
            paths = provider_paths()
            if not any(t in SUPPORTED_ACCESS for t in provider_access_types(*paths).values()):
                return False, "ZCode has no configured API-key provider; OAuth account providers are unavailable through this adapter"
            return True, None
        except NativeError as error:
            return False, str(error)

    def prepare(self, context: ExecutionContext) -> None:
        if any(not isinstance(context.spec.get(k), str) or not context.spec[k].strip() for k in ("provider", "model", "effort")):
            raise BoardError("INVALID_ARGUMENT", "ZCode coding requires a complete provider, model and effort after routing", adapter=self.name)
        try:
            cli_command(context.environment)
            access = provider_access_types(*provider_paths(context.environment))
        except NativeError as error:
            raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter=self.name) from None
        if context.spec.get("provider") and access.get(context.spec["provider"]) not in SUPPORTED_ACCESS:
            raise BoardError("ADAPTER_UNAVAILABLE", "the requested ZCode provider is not a supported API-key provider", adapter=self.name)
        if context.turn_input is None or not context.turn_id:
            raise BoardError("INVALID_ARGUMENT", "ZCode coding requires a governed turn input", adapter=self.name)
        if context.turn_input.get("resumeMode") not in ("initial", "native-session", "reconstructed-new-session"):
            raise BoardError("INVALID_ARGUMENT", "ZCode requires an explicit initial, native-session or reconstructed-new-session turn", adapter=self.name)
        state = context.environment.get("BUDDY_STATE_DIR")
        if not state:
            raise BoardError("INVALID_ARGUMENT", "ZCode requires the owning private Buddy state directory", adapter=self.name)
        if context.turn_output_file().exists():
            raise BoardError("CONFLICT", "the attempt already has a turn result; it cannot execute twice", adapter=self.name)
        turn_io.prepare_turn(context)
        root = Path(state) / "harnesses" / "zcode" / hashlib.sha256(context.task_id.encode()).hexdigest()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        turn_io.private_json(context.directory / "zcode-control.json", {
            "directory": str(context.directory.resolve()), "nativeRoot": str(root.resolve()),
            "cwd": str(Path(turn_io.workspace_cwd(context)).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()), "spec": {k: context.spec[k] for k in ("provider", "model", "effort") if context.spec.get(k)},
        })

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        paths = context.log_paths()
        stdout, stderr = open_logs(paths)
        try:
            process = subprocess.Popen([sys.executable, "-m", "buddy.adapters.zcode_runner", "--control",
                                        str(context.directory / "zcode-control.json")],
                                       cwd=turn_io.workspace_cwd(context), env=context.environment,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
        finally:
            os.close(stdout)
            os.close(stderr)
        handle = ProcessHandle(process, own_group=True, log_paths=paths)
        handle.deadline = time.monotonic() + context.timeout_seconds
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        payload = _read_result(Path(handle.log_paths["stdout"]))
        exit_code = handle.process.returncode
        # The native app server owns another group. Its runner receipt plus the
        # outer group's disappearance are both required, including on cancellation.
        shutdown = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
        if payload is None:
            payload = {"status": "invalid-result", "error": "the ZCode controller produced no complete JSON result"}
        status = "cancelled" if (handle.cancel_requested or payload.get("status") == "cancelled") and shutdown else "failed"
        if not handle.cancel_requested and exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        record, error = turn_io.read_turn(context, shutdown, exit_code, self.validate_turn_provenance)
        seal_error = None
        payload["turnResultPath"] = str(context.turn_output_file())
        if isinstance(getattr(context, "effective_workspace", None), dict):
            payload["workspaceManifest"] = context.effective_workspace
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
        p = record.get("provenance") or {}
        expected = {"adapter": "zcode", "tool": "buddy_finish_turn", "turnEnd": "completed",
                    "rootSessionMatched": True, "receiptVerified": True, "toolResultSuccess": True,
                    "toolResultTruncated": False, "turnResultType": "success", "settlement": "session-closed"}
        if not isinstance(p, dict) or any(type(p.get(k)) is not type(v) or p.get(k) != v for k, v in expected.items()) or "flush" in p:
            return "the ZCode turn lacks its native tool and session-close evidence"
        if p.get("nativeSessionId") != record.get("sessionId"):
            return "the ZCode native root session does not match the turn record"
        for key in ("inputId", "nativeTurnId", "toolCallId", "receiptId"):
            if not isinstance(p.get(key), str) or not p[key]:
                return f"the ZCode turn lacks {key}"
        identity = {key: record.get(key) for key in ("taskId", "attemptId", "generation", "turnId")}
        expected_input = "buddy-" + hashlib.sha256(turn_io.canonical_json(identity).encode()).hexdigest()
        if p["inputId"] != expected_input:
            return "the ZCode native input does not match the authorized attempt"
        for keys in (("turnStartSeq", "toolCallSeq", "toolResultSeq", "turnEndSeq"),
                     ("turnCompletedOrdinal", "promptCompletedOrdinal", "sessionCloseOrdinal")):
            values = [p.get(k) for k in keys]
            if any(type(v) is not int or v < 0 for v in values) or any(a >= b for a, b in zip(values, values[1:])):
                return "the ZCode native evidence is out of order"
        mode, previous = record.get("resumeMode"), record.get("previousSessionId")
        if mode == "native-session" and record.get("sessionId") == previous and previous:
            return None
        if mode == "initial" and previous is None:
            return None
        if mode == "reconstructed-new-session" and (
            previous is None or isinstance(previous, str) and previous.strip() and record.get("sessionId") != previous
        ):
            return None
        return "the ZCode native resume identity is invalid"

    def discover_models(self) -> dict:
        """Ask the installed app-server for its real available models; never send a turn."""
        directory = Path(tempfile.mkdtemp(prefix="buddy-zcode-catalog-"))
        stopped = False
        try:
            (directory / ".zcode").mkdir(mode=0o700)
            turn_io.private_json(directory / ".zcode/config.json", {"plugins": {"enabled": False},
                                  "features": {"mcp": False, "memory": False, "skill": False, "subagent": False}})
            control = directory / "control.json"
            turn_io.private_json(control, {"discover": True, "directory": str(directory), "nativeRoot": str(directory / "native"),
                                         "cwd": str(directory), "timeoutSeconds": 25})
            logs = {"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")}
            stdout, stderr = open_logs(logs)
            try:
                process = subprocess.Popen([sys.executable, "-m", "buddy.adapters.zcode_runner", "--control", str(control)],
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            finally:
                os.close(stdout)
                os.close(stderr)
            handle = ProcessHandle(process, own_group=True, log_paths=logs)
            if handle.wait(30) is None:
                handle.terminate(grace_seconds=8)
            payload = _read_result(Path(logs["stdout"]))
            stopped = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
            if not payload or process.returncode != 0 or payload.get("status") != "ok" or not stopped:
                raise BoardError("ADAPTER_UNAVAILABLE", "native ZCode model discovery did not settle successfully", adapter=self.name)
            return payload["catalog"]
        finally:
            # A killed controller cannot establish that its separate native group
            # stopped. Preserve its private state in that case, just like a turn.
            if stopped:
                shutil.rmtree(directory)


def _read_result(path: Path) -> dict | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            return None
        value = decode_json(raw)
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
    paths = [("task-specification", context.task_file()), ("turn-result", context.turn_output_file()),
             *(("runner-" + key, Path(value)) for key, value in handle.log_paths.items() if key in ("stdout", "stderr"))]
    out = []
    for kind, path in paths:
        if path.is_file():
            out.append({"kind": kind, "location": str(path.resolve()), "sizeBytes": path.stat().st_size,
                        "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()})
    return out
