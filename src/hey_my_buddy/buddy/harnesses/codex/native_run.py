"""The one native Codex run: a frozen request in, one factual result out.

ADR-025 step 3-A1. This module is the single native execution body of the
Codex harness: private-home preparation, the owned App Server spawn and
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
:func:`cleanup_after_run` and :func:`native_evidence` — carry this harness's
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
from ....private_dirs import account_root, ensure_private_dir
from ...roles.turn_io import canonical_json, guard_private_path, private_json
from ...runtime.windows_process import owned_popen
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
    policy_matches,
    prepare_no_tool_home,
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
        self.markers = 0
        self.denied: list[dict] = []
        self.dirty = False

    def note_unknown(self, label: object) -> None:
        name = label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label) else "unknown"
        if name not in self.unknown_counts and len(self.unknown_counts) >= MAX_UNKNOWN_EVENT_TYPES - 1:
            # The public result carries at most MAX_UNKNOWN_EVENT_TYPES distinct
            # types; the reserved residual name holds one of those slots, so
            # beyond MAX-1 real types further distinct types merge into it and
            # the total stays exact, with no occurrence silently dropped. The
            # observer's live mapping reads the same bounded dict.
            name = _UNLISTED_UNKNOWN_TYPES
        self.unknown_counts[name] = self.unknown_counts.get(name, 0) + 1

    def note_marker(self) -> None:
        self.markers += 1

    def mapping(self, *, tool_calls: int, settled: bool, raw_answer: str | None) -> dict:
        return {"settled": settled, "rawAnswer": raw_answer, "toolCalls": tool_calls,
                "toolMarkerFrames": self.markers,
                "unknownEvents": {"countsByType": dict(self.unknown_counts),
                                  "total": sum(self.unknown_counts.values())},
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
_CONVERSATION_ITEMS = ("agentMessage", "reasoning", "userMessage")


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
        if method not in _KNOWN_METHODS:
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
        if not isinstance(kind, str) or kind in NON_TOOL_ITEMS:
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
    """The native credential source, frozen account and controller-owned live endpoint.

    No blackboard client, ExecutionContext or credential file content travels here.
    Direct fixtures can omit the endpoint while retaining native activity facts.
    """

    credential_source: dict | None = None
    account: dict | None = None
    live: CTwoLiveEndpoint | None = None


# -- the run state ----------------------------------------------------------------


@dataclasses.dataclass
class _RunState:
    """The run's own end facts, recorded per stage as it actually happened.

    ``thread_opened`` and ``configured`` carry what the run really reached: a
    result never claims an effective policy before the readbacks confirmed it
    and never reports a completed native outcome without an observed turn.
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
    quota: dict | None = None
    quota_failure: dict | None = None
    capture: dict | None = None
    native_turn_facts: dict | None = None
    #: The review thread/start receipt as the native side actually answered it,
    #: retained before any verdict over it.
    review_receipt: dict | None = None
    #: The Worker coding home this run really prepared, and the review
    #: config/read result observed before any policy verdict: each is a real
    #: preparation fact, never back-derived from a process id or a mode label.
    coding_home_prepared: bool = False
    coding_home: Path | None = None
    review_config: dict | None = None
    #: Whether the on-the-spot native catalog listed the selected model. ADR-027
    #: rule 5: an absent model is handed to the native turn under its own name,
    #: the absence becomes this run's public fact, and the native answer decides.
    selected_model_listed: bool | None = None


@dataclasses.dataclass
class _Preparation:
    """Everything the preparation phase fixed before any process existed."""

    command: list
    environment: dict
    native_root: Path
    invocation_root: Path
    stderr_path: Path
    deadline: float
    incoming: dict
    cwd: str
    home: Path | None = None
    version: str | None = None
    coding_home_prepared: bool = False


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


# -- phase 1: preparation (private roots, home, environment) ----------------------


def _coding_home(native_root: Path, credential_source: dict) -> Path:
    """The Worker's task-private Codex home; its auth cleanup stays outer."""
    from .home import prepare_coding_home
    try:
        return prepare_coding_home(native_root, credential_source)
    except BoardError as error:
        raise CodexProtocolError(error.code.lower().replace("_", "-"), error.message) from None


def _review_home(native_root: Path, environment: dict, cwd: str) -> Path:
    """An independent native server: linked existing auth, read-only config."""
    old_home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
    private_home = native_root / "codex-home"
    private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth = old_home / "auth.json"
    if auth.is_file() and not (private_home / "auth.json").exists():
        (private_home / "auth.json").symlink_to(auth)
    (private_home / "config.toml").write_text(read_only_config(cwd))
    return private_home


