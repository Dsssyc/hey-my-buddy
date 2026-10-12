"""The one native Codex run: a frozen request in, one factual result out.

ADR-025 step 3-A1. This module is the single native execution body of the
Codex harness: evidence-directory preparation, the owned App Server spawn and
handshake, the account/catalog initialization, the per-carrier thread
configuration, turn admission and the event pump, the fast EOF drain and the
conservative group stop each exist exactly once here, driven only by the
frozen :class:`~hey_my_buddy.buddy.harnesses.run_contract.RunRequest`. What
differs between the governed Worker turn (``write`` scope), the fast no-tool
call (``none``) and the read-only review call (``read``) is expressed by the
request and by the role observer's feedback — never by a second native path
and never by re-entering the legacy ``runner`` branches.

The driver owns protocol integrity and fact projection only: thread/turn
identity, the native configuration readbacks, tool-fact projection, unknown
and denied-interaction facts, the Worker usage/quota/checkpoint observations
and the owned-group stop evidence. Whether an unknown event, a tool fact or a
raw answer fails the run stays with the role observer, which receives
cumulative facts and answers one
:class:`~hey_my_buddy.buddy.harnesses.run_contract.RunFeedback`; the driver
executes continue, stop (with one native interrupt attempt) or a carried
correction on the same thread, process and total deadline.

Model discovery (:func:`run_discovery`) is a separate no-prompt metadata
operation over the same spawn and handshake primitives; it never masquerades
as a model run. The role-facing seam operations beside it —
:func:`check_preparation`, :func:`prepare_run_services`,
:func:`validate_turn_provenance`, :func:`session_facts`,
:func:`native_evidence` — carry this harness's
own narrow facts to the shared role executor, which owns every prompt, completion rule and observer.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import queue
import re
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ....errors import BoardError
from ....protocol.activity import ActivityPublisher
from ..c_two_live import CTwoLiveEndpoint
from ....private_dirs import ensure_private_dir
from ...roles.turn_io import canonical_json, guard_private_path, private_json
from ...runtime.windows_process import owned_popen
from ..version import VersionShutdownError, probe_version
from ..base import ProcessHandle
from ..native_support import (
    CancelFlag,
    ObserverInterrupt,
    configuration_spec,
    execution_deadline,
    halt_owned_group as _halt_owned_group,
    identity_or_none,
    shape_guard,
    utc_now,
)
from ..run_contract import (
    ActivityPackage,
    CheckedConfiguration,
    CheckedValue,
    CompletionEvidence,
    ContinuationFacts,
    EffectivePolicy,
    EvidenceRef,
    InterruptEvidence,
    LastAssistantMessagePackage,
    MAX_SCHEMA_BYTES,
    MAX_UNKNOWN_EVENT_TYPES,
    MAX_VALUE_BYTES,
    NativeFailurePackage,
    NativeErrorRecord,
    OptionalFrozenJsonAt,
    PolicyFact,
    ResultConfiguration,
    RunConfiguration,
    RunEnd,
    RunFeedback,
    RunIdentity,
    RunRequest,
    RunResult,
    RunValue,
    StopEvidence,
    StopLayer,
    ToolEvidencePackage,
    UsagePackage,
)
from .config import (
    CodexUnavailable,
    cli_command,
    native_environment,
    native_provider,
    policy_matches,
    read_only_config,
)
from .protocol import (
    CodexProtocolError,
    Connection,
    TurnEvidence,
    attempt_token_usage,
    decode_json,
    quota_candidate_from_response,
    turn_failure_code,
)
from .tool_evidence import (
    NON_TOOL_ITEMS,
    RAW_TOOL_OUTPUTS,
    RAW_TOOL_STARTS,
    TYPED_TOOL_ITEMS,
    CodexToolEventProjector,
)

_NATIVE_STDERR_FILE = "native.stderr.log"
#: The checkpoint's retained root assistant message bound, in serialized bytes.
MAX_CHECKPOINT_MESSAGE_BYTES = 65536
_REFUSAL_MESSAGE = ("This Buddy Worker cannot approve interactive requests; "
                    "report attention in the structured outcome")
_REFUSED_REQUEST_KEYS = ("threadId", "turnId", "itemId", "command", "cwd", "reason")
_INIT_CLIENT = {"clientInfo": {"name": "hey_my_buddy", "title": "Hey My Buddy", "version": "0.9.0"}}
_BOUND_NOTIFICATIONS = 128
#: The one residual bucket when a run sees more distinct unclassified native
#: event types than the public result carries. The parenthesised name cannot
#: pass the label sanitizer below, so no real method can ever collide with it.
_UNLISTED_UNKNOWN_TYPES = "(unlisted-native-events)"


def _mode(request: RunRequest) -> str:
    return {"none": "fast", "read": "review", "write": "worker"}[request.tool_scope]


# -- the cumulative facts the role observer sees ---------------------------------


class _RunFacts:
    """The cumulative, normalized facts of one run, as the observer sees them."""

    def __init__(self):
        self.unknown_counts: dict[str, int] = {}
        self.thread_notifications: dict[str, int] = {}
        self.markers = 0
        self.denied: list[dict] = []
        self.dirty = False

    def note_unknown(self, label: object) -> None:
        self._note_count(self.unknown_counts, label)

    def note_thread_notification(self, label: object) -> None:
        self._note_count(self.thread_notifications, label)

    @staticmethod
    def _note_count(counts: dict[str, int], label: object) -> None:
        name = label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label) else "unknown"
        if name not in counts and len(counts) >= MAX_UNKNOWN_EVENT_TYPES - 1:
            # The public result carries at most MAX_UNKNOWN_EVENT_TYPES distinct
            # types; the reserved residual name holds one of those slots, so
            # beyond MAX-1 real types further distinct types merge into it and
            # the total stays exact, with no occurrence silently dropped. The
            # observer's live mapping reads the same bounded dict.
            name = _UNLISTED_UNKNOWN_TYPES
        counts[name] = counts.get(name, 0) + 1

    def note_marker(self) -> None:
        self.markers += 1

    def mapping(self, *, tool_calls: int, settled: bool, raw_answer: str | None) -> dict:
        return {"settled": settled, "rawAnswer": raw_answer, "toolCalls": tool_calls,
                "toolMarkerFrames": self.markers,
                "unknownEvents": {"countsByType": dict(self.unknown_counts),
                                  "total": sum(self.unknown_counts.values())},
                "threadNotifications": {"countsByType": dict(self.thread_notifications),
                                        "total": sum(self.thread_notifications.values())},
                "deniedInteractions": len(self.denied)}


#: Native notification methods this harness knows in every carrier. A frame
#: outside this set is a retained unknown-event fact for the role observer,
#: never a silent drop and never a driver-side verdict.
_KNOWN_METHODS = frozenset({
    "turn/started", "item/started", "item/updated", "item/completed", "turn/completed",
    "item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta",
    "item/reasoning/summaryPartAdded", "rawResponse/completed", "turn/diff/updated",
    "thread/started", "thread/tokenUsage/updated", "thread/status/changed", "thread/settings/updated",
    "account/rateLimits/updated", "remoteControl/status/changed", "deprecationNotice",
    "warning", "error",
})
_TOOL_METHOD_PREFIXES = ("collabAgent/", "tool/", "mcp/")


class _Classifier:
    """Normalize unknown legal events and tool-shaped frames into retained facts.

    Classification only: nothing here fails the run. Every frame is classified
    — after its tool facts are projected — so a foreign, child or unbound
    frame cannot hide its tool or unknown fact.
    """

    def __init__(self, facts: _RunFacts):
        self.facts = facts

    def observe(self, message: dict) -> bool:
        """Fold one notification in; return whether a new fact appeared."""
        facts = self.facts
        method = message.get("method")
        params = message.get("params")
        if isinstance(method, str) and (method.startswith(_TOOL_METHOD_PREFIXES)
                                        or method.startswith("rawResponseItem/")):
            return self._tool_shaped(method, params)
        if not isinstance(method, str) or method not in _KNOWN_METHODS:
            if isinstance(params, dict) and isinstance(params.get("threadId"), str) and _event_turn(params) is None:
                facts.note_thread_notification(method)
            else:
                facts.note_unknown(method if isinstance(method, str) else "unknown-method")
            return True
        if method in ("item/started", "item/updated", "item/completed"):
            return self._item(params)
        if method == "turn/completed":
            return self._completed_items(params)
        if method == "turn/diff/updated":
            if isinstance(params, dict) and params.get("diff") == "":
                return False
            facts.note_unknown("turn/diff/updated")
            return True
        return False

    def _tool_shaped(self, method: str, params: Any) -> bool:
        kind = self._item_kind(params)
        if method.startswith(_TOOL_METHOD_PREFIXES):
            self.facts.note_marker()
            return True
        if not isinstance(kind, str):
            # A raw frame without an item kind names no operation; the role
            # decides, the fact is retained either way.
            self.facts.note_unknown(method)
            return True
        if kind in NON_TOOL_ITEMS or kind in ("message", "reasoning"):
            return False
        if kind in RAW_TOOL_STARTS or kind in RAW_TOOL_OUTPUTS:
            self.facts.note_marker()
            return True
        self.facts.note_unknown(f"rawResponseItem/{kind}")
        return True

    def _item(self, params: Any) -> bool:
        kind = self._item_kind(params)
        if not isinstance(kind, str):
            self.facts.note_unknown("item-shape-unreadable")
            return True
        if kind in NON_TOOL_ITEMS:
            return False
        if kind in TYPED_TOOL_ITEMS:
            self.facts.note_marker()
            return True
        self.facts.note_unknown(kind)
        return True

    def _completed_items(self, params: Any) -> bool:
        turn = params.get("turn") if isinstance(params, dict) else None
        items = turn.get("items") if isinstance(turn, dict) else None
        if not isinstance(items, list):
            return False
        if any(isinstance(item, dict) and item.get("type") not in NON_TOOL_ITEMS for item in items):
            self.facts.note_marker()
            return True
        return False

    @staticmethod
    def _item_kind(params: Any):
        item = params.get("item") if isinstance(params, dict) else None
        return item.get("type") if isinstance(item, dict) else None


# -- the role-held narrow service binding ----------------------------------------


@dataclasses.dataclass(frozen=True)
class RunServices:
    """The controller-owned live endpoint.

    No blackboard client, ExecutionContext or credential file content travels here.
    Direct fixtures can omit the endpoint while retaining native activity facts.
    """

    live: CTwoLiveEndpoint | None = None


# -- the run state ----------------------------------------------------------------


@dataclasses.dataclass
class _RunState:
    """The run's own end facts, recorded per stage as it actually happened.

    ``thread_opened`` and ``configured`` carry what the run really reached: a
    result records requested policy only when sent, keeps native readbacks
    separate and never reports a completed native outcome without an observed turn.
    """

    status: str = "error"
    reason: str | None = None
    error_text: str | None = None
    model_started: bool = False
    thread_opened: bool = False
    configured: bool = False
    version: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    requested: dict | None = None
    worker_policy: dict | None = None
    shutdown: bool = False
    exit_code: int | None = None
    interrupt_requested: bool = False
    interrupt_basis: str | None = None
    correction_count: int = 0
    event_count: int = 0
    raw_answer: str | None = None
    drained: bool | None = None
    rounds_complete: bool = False
    checkpoint: dict | None = None
    binding_saved: bool = False
    binding_path: Path | None = None
    token_usage: dict | None = None
    usage_rounds: list[TurnEvidence] = dataclasses.field(default_factory=list)
    quota: dict | None = None
    quota_failure: dict | None = None
    native_error: dict | None = None
    capture: dict | None = None
    native_turn_facts: dict | None = None
    #: The review thread/start receipt as the native side actually answered it,
    #: retained before any verdict over it.
    review_receipt: dict | None = None
    review_config: dict | None = None
    #: Whether the on-the-spot native catalog listed the selected model. ADR-027
    #: rule 5: an absent model is handed to the native turn under its own name,
    #: the absence becomes this run's public fact, and the native answer decides.
    selected_model_listed: bool | None = None
    native_provider: str | None = None
    native_model: str | None = None
    catalog_read: dict | None = None
    policy_checks: dict = dataclasses.field(default_factory=dict)
    fast_config: dict | None = None
    fast_receipt: dict | None = None


@dataclasses.dataclass
class _Preparation:
    """Everything the preparation phase fixed before any process existed."""

    command: list
    environment: dict
    native_root: Path
    invocation_root: Path
    stderr_path: Path
    deadline: float
    cwd: str
    version: str | None = None


@dataclasses.dataclass
class _Spawn:
    process: subprocess.Popen
    handle: ProcessHandle
    connection: Connection


class _ActivityWriter:
    """Preserve Codex's own counting and throttle before the shared live publisher."""

    def __init__(self, live: CTwoLiveEndpoint | None):
        # Codex already coalesces on its own phase/count state below. A second
        # throttle could hide a fast correction's final receipt after its
        # per-turn sequence restarted; keep shared validation and ordering only.
        self.publisher = ActivityPublisher(live.publish_activity if live else None,
                                           min_interval_seconds=0)
        self.state: dict = {}
        self.last_payload: dict | None = None

    def write(self, evidence: TurnEvidence, phase: str, tool: str | None = None, *,
              model_turns_base: int = 0, tool_calls: int | None = None) -> None:
        if evidence is None:
            return
        state, tick, now = self.state, time.monotonic(), utc_now()
        if tool:
            state["lastToolActivityAt"] = now
            state["toolName"] = tool[:80]
        if state.get("phase") == phase and tick - state.get("lastWrite", 0) < 2:
            return
        payload = {"phase": phase, "observedAt": now, "eventSeq": evidence.event_seq,
                   "nativeSessionId": evidence.thread_id, "lastNativeActivityAt": now,
                   "counts": {"modelTurns": evidence.model_turns + model_turns_base,
                              "toolCalls": evidence.tool_calls if tool_calls is None else tool_calls}}
        if state.get("lastToolActivityAt"):
            payload.update(lastToolActivityAt=state["lastToolActivityAt"], toolName=state["toolName"])
        # A refused publication never erases the run's own observed activity.
        # Codex retains its native throttle and tool metadata between receipts;
        # ActivityPublisher adds canonical validation and monotone publication.
        state.update(phase=phase, lastWrite=tick)
        self.last_payload = payload
        try:
            self.publisher.publish(payload)
        except BoardError:
            return


