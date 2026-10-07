"""The one native ZCode run: a frozen request in, one factual result out.

ADR-025 step 2-B. This module is the single native execution body of the
ZCode harness: create/resume, configuration readback, subscribe/send,
settlement, close, EOF drain and the owned process hold happen exactly once
here, driven only by the frozen
:class:`~hey_my_buddy.buddy.harnesses.run_contract.RunRequest`. What differs
between the Worker turn and the fast structured call is expressed by the
request (tool scope, schema, continuation, services) and by the role
observer's feedback, never by a second native path.

The driver owns protocol integrity only: root/session/input/call identity and
order, signatures, the native configuration readback, tool/activity/usage/
quota/failure facts and the two-layer stop evidence. Unknown legal events and
tool facts are normalized and retained first, then handed to the role
observer, which answers continue, stop or one correction; the driver executes
that feedback on the same app-server process and the same total deadline and
reports the correction count. It never re-assembles a task brief and never
decides whether an unknown event or a tool fact fails the run.

Model discovery (:func:`run_discovery`) is a separate no-prompt metadata
operation that reuses the same spawn and handshake primitives; it never
masquerades as a model run. The controller supplies the shared live endpoint
through this run's narrow session services.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import queue
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ....errors import BoardError
from ....json_codec import canonical_json, decode_strict_json
from ....private_dirs import ensure_private_dir
from ...roles.turn_io import private_json
from ..base import BoundSessionServices, ProcessHandle
from ...runtime.windows_process import owned_popen
from ....protocol.internal_models import OptionalFrozenJsonAt
from ..native_support import (
    CancelFlag,
    ObserverInterrupt,
    configuration_spec,
    execution_deadline,
    halt_owned_group,
    identity_or_none,
    shape_guard,
    utc_now,
)
from ..run_contract import (
    ActivityPackage,
    MAX_SCHEMA_BYTES,
    CheckedConfiguration,
    CheckedValue,
    CompletionEvidence,
    ContinuationFacts,
    EffectivePolicy,
    EvidenceRef,
    InterruptEvidence,
    NativeFailurePackage,
    UsagePackage,
    ToolEvidencePackage,
    NativeErrorRecord,
    LastAssistantMessagePackage,
    PolicyFact,
    ResultConfiguration,
    RunConfiguration,
    RunEnd,
    RunFeedback,
    RunRequest,
    RunResult,
    SessionService,
    RunValue,
    StopEvidence,
    StopLayer,
)
from .config import SUPPORTED_ACCESS, cli_command, provider_access_types, provider_paths, snapshot_provider_files
from ..inquiry_bridge import InquiryBridge
from ..c_two_live import CTwoLiveEndpoint
from ....protocol.activity import ActivityPublisher
from .protocol import (
    COOPERATIVE_INQUIRY_NOTE,
    ActivityProjection,
    NativeConnection,
    NativeError,
    RootTurnEvidence,
    ZcodeAttemptUsage,
    quota_native_code,
)
from .tool_evidence import ZcodeToolFacts

_END_FACTS_FILE = "run-end-facts.json"
_NATIVE_STDERR_FILE = "native.stderr.log"


def selected(snapshot: dict) -> dict:
    value = snapshot.get("settings", {}).get("model", {}).get("current")
    if not isinstance(value, dict) or not value.get("providerId") or not value.get("modelId"):
        raise NativeError("configuration-unavailable", "ZCode has no usable native model selection")
    return {"provider": value["providerId"], "model": value["modelId"],
            "effort": value.get("options", {}).get("reasoningLevel")}


def configure_session(connection: NativeConnection, snapshot: dict, spec: dict, access: dict) -> dict:
    session_id = snapshot["session"]["sessionId"]
    if any(not isinstance(spec.get(k), str) or not spec[k].strip() for k in ("provider", "model", "effort")):
        raise NativeError("invalid-configuration", "ZCode coding requires a complete provider, model and effort after routing")
    provider, model, effort = spec["provider"], spec["model"], spec["effort"]
    if access.get(provider) not in SUPPORTED_ACCESS:
        raise NativeError("unsupported-provider", "this ZCode adapter supports API-key providers; OAuth account providers require a native authentication host")
    choices = snapshot.get("settings", {}).get("model", {}).get("available", [])
    choice = next((x for x in choices if x.get("ref") == {"providerId": provider, "modelId": model}), None)
    if choice is None:
        raise NativeError("configuration-unavailable", "the requested ZCode provider/model is not in the native available catalog")
    reasoning = choice.get("reasoning", {})
    efforts = [x["value"] for x in reasoning.get("levels", []) if isinstance(x, dict) and isinstance(x.get("value"), str)]
    if effort not in efforts:
        raise NativeError("invalid-configuration", "the requested reasoning effort is not supported by this native model")
    target = {"providerId": provider, "modelId": model, "options": {"reasoningLevel": effort}}
    snapshot = connection.call("session/setModel", {"sessionId": session_id, "model": target, "persistAsWorkspaceLastUsed": False})
    snapshot = connection.call("session/setThoughtLevel", {"sessionId": session_id, "thoughtLevel": effort, "persistAsWorkspaceLastUsed": False})
    actual = selected(snapshot)
    expected = {"provider": provider, "model": model, "effort": effort}
    if actual != expected or snapshot.get("settings", {}).get("thoughtLevel", {}).get("current") != effort:
        raise NativeError("configuration-mismatch", "native ZCode did not retain the requested model and reasoning settings")
    return actual


def catalog(snapshot: dict, access: dict, version: str) -> dict:
    providers: dict[str, dict] = {}
    missing_effort = False
    for model in snapshot.get("settings", {}).get("model", {}).get("available", []):
        ref = model.get("ref") or {}
        provider = ref.get("providerId")
        if access.get(provider) not in SUPPORTED_ACCESS or not ref.get("modelId"):
            continue
        efforts = [x["value"] for x in model.get("reasoning", {}).get("levels", [])
                   if isinstance(x, dict) and isinstance(x.get("value"), str) and x["value"].strip()]
        if not efforts:
            missing_effort = True
            continue
        entry = providers.setdefault(provider, {"provider": provider, "displayName": model.get("providerLabel") or provider,
                                                 "adapter": "zcode", "packageVersion": version, "accessType": access[provider], "models": []})
        entry["models"].append({"id": ref["modelId"], "name": model.get("label") or ref["modelId"],
                                "efforts": efforts,
                                "contextWindow": model.get("contextWindow"), "available": True,
                                "inputModalities": [name for name in ("text", "image", "audio", "video", "pdf")
                                                    if model.get("properties", {}).get("inputFormat", {}).get("supports" + name.capitalize()) is True]})
    warnings = ["OAuth account providers are unavailable through this adapter"] if any(t == "zhipu-account" for t in access.values()) else []
    if missing_effort:
        warnings.append("Models exposing no configurable native reasoning efforts were omitted")
    if not providers:
        # A successful native snapshot with no usable API-key provider is a
        # complete observation, not a discovery failure: the service can retire
        # profiles that are missing from it instead of leaving them unknown
        # forever. The explicit ``discoveries`` status states that boundary.
        warnings.append("The native app server returned no API-key provider; this complete empty observation retires missing ZCode profiles")
    return {"source": "zcode-native-app-server", "adapter": "zcode", "harnessVersion": version,
            "discoveredAt": datetime.now(timezone.utc).isoformat(), "providers": list(providers.values()),
            "discoveries": [{"adapter": "zcode", "status": "complete"}], "warnings": warnings}


# -- the role-facing service binding -------------------------------------------


@dataclasses.dataclass(frozen=True)
class SessionServiceMount:
    """The mechanical, process-free binding of one run's session service.

    Everything here is derived without starting anything: the private MCP
    server name and the fully qualified session tool names from the attempt
    identity, the signing key, the bridge configuration the MCP carrier reads
    and the ``session/create`` mount description. The completion business
    contract, the tool texts and which tool plays which part arrive as the
    caller's own narrow materials — this harness mounts and verifies them, it
    never decides them. The role keeps its task prompt text against these
    exact names and puts the assembled input into ``RunRequest.input_text``;
    the driver mounts this same service and verifies the call evidence
    against it.
    """

    server_name: str
    finish_tool: str
    checkpoint_tool: str | None
    answer_tool: str | None
    bare_tools: tuple[str, ...]
    bridge: dict
    mcp_servers: tuple[dict, ...]
    input_id: str
    bridge_config_path: str
    #: The mounted tools' own contracts (``{"name": …, "inputSchema": …}``),
    #: exactly the tools the MCP carrier publishes for this run; the driver
    #: checks the run's requested output schema against the completion tool's
    #: contract and the refusal-envelope verification set against the full
    #: mounted names.
    tool_contracts: tuple[dict, ...] = ()


@dataclasses.dataclass(frozen=True)
class SessionServices:
    """The role-held service instance of one governed run (the seam's ``services``).

    The driver sees only this narrow binding: the mechanical mount, the role's
    outcome validator for finish-receipt verification, the inquiry bridge
    credentials (``None`` when this run carries no inquiry channel) and the
    controller-owned live endpoint and the native stderr log. No board client,
    database handle, execution context or credential file travels in it.
    """

    mount: SessionServiceMount
    validate_outcome: Callable[[object], str | None]
    inquiry: dict | None = None
    live: CTwoLiveEndpoint | None = None
    native_stderr: str | None = None


def prepare_session_service(*, invocation_root: Path, identity: dict, input_sha256: str,
                            attention_path: Path, session_tools: list[dict] | tuple[dict, ...],
                            completion_tool: str, inquiry: dict | None = None,
                            inquiry_tools: tuple[str, ...] = ()) -> SessionServiceMount:
    """Prepare one run's session-service mount; no process is started.

    The naming is the mechanical rule the native driver and the role share: one
    private MCP server per attempt, whose bare session tools become the fully
    qualified native names the governed prompt cites. The caller — the role —
    supplies this run's own narrow materials: the session tools exactly as the
    MCP carrier publishes them (name and input schema each), which of them is
    the completion tool, and which carry the inquiry channel. The bridge
    configuration (attempt identity, turn-input hash, signing key, attention
    and journal paths) is published once here; the MCP carrier and the driver's
    receipt verification read the same values. Nothing here knows the Worker
    contract or any role's business rules.
    """
    root = ensure_private_dir(Path(invocation_root))
    server_name = "buddy_" + hashlib.sha256(identity["attemptId"].encode()).hexdigest()[:16]
    qualified = {tool["name"]: f"mcp__{server_name}__{tool['name']}" for tool in session_tools}
    if completion_tool not in qualified:
        raise BoardError("INVALID_ARGUMENT",
                         "the completion tool must be one of this run's mounted session tools")
    finish_tool = qualified[completion_tool]
    inquiry_names = tuple(qualified[name] for name in inquiry_tools if name in qualified) \
        if inquiry is not None else ()
    has_inquiry = inquiry is not None
    bridge = {"identity": dict(identity), "inputSha256": input_sha256, "key": secrets.token_hex(32),
              "attentionPath": str(attention_path)}
    if has_inquiry and isinstance(inquiry.get("resultsPath"), str):
        # The MCP tools only read this journal; every record in it is written
        # by the driver after root-turn evidence verified.
        bridge["inquiryJournalPath"] = inquiry["resultsPath"]
    bridge_path = root / "finish-bridge.json"
    private_json(bridge_path, bridge, exclusive=True)
    mcp = [{"name": server_name, "command": sys.executable,
            "args": ["-m", "hey_my_buddy.buddy.roles.session_mcp", "--config", str(bridge_path)],
            "env": [{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}] if os.environ.get("PYTHONPATH") else [],
            "isolation": "session", "protocolVersion": "legacy"}]
    input_id = "buddy-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    return SessionServiceMount(
        server_name=server_name, finish_tool=finish_tool,
        checkpoint_tool=inquiry_names[0] if len(inquiry_names) > 0 else None,
        answer_tool=inquiry_names[1] if len(inquiry_names) > 1 else None,
        bare_tools=tuple(tool["name"] for tool in session_tools),
        bridge=bridge, mcp_servers=tuple(mcp), input_id=input_id, bridge_config_path=str(bridge_path),
        tool_contracts=tuple({"name": tool["name"], "inputSchema": tool["inputSchema"]}
                             for tool in session_tools))


# -- the normalized facts the role observer sees ---------------------------------


def _inquiry_event_metadata(message: dict) -> dict:
    params = message.get("params") if isinstance(message, dict) else None
    method = message.get("method") if isinstance(message, dict) else None
    kind = params.get("type") if isinstance(params, dict) else None
    if method == "state.updated":
        kind = f"state:{str((params or {}).get('reason'))[:40]}"
    data = (params or {}).get("payload") if isinstance(params, dict) else None
    return {"kind": kind or method or "event",
            "toolName": data.get("toolName") if isinstance(data, dict) else None}


def make_inquiry_bridge(credentials: dict, *, identity: dict, journal_path: str,
                        attention_path: str | None = None,
                        live: CTwoLiveEndpoint | None = None) -> InquiryBridge:
    return InquiryBridge(credentials, identity=identity, journal_path=journal_path,
                         attention_path=attention_path, live=live, error_factory=NativeError,
                         event_metadata=_inquiry_event_metadata, limitation=COOPERATIVE_INQUIRY_NOTE)


def check_preparation(spec: dict, environment: dict) -> None:
    """Confirm native capability and the selected provider without spawning."""
    try:
        cli_command(environment)
        access = provider_access_types(*provider_paths(environment))
    except NativeError as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="zcode") from None
    if access.get(spec["provider"]) not in SUPPORTED_ACCESS:
        raise BoardError("ADAPTER_UNAVAILABLE", "the requested ZCode provider is not a supported API-key provider", adapter="zcode")


def prepare_services(*, invocation_root: Path, identity: dict, input_sha256: str,
                     attention_path: Path, session_tools, completion_tool: str,
                     validate_outcome, inquiry: dict | None, inquiry_tools: tuple[str, ...],
                     native_stderr: str) -> BoundSessionServices:
    """Expose only the binding the role actually consumes, without a process."""
    mount = prepare_session_service(
        invocation_root=invocation_root, identity=identity, input_sha256=input_sha256,
        attention_path=attention_path, session_tools=session_tools, completion_tool=completion_tool,
        inquiry=inquiry, inquiry_tools=inquiry_tools)
    return BoundSessionServices(
        description=SessionService(
            tool_names=tuple(f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools)),
        completion_tool=mount.finish_tool, checkpoint_tool=mount.checkpoint_tool,
        answer_tool=mount.answer_tool,
        services=SessionServices(mount=mount, validate_outcome=validate_outcome, inquiry=inquiry,
                                 native_stderr=native_stderr))


def session_facts(native_root: Path, session_id: str | None) -> dict:
    """Report native storage and binding existence; the role decides reuse."""
    binding = native_root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json") if session_id else None
    return {
        "adapter": "zcode", "sessionId": session_id, "captured": session_id is not None,
        "storageScope": "task-private", "storageOwner": "buddy-goal",
        "nativeAppVisibility": "not-listed-in-native-app",
        "bindingPresent": binding is not None and binding.is_file(),
        "note": (
            "the root session is stored in this goal's private ZCode native root (ZCODE_SESSION_DB_PATH/"
            "ZCODE_STORAGE_DIR); the installed ZCode app lists only sessions in its own user home, so the "
            "checkable entrypoint is this attempt's activity, tool summary and fixed artifacts"),
    }


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
    expected_input = "buddy-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
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


class _RunFacts:
    """The cumulative, normalized facts of one run, as the observer sees them."""

    def __init__(self):
        self.unknown_counts: dict[str, int] = {}
        self.markers = 0
        self.denied: list[dict] = []

    def note_unknown(self, label: object) -> None:
        name = label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label) else "unknown"
        self.unknown_counts[name] = self.unknown_counts.get(name, 0) + 1

    def mapping(self, *, tool_calls: int, settled: bool, raw_answer: str | None) -> dict:
        return {
            "settled": settled,
            "rawAnswer": raw_answer,
            "toolCalls": tool_calls,
            "toolMarkerFrames": self.markers,
            "unknownEvents": {"countsByType": dict(self.unknown_counts),
                              "total": sum(self.unknown_counts.values())},
            "deniedInteractions": len(self.denied),
        }


#: Native notification methods the protocol knows at every phase.
_KNOWN_METHODS = frozenset({
    "startup/storageState", "process/mcpTelemetry", "process/mcpResourceSamples", "process/resourceSample",
    "session/event", "state.updated", "computer-use/operation-event", "v4/telemetry/event",
})
#: ``session/event`` kinds that are known before the root input is admitted.
_PRE_ADMITTED_KINDS = frozenset({
    "session.created", "session.resumed", "session.updated", "session.titleUpdated", "session.closed",
})
#: ``session/event`` kinds that are known inside an admitted turn: the model
#: stream families, the turn lifecycle and the tool/permission envelopes. The
#: kinds are facts of the native protocol; whether one is allowed is judged
#: from the retained facts, never here.
_ADMITTED_KINDS = _PRE_ADMITTED_KINDS | {
    "turn.started", "turn.completed", "turn.failed",
    "message.upserted", "message.removed", "part.started", "part.delta",
    "part.upserted", "part.removed", "model.streaming",
    "tool.updated", "permission.requested", "userInput.requested",
}


def _carries_tool_marker(value) -> bool:
    """Native tool parts count even without ids or inside child envelopes."""
    if isinstance(value, dict):
        kinds = (value.get('type', ''), value.get('kind', ''))
        if (any(isinstance(kind, str) and kind.startswith(
                ('tool', 'agent.', 'permission.', 'userInput.', 'subagent.', 'workflow.')) for kind in kinds)
                or value.get('toolCallId') is not None or value.get('role') == 'tool'
                or any(type(value.get(key)) is int and value[key] > 0 for key in ('toolCalls', 'toolCallCount'))):
            return True
        return any(_carries_tool_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_carries_tool_marker(item) for item in value)
    return False


class _Classifier:
    """Normalize unknown legal events and tool markers into retained facts.

    Classification only: nothing here fails the run. The retained counts reach
    the role observer, which decides; the driver-fatal protocol checks live in
    the session-scoped evidence objects.
    """

    def __init__(self, facts: _RunFacts):
        self.facts = facts
        self.admitted = False

    def observe(self, message: dict) -> bool:
        """Fold one notification in; return whether a new fact appeared."""
        facts = self.facts
        if _carries_tool_marker(message):
            facts.markers += 1
            return True
        method = message.get("method")
        if not isinstance(method, str) or method not in _KNOWN_METHODS:
            facts.note_unknown(method if isinstance(method, str) else "unknown-method")
            return True
        if method == "session/event":
            params = message.get("params")
            kind = params.get("type") if isinstance(params, dict) else None
            known = _ADMITTED_KINDS if self.admitted else _PRE_ADMITTED_KINDS
            if not isinstance(kind, str) or kind not in known:
                facts.note_unknown(kind if isinstance(kind, str) else "unknown-event")
                return True
        return False


class NoToolProtocol:
    """The no-tool session's protocol checks, without the policy that moved to the role.

    This keeps exactly the driver-owned guarantees of the old no-tool
    observer: lifecycle metadata order and turn binding, canonical event
    order, foreign-session refusal, the admitted turn's identity, a bounded
    successful completion and a settlement that follows it. What used to raise
    for tool markers and unknown events is now a retained fact for the role
    observer; the raw answer and the canonical event count are recorded facts
    of the run.
    """

    SESSION_EVENTS = {"session.created", "session.resumed", "session.updated", "session.titleUpdated", "session.closed"}
    OPERATION_EVENTS = {"turn-started", "turn-completed", "turn-failed", "session-closed"}
    TELEMETRY_EVENTS = {"turn.started", "model.request.status", "stream.chunk", "usage.delta", "turn.terminal"}

    def __init__(self, session_id: str, input_id: str, tools: ZcodeToolFacts | None = None):
        self.session_id, self.input_id = session_id, input_id
        self.tools = tools
        self.turn_id = None
        self.last_seq = -1
        self.completed = False
        self.settled = False
        self.raw_answer = None
        self.events = 0
        self.metadata_sequences = {}
        self.metadata_turn = None

    def observe_metadata(self, method: str, params: dict) -> None:
        """Native lifecycle projections precede canonical events; they prove no answer."""
        operation = method == "computer-use/operation-event"
        kinds = self.OPERATION_EVENTS if operation else self.TELEMETRY_EVENTS
        sequence = params.get("sequenceNumber" if operation else "eventSeq")
        if (params.get("kind") not in kinds or params.get("sessionId") != self.session_id
                or type(sequence) is not int or sequence <= self.metadata_sequences.get(method, -1)):
            raise NativeError("invalid-protocol", "invalid no-tool native lifecycle metadata")
        self.metadata_sequences[method] = sequence
        if params["kind"] == "session-closed":
            return
        turn = params.get("turnId")
        if (not isinstance(turn, str) or not turn
                or self.turn_id is not None and turn != self.turn_id
                or self.metadata_turn is not None and turn != self.metadata_turn):
            raise NativeError("wrong-native-turn", "no-tool lifecycle metadata differs from the admitted turn")
        self.metadata_turn = turn
        if params["kind"] == "turn-failed":
            raise NativeError("native-turn-failed", "no-tool native turn failed")

    def observe(self, message: dict, _ordinal: int) -> None:
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            raise NativeError("invalid-protocol", "no-tool native event has no object parameters")
        if method in ('startup/storageState', 'process/mcpTelemetry', 'process/mcpResourceSamples', 'process/resourceSample'):
            return
        if method in ("computer-use/operation-event", "v4/telemetry/event"):
            self.observe_metadata(method, params)
            return
        if method == "session/event":
            kind = params.get("type")
            if params.get("sessionId") != self.session_id:
                raise NativeError("invalid-protocol", "foreign session event in a no-tool call")
            seq = params.get("seq")
            if type(seq) is not int or seq <= self.last_seq:
                raise NativeError("invalid-protocol", "no-tool native event order is invalid")
            self.last_seq = seq
            self.events += 1
            data = params.get("payload")
            if not isinstance(data, dict):
                raise NativeError("invalid-protocol", "no-tool native event payload is invalid")
            if kind in self.SESSION_EVENTS:
                return
            if kind == "turn.started":
                if (self.turn_id is not None or data.get("inputId") != self.input_id
                        or not isinstance(params.get("turnId"), str)
                        or self.metadata_turn is not None and params.get("turnId") != self.metadata_turn):
                    raise NativeError("wrong-native-turn", "no-tool turn identity differs")
                self.turn_id = params["turnId"]
                if self.tools is not None:
                    # The trusted root identity comes only from this verified
                    # canonical turn start, never from the observed events.
                    self.tools.add_root(self.session_id, self.turn_id)
            elif not self.turn_id or params.get("turnId") != self.turn_id:
                raise NativeError("wrong-native-turn", "no-tool event differs from the admitted turn")
            elif kind == "turn.failed":
                raise NativeError("native-turn-failed", "no-tool native turn failed")
            elif kind == "turn.completed":
                if self.completed or data.get("inputId") != self.input_id or data.get("resultType") != "success":
                    raise NativeError("native-turn-failed", "no-tool native turn did not complete successfully")
                self.raw_answer = data.get("response")
                if not isinstance(self.raw_answer, str) or len(self.raw_answer.encode()) > 65536:
                    raise NativeError("invalid-native-result", "no bounded native answer")
                self.completed = True
        elif method == "state.updated":
            if params.get("sessionId") not in (None, self.session_id):
                raise NativeError("invalid-protocol", "foreign no-tool state event")
            if params.get("reason") == "prompt_failed":
                raise NativeError("native-turn-failed", "no-tool prompt failed")
            if params.get("reason") == "prompt_completed":
                if not self.completed or self.settled:
                    raise NativeError("invalid-protocol", "no-tool settlement lacks a completed turn")
                self.settled = True


# -- the run state and the spawn primitives --------------------------------------


@dataclasses.dataclass
class _RunState:
    """The run's own end facts, recorded per stage as it actually happened.

    ``session_opened``, ``configured`` and ``completion_ordinal`` carry what
    the run really reached: a result never claims an effective policy before a
    session was opened, never lists configuration checks before the readback
    confirmed them, and never reports a completed native outcome without an
    observed completion.
    """
    status: str = "error"
    reason: str | None = None
    error_text: str | None = None
    failure_kind: str | None = None
    native_failure: dict | None = None
    quota_failure: dict | None = None
    model_started: bool = False
    shutdown: bool = False
    exit_code: int | None = None
    interrupted: bool = False
    observer_stopped: bool = False
    session_opened: bool = False
    configured: bool = False
    create_params: dict | None = None
    completion_ordinal: int | None = None
    signalled: bool = False
    inquiry: dict | None = None
    attention: dict | None = None
    token_usage: dict | None = None
    last_assistant_message: dict | None = None
    drain_error: tuple[str, str] | None = None

    def note_drain_error(self, error: NativeError) -> None:
        if self.drain_error is None:
            self.drain_error = (error.code, str(error))
        if self.status == "ok":
            self.status = "error"
            self.reason = error.code
            self.error_text = str(error)


@dataclasses.dataclass
class _Spawn:
    process: subprocess.Popen
    handle: ProcessHandle
    connection: NativeConnection
    access: dict
    version: str | None


def _spawn_app_server(*, cwd: str, invocation_root: Path, native_root: Path, deadline: float,
                      cancel: CancelFlag, stderr_path: Path, no_tools: bool,
                      owned: list | None = None) -> _Spawn:
    """Spawn the one owned app-server, ready for the native handshake.

    The handshake itself (``runtime/capabilities``) stays with the caller: a
    failure there must flow through the run's own stop collection like every
    other native failure, so the owned group is never left unwatched. The
    moment ``owned_popen`` succeeds this helper is the only holder: it records
    the handle on ``owned`` and, if anything fails before the connection
    exists, it stops what it started itself and reports those stop facts —
    no child is ever left alive by a lost handle.
    """
    from ..discovery import native_environment
    root = ensure_private_dir(native_root)
    incoming = dict(os.environ)
    command = cli_command(incoming)
    clean = native_environment(incoming, command=command)
    private = ensure_private_dir(invocation_root)
    environment, access = snapshot_provider_files(private, {**incoming, **clean})
    # These paths were generated by the driver above, not inherited model endpoints.
    clean.update({key: environment[key] for key in ('ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE')})
    environment = clean
    if incoming.get("BUDDY_DEV_SOURCE") == "1" and "BUDDY_ZCODE_TEST_CASE" in incoming:
        environment["BUDDY_ZCODE_TEST_CASE"] = incoming["BUDDY_ZCODE_TEST_CASE"]
    # ZCODE_MODEL_TELEMETRY_ENABLED=0 short-circuits the single telemetry gate in
    # the installed bundle before it reads ZCODE_HOME (telemetry-state.json is only
    # touched by that path), so no telemetry state can reach the real user home.
    # The session DB, storage and logs stay in this run's private native root.
    environment.update({"ZCODE_STORAGE_DIR": str(root / "storage"), "ZCODE_SESSION_DB_PATH": str(root / "sessions.sqlite"),
                        "ZCODE_LOG_DIR": str(private / "native-logs"), "ZCODE_LOG_CONSOLE": "0",
                        "ZCODE_MODEL_TELEMETRY_ENABLED": "0"})
    try:
        version_result = subprocess.run([*command, "--version"], env=environment, cwd=cwd, capture_output=True, timeout=5)
        version = version_result.stdout.decode(errors="replace").strip()[:80] if version_result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        # Version text is optional metadata. A slow --version probe must not
        # suppress the actual native capability/catalog handshake below.
        version = None
    fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        process = owned_popen([*command, "app-server", "--cwd", cwd], env=environment,
                              cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                              start_new_session=True, close_fds=True)
    except OSError:
        raise NativeError("adapter-unavailable", "The selected ZCode executable could not start") from None
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    if owned is not None:
        owned[:] = [handle]
    try:
        connection = NativeConnection(process, deadline, cancel, no_tools=no_tools)
    except BaseException:
        # The connection setup failed after the child existed: this helper is
        # still the only holder, so it stops the group itself and leaves the
        # observed stop facts for the result before re-raising.
        shutdown, _signalled = halt_owned_group(process, handle, time.monotonic() + 5.0)
        if owned is not None:
            owned[:] = [{"created": True, "shutdown": shutdown,
                         "exit_code": process.returncode}]
        raise
    return _Spawn(process=process, handle=handle, connection=connection, access=access, version=version)


def _stop_native(spawn: _Spawn, deadline: float) -> tuple[bool, bool]:
    """Stop the run's owned app-server through the shared halt primitive."""
    return halt_owned_group(spawn.process, spawn.handle, deadline)


def _drain(connection: NativeConnection, tools: ZcodeToolFacts, classifier: _Classifier,
           protocol, facts: _RunFacts, state: _RunState,
           notify: Callable[..., RunFeedback]) -> bool:
    """Keep every stopped stream frame, even after an earlier failure.

    The order of every frame is the run's own: projection first, then
    classification, then the role's answer to the retained facts — its stop,
    not the driver's scope reading, is what marks the stream incomplete — and
    only then the protocol layer's own settlement checks, which keep their
    established codes. A native interaction or an unclaimed response after
    settlement, or a frame that cannot be recorded, stays a protocol failure
    of the settlement itself. Whether any recorded call was allowed is judged
    only by the blackboard, never here.
    """
    complete = True

    def failed(error: NativeError):
        nonlocal complete
        complete = False
        state.note_drain_error(error)

    while True:
        try:
            message = connection.messages.get(timeout=1)
        except queue.Empty:
            failed(NativeError("invalid-protocol", "the native stream ended without EOF"))
            return False
        if message is None:
            return complete
        if isinstance(message, NativeError):
            failed(message)
            continue
        try:
            # Projection precedes rejection, and a failed frame never prevents
            # the following frames from being projected too.
            tools.observe(message)
            changed = classifier.observe(message)
            if changed:
                # The retained fact goes to the role before any protocol
                # check of this frame runs; only the observer's stop ends
                # the stream.
                try:
                    notify()
                except ObserverInterrupt:
                    raise NativeError(
                        "observer-interrupt",
                        "the role observer stopped the run during the EOF drain") from None
            protocol.observe(message, 0)
            if "id" in message and "method" in message:
                raise NativeError("no-tool-violation", "native interaction after settlement")
            if "id" in message:
                raise NativeError("invalid-protocol", "unclaimed native response after settlement")
        except NativeError as error:
            failed(error)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, BoardError):
            tools.evidence.observe_incomplete("zcode", {})
            failed(NativeError("invalid-protocol", "a native frame could not be recorded"))


