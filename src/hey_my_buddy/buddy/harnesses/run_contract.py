"""The harness-internal run contract: frozen values and one run protocol.

ADR-025 step 1-A draft (execution plan sections 3 and 5). One frozen
:class:`RunRequest` goes in, one factual :class:`RunResult` comes out, and every
harness implements the same :class:`HarnessRun` seam: ``run`` for one native
run. The values are facts: unknown stays ``None``/``unknown``, an observed value
is never filled in from a requested one, and a spawned process is never
reported as a started model. Payloads use ``formatVersion: 1`` with fixed key
sets; packages with a canonical projection are validated at the boundary.
The field set is a draft for the later ADR-025 steps to adjust through the
Host; the ordinary (256 KiB) and strict (512 KiB) JSON allowed sets and read
limits of the existing paths are untouched by this module. No role verdict,
routing mode, task brief, board client, database handle or agent credential is
part of this seam. The field types, bounds and JSON conversions are described
by pydantic through :mod:`hey_my_buddy.protocol.internal_models` (ADR-025
decision 6); this module holds only the constraints that are particular to
these facts.
"""
from __future__ import annotations

import re
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Mapping, Optional, Protocol, runtime_checkable

from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol import activity as activity_protocol
from ...protocol import tool_evidence as tool_evidence_protocol
from ...protocol import usage as usage_protocol
from ...protocol.harness_names import HARNESS_NAMES
from ...protocol.run_identity import RunIdentity
from ...protocol.internal_models import (
    AbsolutePath,
    Count,
    ExitCode,
    FormatVersion,
    FrozenJson,
    FrozenJsonAt,
    Hex64,
    Identifier,
    InternalModel,
    JsonTuple,
    MAX_COUNT,
    NonNegativeInt,
    OptionalFrozenJsonAt,
    OptionalText,
    RawText,
    Text,
    check_text,
    fail,
)

from pydantic import (
    AfterValidator, BeforeValidator, Field, field_serializer, field_validator, model_serializer, model_validator,
)

#: The only internal format version. It never changes a public board contract.
FORMAT_VERSION = 1

from ...protocol.harness_names import HARNESS_NAMES

HarnessName = Literal[*HARNESS_NAMES]
PermissionLevel = Literal["none", "read", "write", "full"]
EndStatus = Literal["ok", "error", "cancelled", "unknown"]
SchemaStatus = Literal["valid", "invalid", "unknown"]
GroupState = Literal["gone", "alive", "unknown"]
ContinuationMode = Literal["native-session", "reconstructed-new-session"]

#: Bound of each JSON fact envelope. Growing tool-call rows live in a private
#: digest-bound file; only its bounded reference travels in a tool package.
MAX_PACKAGE_BYTES = 512 * 1024
MAX_SCHEMA_BYTES = 65536
#: The final value's raw text and its parsed form hold the old 512 KiB strict
#: controller-read range; a value within it is never refused or dropped here.
MAX_VALUE_BYTES = 512 * 1024
MAX_UNKNOWN_EVENT_TYPES = 64
#: The one residual kind every harness folds further distinct unknown-event
#: kinds into once the distinct-kind cap above is reached. The parenthesised
#: name cannot pass :func:`unknown_event_name`'s charset, so no retained kind
#: of a real native label can ever collide with it.
UNLISTED_NATIVE_EVENTS = "(unlisted-native-events)"
MAX_UNCONFIRMED_ITEMS = 16
MAX_UNCONFIRMED_NAME_CHARS = 64
MAX_EVIDENCE_REFS = 32
MAX_SESSION_SERVICES = 8
MAX_SERVICE_TOOL_NAMES = 16
#: A role-assembled input is nonempty; its task, context and feedback text
#: reach the harness whole. NUL is allowed and serialized escaped on the wire.
NonEmptyInputText = Annotated[str, Field(min_length=1)]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise fail("the value must contain non-whitespace text")
    return value


