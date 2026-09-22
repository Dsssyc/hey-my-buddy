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
        self._write_turn_files(context)
        self._resolve_workspace(context)

    def _write_turn_files(self, context: ExecutionContext) -> None:
        """Stage the service-owned turn input and the scoped credential.

        Both live in the private attempt directory. The turn input contains no
        secret; the credential file is mode 0600 and is exported to the child by
        path only, never embedded in the model-visible document.
        """
        turn_input = context.turn_input
        if turn_input is None:
            return
        # The service hashed exactly this canonical text; writing anything else would
        # fail the turn identity check on import.
        text = json.dumps(turn_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        path = context.turn_input_file()
        path.write_text(text)
        os.chmod(path, 0o600)
        if context.agent_credential:
            credential = {
                "token": context.agent_credential,
                "taskId": context.task_id,
                "attemptId": context.attempt_id,
                "generation": context.generation,
                "turnId": context.turn_id,
            }
            credential_path = context.credential_file()
            credential_path.write_text(json.dumps(credential, sort_keys=True))
            os.chmod(credential_path, 0o600)
            # The child CLI reads this env var, sends the token as a scoped credential
            # and fails closed when it is missing or invalid.
            context.environment["BUDDY_AGENT_CREDENTIAL_FILE"] = str(credential_path)
            context.environment["BUDDY_TASK_ID"] = context.task_id
            context.environment["BUDDY_ATTEMPT_ID"] = context.attempt_id

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

    def workspace_cwd(self, context: ExecutionContext) -> str:
        """The directory this attempt actually runs in: the resolved workspace path."""
        manifest = getattr(context, "effective_workspace", None)
        if isinstance(manifest, dict) and manifest.get("path"):
            return str(manifest["path"])
        return context.cwd

    def arguments(self, context: ExecutionContext, inquiry: dict) -> list[str]:
        spec = context.spec
        args = [
            node_binary() or "node",
            self.runner_path() or "",
            "--cwd",
            self.workspace_cwd(context),
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
        if context.turn_input is not None:
            # Both flags are present together; a legacy run omits both.
            args.extend(
                [
                    "--turn-input-file",
                    str(context.turn_input_file()),
                    "--turn-output-file",
                    str(context.turn_output_file()),
                ]
            )
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
                cwd=self.workspace_cwd(context),
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
        turn_record, turn_error = self._import_turn(context, shutdown_confirmed, exit_code)
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

    def _resolve_workspace(self, context: ExecutionContext) -> None:
        """Verify the service-owned input workspace before any child exists.

        The service resolves (and pins) the effective manifest for every turn,
        including continuations, so the adapter never rewrites the immutable turn
        input. A read-only input must still be unchanged; this touches only Git
        reads and runs outside every database transaction.
        """
        turn_input = context.turn_input
        if turn_input is None:
            return
        manifest = turn_input.get("executionWorkspace")
        if not isinstance(manifest, dict) or not manifest:
            context.effective_workspace = None  # type: ignore[attr-defined]
            return
        from ..workflow import workspace_module

        workspace_module().verify(manifest, require_unchanged=manifest.get("access") == "read")
        context.effective_workspace = manifest  # type: ignore[attr-defined]

    def _import_turn(
        self, context: ExecutionContext, shutdown_confirmed: bool, exit_code: int | None
    ) -> tuple[dict | None, str | None]:
        """Read and validate the structured turn output after the process stopped.

        Identity, the service-written input hash, a completed native provenance and
        the runner's own exit/shutdown evidence are all required; a missing or
        malformed record fails honestly and keeps the runner logs.
        """
        turn_input = context.turn_input
        if turn_input is None:
            return None, None
        if exit_code != 0:
            return None, "the runner did not exit zero"
        if not shutdown_confirmed:
            return None, "runner shutdown is not confirmed, so no turn outcome is imported"
        path = context.turn_output_file()
        try:
            raw = path.read_text()
        except OSError:
            return None, "the governed runner wrote no turn output file"
        try:
            record = json.loads(raw)
        except ValueError:
            return None, "the turn output file is not valid JSON"
        if not isinstance(record, dict):
            return None, "the turn output file is not a JSON object"
        if record.get("version") != 1:
            return None, "the turn output version is not supported"
        expected_identity = {
            "taskId": context.task_id,
            "attemptId": context.attempt_id,
            "generation": context.generation,
            "turnId": context.turn_id,
        }
        for key, expected in expected_identity.items():
            if record.get(key) != expected:
                return None, f"the turn record {key} does not match the service-owned execution identity"
        expected_input = hashlib.sha256(
            json.dumps(turn_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if record.get("inputSha256") != expected_input:
            return None, "the turn record inputSha256 does not match the turn input the service wrote"
        outcome = record.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("disposition") not in ("completed", "assistance", "attention"):
            return None, "the turn record carries no completed/assistance/attention outcome"
        provenance = record.get("provenance")
        if not isinstance(provenance, dict):
            return None, "the turn record carries no provenance"
        if provenance.get("tool") != "buddy_finish_turn" or provenance.get("turnEnd") != "completed":
            return None, "the native terminal tool did not complete the turn"
        if provenance.get("flush") not in ("awaited", "flushed"):
            return None, "the session flush was not awaited before the turn record was written"
        if not record.get("sessionId"):
            return None, "the turn record carries no session identity"
        return record, None

    def _seal_workspace(self, context: ExecutionContext) -> tuple[dict | None, str | None]:
        """Seal the actual output tree only after the owned process group is gone."""
        manifest = getattr(context, "effective_workspace", None)
        if not isinstance(manifest, dict) or not manifest:
            turn_input = context.turn_input or {}
            manifest = turn_input.get("executionWorkspace")
        if not isinstance(manifest, dict) or not manifest:
            return None, None
        state_dir = context.environment.get("BUDDY_STATE_DIR")
        if not state_dir:
            return None, "the attempt has no private state directory to seal into"
        try:
            from ..workflow import workspace_module

            seal = workspace_module().seal(Path(state_dir), manifest, context.task_id, context.attempt_id)
        except BoardError as error:
            return None, f"{error.code}: {error.message}"
        if not isinstance(seal, dict) or not seal.get("manifestSha256"):
            return None, "the workspace seal returned no immutable manifest"
        return seal, None


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
