"""The one native Claude Code run: a frozen request in, one factual result out.

ADR-025 step 3-B1, plus the 3-C StructuredOutput correction. This module is the
single native execution body of the Claude Code harness: the policy gates, the
isolated settings, the owned native process, the initialize handshake with the
catalog and account check, the user-message boundary, the send/wait/drain
settlement and the two-layer stop evidence happen exactly once here, driven
only by the frozen :class:`~hey_my_buddy.buddy.harnesses.run_contract.RunRequest`.
What differed between the governed Worker turn and the read-only structured
call is expressed by the request (tool scope, output schema) and by the role
observer's feedback, never by a second native path; both carriers share the
same send/wait/drain primitive. The CLI's ``--json-schema`` value carrier — the
built-in ``StructuredOutput`` tool — is identified by its native facts alone
(this run's schema command line, the confirmed root, the exact built-in name,
the call id and the final structured value it delivered) and travels as
completion evidence, never as an ordinary tool call.

The driver owns protocol integrity only: the preallocated session identity,
the message boundary, the explicit terminal result criteria, background-task
settling, quota and token usage, the observed model set and costs, and the
conservative stop evidence. Unknown stays ``None``/``unknown``, a spawned
process is never a started model, and a handshake never counts as a model
start. Role material — the governed prompt, the completion rules, the outcome
validation and the attention assembly — stays with the roles; the delivered
value travels with its own facts and the role re-validates it.

This harness has no native no-tool run, no in-run session service and no
native session resume, and this module adds none: the request gates refuse
them before any process starts. Model discovery (:func:`run_discovery`) is a
separate initialize-only operation that reuses the same spawn and stop
primitives and never sends a user message.

3-B2 wiring: this module is also the registered seam's role surface. The
shared role executor consumes :func:`check_preparation` before any spawn,
:func:`prepare_run_services` for the one narrow path binding this harness
actually uses (the activity sidecar directory — never the role control file,
the worker input or a blackboard credential), :func:`session_facts` and
:func:`validate_turn_provenance` on collection, and :func:`bind_live_channel`
for the existing activity-reading live channel. ``services`` therefore accepts
exactly that binding or ``None`` (a direct native module call); anything else
is still refused. The legacy ``runner.py`` controller and its execution
entries are gone; there is no second native path.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ....errors import BoardError
from ....json_codec import canonical_json
from ....private_dirs import ensure_private_dir
from ....protocol.activity import ActivitySidecar
from ....protocol.usage import identifier as _usage_identifier
from ...roles.turn_io import private_json
from ..base import ProcessHandle
from ...runtime.windows_process import owned_popen
from ..run_contract import (
    CheckedConfiguration,
    CheckedValue,
    CompletionEvidence,
    ContinuationFacts,
    EffectivePolicy,
    EvidenceRef,
    InterruptEvidence,
    NativeIdentity,
    PolicyFact,
    ResultConfiguration,
    RunConfiguration,
    RunEnd,
    RunFeedback,
    RunRequest,
    RunResult,
    RunValue,
    StopEvidence,
    StopLayer,
)
from .config import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL_ALIAS,
    TOOL_DENIAL_MESSAGE,
    ClaudeUnavailable,
    account_problem,
    cli_command,
    discovery_args,
    execution_args,
    native_environment,
    read_auth_status,
    sandbox_settings,
    settings_policy,
    third_party_overrides,
    token_source_missing,
)
from .protocol import (
    QUOTA_REJECTED_ERROR,
    ClaudeProtocolError,
    Connection,
    QuotaRejected,
    TurnEvidence,
    model_usage_keys,
    result_quota_denial,
    total_cost_usd,
)
from ..live import EXISTING_CAPABILITIES, ExistingLiveChannel
from .tool_evidence import ReadOnlyToolEvidence

_SETTINGS_FILE = "settings.json"
_NATIVE_STDERR_FILE = "native.stderr.log"
_MAX_EARLY_FRAMES = 128
_MAX_DENIED_REQUESTS = 32
_DRAIN_SECONDS = 10.0

#: The request controls this run module actually consumes: the sandbox network
#: allowlist (None keeps the native default, an explicit sequence — empty
#: included — is written verbatim) and the session-wide extra denied tools.
#: The common ``run_harness`` refuses a request carrying a control the module
#: does not declare; declaring one commits the module to reading that field,
#: never to inferring the policy from the tool scope or the caller's role.
supported_request_controls = ("network_allowed_domains", "additional_denied_tools")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _catalog(initialize: dict, version: str, verify_login=None) -> tuple[dict, dict]:
    """The catalog from initialize only: no user message, no model turn.

    ``verify_login`` is the bounded auth-status fallback for the one eligible
    shape (first-party provider, tokenSource absent or null); any other account
    problem, or a failed readback, is refused with its bounded reason. The
    discovery receipt's account fact comes from that same validation: reaching
    the model list means this session's account readback (or its one bounded
    readback) proved the first-party login, so the entry is ``confirmed``; a
    refused account produces no reading at all, and its reason travels on the
    error for the board to treat as an unknown reading.
    """
    account = initialize.get("account")
    problem = account_problem(account)
    if problem:
        if verify_login is None or not token_source_missing(account):
            raise ClaudeProtocolError("first-party-auth-required", problem)
        auth_problem = verify_login()
        if auth_problem:
            raise ClaudeProtocolError("first-party-auth-required", auth_problem)
    entries = initialize.get("models")
    if not isinstance(entries, list):
        raise ClaudeProtocolError("invalid-catalog", "Claude returned no model list")
    if len(entries) > 200:
        raise ClaudeProtocolError("invalid-catalog", "Claude model list exceeds the catalog bound")
    models, warnings, seen = [], [], set()
    for item in entries:
        if not isinstance(item, dict) or item.get("value") == DEFAULT_MODEL_ALIAS:
            continue
        model_id = item.get("resolvedModel")
        if not isinstance(model_id, str) or not model_id or len(model_id) > 128:
            warnings.append("A model without a resolved identity was omitted")
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        levels = item.get("supportedEffortLevels")
        if levels is None or levels == []:
            efforts = [DEFAULT_EFFORT]
        elif not isinstance(levels, list) or len(levels) > 16 or any(
                level not in ("low", "medium", "high", "xhigh", "max") for level in levels):
            warnings.append("A model with an invalid effort directory was omitted")
            continue
        else:
            efforts = list(dict.fromkeys(levels))
        models.append({"id": model_id, "name": item.get("displayName") or model_id,
                       "description": item.get("description") or "", "efforts": efforts,
                       "inputModalities": ["text"], "available": True})
    catalog = {"source": "claude-code-initialize", "adapter": "claude", "harnessVersion": version,
               "discoveredAt": datetime.now(timezone.utc).isoformat(),
               "providers": [{"adapter": "claude", "provider": "anthropic",
                              "displayName": "Anthropic Claude (first-party)", "packageName": "claude-code",
                              "packageVersion": version, "models": models}],
               "discoveries": [{"adapter": "claude", "status": "complete",
                                "accountStatus": "confirmed"}],
               "warnings": list(dict.fromkeys(warnings))}
    return catalog, {"apiProvider": account.get("apiProvider"), "tokenSource": account.get("tokenSource")}


def _interrupt(connection: Connection | None) -> bool:
    if connection is None:
        return False
    try:
        # A fresh short control budget permits a native interrupt after the main deadline.
        connection.cancelled = threading.Event()
        connection.deadline = time.monotonic() + 2
        connection.call({"subtype": "interrupt"})
        return True
    except (ClaudeProtocolError, QuotaRejected, OSError, ValueError):
        # A late quota frame while awaiting interrupt acknowledgement must not
        # escape the cleanup path and suppress the controller's stop receipt.
        return False


def _latest_rejected(rate_limits: dict) -> tuple[str, object]:
    for rate_limit_type in reversed(list(rate_limits)):
        if rate_limits[rate_limit_type].get("status") == "rejected":
            return rate_limit_type, rate_limits[rate_limit_type].get("resetsAt")
    return "unknown", None


def execution_deadline(timeout_seconds) -> float:
    """The one overall native execution deadline; an explicit 0 means unlimited.

    Only this deadline becomes infinite. The version probe and the per-request,
    cancel and shutdown waits keep their own finite bounds, and the ``cancelled``
    event still ends an unlimited turn.
    """
    return math.inf if timeout_seconds == 0 else time.monotonic() + timeout_seconds


class _ObserverInterrupt(Exception):
    """The role observer's stop; the driver interrupts the native run at once."""