def unknown_event_name(label: object) -> str:
    """One observed native label's retained unknown-event kind name.

    A bounded label charset, or ``unknown`` for anything else — including the
    residual name below, which no native label can reproduce.
    """
    return label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label) else "unknown"


def fold_unknown_events(counts: dict[str, int], name: str, occurrences: int = 1) -> None:
    """Fold occurrences of one unknown-event kind into a bounded kind-count map.

    The public result carries at most ``MAX_UNKNOWN_EVENT_TYPES`` distinct
    kinds; the reserved residual name holds one of those slots, so beyond
    ``MAX - 1`` real kinds further distinct kinds merge into it and the total
    stays exact, with no occurrence silently dropped. ``name`` arrives already
    retained: from :func:`unknown_event_name` for a fresh native label, or a
    kind an earlier fold already counted.
    """
    if name not in counts and len(counts) >= MAX_UNKNOWN_EVENT_TYPES - 1:
        name = UNLISTED_NATIVE_EVENTS
    counts[name] = counts.get(name, 0) + occurrences


class RunBuddy(InternalModel):
    """The frozen provider/model/effort selection; no default buddy, no fallback."""

    provider: Annotated[Text(128), AfterValidator(_nonblank)]
    model: Annotated[Text(128), AfterValidator(_nonblank)]
    effort: Annotated[Text(64), AfterValidator(_nonblank)]


class PrivateStatePaths(InternalModel):
    """The selected state root and attempt-private roots of one run."""

    state_dir: AbsolutePath
    invocation_root: AbsolutePath
    native_root: AbsolutePath


class RunBudget(InternalModel):
    """The optional hard deadline consumed by a native driver.

    ``None`` leaves an ordinary Worker run unbounded. The old output
    byte, tool-count and read-byte bounds are not parameters of this seam: the
    harnesses enforce their own existing constants, and the fact packages
    carry their own bounds.
    """

    timeout_seconds: Annotated[int | None, Field(ge=1, le=86400)]


class ResumeCheckpoint(InternalModel):
    """The three facts the existing Codex continuation binding compares."""

    native_turn_id: Text(512)
    attempt_id: Identifier
    input_sha256: Hex64


class RunContinuation(InternalModel):
    """A requested continuation; without evidence no native resume is enabled.

    A native-session resume names the exact previous session it continues; a
    reconstruction may honestly carry none. The harness derives every other
    binding fact from that session identity itself.
    """

    mode: ContinuationMode
    previous_session_id: Optional[Annotated[Text(512), AfterValidator(_nonblank)]] = None
    checkpoint: Optional[ResumeCheckpoint] = None

    @model_validator(mode="after")
    def _native_resume_names_its_session(self) -> "RunContinuation":
        if self.mode == "native-session" and not self.previous_session_id:
            raise fail("a native-session continuation requires the exact previous session id",
                       field="previousSessionId")
        return self


class SessionService(InternalModel):
    """The fully qualified tool names of one mounted in-run session service.

    This is the one fact the native driver consumes: the mounted binding it
    must find and the names the micro task prompt cites. The service instance,
    its schemas and its delivery mode stay with the role and the harness's own
    mount; they never travel in the request.
    """

    tool_names: JsonTuple(Text(512), max_items=MAX_SERVICE_TOOL_NAMES) = ()



class SessionRecordCloseFailure(InternalModel):
    """Bounded diagnostic facts of a failed display-record close."""

    category: Text(128)
    message: RawText(8192)
    truncated: bool = False


class SessionRecordFailureParams(InternalModel):
    """The first display failure, with any failure while closing its file."""

    category: Text(128)
    message: RawText(8192)
    truncated: bool = False
    close_failure: Optional[SessionRecordCloseFailure] = None

    @model_serializer(mode="wrap")
    def _omit_absent_close_failure(self, handler: Any) -> dict:
        return {key: value for key, value in handler(self).items() if value is not None}