# -- phase 1: preparation (private evidence roots and environment) ----------------------


def _prepare(request: RunRequest, mode: str) -> _Preparation:
    """Preparation phase: private evidence roots and native environment."""
    invocation_root = ensure_private_dir(Path(request.private_state.invocation_root))
    native_root = ensure_private_dir(Path(request.private_state.native_root))
    incoming = dict(os.environ)
    try:
        command = cli_command(incoming)
    except CodexUnavailable as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="codex") from None
    environment = native_environment(incoming)
    if mode in ("review", "fast"):
        policy = (tomllib.loads(read_only_config(request.cwd)) if mode == "review"
                  else {"web_search": "disabled", "approval_policy": "never"})
        for key, value in _config_overrides(policy).items():
            command.extend(("-c", key + "=" + value))
    return _Preparation(command=command, environment=environment, native_root=native_root,
                        invocation_root=invocation_root,
                        stderr_path=invocation_root / _NATIVE_STDERR_FILE,
                        deadline=execution_deadline(request.budget.timeout_seconds),
                        cwd=request.cwd)


# -- phase 2: initialization (version probe, owned spawn, handshake) ---------------


def _spawn_app_server(prep: _Preparation, cancel: CancelFlag, owned: list) -> _Spawn:
    """Spawn the one owned App Server, ready for the native handshake.

    The handshake itself stays with the caller, so a failure there flows
    through the run's own stop collection like every other native failure.
    Creation failures consume ``owned_popen_evidence`` before registration.
    Once created, the handle is retained before descriptor cleanup or connection
    setup, and each failure leaves the actual cleanup receipt or unknown handle.
    """
    fd = os.open(prep.stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    process = None
    try:
        try:
            try:
                process = owned_popen([*prep.command, "app-server", "--listen", "stdio://"], cwd=prep.cwd,
                                      env=prep.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=fd, start_new_session=True, close_fds=True)
            except BaseException as error:
                evidence = error.owned_popen_evidence
                owned[:] = [{"created": evidence.created, "shutdown": evidence.shutdown_confirmed,
                             "exit_code": evidence.exit_code}]
                if isinstance(error, OSError):
                    raise CodexProtocolError("adapter-unavailable",
                                             "The selected Codex executable could not start") from None
                raise
            handle = ProcessHandle(process, own_group=True, log_paths={})
            owned[:] = [handle]
        finally:
            os.close(fd)
        connection = Connection(process, prep.deadline, cancel)
    except BaseException:
        if process is not None:
            try:
                shutdown, _signalled = _halt_owned_group(process, handle, time.monotonic() + 5.0)
                owned[:] = [{"created": True, "shutdown": shutdown, "exit_code": process.returncode}]
            finally:
                process.stdout.close()
        raise
    return _Spawn(process=process, handle=handle, connection=connection)


def _catalog(connection: Connection, version: str | None, provider: str) -> tuple[dict, set[str]]:
    data, cursor, seen = [], None, set()
    read_status, read_reason = "complete", None
    while True:
        params = {"limit": 100, "includeHidden": False}
        if cursor:
            params["cursor"] = cursor
        page = connection.call("model/list", params)
        entries = page.get("data")
        if not isinstance(entries, list):
            read_status, read_reason = "unknown", "model-list-shape-unreadable"
            break
        data.extend(entries)
        if len(data) > 200:
            raise CodexProtocolError("invalid-catalog", "Codex model list exceeds the catalog bound")
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor:
            read_status, read_reason = "unknown", "model-cursor-shape-unreadable"
            break
        if cursor in seen:
            raise CodexProtocolError("invalid-catalog", "Codex model cursor did not advance")
        seen.add(cursor)
    models = []
    warnings = [f"The native model reading is incomplete: {read_reason}"] if read_reason else []
    listed_ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        model_id = item.get("model")
        if isinstance(model_id, str) and model_id:
            # The native identity the on-the-spot read listed, kept beside the
            # publishable rows: a listed row with no legal effort still names
            # its model, and execution must see that fact (ADR-027 rule 5).
            listed_ids.add(model_id)
        efforts = [e.get("reasoningEffort") for e in item.get("supportedReasoningEfforts", []) if isinstance(e, dict)]
        efforts = list(dict.fromkeys(e for e in efforts if isinstance(e, str) and e))
        if not isinstance(model_id, str) or not model_id or not efforts:
            warnings.append("A model with no usable identity or reasoning effort was omitted")
            continue
        models.append({"id": model_id, "name": item.get("displayName") or model_id,
                       "description": item.get("description") or "", "efforts": efforts,
                       "inputModalities": item.get("inputModalities") or ["text"], "available": True})
    catalog = {"source": "codex-native-app-server", "adapter": "codex", "harnessVersion": version or "unknown",
               "discoveredAt": datetime.now(timezone.utc).isoformat(),
               "providers": [{"adapter": "codex", "provider": provider, "displayName": provider,
                              "packageName": "codex", "packageVersion": version or "unknown", "models": models}],
               "warnings": list(dict.fromkeys(warnings)),
               "discoveries": [{"adapter": "codex", "status": read_status,
                                **({"reason": read_reason} if read_reason else {})}]}
    return catalog, listed_ids


def _discovery_account_status(account) -> str:
    """The same session's account fact, reported and never judged (ADR-027).

    A chatgpt account whose plan the readback does not name — the field
    missing, empty or ``unknown`` — stays an unknown account; type alone never
    confirms it; the board decides whether to trust any reading.
    """
    if not isinstance(account, dict):
        return "unknown"
    if account.get("type") == "apiKey":
        return "confirmed"
    if account.get("type") != "chatgpt":
        return "unknown"
    plan = account.get("planType")
    if not isinstance(plan, str) or not plan or plan == "unknown":
        return "unknown"
    return "confirmed"


def _initialize(connection: Connection, prep: _Preparation, mode: str, spec: dict,
                state: _RunState) -> bool | None:
    """Initialization phase: handshake, account check, catalog and membership.

    The listed/unlisted fact comes from every
    non-hidden identity this model/list read named, even a row whose efforts
    are empty or invalid: a listed model without the selected effort is
    refused here and never reaches a thread or turn, while a model this read
    did not name never fails here (ADR-027 rule 5) — the selected name is
    handed to the native turn as usual, the caller records the absence as
    this run's public fact, and the real native error decides the failure.
    """
    experimental = {"capabilities": {"experimentalApi": True}} if mode in ("fast", "review") else {}
    connection.call("initialize", {**_INIT_CLIENT, **experimental})
    connection.send({"method": "initialized", "params": {}})
    connection.call("account/read", {"refreshToken": False})
    configured = connection.call("config/read", {"cwd": prep.cwd, "includeLayers": False}).get("config")
    configured = configured if isinstance(configured, dict) else {}
    catalog, listed_ids = _catalog(connection, prep.version, native_provider(configured))
    state.catalog_read = {"discovery": catalog["discoveries"][0], "warnings": catalog["warnings"]}
    if spec.get("model") not in listed_ids:
        return False if catalog["discoveries"][0]["status"] == "complete" else None
    listed = next((model for model in catalog["providers"][0]["models"]
                   if model["id"] == spec.get("model")), None)
    if listed is None or spec.get("effort") not in listed["efforts"]:
        raise CodexProtocolError("invalid-configuration",
                                 "the selected Codex model and effort are not in the current native catalog")
    return True


# -- phase 3: configuration (one thread per carrier) -------------------------------


def _binding_path(root: Path, thread_id: str) -> Path:
    return root / (hashlib.sha256(thread_id.encode()).hexdigest() + ".json")


def _write_binding(root: Path, thread_id: str, binding: dict) -> None:
    path = _binding_path(root, thread_id)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    private_json(temp, binding)
    os.replace(temp, path)


def _read_binding(root: Path, thread_id: str) -> dict:
    try:
        value = decode_json(_binding_path(root, thread_id).read_bytes())
        if not isinstance(value, dict):
            raise ValueError("invalid binding")
        return value
    except (OSError, ValueError):
        raise CodexProtocolError("native-resume-unavailable",
                                 "the Codex thread has no private goal binding") from None


def _continuation_of(request: RunRequest) -> tuple[str, str | None]:
    continuation = request.continuation
    if continuation is None:
        return "initial", None
    return continuation.mode, continuation.previous_session_id


def _check_thread_receipt(response: dict, cwd: str, *, mode: str, previous: str | None) -> str:
    """The one Worker thread-receipt check: identity, checkout."""
    thread = response.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(thread_id, str) or not thread_id \
            or mode == "native-session" and thread_id != previous \
            or mode == "reconstructed-new-session" and thread_id == previous:
        raise CodexProtocolError("wrong-native-thread", "Codex returned an unexpected thread identity")
    if not isinstance(thread.get("cwd"), str) or Path(thread["cwd"]).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("wrong-native-workspace",
                                 "Codex thread checkout differs from the allocated workspace")
    return thread_id


def _configure_worker(connection: Connection, request: RunRequest, prep: _Preparation, state: _RunState) -> str:
    """Worker configuration: continuation checks, then one start or resume."""
    mode, previous = _continuation_of(request)
    requested = configuration_spec(request)
    state.requested = requested
    if mode == "native-session":
        binding = _read_binding(prep.native_root, previous)
        expected = {"taskId": request.identity.task_id, "threadId": previous,
                    "cwd": request.cwd, "configuration": requested}
        if set(binding) != set(expected) | {"lastTurnId", "lastAttemptId", "lastInputSha256"} \
                or {key: binding.get(key) for key in expected} != expected \
                or not isinstance(binding.get("lastTurnId"), str):
            raise CodexProtocolError("native-resume-unavailable",
                                     "the Codex thread binding differs from this goal, checkout or configuration")
        checkpoint = request.continuation.checkpoint
        if checkpoint is not None and any(
                binding.get(key) != getattr(checkpoint, field) for key, field in (
                        ("lastTurnId", "native_turn_id"), ("lastAttemptId", "attempt_id"),
                        ("lastInputSha256", "input_sha256"))):
            raise CodexProtocolError("native-resume-unavailable",
                                     "the Codex binding differs from the previous attempt checkpoint")
        try:
            read = connection.call("thread/read", {"threadId": previous, "includeTurns": True}).get("thread")
            turns = read.get("turns") if isinstance(read, dict) else None
            if not isinstance(turns, list) or not turns or turns[-1].get("id") != binding["lastTurnId"] \
                    or turns[-1].get("status") != "completed":
                raise CodexProtocolError("native-resume-unavailable",
                                         "the Codex thread has changed since its bound completed turn")
            response = connection.call("thread/resume", {"threadId": previous, "cwd": request.cwd,
                                                         "model": requested["model"], "modelProvider": requested["provider"],
                                                         "approvalPolicy": "never", "sandbox": "workspace-write"})
        except CodexProtocolError as error:
            if error.code != "native-rpc-error":
                raise
            raise CodexProtocolError("native-resume-unavailable", str(error)) from None
    elif mode == "initial" and previous is None or mode == "reconstructed-new-session":
        response = connection.call("thread/start", {"cwd": request.cwd, "model": requested["model"],
                                                    "modelProvider": requested["provider"], "approvalPolicy": "never",
                                                    "sandbox": "workspace-write", "serviceName": "hey-my-buddy"})
    else:
        raise CodexProtocolError("invalid-resume-mode",
                                 "Codex requires an explicit initial, native or reconstructed turn")
    thread_id = _check_thread_receipt(response, request.cwd, mode=mode, previous=previous)
    state.native_provider = _bounded((response.get("thread") or {}).get("modelProvider"), 128)
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    return thread_id


def _toml_value(value) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(key, ensure_ascii=False) + " = " + _toml_value(item)
                                for key, item in value.items()) + " }"
    return json.dumps(value, ensure_ascii=False)


