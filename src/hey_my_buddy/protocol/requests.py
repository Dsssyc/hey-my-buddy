"""Pydantic requests for the supported CLI and its matching service operations.

The models describe caller input. The service authenticates and removes
transport credentials before parsing a request, then derives creation identity.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath
import re
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..errors import BoardError
from ..paths import is_absolute_path

MAX_TEXT_BYTES = 1024 * 1024
MAX_NOTE_BYTES = 10000
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: str) -> str:
    if not value.strip() or "\0" in value:
        raise ValueError("must be nonempty and contain no NUL")
    return value


def _bounded(value: str, maximum: int) -> str:
    _nonempty(value)
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"must be at most {maximum} UTF-8 bytes")
    return value


def _title(value: str) -> str:
    _nonempty(value)
    return " ".join(value.split())


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("must be an identifier of 1-128 letters, digits, '.', '_', ':' or '-'")
    return value


def _sha256(value: str) -> str:
    if not SHA256.fullmatch(value):
        raise ValueError("must be a lowercase SHA-256 digest")
    return value


def _cwd(value: str) -> str:
    if not is_absolute_path(value) or "\0" in value:
        raise ValueError("must be an absolute directory path")
    return value


def _absolute_path(value: str) -> str:
    if not is_absolute_path(value) or "\0" in value:
        raise ValueError("must be an absolute path")
    return value


def _relative_path(value: str) -> str:
    if not value or "\0" in value or (os.name == "nt" and ("\\" in value or ":" in value)):
        raise ValueError("must be a relative checkout path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." or part.lower() == ".git" for part in path.parts):
        raise ValueError("must stay inside the checkout and exclude .git")
    return value


def _buddy(value: str) -> str:
    _nonempty(value)
    return value


Title = Annotated[str, Field(min_length=1, max_length=200, description="Nonempty title, at most 200 characters"), AfterValidator(_title)]
Text = Annotated[str, Field(json_schema_extra={"x-maxUtf8Bytes": MAX_TEXT_BYTES}),
                 AfterValidator(lambda value: _bounded(value, MAX_TEXT_BYTES))]
Note = Annotated[str, Field(json_schema_extra={"x-maxUtf8Bytes": MAX_NOTE_BYTES}),
                 AfterValidator(lambda value: _bounded(value, MAX_NOTE_BYTES))]
RunId = Annotated[str, AfterValidator(_identifier)]
Digest = Annotated[str, AfterValidator(_sha256)]
Cwd = Annotated[str, AfterValidator(_cwd)]
AbsolutePath = Annotated[str, AfterValidator(_absolute_path)]
RelativePath = Annotated[str, AfterValidator(_relative_path)]
BuddyName = Annotated[str, AfterValidator(_buddy)]
Access = Literal["read", "write", "full"]
Checkout = Literal["worktree", "existing", "none"]
Harness = Literal["codex", "claude", "zcode", "dsh"]
ExpectedSeconds = Annotated[int, Field(strict=True, ge=1, le=86400)]
Paths = Annotated[list[RelativePath], Field(max_length=256)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rules: ClassVar[tuple[str, ...]] = ()
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null(cls, value):
        if isinstance(value, dict):
            for name in value:
                if name in cls.model_fields and value[name] is None and name not in cls.nullable_fields:
                    raise ValueError(f"{name} must be omitted when inapplicable; null is not a value")
        return value


class EmptyRequest(Request):
    """No parameters."""


class OutputRequest(Request):
    """CLI-only output projection."""
    output: Literal["brief", "full"] = "brief"


class CreateRequest(Request):
    """Create a macro task in the project containing cwd."""
    cwd: Cwd = Field(description="Absolute project directory; availability is checked when used")
    title: Title
    goal: Text = Field(description="Nonempty goal, at most 1 MiB UTF-8")


class AmendRequest(Request):
    """Append a macro task goal revision, or return the current identical goal."""
    macroTaskId: RunId
    goal: Text = Field(description="Nonempty goal, at most 1 MiB UTF-8")


class _Submission(Request):
    cwd: Cwd = Field(description="Existing absolute work directory")
    title: Title
    task: Text = Field(description="Nonempty task, at most 1 MiB UTF-8")
    buddy: BuddyName = Field(description="'route' or a complete recorded catalog buddyId")
    checkout: Checkout
    access: Access
    expectedSeconds: ExpectedSeconds
    inputs: Paths | None = Field(default=None, description="Explicit untracked inputs; required for Git checkouts, may be []")
    writeScope: Paths | None = Field(default=None, description="Nonempty authorized relative paths; ['.'] means entire checkout")
    accessNote: Note | None = Field(default=None, description="Required single-line reason for full access")
    fixes: RunId | None = None
    rules: ClassVar[tuple[str, ...]] = (
        "checkout worktree/existing requires inputs, including an explicit [] when none; checkout none forbids inputs and writeScope",
        "access read forbids writeScope; write/full on a Git checkout requires a nonempty writeScope",
        "access full requires a single-line accessNote; read/write forbid accessNote",
    )

    @model_validator(mode="after")
    def _combinations(self):
        supplied = self.model_fields_set
        git = self.checkout != "none"
        if git and ("inputs" not in supplied or self.inputs is None):
            raise ValueError("inputs is required for worktree/existing, and may be []")
        if not git and ("inputs" in supplied or "writeScope" in supplied):
            raise ValueError("inputs and writeScope are inapplicable with checkout none")
        if self.access == "read" and "writeScope" in supplied:
            raise ValueError("writeScope is inapplicable with access read")
        if git and self.access in ("write", "full") and not self.writeScope:
            raise ValueError("writeScope must be a nonempty list for write/full Git checkout")
        if self.access == "full":
            if self.accessNote is None or self.accessNote.splitlines() != [self.accessNote]:
                raise ValueError("accessNote must be a nonempty single-line reason for full access")
        elif "accessNote" in supplied:
            raise ValueError("accessNote is only applicable with access full")
        return self


class SubmitRequest(_Submission):
    """Submit a Host micro task under one macro task or as standalone."""
    macroTaskId: RunId | Literal["standalone"] = Field(description="Existing macro task id or explicit 'standalone'")
    delegationAuthority: Literal[True] | None = Field(default=None, description="Only when explicitly authorized; requires full access and a named buddy")
    rules: ClassVar[tuple[str, ...]] = _Submission.rules + (
        "delegationAuthority=true requires access full and a complete named buddy",
    )

    @model_validator(mode="after")
    def _delegation(self):
        if "delegationAuthority" in self.model_fields_set:
            if self.delegationAuthority is not True or self.access != "full" or self.buddy == "route":
                raise ValueError("delegationAuthority=true requires full access and a complete named buddy")
        return self


class LeadSubmitRequest(_Submission):
    """Lead-owned submission; macro task and owner come from the credential."""

    @model_validator(mode="after")
    def _explicit_buddy(self):
        if self.buddy == "route":
            raise ValueError("Lead submission requires a complete named buddy")
        return self


class AwaitRequest(Request):
    """Observe one existing micro task until a boundary, delivery, reminder or window end."""
    runId: RunId
    waitSeconds: Annotated[int, Field(strict=True, ge=1, le=86400)] = 86400


class RoutingHistoryRequest(Request):
    """Bounded routing history page for one run."""
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None
    before: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)] | None = None


class GetRequest(Request):
    """Read one run, macro task or project."""
    runId: RunId | None = None
    macroTaskId: RunId | None = None
    cwd: Cwd | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None
    before: Annotated[str, Field(min_length=1, max_length=512), AfterValidator(_nonempty)] | None = None
    includeAudit: bool | None = None
    routingHistory: RoutingHistoryRequest | None = None
    rules: ClassVar[tuple[str, ...]] = (
        "exactly one of runId, macroTaskId, cwd is required",
        "includeAudit and routingHistory apply only to a run; limit and before apply only to a collection",
    )

    @model_validator(mode="after")
    def _selector(self):
        supplied = self.model_fields_set
        if sum(name in supplied for name in ("runId", "macroTaskId", "cwd")) != 1:
            raise ValueError("exactly one of runId, macroTaskId or cwd is required")
        if "runId" in supplied and any(name in supplied for name in ("limit", "before")):
            raise ValueError("limit and before apply only to a collection")
        if "runId" not in supplied and any(name in supplied for name in ("includeAudit", "routingHistory")):
            raise ValueError("includeAudit and routingHistory apply only to runId")
        return self


class RoleGetRequest(Request):
    """Read only the credential's own run, or a Lead-owned run named by runId."""
    runId: RunId | None = None
    includeAudit: bool | None = None
    routingHistory: RoutingHistoryRequest | None = None