class _CancelFlag:
    """A ``threading.Event`` view of the seam's cancel callable."""

    def __init__(self, cancelled: Callable[[], bool]):
        self._cancelled = cancelled
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set() or bool(self._cancelled())

    def set(self) -> None:
        self._event.set()

    def wait(self, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))


@dataclasses.dataclass
class _Preparation:
    """The run's frozen startup decisions, made once before any spawn."""

    session_id: str
    deadline: float
    invocation_root: Path
    command: list[str]
    environment: dict
    args: list[str]
    network_domains: list


@dataclasses.dataclass
class _Spawn:
    process: subprocess.Popen
    handle: ProcessHandle
    connection: Connection
    version: str | None


@dataclasses.dataclass
class _RunState:
    """The run's own end facts, recorded per stage as it actually happened.

    ``catalog_checked``, ``terminal_ok`` and ``drained`` carry what the run
    really reached: a result never claims a checked configuration before the
    catalog check confirmed it, never claims a settled turn before the
    terminal criteria passed, and never claims a complete stream without the
    observed end of the native output.
    """

    status: str = "error"
    reason: str | None = None
    error_text: str | None = None
    quota_failure: dict | None = None
    catalog_checked: bool = False
    terminal_ok: bool = False
    drained: bool | None = None
    shutdown: bool = False
    exit_code: int | None = None
    interrupt_requested: bool = False
    #: The interrupt control request's own real outcome: the native peer's
    #: acknowledged reply, never an inference from the later process stop.
    interrupt_acknowledged: bool | None = None
    token_usage: dict | None = None
    last_assistant_message: dict | None = None
    #: The frozen delivery verification, settled once before the role's settled
    #: facts so the count the role acts on and the package agree (3-C).
    verified_delivery: tuple = ()


def _flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


# -- phase 1: the frozen startup decisions ---------------------------------------


def _prepare(request: RunRequest) -> _Preparation:
    """Gate the request, allocate the session and freeze the native invocation."""
    if request.tool_scope == "none":
        raise BoardError("INVALID_ARGUMENT",
                         "claude has no native no-tool run; the fast structured call is not a capability "
                         "of this harness")
    read_only = request.tool_scope == "read"
    if request.continuation is not None and request.continuation.mode == "native-session":
        raise BoardError("INVALID_ARGUMENT",
                         "claude has no native session resume; a continuation must reconstruct a "
                         "fresh session")
    invocation_root = ensure_private_dir(Path(request.private_state.invocation_root))
    native_root = ensure_private_dir(Path(request.private_state.native_root))
    session_id = str(uuid.uuid4())
    if request.continuation is not None and request.continuation.previous_session_id == session_id:
        raise ClaudeProtocolError("wrong-native-session",
                                  "a reconstructed claude turn must allocate a fresh session id")
    deadline = execution_deadline(request.budget.timeout_seconds)
    incoming = dict(os.environ)
    # Refusals and the policy gate inspect the incoming environment before the
    # allowlisted child environment would drop the offending names.
    overrides = third_party_overrides(incoming)
    if overrides:
        raise ClaudeProtocolError(
            "third-party-provider",
            "Claude execution refuses third-party provider overrides: " + ", ".join(overrides))
    if settings_policy(incoming) is None:
        raise ClaudeProtocolError(
            "settings-policy-unsupported",
            "Claude P1 supports only BUDDY_CLAUDE_SETTINGS_POLICY=isolated; an explicit unsupported "
            "settings policy was supplied")
    command = cli_command(incoming)
    environment = native_environment(incoming)
    # The network allowlist and the extra session-wide denials come from the
    # request controls alone: None keeps the native default under every scope,
    # and an offline or extra-denied run is the caller's explicit decision.
    settings = sandbox_settings(request.network_allowed_domains)
    private_json(native_root / _SETTINGS_FILE, settings)
    args = execution_args(session_id=session_id, model=request.configuration.model,
                          effort=request.configuration.effort,
                          settings_path=str(native_root / _SETTINGS_FILE),
                          read_only=read_only, output_schema=request.output_schema.value,
                          additional_denied_tools=request.additional_denied_tools)
    return _Preparation(session_id=session_id, deadline=deadline, invocation_root=invocation_root,
                        command=command, environment=environment, args=args,
                        network_domains=list(settings["sandbox"]["network"]["allowedDomains"]))