def _config_overrides(config: dict) -> dict[str, str]:
    """Filesystem paths stay inside whole permission-profile table values."""
    overrides = {key: _toml_value(value) for key, value in config.items() if key != "permissions"}
    for name, profile in config.get("permissions", {}).items():
        overrides["permissions." + name] = _toml_value(profile)
    return overrides


def _configure_review(connection: Connection, request: RunRequest,
                      state: _RunState) -> str:
    """Review configuration: native readback facts and thread ownership."""
    cwd, spec = request.cwd, configuration_spec(request)
    expected = tomllib.loads(read_only_config(cwd))
    response = connection.call("thread/start", {
        "cwd": cwd, "model": spec["model"], "modelProvider": spec["provider"],
        "approvalPolicy": "never", "permissions": "buddy-router", "serviceName": "hey-my-buddy",
        "experimentalRawEvents": True, "ephemeral": True,
    })
    configured = connection.call("config/read", {"cwd": cwd, "includeLayers": False}).get("config")
    configured = configured if isinstance(configured, dict) else {}
    # Retain permission facts and presence, never user MCP arguments or credentials.
    state.review_config = {key: _readback_fact(configured[key]) for key in
                           ("web_search", "approval_policy", "default_permissions") if key in configured}
    state.review_config["mcp_servers"] = bool(configured.get("mcp_servers"))
    features = configured.get("features")
    permissions = configured.get("permissions")
    profile = permissions.get("buddy-router") if isinstance(permissions, dict) else None
    state.review_config["features"] = {"apps": _readback_fact(features.get("apps")) if isinstance(features, dict) else None}
    state.review_config["permissions"] = {"buddy-router": _readback_fact(profile)}
    grants = profile.get("filesystem") if isinstance(profile, dict) else None
    filesystem = {key: value for key, value in grants.items() if value is not None} if isinstance(grants, dict) else {}
    state.policy_checks["configReadback"] = {
        "status": "confirmed" if (policy_matches(configured, expected)
                                  and filesystem == expected["permissions"]["buddy-router"]["filesystem"]) else "unknown"}
    receipt_profile = response.get("activePermissionProfile")
    sandbox = response.get("sandbox")
    acknowledged = {"activePermissionProfile": receipt_profile, "sandbox": sandbox,
                    "approvalPolicy": response.get("approvalPolicy"), "model": response.get("model"),
                    "modelProvider": response.get("modelProvider"), "cwd": response.get("cwd")}
    # The acknowledged receipt is retained before any verdict over it, so a
    # policy-mismatch run still carries the native readback it actually got —
    # never the requested policy dressed up as the readback.
    state.review_receipt = {key: _readback_fact(value) for key, value in acknowledged.items()}
    state.policy_checks["threadReceipt"] = {
        "status": "confirmed" if (isinstance(receipt_profile, dict) and isinstance(sandbox, dict)
                                  and receipt_profile.get("id") == "buddy-router"
                                  and response.get("approvalPolicy") == "never"
                                  and sandbox.get("type") == "readOnly"
                                  and sandbox.get("networkAccess") is False) else "unknown"}
    state.native_model = _bounded(response.get("model"), 128)
    state.policy_checks["selectedModel"] = {"status": "confirmed" if state.native_model == spec["model"] else "unknown"}
    if not isinstance(sandbox, dict) or sandbox.get("type") != "readOnly":
        raise CodexProtocolError("readonly-policy-unverified",
                                 "Codex did not acknowledge a read-only sandbox")
    if isinstance(response.get("model"), str) and response["model"] != spec["model"]:
        raise CodexProtocolError("readonly-configuration-mismatch",
                                 "Codex acknowledged a different read-only model")
    if not isinstance(response.get("cwd"), str) or Path(response["cwd"]).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("readonly-configuration-mismatch",
                                 "Codex acknowledged a different read-only checkout")
    thread = response.get("thread") or {}
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(thread.get("cwd"), str) \
            or Path(thread["cwd"]).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("wrong-native-workspace", "Read-only native checkout differs")
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    state.requested = spec
    state.native_provider = _bounded(response.get("modelProvider"), 128)
    return thread_id