class InquireRequest(Request):
    """Observe, ask or read an existing inquiry on a micro task."""
    runId: RunId
    question: Annotated[str, Field(json_schema_extra={"x-maxUtf8Bytes": 4000}),
                        AfterValidator(lambda value: _bounded(value, 4000))] | None = None
    inquiryId: RunId | None = None
    timeoutMs: Annotated[int, Field(strict=True, ge=100, le=5000)] | None = None
    waitMs: Annotated[int, Field(strict=True, ge=0, le=30000)] | None = None
    rules: ClassVar[tuple[str, ...]] = ("question and inquiryId cannot be combined; omit both to observe",)

    @model_validator(mode="after")
    def _selection(self):
        if "question" in self.model_fields_set and "inquiryId" in self.model_fields_set:
            raise ValueError("question and inquiryId cannot be combined")
        return self


class HelperRequest(_Submission):
    """A complete authorized helper task packet."""


class ContinueRequest(Request):
    """Answer a boundary or continue from an exact artifact or turn."""
    runId: RunId
    targetRunId: RunId | None = None
    requestId: RunId | None = None
    artifactId: RunId | None = None
    turnId: RunId | None = None
    input: Text | None = None
    decision: Literal["approve", "decline"] | None = None
    helpers: Annotated[list[HelperRequest], Field(min_length=1, max_length=8)] | None = None
    autoContinue: bool | None = None
    helperPolicy: Literal["keep", "cancel"] | None = None
    buddy: BuddyName | None = None
    access: Access | None = None
    writeScope: Paths | None = None
    accessNote: Note | None = None
    resolution: Literal["restore", "adopt", "abandon"] | None = None
    observedFingerprint: Digest | None = None
    paths: Paths | None = None
    note: Note | None = None
    rules: ClassVar[tuple[str, ...]] = (
        "exactly one of requestId, artifactId or turnId identifies the answered fact",
        "artifactId/turnId continuation requires input; requestId may answer a boundary or resolve a workspace site",
        "helpers are only for an approved boundary and require autoContinue; decline forbids both",
        "resolution requires requestId and observedFingerprint; adopt/abandon require note and forbid paths",
        "access or writeScope changes require input; access read forbids writeScope; access full requires accessNote",
        "helperPolicy is required only while live helpers exist; the service checks that current fact",
    )

    @model_validator(mode="after")
    def _combination(self):
        supplied = self.model_fields_set
        if sum(name in supplied for name in ("requestId", "artifactId", "turnId")) != 1:
            raise ValueError("exactly one of requestId, artifactId or turnId is required")
        if ("artifactId" in supplied or "turnId" in supplied) and self.input is None:
            raise ValueError("input is required to continue an artifact or turn")
        if "requestId" in supplied and self.decision is None and self.resolution is None and self.input is None:
            raise ValueError("requestId continuation requires a decision, resolution or input")
        if self.decision is not None and "requestId" not in supplied:
            raise ValueError("decision requires requestId")
        if self.decision is not None and any(key in supplied for key in (
                "input", "helperPolicy", "buddy", "access", "writeScope", "accessNote", "resolution")):
            raise ValueError("a boundary decision takes only note and applicable helpers/autoContinue")
        if self.decision == "decline" and any(key in supplied for key in ("helpers", "autoContinue")):
            raise ValueError("decline forbids helpers and autoContinue")
        if "helpers" in supplied and (self.decision != "approve" or "autoContinue" not in supplied):
            raise ValueError("helpers require decision approve and explicit autoContinue")
        if "autoContinue" in supplied and "helpers" not in supplied:
            raise ValueError("autoContinue applies only to approved helpers")
        if self.resolution is not None:
            if "requestId" not in supplied or self.decision is not None or self.observedFingerprint is None:
                raise ValueError("resolution requires requestId and observedFingerprint, without decision")
            if self.resolution in ("adopt", "abandon") and (self.note is None or "paths" in supplied):
                raise ValueError("adopt/abandon require note and forbid paths")
        elif any(key in supplied for key in ("observedFingerprint", "paths")):
            raise ValueError("observedFingerprint and paths require resolution")
        if self.input is None and any(key in supplied for key in ("buddy", "helperPolicy")):
            raise ValueError("buddy and helperPolicy require continuation input")
        if any(key in supplied for key in ("access", "writeScope", "accessNote")) and self.input is None:
            raise ValueError("access or writeScope changes require input")
        if self.access == "read" and "writeScope" in supplied:
            raise ValueError("writeScope is inapplicable with access read")
        if self.access == "full":
            if self.accessNote is None or self.accessNote.splitlines() != [self.accessNote]:
                raise ValueError("access full requires a single-line accessNote")
        elif "accessNote" in supplied:
            raise ValueError("accessNote applies only to access full")
        if "writeScope" in supplied and not self.writeScope:
            raise ValueError("writeScope must be a nonempty list")
        return self