# -- phase 2: the owned native process -------------------------------------------


def _probe_version(command: list[str], cwd: str, environment: dict) -> str:
    """The bounded diagnostic version probe; never a paid call."""
    try:
        version_result = subprocess.run([*command, "--version"], cwd=cwd, env=environment,
                                        capture_output=True, timeout=5)
        if version_result.returncode != 0:
            return "unknown"
        return version_result.stdout.decode(errors="replace").strip()[:80]
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _spawn_native(*, command: list[str], args: list[str], cwd: str, environment: dict,
                  invocation_root: Path, deadline: float, cancel: _CancelFlag,
                  owned: list | None = None) -> _Spawn:
    """Spawn the one owned native CLI and connect its stdio protocol."""
    version = _probe_version(command, cwd, environment)
    stderr_path = invocation_root / _NATIVE_STDERR_FILE
    fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        process = owned_popen([*command, *args], cwd=cwd, env=environment, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=fd, start_new_session=True, close_fds=True)
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    try:
        connection = Connection(process, deadline, cancel)
    except BaseException:
        # The spawn helper is the only holder: it stops the child itself and
        # leaves its observed facts for the result's stop layer, so a failed
        # connection setup never leaks a process behind a protocol error.
        handle.wait(3.0)
        if not handle.shutdown_confirmed(settle_seconds=0.2):
            handle.terminate(grace_seconds=1.0)
        if owned is not None:
            owned.append({"shutdown": handle.shutdown_confirmed(settle_seconds=0.5),
                          "exit_code": process.returncode})
        raise
    return _Spawn(process=process, handle=handle, connection=connection, version=version)


def _native_request_response(frame: dict) -> dict:
    """The controller's answer to one native control request: deny, or error."""
    request = frame.get("request") or {}
    if request.get("subtype") == "can_use_tool":
        return {"type": "control_response",
                "response": {"subtype": "success", "request_id": frame["request_id"],
                             "response": {"behavior": "deny", "message": TOOL_DENIAL_MESSAGE}}}
    return {"type": "control_response",
            "response": {"subtype": "error", "request_id": frame["request_id"],
                         "error": "This Buddy Worker controller does not provide this native callback"}}


# -- phase 3: the initialize handshake and the catalog check ---------------------


def _initialize(spawn: _Spawn, preparation: _Preparation, request: RunRequest) -> dict:
    """Initialize once and return the catalog the response itself reported.

    A missing/null tokenSource is verified through the SAME resolved
    executable, allowlisted child environment and cwd as this execution; every
    other account problem is refused outright by the catalog builder.
    """
    initialize = spawn.connection.call({"subtype": "initialize"})
    catalog, _account = _catalog(initialize, spawn.version or "unknown",
                                 verify_login=lambda: read_auth_status(
                                     preparation.command, cwd=request.cwd,
                                     environment=preparation.environment))
    return catalog


def _check_catalog(request: RunRequest, catalog: dict) -> None:
    """The requested provider/model/effort must be in the current native catalog."""
    configuration = request.configuration
    if configuration.provider != "anthropic" or not any(
            model["id"] == configuration.model and configuration.effort in model["efforts"]
            for model in catalog["providers"][0]["models"]):
        raise ClaudeProtocolError("invalid-configuration",
                                  "the selected Claude model and effort are not in the current native catalog")


# -- the live collectors of one execution run ------------------------------------