def _configure_fast(connection: Connection, request: RunRequest, state: _RunState) -> str:
    """Fast configuration: native permission parameters, preserving user settings."""
    cwd, spec = request.cwd, configuration_spec(request)
    response = connection.call("thread/start", {
        "cwd": cwd, "model": spec["model"], "modelProvider": spec["provider"],
        "approvalPolicy": "never", "sandbox": "read-only", "serviceName": "hey-my-buddy",
        "experimentalRawEvents": True, "ephemeral": True,
    })
    configured = connection.call("config/read", {"cwd": cwd, "includeLayers": True}).get("config")
    state.fast_config = {key: _readback_fact(configured[key]) for key in ("web_search", "approval_policy")
                         if isinstance(configured, dict) and key in configured}
    state.policy_checks["configReadback"] = {
        "status": "confirmed" if (isinstance(configured, dict) and configured.get("web_search") == "disabled"
                                  and configured.get("approval_policy") == "never") else "unknown"}
    thread = response.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    state.fast_receipt = {key: _readback_fact(response.get(key)) for key in ("model", "modelProvider", "approvalPolicy")}
    state.native_model = _bounded(response.get("model"), 128)
    state.policy_checks["selectedModel"] = {"status": "confirmed" if state.native_model == spec["model"] else "unknown"}
    state.policy_checks["threadReceipt"] = {"status": "confirmed" if response.get("approvalPolicy") == "never" else "unknown"}
    if isinstance(response.get("model"), str) and response["model"] != spec["model"]:
        raise CodexProtocolError("configuration-mismatch", "Codex acknowledged a different no-tool model")
    if not isinstance(thread_id, str) or not thread_id:
        raise CodexProtocolError("wrong-native-thread", "Codex returned no no-tool thread identity")
    if not isinstance(thread.get("cwd"), str) or Path(thread["cwd"]).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("wrong-native-workspace", "Codex no-tool checkout differs")
    if thread.get("turns") not in (None, []):
        raise CodexProtocolError("wrong-native-thread", "Codex no-tool thread has unrelated history")
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    state.requested = spec
    state.native_provider = _bounded(response.get("modelProvider"), 128)
    return thread_id


# -- phase 4: input and events -----------------------------------------------------


class _EventChain:
    """Facts first, then the carrier's protocol stage.

    Every frame is classified — and its tool facts projected — before any
    carrier check can refuse it, so a foreign, child or unbound frame cannot
    hide. Frames that arrive before the native turn identity exists are
    buffered (bounded) and replayed through the carrier stage only, in order,
    once the turn receipt binds the chain.
    """

    def __init__(self, facts: _RunFacts, classifier: _Classifier,
                 projector: CodexToolEventProjector | None, *, overflow_raises: bool):
        self.facts = facts
        self.classifier = classifier
        self.projector = projector
        self.overflow_raises = overflow_raises
        self.carrier: Callable[[dict], Any] | None = None
        self.pending: list[dict] = []

    def observe_facts(self, message: dict) -> None:
        """Project and classify one frame without the carrier stage."""
        if self.projector is not None:
            self.projector.observe_notification(message)
        if self.classifier.observe(message):
            self.facts.dirty = True

    def observe(self, message: dict) -> None:
        self.observe_facts(message)
        if self.carrier is None:
            if len(self.pending) >= _BOUND_NOTIFICATIONS:
                if self.overflow_raises:
                    raise CodexProtocolError("invalid-native-result",
                                             "Codex no-tool startup event stream exceeded its bound")
                return
            self.pending.append(message)
            return
        self.carrier(message)

    def bind(self, carrier: Callable[[dict], Any]) -> None:
        self.carrier = carrier
        pending, self.pending = self.pending, []
        for message in pending:
            carrier(message)

    def unbind(self) -> None:
        """Buffer again: the next round's frames precede its turn identity."""
        self.carrier = None
        self.pending = []


class _FastProtocol:
    """The no-tool carrier's own protocol checks, without the moved policy.

    This keeps exactly the driver-owned guarantees of the old no-tool
    observer: identity binding of every frame, one turn per round, duplicate
    start/final/completion refusal, and the non-retryable error rule. Tool
    shapes and unknown events are retained facts answered by the role
    observer, never raised here.
    """

    def __init__(self, thread_id: str, turn_id: str, state: dict, evidence: TurnEvidence,
                 run_state: _RunState):
        self.thread_id, self.turn_id, self.state = thread_id, turn_id, state
        self.evidence = evidence
        self.run_state = run_state

    def __call__(self, message: dict) -> None:
        method = message.get("method")
        label = _truncate_utf8(method if isinstance(method, str) else "unknown", 96)
        params = message.get("params")
        opaque = not isinstance(method, str) or method.startswith("rpc/result-unreadable")
        if opaque and (not isinstance(params, dict) or not any(key in params for key in ("threadId", "turnId", "turn"))):
            return
        if method in ("account/rateLimits/updated", "remoteControl/status/changed", "deprecationNotice"):
            return
        if method == "thread/started" and isinstance(params, dict):
            thread = params.get("thread") or {}
            if thread.get("id") == self.thread_id:
                return
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            raise CodexProtocolError("invalid-native-result", f"Unbound native event in no-tool stream ({label})")
        event_turn = _event_turn(params)
        if event_turn is not None and event_turn != self.turn_id:
            raise CodexProtocolError("invalid-native-result", f"Foreign native turn in no-tool stream ({label})")
        _capture_native_error(self.run_state, message, self.thread_id, self.turn_id)
        if method == "error":
            if params.get("willRetry") is True:
                return
            raise CodexProtocolError("native-turn-failed", f"Native no-tool turn reported an error ({label})")
        if event_turn is None:
            return
        if method == "thread/tokenUsage/updated":
            self.evidence.observe(message)
            return
        if method in ("thread/status/changed", "thread/settings/updated", "warning"):
            return
        turn = params.get("turn")
        if method in ("turn/started", "turn/completed"):
            self.evidence.observe(message)
        if method == "turn/started":
            if self.state["started"]:
                raise CodexProtocolError("invalid-native-result", f"Duplicate native turn start ({label})")
            self.state["started"] = True
        elif method in ("item/started", "item/updated", "item/completed"):
            item = params.get("item") if isinstance(params, dict) else None
            if method == "item/completed" and isinstance(item, dict) \
                    and item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                if self.state["final"] is not None:
                    raise CodexProtocolError("invalid-native-result", f"Multiple final answers ({label})")
                self.state["final"] = item
        elif method == "turn/completed":
            if self.state["completed"] is not None:
                raise CodexProtocolError("invalid-native-result", f"Duplicate native turn completion ({label})")
            self.state["completed"] = turn


def _retain_event(capture: dict, key: str, message: dict, *, per_event: int, total: int, cap: int) -> None:
    """The review's bounded, opt-in native event retention (legacy bounds)."""
    events = capture[key]
    encoded = canonical_json(message)
    if len(events) < cap and len(encoded.encode()) <= per_event \
            and sum(len(canonical_json(item).encode()) for item in events) + len(encoded.encode()) <= total:
        events.append(message)
    else:
        capture["truncated"] = True


def _capture_frame(capture: dict, message: dict) -> None:
    method = message.get("method")
    params = message.get("params") or {}
    item = params.get("item") if isinstance(params, dict) else None
    raw_type = item.get("type") if isinstance(item, dict) else None
    if method == "rawResponseItem/completed" and raw_type in (
            "function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output",
            "local_shell_call", "web_search_call"):
        _retain_event(capture, "rawToolEvents", message, per_event=16384, total=262144, cap=128)
    elif method in ("item/started", "item/completed") and raw_type not in ("agentMessage", "reasoning", "userMessage"):
        _retain_event(capture, "toolEvents", message, per_event=8192, total=131072, cap=64)


def _admit_turn(connection: Connection, request: RunRequest, thread_id: str,
                input_text: str, mode: str, state: _RunState) -> dict:
    """The one turn admission site: send this round's input and schema."""
    params: dict = {"threadId": thread_id, "cwd": request.cwd, "model": request.configuration.model,
                    "effort": request.configuration.effort, "approvalPolicy": "never",
                    "input": [{"type": "text", "text": input_text}],
                    "outputSchema": request.output_schema.value}
    if mode == "worker":
        params["sandboxPolicy"] = {"type": "workspaceWrite", "writableRoots": [request.cwd],
                                   "networkAccess": True}
    elif mode == "review":
        params["permissions"] = "buddy-router"
    response = connection.call("turn/start", params)
    if mode == "worker":
        state.worker_policy = {"sandboxPolicy": params["sandboxPolicy"],
                               "approvalPolicy": params["approvalPolicy"]}
    return response


def _observe_quota(connection: Connection, evidence: TurnEvidence) -> dict | None:
    """Best-effort native quota snapshot; a failed read never fails a turn."""
    from ....protocol.usage import normalize_quota
    candidate = evidence.quota_candidate
    previous_deadline = connection.deadline
    connection.deadline = min(previous_deadline, time.monotonic() + 2)
    try:
        response = connection.call("account/rateLimits/read", {})
    except CodexProtocolError:
        return candidate
    finally:
        connection.deadline = previous_deadline
    fresh = quota_candidate_from_response(response, observed_at=utc_now())
    return fresh if fresh is not None and normalize_quota(fresh) is not None else candidate