class SessionRecordFailure(InternalModel):
    """A standard reason; display failure never changes native execution facts."""

    code: Literal["session-record-write-failed"]
    params: SessionRecordFailureParams


class RunRequest(InternalModel):
    """One harness run request: values only, no role authority of any kind."""

    format_version: FormatVersion = FORMAT_VERSION

    identity: RunIdentity
    harness: HarnessName
    buddy: RunBuddy
    cwd: AbsolutePath
    private_state: PrivateStatePaths
    session_record_path: Optional[AbsolutePath] = None
    session_record_failure: Optional[SessionRecordFailure] = None
    input_text: NonEmptyInputText
    permission: PermissionLevel
    output_schema: FrozenJsonAt(MAX_SCHEMA_BYTES)
    budget: RunBudget
    continuation: Optional[RunContinuation] = None
    session_services: JsonTuple(SessionService, max_items=MAX_SESSION_SERVICES) = ()
    capture_evidence: bool = False

class NativeIdentity(InternalModel):
    """One native-provided root identity; no field is ever fabricated.

    The wire key set is a nonempty subset of the closed identity set, and a
    field that was never reported stays absent from the wire instead of being
    written as ``null``.
    """

    session_id: OptionalText(512) = None
    turn_id: OptionalText(512) = None

    @model_validator(mode="after")
    def _carries_one_field(self) -> "NativeIdentity":
        if not any((self.session_id, self.turn_id)):
            raise fail("the native identity payload must carry at least one native identifier field")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict:
        return {key: value for key, value in handler(self).items() if value is not None}


class RunEnd(InternalModel):
    """The native/protocol end fact; never a role verdict.

    ``message`` is the bounded raw text of the failure the driver itself
    observed — the composed codes and whitelisted summaries the native paths
    already produce (at most a few hundred bytes; the bound holds the longest
    real composition, a whitelisted failure summary inside a native error
    text). A role's own stop wording stays with the role and never travels
    here.
    """

    status: EndStatus
    reason_code: OptionalText(128) = None
    native_exit_code: Optional[ExitCode] = None
    message: Optional[RawText(512)] = None


class CheckedValue(InternalModel):
    """One buddy identity value actually checked by the native path."""

    value: Text(128)


class CheckedBuddy(InternalModel):
    """Per-value checked buddy; a requested value never stands in for one."""

    provider: Optional[CheckedValue] = None
    model: Optional[CheckedValue] = None
    effort: Optional[CheckedValue] = None


class ResultBuddy(InternalModel):
    """Requested and checked buddy, kept strictly apart.

    Native readback is retained as checked values. The role projects a complete
    checked buddy into ``resolved``; it never fills an unknown value from the request.
    """

    requested: Optional[RunBuddy] = None
    checked: CheckedBuddy = Field(default_factory=CheckedBuddy)


class RunValue(InternalModel):
    """The final value and its schema status.

    ``raw`` keeps the delivery's own text even when it is not the parsed
    value; schema verdicts stay with the role, so no per-error list travels
    here.
    """

    schema_status: SchemaStatus
    raw: Optional[RawText(MAX_VALUE_BYTES)] = None
    parsed: OptionalFrozenJsonAt(MAX_VALUE_BYTES) = None
    correction_count: NonNegativeInt = 0


class CompletionEvidence(InternalModel):
    """Whether the final value carrier observed the end of its stream."""

    stream_end: Optional[bool] = None


class PolicyFact(InternalModel):
    """The settings actually sent through a native policy path."""

    requested: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None


class EffectivePolicy(InternalModel):
    """The native tool settings consumed by the role projection.

    The network fact stays out until a harness actually reports one: a
    constant ``unknown`` placeholder proves nothing and reads nothing.
    """

    tools: Optional[PolicyFact] = None


class ContinuationFacts(InternalModel):
    """The run's native continuation facts; the role decides whether to use them."""

    resumable: Optional[bool] = None