class _TurnCollector:
    """The live observers of one run: tool facts, turn evidence, activity, the role observer.

    Frames are projected before any rule can drop them: the shared tool-fact
    collector sees every frame first, the turn evidence follows, and only then
    does the role observer hear the changed facts. The user-message boundary
    buffers everything the CLI emitted before the user frame and rejects
    pre-user model output at replay, keeping its projected facts.
    """

    def __init__(self, request: RunRequest, sidecar: ActivitySidecar,
                 observer: Callable[[Mapping[str, Any]], RunFeedback],
                 native_schema_delivery: bool = False):
        self._sidecar = sidecar
        self._observer = observer
        self.tools = ReadOnlyToolEvidence({"adapter": "claude", "taskId": request.identity.task_id,
                                           "attemptId": request.identity.attempt_id,
                                           "generation": request.identity.generation},
                                          native_schema_delivery=native_schema_delivery)
        self.evidence: TurnEvidence | None = None
        self.denied: list[dict] = []
        self.unsupported_requests = 0
        self.early_messages: list[tuple[bool, dict]] = []
        self.user_sent = False
        self.last_activity: dict | None = None
        self._connection: Connection | None = None
        self._last_tool_calls = 0
        self._quiet = False

    def install(self, connection: Connection) -> None:
        self._connection = connection
        connection.on_message = self.buffer
        connection.on_request = self.on_request

    def buffer(self, message: dict) -> None:
        # Frames are tagged by whether the user message had already been sent
        # when they arrived; model output from before it is rejected at replay.
        if len(self.early_messages) >= _MAX_EARLY_FRAMES:
            raise ClaudeProtocolError("invalid-protocol", "Claude exceeded the initialization frame bound")
        self.early_messages.append((self.user_sent, message))

    def on_request(self, frame: dict) -> None:
        request = frame.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            if len(self.denied) >= _MAX_DENIED_REQUESTS:
                raise ClaudeProtocolError("native-request-limit",
                                          "Claude exceeded the native permission request bound")
            tool = request.get("tool_name")
            tool_id = request.get("tool_use_id")
            request_id = frame.get("request_id")
            self.denied.append({
                "toolName": tool[:80] if isinstance(tool, str) and tool else "<unknown>",
                "toolUseId": tool_id[:256] if isinstance(tool_id, str) and tool_id else None,
                # The control request's own identity: the durable correlation
                # for a denial when the native frame carried no tool_use_id.
                "requestId": request_id[:256] if isinstance(request_id, str) and request_id else "<missing>"})
            self._connection.send(_native_request_response(frame))
            # The retained fact reaches the role before any further native
            # waiting, so a stop takes effect at once after the peer's answer.
            self._notify(settled=False, raw=None)
        else:
            self.unsupported_requests += 1
            self._connection.send(_native_request_response(frame))

    def send_input(self, connection: Connection, session_id: str, cwd: str, input_text: str) -> None:
        # The user-message boundary: anything the CLI already emitted is
        # pre-user output; the model is counted as started from this send on.
        connection.pump_available()
        self.user_sent = True
        connection.send({"type": "user", "message": {"role": "user",
                       "content": [{"type": "text", "text": input_text}]}})
        self.evidence = TurnEvidence(session_id, cwd)
        connection.on_message = self.observe

    def replay_early(self) -> None:
        for was_sent, message in self.early_messages:
            if not was_sent and message.get("type") in ("assistant", "result", "stream_event"):
                # Its facts are still collected before the boundary rejection.
                self.tools.observe_frame(message)
                raise ClaudeProtocolError("native-turn-started-early",
                                          "Claude emitted model output before the governed user message")
            self.observe(message)
        self.early_messages.clear()

    def observe(self, frame: dict) -> None:
        # Projection precedes every check: a frame a later rule rejects keeps
        # its tool facts, and the evidence observation follows the same rule.
        self.tools.observe_frame(frame)
        if self.evidence is not None:
            activity = self.evidence.observe(frame)
            if activity:
                self.publish_activity(*activity)
        if self.tools.tool_calls != self._last_tool_calls:
            self._last_tool_calls = self.tools.tool_calls
            self._notify(settled=False, raw=None)

    def publish_activity(self, phase: str, tool: str | None = None) -> None:
        if self.evidence is None:
            return
        now = _now()
        payload = {"phase": phase, "observedAt": now, "eventSeq": self.evidence.event_seq,
                   "nativeSessionId": self.evidence.session_id, "lastNativeActivityAt": now,
                   "counts": {"modelTurns": self.evidence.model_messages,
                              "toolCalls": self.evidence.tool_calls}}
        if tool:
            payload["lastToolActivityAt"] = now
            payload["toolName"] = tool[:80]
        self.last_activity = payload
        try:
            self._sidecar.publish(payload)
        except BoardError:
            # Metadata must never fail the native turn; the last payload stays
            # the run's own activity fact instead of a look-alike file.
            return

    def settled_notify(self, native_result: dict) -> None:
        if "structured_output" not in native_result:
            self._notify(settled=True, raw=None)
            return
        self._notify(settled=True, raw=canonical_json(native_result.get("structured_output")))

    def facts(self, *, settled: bool, raw: str | None) -> dict:
        return {"settled": settled, "rawAnswer": raw,
                "toolCalls": self.tools.tool_calls,
                "deniedInteractions": len(self.denied),
                # This harness's projection does not classify unknown events;
                # the honest absence is reported, never a fabricated zero.
                "unknownEvents": None}

    def _notify(self, *, settled: bool, raw: str | None) -> None:
        if self._quiet:
            # The stop already happened; frames the interrupt's own wait
            # delivers keep their projected facts but no further feedback.
            return
        feedback = self._observer(self.facts(settled=settled, raw=raw))
        if not isinstance(feedback, RunFeedback):
            raise BoardError("INVALID_ARGUMENT", "the observer must answer with one RunFeedback")
        if feedback.action == "correct":
            raise BoardError("INVALID_ARGUMENT",
                             "claude takes no in-run correction; the native -p turn ends at its result")
        if feedback.action == "stop":
            raise _ObserverInterrupt()


# -- phase 4: the shared send/wait/drain settlement ------------------------------


def _send_and_settle(collector: _TurnCollector, spawn: _Spawn, preparation: _Preparation,
                     request: RunRequest) -> dict:
    """Deliver the input and wait for the observed end of the native stream.

    This one primitive serves the write carrier and the read carrier alike:
    draw the user-message boundary, send the role-assembled input text, wait
    for the terminal result frame, then close the input and keep validating
    trailing frames until the transport's own end of stream.
    """
    connection = spawn.connection
    collector.send_input(connection, preparation.session_id, request.cwd, request.input_text)
    collector.replay_early()
    collector.publish_activity("waiting-model")
    evidence = collector.evidence
    while evidence.result is None:
        connection.pump()
    # A quiet window cannot prove the stream ended: close the input and keep
    # validating trailing frames until the observed end of stream, so a delayed
    # duplicate, result or quota frame is still rejected.
    try:
        spawn.process.stdin.close()
    except OSError:
        pass
    connection.drain_until_closed(
        min(_DRAIN_SECONDS, max(0.0, preparation.deadline - time.monotonic())))
    return evidence.result


def _check_terminal(evidence: TurnEvidence, session_id: str) -> dict:
    """The explicit terminal criteria; never a silent win, quota classified first."""
    native_result = evidence.result
    result_denials = native_result.get("permission_denials")
    if isinstance(result_denials, list):
        evidence.permission_denials = min(len(result_denials), 128)
    if not evidence.init_observed:
        raise ClaudeProtocolError("native-init-missing", "Claude did not report the session initialization")
    if native_result.get("session_id") != session_id:
        raise ClaudeProtocolError("wrong-native-session", "Claude returned an unexpected session identity")
    # A terminal result may arrive while reported background agents or
    # workflows still run; that must not be imported as completed work.
    if evidence.unsettled_background_tasks():
        raise ClaudeProtocolError("background-work-unsettled",
                                  "Claude reported unsettled background work at the terminal result")
    subtype = native_result.get("subtype")
    # Success is explicit: subtype "success" with is_error exactly false.
    # Missing fields, wrong types or unknown subtypes are failures (with the
    # structured quota denial classified first), never silent wins.
    if subtype != "success" or native_result.get("is_error") is not False:
        if result_quota_denial(native_result):
            raise QuotaRejected(*_latest_rejected(evidence.rate_limits))
        raise ClaudeProtocolError("native-turn-failed",
                                  "the Claude native turn did not end with an explicit success result")
    return native_result


# -- phase 5: the conservative stop ----------------------------------------------


def _stop_native(spawn: _Spawn, deadline: float) -> tuple[bool, int | None]:
    """Close, wait, then terminate; report only what the owned group confirmed."""
    try:
        spawn.process.stdin.close()
    except OSError:
        pass
    spawn.handle.wait(min(3.0, max(0.0, deadline - time.monotonic())))
    if not spawn.handle.shutdown_confirmed(settle_seconds=0.2):
        spawn.handle.terminate(grace_seconds=1.0)
    shutdown = spawn.handle.shutdown_confirmed(settle_seconds=0.5)
    try:
        spawn.process.stdout.close()
    except OSError:
        pass
    return shutdown, spawn.process.returncode