def _checkpoint(request: RunRequest, evidence: TurnEvidence) -> dict:
    """The Worker's native checkpoint, keyed on the frozen request identity."""
    checkpoint = {"version": 1, "taskId": request.identity.task_id,
                  "attemptId": request.identity.attempt_id, "generation": request.identity.generation,
                  "turnId": request.identity.turn_id, "inputSha256": request.identity.input_sha256,
                  "sessionId": evidence.thread_id, "nativeTurnId": evidence.turn_id,
                  "nativeTurnStarted": evidence.started,
                  "nativeTurnStatus": (evidence.completed or {}).get("status", "incomplete"),
                  "eventSeq": evidence.event_seq, "bindingSaved": False}
    item = evidence.final_item or getattr(evidence, "last_agent_item", None)
    if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
        raw = item["text"].encode()
        text = raw[:MAX_CHECKPOINT_MESSAGE_BYTES].decode("utf-8", errors="ignore")
        while len(canonical_json(text).encode()) > MAX_CHECKPOINT_MESSAGE_BYTES:
            text = text[:len(text) // 2]
        checkpoint["lastAssistantMessage"] = {
            "itemId": item["id"], "text": text, "phase": item.get("phase"), "sourceBytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "truncated": text != item["text"]}
    return checkpoint


# -- the one run -------------------------------------------------------------------


def run(request: RunRequest, *, observer: Callable[[Mapping], RunFeedback],
        services: Any, cancelled: Callable[[], bool]) -> RunResult:
    """Run one native Codex execution: the single native path of this harness."""
    if request.harness != "codex":
        raise BoardError("INVALID_ARGUMENT", "this run module drives codex", harness=request.harness)
    if request.tool_scope == "full" or (request.worker_permission is not None
                                        and request.worker_permission.level == "full"):
        raise BoardError("INVALID_ARGUMENT", "codex full access is not implemented", toolScope=request.tool_scope)
    if services is not None and not isinstance(services, RunServices):
        raise BoardError("INVALID_ARGUMENT", "codex accepts its own narrow run service binding")
    binding = services if services is not None else RunServices()
    mode = _mode(request)
    return _run(request, binding, mode, observer, cancelled)


def _run(request: RunRequest, services: RunServices, mode: str,
         observer: Callable[[Mapping], RunFeedback], cancelled: Callable[[], bool]) -> RunResult:
    cancel = CancelFlag(cancelled)
    facts = _RunFacts()
    state = _RunState()
    if mode == "review" and request.capture_evidence:
        state.capture = {"toolEvents": [], "rawToolEvents": [], "turns": [],
                         "deniedRequests": [], "truncated": False}
    prep: _Preparation | None = None
    spawn: _Spawn | None = None
    owned_spawn: list = [{"created": False, "shutdown": True, "exit_code": None}]
    connection: Connection | None = None
    # Every carrier projects its real native tool facts on the one collector;
    # the governed turn's own frames are evidence like the structured calls',
    # without adopting any read-only policy here.
    projector = CodexToolEventProjector({"adapter": "codex", "taskId": request.identity.task_id,
                                         "attemptId": request.identity.attempt_id,
                                         "generation": request.identity.generation})
    activity = _ActivityWriter(services.live)
    chain = _EventChain(facts, _Classifier(facts), projector, overflow_raises=mode == "fast")
    try:
        prep = _prepare(request, mode)
        prep.version = probe_version(prep.command, prep.environment, cwd=request.cwd,
                                     deadline=prep.deadline, owned=owned_spawn)
        state.version = prep.version
        spawn = _spawn_app_server(prep, cancel, owned_spawn)
        connection = spawn.connection
        _wire(connection, chain, facts, observer, request, state, mode)
        state.selected_model_listed = _initialize(connection, prep, mode, configuration_spec(request), state)
        if mode == "worker":
            thread_id = _configure_worker(connection, request, prep, state)
            _worker_turn(connection, chain, request, state, thread_id, observer, activity)
        else:
            if mode == "fast":
                thread_id = _configure_fast(connection, request, state)
            else:
                thread_id = _configure_review(connection, request, state)
            _structured_rounds(connection, chain, request, state, thread_id,
                               mode, observer, activity)
    except ObserverInterrupt:
        state.status, state.reason = "cancelled", "observer-interrupt"
        state.error_text = "the role observer stopped the run"
        state.interrupt_requested = True
        state.interrupt_basis = state.interrupt_basis or "observer-request"
        if connection is not None and state.thread_id and state.turn_id:
            _interrupt(connection, state)
    except (CodexProtocolError, VersionShutdownError) as error:
        state.status = "cancelled" if error.code == "user-cancel" else "error"
        state.reason, state.error_text = error.code, str(error)
        if connection is not None and state.thread_id and state.turn_id \
                and error.code in ("user-cancel", "deadline"):
            _interrupt(connection, state)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        state.status, state.reason = "error", "invalid-native-result"
        state.error_text = "Codex returned invalid or incomplete native data"
    finally:
        # A preparation failure also reaches the stop collection: nothing was
        # ever spawned, and that known fact — not an unknown — is what the
        # result must carry.
        _stop_collection(request, prep, state, spawn, owned_spawn, mode, cancel)
    return _build_result(request, prep, state, mode, projector, facts, activity)


def _wire(connection: Connection, chain: _EventChain, facts: _RunFacts,
          observer: Callable[[Mapping], RunFeedback], request: RunRequest,
          state: _RunState, mode: str) -> None:
    """Bind the connection's frame paths to the fact-first chain."""
    connection.on_notification = chain.observe

    def on_request(message: dict) -> None:
        method = message.get("method")
        params = message.get("params")
        record = {"method": method if isinstance(method, str) and len(method) <= 80 else "unknown",
                  "threadId": params.get("threadId") if isinstance(params, dict) else None,
                  "turnId": params.get("turnId") if isinstance(params, dict) else None}
        if request.capture_evidence and mode == "review" and state.capture is not None:
            record.update(requestId=message.get("id"),
                          params={key: params[key] for key in _REFUSED_REQUEST_KEYS
                                  if isinstance(params, dict) and key in params},
                          response={"code": -32601, "denied": True})
            if len(state.capture["deniedRequests"]) < 32:
                state.capture["deniedRequests"].append(record)
        facts.denied.append(record)
        connection.send({"id": message["id"], "error": {"code": -32601, "message": _REFUSAL_MESSAGE}})
        facts.dirty = True

    connection.on_request = on_request

    def dispatch_feedback() -> None:
        # Runs after every pump step, the waits inside ``call`` included: the
        # refusal answer is already on the wire when the role's stop lands.
        if not facts.dirty:
            return
        facts.dirty = False
        tool_calls = chain.projector.tool_calls if chain.projector is not None else 0
        feedback = observer(facts.mapping(tool_calls=tool_calls, settled=False, raw_answer=None))
        _take_feedback(feedback, mode, settled=False)

    connection.after_pump = dispatch_feedback


def _take_feedback(feedback: Any, mode: str, *, settled: bool) -> RunFeedback:
    if not isinstance(feedback, RunFeedback):
        raise BoardError("INVALID_ARGUMENT", "the observer must answer with one RunFeedback")
    if feedback.action == "correct":
        if not settled:
            raise BoardError("INVALID_ARGUMENT", "a correction is only answerable at a settled run")
        if mode == "worker":
            raise BoardError("INVALID_ARGUMENT", "the governed codex turn takes no format correction")
        return feedback
    if feedback.action == "stop":
        raise ObserverInterrupt()
    return feedback


def _worker_turn(connection: Connection, chain: _EventChain, request: RunRequest,
                 state: _RunState, thread_id: str,
                 observer: Callable[[Mapping], RunFeedback], activity: _ActivityWriter) -> None:
    """The governed turn: one admission, settlement, usage, quota, checkpoint."""
    state.model_started = True
    response = _admit_turn(connection, request, thread_id, request.input_text, "worker", state)
    turn = response.get("turn")
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    if not isinstance(turn_id, str) or not turn_id:
        raise CodexProtocolError("wrong-native-turn", "Codex did not acknowledge a native turn")
    state.turn_id = turn_id
    if chain.projector is not None:
        # The trusted root identity comes only from this native turn receipt.
        chain.projector.observe_root(thread_id, turn_id)
    evidence = TurnEvidence(thread_id, turn_id)

    def carrier(message: dict) -> None:
        _capture_native_error(state, message, thread_id, turn_id)
        observed = evidence.observe(message)
        if observed:
            activity.write(evidence, observed[0], observed[1])

    chain.bind(carrier)
    activity.write(evidence, "waiting-model")
    try:
        while evidence.completed is None:
            connection.pump()
        native_turn = evidence.completed
        if evidence.final_item is None and isinstance(native_turn.get("items"), list):
            finals = [item for item in native_turn["items"] if isinstance(item, dict)
                      and item.get("type") == "agentMessage" and item.get("phase") == "final_answer"]
            if len(finals) == 1:
                evidence.final_item = finals[0]
    finally:
        # Even a transport failure or a deadline can leave a completed root
        # assistant message; the retained checkpoint permits reconstruction,
        # never resume without a completed native turn.
        state.checkpoint = _checkpoint(request, evidence)
        state.event_count = evidence.event_seq
        state.token_usage = attempt_token_usage(evidence)
    state.quota = _observe_quota(connection, evidence)
    failure_code = turn_failure_code(native_turn)
    if failure_code is not None:
        from ....protocol.usage import classify_quota_code
        classification = classify_quota_code(failure_code)
        if classification != "unknown":
            state.quota_failure = {"code": classification, "nativeCode": failure_code, "source": "codex/app-server-turn-error",
                                   "observedAt": utc_now()}
    if native_turn.get("status") != "completed" or not evidence.started:
        raise CodexProtocolError("native-turn-failed", "Codex turn did not complete successfully")
    item = evidence.final_item
    # The native half of the governed turn's provenance, exactly as the legacy
    # record carried it; the role's completion policy adds its own verdict keys
    # on top and decides over these facts.
    state.native_turn_facts = {
        "adapter": "codex", "nativeThreadId": thread_id, "nativeTurnId": turn_id,
        "finalItemId": item.get("id") if isinstance(item, dict) else None,
        "finalMessageCompleted": isinstance(item, dict), "nativeTurnStarted": evidence.started,
        "nativeTurnCompleted": True, "eventSeq": evidence.event_seq}
    correlated = next((record for record in chain.facts.denied
                       if record.get("threadId") == thread_id and record.get("turnId") == turn_id), None)
    if correlated is not None:
        state.native_turn_facts.update(nativeRequestMethod=correlated["method"],
                                       nativeRequestThreadId=correlated["threadId"],
                                       nativeRequestTurnId=correlated["turnId"])
    state.raw_answer = item.get("text") if isinstance(item, dict) and isinstance(item.get("text"), str) else None
    state.status = "ok"
    _settle_feedback(observer, chain,
                     chain.projector.tool_calls if chain.projector is not None
                     else evidence.tool_calls, state.raw_answer, "worker")


def _structured_rounds(connection: Connection, chain: _EventChain, request: RunRequest,
                       state: _RunState, thread_id: str, mode: str,
                       observer: Callable[[Mapping], RunFeedback], activity: _ActivityWriter) -> None:
    """Fast/review: rounds on one thread; corrections are the role's decision.

    How often a correction is offered — at most once, never for an enum
    violation — is the role's rule alone; this loop only executes what a
    settled feedback asks, under the shared deadline and cancel.
    """
    input_text = request.input_text
    round_index = 0
    while True:
        fast_state = {"started": False, "completed": None, "final": None}
        chain.unbind()
        state.model_started = True
        response = _admit_turn(connection, request, thread_id, input_text, mode, state)
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if mode == "fast":
            if not isinstance(turn_id, str) or not turn_id:
                raise CodexProtocolError("invalid-native-result", "Codex returned no native turn identity")
        elif not isinstance(turn_id, str):
            raise CodexProtocolError("wrong-native-turn", "No read-only turn identity")
        state.turn_id = turn_id
        if chain.projector is not None:
            # The trusted root identity comes only from this native turn receipt.
            chain.projector.observe_root(thread_id, turn_id)
        if mode == "fast":
            items = turn.get("items")
            if items is not None and not isinstance(items, list):
                chain.observe_facts({"method": "turn/start-items-unreadable"})
            elif items:
                for item in items:
                    chain.observe_facts({"method": "item/started", "params": {
                        "threadId": thread_id, "turnId": turn_id, "item": item}})
        evidence = TurnEvidence(thread_id, turn_id)
        state.usage_rounds.append(evidence)
        if mode == "fast":
            chain.bind(_FastProtocol(thread_id, turn_id, fast_state, evidence, state))
        else:

            def carrier(message: dict, _evidence=evidence, _turn_id=turn_id, _round=round_index) -> None:
                params = message.get("params")
                if not isinstance(params, dict) or params.get("threadId") != thread_id \
                        or params.get("turnId") not in (None, _turn_id):
                    return
                if state.capture is not None:
                    _capture_frame(state.capture, message)
                _capture_native_error(state, message, thread_id, _turn_id)
                observed = _evidence.observe(message)
                if observed:
                    activity.write(_evidence, observed[0], observed[1], model_turns_base=_round,
                                   tool_calls=chain.projector.tool_calls)
            chain.bind(carrier)
        try:
            if mode == "fast":
                while fast_state["completed"] is None:
                    connection.pump()
            else:
                while evidence.completed is None:
                    connection.pump()
            raw = _fast_final_checks(fast_state) if mode == "fast" else _review_final_checks(evidence)
        finally:
            state.event_count += evidence.event_seq
        state.raw_answer = raw
        state.rounds_complete = True
        if mode == "review" and state.capture is not None:
            state.capture["turns"].append({"sessionId": thread_id, "turnId": turn_id,
                                           "started": True, "completed": True})
        feedback = _settle_feedback(observer, chain,
                                    chain.projector.tool_calls if chain.projector is not None
                                    else evidence.tool_calls, raw, mode)
        if feedback.action == "correct":
            state.correction_count += 1
            input_text = feedback.input_text
            state.rounds_complete = False
            round_index += 1
            continue
        break
    if mode == "fast":
        state.drained = _drain_to_eof(connection, chain, observer, mode)
    state.status = "ok"


def _fast_final_checks(fast_state: dict) -> str:
    final = fast_state["final"]
    if (fast_state["completed"] or {}).get("status") in ("failed", "interrupted"):
        raise CodexProtocolError("native-turn-failed", "Native no-tool turn did not complete (turn/completed)")
    if (not fast_state["started"] or (fast_state["completed"] or {}).get("status") != "completed"
            or not isinstance(final, dict) or not isinstance(final.get("text"), str)
            or not isinstance(final.get("id"), str)):
        raise CodexProtocolError("invalid-native-result", "No complete native no-tool answer")
    return final["text"]


def _review_final_checks(evidence: TurnEvidence) -> str:
    item = evidence.final_item
    if not evidence.started or (evidence.completed or {}).get("status") != "completed" \
            or not isinstance(item, dict):
        raise CodexProtocolError("native-turn-failed", "No completed native structured answer")
    return item.get("text")


def _settle_feedback(observer: Callable[[Mapping], RunFeedback], chain: _EventChain,
                     tool_calls: int, raw: str | None, mode: str) -> RunFeedback:
    feedback = observer(chain.facts.mapping(tool_calls=tool_calls, settled=True, raw_answer=raw))
    return _take_feedback(feedback, mode, settled=True)


def _drain_to_eof(connection: Connection, chain: _EventChain,
                  observer: Callable[[Mapping], RunFeedback], mode: str) -> bool:
    """Consume every queued native frame through EOF after closing the input.

    The order of every frame is the run's own: fact projection, then the role's
    answer — its stop, not the driver's scope reading, is what marks the stream
    incomplete — while the carrier's settlement checks keep their codes.
    """
    connection.process.stdin.close()
    while True:
        remaining = connection._remaining()
        try:
            message = connection.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            continue
        if message is None:
            return True
        if isinstance(message, CodexProtocolError):
            raise message
        if "id" in message and "method" in message:
            # A late request keeps its projected facts and its refusal answer;
            # the carrier stage does not run for it, as before.
            chain.observe_facts(message)
            connection.on_request(message)
        elif "id" in message:
            raise CodexProtocolError("invalid-native-result", "Unexpected late native response")
        else:
            chain.observe(message)
        if chain.facts.dirty:
            chain.facts.dirty = False
            tool_calls = chain.projector.tool_calls if chain.projector is not None else 0
            feedback = observer(chain.facts.mapping(tool_calls=tool_calls, settled=False, raw_answer=None))
            _take_feedback(feedback, mode, settled=False)


def _interrupt(connection: Connection, state: _RunState) -> None:
    """A fresh short control budget permits a native interrupt after the deadline."""
    try:
        connection.on_notification = lambda message: None
        connection.cancelled = CancelFlag(lambda: False)
        connection.deadline = time.monotonic() + 2
        connection.call("turn/interrupt", {"threadId": state.thread_id, "turnId": state.turn_id})
        state.interrupt_requested = True
        state.interrupt_basis = ("native-turn-interrupt-ack" if connection.last_result_readable is True
                                 else "native-turn-interrupt-unconfirmed")
    except (CodexProtocolError, OSError, ValueError):
        # A closed input (the fast drain already ended the stream) or a failed
        # control write is an unconfirmed interrupt, never a crash and never a
        # claim that the native turn was interrupted.
        state.interrupt_requested = True
        state.interrupt_basis = "native-turn-interrupt-unconfirmed"


# -- phase 5: stop collection -------------------------------------------------------


def _stop_collection(request: RunRequest, prep: _Preparation,
                     state: _RunState, spawn: _Spawn | None, owned_spawn: list,
                     mode: str, cancel: CancelFlag) -> None:
    """Stop phase: the owned group's conservative confirmation, then cleanup."""
    if spawn is None:
        receipt = owned_spawn[0]
        # The initial no-child fact, public probe result and creation receipt
        # occupy the same boundary. A retained handle without a receipt is unknown.
        state.shutdown = isinstance(receipt, dict) and receipt["shutdown"] is True
        state.exit_code = receipt["exit_code"] if isinstance(receipt, dict) else receipt.process.returncode
        return
    shutdown, _signalled = _halt_owned_group(spawn.process, spawn.handle, prep.deadline)
    state.shutdown = shutdown
    state.exit_code = spawn.process.returncode
    try:
        spawn.process.stdout.close()
    except OSError:
        pass
    if mode == "worker":
        _save_binding(request, prep, state)
    if state.status == "ok" and (not shutdown or spawn.process.returncode != 0):
        state.status, state.reason = "error", "native-shutdown-failed"
        state.error_text = "Codex App Server did not exit with confirmed process-group shutdown"
    if cancel.is_set() and state.status != "ok":
        state.status, state.reason = "cancelled", "user-cancel"
        state.error_text = "the Codex execution was cancelled"


def _save_binding(request: RunRequest, prep: _Preparation,
                  state: _RunState) -> None:
    checkpoint = state.checkpoint
    if not checkpoint or checkpoint.get("nativeTurnStarted") is not True \
            or checkpoint.get("nativeTurnStatus") != "completed" \
            or not state.shutdown or state.exit_code != 0:
        return
    binding = {"taskId": checkpoint["taskId"], "threadId": state.thread_id, "cwd": request.cwd,
               "configuration": state.requested, "lastTurnId": state.turn_id,
               "lastAttemptId": checkpoint["attemptId"], "lastInputSha256": checkpoint["inputSha256"]}
    _write_binding(prep.native_root, state.thread_id, binding)
    checkpoint["bindingSaved"] = True
    state.binding_saved = True
    state.binding_path = _binding_path(prep.native_root, state.thread_id)


# -- the factual result --------------------------------------------------------------


_tool_evidence_package = shape_guard(ToolEvidencePackage)
_json_package = shape_guard(OptionalFrozenJsonAt(MAX_SCHEMA_BYTES))
_usage_package = shape_guard(UsagePackage)
_quota_package = shape_guard(NativeFailurePackage)
_native_error_package = shape_guard(NativeErrorRecord)
_message_package = shape_guard(LastAssistantMessagePackage)
_activity_package = shape_guard(ActivityPackage)


def _readback_fact(value):
    """Bound one whitelisted native field before writing a projected receipt.

    The six retained review fields fit the evidence reader's 512 KiB budget;
    an oversized field keeps its omission fact, never its arbitrary contents.
    """
    size = len(canonical_json(value).encode())
    if size <= 64 * 1024:
        return value
    return {"status": "unknown", "reason": "readback-exceeds-evidence-bound", "sizeBytes": size}


def _bounded(value: str | None, limit: int) -> str | None:
    return value if isinstance(value, str) and len(value.encode()) <= limit else None


def _truncate_utf8(value: str, limit: int) -> str:
    return value.encode()[:limit].decode("utf-8", errors="ignore")


def _event_turn(params: dict):
    turn = params.get("turn")
    if isinstance(turn, dict) and turn.get("id") is not None:
        return turn["id"]
    return params.get("turnId")


def _capture_native_error(state: _RunState, message: dict, thread_id: str, turn_id: str) -> None:
    """Retain only bound native error fields, separate from quota attribution."""
    params = message.get("params")
    if not isinstance(params, dict) or params.get("threadId") != thread_id:
        return
    event_turn = _event_turn(params)
    if event_turn is not None and event_turn != turn_id:
        return
    method = message.get("method")
    if method == "turn/completed" and event_turn == turn_id:
        error = (params.get("turn") or {}).get("error")
    elif method == "error" and params.get("willRetry") is not True:
        error = params.get("error")
    else:
        return
    if not isinstance(error, dict):
        return
    retained = {}
    for key in ("message", "additionalDetails"):
        value = error.get(key)
        if isinstance(value, str):
            retained[key] = _truncate_utf8(value, 4096)
            if retained[key] != value:
                retained[key + "Truncated"] = True
    info = error.get("codexErrorInfo")
    if isinstance(info, (str, dict)):
        if len(canonical_json(info).encode()) <= 2048:
            retained["codexErrorInfo"] = info
        else:
            retained["codexErrorInfoTruncated"] = True
    if retained:
        state.native_error = {"source": "codex/app-server-turn-error", "method": method,
                              "nativeThreadId": thread_id, "error": retained}
        if event_turn == turn_id:
            state.native_error["nativeTurnId"] = turn_id


def _checked_configuration(request: RunRequest, state: _RunState, mode: str) -> ResultConfiguration:
    checked = CheckedConfiguration()
    if state.configured:
        catalog_model = CheckedValue(value=request.configuration.model)
        catalog_effort = CheckedValue(value=request.configuration.effort)
        if mode == "worker":
            checked = CheckedConfiguration(
                provider=CheckedValue(value=state.native_provider) if isinstance(state.native_provider, str) else None,
                model=catalog_model if state.selected_model_listed is True else None,
                effort=catalog_effort if state.selected_model_listed is True else None)
        else:
            checked = CheckedConfiguration(
                provider=CheckedValue(value=state.native_provider) if isinstance(state.native_provider, str) else None,
                model=CheckedValue(value=state.native_model) if state.native_model else None,
                effort=catalog_effort if state.selected_model_listed is True
                and state.native_model == request.configuration.model else None)
    return ResultConfiguration(
        requested=RunConfiguration(provider=request.configuration.provider,
                                   model=request.configuration.model,
                                   effort=request.configuration.effort),
        checked=checked)


def _effective_policy(state: _RunState, mode: str) -> EffectivePolicy:
    if not state.configured:
        return EffectivePolicy()
    if mode == "worker":
        return EffectivePolicy(
            tools=PolicyFact(requested=_json_package(state.worker_policy)) if state.worker_policy else None)
    if mode == "fast":
        return EffectivePolicy(
            tools=PolicyFact(requested=_json_package({"sandbox": "read-only", "approvalPolicy": "never"})))
    return EffectivePolicy(
        tools=PolicyFact(requested=_json_package({"permissions": "buddy-router", "approvalPolicy": "never"})))


def _last_assistant_message(checkpoint: dict | None) -> dict | None:
    message = checkpoint.get("lastAssistantMessage") if isinstance(checkpoint, dict) else None
    if not isinstance(message, dict):
        return None
    from ....protocol import usage
    return usage.normalize_last_assistant_message(
        {"text": message.get("text"), "itemId": message.get("itemId"), "phase": message.get("phase"),
         "sourceBytes": message.get("sourceBytes"), "sha256": message.get("sha256"),
         "truncated": message.get("truncated")},
        source="codex/app-server-root-assistant-message")


def _continuation_facts(state: _RunState, mode: str) -> ContinuationFacts | None:
    if mode != "worker" or state.thread_id is None:
        return None
    return ContinuationFacts(resumable=True if state.binding_saved else None)


def _evidence_refs(request: RunRequest, state: _RunState, facts: _RunFacts) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    invocation_root = Path(request.private_state.invocation_root)

    def retain(kind: str, path: Path, value: dict | None) -> None:
        if value is not None:
            private_json(path, value, exclusive=True)
        if not path.is_file():
            return
        raw = path.read_bytes()
        refs.append(EvidenceRef(kind=kind, location=str(path), size_bytes=len(raw),
                                sha256=hashlib.sha256(raw).hexdigest()))

    if facts.unknown_counts:
        retain("unknown-events", invocation_root / "unknown-events.json",
               {"countsByType": facts.unknown_counts, "total": sum(facts.unknown_counts.values())})
    if state.catalog_read is not None:
        retain("catalog-read", invocation_root / "catalog-read.json", state.catalog_read)
    if state.policy_checks:
        retain("policy-check", invocation_root / "policy-check.json", state.policy_checks)
    if state.fast_config is not None:
        retain("fast-config-readback", invocation_root / "fast-config-readback.json", {"config": state.fast_config})
    if state.fast_receipt is not None:
        retain("fast-thread-receipt", invocation_root / "fast-thread-receipt.json", state.fast_receipt)
    if facts.denied:
        retain("denied-interactions", invocation_root / "denied-interactions.json",
               {"records": facts.denied})
    if state.quota is not None:
        # The native quota snapshot has no field in the common result yet; the
        # retained file keeps the real observation for the role wiring.
        retain("quota-snapshot", invocation_root / "quota-snapshot.json", state.quota)
    if state.capture is not None:
        retain("review-captured-events", invocation_root / "review-captured-events.json", state.capture)
    if state.selected_model_listed is False:
        # The on-the-spot absence of the selected model, with the selected
        # identity kept beside it: this run's own public fact (ADR-027 rule 5),
        # never a refusal verdict and never a substitute for the native answer.
        retain("model-check", invocation_root / "model-check.json",
               {"adapter": "codex", "selectedModelListed": False,
                "selected": {"provider": request.configuration.provider,
                             "model": request.configuration.model,
                             "effort": request.configuration.effort}})
    if state.native_turn_facts is not None:
        retain("native-turn-facts", invocation_root / "native-turn-facts.json", state.native_turn_facts)
    if state.review_config is not None:
        retain("review-config-readback", invocation_root / "review-config-readback.json",
               {"config": state.review_config})
    if state.review_receipt is not None:
        retain("review-thread-receipt", invocation_root / "review-thread-receipt.json",
               state.review_receipt)
    if state.checkpoint is not None:
        retain("native-checkpoint", invocation_root / "native-checkpoint.json", state.checkpoint)
    if facts.thread_notifications:
        retain("thread-notifications", invocation_root / "thread-notifications.json",
               {"countsByType": facts.thread_notifications, "total": sum(facts.thread_notifications.values())})
    return tuple(refs)


def _build_result(request: RunRequest, prep: _Preparation | None, state: _RunState, mode: str,
                  projector: CodexToolEventProjector | None, facts: _RunFacts,
                  activity: _ActivityWriter) -> RunResult:
    if state.usage_rounds:
        usages = [attempt_token_usage(evidence) for evidence in state.usage_rounds]
        known = [usage for usage in usages if usage is not None]
        if known:
            state.token_usage = dict(known[0])
            for key in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "nativeRecords"):
                state.token_usage[key] = sum(usage[key] for usage in known)
            if len(known) != len(usages) or any(usage["completeness"] != "complete" for usage in known):
                state.token_usage["completeness"] = "partial"
    native_identity = None
    if state.thread_id is not None:
        fields = {"session_id": state.thread_id}
        if state.turn_id:
            fields["turn_id"] = state.turn_id
        native_identity = identity_or_none(fields)
    tool_package = None
    if projector is not None and (state.thread_opened or mode == "review"):
        # Stream facts record the observed ends — the completed root turns
        # (worker: the governed turn's own native receipt; review: every
        # observed round) and the real EOF (fast) — never the run's business
        # status and never the group's stop, which a conservative halt must
        # not overwrite. The review carrier keeps its incomplete package even
        # before any turn: the binding and the empty stream are the receipt's
        # own facts, exactly as the legacy review summary carried them.
        if mode == "fast":
            stream_complete = state.drained is True
        elif mode == "review":
            stream_complete = state.rounds_complete
        else:
            checkpoint = state.checkpoint or {}
            stream_complete = (checkpoint.get("nativeTurnStarted") is True
                               and checkpoint.get("nativeTurnStatus") == "completed")
        tool_package = projector.finish(stream_complete)
    value = None
    if _bounded(state.raw_answer, MAX_VALUE_BYTES) is not None:
        value = RunValue(schema_status="unknown",
                         raw=state.raw_answer, correction_count=state.correction_count)
    completion = None
    if state.turn_id is not None:
        completion = CompletionEvidence(stream_end=state.drained if mode == "fast" else None)
    return RunResult(
        identity=request.identity, harness="codex",
        end=RunEnd(status=state.status, reason_code=state.reason,
                   native_exit_code=state.exit_code,
                   message=_bounded(state.error_text, 512)),
        harness_version=state.version,
        native_event_count=state.event_count or None,
        model_started=True if state.model_started else None,
        configuration=_checked_configuration(request, state, mode),
        native_identity=native_identity,
        value=value, completion_evidence=completion,
        tool_evidence=_tool_evidence_package(tool_package),
        effective_policy=_effective_policy(state, mode),
        activity=_activity_package(activity.last_payload),
        usage=_usage_package(state.token_usage),
        native_failure=_quota_package(state.quota_failure),
        native_error=_native_error_package(state.native_error),
        last_assistant_message=_message_package(_last_assistant_message(state.checkpoint)),
        continuation=_continuation_facts(state, mode),
        stop_evidence=StopEvidence(
            native=StopLayer(
                group_state="gone" if state.shutdown else "unknown"),
            interrupt=InterruptEvidence(requested=True if state.interrupt_requested else None,
                                        basis=state.interrupt_basis)),
        evidence_refs=_evidence_refs(request, state, facts))


# -- model discovery -----------------------------------------------------------------


def run_discovery(*, cwd: str, invocation_root: Path, native_root: Path,
                  timeout_seconds: int, cancelled: Callable[[], bool]) -> dict:
    """One no-prompt native catalog read over the same spawn and handshake.

    Discovery never sends a turn, never prepares a private home and never
    configures a model; it performs the account check and the native model
    list read in the same session and stops the owned process conservatively.
    The account fact is reported, never judged (ADR-027 rule 1): a session
    whose ``account/read`` answer is missing, failed or unrecognized still
    reports the models it read, marked ``unknown``, and the board decides
    whether to trust the reading. The return value is the catalog receipt
    shape the current callers consume.
    """
    owned: list = [{"created": False, "shutdown": True, "exit_code": None}]
    shutdown = True
    try:
        deadline = execution_deadline(timeout_seconds)
        cancel = CancelFlag(cancelled)
        invocation_root = ensure_private_dir(Path(invocation_root))
        native_root = ensure_private_dir(Path(native_root))
        incoming = dict(os.environ)
        try:
            command = cli_command(incoming)
        except CodexUnavailable as error:
            raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="codex") from None
        environment = native_environment(incoming)
        try:
            version = probe_version(command, environment, cwd=cwd, deadline=deadline, owned=owned)
            prep = _Preparation(command=command, environment=environment, native_root=native_root,
                                invocation_root=invocation_root,
                                stderr_path=invocation_root / _NATIVE_STDERR_FILE,
                                deadline=deadline, cwd=cwd, version=version)
            spawn = _spawn_app_server(prep, cancel, owned)
        except Exception:
            shutdown = isinstance(owned[0], dict) and owned[0]["shutdown"] is True
            raise
        shutdown = False
        try:
            connection = spawn.connection
            facts = _RunFacts()
            classifier = _Classifier(facts)
            connection.on_notification = classifier.observe
            connection.call("initialize", _INIT_CLIENT)
            connection.send({"method": "initialized", "params": {}})
            try:
                account = connection.call("account/read", {"refreshToken": False}).get("account")
            except CodexProtocolError:
                account = None
            account_status = _discovery_account_status(account)
            configured = connection.call("config/read", {"cwd": cwd, "includeLayers": False}).get("config")
            configured = configured if isinstance(configured, dict) else {}
            catalog_value, _listed_ids = _catalog(connection, version, native_provider(configured))
            from ....protocol.billing import codex_account, fact
            provider = native_provider(configured)
            observed_at = utc_now()
            catalog_value["billingByProvider"] = {provider: (
                codex_account(account, observed_at) if provider == "openai"
                else fact("unknown", "codex/account-read", observed_at))}
            catalog_value["discoveries"][0]["accountStatus"] = account_status
            if facts.unknown_counts:
                catalog_value["unknownEvents"] = {"countsByType": facts.unknown_counts,
                                                   "total": sum(facts.unknown_counts.values())}
                catalog_value["warnings"].extend(
                    f"Unrecognized native reading event: {label} ({count})"
                    for label, count in facts.unknown_counts.items())
            if account_status == "unknown":
                catalog_value["warnings"] = [*catalog_value["warnings"],
                                             "The discovery session could not confirm the Codex account; "
                                             "whether to trust this reading is the board's judgment"]
        finally:
            try:
                shutdown, _signalled = _halt_owned_group(spawn.process, spawn.handle, deadline)
            finally:
                spawn.process.stdout.close()
        if not shutdown or spawn.process.returncode != 0:
            raise CodexProtocolError("native-shutdown-failed",
                                     "the native app server did not exit normally with confirmed group shutdown")
        return catalog_value
    except Exception as error:
        error.discovery_shutdown_confirmed = shutdown
        raise