def _bridge_identity(request: RunRequest) -> dict:
    return {"taskId": request.identity.task_id, "attemptId": request.identity.attempt_id,
            "generation": request.identity.generation, "turnId": request.identity.turn_id}


def _continuation_of(request: RunRequest) -> tuple[str, str | None]:
    continuation = request.continuation
    if continuation is None:
        return "initial", None
    previous = continuation.previous_session_id
    if previous is not None and (not isinstance(previous, str) or not previous.strip()):
        raise NativeError("invalid-resume-mode", "the previous native session identity must be null or a nonblank string")
    if continuation.mode == "native-session":
        if not isinstance(previous, str) or not previous:
            raise NativeError("native-resume-unavailable", "native resume requires the exact previous session identity")
        return "native-session", previous
    if continuation.mode == "reconstructed-new-session":
        return "reconstructed-new-session", previous
    raise NativeError("invalid-resume-mode", "ZCode requires initial, an explicitly bound native-session or a reconstructed-new-session turn")


def _checked_root_session(snapshot: dict, cwd: str, *, previous: str | None, mode: str,
                          require_interactive: bool) -> str:
    session = snapshot.get("session") or {}
    session_id = session.get("sessionId")
    if (not isinstance(session_id, str) or not session_id or session.get("parentSessionId")
            or require_interactive and session.get("sessionKind") not in (None, "interactive")):
        raise NativeError("wrong-native-session", "ZCode did not create or restore a root session")
    if mode == "native-session" and session_id != previous:
        raise NativeError("wrong-native-session", "ZCode resumed a different native session")
    if mode == "reconstructed-new-session" and session_id == previous:
        raise NativeError("wrong-native-session", "ZCode reused the previous session instead of creating the requested new root")
    native_cwd = (session.get("workspace") or {}).get("workspacePath")
    if not native_cwd or Path(native_cwd).resolve() != Path(cwd).resolve():
        raise NativeError("wrong-native-workspace", "ZCode session checkout does not match the allocated workspace")
    return session_id