class StopLayer(InternalModel):
    """One layer's stop facts; unknown is never read as stopped.

    ``group_state`` is ``gone`` only when this layer's own owned observation
    found the group gone, or when the holding side confirmed the spawn never
    happened. An exited leader, a closed stream or an acknowledged interrupt is
    never, by itself, a gone group.
    """

    group_state: GroupState = "unknown"


class InterruptEvidence(InternalModel):
    """A native interrupt: the request fact and its basis.

    A missing record stays ``None``; only an explicit ``True``/``False`` from
    the payload or the collector is a fact. An acknowledgement a harness
    cannot observe is never carried here as a constant.
    """

    requested: Optional[bool] = None
    basis: OptionalText(48) = None


class StopEvidence(InternalModel):
    """The native layer's stop facts plus the interrupt record.

    The outer controller's own stop confirmation is the holding side's real
    process fact, carried by the collector — never a constant second layer.
    """

    native: StopLayer = Field(default_factory=StopLayer)
    interrupt: InterruptEvidence = Field(default_factory=InterruptEvidence)


class EvidenceRef(InternalModel):
    """One private evidence file's binding, size and digest."""

    kind: Text(32)
    location: Text(1024)
    size_bytes: Optional[Count] = None
    sha256: Optional[Hex64] = None


def _normalized_package(kind: str, value: Any) -> Optional[FrozenJson]:
    """Validate one fact package against its own canonical projection.

    ``None`` stays unknown; a package that its own normalizer cannot accept is
    refused instead of being quietly dropped, so no fact is lost in the codec.
    The normalized projection is what the field keeps, on construction exactly
    as on decode.
    """
    if value is None:
        return None
    unpacked = value.value if isinstance(value, FrozenJson) else value
    if kind == "activity":
        try:
            normalized = activity_protocol.normalize_activity(unpacked)
        except BoardError as error:
            raise fail("the activity package is not the canonical projection",
                       reason=str(error.message)[:200]) from None
    elif kind == "usage":
        normalized = usage_protocol.normalize_token_usage(unpacked)
    elif kind == "providerRejection":
        # Every harness reports its rejection phase in this provider rejection package;
        # the common codec refuses a missing phase rather than inferring one.
        normalized = usage_protocol.normalize_provider_rejection(unpacked)
    elif kind == "lastAssistantMessage":
        normalized = usage_protocol.normalize_last_assistant_message(unpacked)
    else:  # pragma: no cover - the kind set is closed above
        raise fail(f"unknown package kind {kind}")
    if normalized is None:
        raise fail(f"the {kind} package is not a usable canonical projection", field=kind)
    return FrozenJson.from_value(normalized, kind, maximum=MAX_PACKAGE_BYTES)


def _validated_tool_evidence(value: Any) -> Optional[FrozenJson]:
    """Validate the bounded envelope; the service checks its digest-bound rows.

    No publication judgment lives here. Growing per-call facts cross the
    execution/service boundary as a private file, never a capped event list.
    """
    if value is None:
        return None
    package = value.value if isinstance(value, FrozenJson) else value
    tool_evidence_protocol.validate_tool_evidence_package(package)
    if package["binding"]["harness"] not in HARNESS_NAMES:
        raise fail("the tool evidence binding harness is not a harness name", field="toolEvidence.binding.harness")
    return FrozenJson.from_value(package, "toolEvidence", maximum=MAX_PACKAGE_BYTES)


def _native_error_record(value: Any) -> Optional[FrozenJson]:
    """One harness's own whitelisted native failure attribution, as JSON.

    This is a second, separate existing fact beside the provider rejection attribution: the
    harness already projects the native failure attribution through its own
    whitelist before it reaches this field, and the real shapes are structured
    (nested attribution objects, nullable codes and text summaries), so the
    field keeps that bounded JSON verbatim instead of re-describing every
    vendor's schema. Redeciding what the whitelist admits stays with the
    harness; nothing here judges publishability.
    """
    if value is None:
        return None
    unpacked = value.value if isinstance(value, FrozenJson) else value
    if not isinstance(unpacked, dict):
        raise fail("nativeError must be an object", field="nativeError")
    return FrozenJson.from_value(unpacked, "nativeError", maximum=MAX_PACKAGE_BYTES)