# -- the registered seam's role-facing operations ----------------------------------


def check_preparation(spec: dict, environment: dict) -> None:
    """Confirm the native command without selecting a provider or account.

    This is the harness's own share of Worker and fast preparation. Every
    governed-turn, resume-mode and private-state rule belongs to the shared
    role executor and is not repeated here.
    """
    try:
        cli_command(environment)
    except CodexUnavailable as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="codex") from None


def prepare_run_services(*, tool_scope: str = "write") -> RunServices:
    """Bind the harness's native services without selecting credentials."""
    return RunServices()


def validate_turn_provenance(record: dict) -> str | None:
    """The governed turn record's native-source checks, and nothing else.

    Only the native provenance is validated here: whether the record's claimed
    native completion, identity correlation and resume identity hold. The
    business outcome decision stays with the role's completion policy.
    """
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


def session_facts(native_root: Path, session_id: str | None) -> dict:
    """The native thread's storage and visibility facts; the role decides reuse."""
    captured = isinstance(session_id, str) and bool(session_id)
    return {
        "adapter": "codex",
        "sessionId": session_id if captured else None,
        "captured": captured,
        "storageScope": "harness-user-store",
        "storageOwner": "harness-user-store",
        "nativeAppVisibility": "listed-in-native-app",
        "note": ("Codex stores ordinary threads in the user's native directory. Native continuation "
                 "rechecks task, checkout, configuration and the last completed turn binding; "
                 "application visibility still needs installed-run verification."),
    }