def _open_root(connection: NativeConnection, workspace: dict, *, create_params: dict,
               resume: str | None = None) -> dict:
    """The one create/resume site: open this run's root native session."""
    if resume is not None:
        return connection.call("session/resume", {"sessionId": resume, "workspace": workspace,
                                                  "mcpServers": create_params.get("mcpServers", [])})
    return connection.call("session/create", {"workspace": workspace, **create_params})


def _admit_root_input(connection: NativeConnection, *, snapshot: dict, request: RunRequest,
                      spec: dict, access: dict, input_id: str, input_text: str,
                      previous: str | None, mode: str, require_interactive: bool,
                      settled: Callable[[], bool], classifier: "_Classifier",
                      state: _RunState, on_configured: Callable[[str, dict], None] | None = None,
                      before_send: Callable[[str], None] | None = None,
                      after_admit: Callable[[str], None] | None = None) -> tuple[str, dict]:
    """The one configure/subscribe/send/settlement site of every root session.

    Both carriers spell their differences as parameters: how the session was
    opened, whether the root must be interactive, what input id and text this
    round admits, when settlement is proven, and the two carrier-specific
    moments — binding this round's observers before the input is sent and
    activating the live channel after the input was admitted. The lifecycle
    itself — root checks, configuration readback, subscription, admission, the
    settle pump — exists exactly once here.
    """
    session_id = _checked_root_session(snapshot, request.cwd, previous=previous, mode=mode,
                                       require_interactive=require_interactive)
    resolved = configure_session(connection, snapshot, spec, access)
    if on_configured is not None:
        # The run keeps its configuration facts the moment the native readback
        # confirmed them, so a later failure still reports what was resolved.
        on_configured(session_id, resolved)
    connection.call("session/subscribe", {"sessionId": session_id,
                                          "deliveryKind": "web-remote-replayable",
                                          "includeSnapshot": False})
    classifier.admitted = True
    if before_send is not None:
        before_send(session_id)
    state.model_started = True
    accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id,
                                                "content": input_text})
    if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
        raise NativeError("native-admission-failed", "the root input was not admitted by the native session")
    if after_admit is not None:
        after_admit(session_id)
    while not settled():
        connection.pump()
    return session_id, resolved