class IntegrationTarget(Request):
    path: AbsolutePath
    ref: Annotated[str, Field(min_length=1, max_length=512), AfterValidator(_nonempty)]


class AcceptRequest(Request):
    """Accept an exact delivered artifact against one explicit integration decision."""
    runId: RunId
    targetRunId: RunId | None = None
    artifactId: RunId
    note: Note
    target: IntegrationTarget | None = None
    notRequired: Note | None = None
    adjusted: bool | None = None
    hostPaths: Paths | None = None
    beforeCommit: RunId | None = None
    rules: ClassVar[tuple[str, ...]] = (
        "exactly one of target {path,ref} or nonempty notRequired is required",
        "notRequired forbids adjusted, hostPaths and beforeCommit",
        "target requires beforeCommit unless its tip merge proves the previous target from its parents",
        "checkout none can only use notRequired; the service checks the stored checkout",
    )

    @model_validator(mode="after")
    def _integration(self):
        supplied = self.model_fields_set
        if ("target" in supplied) == ("notRequired" in supplied):
            raise ValueError("exactly one of target or notRequired is required")
        if "notRequired" in supplied and any(key in supplied for key in ("adjusted", "hostPaths", "beforeCommit")):
            raise ValueError("notRequired forbids adjusted, hostPaths and beforeCommit")
        return self