def native_evidence(result: RunResult) -> dict:
    """Project this run's real native evidence; no missing fact is invented.

    The fast posture comes from the public policy fact; the review
    acknowledgment and configuration readback come from this run's integrity-checked
    evidence references, retained as the native side actually answered — a
    policy whose restrictions are unverified keeps its real readback and is never
    dressed up as enforced. The interrupt facts keep the legacy true keys and
    appear only when a real request (and a real acknowledgement) happened; an
    acknowledgement is never inferred from a confirmed stop. A model the
    on-the-spot catalog did not list keeps its ``selectedModelListed`` fact
    and the selected identity beside it. Keys this run never produced are
    absent, never filled with a placeholder.
    """
    projection: dict = {}
    for kind, key in (("unknown-events", "unknownEvents"), ("catalog-read", "catalogRead"),
                      ("policy-check", "policyCheck"), ("fast-config-readback", "nativeConfigPolicy"),
                      ("fast-thread-receipt", "nativeThreadReceipt")):
        value = _read_evidence_ref(result, kind)
        if value is not None:
            projection[key] = value["config"] if kind == "fast-config-readback" else value
    if result.native_error is not None:
        projection["nativeFailure"] = result.native_error.value
    notifications = _read_evidence_ref(result, "thread-notifications")
    if notifications is not None:
        projection["threadNotifications"] = notifications
    receipt = _read_evidence_ref(result, "review-thread-receipt")
    if isinstance(receipt, dict):
        projection["nativePolicy"] = receipt
    else:
        policy = result.effective_policy.tools
        requested = policy.requested.value if policy is not None and policy.requested is not None else None
        if isinstance(requested, dict):
            projection["nativePolicy"] = requested
    config = _read_evidence_ref(result, "review-config-readback")
    if config is not None and isinstance(config.get("config"), dict):
        projection["nativeConfigPolicy"] = config["config"]
    capture = _read_evidence_ref(result, "review-captured-events")
    if capture is not None:
        projection["nativeToolEvents"] = capture.get("toolEvents")
        projection["nativeRawToolEvents"] = capture.get("rawToolEvents")
        projection["nativeTurns"] = capture.get("turns")
        projection["nativeDeniedRequests"] = capture.get("deniedRequests")
        projection["nativeEvidenceTruncated"] = capture.get("truncated") is True
    interrupt = result.stop_evidence.interrupt
    if interrupt is not None and interrupt.requested is True:
        projection["nativeInterruptRequested"] = True
        if interrupt.basis == "native-turn-interrupt-ack":
            projection["nativeInterruptAcknowledged"] = True
    check = _read_evidence_ref(result, "model-check")
    if isinstance(check, dict) and check.get("selectedModelListed") is False:
        projection["selectedModelListed"] = False
        if isinstance(check.get("selected"), dict):
            projection["selectedModel"] = check["selected"]
    return projection


def _read_evidence_ref(result: RunResult, kind: str) -> dict | None:
    """One verified evidence reference of this run, or None when absent."""
    from ..controller import STRICT_RESULT_BYTES
    for ref in result.evidence_refs:
        if ref.kind != kind:
            continue
        with guard_private_path(Path(ref.location)).open("rb") as stream:
            raw = stream.read(STRICT_RESULT_BYTES + 1)
        if (len(raw) > STRICT_RESULT_BYTES or ref.size_bytes != len(raw)
                or ref.sha256 != hashlib.sha256(raw).hexdigest()):
            raise BoardError("INVALID_ARGUMENT", "The native evidence reference no longer matches its content")
        value = decode_json(raw)
        return value if isinstance(value, dict) else None
    return None


#: The request controls this driver really consumes. The resume checkpoint's
#: three binding facts are compared against the private goal binding before any
#: thread/read; declaring it only confirms the capability exists (ADR-023),
#: the registry's check never proves more.
supported_request_controls = ("resume_checkpoint",)

__all__ = [
    "RunServices", "check_preparation",
    "execution_deadline", "native_evidence", "prepare_run_services", "run",
    "run_discovery", "session_facts", "validate_turn_provenance",
]