def _close_root_session(connection: NativeConnection, session_id: str,
                        tools: ZcodeToolFacts | None) -> None:
    """The one close site; an unacknowledged close always fails the run."""
    if tools is not None:
        tools.close_pending = True
    try:
        closed = connection.call("session/close", {"sessionId": session_id})
        if closed.get("closed") is not True:
            raise NativeError("session-close-unconfirmed",
                              "the native session close was not acknowledged")
    finally:
        if tools is not None:
            tools.close_pending = False


def _check_service_descriptions(request: RunRequest, mount: SessionServiceMount) -> tuple[str, dict]:
    """Verify the mount is exactly what this run describes, and name its parts.

    Three sets must be one set: the qualified tool names the request describes,
    the tools the mount actually carries (the same list the MCP carrier
    publishes), and — through the mount's contracts — the verification set the
    receipt checks use. The requested output schema must be the completion
    tool's own contract, so a verified receipt can only ever stand for the
    schema the request asked for. Returns the completion tool's bare name and
    contract.
    """
    described = {name for service in request.session_services for name in service.tool_names}
    mounted = {f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools}
    if described and described != mounted:
        raise BoardError("INVALID_ARGUMENT",
                         "the described session services differ from this run's mounted binding")
    completion = next((tool for tool in mount.tool_contracts
                       if f"mcp__{mount.server_name}__{tool['name']}" == mount.finish_tool), None)
    if completion is None:
        raise BoardError("INVALID_ARGUMENT",
                         "the mounted binding carries no contract for its completion tool")
    if request.output_schema.value != completion["inputSchema"]:
        raise BoardError("INVALID_ARGUMENT",
                         "the requested output schema differs from the mounted completion contract; "
                         "this run module takes its final value through that contract")
    return completion["name"], completion