# -- phase 6: the factual result -------------------------------------------------


def _identity_or_none(fields: dict) -> NativeIdentity | None:
    try:
        return NativeIdentity(**fields)
    except BoardError:
        return None


def _usable(converter: Callable[[dict], Any], value: dict | None) -> dict | None:
    """One package its own canonical projection accepted, or ``None``.

    A value the package's projection refuses is dropped alone; every other
    observed fact of the run keeps its place, so no fallback blankets known
    facts into unknowns.
    """
    if value is None:
        return None
    try:
        return value if converter(value) is not None else None
    except BoardError:
        return None


def _retain(evidence_refs: list, invocation_root: Path, kind: str, name: str, value: dict) -> None:
    target = invocation_root / name
    private_json(target, value, exclusive=True)
    raw = target.read_bytes()
    evidence_refs.append(EvidenceRef(kind=kind, location=str(target), size_bytes=len(raw),
                                     sha256=hashlib.sha256(raw).hexdigest()))


def _native_observations(collector: _TurnCollector, native_result: dict | None,
                         state: _RunState) -> dict:
    """The observed session model, model set, costs and quota windows, as facts.

    Each value keeps its native source; none is ever filled from a requested
    configuration, and an unobserved one stays null. ``quota_failure`` carries
    the frozen facts of one native quota rejection only: the legacy bounded
    receipt shape travels in this package for the role's projection, and an
    unrejected run writes nothing. The interrupt pair appears only when an
    interrupt was actually requested, as the strict booleans the shared
    projection keeps — the acknowledged value is the peer's own reply, never
    an inference from the process stop.
    """
    observations = {"sessionModel": collector.evidence.session_model if collector.evidence else None,
                    "observedModels": model_usage_keys(native_result) if native_result is not None else [],
                    "totalCostUsd": total_cost_usd(native_result) if native_result is not None else None,
                    "unsupportedNativeRequests": min(collector.unsupported_requests, 128)}
    if state.quota_failure is not None:
        observations["quotaFailure"] = _quota_failure(
            {"rateLimitType": state.quota_failure.get("nativeCode"), "resetsAt": state.quota_failure.get("resetsAt")})
    if state.interrupt_requested:
        observations["nativeInterruptRequested"] = True
        observations["nativeInterruptAcknowledged"] = state.interrupt_acknowledged is True
    if collector.evidence is not None:
        observations["quota"] = collector.evidence.quota_candidate()
        observations["rateLimitObservations"] = dict(sorted(collector.evidence.rate_limits.items()))
    return observations


def _quota_failure(value: object) -> dict:
    """Only rateLimitType and resetsAt, typed and bounded; never native error text.

    The legacy collect's own bounded projection, kept verbatim: an identifier
    that cannot be named becomes ``unknown``, and the reset time keeps its
    original bounded string or finite number instead of being normalized.
    """
    source = value if isinstance(value, dict) else {}
    resets = source.get("resetsAt")
    return {"rateLimitType": _usage_identifier(source.get("rateLimitType")) or "unknown",
            "resetsAt": resets if isinstance(resets, str) and len(resets) <= 64
            else resets if isinstance(resets, (int, float)) and not isinstance(resets, bool) and math.isfinite(resets)
            else None}


def _native_turn_facts(collector: _TurnCollector, session_id: str, subtype: str) -> dict:
    """The native fact parts of the governed turn's provenance.

    The role judgments of the old provenance document — whether the structured
    output was validated, the turn-end wording, the controller attention —
    stay with the role; only what this driver actually observed is retained
    here for the role's own record assembly.
    """
    evidence = collector.evidence
    native_result = evidence.result
    facts: dict[str, Any] = {"adapter": "claude", "nativeSessionId": session_id,
                             "resultSubtype": subtype if isinstance(subtype, str) and 0 < len(subtype) <= 64 else None,
                             # The terminal criteria already required is_error to be
                             # exactly false; that observed native fact travels with
                             # the record the role assembles.
                             "resultIsError": False,
                             "structuredOutputSource": "json-schema",
                             "initObserved": True, "backgroundSettled": True,
                             "eventSeq": evidence.event_seq,
                             "sessionModel": evidence.session_model,
                             "modelUsage": model_usage_keys(native_result),
                             "permissionDenials": evidence.permission_denials,
                             "deniedControlRequestIds": [item["requestId"] for item in collector.denied],
                             "deniedToolUseIds": [item["toolUseId"] for item in collector.denied
                                                  if item["toolUseId"]]}
    if collector.denied:
        facts["deniedToolNames"] = [item["toolName"] for item in collector.denied[:32]]
    return facts


def _effective_policy(preparation: _Preparation) -> EffectivePolicy:
    """The native posture actually placed on the command line, read back from it."""
    tools_value = _flag_value(preparation.args, "--tools")
    disallowed_value = _flag_value(preparation.args, "--disallowedTools")
    requested = {"tools": tools_value.split(",") if tools_value else [],
                 "permissionMode": _flag_value(preparation.args, "--permission-mode"),
                 "disallowedTools": disallowed_value.split(",") if disallowed_value else [],
                 "sandboxNetworkAllowedDomains": list(preparation.network_domains)}
    return EffectivePolicy(
        tools=PolicyFact(requested=requested))


def _representable_value(structured) -> RunValue | None:
    """The delivered value as one :class:`RunValue`, or ``None`` when it cannot be carried.

    The raw text's and the parsed form's bounds are the run contract's own; a
    delivery that exceeds them is dropped alone. The completion evidence, the
    stop facts, the checked configuration and every usage package keep their
    places, and the run's end status stays what the native interaction
    actually reported — an unrepresentable value is never allowed to erase
    the confirmed facts around it.
    """
    try:
        return RunValue(schema_status="unknown",
                        raw=canonical_json(structured), parsed=structured)
    except BoardError:
        return None


