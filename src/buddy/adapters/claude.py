"""Claude Code coding adapter, backed by its native stream-json CLI.

P1 scope (ADR-012 section six): a ``--json-schema`` structured result, native
permission requests converted to controller attention, and a freshly allocated
``--session-id`` per attempt with no native resume. Native model-turn support
and sandbox enforcement require separately authorized acceptance probes.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ..errors import BoardError
from .. import usage
from . import turn_io
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs
from .windows_process import owned_popen
from .claude_config import ClaudeUnavailable, cli_command, settings_policy, third_party_overrides
from .claude_protocol import QUOTA_REJECTED_ERROR, decode_json

#: Native initialize metadata is cached briefly so repeated capability reads do
#: not respawn the CLI; ``discover_models`` always refreshes explicitly.
_METADATA_TTL_SECONDS = 60.0
_metadata_cache: dict | None = None
_metadata_lock = threading.RLock()


class ClaudeAdapter(Adapter):
    name = "claude"
    capabilities = ("claude", "workspace", "cancel", "artifacts", "deadline")
    native_resume = False
    model_discovery = True
    read_only_structured = True

    def start_read_only_structured(self, context, request):
        from .read_only import start
        return start(self.name, context, request)

    def available(self) -> tuple[bool, str | None]:
        from ..harness_runtime import selected
        record = selected("claude")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        usable, reason = self.discovery_available()
        if not usable:
            return False, reason
        try:
            _cached_native_metadata()
        except BoardError as error:
            return False, error.message
        return True, None

    def discovery_available(self) -> tuple[bool, str | None]:
        # Explicit discovery must be able to recheck login even when a previous
        # execution-readiness query cached an unauthenticated result.
        try:
            cli_command()
        except ClaudeUnavailable as error:
            return False, str(error)
        overrides = third_party_overrides()
        if overrides:
            return False, "Claude refuses third-party provider overrides: " + ", ".join(overrides)
        return True, None

    def prepare(self, context: ExecutionContext) -> None:
        if any(not isinstance(context.spec.get(key), str) or not context.spec[key].strip()
               for key in ("provider", "model", "effort")):
            raise BoardError("INVALID_ARGUMENT", "Claude requires a complete provider, model and effort after routing", adapter=self.name)
        if context.spec["provider"] != "anthropic":
            raise BoardError("INVALID_ARGUMENT", "Claude execution requires the first-party Anthropic provider", adapter=self.name)
        if not context.turn_id or context.turn_input is None:
            raise BoardError("INVALID_ARGUMENT", "Claude requires a governed turn", adapter=self.name)
        mode = context.turn_input.get("resumeMode")
        if mode == "native-session":
            raise BoardError("INVALID_ARGUMENT", "Claude P1 never resumes a native session; each attempt allocates a fresh --session-id", adapter=self.name)
        if mode not in ("initial", "reconstructed-new-session"):
            raise BoardError("INVALID_ARGUMENT", "Claude requires an explicit initial or reconstructed turn mode", adapter=self.name)
        if settings_policy(context.environment) is None:
            raise BoardError("ADAPTER_UNAVAILABLE",
                             "Claude P1 supports only BUDDY_CLAUDE_SETTINGS_POLICY=isolated; "
                             "an explicit unsupported settings policy was supplied", adapter=self.name)
        overrides = third_party_overrides(context.environment)
        if overrides:
            raise BoardError("ADAPTER_UNAVAILABLE", "Claude refuses third-party provider overrides: " + ", ".join(overrides), adapter=self.name)
        if not context.environment.get("BUDDY_STATE_DIR"):
            raise BoardError("INVALID_ARGUMENT", "Claude requires the private Buddy state directory", adapter=self.name)
        if context.turn_output_file().exists():
            raise BoardError("CONFLICT", "the attempt already has a turn result", adapter=self.name)
        try:
            cli_command(context.environment)
        except ClaudeUnavailable as error:
            raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter=self.name) from None
        turn_io.prepare_turn(context)
        root = context.directory / "claude-private"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        manifest = context.turn_input.get("executionWorkspace")
        manifest = manifest if isinstance(manifest, dict) else {}
        turn_io.private_json(context.directory / "claude-control.json", {
            "directory": str(context.directory.resolve()), "nativeRoot": str(root.resolve()),
            "cwd": str(Path(turn_io.workspace_cwd(context)).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()), "activityFile": str(context.directory / "activity.json"),
            "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
            "sessionId": str(uuid.uuid4()),
            "access": "read" if manifest.get("access") == "read" else "write",
            "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        })

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        from ..harness_runtime import controller_environment
        paths = context.log_paths()
        stdout, stderr = open_logs(paths)
        try:
            process = owned_popen([sys.executable, "-m", "buddy.adapters.claude_runner", "--control",
                                        str(context.directory / "claude-control.json")],
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
        # The native CLI owns another group. The controller receipt plus the
        # outer controller group's disappearance are both required, including on
        # cancellation and quota rejection.
        shutdown = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
        if payload is None:
            payload = {"status": "invalid-result", "error": "the Claude controller produced no complete JSON result"}
        # ADR-018 items 22/23 and the retained root assistant text. The canonical
        # shapes come from ``buddy.usage``; the raw observations never estimated
        # anything, and a missing value stays null. ``quotaFailure`` keeps the
        # bounded Claude-native ``rateLimitType``/``resetsAt`` shape this adapter
        # has always published (pinned by its existing tests); the canonical quota
        # windows and reached state are published through ``quota`` instead.
        payload["tokenUsage"] = usage.normalize_token_usage(payload.get("tokenUsage"))
        payload["quota"] = usage.normalize_quota(payload.get("quota"))
        payload["lastAssistantMessage"] = usage.normalize_last_assistant_message(
            payload.get("lastAssistantMessage"), source="claude/stream-json-root-assistant-message")
        status = "failed"
        if shutdown and payload.get("status") == "cancelled":
            status = "cancelled"
        elif exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        quota_rejected = payload.get("code") == "quota-rejected"
        if quota_rejected:
            payload["error"] = QUOTA_REJECTED_ERROR
            payload["quotaFailure"] = _quota_failure(payload.get("quotaFailure"))
            status = "failed"
        record, error = _read_native_turn(context, shutdown, exit_code)
        attention = payload.get("nativeAttention") if isinstance(payload.get("nativeAttention"), dict) else {}
        requests = attention.get("requests") if type(attention.get("requests")) is int else 0
        denials = attention.get("resultDenials") if type(attention.get("resultDenials")) is int else 0
        payload["attentionRequired"] = requests > 0 or denials > 0
        if quota_rejected:
            # A quota failure carries no outcome and never seals; the bounded
            # quotaFailure block is the only quota fact published.
            record = None
            error = QUOTA_REJECTED_ERROR
        elif payload["attentionRequired"] and record is not None and (record.get("outcome") or {}).get("disposition") == "completed":
            # A refused native permission request must reach the Host as attention;
            # a completed outcome cannot stand in for the attention it requires.
            error = ("a native permission request was denied during this turn; a completed outcome cannot "
                     "stand in for the Host attention that request requires")
        seal_error = None
        payload["turnResultPath"] = str(context.turn_output_file())
        payload["nativeSession"] = _native_session(payload, context)
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
        p = record.get("provenance")
        expected = {"adapter": "claude", "turnEnd": "completed", "resultIsError": False,
                    "resultSubtype": "success", "structuredOutputSource": "json-schema",
                    "initObserved": True, "backgroundSettled": True}
        if not isinstance(p, dict) or any(type(p.get(key)) is not type(value) or p.get(key) != value
                                          for key, value in expected.items()):
            return "the Claude turn lacks native completion and initialization evidence"
        session = record.get("sessionId")
        if not isinstance(session, str) or not session or p.get("nativeSessionId") != session:
            return "the Claude native session identity is missing or mismatched"
        if p.get("structuredOutputValidated") is not True and p.get("controllerAttention") is not True:
            return "the Claude structured output was not validated"
        if p.get("resultSubtype") is not None and (not isinstance(p["resultSubtype"], str) or not p["resultSubtype"]
                                                   or len(p["resultSubtype"]) > 64):
            return "the Claude result subtype is invalid"
        if p.get("sessionModel") is not None and (not isinstance(p["sessionModel"], str) or not p["sessionModel"]
                                                  or len(p["sessionModel"]) > 128):
            return "the Claude session model readback is invalid"
        if type(p.get("eventSeq")) is not int or p["eventSeq"] < 2:
            return "the Claude result lacks native event evidence"
        usage = p.get("modelUsage")
        if not isinstance(usage, list) or len(usage) > 16 or any(not isinstance(item, str) or not item or len(item) > 128
                                                                 for item in usage):
            return "the Claude observed model set is invalid"
        if type(p.get("permissionDenials")) is not int or p["permissionDenials"] < 0 or p["permissionDenials"] > 128:
            return "the Claude permission denial count is invalid"
        if p.get("controllerAttention") is True:
            ids = p.get("deniedControlRequestIds")
            bound = bool(isinstance(ids, list) and 0 < len(ids) <= 32
                         and all(isinstance(item, str) and item and len(item) <= 256 for item in ids))
            if not bound and p.get("permissionDenials") <= 0:
                return "the Claude controller attention is not bound to denied native permission requests"
            if (record.get("outcome") or {}).get("disposition") != "attention":
                return "the Claude controller attention must carry an attention outcome"
            if p.get("structuredOutputValidated") is not False:
                return "the Claude controller attention must not claim a validated structured output"
        mode, previous = record.get("resumeMode"), record.get("previousSessionId")
        if mode == "initial" and previous is None:
            return None
        if mode == "reconstructed-new-session" and (previous is None or (isinstance(previous, str) and previous and record["sessionId"] != previous)):
            return None
        return "the Claude P1 session identity is invalid; native-session resume is not supported"

    def discover_models(self) -> dict:
        """Ask the native CLI through initialize only; never send a user message."""
        _reset_metadata_cache()
        return _cached_native_metadata()["catalog"]


def _reset_metadata_cache() -> None:
    global _metadata_cache
    with _metadata_lock:
        _metadata_cache = None


def _cache_key() -> tuple:
    try:
        command = cli_command()
    except ClaudeUnavailable as error:
        return ("missing", str(error))
    return (tuple(command), tuple(sorted(third_party_overrides())))


def _probe_native_metadata() -> dict:
    """One initialize-only native discovery run in a private temporary root."""
    from ..harness_runtime import controller_environment
    directory = Path(tempfile.mkdtemp(prefix="buddy-claude-catalog-"))
    stopped = False
    try:
        control = directory / "control.json"
        turn_io.private_json(control, {"discover": True, "directory": str(directory), "cwd": str(directory),
                                       "timeoutSeconds": 25})
        logs = {"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")}
        stdout, stderr = open_logs(logs)
        try:
            process = owned_popen([sys.executable, "-m", "buddy.adapters.claude_runner", "--control", str(control)],
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
            raise BoardError("ADAPTER_UNAVAILABLE", reason or "Claude native model discovery did not settle", adapter="claude")
        return payload
    finally:
        # A killed controller cannot establish that its separate native group
        # stopped. Preserve its private state in that case, just like a turn.
        if stopped:
            shutil.rmtree(directory)


def _cached_native_metadata() -> dict:
    # Concurrent capability readers share one initialize-only probe. In
    # particular an unauthenticated console refresh must not fan out CLIs.
    with _metadata_lock:
        return _load_native_metadata()


def _load_native_metadata() -> dict:
    """Cached initialize metadata for repeated capability reads.

    Bounded failures (a missing first-party login, an unusable CLI) are cached
    too, so console and capability checks do not respawn the native CLI on
    every call while the account stays unauthenticated. ``discover_models``
    always resets the cache for an explicit fresh discovery.
    """
    global _metadata_cache
    key = _cache_key()
    now = time.monotonic()
    if _metadata_cache and _metadata_cache.get("key") == key and now - _metadata_cache.get("at", 0.0) < _METADATA_TTL_SECONDS:
        if "error" in _metadata_cache:
            raise _metadata_cache["error"]
        return _metadata_cache["metadata"]
    try:
        metadata = _probe_native_metadata()
    except BoardError as error:
        _metadata_cache = {"key": key, "at": now, "error": error}
        raise
    _metadata_cache = {"key": key, "at": now, "metadata": metadata}
    return metadata


def _quota_failure(value: object) -> dict:
    """Only rateLimitType and resetsAt, typed and bounded; never native error text."""
    source = value if isinstance(value, dict) else {}
    resets = source.get("resetsAt")
    return {"rateLimitType": usage.identifier(source.get("rateLimitType")) or "unknown",
            "resetsAt": resets if isinstance(resets, str) and len(resets) <= 64
            else resets if isinstance(resets, (int, float)) and not isinstance(resets, bool) and math.isfinite(resets)
            else None}


def _native_session(payload: dict, context: ExecutionContext) -> dict:
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    mode = (context.turn_input or {}).get("resumeMode") if isinstance(context.turn_input, dict) else None
    return {
        "adapter": "claude",
        "sessionId": session_id,
        "captured": session_id is not None,
        "storageScope": "harness-user-store",
        "nativeAppVisibility": "unknown",
        "resumeMode": mode,
        "resumable": False,
        "note": ("P1 allocates a fresh --session-id for every attempt, never resumes a native session and makes no "
                 "native-session claim; the session lives in the user's own Claude Code store with unverified visibility"),
    }


def _read_native_turn(context: ExecutionContext, shutdown: bool, exit_code: int | None):
    if exit_code != 0 or not shutdown:
        return None, "the Claude controller did not exit zero with confirmed shutdown"
    try:
        raw = context.turn_output_file().read_bytes()
        if len(raw) > turn_io.MAX_RECORD_BYTES:
            return None, "the Claude turn record exceeds its byte bound"
        record = decode_json(raw)
    except (OSError, ValueError, RecursionError):
        return None, "the Claude controller wrote no valid turn record"
    try:
        control = decode_json((context.directory / "claude-control.json").read_bytes())
    except (OSError, ValueError, RecursionError):
        return None, "the Claude attempt has no readable control record"
    expected = {"version": 1, "taskId": context.task_id, "attemptId": context.attempt_id,
                "generation": context.generation, "turnId": context.turn_id,
                "resumeMode": context.turn_input.get("resumeMode"),
                "previousSessionId": context.turn_input.get("previousSessionId"),
                "inputSha256": turn_io.input_hash(context.turn_input)}
    if not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()):
        return None, "the Claude turn record differs from the service-owned attempt"
    if record.get("sessionId") != control.get("sessionId"):
        return None, "the Claude result session does not equal the UUID preallocated by Buddy"
    error = turn_io.validate_outcome(record.get("outcome")) or ClaudeAdapter.validate_turn_provenance(record)
    return (None, error) if error else (record, None)


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
    result = []
    for kind, path in paths:
        if path.is_file():
            result.append({"kind": kind, "location": str(path.resolve()), "sizeBytes": path.stat().st_size,
                           "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result