#: One canonical fact package, validated by its own projection on every path.
ActivityPackage = Annotated[Optional[FrozenJson], BeforeValidator(partial(_normalized_package, "activity"))]
UsagePackage = Annotated[Optional[FrozenJson], BeforeValidator(partial(_normalized_package, "usage"))]
ProviderRejectionPackage = Annotated[Optional[FrozenJson],
                                 BeforeValidator(partial(_normalized_package, "providerRejection"))]
LastAssistantMessagePackage = Annotated[Optional[FrozenJson],
                                        BeforeValidator(partial(_normalized_package, "lastAssistantMessage"))]
ToolEvidencePackage = Annotated[Optional[FrozenJson], BeforeValidator(_validated_tool_evidence)]
NativeErrorRecord = Annotated[Optional[FrozenJson], BeforeValidator(_native_error_record)]


class RunFeedback(InternalModel):
    """The role observer's minimal in-process answer to the observed facts.

    The actions are exactly three: ``continue`` lets the run proceed, ``stop``
    asks the driver to interrupt the native run at once, and ``correct``
    carries the complete next input text of one in-run correction. Whether a
    correction is offered at all — at most once, never for an enum violation —
    and how that text is assembled stay with the role's observation code; the
    driver only executes the feedback, keeps the one native process and the
    total deadline, and reports how many corrections happened. No role verdict
    travels in this value.
    """

    action: Literal["continue", "stop", "correct"]
    input_text: Optional[NonEmptyInputText] = None

    @model_validator(mode="after")
    def _correction_carries_its_input(self) -> "RunFeedback":
        if self.action == "correct" and self.input_text is None:
            raise fail("a correction carries the complete next input text", field="inputText")
        if self.action != "correct" and self.input_text is not None:
            raise fail("only a correction carries an input text", field="inputText")
        return self


#: The shared immutable continue/stop answers; every correcting answer carries
#: its own input text and is constructed per observation.
FEEDBACK_CONTINUE = RunFeedback(action="continue")
FEEDBACK_STOP = RunFeedback(action="stop")


class UnknownEvents(InternalModel):
    """Bounded native event counts, with an immutable map and an object on wire."""

    counts_by_type: JsonTuple(tuple[Text(128), Count], max_items=MAX_UNKNOWN_EVENT_TYPES)
    total: Count

    @field_validator("counts_by_type", mode="before")
    @classmethod
    def _object_counts(cls, value: Any) -> tuple:
        if not isinstance(value, dict):
            raise fail("unknownEvents.countsByType must be an object")
        return tuple(value.items())

    @field_serializer("counts_by_type")
    def _serialize_counts(self, value: tuple) -> dict:
        return dict(value)


class NativeEvidence(InternalModel):
    """Native shapes not understood and requested properties not confirmed.

    Names describe observations supplied by each harness; this contract gives
    them no role verdict and defines no harness-specific vocabulary.
    """

    unknown_events: Optional[UnknownEvents] = None
    unconfirmed: JsonTuple(Annotated[str, Field(min_length=1, max_length=MAX_UNCONFIRMED_NAME_CHARS,
                                              pattern=r"^[a-z0-9-]+$")],
                           max_items=MAX_UNCONFIRMED_ITEMS) = ()