def _build_result(request: RunRequest, *, state: _RunState, collector: _TurnCollector | None,
                  spawn: _Spawn | None, owned_spawn: list, preparation: _Preparation | None,
                  native_result: dict | None) -> RunResult:
    from ....protocol.activity import normalize_activity
    from ....protocol.usage import (
        normalize_last_assistant_message,
        normalize_quota_failure,
        normalize_token_usage)
    from ..run_contract import _validated_tool_evidence

    evidence = collector.evidence if collector is not None else None
    user_sent = collector.user_sent if collector is not None else False
    session_id = preparation.session_id if preparation is not None else None
    # The preallocated id becomes this run's native identity only once the
    # native handshake itself confirmed it: TurnEvidence accepts an init frame
    # only when its session_id equals the preallocation, so ``init_observed``
    # is the native's own attribution, never the driver's. A foreign identity
    # the stream reported stays in the fact packages (the tool roots below),
    # never masquerading as this run's root.
    session_confirmed = evidence is not None and evidence.init_observed
    native_identity = _identity_or_none({"session_id": session_id}) if session_confirmed and session_id else None
    stream_ended = state.drained is True
    # The delivery identification froze at settlement, before the role's
    # settled facts: this run's schema command line armed the collector, the
    # handshake-confirmed root, the terminal result and the final structured
    # value it carried proved the call. A turn that never settled counts every
    # candidate in its package; the frozen tuple is the one classification the
    # role and the package share.
    verified_delivery = state.verified_delivery
    tool_package = None
    if spawn is not None and collector is not None:
        # The stream's own observed end is a transport fact, reported exactly
        # as observed: it is never downgraded by the business verdict nor by
        # the process-group stop, which travel in their own fields. The one
        # exclusion is the verified built-in delivery call — the value carrier,
        # never an ordinary task tool.
        tool_package = _usable(_validated_tool_evidence, collector.tools.finish(
            stream_complete=stream_ended, exclude_calls=verified_delivery))
    value = None
    completion = None
    if native_result is not None and "structured_output" in native_result:
        value = _representable_value(native_result.get("structured_output"))
    if state.terminal_ok and native_result is not None:
        # The delivery facts report what the transport and the terminal checks
        # each observed on their own: the stream's end is the drain's fact,
        # never back-inferred from the process-group stop. A verified built-in delivery call stays excluded from
        # tool facts; an unverified call remains counted there.
        completion = CompletionEvidence(stream_end=stream_ended)
    checked = CheckedConfiguration()
    if state.catalog_checked:
        # The checked block restates the requested values the native checks
        # confirmed: the account readback proved the first-party provider, the
        # initialize catalog proved the model and effort membership. No
        # applied-effort readback exists in the native result, so none is
        # claimed for it.
        checked = CheckedConfiguration(
            provider=CheckedValue(value=request.configuration.provider),
            model=CheckedValue(value=request.configuration.model),
            effort=CheckedValue(value=request.configuration.effort))
    effective = _effective_policy(preparation) if preparation is not None else EffectivePolicy()
    continuation = None
    if session_confirmed and session_id is not None:
        continuation = ContinuationFacts(resumable=False)
    evidence_refs: list[EvidenceRef] = []
    if spawn is not None and collector is not None:
        invocation_root = preparation.invocation_root
        stderr_path = invocation_root / _NATIVE_STDERR_FILE
        if stderr_path.is_file():
            raw_stderr = stderr_path.read_bytes()
            evidence_refs.append(EvidenceRef(kind="native-stderr", location=str(stderr_path),
                                             size_bytes=len(raw_stderr),
                                             sha256=hashlib.sha256(raw_stderr).hexdigest()))
        if collector.denied:
            # The refused interactions' own full records — native request and
            # tool identities included — stay as their clearly-sourced evidence
            # reference consumed by the role.
            _retain(evidence_refs, invocation_root, "denied-interactions", "denied-interactions.json",
                    {"records": collector.denied})
        if evidence is not None or state.interrupt_requested:
            _retain(evidence_refs, invocation_root, "native-observations", "native-observations.json",
                    _native_observations(collector, native_result, state))
        if state.status == "ok" and native_result is not None:
            _retain(evidence_refs, invocation_root, "native-turn-facts", "native-turn-facts.json",
                    _native_turn_facts(collector, session_id, native_result.get("subtype")))
    if spawn is None:
        if owned_spawn and isinstance(owned_spawn[0], dict):
            created = owned_spawn[0]
            stop_native = StopLayer(group_state="gone" if created["shutdown"] else "unknown")
        else:
            # No process object ever existed: the holding side itself confirms
            # the spawn never happened, the one honest "gone" without a process.
            stop_native = StopLayer(group_state="gone")
    else:
        stop_native = StopLayer(group_state="gone" if state.shutdown else "unknown")
    interrupt = InterruptEvidence(
        requested=True if state.interrupt_requested else None,
        basis="claude/control-request-interrupt" if state.interrupt_requested else None)

    def compose(*, packages: bool) -> RunResult:
        return RunResult(
            identity=request.identity, harness="claude",
            end=RunEnd(status=state.status, reason_code=state.reason,
                       native_exit_code=state.exit_code,
                       message=state.error_text),
            harness_version=spawn.version if spawn is not None else None,
            native_event_count=evidence.event_seq if evidence is not None and evidence.event_seq else None,
            model_started=True if user_sent else None,
            configuration=ResultConfiguration(
                requested=RunConfiguration(provider=request.configuration.provider,
                                           model=request.configuration.model,
                                           effort=request.configuration.effort),
                checked=checked),
            native_identity=native_identity,
            value=value if packages else None,
            completion_evidence=completion if packages else None,
            tool_evidence=tool_package if packages else None,
            effective_policy=effective,
            activity=_usable(normalize_activity,
                             collector.last_activity) if collector is not None and packages else None,
            usage=_usable(normalize_token_usage, state.token_usage) if packages else None,
            native_failure=_usable(normalize_quota_failure, state.quota_failure) if packages else None,
            native_error=None,
            last_assistant_message=_usable(normalize_last_assistant_message,
                                           state.last_assistant_message) if packages else None,
            continuation=continuation,
            stop_evidence=StopEvidence(native=stop_native, interrupt=interrupt),
            evidence_refs=tuple(evidence_refs))

    try:
        return compose(packages=True)
    except BoardError:
        # A fact that cannot be represented in the common result is the run's
        # own failure, never an unhandled escape. The fallback keeps every
        # already-observed stage fact — identity, model start, the confirmed
        # configuration, the stop evidence, the retained references — and drops
        # only the packages whose own shape failed, so nothing observed becomes
        # unknown again.
        state.status = "error"
        state.reason = "invalid-native-result"
        state.error_text = "the native execution returned invalid or incomplete data"
        return compose(packages=False)


# -- the one run -----------------------------------------------------------------