# -- the one run -----------------------------------------------------------------


@dataclasses.dataclass
class _CarrierOutcome:
    """What the executed carrier actually reached, in the run's own order.

    The owner loop and the carrier phase share exactly this record: every
    field keeps its honest initial value until the carrier's own stage really
    set it, so a later failure still reports the session, configuration and
    evidence the run had really reached.
    """
    session_id: str | None = None
    turn_id: str | None = None
    resolved: dict | None = None
    raw_answer: str | None = None
    event_count: int = 0
    correction_count: int = 0
    final_message_completed: bool = False
    last_protocol: NoToolProtocol | None = None
    evidence: RootTurnEvidence | None = None
    projection: ActivityProjection | None = None
    attempt_usage: ZcodeAttemptUsage | None = None
    inquiry_bridge: InquiryBridge | None = None
    binding_path: Path | None = None
    record: dict | None = None
    verified_outcome: dict | None = None
    verified_completion: dict | None = None


def run(request: RunRequest, *, observer: Callable[[Mapping[str, Any]], RunFeedback],
        services: Any, cancelled: Callable[[], bool]) -> RunResult:
    """Run one native ZCode execution: the single native path of this harness."""
    if request.harness != "zcode":
        raise BoardError("INVALID_ARGUMENT", "this run module drives zcode", harness=request.harness)
    if services is not None and not isinstance(services, SessionServices):
        raise BoardError("INVALID_ARGUMENT", "zcode accepts its own narrow session service binding")
    no_tool = request.tool_scope == "none"
    # The tool scope only decides the native tool settings; the value carrier
    # follows this run's service binding alone: a bound completion service
    # takes the final value through its completion tool, and a run without one
    # takes it from the final message — under any scope, with the role
    # observer deciding what tool and unknown facts mean for it.
    completion_carrier = services is not None
    if services is not None and no_tool:
        raise BoardError("INVALID_ARGUMENT",
                         "a no-tool zcode run mounts no session service; the scope and the value carrier are separate choices")
    if services is None and request.continuation is not None:
        raise BoardError("INVALID_ARGUMENT",
                         "a final-message zcode run opens fresh root sessions and continues no native one")
    return _run(request, services=services, no_tool=no_tool,
                completion_carrier=completion_carrier, observer=observer, cancelled=cancelled)


def _run(request: RunRequest, *, services: Any, no_tool: bool, completion_carrier: bool,
         observer: Callable[[Mapping[str, Any]], RunFeedback],
         cancelled: Callable[[], bool]) -> RunResult:
    """The owner loop: one spawn and handshake, one dispatch to the request's
    carrier, one stop collection, one factual result."""
    deadline = execution_deadline(request.budget.timeout_seconds)
    invocation_root = ensure_private_dir(Path(request.private_state.invocation_root))
    native_root = ensure_private_dir(Path(request.private_state.native_root))
    stderr_path = (Path(services.native_stderr) if services is not None and services.native_stderr
                   else invocation_root / _NATIVE_STDERR_FILE)
    cancel = CancelFlag(cancelled)

    facts = _RunFacts()
    classifier = _Classifier(facts)
    state = _RunState()
    # Every run projects task-tool facts under every scope and carrier: the
    # collector's binding is the request's own execution identity, the
    # projection spans every correction session and is installed before the
    # runtime handshake so no native tool fact is lost, and a completion-tool
    # run excludes only its own mounted delivery calls — the final value's
    # evidence is the receipt, never a task-tool count of the delivery tool.
    tools = ZcodeToolFacts({"adapter": "zcode", "taskId": request.identity.task_id,
                            "attemptId": request.identity.attempt_id,
                            "generation": request.identity.generation})
    outcome = _CarrierOutcome()
    spawn: _Spawn | None = None
    owned_spawn: list = []
    drained: bool | None = None

    def notify(*, settled: bool = False, raw: str | None = None) -> RunFeedback:
        facts_snapshot = facts.mapping(tool_calls=tools.tool_calls if tools is not None else 0,
                                       settled=settled, raw_answer=raw)
        feedback = observer(facts_snapshot)
        if not isinstance(feedback, RunFeedback):
            raise BoardError("INVALID_ARGUMENT", "the observer must answer with one RunFeedback")
        if feedback.action == "correct" and not settled:
            raise BoardError("INVALID_ARGUMENT", "a correction is only answerable at a settled run")
        if feedback.action == "stop":
            raise ObserverInterrupt()
        return feedback

    feedback_due = [False]

    def attention_sink(record_item: dict) -> None:
        # Record the fact first; the pump still answers the request itself
        # right after this returns. The role's answer to the retained fact is
        # taken at the driver's next pump boundary — before any further native
        # waiting — so a stop takes effect at once without bypassing the
        # recorder or the refusal I/O.
        facts.denied.append(record_item)
        if outcome.inquiry_bridge is not None:
            outcome.inquiry_bridge.note_attention(record_item)
        feedback_due[0] = True

    def dispatch_feedback() -> None:
        # NativeConnection invokes this after every pump step, including the
        # steps inside RPC reply waits. Its reverse-request refusal is already
        # sent, and the role can stop before any further native waiting.
        if feedback_due[0]:
            feedback_due[0] = False
            notify()

    try:
        spawn = _spawn_app_server(cwd=request.cwd, invocation_root=invocation_root, native_root=native_root,
                                  deadline=deadline, cancel=cancel, stderr_path=stderr_path, no_tools=no_tool,
                                  owned=owned_spawn)
        connection = spawn.connection
        connection.attention = attention_sink
        connection.after_pump = dispatch_feedback
        connection.call("runtime/capabilities", {})
        workspace = {"workspacePath": request.cwd, "workspaceKey": request.cwd}

        if not completion_carrier:
            _final_message_rounds(connection=connection, request=request, spawn=spawn,
                                  workspace=workspace, no_tool=no_tool, facts=facts,
                                  classifier=classifier, state=state, tools=tools,
                                  notify=notify, outcome=outcome)
        else:
            _governed_turn(connection=connection, request=request, services=services, spawn=spawn,
                           workspace=workspace, invocation_root=invocation_root, native_root=native_root,
                           facts=facts, classifier=classifier, state=state, tools=tools,
                           notify=notify, outcome=outcome)
    except ObserverInterrupt:
        state.status = "cancelled"
        state.reason = "observer-interrupt"
        state.observer_stopped = True
        outcome.record = None
    except NativeError as error:
        state.status = "cancelled" if error.code == "cancelled" else "error"
        state.reason = error.code
        state.error_text = str(error)
        if error.code == "native-disconnected":
            state.failure_kind = "transport"
        # Whitelisted native failure attribution (only the exported turn.failed
        # event supplies it) is observable on the failed result; it never
        # changes the status, code or failureKind, so this stays a harness
        # error distinct from a deadline, a user cancel or a transport break.
        failure = getattr(error, "failure", None)
        if isinstance(failure, dict):
            state.native_failure = failure
            # A structured native failure code that means quota exhaustion is
            # published as the explicit quota reason; provider prose is never read
            # and no retry or probe is attempted.
            native_code = quota_native_code(failure)
            if native_code is not None:
                state.quota_failure = {"nativeCode": native_code, "source": "zcode/session-turn-failed",
                                       "observedAt": utc_now()}
        # A failed turn still retains its native observations: the bounded read is
        # optional, so it can never turn this failure into another one.
        if outcome.attempt_usage is not None and spawn is not None:
            outcome.attempt_usage.capture_final(spawn.connection)
        outcome.record = None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        state.status = "error"
        state.reason = "invalid-native-result"
        state.error_text = "the native execution returned invalid or incomplete data"
        outcome.record = None
    finally:
        drained = _stop_collection(spawn=spawn, deadline=deadline, cancel=cancel, state=state,
                                   tools=tools, classifier=classifier, facts=facts, notify=notify,
                                   completion_carrier=completion_carrier, outcome=outcome)
    tool_package = None
    if state.session_opened:
        # A stream is complete only when it was read to its EOF with the owned
        # group confirmed stopped — the same rule for both carriers; anything
        # less is unproven, never complete by assumption.
        stream_complete = bool(state.status == "ok" and state.shutdown
                               and drained is True
                               and not tools.close_pending)
        exclude = outcome.evidence.verified_delivery() if outcome.evidence is not None else frozenset()
        tool_package = tools.finish(stream_complete, exclude_calls=exclude)
    evidence_refs = _evidence_refs(invocation_root=invocation_root, stderr_path=stderr_path,
                                   state=state, facts=facts, record=outcome.record)
    return _finish_result(request=request, spawn=spawn, state=state, owned_spawn=owned_spawn,
                          outcome=outcome, completion_carrier=completion_carrier, drained=drained,
                          evidence_refs=evidence_refs, tool_package=tool_package)


