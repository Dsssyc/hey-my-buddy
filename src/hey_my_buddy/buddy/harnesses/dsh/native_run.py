"""The one native DSH run: a frozen request in, one factual result out.

ADR-025 step 4-B1. This module is the single native execution body of the DSH
harness: the public launch configuration, the owned ACP spawn, the session open,
the model/effort selection with its native readback, the prompt/update loop, the
session close and the conservative group stop happen exactly once here, driven
only by the frozen :class:`~hey_my_buddy.buddy.harnesses.run_contract.RunRequest`.
Every launch — a run, a discovery, a fake agent under test — goes through the
already-accepted :mod:`.acp.launch` wrapper, which forces this run's private
``DSH_HOME`` over the ``native_environment`` allow-list, so no DSH process ever
reaches the user's daily home.

The tool scope is satisfied by DSH's own public launch configuration and
reported as facts: ``none`` disables the confirmed tool rows plus the plan-mode
row through a launch patch, ``read`` injects the public read-only permission
preset, and ``write`` keeps the default workspace-write preset. The permission
callback only refuses escalations; a new tool row the agent reports is a
retained fact, never a claimed restriction, and tools are classified through
the shared fixed table by native name with ``other`` for everything unknown.

The driver owns protocol integrity and facts only: identity binding, the signed
finish/inquiry receipts, the native configuration readback, tool and update
facts, the conservative stop evidence. Whether an unknown event or a tool fact
fails a run stays with the role observer; the discovery operation
(:func:`run_discovery`) is a separate no-prompt metadata read that never
masquerades as a model run. The owning harness's settings and credentials are
never relocated or read: when the source home carries them, the public launch
patch points DSH's own ``settings`` and ``credentials`` rows at those paths and
re-pins the session-record root inside this run's private ``DSH_HOME``, so a
real run keeps its configuration and login without this module ever opening
either file. The observed model identity and the per-step usage of a real turn
live only in DSH's private session record; the optional reader below turns a
matching record into the shared usage facts with bounded streaming reads, and
a missing, foreign or unreadable record stays an unknown fact.

The role-facing seams of the shared harness registry live here too, and nowhere
else in this package: the process-free service binding (:func:`prepare_services`
and its mount), the no-spawn preparation check (:func:`check_preparation`), the
turn provenance validator and the storage facts (:func:`validate_turn_provenance`,
:func:`session_facts`), the fast channel's native evidence projection
(:func:`native_evidence`), and the controller-supplied live endpoint over
the shared cooperative inquiry bridge. Host
questions are only ever queued by the bridge and delivered at the root's own
checkpoint tool call inside the admitted turn; the journal advances only after
this driver verified the signed root-turn receipt. Native resume stays unwired:
a ``reconstructed-new-session`` continuation rebuilds a fresh root session, and
a ``native-session`` continuation is refused explicitly.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ....errors import BoardError
from ....private_dirs import ensure_private_dir, open_regular_fd
from ....protocol.internal_models import OptionalFrozenJsonAt
from ...roles.turn_io import private_json
from ..base import BoundSessionServices
from ..native_support import (
    CancelFlag,
    ObserverInterrupt,
    execution_deadline,
    identity_or_none,
    shape_guard,
    utc_now,
)
from ..inquiry_bridge import InquiryBridge
from ..c_two_live import CTwoLiveEndpoint
from ....protocol.activity import ActivityPublisher
from ..run_contract import (
    ActivityPackage,
    MAX_SCHEMA_BYTES,
    MAX_UNKNOWN_EVENT_TYPES,
    CheckedConfiguration,
    CheckedValue,
    CompletionEvidence,
    EffectivePolicy,
    EvidenceRef,
    InterruptEvidence,
    LastAssistantMessagePackage,
    NativeFailurePackage,
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
    ToolEvidencePackage,
    UsagePackage,
)
from ..runtime_selection import command_for
from .acp import AcpClient
from .acp.client import PermissionPolicy
from .acp.connection import AcpConnectionClosed, AcpRequestError, AcpTimeout, FrameMetaLog, stop_evidence
from .acp.launch import LaunchOwnershipError
from .protocol import (
    MODEL_ACTIVITY_KINDS,
    UPDATE_KINDS,
    DshActivity,
    DshToolFacts,
    NativeError,
    RootTurnEvidence,
    decode_json,
    text_blocks,
)

#: The public child-environment key of DSH's read-only permission preset and its
#: only value this harness sets (F-D2: the preset's sandbox rejects writes and
#: reports upgrade requests to the client's reject-only callback).
PERMISSION_MODE_ENV = "DSH_PERMISSION_MODE"
READ_ONLY_MODE = "read-only"
#: The mode-control tool row the none-scope probe disables together with the
#: tool rows, so no plan-mode tool survives either.
PLAN_MODE_ROW = "plan-mode"
#: The confirmed DSH tool-providing rows the ``none`` scope disables through the
#: public launch patch. The list is a public value of this integration line,
#: bound to the installed DSH's public composition surface: the Host's
#: no-model ``dump-config`` read (2026-10-06, private HOME/DSH_HOME) found
#: sixteen ``tool-`` rows, of which ``tool-result-pruner`` is context pruning
#: and the remaining fifteen are the tool-providing rows — including the two
#: platform-exclusive shells. The historical fourteen-row count predates this
#: surface read and is superseded by it. A future row outside this list is
#: reported as a retained fact, never claimed as restricted; ``commands`` rows
#: cannot be disabled and no vendor package is statically proven here.
NONE_SCOPE_DISABLED_ROWS: tuple[str, ...] = (
    "tool-bash", "tool-pwsh", "tool-jobs", "tool-fs", "tool-fs-search",
    "tool-skill", "tool-subagent-control", "tool-subagent-list-agents",
    "tool-subagent", "tool-subagent-fork", "tool-workflow", "tool-todo",
    "tool-goal", "tool-ralph", "tool-web",
)
#: The rows every launch patch disables for cost and privacy, registered as the
#: fourth and fifth accepted behavior differences (2026-10-06): hey-my-buddy's
#: own DSH launches — runs, fast calls and no-input discovery alike — turn off
#: the title LLM (extra model calls) and the OTel exporter (leaves the private
#: home) through this run's private patch only; a user's own interactive DSH is
#: untouched and no user configuration is changed.
_ALWAYS_DISABLED_ROWS = ("session-title-llm", "session-telemetry-otel")

# -- the private session record's launch constants ------------------------------------

#: DSH writes its session rollout under ``dshHomePath('sessions')``; this run's
#: private ``DSH_HOME`` makes that root attempt-private already, and the launch
#: patch re-pins the row so a bound user setting cannot move it out.
_SESSIONS_DIRNAME = "sessions"
#: The public row id whose config root is re-pinned to the private sessions dir.
_SESSION_ROOT_ROW = "session-persistence-jsonl"
_RECORD_SUFFIXES = (".v3.jsonl.zstd", ".v3.jsonl")
_RECORD_VERSION = 3
_MAX_RECORD_FILES = 64
_MAX_RECORD_DEPTH = 4
#: Decompressed proportionate bounds: a record is read streaming, never wholly
#: into memory, and a bound stop is an honestly partial fact, never a guess.
_MAX_RECORD_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_RECORD_LINE_BYTES = 4 * 1024 * 1024
_MAX_RECORD_STEPS = 512
_RECORD_USAGE_FIELDS = ("inputTokens", "outputTokens", "totalTokens",
                        "cacheReadTokens", "cacheWriteTokens", "reasoningTokens")

_SESSION_TIMEOUT = 30.0
_CONFIG_TIMEOUT = 30.0
_CLOSE_TIMEOUT = 30.0
_SETTLE_SECONDS = 5.0
_DRAIN_SECONDS = 10.0
_MAX_UPDATES = 8192
_MAX_ANSWER_BYTES = 65536
#: How long an interrupted prompt may keep the loop waiting before the run
#: abandons it and lets the owned group stop end the agent.
_ABANDON_SECONDS = 5.0

_END_REASONS = {"end_turn": None, "max_tokens": "native-max-tokens",
                "max_turn_requests": "native-max-turn-requests", "refusal": "native-refusal"}


def tool_scope_launch(scope: str) -> tuple[list[dict], dict[str, str]]:
    """The public launch configuration of one tool scope: patch rows and child env.

    The scope is a declaration this harness satisfies with DSH's own public
    switches and reports as facts (ADR-025 decision 4): ``none`` disables the
    confirmed tool rows plus plan-mode, ``read`` selects the read-only
    permission preset, and ``write`` keeps the default preset — the scope never
    claims a restriction the launch configuration does not make.
    """
    if scope == "write":
        return [], {}
    if scope == "read":
        return [], {PERMISSION_MODE_ENV: READ_ONLY_MODE}
    if scope == "none":
        if not NONE_SCOPE_DISABLED_ROWS:
            raise NativeError(
                "scope-configuration-unconfirmed",
                "the none tool scope needs the confirmed DSH tool-row list; "
                "this run refuses to launch a guessed configuration")
        rows = [{"id": name, "disabled": True}
                for name in (*NONE_SCOPE_DISABLED_ROWS, PLAN_MODE_ROW)]
        return rows, {}
    raise NativeError("invalid-tool-scope", f"the tool scope {scope!r} is not a declared scope")


def source_home(environment: Mapping[str, str]) -> Path:
    """The owning DSH home this harness binds to, by path only.

    The convention of the existing entries (the direct-LLM runner and the
    catalog probe): the parent ``DSH_HOME`` when set, else the user's default
    DSH home. This module never reads the home's files; the settings and
    credentials documents stay where they are and are only ever named in the
    public launch patch.
    """
    return Path(environment.get("DSH_HOME") or Path.home() / ".dsh").resolve()


def source_binding_rows(source: Path) -> list[dict]:
    """The two patch rows that keep a real run's configuration and login.

    When the owning home carries the settings document or the credentials
    store, DSH's own public ``settings``/``credentials`` rows are pointed at
    those paths (``watch`` off): path passing only, no content is read, copied
    or written, and no new login happens. Files that do not exist contribute no
    row, so a test or probe home binds nothing.
    """
    rows: list[dict] = []
    settings = source / "settings.yaml"
    credentials = source / ".credentials.yaml"
    if settings.is_file():
        rows.append({"id": "settings", "config": {"path": str(settings), "watch": False}})
    if credentials.is_file():
        rows.append({"id": "credentials", "config": {"path": str(credentials), "watch": False}})
    return rows


_ACP_PROFILE_MANIFEST = {
    "name": "dsh-profile-acp", "private": True, "dependencies": {},
    "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"],
                        "patchReload": "startup"}}}
_ACP_PROFILE_CORDIS = (
    "# dsh profile root — an empty entry list. The tree is composed as patches:\n"
    "# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml, then any\n"
    "# --patch overlays. Edit cordis.patch.yml, not this file.\n[]\n")
_ACP_PROFILE_PATCH = (
    "# Your patch layer for this dsh profile, applied after every bundle layer:\n"
    "# a top-level YAML array of loader patch entries (id-targeted config\n"
    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n[]\n")
_ACP_PROFILE_WORKSPACE = "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n"


def materialize_acp_profile(dsh_home: Path) -> None:
    """Write the private home's acp profile: the Host-proven boot composition.

    The manifest names the two ACP bundles; the dsh installation resolves them
    itself (the owning home carries no acp profile to copy, and eligibility
    stays at the selected command). The files are written once per private
    home; a home that already carries them is left exactly as it is.
    """
    profile = dsh_home / "profiles" / "acp"
    if (profile / "package.json").is_file():
        return
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_json(profile / "package.json", _ACP_PROFILE_MANIFEST)
    for name, text in (("cordis.yml", _ACP_PROFILE_CORDIS),
                       ("cordis.patch.yml", _ACP_PROFILE_PATCH),
                       ("pnpm-workspace.yaml", _ACP_PROFILE_WORKSPACE)):
        target = profile / name
        fd = open_regular_fd(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)


# -- the role-facing service binding ---------------------------------------------


@dataclasses.dataclass(frozen=True)
class SessionServiceMount:
    """The mechanical, process-free binding of one run's session service.

    Everything here is derived without starting anything: the private MCP server
    name and the fully qualified session tool names, the signing bridge the
    session tools read, and the ``session/new`` mount description. The role
    keeps its task prompt text against these exact names and puts the assembled
    input into ``RunRequest.input_text``; the driver mounts this same service
    and verifies the call evidence against it.
    """

    server_name: str
    finish_tool: str
    checkpoint_tool: str | None
    answer_tool: str | None
    bare_tools: tuple[str, ...]
    bridge: dict
    mcp_servers: tuple[dict, ...]
    bridge_config_path: str
    #: The mounted tools' own contracts (``{"name": ..., "inputSchema": ...}``),
    #: exactly the tools the session MCP carrier publishes for this run.
    tool_contracts: tuple[dict, ...] = ()


@dataclasses.dataclass(frozen=True)
class SessionServices:
    """The role-held service instance of one governed run (the seam's ``services``).

    The driver sees only this narrow binding: the mechanical mount, the role's
    outcome validator for finish-receipt verification, the optional inquiry
    channel credentials and the controller-owned live endpoint. No board client,
    database handle, execution context or credential file travels in it.
    ``native_stderr`` is the role's own path for the child's captured stderr
    tail; the ACP wrapper owns the capture, and the governed driver mirrors its
    bounded tail there when the role supplied a path.
    """

    mount: SessionServiceMount
    validate_outcome: Callable[[object], str | None]
    inquiry: dict | None = None
    live: CTwoLiveEndpoint | None = None
    native_stderr: str | None = None


def prepare_session_service(*, invocation_root: Path, identity: dict, input_sha256: str,
                            attention_path: Path, session_tools: list[dict] | tuple[dict, ...],
                            completion_tool: str,
                            inquiry: dict | None = None,
                            inquiry_tools: tuple[str, ...] = ()) -> SessionServiceMount:
    """Prepare one run's session-service mount; no process is started.

    The naming is the mechanical rule the native driver and the role share: one
    private MCP server per attempt whose tools DSH presents as
    ``mcp__<server>__<tool>``. The bridge configuration (attempt identity,
    turn-input hash, signing key, attention and optional journal paths) is
    published once here; the session tools and the driver's receipt verification
    read the same values. Nothing here knows the Worker contract or any role's
    business rules: the stdio carrier is the shared
    :mod:`hey_my_buddy.buddy.roles.session_mcp` module the Host consolidated,
    and this mount only names it in the child command.
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
    # The checkpoint/answer tools are mounted whenever the session tools carry
    # them, inquiry channel or not: their verified receipts and refusal
    # envelopes are the session-tool seam's own delivery evidence either way.
    # The inquiry channel only decides whether the bridge carries the journal
    # the tools read and which names the role's prompt cites.
    checkpoint_tool = qualified.get("buddy_checkpoint")
    answer_tool = qualified.get("buddy_answer_inquiry")
    bridge = {"identity": dict(identity), "inputSha256": input_sha256, "key": secrets.token_hex(32),
              "attentionPath": str(attention_path)}
    if inquiry is not None and isinstance(inquiry.get("resultsPath"), str):
        bridge["inquiryJournalPath"] = inquiry["resultsPath"]
    bridge_path = root / "finish-bridge.json"
    private_json(bridge_path, bridge, exclusive=True)
    mcp = [{"name": server_name, "command": sys.executable,
            "args": ["-m", "hey_my_buddy.buddy.roles.session_mcp", "--config", str(bridge_path)],
            "env": [{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}] if os.environ.get("PYTHONPATH") else []}]
    return SessionServiceMount(
        server_name=server_name, finish_tool=finish_tool,
        checkpoint_tool=checkpoint_tool,
        answer_tool=answer_tool,
        bare_tools=tuple(tool["name"] for tool in session_tools),
        bridge=bridge, mcp_servers=tuple(mcp), bridge_config_path=str(bridge_path),
        tool_contracts=tuple({"name": tool["name"], "inputSchema": tool["inputSchema"]}
                             for tool in session_tools))


def prepare_services(*, invocation_root: Path, identity: dict, input_sha256: str,
                     attention_path: Path, session_tools, completion_tool: str,
                     validate_outcome,
                     inquiry: dict | None, inquiry_tools: tuple[str, ...],
                     native_stderr: str) -> BoundSessionServices:
    """Expose only the binding the role actually consumes, without a process.

    The role supplies the mechanical mount and outcome validator.
    ``native_stderr`` is consumed as the governed stderr-tail mirror; the
    controller injects its optional live endpoint after preparation.
    """
    mount = prepare_session_service(
        invocation_root=invocation_root, identity=identity, input_sha256=input_sha256,
        attention_path=attention_path, session_tools=session_tools,
        completion_tool=completion_tool,
        inquiry=inquiry, inquiry_tools=inquiry_tools)
    return BoundSessionServices(
        description=SessionService(
            tool_names=tuple(f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools)),
        completion_tool=mount.finish_tool, checkpoint_tool=mount.checkpoint_tool,
        answer_tool=mount.answer_tool,
        services=SessionServices(mount=mount, validate_outcome=validate_outcome,
                                 inquiry=inquiry,
                                 native_stderr=native_stderr))


#: The cooperative-checkpoint delivery fact, stated as this harness's own
#: limitation: a Host question only reaches the root through the checkpoint tool
#: the root itself calls inside the one admitted turn. Nothing is injected into
#: the native turn, no input arrives mid-turn, and only a verified root-turn
#: receipt advances the journal.
CHECKPOINT_INQUIRY_NOTE = (
    "Host questions reach this root only when the root itself calls buddy_checkpoint inside the one "
    "admitted native turn (cooperative-checkpoint delivery); they are never injected mid-turn, never "
    "start a new turn, and an answer counts only through buddy_answer_inquiry verified against this "
    "root turn's own tool evidence"
)


def _inquiry_event_metadata(message: dict) -> dict:
    """The two native metadata items the bridge's live view keeps: kind and tool name.

    Only the ACP update kind and, for tool frames, the tool title are read —
    never reasoning, tool arguments, output or any message body.
    """
    params = message.get("params") if isinstance(message, dict) else None
    kind = tool_name = None
    if isinstance(params, dict):
        update = params.get("update")
        if isinstance(update, dict):
            kind = update.get("sessionUpdate")
            title = update.get("title")
            if kind in ("tool_call", "tool_call_update") and isinstance(title, str):
                tool_name = title
    return {"kind": kind if isinstance(kind, str) and kind else "event", "toolName": tool_name}


def make_inquiry_bridge(*, identity: dict, journal_path: str,
                        attention_path: str | None = None,
                        live: CTwoLiveEndpoint | None = None) -> InquiryBridge:
    """The shared cooperative bridge over this harness's own native error shape."""
    return InquiryBridge(identity=identity, journal_path=journal_path,
                         attention_path=attention_path, live=live, error_factory=NativeError,
                         event_metadata=_inquiry_event_metadata, limitation=CHECKPOINT_INQUIRY_NOTE)


def check_preparation(spec: dict, environment: dict) -> None:
    """Confirm the capability: a ready installed dsh command, selected or discoverable.

    A service-selected record is confirmed without spawning. Without one, the
    same bounded discovery that :func:`command_for` falls back to refreshes the
    selection first — the harness's own version handshake is the only thing it
    launches, never a prompt or a model call. A missing or unhealthy command
    refuses here, before any attempt builds a run.
    """
    from ..runtime_selection import selected
    record = selected("dsh", environment)
    if record is None:
        from ..discovery import discover
        record = discover("dsh", environment=environment)
    if not isinstance(record, dict) or record.get("status") != "ready" or not record.get("command"):
        raise BoardError("ADAPTER_UNAVAILABLE",
                         "the dsh run needs a selected, ready installed dsh command; refresh the harness selection",
                         adapter="dsh")


def session_facts(native_root: Path, session_id: str | None) -> dict:
    """Storage and visibility facts only; the role decides any reuse.

    This run's rollout is pinned into the attempt's private ``DSH_HOME`` sessions
    root by the launch patch, and the owning settings and credentials stay in the
    user's DSH home by path only, so the installed app never lists this session.
    DSH saves no continuation binding — native resume stays unwired — so the
    honest binding fact is its absence, and reuse is always a rebuilt session.
    """
    return {
        "adapter": "dsh", "sessionId": session_id, "captured": session_id is not None,
        "storageScope": "attempt-private-sessions", "storageOwner": "buddy-attempt",
        "nativeAppVisibility": "not-listed-in-native-app",
        "credentialsStore": "harness-user-store",
        "bindingPresent": False,
        "note": (
            "this run's session rollout lives under its attempt-private DSH_HOME sessions root; the DSH "
            "home, settings and credentials store stay with the owning harness by path only, so the "
            "installed app lists none of it. DSH saves no continuation binding: native resume is not "
            "wired, and every continuation rebuilds a new session"
        ),
    }


def validate_turn_provenance(record: dict) -> str | None:
    """The governed turn needs the ordered finish, settlement and close evidence.

    The expected provenance is exactly what :meth:`RootTurnEvidence.provenance`
    retains, so a real verified receipt is the only shape that passes; a forged,
    foreign-session or reordered record fails here before the turn is imported.
    """
    p = record.get("provenance")
    expected = {"version": 1, "adapter": "dsh", "tool": "buddy_finish_turn", "turnEnd": "completed",
                "stopReason": "end_turn", "rootSessionMatched": True, "receiptVerified": True,
                "sessionClose": "acknowledged"}
    if not isinstance(p, dict) or any(type(p.get(k)) is not type(v) or p.get(k) != v for k, v in expected.items()):
        return "the dsh turn lacks its completed finish-tool evidence"
    if p.get("nativeSessionId") != record.get("sessionId"):
        return "the dsh native root session does not match the turn record"
    if not isinstance(p.get("toolCallId"), str) or not p["toolCallId"]:
        return "the dsh turn carries no finish tool call identity"
    if not isinstance(p.get("receiptId"), str) or len(p["receiptId"]) != 32:
        return "the dsh turn carries no verified finish receipt identity"
    ordinals = [p.get(k) for k in ("callOrdinal", "resultOrdinal", "settledOrdinal", "sessionCloseOrdinal")]
    if (any(type(value) is not int or value < 0 for value in ordinals)
            or not ordinals[0] < ordinals[1] <= ordinals[2] <= ordinals[3]):
        return "the dsh native evidence is out of order"
    mode, previous = record.get("resumeMode"), record.get("previousSessionId")
    if mode == "initial" and previous is None:
        return None
    if mode == "reconstructed-new-session" and (
            previous is None or isinstance(previous, str) and previous.strip()
            and record.get("sessionId") != previous):
        return None
    return "the dsh turn resume identity is invalid"


def native_evidence(result: RunResult) -> dict:
    """The none-scope launch configuration and stream end, without any policy guess."""
    policy = result.effective_policy.tools
    requested = policy.requested.value if policy is not None and policy.requested is not None else None
    settings = requested if isinstance(requested, dict) else {}
    completion = result.completion_evidence
    return {"eventCount": result.native_event_count,
            "disabledRows": settings.get("disabledRows"),
            "streamEof": completion.stream_end if completion is not None else None}


def _check_service_descriptions(request: RunRequest, mount: SessionServiceMount) -> None:
    """Verify the mount is exactly what this run describes.

    Three sets must be one set: the qualified tool names the request describes,
    the tools the mount actually carries, and — through the mount's contracts —
    the verification set the receipt checks use. The requested output schema
    must be the completion tool's own contract, so a verified receipt can only
    ever stand for the schema the request asked for.
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


# -- the run state and the spawn stage --------------------------------------------


@dataclasses.dataclass
class _RunState:
    """The run's own end facts, recorded per stage as it actually happened."""

    status: str = "error"
    reason: str | None = None
    error_text: str | None = None
    model_started: bool = False
    harness_version: str | None = None
    session_opened: bool = False
    configured: bool = False
    checked: dict | None = None
    interrupt_requested: bool = False
    interrupt_basis: str | None = None
    signalled: bool = False
    deadline_hit: bool = False
    observer_stopped: bool = False
    attention_error: str | None = None
    update_overflow: bool = False
    prompt_stop_reason: str | None = None
    event_count: int = 0
    #: The cooperative bridge's own reports, set at settlement; a run without an
    #: inquiry channel reports neither.
    inquiry: dict | None = None
    attention: dict | None = None
    #: The accepted finish's ordered native evidence, retained for the role's
    #: turn record; any earlier failure leaves it unset.
    provenance: dict | None = None
    #: The launch wrapper's own stop evidence when its bookkeeping failed after
    #: the child existed: the process was held, so its group facts are real.
    spawn_failure: dict | None = None
    stop: dict = dataclasses.field(default_factory=dict)


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _launch_agent(*, cwd: str, native_root: Path, invocation_root: Path, dsh_home: Path,
                  scope_rows: list[dict], extra_env: dict[str, str],
                  permission_policy) -> AcpClient:
    """Spawn the one owned ACP agent through the accepted private-launch wrapper.

    ``DSH_HOME`` is always this run's private directory inside the native root,
    the child environment keeps exactly the ``native_environment`` allow-list
    plus the validated homes and the scope's public key, and the whole argv —
    the selected DSH command, the ``acp`` profile and this run's patch — is
    recorded in the wrapper's launch log. ``HOME`` stays inherited; a caller
    that needs this run's private home names it through the wrapper itself,
    which validates it like ``DSH_HOME``. Every launch of this module carries the same
    patch order: the owning home's settings/credentials rows first, then the
    private session-record root pin (so a bound setting cannot move the rollout
    out of the private home), then the tool scope's rows, then the two
    always-off rows last. Any failure after the child exists keeps the
    ownership evidence on the error instead of dropping the child.
    """
    command = command_for("dsh", dict(os.environ))
    argv = [*command, "--profile", "acp"]
    rows = [*source_binding_rows(source_home(os.environ)),
            {"id": _SESSION_ROOT_ROW, "config": {"root": str(dsh_home / _SESSIONS_DIRNAME)}},
            *scope_rows,
            *({"id": name, "disabled": True} for name in _ALWAYS_DISABLED_ROWS)]
    if rows:
        patch_path = invocation_root / "dsh-launch-patch.json"
        private_json(patch_path, rows, exclusive=True)
        argv += ["--patch", str(patch_path)]
    materialize_acp_profile(dsh_home)
    try:
        return AcpClient.start(argv, private_root=native_root, dsh_home=dsh_home,
                               extra_env=extra_env or None, cwd=Path(cwd),
                               frame_log=FrameMetaLog(native_root / "logs" / "frames.jsonl"),
                               permission_policy=permission_policy)
    except LaunchOwnershipError as error:
        # The child existed and the launch wrapper itself finalized it; the run
        # keeps the observed stop facts instead of dropping them with the error.
        failure = NativeError("adapter-unavailable",
                              "the ACP agent failed during its launch bookkeeping")
        failure.spawn_evidence = error.evidence
        raise failure from None
    except OSError:
        raise NativeError("adapter-unavailable", "The selected DSH executable could not start") from None


def _native_call(call, *, code: str, message: str):
    """One bounded native request outside the prompt: errors stay stage-specific."""
    try:
        return call()
    except AcpRequestError as error:
        raise NativeError(code, f"{message}: the native agent refused {error.method}") from None
    except AcpTimeout:
        raise NativeError(code, f"{message}: the native agent did not answer within its budget") from None
    except AcpConnectionClosed:
        raise NativeError("native-disconnected",
                          f"{message}: the native agent connection ended before the answer") from None


def _initialize(client: AcpClient) -> str | None:
    """The protocol handshake; the version text is optional metadata."""
    result = _native_call(lambda: client.initialize(timeout=_SESSION_TIMEOUT),
                          code="adapter-unavailable", message="the ACP initialize failed")
    version = (result.get("agentInfo") or {}).get("version") if isinstance(result, dict) else None
    return version if isinstance(version, str) and version else None


# -- the configuration stage -------------------------------------------------------


def _declared_model_value(options: list, configuration: RunConfiguration) -> str:
    """The declared model option value string for this run's provider and model."""
    for option in options or []:
        if not isinstance(option, dict) or option.get("id") != "model":
            continue
        for group in option.get("options") or []:
            if not isinstance(group, dict):
                continue
            for entry in group.get("options") or []:
                if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
                    continue
                try:
                    parsed = json.loads(entry["value"])
                except ValueError:
                    continue
                if (isinstance(parsed, list) and len(parsed) == 2
                        and parsed[0] == configuration.provider and parsed[1] == configuration.model):
                    return entry["value"]
    raise NativeError("configuration-unavailable",
                      "the requested DSH provider/model is not in the native declared options")


def _declared_effort_value(options: list, configuration: RunConfiguration) -> str:
    """The declared reasoning-effort value for this run's effort."""
    for option in options or []:
        if not isinstance(option, dict) or option.get("id") != "reasoning_effort":
            continue
        declared = [entry.get("value") for entry in (option.get("options") or [])
                    if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
        if configuration.effort in declared:
            return configuration.effort
        break
    raise NativeError("invalid-configuration",
                      "the requested reasoning effort is not a declared native option")


def _readback(options: list, option_id: str) -> str | None:
    for option in options or []:
        if isinstance(option, dict) and option.get("id") == option_id:
            value = option.get("currentValue")
            return value if isinstance(value, str) else None
    return None


def _configure(client: AcpClient, session_id: str, snapshot: dict,
               configuration: RunConfiguration) -> dict:
    """The one model/effort selection site: declared values in, readback out.

    Both values are set through ``session/set_config_option`` and confirmed only
    from the response's own option group — the requested value never stands in
    for a checked one, and DSH emits no configuration notifications to wait for.
    """
    options = snapshot.get("configOptions") if isinstance(snapshot, dict) else None
    if not isinstance(options, list):
        raise NativeError("configuration-unavailable", "the native session exposed no configuration options")
    model_value = _declared_model_value(options, configuration)
    effort_value = _declared_effort_value(options, configuration)
    echoed = _native_call(lambda: client.set_config_option(session_id, "model", model_value,
                                                           timeout=_CONFIG_TIMEOUT),
                          code="configuration-unavailable", message="the native model selection failed")
    if _readback(echoed.get("configOptions") if isinstance(echoed, dict) else None, "model") != model_value:
        raise NativeError("configuration-mismatch", "the native session did not retain the requested model")
    echoed = _native_call(lambda: client.set_config_option(session_id, "reasoning_effort", effort_value,
                                                           timeout=_CONFIG_TIMEOUT),
                          code="invalid-configuration", message="the native effort selection failed")
    if _readback(echoed.get("configOptions") if isinstance(echoed, dict) else None,
                 "reasoning_effort") != effort_value:
        raise NativeError("configuration-mismatch", "the native session did not retain the requested reasoning effort")
    return {"provider": configuration.provider, "model": configuration.model,
            "effort": configuration.effort, "modelValue": model_value}


# -- the update pump and the observer seam ------------------------------------------


class _RunFacts:
    """The cumulative, normalized facts of one run, as the observer sees them."""

    def __init__(self):
        self.unknown_counts: dict[str, int] = {}

    def note_unknown(self, label: object) -> None:
        name = label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label) else "unknown"
        if name not in self.unknown_counts and len(self.unknown_counts) >= MAX_UNKNOWN_EVENT_TYPES - 1:
            # The shared contract bounds the distinct kinds. The surplus folds
            # into one bucket so an over-wide classification can never cost the
            # run its total, its stop facts or its result.
            name = "unclassified-surplus"
        self.unknown_counts[name] = self.unknown_counts.get(name, 0) + 1

    def mapping(self, *, tool_calls: int, settled: bool, raw_answer: str | None,
                denied: int) -> dict:
        return {
            "settled": settled,
            "rawAnswer": raw_answer,
            "toolCalls": tool_calls,
            # DSH has no separate marker-frame family: every tool frame is
            # already a projected fact counted in toolCalls.
            "toolMarkerFrames": 0,
            "unknownEvents": {"countsByType": dict(self.unknown_counts),
                              "total": sum(self.unknown_counts.values())},
            "deniedInteractions": denied,
        }


class _Pump:
    """The one bounded queue between the ACP reader thread and the run loop.

    The reader thread must never block and never die on a slow consumer: an
    update that arrives while the queue is full is dropped and recorded as the
    run's own overflow fact, which fails the run instead of hiding a frame.
    """

    def __init__(self, connection):
        self.updates: queue.Queue = queue.Queue(maxsize=_MAX_UPDATES)
        self.overflow = False
        connection.observe(self._enqueue)

    def _enqueue(self, message) -> None:
        try:
            self.updates.put_nowait(message)
        except queue.Full:
            self.overflow = True

    def get(self, timeout: float):
        return self.updates.get(timeout=timeout)

    def drain(self) -> list:
        remaining = []
        while True:
            try:
                remaining.append(self.updates.get_nowait())
            except queue.Empty:
                return remaining

    def empty(self) -> bool:
        return self.updates.empty()


class _AttentionPolicy:
    """The reject-only permission policy plus the attention fact it produces.

    The answers come from the shared :class:`PermissionPolicy` — this policy
    never allows an escalation — and every refusal is recorded where the role
    reads attention facts, so the role's own completion rule can refuse a
    ``completed`` outcome. With a mounted inquiry bridge the shared bridge owns
    the attention record and its identity-bound file; without one this policy
    keeps the file current itself. A write failure is recorded, never fatal: the
    refusal itself already answered.
    """

    def __init__(self, bridge: dict, state: "_RunState",
                 inquiry_bridge: "InquiryBridge | None" = None):
        self._policy = PermissionPolicy()
        self._bridge = bridge
        self._state = state
        self._inquiry_bridge = inquiry_bridge

    def decide(self, params):
        outcome, basis = self._policy.decide(params)
        record = {"ts": utc_now(), "outcome": outcome.get("outcome"), "basis": basis[:400]}
        if self._inquiry_bridge is not None:
            self._inquiry_bridge.note_attention(record)
            return outcome, basis
        requests = [record]
        try:
            path = Path(self._bridge["attentionPath"])
            if path.is_file():
                value = json.loads(path.read_text()).get("requests")
                requests = [item for item in value if isinstance(item, dict)][:7] + requests \
                    if isinstance(value, list) else requests
            private_json(path, {"version": 1, "requests": requests})
        except (OSError, ValueError, BoardError) as error:
            self._state.attention_error = type(error).__name__
        return outcome, basis


# -- the optional private session record ---------------------------------------------


class _RecordStream:
    """Bounded streaming lines of one record file, compressed or plain.

    The ``.zstd`` variant streams through the project's pinned ``zstandard``
    library — never a system ``zstd`` command, never a whole-record buffer —
    and a missing library surfaces as the read fault it is. Every read stops
    at its proportionate bound; a bound stop marks the record truncated
    instead of pretending the file ended cleanly.
    """

    def __init__(self, path: Path):
        self.path = path
        self.truncated = False

    def lines(self) -> Iterator[bytes]:
        source = self._decompressed if self.path.name.endswith(".zstd") else self._plain
        pending = b""
        total = 0
        try:
            for chunk in source():
                total += len(chunk)
                if total > _MAX_RECORD_TOTAL_BYTES:
                    self.truncated = True
                    return
                pending += chunk
                while True:
                    index = pending.find(b"\n")
                    if index < 0:
                        break
                    line, pending = pending[:index], pending[index + 1:]
                    if len(line) > _MAX_RECORD_LINE_BYTES:
                        self.truncated = True
                        continue
                    yield line
                if len(pending) > _MAX_RECORD_LINE_BYTES:
                    self.truncated = True
                    pending = b""
            if pending.strip():
                yield pending
        except Exception:  # noqa: BLE001 - an unreadable record is a partial fact, never a run failure
            self.truncated = True

    def _plain(self) -> Iterator[bytes]:
        with self.path.open("rb") as handle:
            while chunk := handle.read(262144):
                yield chunk

    def _decompressed(self) -> Iterator[bytes]:
        import zstandard
        with self.path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            while chunk := reader.read(262144):
                yield chunk


class _RecordAccumulator:
    """The attempt-wide fold of this run's matched session records.

    The projection rules are the existing usage observer's, applied to the
    private record instead of the in-process events: per-field sums over
    non-negative safe integers, a field a contributing record lacked is
    omitted instead of read as zero, cached input derives from the record's
    own counters, and only a seen, completed turn end with no missing usage
    and an untruncated read is complete. Everything else stays partial.
    """

    def __init__(self) -> None:
        self.sums: dict[str, int] = {field: 0 for field in _RECORD_USAGE_FIELDS}
        self.missed: set[str] = set()
        self.records = 0
        self.missing_usage = False
        self.turn_end_seen = False
        self.turn_end_completed = False
        self.truncated = False
        self.failure: dict | None = None
        self.last_assistant: str | None = None
        self.last_assistant_id: str | None = None
        self.model: dict | None = None
        self.steps: list[dict] = []
        self.sessions: list[str] = []

    def complete_field(self, field: str) -> bool:
        return self.records > 0 and field not in self.missed

    def fold_usage(self, usage: object, step: dict) -> None:
        usable = False
        if isinstance(usage, dict):
            entry = dict(step)
            for field in _RECORD_USAGE_FIELDS:
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    self.sums[field] += value
                    entry[field] = value
                    usable = True
                else:
                    self.missed.add(field)
            if usable:
                self.records += 1
                if len(self.steps) < _MAX_RECORD_STEPS:
                    self.steps.append(entry)
                return
        self.missing_usage = True

    def cached_input(self) -> int | None:
        if all(self.complete_field(field) for field in
               ("totalTokens", "inputTokens", "outputTokens")):
            derived = self.sums["totalTokens"] - self.sums["inputTokens"] - self.sums["outputTokens"]
            if derived >= 0:
                return derived
        if self.complete_field("cacheReadTokens") and self.complete_field("cacheWriteTokens"):
            return self.sums["cacheReadTokens"] + self.sums["cacheWriteTokens"]
        return None

    def usage(self) -> dict | None:
        if not self.records:
            return None
        complete = (not self.missing_usage and self.turn_end_seen
                    and self.turn_end_completed and not self.truncated)
        projected: dict = {"source": "dsh/session-record", "inputBasis": "excludes-cached",
                           "nativeRecords": self.records,
                           "completeness": "complete" if complete else "partial"}
        for field in ("inputTokens", "outputTokens"):
            if self.complete_field(field):
                projected[field] = self.sums[field]
        cached = self.cached_input()
        if cached is not None:
            projected["cachedInputTokens"] = cached
        if self.complete_field("reasoningTokens"):
            projected["reasoningOutputTokens"] = self.sums["reasoningTokens"]
        return projected


def _record_candidates(root: Path) -> list[Path]:
    """The bounded, deterministic scan for record files under the sessions root."""
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > _MAX_RECORD_DEPTH or len(found) >= _MAX_RECORD_FILES:
            return
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            return
        for entry in ordered:
            if len(found) >= _MAX_RECORD_FILES:
                return
            try:
                if entry.is_file(follow_symlinks=False):
                    if entry.name.endswith(_RECORD_SUFFIXES):
                        found.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
            except OSError:
                continue

    walk(root, 0)
    return found


def _record_text(value: object) -> str:
    """The plain text blocks of a record message's content array, concatenated."""
    blocks = value if isinstance(value, list) else []
    return "".join(block["text"] for block in blocks
                   if isinstance(block, dict) and block.get("type") == "text"
                   and isinstance(block.get("text"), str))


def _fold_record(path: Path, session_ids: frozenset, acc: _RecordAccumulator) -> bool:
    """Fold one record file in; return whether its header named one of this run's sessions.

    The header's own session id is the only attribution: a foreign session's
    record is skipped, never folded into this attempt's facts. A malformed
    event line marks the read partial and the fold continues with the rest.
    """
    stream = _RecordStream(path)
    session = None
    for raw in stream.lines():
        try:
            event = decode_json(raw)
        except (ValueError, RecursionError):
            acc.truncated = True
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if session is None:
            if (kind != "session" or event.get("version") != _RECORD_VERSION
                    or not isinstance(event.get("id"), str)):
                continue
            if event["id"] not in session_ids:
                return False
            session = event["id"]
            acc.sessions.append(session)
            continue
        if kind == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            source = message.get("source") if isinstance(message.get("source"), dict) else {}
            acc.fold_usage(data.get("usage"), {
                "session": session,
                "turn": data.get("turn") if type(data.get("turn")) is int else None,
                "step": data.get("step") if type(data.get("step")) is int else None})
            if source.get("kind") == "model":
                text = _record_text(message.get("content"))
                if text.strip():
                    acc.last_assistant = text
                    acc.last_assistant_id = message.get("id") if isinstance(message.get("id"), str) else None
                if isinstance(source.get("provider"), str) and isinstance(source.get("model"), str):
                    acc.model = {"provider": source["provider"], "model": source["model"]}
        elif kind == "turn/end":
            reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
            # The last turn end is this attempt's terminal reason.
            acc.turn_end_seen = True
            acc.turn_end_completed = reason.get("kind") == "completed"
            if reason.get("kind") == "error":
                error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
                code = error.get("code")
                # Only the machine classification: a provider message can carry
                # credentials and is never copied anywhere.
                acc.failure = {"kind": "error",
                               "code": code if type(code) is str and 0 < len(code) <= 64 else None}
    if stream.truncated:
        acc.truncated = True
    return session is not None


def session_record_facts(dsh_home: Path, session_ids) -> dict | None:
    """This run's private session-record facts, or ``None`` when none matched.

    The record is the optional source of the observed model identity, the
    per-step usage and the retained root assistant text. Every failure mode —
    a missing directory, a missing decompression library, a foreign or
    malformed record, a bound stop — keeps the known facts standing, reports
    the partial marker, and never raises into the run; an absent record is an
    unknown, never a zero.
    """
    sessions = frozenset(s for s in session_ids if isinstance(s, str) and s)
    root = Path(dsh_home) / _SESSIONS_DIRNAME
    if not sessions or not root.is_dir():
        return None
    acc = _RecordAccumulator()
    try:
        candidates = _record_candidates(root)
    except OSError:
        return None
    try:
        for path in candidates:
            _fold_record(path, sessions, acc)
    except OSError:
        acc.truncated = True
    usage = acc.usage()
    if usage is None and acc.model is None and acc.last_assistant is None and acc.failure is None:
        return None
    return {"usage": usage, "model": acc.model, "lastAssistant": acc.last_assistant,
            "lastAssistantSourceId": acc.last_assistant_id,
            "failure": acc.failure, "steps": acc.steps, "sessions": acc.sessions,
            "recordsRead": len(candidates), "truncated": acc.truncated}


# -- the one run -------------------------------------------------------------------


def _fold_update(message: object, state: _RunState, *, facts: _RunFacts, tools: DshToolFacts,
                 activity: DshActivity, evidence: RootTurnEvidence | None,
                 inquiry_bridge: InquiryBridge | None = None) -> bool:
    """Fold one native notification in; return whether a new retained fact appeared.

    Projection precedes every judgment: the tool facts, the unknown-event
    classification and the bridge's bounded live view run before the governed
    evidence, so a foreign or malformed frame still lands in the retained facts.
    Nothing here fails the run; a verified receipt inside the evidence advances
    the journal through the evidence's own callbacks.
    """
    if not isinstance(message, dict):
        facts.note_unknown("non-object-frame")
        return True
    method = message.get("method")
    params = message.get("params")
    if method != "session/update" or not isinstance(params, dict):
        facts.note_unknown(method if isinstance(method, str) else "unknown-method")
        return True
    update = params.get("update")
    kind = update.get("sessionUpdate") if isinstance(update, dict) else None
    if kind not in UPDATE_KINDS:
        facts.note_unknown(kind if isinstance(kind, str) else "unknown-event")
    tools.observe(message)
    if evidence is not None:
        evidence.observe(message, state.event_count)
    if kind in MODEL_ACTIVITY_KINDS and not state.model_started:
        state.model_started = True
    activity.note(message, state.event_count)
    if inquiry_bridge is not None:
        inquiry_bridge.note_event(message, activity.phase)
    return True


def run(request: RunRequest, *, observer: Callable[[Mapping[str, Any]], RunFeedback],
        services: Any, cancelled: Callable[[], bool]) -> RunResult:
    """Run one native DSH execution: the single native path of this harness."""
    if request.harness != "dsh":
        raise BoardError("INVALID_ARGUMENT", "this run module drives dsh", harness=request.harness)
    if services is not None and not isinstance(services, SessionServices):
        raise BoardError("INVALID_ARGUMENT", "dsh accepts its own narrow session service binding")
    governed = services is not None
    if not governed and request.session_services:
        raise BoardError("INVALID_ARGUMENT",
                         "the request describes session services but this run mounts none")
    # Native resume stays unwired (native_resume=false, an unverified optional
    # capability): a continuation always rebuilds a new session, and a request
    # that names the exact previous native session is refused explicitly.
    if request.continuation is not None and request.continuation.mode != "reconstructed-new-session":
        raise BoardError("INVALID_ARGUMENT",
                         "dsh runs no native-session resume; only the reconstruction continuation is accepted")
    if governed:
        _check_service_descriptions(request, services.mount)
    deadline = execution_deadline(request.budget.timeout_seconds)
    cancel = CancelFlag(cancelled)
    invocation_root = ensure_private_dir(Path(request.private_state.invocation_root))
    native_root = ensure_private_dir(Path(request.private_state.native_root))
    dsh_home = ensure_private_dir(native_root / "dsh-home")
    state = _RunState()
    facts = _RunFacts()
    tools = DshToolFacts({"adapter": "dsh", "taskId": request.identity.task_id,
                          "attemptId": request.identity.attempt_id,
                          "generation": request.identity.generation})
    activity = DshActivity()
    # The cooperative inquiry bridge lives in this process because only it holds
    # the native connection; it queues questions for the root's own checkpoint
    # tool and journals delivered/answered state after the verified receipt.
    inquiry_bridge: InquiryBridge | None = None
    if governed and services.inquiry is not None:
        inquiry_bridge = make_inquiry_bridge(
            identity={"taskId": request.identity.task_id,
                                        "attemptId": request.identity.attempt_id,
                                        "generation": request.identity.generation,
                                        "turnId": request.identity.turn_id},
            journal_path=str(services.inquiry.get("resultsPath") or ""),
            attention_path=services.mount.bridge.get("attentionPath"), live=services.live)
        inquiry_bridge.start()
    client: AcpClient | None = None
    sessions: list[str] = []
    evidence: RootTurnEvidence | None = None
    raw_answer: str | None = None
    correction_count = 0
    drained: bool | None = None
    pump: _Pump | None = None

    def denied_count() -> int:
        if client is None:
            return 0
        connection_facts = client.facts()
        return len(connection_facts.get("deniedInteractions") or []) \
            + len(connection_facts.get("permissionDecisions") or [])

    def notify(*, settled: bool = False, raw: str | None = None) -> RunFeedback:
        feedback = observer(facts.mapping(tool_calls=tools.tool_calls, settled=settled,
                                          raw_answer=raw, denied=denied_count()))
        if not isinstance(feedback, RunFeedback):
            raise BoardError("INVALID_ARGUMENT", "the observer must answer with one RunFeedback")
        if feedback.action == "correct" and not settled:
            raise BoardError("INVALID_ARGUMENT", "a correction is only answerable at a settled run")
        if feedback.action == "stop":
            raise ObserverInterrupt()
        return feedback

    def fold(message: object) -> bool:
        state.event_count += 1
        changed = _fold_update(message, state, facts=facts, tools=tools, activity=activity,
                               evidence=evidence, inquiry_bridge=inquiry_bridge)
        if pump is not None and pump.overflow:
            raise NativeError("invalid-protocol", "the native update queue overflowed; frames were dropped")
        return changed

    def send_cancel(session: str | None) -> None:
        if client is None or session is None or state.interrupt_requested:
            return
        state.interrupt_requested = True
        state.interrupt_basis = ("observer-request" if state.observer_stopped
                                 else "deadline-budget" if state.deadline_hit
                                 else "session-cancel-notification")
        try:
            client.cancel(session, timeout=10.0)
        except (AcpTimeout, AcpConnectionClosed, BoardError):
            pass  # the stop collection still owns the group; the cancel is a transport fact

    try:
        scope_rows, extra_env = tool_scope_launch(request.tool_scope)
        policy = (_AttentionPolicy(services.mount.bridge, state, inquiry_bridge)
                  if governed else PermissionPolicy())
        client = _launch_agent(cwd=request.cwd, native_root=native_root,
                               invocation_root=invocation_root, dsh_home=dsh_home,
                               scope_rows=scope_rows, extra_env=extra_env,
                               permission_policy=policy)
        state.harness_version = _initialize(client)
        pump = _Pump(client.connection)
        if governed:
            mount = services.mount
            evidence = RootTurnEvidence(
                "", mount.finish_tool, mount.bridge,
                checkpoint_name=mount.checkpoint_tool, answer_name=mount.answer_tool,
                on_delivery=(lambda receipt, call_id: inquiry_bridge.deliver_inquiries(receipt, call_id))
                if inquiry_bridge is not None else None,
                on_answer=(lambda receipt, call_id: inquiry_bridge.record_answer(receipt, call_id))
                if inquiry_bridge is not None else None,
                validate_outcome=services.validate_outcome, mounted_tools=mount.bare_tools)
            _governed_round(client=client, pump=pump, request=request, services=services,
                            deadline=deadline, cancel=cancel, state=state, tools=tools,
                            activity=activity, sessions=sessions, evidence=evidence,
                            inquiry_bridge=inquiry_bridge,
                            fold=fold, notify=notify,
                            denied=denied_count, send_cancel=send_cancel)
            state.status = "ok"
            notify(settled=True)
        else:
            raw_answer, correction_count = _final_message_rounds(
                client=client, pump=pump, request=request, deadline=deadline, cancel=cancel,
                state=state, facts=facts, tools=tools, activity=activity, sessions=sessions,
                fold=fold, notify=notify, denied=denied_count, send_cancel=send_cancel)
            state.status = "ok"
    except ObserverInterrupt:
        state.status = "cancelled"
        state.reason = "observer-interrupt"
        state.observer_stopped = True
        send_cancel(sessions[-1] if sessions else None)
    except NativeError as error:
        state.spawn_failure = getattr(error, "spawn_evidence", None)
        state.status = "cancelled" if error.code in ("cancelled", "native-cancelled") else "error"
        state.reason = error.code
        state.error_text = str(error)[:512]
        send_cancel(sessions[-1] if sessions else None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        state.status = "error"
        state.reason = "invalid-native-result"
        state.error_text = "the native execution returned invalid or incomplete data"
    finally:
        drained = _collect(client=client, pump=pump, state=state, tools=tools, sessions=sessions,
                           deadline=deadline, cancel=cancel, fold=fold, notify=notify)
        if inquiry_bridge is not None:
            # Closing before the process disappears keeps an after-end question
            # honest instead of leaving a dangling observation; the reports are
            # retained as evidence references whatever the run's end was.
            inquiry_bridge.close()
            state.inquiry = inquiry_bridge.report()
            state.attention = inquiry_bridge.attention_report()
    record = session_record_facts(dsh_home, sessions) if sessions else None
    return _build_result(request, state=state, tools=tools, activity=activity, client=client,
                         sessions=sessions, evidence=evidence, raw_answer=raw_answer,
                         correction_count=correction_count, drained=drained,
                         invocation_root=invocation_root, record=record,
                         stderr_mirror=services.native_stderr if governed else None)


# -- the session/prompt stages shared by both carriers -------------------------------


def _open_session(*, client: AcpClient, request: RunRequest, servers: list,
                  state: _RunState, tools: DshToolFacts, activity: DshActivity,
                  sessions: list) -> tuple[str, dict]:
    """The one session-open site: a fresh root session with this run's mounts."""
    snapshot = _native_call(lambda: client.new_session(request.cwd, mcp_servers=servers,
                                                       timeout=_SESSION_TIMEOUT),
                            code="session-open-failed", message="the native session could not be created")
    session_id = snapshot.get("sessionId") if isinstance(snapshot, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise NativeError("wrong-native-session", "the native agent did not open a root session")
    state.session_opened = True
    sessions.append(session_id)
    tools.add_root(session_id)
    activity.session_id = session_id
    bound = getattr(state, "round_evidence", None)
    if bound is not None:
        bound.session_id = session_id
    return session_id, snapshot


def _prompt_and_observe(*, client: AcpClient, pump: _Pump, session_id: str, input_text: str,
                        deadline: float, cancel: CancelFlag, state: _RunState,
                        fold, notify, activity: DshActivity, denied: Callable[[], int],
                        send_cancel) -> dict:
    """The one prompt/observe site: send once, fold updates, honor stop feedback.

    The prompt runs on its own thread because the ACP response only arrives at
    the turn's end; the run loop owns every update, the cancel flag, the overall
    deadline and the role's feedback while it streams.
    """
    activity.note_prompt()
    result: dict = {}

    def send() -> None:
        try:
            result["value"] = client.prompt(session_id, input_text, timeout=_remaining(deadline))
        except BaseException as error:  # noqa: BLE001 - the loop maps every failure below
            result["error"] = error

    thread = threading.Thread(target=send, daemon=True, name="dsh-acp-prompt")
    thread.start()
    interrupted = False
    interrupted_at: float | None = None
    denied_seen = -1

    while thread.is_alive() or not pump.empty():
        if not interrupted and (cancel.is_set() or _remaining(deadline) <= 0):
            if _remaining(deadline) <= 0:
                state.deadline_hit = True
            send_cancel(session_id)
            interrupted, interrupted_at = True, time.monotonic()
        elif interrupted and thread.is_alive() and time.monotonic() - interrupted_at > _ABANDON_SECONDS:
            # The agent never answered the interrupt: abandon the prompt and let
            # the stop collection end the owned group; no result may come of it.
            raise NativeError("cancelled" if cancel.is_set() else "deadline",
                              "the native prompt did not answer after the interrupt")
        try:
            message = pump.get(timeout=0.05)
        except queue.Empty:
            message = None
        if message is not None and fold(message) or denied() != denied_seen:
            denied_seen = denied()
            notify()
    thread.join(timeout=5.0)
    if "error" in result:
        error = result["error"]
        if isinstance(error, AcpConnectionClosed):
            raise NativeError("native-disconnected", "the native agent connection ended during the prompt")
        if isinstance(error, AcpTimeout):
            if state.deadline_hit or _remaining(deadline) <= 0:
                state.deadline_hit = True
                raise NativeError("deadline", "the run's overall deadline expired during the prompt")
            if cancel.is_set():
                raise NativeError("cancelled", "the owned DSH execution was cancelled")
            raise NativeError("invalid-protocol", "the native prompt exceeded its budget without a deadline")
        raise error
    value = result.get("value")
    return value if isinstance(value, dict) else {}


def _settle_stop_reason(state: _RunState, stop_reason: object) -> str | None:
    """Map the native stop reason to the run's end; ``None`` means success."""
    if not isinstance(stop_reason, str) or not stop_reason:
        raise NativeError("invalid-protocol", "the native prompt returned no stop reason")
    state.prompt_stop_reason = stop_reason
    if stop_reason == "end_turn":
        # The native stop reason is itself the model turn's end fact; turn
        # Earlier native activity may already have confirmed this fact.
        if not state.model_started:
            state.model_started = True
        return None
    if stop_reason == "cancelled":
        return "native-cancelled"
    return _END_REASONS.get(stop_reason, "native-stop")


def _close_session(client: AcpClient, session_id: str) -> None:
    """The one close site; an unacknowledged close always fails the run."""
    try:
        client.close_session(session_id, timeout=_CLOSE_TIMEOUT)
    except (AcpRequestError, AcpTimeout) as error:
        raise NativeError("session-close-unconfirmed",
                          "the native session close was not acknowledged") from error


# -- the two carriers ---------------------------------------------------------------


def _final_message_rounds(*, client: AcpClient, pump: _Pump, request: RunRequest,
                          deadline: float, cancel: CancelFlag, state: _RunState,
                          facts: _RunFacts, tools: DshToolFacts, activity: DshActivity,
                          sessions: list, fold, notify, denied, send_cancel) -> tuple[str | None, int]:
    """The final-message carrier: one fresh root session per executed correction.

    The scope only decides the native tool settings; the value carrier follows
    the run's own service binding alone. Each round takes its answer from the
    turn's message chunks and offers the settled fact to the role observer,
    whose correction — how often is the role's rule — opens the next session on
    the same process and the same total deadline.
    """
    input_text = request.input_text
    correction_count = 0
    while True:
        session_id, snapshot = _open_session(client=client, request=request, servers=[],
                                             state=state, tools=tools, activity=activity,
                                             sessions=sessions)
        state.configured = True
        state.checked = _configure(client, session_id, snapshot, request.configuration)
        raw_parts: list[str] = []
        raw_bytes = 0

        def chunk_fold(message: object) -> bool:
            nonlocal raw_bytes
            params = message.get("params") if isinstance(message, dict) else None
            # Only this round's own session/new root contributes the final
            # text: a foreign or earlier round's chunk is isolated as an
            # unknown-origin fact and is never this round's answer.
            if isinstance(params, dict) and params.get("sessionId") != session_id:
                update = params.get("update")
                if isinstance(update, dict) and update.get("sessionUpdate") == "agent_message_chunk":
                    facts.note_unknown("foreign-root-text")
                return fold(message)
            update = params.get("update") if isinstance(params, dict) else None
            if isinstance(update, dict) and update.get("sessionUpdate") == "agent_message_chunk":
                for text in text_blocks(update.get("content")):
                    encoded = text.encode()
                    if raw_bytes + len(encoded) > _MAX_ANSWER_BYTES:
                        raise NativeError("answer-too-large", "no bounded native answer")
                    raw_bytes += len(encoded)
                    raw_parts.append(text)
            return fold(message)

        value = _prompt_and_observe(client=client, pump=pump, session_id=session_id,
                                    input_text=input_text, deadline=deadline, cancel=cancel,
                                    state=state, fold=chunk_fold, notify=notify, activity=activity,
                                    denied=denied, send_cancel=send_cancel)
        failure = _settle_stop_reason(state, value.get("stopReason"))
        if failure is not None:
            raise NativeError(failure, f"the native turn stopped with reason {state.prompt_stop_reason!r}")
        raw = "".join(raw_parts)
        tools.close_root(session_id)
        _close_session(client, session_id)
        feedback = notify(settled=True, raw=raw)
        if feedback.action == "correct":
            correction_count += 1
            input_text = feedback.input_text
            continue
        return raw, correction_count


def _governed_round(*, client: AcpClient, pump: _Pump, request: RunRequest,
                    services: SessionServices, deadline: float, cancel: CancelFlag,
                    state: _RunState, tools: DshToolFacts, activity: DshActivity,
                    sessions: list, evidence: RootTurnEvidence,
                    inquiry_bridge: InquiryBridge | None, fold, notify,
                    denied, send_cancel) -> None:
    """The governed completion-tool carrier: one root turn, one verified receipt.

    The same session-open, configuration and prompt sites run with the mounted
    session service; the value is the signed receipt's outcome and nothing else.
    A failed or refused finish call keeps the turn alive for the root's own
    corrected retry, exactly like the other carrier of this seam. The inquiry
    bridge activates with the root session and closes at settlement: an idle or
    finished agent is never woken for an inquiry.
    """
    mount = services.mount
    state.round_evidence = evidence
    session_id, snapshot = _open_session(client=client, request=request,
                                         servers=list(mount.mcp_servers),
                                         state=state, tools=tools, activity=activity,
                                         sessions=sessions)
    if inquiry_bridge is not None:
        inquiry_bridge.activate(session_id)
    state.configured = True
    state.checked = _configure(client, session_id, snapshot, request.configuration)
    publisher = ActivityPublisher(services.live.publish_activity if services.live else None)

    def governed_fold(message: object) -> bool:
        changed = fold(message)
        if changed:
            try:
                publisher.publish(activity.payload())
            except BoardError:
                pass  # metadata must never fail the native turn
        return changed

    value = _prompt_and_observe(client=client, pump=pump, session_id=session_id,
                                input_text=request.input_text, deadline=deadline, cancel=cancel,
                                state=state, fold=governed_fold, notify=notify, activity=activity,
                                denied=denied, send_cancel=send_cancel)
    if value.get("stopReason") == "cancelled":
        # A cancelled turn owes no finish receipt; the reason, not a missing
        # tool, is what the turn's end means.
        raise NativeError("native-cancelled", "the native root turn stopped cancelled")
    failure = _settle_stop_reason(state, value.get("stopReason"))
    if failure is not None:
        raise NativeError(failure, f"the native turn stopped with reason {state.prompt_stop_reason!r}")
    evidence.settle(state.event_count, value.get("stopReason"))
    if inquiry_bridge is not None:
        # Stop accepting questions the instant the root turn settled: an idle
        # or finished agent is never woken for an inquiry.
        inquiry_bridge.close()
    activity.phase = "finishing"
    try:
        publisher.publish(activity.payload())
    except BoardError:
        pass
    _close_session(client, session_id)
    # The close was acknowledged: the accepted finish's ordered native evidence
    # is complete and is retained for the role's turn record.
    state.provenance = evidence.provenance(close_ordinal=state.event_count,
                                           event_count=state.event_count)


# -- collection, stop facts and the result -------------------------------------------


def _collect(*, client: AcpClient | None, pump: _Pump | None, state: _RunState,
             tools: DshToolFacts, sessions: list, deadline: float, cancel: CancelFlag,
             fold, notify) -> bool | None:
    """Stop the owned group and read every late frame; unknown stays unknown.

    The EOF drain keeps every stopped-stream frame: each late fact reaches the
    role observer, and only the observer's stop — never the driver's scope
    reading — marks the stream incomplete. An unconfirmed stop escalates once to
    a group termination and reports what was actually observed; it never reads
    as gone.
    """
    if client is None:
        return None
    if pump is not None and pump.overflow:
        state.update_overflow = True
    try:
        shutdown = client.shutdown(drain_seconds=_DRAIN_SECONDS, settle_seconds=_SETTLE_SECONDS)
    except BaseException:
        shutdown = stop_evidence(client.handle)
    drained_complete = _drain_late(pump, state, fold, notify)
    if not shutdown.get("shutdownConfirmed"):
        client.handle.terminate(grace_seconds=3.0)
        state.signalled = True
        shutdown = stop_evidence(client.handle)
        drained_complete = _drain_late(pump, state, fold, notify) and drained_complete
    if cancel.is_set() and state.status == "ok":
        state.status = "cancelled"
        state.reason = "cancelled"
        state.error_text = "the owned DSH execution was cancelled"
    state.stop = {
        "shutdown": bool(shutdown.get("shutdownConfirmed")),
        "group_observed": shutdown.get("groupObserved"),
        "leader_exited": shutdown.get("leaderExited"),
        "exit_code": shutdown.get("leaderExitCode"),
    }
    return drained_complete


def _drain_late(pump: _Pump | None, state: _RunState, fold, notify) -> bool:
    """Fold every buffered frame after EOF and hand each new fact to the role.

    The role observer still rules on late facts: a fact that reaches it during
    this tail is judged like any other, so a no-tool role can still refuse the
    run — its stop here cancels the run (the native turn already ended) and
    marks the stream incomplete. A fold failure keeps the retained facts but
    leaves the stream unproven; nothing in this tail can drop a fact or the
    whole result.
    """
    if pump is None:
        return False
    complete = True
    role_stopped = False
    for message in pump.drain():
        try:
            changed = fold(message)
        except NativeError:
            complete = False
            continue
        except BoardError:
            complete = False
            continue
        if changed and not role_stopped:
            try:
                notify()
            except ObserverInterrupt:
                complete = False
                role_stopped = True
                state.observer_stopped = True
                if state.status == "ok":
                    state.status = "cancelled"
                    state.reason = "observer-interrupt"
            except BoardError:
                complete = False
    if pump.overflow:
        return False
    return complete


_json_package = shape_guard(OptionalFrozenJsonAt(MAX_SCHEMA_BYTES))
_dsh_tool_package = shape_guard(ToolEvidencePackage)
_usage_package = shape_guard(UsagePackage)
_quota_package = shape_guard(NativeFailurePackage)
_message_package = shape_guard(LastAssistantMessagePackage)
_activity_package = shape_guard(ActivityPackage)


def _build_result(request: RunRequest, *, state: _RunState,
                  tools: DshToolFacts, activity: DshActivity, client: AcpClient | None, sessions: list,
                  evidence: RootTurnEvidence | None, raw_answer: str | None,
                  correction_count: int, drained: bool | None,
                  invocation_root: Path, record: dict | None = None,
                  stderr_mirror: str | None = None) -> RunResult:
    """Assemble the factual result; every field reports what actually happened."""
    stop = state.stop
    session_id = sessions[-1] if sessions else None
    identity_fields: dict = {}
    if session_id:
        identity_fields["session_id"] = session_id
    native_identity = identity_or_none(identity_fields) if identity_fields else None
    if state.status == "ok" and sessions and not stop.get("shutdown"):
        state.status = "error"
        state.reason = "native-shutdown-failed"
        state.error_text = "the native agent did not exit with a confirmed group shutdown"
    if not sessions:
        # No root session was ever opened — three honest shapes, and a missing
        # session never means a missing process: a held client's own group
        # observation, the launch wrapper's finalize evidence when its
        # bookkeeping failed after the child existed, or the one gone whose
        # spawn truly never happened.
        if client is not None:
            observed = stop.get("group_observed")
            group_state = "gone" if stop.get("shutdown") else ("alive" if observed == "alive" else "unknown")
            stop_native = StopLayer(group_state=group_state)
        elif state.spawn_failure is not None:
            spawn = state.spawn_failure
            observed = spawn.get("groupObserved")
            group_state = ("gone" if spawn.get("shutdownConfirmed")
                           else "alive" if observed == "alive" else "unknown")
            stop_native = StopLayer(group_state=group_state)
        else:
            stop_native = StopLayer(group_state="gone")
    else:
        observed = stop.get("group_observed")
        group_state = "gone" if stop.get("shutdown") else ("alive" if observed == "alive" else "unknown")
        stop_native = StopLayer(group_state=group_state)
    interrupt = InterruptEvidence(
        requested=True if state.interrupt_requested else None,
        basis=state.interrupt_basis)
    checked_config = CheckedConfiguration()
    if state.configured and state.checked is not None:
        checked = state.checked
        checked_config = CheckedConfiguration(
            provider=CheckedValue(value=checked["provider"]),
            model=CheckedValue(value=checked["model"]),
            effort=CheckedValue(value=checked["effort"]))
    # The stream is complete on its own evidence: EOF read to the end without a
    # dropped frame or an observer cut, and the opened root turn reached its
    # native end. The business verdict and the group stop are separate facts
    # and never stand in for stream evidence.
    stream_complete = bool(drained is True and sessions and state.prompt_stop_reason)
    exclude = evidence.verified_delivery() if evidence is not None else frozenset()
    tool_package = tools.finish(stream_complete, exclude_calls=exclude) if state.session_opened else None
    value, completion = _value_and_completion(evidence, raw_answer,
                                              correction_count, stream_complete)
    record_failure = record.get("failure") if record else None
    return RunResult(
        identity=request.identity, harness="dsh",
        end=RunEnd(status=state.status, reason_code=state.reason,
                   native_exit_code=stop.get("exit_code"),
                   message=state.error_text),
        harness_version=state.harness_version,
        native_event_count=state.event_count or None,
        model_started=True if state.model_started else None,
        configuration=ResultConfiguration(
            requested=RunConfiguration(provider=request.configuration.provider,
                                       model=request.configuration.model,
                                       effort=request.configuration.effort),
            checked=checked_config),
        native_identity=native_identity,
        value=value, completion_evidence=completion,
        tool_evidence=_dsh_tool_package(tool_package),
        effective_policy=_effective_policy(request, state),
        usage=_usage_package(record.get("usage")) if record else None,
        native_failure=(_quota_package({"nativeCode": record_failure["code"],
                                        "source": "dsh/session-turn-end"})
                        if record_failure and record_failure.get("code") else None),
        last_assistant_message=(_message_package({"text": record["lastAssistant"],
                                                  "sourceId": record.get("lastAssistantSourceId")})
                                if record and record.get("lastAssistant") else None),
        activity=(_activity_package(activity.payload())
                  if state.event_count or activity.counts["modelTurns"] else None),
        continuation=None,
        stop_evidence=StopEvidence(native=stop_native, interrupt=interrupt),
        evidence_refs=_evidence_refs(client, invocation_root, state,
                                     record=record, stderr_mirror=stderr_mirror))


def _value_and_completion(evidence: RootTurnEvidence | None,
                          raw_answer: str | None, correction_count: int,
                          stream_complete: bool) -> tuple:
    """The final value and its completion evidence, per the bound carrier."""
    if evidence is not None:
        if evidence.receipt is None:
            return None, None
        completion = CompletionEvidence(stream_end=stream_complete)
        return (RunValue(schema_status="valid",
                         parsed=_json_package(evidence.receipt["outcome"]),
                         correction_count=0),
                completion)
    if raw_answer is None:
        return None, None
    completion = CompletionEvidence(stream_end=stream_complete)
    return (RunValue(schema_status="unknown", raw=raw_answer,
                     correction_count=correction_count), completion)


def _effective_policy(request: RunRequest, state: _RunState) -> EffectivePolicy:
    """The scope's own launch configuration, reported as the fact it is."""
    if not state.session_opened:
        return EffectivePolicy()
    if request.tool_scope == "none":
        requested = {"disabledRows": [*NONE_SCOPE_DISABLED_ROWS, PLAN_MODE_ROW, *_ALWAYS_DISABLED_ROWS]}
        return EffectivePolicy(
            tools=PolicyFact(requested=_json_package(requested)))
    if request.tool_scope == "read":
        return EffectivePolicy(
            tools=PolicyFact(requested=_json_package({"permissionMode": READ_ONLY_MODE})))
    return EffectivePolicy(tools=PolicyFact())


def _evidence_refs(client: AcpClient | None, invocation_root: Path, state: _RunState,
                   record: dict | None = None,
                   stderr_mirror: str | None = None) -> tuple:
    """The run's retained private evidence: refusal records and protocol facts."""
    if client is None:
        return ()
    refs: list[EvidenceRef] = []
    connection_facts = client.facts()

    def retain(kind: str, name: str, value: dict) -> None:
        target = invocation_root / name
        private_json(target, value, exclusive=True)
        raw = target.read_bytes()
        refs.append(EvidenceRef(kind=kind, location=str(target), size_bytes=len(raw),
                                sha256=hashlib.sha256(raw).hexdigest()))

    if record is not None:
        # The optional record's own extracted facts: the observed model identity,
        # the per-step usage and the binding that matched them to this run. The
        # shared contract has no observed-configuration slot, so the model stays
        # this retained fact instead of a look-alike checked value.
        retain("dsh-session-record", "dsh-session-record.json", record)

    if state.provenance is not None:
        # The accepted finish's ordered native evidence; the role reads it back
        # through the size- and hash-verified reference to build its turn record.
        retain("turn-provenance", "native-provenance.json", state.provenance)
    if state.inquiry is not None:
        retain("inquiry-report", "inquiry-report.json", state.inquiry)
    if state.attention is not None:
        retain("attention-report", "attention-report.json", state.attention)

    if state.attention_error:
        # The refused upgrade reached the agent but its attention record could
        # not be written: the fact is retained so the run never loses it.
        retain("attention-record", "attention-record.json",
               {"error": state.attention_error, "ts": utc_now()})

    denied = connection_facts.get("deniedInteractions") or []
    decisions = connection_facts.get("permissionDecisions") or []
    if denied:
        retain("denied-interactions", "denied-interactions.json", {"records": list(denied)})
    if decisions:
        retain("permission-decisions", "permission-decisions.json", {"records": list(decisions)})
    protocol_facts = {key: connection_facts[key] for key in (
        "protocolFaults", "protocolFaultCount", "unmatchedResponses", "unmatchedResponseCount",
        "notificationCounts", "observationFailureCount", "writeFailureCount", "pendingWrites",
        "writeUncertain", "eof") if key in connection_facts}
    if protocol_facts:
        retain("acp-connection-facts", "acp-connection-facts.json", protocol_facts)
    frame_log = client.connection.frame_log
    if frame_log is not None and frame_log.path.is_file():
        raw = frame_log.path.read_bytes()
        refs.append(EvidenceRef(kind="acp-frame-log", location=str(frame_log.path),
                                size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    stderr = client.stderr_tail()
    if stderr:
        target = invocation_root / "native-stderr.log"
        try:
            fd = open_regular_fd(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(fd, stderr.encode("utf-8", errors="replace"))
            finally:
                os.close(fd)
        except BoardError:
            return tuple(refs)  # an unwritable tail is a lost optional extra, never a fault
        raw = target.read_bytes()
        refs.append(EvidenceRef(kind="native-stderr", location=str(target),
                                size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        if stderr_mirror:
            # The role's own copy of the same bounded tail; the retained
            # reference above stays the evidence either way.
            try:
                fd = open_regular_fd(Path(stderr_mirror),
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    os.write(fd, stderr.encode("utf-8", errors="replace"))
                finally:
                    os.close(fd)
            except (OSError, BoardError):
                pass
    return tuple(refs)


# -- discovery -------------------------------------------------------------------


def run_discovery(*, cwd: str, invocation_root: Path, native_root: Path,
                  timeout_seconds: int, cancelled: Callable[[], bool]) -> dict:
    """One no-prompt native catalog read over the same spawn and handshake.

    Discovery never sends a prompt, never mounts a session service and never
    configures a model: it creates one bare session, reads the declared model
    and effort selectors from the session's own configuration, and stops the
    owned process conservatively. Per-model effort availability and context
    windows are not exposed by this no-prompt surface and stay unknown facts.
    """
    deadline = execution_deadline(timeout_seconds)
    invocation_root = ensure_private_dir(Path(invocation_root))
    native_root = ensure_private_dir(Path(native_root))
    dsh_home = ensure_private_dir(native_root / "dsh-home")
    # Discovery carries the same uniform patch as every launch of this module —
    # the source binding, the private session-record root pin and the two
    # always-off rows — with no tool-scope rows: it never sends a prompt.
    client = _launch_agent(cwd=cwd, native_root=native_root, invocation_root=invocation_root,
                           dsh_home=dsh_home, scope_rows=[], extra_env={},
                           permission_policy=PermissionPolicy())
    catalog_value: dict | None = None
    error: NativeError | None = None
    try:
        version = _initialize(client) or "unknown"
        snapshot = _native_call(lambda: client.new_session(cwd, mcp_servers=[],
                                                           timeout=_SESSION_TIMEOUT),
                                code="session-open-failed", message="the discovery session failed")
        session_id = snapshot.get("sessionId") if isinstance(snapshot, dict) else None
        options = snapshot.get("configOptions") if isinstance(snapshot, dict) else None
        if not isinstance(session_id, str) or not session_id or not isinstance(options, list):
            raise NativeError("catalog-unavailable", "the native session exposed no configuration options")
        catalog_value = _catalog(options, version)
        try:
            client.close_session(session_id, timeout=_CLOSE_TIMEOUT)
        except (AcpRequestError, AcpTimeout) as close_error:
            raise NativeError("session-close-unconfirmed",
                              "the discovery session close was not acknowledged") from close_error
        except AcpConnectionClosed as close_error:
            raise NativeError("native-disconnected",
                              "the native connection ended during the discovery session close") from close_error
    except NativeError as caught:
        error = caught
    except BoardError as caught:
        error = NativeError("catalog-unavailable", str(caught.message)[:200])
    finally:
        shutdown = _stop(client)
    if error is not None:
        # The one stop fact this operation owns, measured from its own stop
        # collection, carried on the error for the outer layers: anything but
        # True is unconfirmed, unknown stays False, and a failure before any
        # process existed carries no stop fact at all. The shared controller's
        # verbatim publication of it is the remaining integration item.
        error.discovery_shutdown_confirmed = shutdown.get("shutdownConfirmed") is True
        raise error
    if not shutdown.get("shutdownConfirmed") or client.process.returncode != 0:
        failed = NativeError("native-shutdown-failed",
                             "the native agent did not exit normally with confirmed group shutdown")
        failed.discovery_shutdown_confirmed = shutdown.get("shutdownConfirmed") is True
        raise failed
    return catalog_value


def _stop(client: AcpClient) -> dict:
    """The discovery stop: escalate once on an unconfirmed group, never claim gone."""
    shutdown = client.shutdown(drain_seconds=_DRAIN_SECONDS, settle_seconds=_SETTLE_SECONDS)
    if not shutdown.get("shutdownConfirmed"):
        client.handle.terminate(grace_seconds=3.0)
        shutdown = stop_evidence(client.handle)
    try:
        client.process.stdout.close()
    except OSError:
        pass
    return shutdown


def _catalog(options: list, version: str) -> dict:
    """The declared selectors, projected into the catalog receipt shape."""
    providers: dict[str, dict] = {}
    efforts: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("id") == "reasoning_effort":
            efforts = [entry.get("value") for entry in (option.get("options") or [])
                       if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
        if option.get("id") != "model":
            continue
        for group in option.get("options") or []:
            if not isinstance(group, dict):
                continue
            for entry in group.get("options") or []:
                if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
                    continue
                try:
                    parsed = json.loads(entry["value"])
                except ValueError:
                    continue
                if not (isinstance(parsed, list) and len(parsed) == 2
                        and all(isinstance(item, str) and item for item in parsed)):
                    continue
                provider, model_id = parsed
                bucket = providers.setdefault(provider, {
                    "provider": provider, "displayName": provider, "adapter": "dsh", "models": []})
                if any(model["id"] == model_id for model in bucket["models"]):
                    continue
                bucket["models"].append({
                    "id": model_id, "name": entry.get("name") or model_id,
                    "efforts": [], "contextWindow": None, "available": True,
                    "unavailableReason": None, "inputModalities": []})
    for bucket in providers.values():
        for model in bucket["models"]:
            model["efforts"] = list(efforts)
    return {
        "source": "dsh-acp-session-config", "adapter": "dsh", "harnessVersion": version,
        "discoveredAt": utc_now(), "providers": list(providers.values()),
        "discoveries": [{"adapter": "dsh", "status": "complete"}],
        "warnings": [
            "The no-prompt ACP surface exposes the declared model and effort selectors only; "
            "per-model effort availability and context windows stay unknown until a real session.",
        ],
    }


__all__ = [
    "CHECKPOINT_INQUIRY_NOTE", "NONE_SCOPE_DISABLED_ROWS", "SessionServiceMount", "SessionServices",
    "check_preparation", "execution_deadline", "make_inquiry_bridge",
    "native_evidence", "prepare_services", "prepare_session_service", "run", "run_discovery",
    "session_facts", "session_record_facts", "tool_scope_launch", "validate_turn_provenance",
]