def run(request: RunRequest, *, observer: Callable[[Mapping[str, Any]], RunFeedback],
        services: Any, cancelled: Callable[[], bool]) -> RunResult:
    """Run one native Claude Code execution: the single native path of this harness."""
    if request.harness != "claude":
        raise BoardError("INVALID_ARGUMENT", "this run module drives claude", harness=request.harness)
    if services is not None and not isinstance(services, BoundRunPaths):
        # No in-run session service exists on this harness; the one accepted
        # binding is this module's own narrow activity-path binding, and a
        # direct native module call may pass None.
        raise BoardError("INVALID_ARGUMENT", "claude mounts no in-run session service")
    if request.session_services:
        raise BoardError("INVALID_ARGUMENT",
                         "claude has no native session service; the value carrier is the native schema")
    state = _RunState()
    owned_spawn: list = []
    spawn: _Spawn | None = None
    preparation: _Preparation | None = None
    collector: _TurnCollector | None = None
    native_result: dict | None = None
    cancel = _CancelFlag(cancelled)

    def request_interrupt() -> None:
        # A fresh short control budget permits a native interrupt after the
        # failure; the peer's own acknowledgement reply is kept as the fact —
        # whether the CLI answered never changes the stop facts.
        if collector is not None:
            collector._quiet = True
        acknowledged = False
        if spawn is not None:
            acknowledged = _interrupt(spawn.connection)
        state.interrupt_requested = True
        state.interrupt_acknowledged = acknowledged

    try:
        preparation = _prepare(request)
        # The activity sidecar belongs to the attempt's real activity directory
        # when the role bound one; a direct native module call without the
        # binding keeps the invocation root, never inventing a second location.
        activity_directory = Path(services.activity_dir) if services is not None else preparation.invocation_root
        collector = _TurnCollector(
            request,
            ActivitySidecar(activity_directory, task_id=request.identity.task_id,
                            attempt_id=request.identity.attempt_id,
                            generation=request.identity.generation),
            observer,
            # The delivery identification is armed by this run's own command
            # line carrying the native schema, never by the caller's role or
            # the tool scope: a run without the flag exempts nothing.
            native_schema_delivery="--json-schema" in preparation.args)
        spawn = _spawn_native(command=preparation.command, args=preparation.args, cwd=request.cwd,
                              environment=preparation.environment,
                              invocation_root=preparation.invocation_root, deadline=preparation.deadline,
                              cancel=cancel, owned=owned_spawn)
        collector.install(spawn.connection)
        _check_catalog(request, _initialize(spawn, preparation, request))
        state.catalog_checked = True
        _send_and_settle(collector, spawn, preparation, request)
        state.drained = True
        native_result = _check_terminal(collector.evidence, preparation.session_id)
        state.terminal_ok = True
        state.status = "ok"
        # The delivery verification freezes here, before the settled facts
        # reach the role: candidates that did not prove themselves against the
        # final structured value return to the count now, so a budget stop at
        # settlement sees exactly the classification the finished package will
        # publish. Failure paths never settle, and their packages count every
        # candidate; the settled callback is the one place the role's last
        # count and the package must not diverge.
        state.verified_delivery = collector.tools.settle_delivery(
            native_result.get("structured_output"), {"sessionId": preparation.session_id})
        collector.settled_notify(native_result)
    except _ObserverInterrupt:
        state.status = "cancelled"
        state.reason = "observer-interrupt"
        request_interrupt()
    except QuotaRejected as error:
        # Quota exhaustion is infrastructure unavailability: no retry, no outcome.
        state.status = "error"
        state.reason = "quota-rejected"
        state.error_text = QUOTA_REJECTED_ERROR
        state.quota_failure = {"nativeCode": error.rate_limit_type,
                               "source": "claude/stream-json-rate-limit-event",
                               "observedAt": _now(), "resetsAt": error.resets_at}
        request_interrupt()
    except ClaudeProtocolError as error:
        state.status = "cancelled" if error.code == "user-cancel" else "error"
        state.reason = error.code
        state.error_text = str(error)
        if error.code in ("user-cancel", "deadline"):
            request_interrupt()
    except ClaudeUnavailable as error:
        state.status = "error"
        state.reason = "claude-unavailable"
        state.error_text = str(error)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        state.status = "error"
        state.reason = "invalid-native-result"
        state.error_text = "the native execution returned invalid or incomplete data"
    finally:
        if collector is not None and collector.evidence is not None:
            # ADR-018 items 22/23 and the retained root assistant text: raw
            # native observations for every path, including a quota rejection,
            # a deadline or a process failure. A value the native stream never
            # reported stays null instead of being estimated.
            state.token_usage = collector.evidence.token_usage()
            state.last_assistant_message = collector.evidence.last_assistant_message()
        if spawn is not None:
            state.shutdown, state.exit_code = _stop_native(spawn, preparation.deadline)
            if state.status == "ok" and (not state.shutdown or state.exit_code != 0):
                state.status = "error"
                state.reason = "native-shutdown-failed"
                state.error_text = ("the Claude native process did not exit with confirmed "
                                    "process-group shutdown")
        if cancel.is_set() and state.status != "ok":
            state.status = "cancelled"
            state.reason = "user-cancel"
            state.error_text = "the owned Claude execution was cancelled"
    return _build_result(request, state=state, collector=collector, spawn=spawn,
                         owned_spawn=owned_spawn, preparation=preparation, native_result=native_result)


# -- the initialize-only discovery ------------------------------------------------


def _with_stop_fact(error: BaseException, stopped: bool) -> BaseException:
    """Attach the operation's own native stop fact to one discovery failure.

    The outer discovery layer recycles its directory only when it can prove
    the native side stopped, so every failure this operation raises carries
    ``discovery_shutdown_confirmed``: the owned child's actual conservative
    stop result, or the known never-started fact for a refusal before any
    spawn. An unobserved stop is always false — never inferred from the error
    code or the controller's own exit.
    """
    error.discovery_shutdown_confirmed = stopped is True
    return error