def _final_message_rounds(*, connection: NativeConnection, request: RunRequest, spawn: _Spawn,
                          workspace: dict, no_tool: bool, facts: _RunFacts, classifier: _Classifier,
                          state: _RunState, tools: ZcodeToolFacts,
                          notify: Callable[..., RunFeedback], outcome: _CarrierOutcome) -> None:
    """The final-message carrier: fresh root sessions on the one process and
    total deadline, one per executed observer correction.

    How often a correction is offered — at most once, never for an enum — is
    the role's rule alone; this loop only executes what a settled feedback
    asks, under the shared deadline and cancel.
    """
    def final_chain(holder: list) -> Callable[[dict, int], None]:
        def observe(message: dict, ordinal: int) -> None:
            # Projection precedes classification and every check: a
            # refused, foreign or child call stays in the evidence
            # whatever this chain decides, and the retained facts reach
            # the role before any driver-side protocol check runs.
            tools.observe(message)
            changed = classifier.observe(message)
            if changed:
                notify()
            if holder:
                holder[0].observe(message, ordinal)
        return observe

    protocol_holder: list = []

    def note_configured(opened_session: str, resolved_spec: dict) -> None:
        outcome.session_id, outcome.resolved = opened_session, resolved_spec
        state.configured = True

    def bind_protocol(opened_session: str) -> None:
        outcome.last_protocol = NoToolProtocol(opened_session, input_id, tools)
        protocol_holder[:] = [outcome.last_protocol]
        connection.observe = final_chain(protocol_holder)

    input_text = request.input_text
    if no_tool:
        create_params = {"titleGenerationEnabled": False, "toolAllowlist": [],
                         "mcpServers": [], "offPeakToolEnabled": False,
                         "dynamicWorkflowEnabled": False}
    else:
        # A read or write scope without a bound service runs the same
        # final-message carrier over the session's own default tools;
        # the effective policy reports that honestly and the external
        # review eligibility is untouched.
        create_params = {"mode": "yolo", "titleGenerationEnabled": False, "mcpServers": []}
    state.create_params = create_params
    while True:
        protocol_holder[:] = []
        connection.observe = final_chain(protocol_holder)
        snapshot = _open_root(connection, workspace, create_params=create_params)
        state.session_opened = True
        input_id = "buddy-no-tool-" + secrets.token_hex(16)
        outcome.session_id, outcome.resolved = _admit_root_input(
            connection, snapshot=snapshot, request=request, spec=configuration_spec(request),
            access=spawn.access, input_id=input_id, input_text=input_text,
            previous=None, mode="initial", require_interactive=False,
            settled=lambda: bool(outcome.last_protocol is not None and outcome.last_protocol.settled),
            classifier=classifier, state=state,
            on_configured=note_configured,
            before_send=lambda opened: bind_protocol(opened))
        protocol = outcome.last_protocol
        state.completion_ordinal = connection.ordinal
        final_message_completed = bool(protocol and protocol.completed)
        # The observed answer, count and turn identity are facts of the
        # settled round: capture them before the close, so a close that
        # is not acknowledged fails the run without erasing them.
        outcome.final_message_completed = final_message_completed
        outcome.raw_answer = protocol.raw_answer
        outcome.event_count = protocol.events
        outcome.turn_id = protocol.turn_id
        _close_root_session(connection, outcome.session_id, tools)
        state.status = "ok"
        feedback = notify(settled=True, raw=outcome.raw_answer)
        if feedback.action == "correct":
            outcome.correction_count += 1
            input_text = feedback.input_text
            classifier.admitted = False
            continue
        break


def _governed_turn(*, connection: NativeConnection, request: RunRequest, services: SessionServices,
                   spawn: _Spawn, workspace: dict, invocation_root: Path, native_root: Path,
                   facts: _RunFacts, classifier: _Classifier, state: _RunState,
                   tools: ZcodeToolFacts, notify: Callable[..., RunFeedback],
                   outcome: _CarrierOutcome) -> None:
    """The governed completion-tool turn: the same lifecycle sites, with a
    mounted session service, a continuation and live observers."""
    mount = services.mount
    _check_service_descriptions(request, mount)
    mode, previous = _continuation_of(request)
    create_params = {"mode": "yolo", "titleGenerationEnabled": False,
                     "mcpServers": list(mount.mcp_servers)}
    state.create_params = create_params
    snapshot = None
    if mode == "native-session":
        outcome.binding_path = native_root / (hashlib.sha256(previous.encode()).hexdigest() + ".json")
        try:
            binding = decode_strict_json(outcome.binding_path.read_bytes())
        except (OSError, ValueError):
            raise NativeError("native-resume-unavailable", "the previous native session has no private goal binding") from None
        if binding != {"taskId": request.identity.task_id, "sessionId": previous,
                       "cwd": request.cwd, "configuration": configuration_spec(request)}:
            raise NativeError("native-resume-unavailable", "the previous native session does not match this goal, checkout and configuration")
        snapshot = _open_root(connection, workspace, create_params=create_params, resume=previous)
    else:
        snapshot = _open_root(connection, workspace, create_params=create_params)
    state.session_opened = True
    # The per-run observers exist before the session opens; the session
    # identity is bound onto them the moment the root is opened.
    outcome.projection = ActivityProjection(None)
    outcome.attempt_usage = ZcodeAttemptUsage(None, resumed=mode == "native-session")
    # Reuse the shared normalizer, monotone ordering and throttle for the
    # whole native turn. Final facts stay in the native projection even when
    # the controller has no live endpoint or refuses a publication.
    publisher = ActivityPublisher(services.live.publish_activity if services.live else None)

    def publish_activity() -> None:
        try:
            publisher.publish(outcome.projection.payload())
        except BoardError:
            return  # optional metadata cannot fail the native turn

    if services.inquiry is not None:
        outcome.inquiry_bridge = make_inquiry_bridge(services.inquiry,
                                                     identity=_bridge_identity(request),
                                                     journal_path=str(services.inquiry.get("resultsPath") or ""),
                                                     attention_path=mount.bridge.get("attentionPath"),
                                                     live=services.live)
        outcome.inquiry_bridge.start()
        state.inquiry = outcome.inquiry_bridge.report()

    def observe(message: dict, ordinal: int) -> None:
        if outcome.evidence is None:
            return
        # Projection precedes everything and keeps every observed
        # fact: child relays, foreign sessions and unverified or
        # forged same-name calls stay task-tool facts. Only the root
        # calls whose results carried verified delivery evidence — a
        # verified receipt or signed refusal envelope, tracked by the
        # root-turn evidence itself — leave the published package at
        # finish time; their evidence is the receipt.
        tools.observe(message)
        outcome.evidence.observe(message, ordinal)
        if outcome.evidence.turn_id is not None:
            # The trusted root identity comes only from this verified
            # canonical turn start, never from the observed events;
            # add_root keeps it unique by itself.
            tools.add_root(outcome.session_id, outcome.evidence.turn_id)
        changed = classifier.observe(message)
        if changed:
            notify()
        # Attempt usage is observed next to the root-turn evidence; a
        # foreign session or an unstarted turn contributes nothing.
        outcome.attempt_usage.observe(message, outcome.evidence.turn_id)
        outcome.projection.note(message, ordinal)
        if outcome.inquiry_bridge is not None:
            outcome.inquiry_bridge.note_event(message, outcome.projection.phase)
        # Native events in a long phase still advance the observation.
        # The publisher coalesces token-level updates within its own window.
        publish_activity()

    def bind_worker_session(opened_session: str) -> None:
        outcome.evidence = RootTurnEvidence(
            opened_session, mount.input_id, mount.finish_tool, mount.bridge,
            checkpoint_name=mount.checkpoint_tool, answer_name=mount.answer_tool,
            on_delivery=(lambda receipt, call_id: outcome.inquiry_bridge.deliver_inquiries(receipt, call_id)),
            on_answer=(lambda receipt, call_id: outcome.inquiry_bridge.record_answer(receipt, call_id)),
            validate_outcome=services.validate_outcome,
            mounted_tools=mount.bare_tools)
        outcome.projection.session_id = opened_session
        outcome.attempt_usage.session_id = opened_session
        connection.observe = observe
        outcome.projection.phase = "waiting-model"
        publish_activity()
        # The pre-model cursor is a bounded, optional read: it can never
        # fail or delay the turn, and without it no message is ever
        # attributed.
        outcome.attempt_usage.capture_baseline(connection)

    def note_configured(opened_session: str, resolved_spec: dict) -> None:
        outcome.session_id, outcome.resolved = opened_session, resolved_spec
        state.configured = True
        if mode != "native-session":
            outcome.binding_path = native_root / (hashlib.sha256(opened_session.encode()).hexdigest() + ".json")
            private_json(outcome.binding_path, {"taskId": request.identity.task_id,
                                                "sessionId": opened_session,
                                                "cwd": request.cwd, "configuration": resolved_spec},
                         exclusive=True)

    def after_admit(opened_session: str) -> None:
        if outcome.inquiry_bridge is not None:
            outcome.inquiry_bridge.activate(opened_session)

    outcome.session_id, outcome.resolved = _admit_root_input(
        connection, snapshot=snapshot, request=request, spec=configuration_spec(request),
        access=spawn.access, input_id=mount.input_id, input_text=request.input_text,
        previous=previous, mode=mode, require_interactive=True,
        settled=lambda: bool(outcome.evidence is not None and outcome.evidence.settled_ordinal),
        classifier=classifier, state=state, on_configured=note_configured,
        before_send=bind_worker_session, after_admit=after_admit)
    # The settlement read is also bounded and optional; the root turn is
    # already settled, so nothing here may change its result.
    outcome.attempt_usage.capture_final(connection)
    if outcome.inquiry_bridge is not None:
        # Stop accepting observations the instant the root turn settled:
        # an idle or finished agent is never woken for an inquiry.
        outcome.inquiry_bridge.close()
    outcome.projection.phase = "finishing"
    publish_activity()
    # The native completion facts are observed once and survive any
    # later failure: a close that is not acknowledged fails the run,
    # but it never rewrites which mechanism delivered the value or
    # erases the verified receipt and outcome the root already gave.
    outcome.verified_outcome = outcome.evidence.receipt["outcome"]
    outcome.verified_completion = {"call_id": outcome.evidence.call_id,
                                   "receipt_id": outcome.evidence.receipt["receiptId"],
                                   "turn_id": outcome.evidence.turn_id,
                                   "result_seq": outcome.evidence.result_seq}
    state.completion_ordinal = outcome.evidence.completed_ordinal
    outcome.turn_id = outcome.evidence.turn_id
    state.status = "ok"
    notify(settled=True)
    # Only the governed turn is closed here; a final-message loop closes
    # every session itself, including a corrected one.
    _close_root_session(connection, outcome.session_id, None)
    outcome.record = {"outcome": outcome.verified_outcome, "provenance": None}
    outcome.evidence.close_ordinal = connection.ordinal
    outcome.record["provenance"] = outcome.evidence.provenance()