class ConcludeRequest(Request):
    """Conclude a stopped failed, cancelled or delivered micro task."""
    rules: ClassVar[tuple[str, ...]] = (
        "artifactId answers the delivered or current stopped turn's fixed result; runId alone concludes when no result is being answered",
    )
    runId: RunId
    targetRunId: RunId | None = None
    artifactId: RunId | None = None
    note: Note


class CancelRequest(Request):
    """Request ordered cancellation of a micro task and its owned children."""
    runId: RunId
    targetRunId: RunId | None = None
    note: Note | None = None


class SuggestRequest(Request):
    """Worker or Lead suggestion on its credential-bound run."""
    runId: RunId | None = None
    body: Annotated[str, Field(json_schema_extra={"x-maxUtf8Bytes": 4000}),
                    AfterValidator(lambda value: _bounded(value, 4000))]


class LeadListRequest(Request):
    """Credential-scoped page of Lead-owned tasks."""
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None
    before: Annotated[str, Field(min_length=1, max_length=512), AfterValidator(_nonempty)] | None = None


class HarnessesRequest(Request):
    """Read current harness availability and capabilities."""
    includeUnavailable: bool | None = None
    refresh: bool | None = None
    harness: Harness | None = None


class HarnessSetRequest(Request):
    """Select one harness executable, or null to restore automatic detection."""
    harness: Harness
    path: AbsolutePath | None = Field(description="Absolute executable path, or null for automatic detection")
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"path"})