def _prepare(request: RunRequest, services: RunServices, mode: str) -> _Preparation:
    """Preparation phase: private roots, the carrier's native home, environment."""
    if mode == "fast" and (type(request.budget.timeout_seconds) is not int
                           or not 0 < request.budget.timeout_seconds <= 60):
        raise CodexProtocolError("no-tool-policy-unverified",
                                 "Codex no-tool deadline must be at most 60 seconds")
    invocation_root = ensure_private_dir(Path(request.private_state.invocation_root))
    native_root = ensure_private_dir(Path(request.private_state.native_root))
    incoming = dict(os.environ)
    try:
        command = cli_command(incoming)
    except CodexUnavailable as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="codex") from None
    environment = native_environment(incoming)
    home = None
    prepared = False
    if mode == "fast":
        home = prepare_no_tool_home(native_root, environment, configuration_spec(request))
        environment["CODEX_HOME"] = str(home)
    elif mode == "review":
        home = _review_home(native_root, environment, request.cwd)
        environment["CODEX_HOME"] = str(home)
    elif mode == "worker":
        home = _coding_home(native_root, services.credential_source)
        environment["CODEX_HOME"] = str(home)
        environment["CODEX_SQLITE_HOME"] = str(home)
        # Command overrides keep repository config from redirecting native state.
        command = [*command, "-c", "sqlite_home=" + json.dumps(str(home)),
                   "-c", 'cli_auth_credentials_store="file"']
        prepared = True
    return _Preparation(command=command, environment=environment, native_root=native_root,
                        invocation_root=invocation_root,
                        stderr_path=invocation_root / _NATIVE_STDERR_FILE,
                        deadline=execution_deadline(request.budget.timeout_seconds),
                        incoming=incoming, cwd=request.cwd, home=home,
                        coding_home_prepared=prepared)


# -- phase 2: initialization (version probe, owned spawn, handshake) ---------------