def _stop_collection(*, spawn: _Spawn | None, deadline: float, cancel: CancelFlag,
                     state: _RunState, tools: ZcodeToolFacts, classifier: _Classifier,
                     facts: _RunFacts, notify: Callable[..., RunFeedback],
                     completion_carrier: bool, outcome: _CarrierOutcome) -> bool | None:
    """The stop phase: retained observations, bridge close, the conservative
    group stop and the EOF drain, in their established order."""
    if outcome.attempt_usage is not None:
        # ADR-018 items 22/23 and the retained root assistant text: raw native
        # observations for every path, including a quota or process failure. A
        # value the native records never proved stays null.
        state.token_usage = outcome.attempt_usage.raw_usage()
        state.last_assistant_message = outcome.attempt_usage.last_assistant_message
    if outcome.inquiry_bridge is not None:
        # Closing before the process disappears keeps an after-end question
        # honest instead of leaving a dangling observation.
        outcome.inquiry_bridge.close()
        state.inquiry = outcome.inquiry_bridge.report()
        state.attention = outcome.inquiry_bridge.attention_report()
    drained: bool | None = None
    if spawn is not None:
        shutdown, signalled = _stop_native(spawn, deadline)
        state.shutdown = shutdown
        state.exit_code = spawn.process.returncode
        state.signalled = signalled
        state.interrupted = signalled or state.observer_stopped
        if state.status == "ok" and (not shutdown or spawn.process.returncode != 0):
            state.status = "error"
            state.reason = "native-shutdown-failed"
            state.error_text = ("the native app server did not exit normally "
                                "with confirmed group shutdown")
            outcome.record = None
        if tools is not None and shutdown:
            # The native server has exited. Read through its terminal EOF so
            # an event queued after settlement cannot hide behind close/ack
            # — for both carriers: a stream nobody read to its end is not
            # proven complete, whatever mechanism delivered the value. A
            # late frame keeps its projected fact and reaches the role
            # observer; its stop — not the driver's scope reading — is what
            # marks the stream incomplete here.
            if not completion_carrier and outcome.last_protocol is not None:
                drained = _drain(spawn.connection, tools, classifier, outcome.last_protocol, facts,
                                 state, notify)
            elif completion_carrier and outcome.evidence is not None:
                drained = _drain(spawn.connection, tools, classifier, outcome.evidence, facts,
                                 state, notify)
        try:
            spawn.process.stdout.close()
        except OSError:
            pass
    if cancel.is_set():
        state.status = "cancelled"
        state.reason = "cancelled"
        state.error_text = "the owned ZCode execution was cancelled"
        outcome.record = None
    return drained


def _evidence_refs(*, invocation_root: Path, stderr_path: Path, state: _RunState,
                   facts: _RunFacts, record: dict | None) -> list[EvidenceRef]:
    """The run's retained private evidence, in its established order."""
    refs: list[EvidenceRef] = []

    def retain(kind: str, name: str, value: dict) -> None:
        target = invocation_root / name
        private_json(target, value, exclusive=True)
        raw = target.read_bytes()
        refs.append(EvidenceRef(kind=kind, location=str(target), size_bytes=len(raw),
                                sha256=hashlib.sha256(raw).hexdigest()))

    if record is not None and record.get("provenance") is not None:
        # The governed turn record is a role document; the driver contributes
        # only these two native evidence parts (the verified outcome and the
        # ordered provenance), and the role assembles the record it publishes.
        retain("turn-provenance", "native-provenance.json", record["provenance"])
    if state.inquiry is not None:
        retain("inquiry-report", "inquiry-report.json", state.inquiry)
    if state.attention is not None:
        retain("attention-report", "attention-report.json", state.attention)
    if facts.denied:
        # The refused interactions' own full records — timestamps included —
        # stay as their clearly-sourced evidence reference; the common result
        # carries the method/action/reason triple.
        retain("denied-interactions", "denied-interactions.json", {"records": facts.denied})
    if stderr_path.is_file():
        raw_stderr = stderr_path.read_bytes()
        refs.append(EvidenceRef(kind="native-stderr", location=str(stderr_path),
                                size_bytes=len(raw_stderr),
                                sha256=hashlib.sha256(raw_stderr).hexdigest()))
    return refs


def _finish_result(*, request: RunRequest, spawn: _Spawn | None, state: _RunState,
                   owned_spawn: list, outcome: _CarrierOutcome, completion_carrier: bool,
                   drained: bool | None, evidence_refs: list[EvidenceRef],
                   tool_package) -> RunResult:
    """Compose the common result; an unrepresentable package fails the run alone."""
    try:
        return _build_result(request, spawn=spawn, state=state, owned_spawn=owned_spawn,
                             completion_carrier=completion_carrier,
                             verified_outcome=outcome.verified_outcome,
                             verified_completion=outcome.verified_completion,
                             final_message_completed=outcome.final_message_completed,
                             resolved=outcome.resolved,
                             session_id=outcome.session_id, turn_id=outcome.turn_id,
                             raw_answer=outcome.raw_answer,
                             correction_count=outcome.correction_count, event_count=outcome.event_count,
                             record=outcome.record, projection=outcome.projection,
                             binding_path=outcome.binding_path, drained=drained,
                             evidence_refs=evidence_refs, tool_package=tool_package)
    except BoardError:
        # A fact that cannot be represented in the common result is the run's
        # own failure, never an unhandled escape. The fallback keeps every
        # already-observed stage fact — identity, model start, the confirmed
        # configuration, the stop evidence — and drops only the packages whose
        # own shape failed, so nothing observed becomes unknown again.
        fallback = dataclasses.replace(state, status="error", reason="invalid-native-result",
                                       error_text="the native execution returned invalid or incomplete data")
        return _build_result(request, spawn=spawn, state=fallback, owned_spawn=owned_spawn,
                             completion_carrier=completion_carrier,
                             verified_outcome=outcome.verified_outcome,
                             verified_completion=outcome.verified_completion,
                             final_message_completed=outcome.final_message_completed,
                             resolved=outcome.resolved,
                             session_id=outcome.session_id, turn_id=outcome.turn_id,
                             raw_answer=outcome.raw_answer,
                             correction_count=outcome.correction_count, event_count=outcome.event_count,
                             record=None, projection=None, binding_path=outcome.binding_path, drained=drained,
                             evidence_refs=evidence_refs, tool_package=None)