def run_discovery(*, cwd: str, invocation_root: Path, native_root: Path, timeout_seconds: int,
                  cancelled: Callable[[], bool]) -> dict:
    """One initialize-only native catalog read: no user message, no model turn.

    Discovery reuses the same spawn, handshake and conservative stop as an
    execution run. It never sends a user frame, never persists a session and
    never checks the settings policy (the isolated gate guards executions,
    not the metadata read); the third-party provider refusal still applies.
    The return value is the catalog receipt shape consumed by the registered
    discovery callers; success is returned only after the owned native group
    is confirmed stopped with a zero exit.
    """
    deadline = execution_deadline(timeout_seconds)
    cancel = _CancelFlag(cancelled)
    invocation_root = ensure_private_dir(Path(invocation_root))
    ensure_private_dir(Path(native_root))
    incoming = dict(os.environ)
    overrides = third_party_overrides(incoming)
    if overrides:
        # Refused before any spawn: the known never-started fact.
        raise _with_stop_fact(ClaudeProtocolError(
            "third-party-provider",
            "Claude execution refuses third-party provider overrides: " + ", ".join(overrides)), True)
    try:
        command = cli_command(incoming)
        environment = native_environment(incoming)
    except ClaudeUnavailable as error:
        raise _with_stop_fact(error, True) from None
    owned_spawn: list = []
    try:
        spawn = _spawn_native(command=command, args=discovery_args(), cwd=cwd, environment=environment,
                              invocation_root=invocation_root, deadline=deadline, cancel=cancel,
                              owned=owned_spawn)
    except BaseException as error:
        stopped = (owned_spawn[0]["shutdown"] is True
                   if owned_spawn and isinstance(owned_spawn[0], dict) else True)
        raise _with_stop_fact(error, stopped)
    catalog: dict | None = None
    error: Exception | None = None
    shutdown = False
    try:
        spawn.connection.on_request = lambda frame: spawn.connection.send(_native_request_response(frame))
        initialize = spawn.connection.call({"subtype": "initialize"})
        catalog, _account = _catalog(initialize, spawn.version or "unknown",
                                     verify_login=lambda: read_auth_status(command, cwd=cwd,
                                                                           environment=environment))
    except (ClaudeProtocolError, OSError, ValueError, TypeError, KeyError, AttributeError,
            RecursionError) as caught:
        error = caught
    finally:
        shutdown, _exit_code = _stop_native(spawn, deadline)
    if error is not None:
        raise _with_stop_fact(error, shutdown)
    if not shutdown or spawn.process.returncode != 0:
        raise _with_stop_fact(ClaudeProtocolError("native-shutdown-failed",
                                                  "the Claude native process did not exit with confirmed "
                                                  "process-group shutdown"), shutdown)
    return catalog


# -- the registered seam's role surface -------------------------------------------


@dataclasses.dataclass(frozen=True)
class BoundRunPaths:
    """The one narrow binding a claude run consumes: where its activity sidecar goes.

    The role hands exactly the attempt's activity directory; the whole control
    file, the worker input and every blackboard credential stay outside this
    binding, and the run reads nothing else from it.
    """

    activity_dir: Path


def prepare_run_services(*, invocation_root: Path, native_root: Path, activity_dir: Path,
                         account: dict | None, tool_scope: str) -> BoundRunPaths:
    """Bind the paths one claude run actually uses; no process, no authority.

    The shared worker/fast/review binding point passes every harness the same
    narrow materials. This harness consumes only ``activity_dir`` — the frozen
    account already reached the launch environment through the role's own
    account seam, and the tool scope already reached the request — so the
    remaining parameters are accepted and left unused, never re-derived.
    """
    return BoundRunPaths(activity_dir=Path(activity_dir))


def check_preparation(spec: dict, environment: dict) -> None:
    """The harness-side pre-spawn refusals of one prepared role execution.

    These are the checks the legacy ``prepare`` made before any process
    existed: the first-party provider requirement, the isolated settings
    policy, the third-party provider refusal and a resolvable CLI. The
    governed-turn, resume-mode and private-state rules belong to the shared
    executor and are not repeated here.
    """
    if spec.get("provider") != "anthropic":
        raise BoardError("INVALID_ARGUMENT",
                         "Claude execution requires the first-party Anthropic provider", adapter="claude")
    if settings_policy(environment) is None:
        raise BoardError("ADAPTER_UNAVAILABLE",
                         "Claude P1 supports only BUDDY_CLAUDE_SETTINGS_POLICY=isolated; "
                         "an explicit unsupported settings policy was supplied", adapter="claude")
    overrides = third_party_overrides(environment)
    if overrides:
        raise BoardError("ADAPTER_UNAVAILABLE",
                         "Claude refuses third-party provider overrides: " + ", ".join(overrides), adapter="claude")
    try:
        cli_command(environment)
    except ClaudeUnavailable as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="claude") from None


def session_facts(native_root: Path, session_id: str | None) -> dict:
    """The native storage and visibility facts of a claude session.

    The session lives in the user's own Claude Code store; this module keeps no
    private binding for it, so the fact package reports the store scope and the
    unverified visibility exactly as the legacy ``_native_session`` did. The
    role adds the resume mode and decides reuse.
    """
    return {
        "adapter": "claude", "sessionId": session_id, "captured": session_id is not None,
        "storageScope": "harness-user-store", "nativeAppVisibility": "unknown",
        "note": ("P1 allocates a fresh --session-id for every attempt, never resumes a native session and makes no "
                 "native-session claim; the session lives in the user's own Claude Code store with unverified visibility"),
    }


def validate_turn_provenance(record: dict) -> str | None:
    """The native-source validation of one imported claude turn, unchanged."""
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


def bind_live_channel(identity: RunIdentity, *, credentials: dict, journal_path: str | None,
                      activity_path: Path) -> ExistingLiveChannel:
    """The existing-facilities live binding of one governed claude run.

    The one wired facility is activity: the channel reads the attempt's own
    published sidecar, validated against the complete execution identity. This
    harness delivers no inquiry — the declared capability is ``unsupported`` —
    so no ask, journal or bridge observation is bound, and the narrow
    materials the shared binding receives are left unused rather than routed
    into a bypass.
    """
    def read_activity():
        # The existing attempt-bound sidecar reader — validation, binding and
        # throttling belong to it; a foreign attempt's file reads as nothing.
        from ....protocol import activity as activity_protocol
        return activity_protocol.read_sidecar(Path(activity_path),
                                               task_id=identity.task_id,
                                               attempt_id=identity.attempt_id,
                                               generation=identity.generation)

    return ExistingLiveChannel(identity, EXISTING_CAPABILITIES["claude"], read_activity=read_activity)


__all__ = ["BoundRunPaths", "bind_live_channel", "check_preparation", "prepare_run_services",
           "run", "run_discovery", "session_facts", "validate_turn_provenance"]