def _probe_version(command: list, environment: dict, cwd: str, deadline: float, mode: str) -> str | None:
    """Version is diagnostic only; a failed probe never refuses the run."""
    try:
        timeout = max(0.1, min(5, deadline - time.monotonic())) if mode == "fast" else 5
        result = subprocess.run([*command, "--version"], cwd=cwd, env=environment,
                                capture_output=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.decode(errors="replace").strip()[:80]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _spawn_app_server(prep: _Preparation, cancel: CancelFlag, owned: list) -> _Spawn:
    """Spawn the one owned App Server, ready for the native handshake.

    The handshake itself stays with the caller, so a failure there flows
    through the run's own stop collection like every other native failure.
    The moment ``owned_popen`` succeeds this helper records the handle on
    ``owned`` and, if the connection cannot be built, stops what it started
    itself and leaves the observed stop facts — no child is ever left alive
    by a lost handle.
    """
    fd = os.open(prep.stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        process = owned_popen([*prep.command, "app-server", "--listen", "stdio://"], cwd=prep.cwd,
                              env=prep.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=fd, start_new_session=True, close_fds=True)
    except OSError:
        raise CodexProtocolError("adapter-unavailable",
                                 "The selected Codex executable could not start") from None
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    owned[:] = [handle]
    try:
        connection = Connection(process, prep.deadline, cancel)
    except BaseException:
        shutdown, _signalled = _halt_owned_group(process, handle, time.monotonic() + 5.0)
        owned[:] = [{"created": True, "shutdown": shutdown, "exit_code": process.returncode}]
        raise
    return _Spawn(process=process, handle=handle, connection=connection)


def _catalog(connection: Connection, version: str | None) -> dict:
    data, cursor, seen = [], None, set()
    while True:
        params = {"limit": 100, "includeHidden": False}
        if cursor:
            params["cursor"] = cursor
        page = connection.call("model/list", params)
        entries = page.get("data")
        if not isinstance(entries, list):
            raise CodexProtocolError("invalid-catalog", "Codex returned no model list")
        data.extend(entries)
        if len(data) > 200:
            raise CodexProtocolError("invalid-catalog", "Codex model list exceeds the catalog bound")
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise CodexProtocolError("invalid-catalog", "Codex model cursor is invalid")
        seen.add(cursor)
    models, warnings = [], []
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
               "providers": [{"adapter": "codex", "provider": "openai", "displayName": "OpenAI ChatGPT plan",
                              "packageName": "codex", "packageVersion": version or "unknown", "models": models}],
               "warnings": list(dict.fromkeys(warnings))}
    return catalog, listed_ids


def _account_binding(prep: _Preparation, services: RunServices) -> dict:
    """The frozen account binding: coding source, structured account, or the
    locally selected Worker account from the incoming environment."""
    binding = services.credential_source or services.account or {}
    if binding:
        return binding
    incoming = prep.incoming
    if not incoming.get("BUDDY_ACCOUNT_SELECTION"):
        return {}
    try:
        selected = decode_json(incoming["BUDDY_ACCOUNT_SELECTION"])
        if (selected.get("adapter") == "codex" and selected.get("source") == "worker"
                and incoming.get("CODEX_HOME") == str(account_root(Path(incoming["BUDDY_STATE_DIR"]), "codex"))):
            return selected
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        pass
    return {}


def _discovery_account_status(account, independent: bool) -> str:
    """The same session's account fact, reported and never judged (ADR-027).

    A chatgpt account whose plan the readback does not name — the field
    missing, empty or ``unknown`` — stays an unknown account; type alone never
    confirms it. An independent worker's apiKey binding keeps its existing
    rule; the board decides whether to trust any reading.
    """
    if not isinstance(account, dict) or account.get("type") not in (
            ("chatgpt", "apiKey") if independent else ("chatgpt",)):
        return "unknown"
    if account.get("type") == "chatgpt":
        plan = account.get("planType")
        if not isinstance(plan, str) or not plan or plan == "unknown":
            return "unknown"
    return "confirmed"


def _initialize(connection: Connection, prep: _Preparation, services: RunServices,
                mode: str, spec: dict) -> bool:
    """Initialization phase: handshake, account check, catalog and membership.

    The provider gate stays exact. The listed/unlisted fact comes from every
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
    account = connection.call("account/read", {"refreshToken": False}).get("account")
    independent = _account_binding(prep, services).get("source") == "worker"
    if (not isinstance(account, dict) or account.get("type") not in
            (("chatgpt", "apiKey") if independent else ("chatgpt",))):
        raise CodexProtocolError("account-plan-required",
                                 "Codex requires an existing ChatGPT account-plan login")
    catalog, listed_ids = _catalog(connection, prep.version)
    if spec.get("provider") != "openai":
        raise CodexProtocolError("invalid-configuration",
                                 "the selected Codex model and effort are not in the current native catalog")
    if spec.get("model") not in listed_ids:
        return False
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
    """The one Worker thread-receipt check: identity, checkout and provider."""
    thread = response.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(thread_id, str) or not thread_id \
            or mode == "native-session" and thread_id != previous \
            or mode == "reconstructed-new-session" and thread_id == previous:
        raise CodexProtocolError("wrong-native-thread", "Codex returned an unexpected thread identity")
    if not isinstance(thread.get("cwd"), str) or Path(thread["cwd"]).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("wrong-native-workspace",
                                 "Codex thread checkout differs from the allocated workspace")
    if thread.get("modelProvider") not in (None, "openai"):
        raise CodexProtocolError("wrong-native-provider", "Codex thread selected a different provider")
    return thread_id


def _configure_worker(connection: Connection, request: RunRequest, services: RunServices,
                      prep: _Preparation, state: _RunState) -> str:
    """Worker configuration: continuation checks, then one start or resume."""
    mode, previous = _continuation_of(request)
    requested = configuration_spec(request)
    state.requested = requested
    if mode == "native-session":
        if not isinstance(previous, str) or not previous:
            raise CodexProtocolError("native-resume-unavailable",
                                     "native continuation requires the bound Codex thread")
        binding = _read_binding(prep.native_root, previous)
        expected = {"taskId": request.identity.task_id, "threadId": previous,
                    "cwd": request.cwd, "configuration": requested,
                    "codexHome": prep.environment["CODEX_HOME"],
                    "credentialSource": services.credential_source}
        if {key: binding.get(key) for key in expected} != expected \
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
        read = connection.call("thread/read", {"threadId": previous, "includeTurns": True}).get("thread")
        turns = read.get("turns") if isinstance(read, dict) else None
        if not isinstance(turns, list) or not turns or turns[-1].get("id") != binding["lastTurnId"] \
                or turns[-1].get("status") != "completed":
            raise CodexProtocolError("native-resume-unavailable",
                                     "the Codex thread has changed since its bound completed turn")
        response = connection.call("thread/resume", {"threadId": previous, "cwd": request.cwd,
                                                     "model": requested["model"], "modelProvider": "openai",
                                                     "approvalPolicy": "never", "sandbox": "workspace-write"})
    elif mode == "initial" and previous is None or mode == "reconstructed-new-session":
        response = connection.call("thread/start", {"cwd": request.cwd, "model": requested["model"],
                                                    "modelProvider": "openai", "approvalPolicy": "never",
                                                    "sandbox": "workspace-write", "serviceName": "hey-my-buddy"})
    else:
        raise CodexProtocolError("invalid-resume-mode",
                                 "Codex requires an explicit initial, native or reconstructed turn")
    thread_id = _check_thread_receipt(response, request.cwd, mode=mode, previous=previous)
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    return thread_id


def _configure_review(connection: Connection, request: RunRequest,
                      state: _RunState) -> tuple[str, dict]:
    """Review configuration: exact policy readback, then the buddy-router thread."""
    cwd, spec = request.cwd, configuration_spec(request)
    expected = tomllib.loads(read_only_config(cwd))
    configured = connection.call("config/read", {"cwd": cwd, "includeLayers": False}).get("config") or {}
    if isinstance(configured, dict):
        # The observed readback is retained before any verdict, so a mismatch
        # run still carries the configuration it actually read.
        state.review_config = configured
    # The retained policy evidence is the acknowledged native state, never the
    # run's own proposal.
    profile = (configured.get("permissions") or {}).get("buddy-router") or {}
    filesystem = {key: value for key, value in (profile.get("filesystem") or {}).items() if value is not None}
    if (not policy_matches(configured, expected) or configured.get("mcp_servers")
            or filesystem != expected["permissions"]["buddy-router"]["filesystem"] or profile.get("extends")):
        raise CodexProtocolError("readonly-policy-unverified",
                                 "Codex effective configuration differs from the private read-only policy")
    response = connection.call("thread/start", {
        "cwd": cwd, "model": spec["model"], "modelProvider": "openai",
        "approvalPolicy": "never", "permissions": "buddy-router", "serviceName": "hey-my-buddy",
        "experimentalRawEvents": True,
        "config": {"web_search": "disabled", "features.apps": False, "features.multi_agent": False},
    })
    receipt_profile = response.get("activePermissionProfile") or {}
    sandbox = response.get("sandbox") or {}
    acknowledged = {"activePermissionProfile": receipt_profile, "sandbox": sandbox,
                    "approvalPolicy": response.get("approvalPolicy"), "model": response.get("model"),
                    "modelProvider": response.get("modelProvider"), "cwd": response.get("cwd")}
    # The acknowledged receipt is retained before any verdict over it, so a
    # policy-mismatch run still carries the native readback it actually got —
    # never the requested policy dressed up as the readback.
    state.review_receipt = acknowledged
    if (receipt_profile.get("id") != "buddy-router" or response.get("approvalPolicy") != "never"
            or sandbox.get("type") != "readOnly" or sandbox.get("networkAccess", False) is not False):
        raise CodexProtocolError("readonly-policy-unverified",
                                 "Codex did not acknowledge the private read-only permission profile")
    if response.get("model") != spec["model"] or response.get("modelProvider") != "openai" \
            or Path(response.get("cwd") or "").resolve() != Path(cwd).resolve():
        raise CodexProtocolError("readonly-configuration-mismatch",
                                 "Codex acknowledged a different read-only configuration")
    thread = response.get("thread") or {}
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or Path(thread.get("cwd", "")).resolve() != Path(cwd).resolve():
        raise CodexProtocolError("wrong-native-workspace", "Read-only native checkout differs")
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    state.requested = spec
    return thread_id, {"acknowledged": acknowledged, "filesystem": filesystem}


def _configure_fast(connection: Connection, request: RunRequest, state: _RunState,
                    home: Path) -> str:
    """Fast configuration: the private no-tool layers readback, then the thread."""
    cwd, spec = request.cwd, configuration_spec(request)
    expected = tomllib.loads((home / "config.toml").read_text())
    config_read = connection.call("config/read", {"cwd": cwd, "includeLayers": True})
    configured, layers = config_read.get("config"), config_read.get("layers")
    own = [layer for layer in layers or [] if isinstance(layer, dict)
           and isinstance(layer.get("name"), dict) and layer["name"].get("type") == "user"
           and layer["name"].get("file") == str(home / "config.toml")]
    foreign = [layer for layer in layers or [] if isinstance(layer, dict)
               and isinstance(layer.get("name"), dict)
               and layer["name"].get("type") not in ("user", "packagedDefaults") and layer.get("config")]
    if (not isinstance(configured, dict) or configured.get("web_search") != "disabled"
            or configured.get("approval_policy") != "never" or configured.get("mcp_servers")
            or not isinstance(layers, list) or len(own) != 1 or foreign
            or not policy_matches(own[0].get("config"), expected)
            or any(layer is not own[0] and isinstance(layer, dict)
                   and isinstance(layer.get("name"), dict) and layer["name"].get("type") == "user"
                   for layer in layers)):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex effective no-tool policy differs")
    response = connection.call("thread/start", {
        "cwd": cwd, "model": spec["model"], "modelProvider": "openai",
        "approvalPolicy": "never", "sandbox": "read-only", "serviceName": "hey-my-buddy",
        "experimentalRawEvents": True, "environments": [], "dynamicTools": [],
        "selectedCapabilityRoots": [],
        "allowProviderModelFallback": False, "ephemeral": True,
        "baseInstructions": "Return only JSON matching the supplied output schema. Do not call tools.",
    })
    thread = response.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if (not isinstance(thread_id, str) or not thread_id
            or Path(thread.get("cwd") or "").resolve() != Path(cwd).resolve()
            or thread.get("turns") not in (None, [])
            or response.get("model") != spec["model"] or response.get("modelProvider") != "openai"
            or response.get("approvalPolicy") != "never" or thread.get("environments") != []):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex did not acknowledge the no-tool thread")
    state.thread_opened = True
    state.thread_id = thread_id
    state.configured = True
    state.requested = spec
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

    def __init__(self, thread_id: str, turn_id: str, state: dict):
        self.thread_id, self.turn_id, self.state = thread_id, turn_id, state

    def __call__(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params")
        if method in ("account/rateLimits/updated", "remoteControl/status/changed", "deprecationNotice"):
            return
        if method == "thread/started" and isinstance(params, dict):
            thread = params.get("thread") or {}
            if thread.get("id") == self.thread_id and thread.get("environments") == []:
                return
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            raise CodexProtocolError("invalid-native-result", "Unbound native event in no-tool stream")
        if method == "error":
            if params.get("willRetry") is True:
                return
            raise CodexProtocolError("native-turn-failed", "Native no-tool turn reported an error")
        if method in ("thread/tokenUsage/updated", "thread/status/changed", "thread/settings/updated", "warning"):
            return
        turn = params.get("turn") if isinstance(params, dict) else None
        event_turn = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        if event_turn != self.turn_id:
            raise CodexProtocolError("invalid-native-result", "Foreign native turn in no-tool stream")
        if method == "turn/started":
            if self.state["started"]:
                raise CodexProtocolError("invalid-native-result", "Duplicate native turn start")
            self.state["started"] = True
        elif method in ("item/started", "item/updated", "item/completed"):
            item = params.get("item") if isinstance(params, dict) else None
            if method == "item/completed" and isinstance(item, dict) \
                    and item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                if self.state["final"] is not None:
                    raise CodexProtocolError("invalid-native-result", "Multiple final answers")
                self.state["final"] = item
        elif method == "turn/completed":
            if self.state["completed"] is not None:
                raise CodexProtocolError("invalid-native-result", "Duplicate native turn completion")
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
    raw_type = (params.get("item") or {}).get("type") if isinstance(params, dict) else None
    if method == "rawResponseItem/completed" and raw_type in (
            "function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output",
            "local_shell_call", "web_search_call"):
        _retain_event(capture, "rawToolEvents", message, per_event=16384, total=262144, cap=128)
    elif method in ("item/started", "item/completed") and raw_type not in ("agentMessage", "reasoning", "userMessage"):
        _retain_event(capture, "toolEvents", message, per_event=8192, total=131072, cap=64)


def _admit_turn(connection: Connection, request: RunRequest, thread_id: str,
                input_text: str, mode: str) -> dict:
    """The one turn admission site: send this round's input and schema."""
    params: dict = {"threadId": thread_id, "cwd": request.cwd, "model": request.configuration.model,
                    "effort": request.configuration.effort, "approvalPolicy": "never",
                    "input": [{"type": "text", "text": input_text}],
                    "outputSchema": request.output_schema.value}
    if mode == "worker":
        params["sandboxPolicy"] = {"type": "workspaceWrite", "writableRoots": [request.cwd],
                                   "networkAccess": False}
    elif mode == "fast":
        params["environments"] = []
    elif mode == "review":
        params["permissions"] = "buddy-router"
    return connection.call("turn/start", params)


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
    if services is not None and not isinstance(services, RunServices):
        raise BoardError("INVALID_ARGUMENT", "codex accepts its own narrow run service binding")
    binding = services if services is not None else RunServices()
    mode = _mode(request)
    if mode == "worker":
        if not isinstance(binding.credential_source, dict):
            raise BoardError("INVALID_ARGUMENT",
                             "a governed codex turn requires the frozen coding-home credential source")
        if not request.identity.input_sha256 or not request.identity.turn_id:
            raise BoardError("INVALID_ARGUMENT",
                             "a governed codex turn carries its turn identity and input digest")
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
    owned_spawn: list = []
    connection: Connection | None = None
    # Every carrier projects its real native tool facts on the one collector;
    # the governed turn's own frames are evidence like the structured calls',
    # without adopting any read-only policy here.
    projector = CodexToolEventProjector({"adapter": "codex", "taskId": request.identity.task_id,
                                         "attemptId": request.identity.attempt_id,
                                         "generation": request.identity.generation})
    activity = _ActivityWriter(services.live)
    chain = _EventChain(facts, _Classifier(facts), projector, overflow_raises=mode == "fast")
    review_policy: dict | None = None
    try:
        prep = _prepare(request, services, mode)
        state.coding_home_prepared = prep.coding_home_prepared
        state.coding_home = prep.home if prep.coding_home_prepared else None
        prep.version = _probe_version(prep.command, prep.environment, request.cwd, prep.deadline, mode)
        state.version = prep.version
        spawn = _spawn_app_server(prep, cancel, owned_spawn)
        connection = spawn.connection
        _wire(connection, chain, facts, observer, request, state, mode)
        state.selected_model_listed = _initialize(connection, prep, services, mode, configuration_spec(request))
        if mode == "worker":
            thread_id = _configure_worker(connection, request, services, prep, state)
            _worker_turn(connection, chain, request, state, thread_id, observer, activity)
        else:
            if mode == "fast":
                thread_id = _configure_fast(connection, request, state, prep.home)
            else:
                thread_id, review_policy = _configure_review(connection, request, state)
            _structured_rounds(connection, chain, request, state, thread_id,
                               mode, observer, activity)
    except ObserverInterrupt:
        state.status, state.reason = "cancelled", "observer-interrupt"
        state.error_text = "the role observer stopped the run"
        state.interrupt_requested = True
        state.interrupt_basis = state.interrupt_basis or "observer-request"
        if connection is not None and state.thread_id and state.turn_id:
            _interrupt(connection, state)
    except CodexProtocolError as error:
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
        _stop_collection(request, services, prep, state, spawn, owned_spawn, mode, cancel)
    return _build_result(request, prep, state, mode, projector, facts, review_policy, activity)


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
    response = _admit_turn(connection, request, thread_id, request.input_text, "worker")
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
    state.quota = _observe_quota(connection, evidence)
    failure_code = turn_failure_code(native_turn)
    if failure_code is not None:
        from ....protocol.usage import classify_quota_code
        if classify_quota_code(failure_code) != "unknown":
            state.quota_failure = {"nativeCode": failure_code, "source": "codex/app-server-turn-error",
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
        response = _admit_turn(connection, request, thread_id, input_text, mode)
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if mode == "fast":
            if not isinstance(turn_id, str) or not turn_id:
                raise CodexProtocolError("invalid-native-result", "Codex returned no native turn identity")
            if turn.get("items") not in (None, []):
                if not isinstance(turn["items"], list) or any(
                        not isinstance(item, dict) or item.get("type") not in _CONVERSATION_ITEMS
                        for item in turn["items"]):
                    raise CodexProtocolError("no-tool-violation", "Native turn started with a tool item")
        elif not isinstance(turn_id, str):
            raise CodexProtocolError("wrong-native-turn", "No read-only turn identity")
        state.turn_id = turn_id
        if chain.projector is not None:
            # The trusted root identity comes only from this native turn receipt.
            chain.projector.observe_root(thread_id, turn_id)
        evidence = None
        if mode == "fast":
            chain.bind(_FastProtocol(thread_id, turn_id, fast_state))
        else:
            evidence = TurnEvidence(thread_id, turn_id)

            def carrier(message: dict, _evidence=evidence, _turn_id=turn_id, _round=round_index) -> None:
                params = message.get("params")
                if not isinstance(params, dict) or params.get("threadId") != thread_id \
                        or params.get("turnId") not in (None, _turn_id):
                    return
                if state.capture is not None:
                    _capture_frame(state.capture, message)
                observed = _evidence.observe(message)
                if observed:
                    activity.write(_evidence, observed[0], observed[1], model_turns_base=_round,
                                   tool_calls=chain.projector.tool_calls)
            chain.bind(carrier)
        if mode == "fast":
            while fast_state["completed"] is None:
                connection.pump()
        else:
            while evidence.completed is None:
                connection.pump()
        raw = _fast_final_checks(fast_state) if mode == "fast" else _review_final_checks(evidence)
        if evidence is not None:
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
        elif not isinstance(message.get("method"), str):
            raise CodexProtocolError("invalid-native-result", "Unrecognized late native frame")
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
        state.interrupt_basis = "native-turn-interrupt-ack"
    except (CodexProtocolError, OSError, ValueError):
        # A closed input (the fast drain already ended the stream) or a failed
        # control write is an unconfirmed interrupt, never a crash and never a
        # claim that the native turn was interrupted.
        state.interrupt_requested = True
        state.interrupt_basis = "native-turn-interrupt-unconfirmed"


# -- phase 5: stop collection -------------------------------------------------------


def _stop_collection(request: RunRequest, services: RunServices, prep: _Preparation,
                     state: _RunState, spawn: _Spawn | None, owned_spawn: list,
                     mode: str, cancel: CancelFlag) -> None:
    """Stop phase: the owned group's conservative confirmation, then cleanup."""
    if spawn is None:
        created = owned_spawn[0] if owned_spawn and isinstance(owned_spawn[0], dict) else None
        if created is not None:
            # The spawn helper itself stopped the child it had created.
            state.shutdown = bool(created["shutdown"])
            state.exit_code = created["exit_code"]
        else:
            # No process object ever existed: the holding side itself confirms
            # the spawn never happened — a known not-started fact, never an
            # unknown, and never inferred from a missing pid after a spawn.
            state.shutdown = True
            state.exit_code = None
        if mode in ("fast", "review"):
            _remove_private_auth(prep.native_root if prep is not None
                                 else Path(request.private_state.native_root))
        return
    shutdown, _signalled = _halt_owned_group(spawn.process, spawn.handle, prep.deadline)
    state.shutdown = shutdown
    state.exit_code = spawn.process.returncode
    try:
        spawn.process.stdout.close()
    except OSError:
        pass
    if mode in ("fast", "review") and shutdown:
        _remove_private_auth(prep.native_root)
    if mode == "worker":
        _save_binding(request, services, prep, state)
    if state.status == "ok" and (not shutdown or spawn.process.returncode != 0):
        state.status, state.reason = "error", "native-shutdown-failed"
        state.error_text = "Codex App Server did not exit with confirmed process-group shutdown"
    if cancel.is_set() and state.status != "ok":
        state.status, state.reason = "cancelled", "user-cancel"
        state.error_text = "the Codex execution was cancelled"


def _save_binding(request: RunRequest, services: RunServices, prep: _Preparation,
                  state: _RunState) -> None:
    checkpoint = state.checkpoint
    if not checkpoint or checkpoint.get("nativeTurnStarted") is not True \
            or checkpoint.get("nativeTurnStatus") != "completed" \
            or not state.shutdown or state.exit_code != 0:
        return
    binding = {"taskId": checkpoint["taskId"], "threadId": state.thread_id, "cwd": request.cwd,
               "configuration": state.requested, "lastTurnId": state.turn_id,
               "lastAttemptId": checkpoint["attemptId"], "lastInputSha256": checkpoint["inputSha256"],
               "codexHome": prep.environment["CODEX_HOME"],
               "credentialSource": services.credential_source}
    _write_binding(prep.native_root, state.thread_id, binding)
    checkpoint["bindingSaved"] = True
    state.binding_saved = True
    state.binding_path = _binding_path(prep.native_root, state.thread_id)


def _remove_private_auth(native_root: Path) -> None:
    auth = native_root / "codex-home" / "auth.json"
    if auth.is_symlink():
        auth.unlink()


# -- the factual result --------------------------------------------------------------


_tool_evidence_package = shape_guard(ToolEvidencePackage)
_json_package = shape_guard(OptionalFrozenJsonAt(MAX_SCHEMA_BYTES))
_usage_package = shape_guard(UsagePackage)
_quota_package = shape_guard(NativeFailurePackage)
_message_package = shape_guard(LastAssistantMessagePackage)
_activity_package = shape_guard(ActivityPackage)


def _bounded(value: str | None, limit: int) -> str | None:
    return value if value is not None and len(value.encode()) <= limit else None


def _checked_configuration(request: RunRequest, state: _RunState, mode: str) -> ResultConfiguration:
    checked = CheckedConfiguration()
    if state.configured:
        catalog_model = CheckedValue(value=request.configuration.model)
        catalog_effort = CheckedValue(value=request.configuration.effort)
        if mode == "worker":
            checked = CheckedConfiguration(
                provider=CheckedValue(value=request.configuration.provider),
                model=catalog_model, effort=catalog_effort)
        else:
            checked = CheckedConfiguration(
                provider=CheckedValue(value=request.configuration.provider),
                model=CheckedValue(value=request.configuration.model),
                effort=catalog_effort)
    return ResultConfiguration(
        requested=RunConfiguration(provider=request.configuration.provider,
                                   model=request.configuration.model,
                                   effort=request.configuration.effort),
        checked=checked)


def _effective_policy(state: _RunState, mode: str,
                      review_policy: dict | None, home: Path | None) -> EffectivePolicy:
    if not state.configured:
        return EffectivePolicy()
    if mode == "worker":
        return EffectivePolicy()
    if mode == "fast":
        return EffectivePolicy(
            tools=PolicyFact(requested=_json_package({"configuration": "private-no-tool",
                                                      "environments": [], "dynamicTools": [],
                                                      "modelCatalog": str(home / "no-tool-models.json") if home else ""})))
    return EffectivePolicy(
        tools=PolicyFact(requested=_json_package(review_policy["acknowledged"] if review_policy else {})))


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
    if state.coding_home_prepared:
        # The Worker run's own preparation fact: the home it really created in
        # this native root. The outer cleanup reads exactly this marker, never
        # an inference from a pid or a mode label.
        retain("coding-home", invocation_root / "coding-home.json",
               {"adapter": "codex", "nativeRoot": str(Path(request.private_state.native_root)),
                "home": str(state.coding_home) if state.coding_home is not None else None})
    stderr = invocation_root / _NATIVE_STDERR_FILE
    if stderr.is_file():
        raw = stderr.read_bytes()
        refs.append(EvidenceRef(kind="native-stderr", location=str(stderr), size_bytes=len(raw),
                                sha256=hashlib.sha256(raw).hexdigest()))
    return tuple(refs)


def _build_result(request: RunRequest, prep: _Preparation | None, state: _RunState, mode: str,
                  projector: CodexToolEventProjector | None, facts: _RunFacts,
                  review_policy: dict | None, activity: _ActivityWriter) -> RunResult:
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
        effective_policy=_effective_policy(state, mode, review_policy,
                                           prep.home if prep is not None else None),
        activity=_activity_package(activity.last_payload),
        usage=_usage_package(state.token_usage),
        native_failure=_quota_package(state.quota_failure),
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
    version = _probe_version(command, environment, cwd, deadline, "discovery")
    prep = _Preparation(command=command, environment=environment, native_root=native_root,
                        invocation_root=invocation_root,
                        stderr_path=invocation_root / _NATIVE_STDERR_FILE,
                        deadline=deadline, incoming=incoming, cwd=cwd, version=version)
    spawn = _spawn_app_server(prep, cancel, [])
    error: Exception | None = None
    catalog_value = None
    try:
        connection = spawn.connection
        connection.call("initialize", _INIT_CLIENT)
        connection.send({"method": "initialized", "params": {}})
        try:
            account = connection.call("account/read", {"refreshToken": False}).get("account")
        except CodexProtocolError:
            account = None
        independent = _account_binding(prep, RunServices()).get("source") == "worker"
        account_status = _discovery_account_status(account, independent)
        catalog_value, _listed_ids = _catalog(connection, version)
        catalog_value["discoveries"] = [{"adapter": "codex", "status": "complete",
                                         "accountStatus": account_status}]
        if account_status == "unknown":
            catalog_value["warnings"] = [*catalog_value["warnings"],
                                         "The discovery session could not confirm the Codex account; "
                                         "whether to trust this reading is the board's judgment"]
    except Exception as caught:
        error = caught
    finally:
        shutdown, _signalled = _halt_owned_group(spawn.process, spawn.handle, deadline)
        try:
            spawn.process.stdout.close()
        except OSError:
            pass
    if error is not None:
        # The stop fact of the process this run really owned, handed to the
        # role controller beside the original reason: never inferred from an
        # error code, and an unconfirmed halt stays False.
        error.discovery_shutdown_confirmed = shutdown is True
        raise error
    if not shutdown or spawn.process.returncode != 0:
        failure = CodexProtocolError("native-shutdown-failed",
                                     "the native app server did not exit normally with confirmed group shutdown")
        failure.discovery_shutdown_confirmed = shutdown is True
        raise failure
    return catalog_value


# -- the registered seam's role-facing operations ----------------------------------


def check_preparation(spec: dict, environment: dict) -> None:
    """Confirm the native command and the one provider this harness runs.

    This is the harness's own share of the Worker and fast preparation: the
    command selection without a spawn and the native-provider rule. Every
    governed-turn, resume-mode and private-state rule belongs to the shared
    role executor and is not repeated here.
    """
    try:
        cli_command(environment)
    except CodexUnavailable as error:
        raise BoardError("ADAPTER_UNAVAILABLE", str(error), adapter="codex") from None
    if spec.get("provider") != "openai":
        raise BoardError("INVALID_ARGUMENT",
                         "Codex account-plan execution requires the native OpenAI provider", adapter="codex")


def _incoming_worker_selection(environment: dict, state: Path) -> dict | None:
    """The checked Worker selection the account environment already bound."""
    if not environment.get("BUDDY_ACCOUNT_SELECTION"):
        return None
    try:
        selected = decode_json(environment["BUDDY_ACCOUNT_SELECTION"])
        if (isinstance(selected, dict) and selected.get("adapter") == "codex"
                and selected.get("source") == "worker"
                and environment.get("CODEX_HOME") == str(account_root(state, "codex"))):
            return selected
    except (ValueError, TypeError, AttributeError, OSError):
        pass
    return None


def prepare_run_services(*, account: dict | None, tool_scope: str = "write") -> RunServices:
    """Bind this harness's narrow run services from the controller process.

    The Worker (``write``) coding home's credential source is resolved exactly
    as the legacy carrier did: the service-frozen account when one is bound,
    else the checked Worker selection the account environment already carries
    — never a native fallback for a bound selection. The structured calls
    (``none`` and ``read``) bind only the frozen account identity: no coding
    home is prepared and no coding credential source exists on those carriers. Only paths and identities
    enter the binding; no credential content is read.
    """
    if tool_scope in ("none", "read"):
        return RunServices(account=account if isinstance(account, dict) else None)
    if tool_scope != "write":
        raise BoardError("INVALID_ARGUMENT",
                         "codex binds its native services for the none, read and write tool scopes",
                         adapter="codex")
    environment = dict(os.environ)
    state = environment.get("BUDDY_STATE_DIR")
    if not state:
        raise BoardError("INVALID_ARGUMENT",
                         "a governed codex turn requires the owning private state directory", adapter="codex")
    frozen = account if isinstance(account, dict) else None
    if frozen is None:
        frozen = _incoming_worker_selection(environment, Path(state))
    from .home import credential_source
    source = credential_source(Path(state), environment, account=frozen)
    # The write carrier's account gate reads the credential source, which is
    # always present and always preferred here; the account field belongs to
    # the structured calls alone.
    return RunServices(credential_source=source)


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
        "storageScope": "buddy-goal-private",
        "storageOwner": "buddy-goal",
        "nativeAppVisibility": "not-listed-in-native-app",
        "note": ("The native thread stays in this goal's private CODEX_HOME. Native continuation rechecks "
                 "the goal, home, credential source, checkout, configuration and last completed turn "
                 "binding; live App visibility requires native verification."),
    }


def cleanup_after_run(native_root: Path, result: RunResult) -> dict:
    """Remove the Worker coding home's private auth link after a confirmed stop.

    The caller has confirmed both stop layers. The coding-home marker is this
    run's own SHA-verified preparation fact, so a preparation that failed, a
    foreign native root or a fast/review run removes nothing and reports no
    credential fact.
    """
    marker = _read_evidence_ref(result, "coding-home")
    if not isinstance(marker, dict) or marker.get("nativeRoot") != str(native_root):
        return {}
    from .home import remove_coding_auth
    removed = remove_coding_auth(Path(native_root))
    return {"codingHomePrepared": True, "credentialCleanup": {"complete": True, "removed": removed}}


def native_evidence(result: RunResult) -> dict:
    """Project this run's real native evidence; no missing fact is invented.

    The fast posture comes from the public policy fact; the review
    acknowledgment and configuration readback come from this run's verified
    evidence references, retained as the native side actually answered — a
    policy that failed verification keeps its real readback and is never
    dressed up as enforced. The interrupt facts keep the legacy true keys and
    appear only when a real request (and a real acknowledgement) happened; an
    acknowledgement is never inferred from a confirmed stop. A model the
    on-the-spot catalog did not list keeps its ``selectedModelListed`` fact
    and the selected identity beside it. Keys this run never produced are
    absent, never filled with a placeholder.
    """
    projection: dict = {}
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
    "RunServices", "check_preparation", "cleanup_after_run",
    "execution_deadline", "native_evidence", "prepare_run_services", "run",
    "run_discovery", "session_facts", "validate_turn_provenance",
]
