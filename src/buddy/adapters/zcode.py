"""ZCode coding adapter using its native app-server and a private finish MCP."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..errors import BoardError
from ..private_dirs import context_root, native_root, ensure_private_dir
from .. import usage
from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle, ReadOnlyStructuredRequest, open_logs
from .windows_process import owned_popen
from . import turn_io
from .zcode_config import SUPPORTED_ACCESS, cli_command, provider_access_types, provider_paths
from .zcode_protocol import NativeError, decode_json

#: The public CLI bundle text the free read-only check ever reads (plan L6).
_MAX_PUBLIC_BUNDLE_BYTES = 32 * 1024 * 1024
#: The free check's in-process cache bound: it only saves rescanning large
#: public bundles and never persists, so a small bound covers the real set of
#: installed CLIs without growing with the host's filesystem.
_NATIVE_CONTRACT_CACHE_LIMIT = 8
#: Keyed by resolved path and full stat identity; the value is the contract
#: problem (``None`` qualified). Every read stats first, so a changed or missing
#: file can never borrow an older positive.
_NATIVE_CONTRACT_CACHE: OrderedDict[tuple[str, int, int, int, int, int], str | None] = OrderedDict()


class ZcodeAdapter(Adapter):
    name = "zcode"
    #: ``observe`` publishes bounded native activity. ``inquiry`` is the
    #: cooperative checkpoint channel: a Host question is queued by the
    #: controller's bridge and delivered only at the root's own
    #: ``buddy_checkpoint``/``buddy_answer_inquiry`` session tools inside the one
    #: admitted native turn, never injected through a native send, command,
    #: restart or new turn (see zcode_protocol.COOPERATIVE_INQUIRY_NOTE). The
    #: native input protocol still has no turn-bound in-turn method, so native
    #: permissions stay auto-denied and native questions still end as
    #: structured attention.
    capabilities = ("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session")
    native_resume = True
    model_discovery = True
    no_tool_structured = True
    # The review entry starts one restricted root native session per answer
    # round through the accepted L6-A protocol helper: native ``mode: plan``, a
    # Read/Glob/Grep tool allowlist, no MCP servers, no off-peak or dynamic
    # workflow tools and no title generation. ZCode has no native OS sandbox,
    # so none is claimed; the blackboard alone judges the recorded tool facts.
    read_only_structured = True
    read_only_structured_resume = True
    read_only_tool_categories = ("read", "search")

    def start_no_tool_structured(self, context, request):
        from .read_only import start_no_tool
        return start_no_tool(self.name, context, request)

    def start_read_only_structured(self, context: ExecutionContext,
                                   request: ReadOnlyStructuredRequest) -> ProcessHandle:
        """Start the restricted review controller after re-verifying its contract.

        The free check runs against ``context.environment`` — the CLI selection
        this attempt will actually start — so a qualified default installation
        can never endorse a different command.
        """
        problem = self._native_contract_check(context.environment)
        if problem is not None:
            raise BoardError("UNSUPPORTED_ADAPTER", problem, adapter=self.name)
        from .read_only import start
        return start(self.name, context, request)

    def local_read_only_check(self) -> dict:
        result = super().local_read_only_check()
        if not result["eligible"]:
            return result
        problem = self._native_contract_check()
        if problem is not None:
            return {**result, "eligible": False, "reasonCode": "readonly-native-contract-unverified",
                    "reason": problem}
        return result

    def _native_contract_check(self, environment: dict | None = None) -> str | None:
        """The first read-only contract problem in the public CLI bundle text.

        Locating the bundle uses only the CLI selection the runner itself would
        start — the explicit dev-source path or an already selected harness
        record — so this check is pure filesystem: it never runs ``--version``,
        an app-server or a session, and it never reads provider configuration
        or credentials. A CLI that is missing, not a public JS/CJS/MJS bundle,
        unreadable, over 32 MiB or without the restricted mechanisms returns
        its problem string; ``None`` only means the installed public text
        carries the mechanisms — the runtime still verifies the actual native
        session, model and mode reports. Results are cached per resolved path
        and stat identity; a changed or missing file never borrows an older
        positive, and the cache never decides by version or hash.
        """
        env = os.environ if environment is None else environment
        try:
            if env.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_ZCODE_CLI"):
                path = Path(env["BUDDY_ZCODE_CLI"]).expanduser()
                if not path.is_absolute():
                    return "the public ZCode CLI path must be absolute for a read-only contract check"
                path = path.resolve()
                if not path.is_file():
                    raise NativeError("adapter-unavailable", "ZCode CLI is missing")
                command = [str(path)]
            else:
                from ..harness_runtime import selected
                record = selected("zcode", env)
                if record is None:
                    return ("the ZCode CLI selection is not resolvable without a native probe; "
                            "the public read-only bundle was not checked")
                if record.get("status") != "ready" or not record.get("command"):
                    return "the selected ZCode CLI is not ready for a public bundle check"
                command = list(record["command"])
        except (NativeError, BoardError) as error:
            detail = (str(error) or "the CLI selection is unavailable")[:200]
            return f"the public ZCode CLI bundle could not be located for the read-only contract check: {detail}"
        cli = Path(command[-1]).expanduser()
        if not cli.is_absolute():
            return "the selected public ZCode CLI path must be absolute for a read-only contract check"
        if cli.suffix not in (".js", ".cjs", ".mjs"):
            return "the located ZCode CLI is not a public JS/CJS/MJS bundle to check"
        try:
            resolved = cli.resolve()
            info = resolved.stat()
            if not stat.S_ISREG(info.st_mode):
                return "the located ZCode CLI bundle is not a regular public file"
            if info.st_size > _MAX_PUBLIC_BUNDLE_BYTES:
                return "the public ZCode CLI bundle exceeds the 32 MiB read-only contract bound"
        except OSError:
            return "the public ZCode CLI bundle could not be examined"
        key = (str(resolved), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if key in _NATIVE_CONTRACT_CACHE:
            _NATIVE_CONTRACT_CACHE.move_to_end(key)
            return _NATIVE_CONTRACT_CACHE[key]
        try:
            with resolved.open("rb") as stream:
                before = os.fstat(stream.fileno())
                content = stream.read(_MAX_PUBLIC_BUNDLE_BYTES + 1)
                after = os.fstat(stream.fileno())
            if len(content) > _MAX_PUBLIC_BUNDLE_BYTES:
                return "the public ZCode CLI bundle exceeds the 32 MiB read-only contract bound"
            latest = resolved.stat()
            identities = [(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
                          for item in (info, before, after, latest)]
            if len(set(identities)) != 1:
                return "the public ZCode CLI bundle changed during the read-only contract check"
            text = content.decode("utf-8", errors="replace")
        except (OSError, UnicodeError):
            return "the public ZCode CLI bundle could not be read"
        from .zcode_read_only import native_contract_problem
        problem = native_contract_problem(text)
        _NATIVE_CONTRACT_CACHE[key] = problem
        while len(_NATIVE_CONTRACT_CACHE) > _NATIVE_CONTRACT_CACHE_LIMIT:
            _NATIVE_CONTRACT_CACHE.popitem(last=False)
        return problem

    def available(self) -> tuple[bool, str | None]:
        from ..harness_runtime import selected
        record = selected("zcode")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        try:
            cli_command()
            paths = provider_paths()
            if not any(t in SUPPORTED_ACCESS for t in provider_access_types(*paths).values()):
                return False, "ZCode has no configured API-key provider; OAuth account providers are unavailable through this adapter"
            return True, None
        except NativeError as error:
            return False, str(error)

    def prepare(self, context: ExecutionContext) -> None:
        context.private_adapter = self.name
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
        root = ensure_private_dir(native_root(Path(state), self.name, context.task_id))
        # The controller hosts the private inquiry bridge: read-only activity is
        # always available, and a Host question is queued for cooperative
        # delivery at the root's next checkpoint (see
        # zcode_protocol.COOPERATIVE_INQUIRY_NOTE). No native command may inject
        # one.
        inquiry = turn_io.inquiry_paths(context)
        private_control = context_root(context, self.name) / "zcode-control.json"
        turn_io.private_json(private_control, {
            "directory": str(context.directory.resolve()), "privateRoot": str(context_root(context, self.name).resolve()), "nativeRoot": str(root.resolve()),
            "cwd": str(Path(turn_io.workspace_cwd(context)).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()),
            "inquiry": {k: inquiry[k] for k in ("socketPath", "resultsPath", "errorPath", "token")},
            "spec": {k: context.spec[k] for k in ("provider", "model", "effort") if context.spec.get(k)},
        })

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        from ..harness_runtime import controller_environment
        paths = context.log_paths()
        stdout, stderr = open_logs(paths)
        try:
            process = owned_popen([sys.executable, "-m", "buddy.adapters.zcode_runner", "--control",
                                        str(context_root(context, self.name) / "zcode-control.json")],
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
        if getattr(handle, "no_tool", False):
            from .read_only import collect
            return collect(handle)
        payload = _read_result(Path(handle.log_paths["stdout"]))
        exit_code = handle.process.returncode
        # The native app server owns another group. Its runner receipt plus the
        # outer group's disappearance are both required, including on cancellation.
        shutdown = bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True and handle.shutdown_confirmed())
        if payload is None:
            payload = {"status": "invalid-result", "error": "the ZCode controller produced no complete JSON result"}
        # ADR-018 items 22/23 and the retained root assistant text. ZCode exposes
        # no quota-window interface, so ``quota`` is honestly null unless the
        # native records prove one; the canonical shapes come from ``buddy.usage``
        # and a value the native records never proved stays null.
        payload["tokenUsage"] = usage.normalize_token_usage(payload.get("tokenUsage"))
        payload["quota"] = usage.normalize_quota(payload.get("quota"))
        payload["quotaFailure"] = usage.normalize_quota_failure(payload.get("quotaFailure"))
        payload["lastAssistantMessage"] = usage.normalize_last_assistant_message(
            payload.get("lastAssistantMessage"), source="zcode/session-root-assistant-message")
        status = "cancelled" if (handle.cancel_requested or payload.get("status") == "cancelled") and shutdown else "failed"
        if not handle.cancel_requested and exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        record, error = turn_io.read_turn(context, shutdown, exit_code, self.validate_turn_provenance)
        # A refused native interactive request must reach the Host as attention.
        # The session-private finish tool refuses a completed outcome while such a
        # request is outstanding; this second check covers a receipt issued before
        # the refusal was recorded, so a refused approval can never be delivered
        # as silently completed work.
        attention = payload.get("nativeAttention") if isinstance(payload.get("nativeAttention"), dict) else {}
        attention_requests = attention.get("requests") if type(attention.get("requests")) is int else 0
        payload["attentionRequired"] = attention_requests > 0
        if (attention_requests > 0 and record is not None
                and (record.get("outcome") or {}).get("disposition") == "completed"):
            error = ("a native interactive request was refused during this turn; a completed outcome "
                     "cannot stand in for the Host attention that request requires")
        seal_error = None
        payload["turnResultPath"] = str(context.turn_output_file())
        payload["nativeSession"] = _native_session(payload, context, shutdown, record)
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
        from ..harness_runtime import controller_environment
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
                process = owned_popen([sys.executable, "-m", "buddy.adapters.zcode_runner", "--control", str(control)],
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
                raise BoardError("ADAPTER_UNAVAILABLE", "native ZCode model discovery did not settle successfully", adapter=self.name)
            return payload["catalog"]
        finally:
            # A killed controller cannot establish that its separate native group
            # stopped. Preserve its private state in that case, just like a turn.
            if stopped:
                shutil.rmtree(directory)


def _native_session(payload: dict, context: ExecutionContext, shutdown_confirmed: bool,
                    record: dict | None = None) -> dict:
    """Truthful native-session facts for one ZCode attempt.

    Git isolation and native session storage are separate dimensions. The root
    session lives in this goal's private ZCode store, so the installed ZCode app
    does not list it; a continuation can resume it only when the private binding
    for the exact session still exists and the attempt settled with real shutdown
    evidence.
    """
    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else (record if isinstance(record, dict) else {})
    provenance = turn.get("provenance") if isinstance(turn.get("provenance"), dict) else {}
    session_id = payload.get("sessionId") or turn.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    mode = (context.turn_input or {}).get("resumeMode") if isinstance(context.turn_input, dict) else None
    bound = False
    if session_id:
        try:
            control = json.loads((context_root(context, "zcode") / "zcode-control.json").read_text())
            native_root = Path(control["nativeRoot"])
            binding = native_root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")
            bound = binding.is_file()
        except (OSError, ValueError, KeyError, TypeError):
            bound = False
    return {
        "adapter": "zcode",
        "sessionId": session_id,
        "captured": session_id is not None,
        "storageScope": "task-private",
        "storageOwner": "buddy-goal",
        "nativeAppVisibility": "not-listed-in-native-app",
        "resumeMode": mode,
        "bindingPresent": bound,
        "resumable": bool(bound and shutdown_confirmed and provenance.get("settlement") == "session-closed"),
        "note": (
            "the root session is stored in this goal's private ZCode native root (ZCODE_SESSION_DB_PATH/"
            "ZCODE_STORAGE_DIR); the installed ZCode app lists only sessions in its own user home, so the "
            "checkable entrypoint is this attempt's activity, tool summary and fixed artifacts"
        ),
    }


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
             ("native-attention", context.directory / "attention.json"),
             *(("runner-" + key, Path(value)) for key, value in handle.log_paths.items() if key in ("stdout", "stderr"))]
    out = []
    for kind, path in paths:
        if path.is_file():
            out.append({"kind": kind, "location": str(path.resolve()), "sizeBytes": path.stat().st_size,
                        "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()})
    return out