class RunResult(InternalModel):
    """One native run's facts; ``ok`` means the native interaction completed.

    The outer collector adds the outer stop facts and the roles project this
    value into the current AdapterOutcome and board results, so no field here is
    a Worker success, a publishable Router answer or a Host acceptance.
    """

    format_version: FormatVersion = FORMAT_VERSION

    identity: RunIdentity
    harness: HarnessName
    end: RunEnd
    #: The version text the native probe actually returns is truncated to 80
    #: characters by the production probe; the library's character bound carries
    #: exactly that source. An empty probe text stays empty here — the legacy
    #: "unknown" spelling is the outer projection's choice.
    harness_version: Annotated[Optional[str], Field(max_length=80)] = None
    #: The old native event count's exact meaning, kept as its own fact: the
    #: canonical native events observed before the final round's settlement. It
    #: is a count, never an order — sequence and ordinal evidence is the native
    #: provenance's own.
    native_event_count: Optional[Count] = None
    native_evidence: Optional[NativeEvidence] = None
    model_started: Optional[bool] = None
    buddy: ResultBuddy = Field(default_factory=ResultBuddy)
    native_identity: Optional[NativeIdentity] = None
    value: Optional[RunValue] = None
    completion_evidence: Optional[CompletionEvidence] = None
    tool_evidence: ToolEvidencePackage = None
    effective_policy: EffectivePolicy = Field(default_factory=EffectivePolicy)
    activity: ActivityPackage = None
    usage: UsagePackage = None
    provider_rejection: ProviderRejectionPackage = None
    native_error: NativeErrorRecord = None
    last_assistant_message: LastAssistantMessagePackage = None
    session_record_failure: Optional[SessionRecordFailure] = None
    continuation: Optional[ContinuationFacts] = None
    stop_evidence: StopEvidence = Field(default_factory=StopEvidence)
    evidence_refs: JsonTuple(EvidenceRef, max_items=MAX_EVIDENCE_REFS) = ()


@runtime_checkable
class HarnessRun(Protocol):
    """The one seam every harness implements (ADR-025 decision 1).

    ``run`` takes one frozen request and returns one factual result. ``observer``
    is the role-owned in-process callback that receives already-normalized,
    retained run facts and answers with one :class:`RunFeedback`: continue, stop
    (the driver interrupts the native run), or correct (the driver runs the
    carried next input text as one in-run correction on the same native process
    and total deadline). ``services`` are the role-held narrow session service
    instances (completion, inquiry, checkpoint); their method surface belongs to
    the roles. ``cancelled`` is the outer cancel check. Optional native
    capabilities are declared as a callable or ``None`` on every registered
    module, so callers do not inspect module shape or function signatures.
    """

    supported_request_controls: tuple[str, ...]
    prepare_run_services: Callable[..., Any] | None
    native_evidence: Callable[[RunResult], dict] | None
    run_discovery: Callable[..., dict] | None
    run_delete_sessions: Callable[..., dict[str, str]] | None

    def run(self, request: RunRequest, *, observer: Callable[[Mapping[str, Any]], RunFeedback],
            services: Any, cancelled: Callable[[], bool]) -> RunResult: ...

    def check_preparation(self, spec: dict, environment: dict) -> None: ...

    def session_facts(self, native_root: Path, session_id: str | None) -> dict: ...

    def validate_turn_provenance(self, record: dict) -> str | None: ...


__all__ = [
    "FEEDBACK_CONTINUE", "FEEDBACK_STOP",
    "FORMAT_VERSION",
    "FrozenJson", "HARNESS_NAMES", "HarnessRun", "MAX_COUNT", "CheckedBuddy", "CheckedValue",
    "CompletionEvidence", "ContinuationFacts", "EffectivePolicy", "EvidenceRef",
    "InterruptEvidence", "NativeIdentity", "NativeEvidence", "UnknownEvents", "PrivateStatePaths",
    "MAX_UNKNOWN_EVENT_TYPES", "MAX_UNCONFIRMED_ITEMS", "MAX_UNCONFIRMED_NAME_CHARS",
    "UNLISTED_NATIVE_EVENTS",
    "ResumeCheckpoint", "RunBudget", "RunBuddy", "RunContinuation", "RunEnd", "RunFeedback", "RunIdentity",
    "PermissionLevel", "RunRequest", "RunResult", "RunValue", "SessionService", "StopEvidence",
    "StopLayer", "canonical_json",
    "fold_unknown_events", "unknown_event_name",
]