class QuotaRedetectRequest(Request):
    """Clear one provider cooling state with an explicit operation identity."""
    requestId: RunId
    harness: Harness
    provider: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{1,64}$")]


class RuntimeRequest(Request):
    """Inspect installed runtime identity and source leakage."""
    destination: AbsolutePath | None = None


class StorageApplyRequest(Request):
    """Apply the named storage plan after explicit confirmation."""
    planId: Annotated[str, Field(pattern=r"^stg-[0-9a-f]{32}$")]
    commandId: RunId
    confirm: Literal[True]


class ServiceControlRequest(Request):
    """Stop or restart the service while preserving truthful shutdown evidence."""
    drainSeconds: Annotated[int, Field(strict=True, ge=0, le=120)] | None = None
    note: Note | None = None


class ServiceControlWireRequest(ServiceControlRequest):
    action: Literal["stop", "restart", "status"]


class ConsoleRequest(Request):
    """Open, observe or close the browser console."""
    action: Literal["open", "status", "close"] = "open"
    browser: bool | None = Field(default=None, description="Local open choice; default true")
    wait: bool | None = Field(default=None, description="Local open choice; default false")
    expectedConsoleId: Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")] | None = None
    rules: ClassVar[tuple[str, ...]] = (
        "browser and wait apply only to open; expectedConsoleId applies only to close",
    )

    @model_validator(mode="after")
    def _action(self):
        supplied = self.model_fields_set
        if self.action != "open" and ("browser" in supplied or "wait" in supplied):
            raise ValueError("browser and wait apply only to console open")
        if self.action != "close" and "expectedConsoleId" in supplied:
            raise ValueError("expectedConsoleId applies only to console close")
        return self


class ConsoleWireRequest(Request):
    """Console lifecycle fields that cross the service boundary."""
    action: Literal["open", "status", "close"] = "open"
    expectedConsoleId: Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")] | None = None

    @model_validator(mode="after")
    def _action(self):
        if self.action != "close" and "expectedConsoleId" in self.model_fields_set:
            raise ValueError("expectedConsoleId applies only to console close")
        return self


HOST_REQUESTS: dict[str, type[Request]] = {
    "create": CreateRequest, "amend": AmendRequest, "submit": SubmitRequest,
    "await": AwaitRequest, "get": GetRequest, "inquire": InquireRequest,
    "continue": ContinueRequest, "accept": AcceptRequest,
    "conclude": ConcludeRequest, "cancel": CancelRequest,
    "console": ConsoleRequest,
}
OPERATIONS_REQUESTS: dict[str, type[Request]] = {
    "health": EmptyRequest, "harnesses": HarnessesRequest,
    "harness-set": HarnessSetRequest, "quota-redetect": QuotaRedetectRequest,
    "runtime": RuntimeRequest, "install": EmptyRequest, "paths": EmptyRequest,
    "storage-plan": EmptyRequest, "storage-apply": StorageApplyRequest,
    "worker-sessions": EmptyRequest,
    "restart": ServiceControlRequest, "stop": ServiceControlRequest,
}
ROLE_REQUESTS: dict[str, type[Request]] = {
    "submit": LeadSubmitRequest, "get": RoleGetRequest, "await": AwaitRequest,
    "inquire": InquireRequest, "continue": ContinueRequest, "accept": AcceptRequest,
    "conclude": ConcludeRequest, "cancel": CancelRequest,
    "suggest": SuggestRequest, "list": LeadListRequest,
}


