"""The ``dsh`` adapter: run one delegated task through the declared Node runner.

Node is needed only here and inside the upstream dsh plugins. The runner declared as
``dsh.runner`` in ``packaging/runtime-assets.json`` keeps ownership of the dsh
process group and of the in-run inquiry bridge socket; this adapter owns the runner
process group, the child handle, the deadline and the log files.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..errors import BoardError
from ..private_dirs import context_root, ensure_private_dir
from . import turn_io
from ..runtime import resource_path
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, open_logs
from .windows_process import owned_popen

TERMINATE_GRACE_SECONDS = 3.0


def node_binary(environment=None) -> str | None:
    from ..harness_runtime import selected
    env = os.environ if environment is None else environment
    if os.environ.get('BUDDY_DEV_SOURCE') == '1' and env.get('BUDDY_NODE'):
        return env['BUDDY_NODE']
    record = selected('dsh', env)
    command = record.get('command', []) if record else []
    if len(command) > 1 and Path(command[0]).stem == 'node':
        return command[0]
    from ..harness_discovery import discover
    result = discover('node', environment=env)
    return result.get('executable') if result.get('available') else None


class DshAdapter(Adapter):
    name = "dsh"
    capabilities = ("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline")
    model_discovery = True
    no_tool_structured = True
    # The native DSH permission policy does not confine reads or networking.
    # Do not advertise a read-only structured Router capability.

    def start_no_tool_structured(self, context, request):
        from .read_only import start_no_tool
        return start_no_tool(self.name, context, request)

    def discover_models(self) -> dict:
        from .dsh_catalog import discover_models

        return discover_models()

    def available(self) -> tuple[bool, str | None]:
        from ..harness_runtime import selected
        record = selected("dsh")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        if not (os.environ.get('BUDDY_DEV_SOURCE') == '1' and os.environ.get('BUDDY_RUNNER_PATH')):
            from ..harness_discovery import discover
            detected = discover('dsh')
            if not detected['available']:
                return False, detected.get('remedy') or detected.get('reasonCode')
        if not node_binary():
            return False, "Node.js is required for the dsh runner; set BUDDY_NODE"
        try:
            runner = self.runner_path()
        except BoardError as error:
            return False, error.message
        if runner is None or not Path(runner).is_file():
            return False, "the dsh runner entrypoint is missing from this distribution"
        return True, None

    def runner_path(self) -> str | None:
        """The declared ``dsh.runner`` resource, or the explicit test override."""
        override = os.environ.get("BUDDY_RUNNER_PATH")
        if override:
            return override
        return str(resource_path("dsh.runner"))

    def prepare(self, context: ExecutionContext) -> None:
        context.private_adapter = self.name
        usable, reason = self.available()
        if not usable:
            raise BoardError("ADAPTER_UNAVAILABLE", reason or "the dsh adapter is unavailable", adapter=self.name)
        turn_io.prepare_turn(context)

    def inquiry_paths(self, context: ExecutionContext) -> dict:
        return turn_io.inquiry_paths(context)

    def workspace_cwd(self, context: ExecutionContext) -> str:
        return turn_io.workspace_cwd(context)

    def arguments(self, context: ExecutionContext, inquiry: dict) -> list[str]:
        spec = context.spec
        args = [
            node_binary(context.environment) or "node",
            self.runner_path() or "",
            "--cwd",
            self.workspace_cwd(context),
            "--task-file",
            str(context.task_file()),
            "--timeout",
            str(spec["timeoutSeconds"]),
            "--log-dir",
            str(context.directory / "dsh-run"),
            "--flat-log-dir",
            "--private-dir",
            str(ensure_private_dir(context_root(context, self.name))),
            f"--inquiry-socket={inquiry['socketPath']}",
            f"--inquiry-token={inquiry['token']}",
            f"--inquiry-results={inquiry['resultsPath']}",
            f"--inquiry-error={inquiry['errorPath']}",
        ]
        # Every run's session rollout is attempt-private through the runner's
        # supported per-run patch overlay on the JSONL session backend root.
        # DSH_HOME, the credentials store and the settings document are never
        # relocated, so native model auth keeps resolving in the owning harness.
        args.append(f"--session-root={ensure_private_dir(context_root(context, self.name) / 'sessions')}")
        from ..harness_runtime import selected
        selected_harness = selected('dsh', context.environment)
        if selected_harness and selected_harness.get('executable'):
            args.append('--dsh-bin=' + selected_harness['executable'])
        for key in ("model", "provider", "effort"):
            if spec.get(key):
                args.append(f"--{key}={spec[key]}")
        if context.turn_input is not None:
            # The service supplies the paired governed turn files, and the runner
            # mounts the bounded activity observer for this exact attempt. The
            # sidecar lives in the private attempt directory the owning Worker
            # already polls; its path never enters a public result.
            args.extend(
                [
                    "--turn-input-file",
                    str(context.turn_input_file()),
                    "--turn-output-file",
                    str(context.turn_output_file()),
                    f"--activity-file={activity_sidecar_path(context)}",
                    "--usage-file",
                    str(native_usage_sidecar_path(context)),
                ]
            )
        return args

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        inquiry = self.inquiry_paths(context)
        context.inquiry = inquiry  # type: ignore[attr-defined]
        log_paths = context.log_paths()
        stdout, stderr = open_logs(log_paths)
        from ..harness_discovery import native_environment
        from ..harness_runtime import selected
        record = selected('dsh', context.environment) or {}
        environment = native_environment(context.environment, command=record.get('command', []))
        # The DSH bridge has intentionally scoped blackboard authority. None of
        # the Host/Worker service identity or provider API-key variables survive.
        for key in ('BUDDY_AGENT_CREDENTIAL_FILE', 'BUDDY_TASK_ID', 'BUDDY_ATTEMPT_ID', 'BUDDY_STATE_DIR', 'BUDDY_PYTHON', 'DSH_HOME'):
            if key in context.environment:
                environment[key] = context.environment[key]
        if os.environ.get('BUDDY_DEV_SOURCE') == '1' and os.environ.get('BUDDY_RUNNER_PATH'):
            for key in ('DSH_BIN', 'MOCK_ARTIFACT_DIR', 'MOCK_STUB_SLEEP_SECONDS'):
                if key in context.environment:
                    environment[key] = context.environment[key]
        try:
            process = owned_popen(
                self.arguments(context, inquiry),
                cwd=self.workspace_cwd(context),
                env=environment,
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
        # ``timeoutSeconds=0`` is the normalized no-deadline sentinel: the runner
        # installs no headless termination timer for it. Stamping ``now + 0``
        # would publish an already-expired deadline for a run that is still
        # allowed to finish, so an unbounded attempt reports no deadline rather
        # than a misleading immediate one. Positive values are unchanged.
        timeout_seconds = context.timeout_seconds
        handle.deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        if getattr(handle, "no_tool", False):
            from .read_only import collect
            return collect(handle)
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
            preflight = _preflight_failed(exit_code, stdout_path) and handle.shutdown_confirmed()
            stopped = handle.shutdown_confirmed()
            return AdapterOutcome(
                status="cancelled" if handle.cancel_requested else "failed",
                result={"status": "invalid-result", "error": "the dsh runner produced no parseable result",
                        **({'modelStarted': False, 'code': 'adapter-unavailable'} if preflight else {}),
                        # A stopped observer may still have recorded this attempt's
                        # native usage before the runner lost its own result.
                        **(_native_usage(context) if stopped else {})},
                error="the dsh runner produced no parseable result",
                exit_code=exit_code,
                signal=_signal_name(handle.process),
                shutdown_confirmed=stopped,
            )
        payload = {**payload, "inquiryBridge": {k: inquiry.get(k) for k in ("socketPath", "resultsPath", "errorPath")}}
        native_activity = payload.get("nativeActivity") if isinstance(payload.get("nativeActivity"), dict) else {}
        payload["nativeActivity"] = {**native_activity, "sidecarWritten": activity_sidecar_path(context).is_file()}
        native_usage = payload.get("nativeUsage") if isinstance(payload.get("nativeUsage"), dict) else {}
        payload["nativeUsage"] = {**native_usage, "sidecarWritten": native_usage_sidecar_path(context).is_file()}
        payload.update(_native_usage(context))
        shutdown_confirmed = (bool(payload.get("processState", {}).get("shutdownConfirmed")) or _preflight_failed(
            exit_code, stdout_path
        )) and handle.shutdown_confirmed()
        status = payload.get("status")
        if handle.cancel_requested or status == "cancelled":
            final = "cancelled" if shutdown_confirmed else "failed"
        elif exit_code == 0 and status == "ok" and shutdown_confirmed:
            final = "ok"
        else:
            final = "failed"
        turn_record, turn_error = self._import_turn(context, shutdown_confirmed, exit_code)
        # The validated turn record is the only proven session identity. Bind it
        # AFTER import: a rejected or missing turn never contributes an id, and
        # a failed attempt stays honestly uncaptured instead of inventing one.
        payload["nativeSession"] = _native_session(payload, turn_record if turn_error is None else None)
        if context.turn_input is not None:
            payload["turnResultPath"] = str(context.turn_output_file())
            effective = getattr(context, "effective_workspace", None)
            if isinstance(effective, dict) and effective:
                # The service pins this effective input manifest with the turn.
                payload["workspaceManifest"] = effective
            if turn_error is not None:
                payload["turnError"] = turn_error
                if final == "ok":
                    final = "failed"
            elif turn_record is not None:
                payload["turn"] = turn_record
        seal_error: str | None = None
        if final == "ok" and turn_record is not None:
            seal, seal_error = self._seal_workspace(context)
            if seal_error is not None:
                payload["workspaceSealError"] = seal_error
                final = "failed"
            elif seal:
                payload["workspaceSeal"] = seal
        # Model-reported artifacts are untrusted references; only a stopped process
        # with confirmed shutdown may publish the files it actually produced.
        artifacts = _discovered_artifacts(handle, payload) if shutdown_confirmed else []
        error = payload.get("error") or turn_error or seal_error
        return AdapterOutcome(
            status=final,
            result=payload,
            error=error,
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

    def _import_turn(
        self, context: ExecutionContext, shutdown_confirmed: bool, exit_code: int | None
    ) -> tuple[dict | None, str | None]:
        return turn_io.read_turn(context, shutdown_confirmed, exit_code, self.validate_turn_provenance)

    @staticmethod
    def validate_turn_provenance(record: dict) -> str | None:
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("tool") != "buddy_finish_turn" or provenance.get("turnEnd") != "completed":
            return "the native terminal tool did not complete the turn"
        if provenance.get("rootSessionMatched") is not True:
            return "the accepted tool result was not correlated with the root session"
        if (record.get("provenance") or {}).get("flush") not in ("awaited", "flushed"):
            return "the session flush was not awaited before the turn record was written"
        return None

    def _seal_workspace(self, context: ExecutionContext) -> tuple[dict | None, str | None]:
        return turn_io.seal_workspace(context)


def _native_session(payload: dict, turn_record: dict | None = None) -> dict:
    """Truthful native-session facts for one dsh attempt.

    Git isolation and native session storage are separate dimensions. Every run
    moves only the session rollout root into the attempt (the runner's per-run
    patch overlay on the JSONL session backend); the DSH home, credentials store
    and settings document keep their owning-harness values, so native model auth
    is never relocated or simulated.

    The session id comes from the validated turn record the adapter imported and
    is never invented: a rejected or missing turn reports no id.
    """
    turn_session = turn_record.get("sessionId") if isinstance(turn_record, dict) else None
    if not isinstance(turn_session, str) or not turn_session:
        turn_session = None
    session_id = turn_session
    session_id_source = "validated-turn" if turn_session else "none"
    native_storage = payload.get("nativeStorage") if isinstance(payload.get("nativeStorage"), dict) else {}
    session_root_private = native_storage.get("sessionRootPrivate") is True
    return {
        "adapter": "dsh",
        "sessionId": session_id,
        "captured": session_id is not None,
        "sessionIdSource": session_id_source,
        "storageScope": native_storage.get("scope") or "unknown",
        "storageOwner": "buddy-attempt" if session_root_private else "harness-user-store",
        "credentialsStore": native_storage.get("credentialsStore") or "harness-user-store",
        "nativeAppVisibility": native_storage.get("nativeAppVisibility") or "unknown",
        "resumeMode": native_storage.get("resumeMode") or "reconstructed-new-session",
        "resumable": False,
        "note": native_storage.get("note"),
    }


def activity_sidecar_path(context: ExecutionContext) -> Path:
    """The attempt-private ``activity.json`` the DSH observer writes for the Worker."""
    return context.directory / "activity.json"


def native_usage_sidecar_path(context: ExecutionContext) -> Path:
    """The attempt-private ``native-usage.json`` the DSH usage observer writes."""
    return context.directory / "native-usage.json"


def _native_usage(context: ExecutionContext) -> dict:
    """This attempt's native usage, quota and retained root assistant text.

    The observer's document is attempt-bound; a foreign, malformed or missing
    sidecar contributes nothing at all. DSH reports its input count excluding
    cache read/write tokens, which ``buddy.usage`` unifies exactly once; a
    provider that exposes no quota window stays unknown instead of guessed.
    """
    from .. import usage
    if context.turn_input is None:
        # Only a governed turn mounts the observer; an ungoverned run has no
        # attempt-bound native record and reports unknown usage.
        return {"tokenUsage": None, "quota": None, "quotaFailure": None, "lastAssistantMessage": None}
    document = usage.read_sidecar(native_usage_sidecar_path(context), task_id=context.task_id,
                                  attempt_id=context.attempt_id, generation=context.generation)
    observed = document.get("nativeUsage") if isinstance(document, dict) else None
    observed = observed if isinstance(observed, dict) else {}
    native = observed.get("tokenUsage") if isinstance(observed.get("tokenUsage"), dict) else {}
    # The observer reports native counters; the canonical unification (DSH input
    # excludes cache, so cache is added exactly once) lives in ``buddy.usage``.
    candidate = {key: native.get(key) for key in (
        "inputBasis", "inputTokens", "cachedInputTokens", "outputTokens",
        "reasoningOutputTokens", "cacheReadTokens", "cacheWriteTokens", "completeness")}
    candidate["source"] = native.get("source") or observed.get("source")
    candidate["nativeRecords"] = native.get("records")
    candidate["coverage"] = "native-root-session"
    failure = observed.get("failure")
    failure_code = failure.get("code") if isinstance(failure, dict) else None
    quota_failure = usage.normalize_quota_failure({"nativeCode": failure_code, "source": "dsh/session-turn-end", "observedAt": document.get("updatedAt") if document else None})
    if quota_failure is not None and quota_failure["code"] == "unknown":
        quota_failure = None
    return {
        "tokenUsage": usage.normalize_token_usage(candidate),
        "quota": usage.normalize_quota(observed.get("quota")),
        "quotaFailure": quota_failure,
        "lastAssistantMessage": usage.normalize_last_assistant_message(
            observed.get("lastAssistantMessage"), source="dsh/session-root-assistant-message"),
    }


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

    Only files the runner itself names are offered: the two runner logs.
    Nothing else is guessed from the working tree.
    """
    candidates: list[tuple[str, Path]] = []
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
