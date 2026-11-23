"""The single static declaration of every harness's shared facts.

One harness's declaration gathers in one place the static facts that previously
lived as parallel per-topic tables and adapter literals: the capability surface
and permission levels, the input limit and the model-start visibility of the
native protocol, the inquiry delivery mode, the configuration-directory
variable, the native-resume family, session-storage ownership, and how session
tools ride a turn. The per-topic tables in
:mod:`hey_my_buddy.protocol.capabilities` and
:mod:`hey_my_buddy.protocol.live` derive from this mapping, and the adapters
and the blackboard read the resume and storage facts from it, so no second copy
can drift. Everything here is a harness capability written in code (ADR-023):
never a setting, a model budget or a health observation, and behavior that
genuinely differs by harness protocol keeps its own boundary in the run
modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

#: The fixed sentence every unknown input-limit declaration carries: no numeric
#: value was obtained for the channel, which is never a claim that none exists.
UNKNOWN_INPUT_LIMIT_NOTE = ("no numeric input limit was obtained for this channel; "
                            "unknown does not mean there is no limit")


@dataclass(frozen=True)
class InputLimit:
    """One harness's declared single-input limit, written in code (ADR-023).

    Like the permission levels this is a harness capability, not a model
    budget, a database setting or a health observation. A known limit states
    the maximum admitted size (a prompt exactly at it is admitted), the unit
    the harness itself counts in, the native input scope the check covers, the
    form versions the value was verified on, and the public source those
    numbers were read from. An unknown limit states explicitly that no value
    was obtained; it never reads as unlimited. The counting itself lives in
    :mod:`hey_my_buddy.protocol.input_limit`.
    """

    form: str
    scope: str
    source: str
    state: Literal["known", "unknown"]
    value: int | None = None
    unit: str | None = None
    versions: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def known(cls, *, form: str, scope: str, source: str, value: int, unit: str,
              versions: tuple[str, ...]) -> "InputLimit":
        return cls(form=form, scope=scope, source=source, state="known",
                   value=value, unit=unit, versions=versions)

    @classmethod
    def unknown(cls, *, form: str, scope: str, source: str,
                note: str = UNKNOWN_INPUT_LIMIT_NOTE) -> "InputLimit":
        return cls(form=form, scope=scope, source=source, state="unknown", note=note)

    def to_payload(self) -> dict:
        payload = {"state": self.state, "form": self.form,
                   "scope": self.scope, "source": self.source}
        if self.state == "known":
            return {**payload, "value": self.value, "unit": self.unit,
                    "versions": list(self.versions)}
        return {**payload, "note": self.note}


@dataclass(frozen=True)
class ModelStartUnseen:
    """One harness's declaration that its native protocol cannot show the
    model's start in real time, written in code (ADR-023).

    Like the input limits this is a harness capability, not a setting or a
    health observation. The Router start wait judges silence within the wait
    as a model that never began; on a protocol that cannot show the start as
    it happens that judgment is false, so the execution process must not apply
    the wait to a harness declared here and the harness's own stream idle
    timeout bounds the call instead. A harness without the declaration is not
    declared the opposite: it is simply not verified either way and keeps the
    configured wait.
    """

    form: str
    versions: tuple[str, ...]
    basis: str


#: How a harness's live inquiry question reaches the running turn: mid-turn
#: input, the session's cooperative checkpoint, or not at all.
DeliveryMode = Literal["realtime", "cooperative-checkpoint", "unsupported"]

#: Who durably owns a harness's native session storage: the harness's own user
#: store, this product's private micro-task state, or no native session at all.
StorageOwner = Literal["harness-user-store", "hey-my-buddy-micro-task", "none"]

#: The carrier a harness's Worker session tools ride: tools the harness mounts
#: dynamically during the turn, or a fixed stdio MCP server.
SessionToolCarrier = Literal["dynamic-tools", "stdio-mcp"]


@dataclass(frozen=True)
class HarnessDeclaration:
    """Every shared static fact one coded harness declares, in one place.

    The per-topic tables and the adapters derive their values from these
    fields; a fact that only one harness's protocol shape owns (the native
    checkpoint and session-record formats, account probes, catalog specifics,
    tool-event evidence) stays in its own module and is not declared here.
    """

    name: str
    #: The adapter capability surface without the derived ``permission:*``
    #: entries, which come from ``permission_levels``.
    capabilities: tuple[str, ...]
    permission_levels: Mapping[str, Literal["enforced", "convention"]]
    #: The version on which the ADR-023 clause 10 permission behavior was
    #: verified; ``None`` records missing verification, not unsupported
    #: permissions.
    permissions_verified_version: str | None
    #: Whether the harness can mount the read-only frozen snapshot query
    #: service for the Router and maintenance planes (ADR-035 decision 2).
    router_snapshot_query: bool
    input_limit: InputLimit
    model_start_unseen: ModelStartUnseen | None
    inquiry_delivery: DeliveryMode
    #: The environment variable that names the harness's own configuration
    #: directory, which every child-environment builder passes through.
    config_dir_env_var: str
    #: Whether a prior native session can resume at all.
    native_resume: bool
    #: Whether a proven-interrupted prior native session can resume.
    interrupted_resume: bool
    #: Whether a native resume may cross a change of permission level.
    native_resume_permission_change: bool
    #: Whether a correction continues the same native session (a new turn on
    #: the same thread, keeping the root context, so the correction input
    #: carries only the error locations) or starts a fresh one that
    #: reassembles the full role text, query entry and previous answer.
    same_session_corrections: bool
    #: Whether the harness exposes its model catalog for discovery.
    model_discovery: bool
    session_storage_owner: StorageOwner
    #: The resume reason a mismatch of the declared storage owner records;
    #: ``None`` when no storage ownership is required for a resume.
    storage_mismatch_resume_reason: str | None
    #: Whether the ``web`` permission level still admits stdio MCP session
    #: tools on the turn.
    web_allows_mcp: bool
    session_tool_carrier: SessionToolCarrier


#: The one declaration of each name in ``HARNESS_NAMES``, in that order.
HARNESS_DECLARATIONS: dict[str, HarnessDeclaration] = {
    "codex": HarnessDeclaration(
        name="codex",
        capabilities=("codex", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
        permission_levels={"none": "convention", "web": "enforced", "read": "enforced",
                           "write": "enforced", "full": "enforced"},
        permissions_verified_version=None,
        router_snapshot_query=True,
        # Codex CLI 0.164.0's app-server ``turn/start`` sums the turn's native
        # text input items in Unicode scalar values and rejects above 1,048,576
        # (public source of tag rust-v0.164.0, commit 6aaac694).
        input_limit=InputLimit.known(
            form="cli-app-server", scope="turn-start-text",
            source="codex-0.164.0-public-source",
            value=1_048_576, unit="unicode-scalars", versions=("0.164.0",)),
        model_start_unseen=None,
        inquiry_delivery="realtime",
        config_dir_env_var="CODEX_HOME",
        native_resume=True,
        interrupted_resume=True,
        native_resume_permission_change=True,
        same_session_corrections=True,
        model_discovery=True,
        session_storage_owner="harness-user-store",
        storage_mismatch_resume_reason="native-storage-changed",
        web_allows_mcp=True,
        session_tool_carrier="dynamic-tools",
    ),
    "claude": HarnessDeclaration(
        name="claude",
        capabilities=("claude", "inquiry", "workspace", "cancel", "artifacts", "deadline"),
        # Claude Code CLI 2.1.329 with `--tools "" --strict-mcp-config --mcp-config
        # <snapshot only> --allowedTools mcp__hey_my_buddy_snapshot__*` exposes exactly
        # StructuredOutput and the mounted snapshot tools — no ToolSearch, no built-in
        # tools, no user MCP — so `none` is enforced (ADR-035 decision 3, probes note
        # 2026-11-19).
        permission_levels={"none": "enforced", "web": "enforced", "read": "enforced",
                           "write": "enforced", "full": "enforced"},
        permissions_verified_version=None,
        router_snapshot_query=True,
        input_limit=InputLimit.unknown(
            form="cli-stream-json", scope="prompt-text", source="claude-code-cli-reference"),
        model_start_unseen=None,
        inquiry_delivery="realtime",
        config_dir_env_var="CLAUDE_CONFIG_DIR",
        native_resume=False,
        interrupted_resume=False,
        native_resume_permission_change=False,
        same_session_corrections=False,
        model_discovery=True,
        session_storage_owner="none",
        storage_mismatch_resume_reason=None,
        web_allows_mcp=False,
        session_tool_carrier="stdio-mcp",
    ),
    "zcode": HarnessDeclaration(
        name="zcode",
        capabilities=("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
        permission_levels={"none": "enforced", "read": "convention", "write": "convention",
                           "full": "enforced"},
        permissions_verified_version=None,
        router_snapshot_query=True,
        input_limit=InputLimit.unknown(
            form="cli-app-server", scope="session-send-text", source="zcode-cli-docs"),
        model_start_unseen=None,
        inquiry_delivery="realtime",
        config_dir_env_var="ZCODE_DATA_BASE_DIR",
        native_resume=True,
        interrupted_resume=False,
        native_resume_permission_change=True,
        same_session_corrections=False,
        model_discovery=True,
        session_storage_owner="hey-my-buddy-micro-task",
        storage_mismatch_resume_reason=None,
        web_allows_mcp=False,
        session_tool_carrier="stdio-mcp",
    ),
    "dsh": HarnessDeclaration(
        name="dsh",
        capabilities=("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
        permission_levels={"none": "enforced", "web": "enforced", "read": "enforced",
                           "write": "enforced", "full": "enforced"},
        permissions_verified_version="0.2.2-rc.1",
        router_snapshot_query=True,
        input_limit=InputLimit.unknown(
            form="cli-acp", scope="session-prompt-text", source="dsh-cli-help"),
        # DSH CLI 0.2.2-rc.1 (CLI form, ACP channel) forwards only events
        # already written to the session: one whole model step — reasoning,
        # message and tool calls — streams into the session first and only then
        # goes out block by block, with no handshake capability, settings entry
        # or environment switch that changes it and no earlier notification at
        # the turn's start; the harness's own stream idle timeout (no data at
        # all, 300 seconds by default) is what bounds a silent call.
        model_start_unseen=ModelStartUnseen(
            form="cli-acp", versions=("0.2.2-rc.1",),
            basis="the ACP server emits each block only after the model's whole step "
                  "is written to the session"),
        inquiry_delivery="cooperative-checkpoint",
        config_dir_env_var="DSH_HOME",
        native_resume=True,
        interrupted_resume=True,
        native_resume_permission_change=False,
        same_session_corrections=False,
        model_discovery=True,
        session_storage_owner="hey-my-buddy-micro-task",
        storage_mismatch_resume_reason="private-native-home-required",
        web_allows_mcp=True,
        session_tool_carrier="stdio-mcp",
    ),
}


def harness_declaration(name: str) -> HarnessDeclaration:
    """The one static declaration of a registered harness name."""
    return HARNESS_DECLARATIONS[name]