class HelpRequest(Request):
    """Local `help [operations|role] [METHOD]` argument grammar."""
    arguments: Annotated[list[str], Field(max_length=2)] = Field(default_factory=list)

    @property
    def audience(self) -> Literal["host", "operations", "role"]:
        return self.arguments[0] if self.arguments and self.arguments[0] in ("operations", "role") else "host"

    @property
    def method(self) -> str | None:
        if not self.arguments:
            return None
        return (self.arguments[1] if len(self.arguments) == 2 else None) if self.audience != "host" else self.arguments[0]

    @model_validator(mode="after")
    def _grammar(self):
        if len(self.arguments) == 2 and self.arguments[0] not in ("operations", "role"):
            raise ValueError("two help arguments require operations or role before METHOD")
        registries = {"host": HOST_REQUESTS, "operations": OPERATIONS_REQUESTS, "role": ROLE_REQUESTS}
        method = self.method
        if method is not None and method not in registries[self.audience]:
            allowed = ", ".join(registries[self.audience])
            raise ValueError(f"unknown {self.audience} help method {method!r}; allowed: {allowed}")
        return self


OPERATION_TO_METHOD = {
    "workflow_create": "create", "workflow_amend": "amend", "workflow_submit": "submit",
    "workflow_get": "get", "workflow_continue": "continue", "workflow_accept": "accept",
    "workflow_conclude": "conclude", "workflow_cancel": "cancel",
    "inquiry_observe": "inquire", "workflow_suggest": "suggest", "task_list": "list",
    "capabilities": "harnesses", "harness_set": "harness-set", "quota_redetect": "quota-redetect",
    "runtime_info": "runtime", "storage_plan": "storage-plan", "storage_apply": "storage-apply",
    "worker_sessions": "worker-sessions",
    "service_control": "stop",
}


def request_model(name: str, *, audience: Literal["host", "operations", "role", "service", "lead", "local"] = "host") -> type[Request]:
    """Select the one request model for a CLI audience or named operation."""
    if audience == "local" and name == "output":
        return OutputRequest
    if audience == "local" and name == "help":
        return HelpRequest
    normalized_name = name.replace(".", "_")
    method = OPERATION_TO_METHOD.get(normalized_name, normalized_name)
    if audience == "service":
        if method == "console":
            return ConsoleWireRequest
        if normalized_name == "service_control":
            return ServiceControlWireRequest
        return HOST_REQUESTS.get(method) or OPERATIONS_REQUESTS.get(method) or ROLE_REQUESTS[method]
    if audience == "lead":
        if method == "submit":
            return LeadSubmitRequest
        return ROLE_REQUESTS[method]
    registry = {"host": HOST_REQUESTS, "operations": OPERATIONS_REQUESTS, "role": ROLE_REQUESTS}[audience]
    return registry[method]


def parse_request(name: str, payload: dict, *, audience: Literal["host", "operations", "role", "service", "lead", "local"] = "host") -> Request:
    """Validate one JSON request and report field paths in a board error."""
    model = request_model(name, audience=audience)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        issues = error.errors(include_url=False, include_context=False, include_input=False)
        details = []
        for issue in issues[:8]:
            location = ".".join(str(part) for part in issue["loc"]) or "$"
            details.append({"field": location, "message": issue["msg"], "type": issue["type"]})
        first = details[0]
        raise BoardError("INVALID_ARGUMENT", f"{first['field']}: {first['message']}", field=first["field"], issues=details) from None


def dump_request(request: Request) -> dict:
    """Return the exact validated camelCase wire fields without optional omissions."""
    return request.model_dump(exclude_unset=True, mode="json")


def parse_output(payload: dict) -> tuple[dict, Literal["brief", "full"]]:
    """Validate and remove the CLI-only projection choice through Pydantic."""
    remaining = dict(payload)
    raw = {"output": remaining.pop("output")} if "output" in remaining else {}
    return remaining, parse_request("output", raw, audience="local").output