_tool_package = shape_guard(ToolEvidencePackage)
_usage_package = shape_guard(UsagePackage)
_quota_package = shape_guard(NativeFailurePackage)
_native_error_package = shape_guard(NativeErrorRecord)
_message_package = shape_guard(LastAssistantMessagePackage)
_activity_package_fn = shape_guard(ActivityPackage)
_json_package = shape_guard(OptionalFrozenJsonAt(MAX_SCHEMA_BYTES))


def _activity_package(projection) -> Any:
    return _activity_package_fn(projection.payload()) if projection is not None else None


def _build_result(request: RunRequest, *, spawn, state, owned_spawn, resolved, session_id,
                  turn_id, raw_answer, correction_count, event_count, record,
                  projection, binding_path, drained, evidence_refs, tool_package,
                  completion_carrier, verified_outcome, verified_completion,
                  final_message_completed) -> RunResult:
    native_identity = None
    if session_id is not None:
        fields = {"session_id": session_id}
        if turn_id:
            fields["turn_id"] = turn_id
        native_identity = identity_or_none(fields)
    if spawn is None:
        if owned_spawn and isinstance(owned_spawn[0], dict):
            # A child existed and the spawn helper itself stopped it when the
            # connection could not be set up; those are its observed facts.
            created = owned_spawn[0]
            stop_native = StopLayer(group_state="gone" if created["shutdown"] else "unknown")
        else:
            # No process object ever existed: the holding side itself confirms
            # the spawn never happened, which is the one honest "gone" without
            # a process.
            stop_native = StopLayer(group_state="gone")
    else:
        stop_native = StopLayer(group_state="gone" if state.shutdown else "unknown")
    # This harness has no native interrupt acknowledgement: a signal sent or a
    # group observed gone proves the process side only, never an SDK answer, so
    # no acknowledgement is carried at all instead of a constant.
    interrupt = InterruptEvidence(
        requested=True if state.interrupted else None,
        basis="owned-group-signal" if state.signalled else
        ("observer-request" if state.observer_stopped else None))
    checked = CheckedConfiguration()
    if resolved is not None:
        checked = CheckedConfiguration(
            provider=CheckedValue(value=resolved["provider"]),
            model=CheckedValue(value=resolved["model"]),
            effort=CheckedValue(value=resolved["effort"]))
    value = None
    completion = None
    if completion_carrier:
        # The bound carrier decides how the value was taken; a later failure
        # such as an unacknowledged close fails the run but never rewrites
        # the mechanism or erases the verified receipt facts already given.
        if verified_outcome is not None and verified_completion is not None:
            value = RunValue(schema_status="valid",
                             parsed=_json_package(verified_outcome),
                             correction_count=0)
            completion = CompletionEvidence(stream_end=state.shutdown and drained is True)
    elif tool_package is not None:
        # The final-message carrier: the role's schema subset rule is the
        # role's; the driver only reports the raw final message, so the schema
        # status stays unknown rather than naming a check that did not run here.
        value = RunValue(schema_status="unknown", raw=raw_answer,
                         correction_count=correction_count)
        if final_message_completed and state.completion_ordinal is not None:
            completion = CompletionEvidence(stream_end=drained)
    effective = EffectivePolicy()
    if state.session_opened and state.create_params is not None:
        if request.tool_scope == "none":
            effective = EffectivePolicy(
                tools=PolicyFact(requested=_json_package(state.create_params)))
        else:
            effective = EffectivePolicy(
                tools=PolicyFact())
    continuation = None
    if binding_path is not None and session_id is not None:
        continuation = ContinuationFacts(
            resumable=bool(binding_path.is_file() and state.shutdown and record is not None))
    return RunResult(
        identity=request.identity, harness="zcode",
        end=RunEnd(status=state.status, reason_code=state.reason,
                   native_exit_code=state.exit_code,
                   message=state.error_text),
        harness_version=spawn.version if spawn is not None else None,
        model_started=True if state.model_started else None,
        native_event_count=event_count if event_count else None,
        configuration=ResultConfiguration(
            requested=RunConfiguration(provider=request.configuration.provider,
                                       model=request.configuration.model,
                                       effort=request.configuration.effort),
            checked=checked),
        native_identity=native_identity,
        value=value, completion_evidence=completion,
        tool_evidence=_tool_package(tool_package),
        effective_policy=effective,
        activity=_activity_package(projection),
        usage=_usage_package(state.token_usage),
        native_error=_native_error_package(state.native_failure),
        native_failure=_quota_package(state.quota_failure),
        last_assistant_message=_message_package(state.last_assistant_message),
        continuation=continuation,
        stop_evidence=StopEvidence(native=stop_native, interrupt=interrupt),
        evidence_refs=tuple(evidence_refs))


def native_evidence(result: RunResult) -> dict:
    """Project observed native settings and EOF without a role policy guess."""
    policy = result.effective_policy.tools
    requested = policy.requested.value if policy is not None and policy.requested is not None else None
    settings = requested if isinstance(requested, dict) else {}
    completion = result.completion_evidence
    return {"eventCount": result.native_event_count,
            "toolAllowlist": settings.get("toolAllowlist"),
            "titleGenerationEnabled": settings.get("titleGenerationEnabled"),
            "streamEof": completion.stream_end if completion is not None else None}


def run_discovery(*, cwd: str, invocation_root: Path, native_root: Path, timeout_seconds: int,
                  cancelled: Callable[[], bool]) -> dict:
    """One no-prompt native catalog read over the same spawn and handshake.

    Discovery never sends a turn, never mounts a session service and never
    configures a model; it creates one bare session, reads the native
    available-model snapshot and stops the owned process conservatively. The
    return value is the catalog receipt shape consumed by the current callers.
    """
    deadline = execution_deadline(timeout_seconds)
    cancel = CancelFlag(cancelled)
    invocation_root = ensure_private_dir(Path(invocation_root))
    native_root = ensure_private_dir(Path(native_root))
    # Discovery's empty workspace retains the native project-input switches
    # formerly prepared by the outer descriptor. This is native configuration,
    # not a role prompt or a synthetic run request.
    ensure_private_dir(Path(cwd) / ".zcode")
    private_json(Path(cwd) / ".zcode/config.json", {
        "plugins": {"enabled": False},
        "features": {"mcp": False, "memory": False, "skill": False, "subagent": False}})
    stderr_path = invocation_root / _NATIVE_STDERR_FILE
    spawn = _spawn_app_server(cwd=cwd, invocation_root=invocation_root, native_root=native_root,
                              deadline=deadline, cancel=cancel, stderr_path=stderr_path, no_tools=True)
    catalog_value = None
    error: NativeError | None = None
    try:
        spawn.connection.call("runtime/capabilities", {})
        workspace = {"workspacePath": cwd, "workspaceKey": cwd}
        snapshot = spawn.connection.call("session/create", {"workspace": workspace,
                                                            "titleGenerationEnabled": False,
                                                            "toolAllowlist": []})
        session_id = snapshot["session"]["sessionId"]
        version = spawn.version if spawn.version is not None else "unknown"
        catalog_value = catalog(snapshot, spawn.access, version)
        spawn.connection.call("session/close", {"sessionId": session_id})
    except NativeError as caught:
        error = caught
    finally:
        shutdown, _interrupted = _stop_native(spawn, deadline)
        try:
            spawn.process.stdout.close()
        except OSError:
            pass
    if error is not None:
        raise error
    if not shutdown or spawn.process.returncode != 0:
        raise NativeError("native-shutdown-failed",
                          "the native app server did not exit normally with confirmed group shutdown")
    return catalog_value


__all__ = [
    "NoToolProtocol", "SessionServiceMount", "SessionServices", "catalog",
    "configure_session", "execution_deadline", "prepare_session_service", "prepare_services",
    "check_preparation", "session_facts", "validate_turn_provenance", "native_evidence", "run",
    "run_discovery", "selected",
]
