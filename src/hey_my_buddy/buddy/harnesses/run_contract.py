"""The harness-internal run contract: frozen values, one codec, one protocol.

ADR-025 step 1-A draft (execution plan sections 3 and 5). One frozen
:class:`RunRequest` goes in, one factual :class:`RunResult` comes out, and every
harness implements the same :class:`HarnessRun` seam: ``run`` for one native
run. The values are facts: unknown stays ``None``/``unknown``, an observed value
is never filled in from a requested one, and a spawned process is never
reported as a started model. The internal JSON uses ``formatVersion: 1`` with
fixed key sets and the project's bounded JSON rules; packages that already have
a canonical projection are validated against it once, at the decode boundary.
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

from functools import partial
from typing import Annotated, Any, Callable, Literal, Mapping, Optional, Protocol, Tuple, runtime_checkable

from ...errors import BoardError
from ...json_codec import canonical_json, decode_bounded_frame
from ...protocol import activity as activity_protocol
from ...protocol import tool_evidence as tool_evidence_protocol
from ...protocol import usage as usage_protocol
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

from pydantic import AfterValidator, BeforeValidator, Field, field_serializer, model_serializer, model_validator

#: The only internal format version. It never changes a public board contract.
FORMAT_VERSION = 1

HARNESS_NAMES = ("codex", "claude", "zcode", "dsh")
TOOL_SCOPES = ("none", "read", "write")
END_STATUSES = ("ok", "error", "cancelled")
MODEL_START_BASES = ("native-start", "input-admitted", "input-sent", "legacy-report", "unknown")
CHECK_BASES = ("catalog-membership", "native-readback", "unknown")
SCHEMA_STATUSES = ("valid", "invalid", "unknown")
VALUE_MECHANISMS = ("native-schema", "completion-tool", "final-message")
POLICY_ENFORCEMENTS = ("native", "unrestricted", "unknown")
GROUP_STATES = ("gone", "alive", "unknown")
CONTINUATION_MODES = ("native-session", "reconstructed-new-session")
#: The native-provided identifier fields, matching the tool-evidence identity set.
NATIVE_ID_KEYS = {"sessionId": "session_id", "threadId": "thread_id", "turnId": "turn_id",
                  "inputId": "input_id", "callId": "call_id"}

HarnessName = Literal["codex", "claude", "zcode", "dsh"]
ToolScope = Literal["none", "read", "write"]
EndStatus = Literal["ok", "error", "cancelled"]
ModelStartBasis = Literal["native-start", "input-admitted", "input-sent", "legacy-report", "unknown"]
CheckBasis = Literal["catalog-membership", "native-readback", "unknown"]
SchemaStatus = Literal["valid", "invalid", "unknown"]
ValueMechanism = Literal["native-schema", "completion-tool", "final-message"]
PolicyEnforcement = Literal["native", "unrestricted", "unknown"]
GroupState = Literal["gone", "alive", "unknown"]
ContinuationMode = Literal["native-session", "reconstructed-new-session"]

#: Draft bounds of this internal format. They are new bounds for new frames; the
#: existing per-path read limits (ordinary 256 KiB evidence, strict 512 KiB
#: controller results) are separate and unchanged. The request bound holds a
#: full role-assembled prompt: the board's task text allows 1 MiB
#: (``protocol/schemas.MAX_TASK_BYTES``) and the role may add bounds of its own,
#: plus schemas and service descriptions; the result bound holds the largest
#: existing strict read (512 KiB) beside every bounded package.
MAX_RUN_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RUN_RESULT_BYTES = 2 * 1024 * 1024
#: The bounded fact-package size: it holds the old strict 512 KiB read range.
#: The per-field and per-count bounds below (128 events, 512-char identifiers)
#: keep a maximal legal tool-evidence package within it, so no package the old
#: paths could carry is refused here for size alone.
MAX_PACKAGE_BYTES = 512 * 1024
#: The role-assembled input text: the board's task text bound is 1 MiB and the
#: roles add their own bounded material (hints, workspace facts, the answer
#: schema for fast prompts), so the bound is the task bound plus that margin.
#: The request frame bound above carries the worst-case JSON escaping of such
#: a text (six bytes per control character) instead of truncating or rewriting
#: a role prompt.
MAX_INPUT_TEXT_BYTES = 2 * 1024 * 1024
MAX_SCHEMA_BYTES = 65536
#: The final value's raw text and its parsed form hold the old 512 KiB strict
#: controller-read range; a value within it is never refused or dropped here.
MAX_VALUE_BYTES = 512 * 1024
MAX_ERROR_ITEMS = 16
MAX_ERROR_CHARS = 500
MAX_DENIED_INTERACTIONS = 64
MAX_UNKNOWN_EVENT_TYPES = 64
MAX_POLICY_LIMITATIONS = 8
MAX_EVIDENCE_REFS = 32
MAX_SESSION_SERVICES = 8
MAX_SERVICE_TOOL_NAMES = 16
MAX_NETWORK_DOMAINS = 64
MAX_CHECKS = 8
MAX_ROOT_IDENTITIES = 32
#: The role-assembled input text keeps the old input allowed set: any bounded
#: string, NUL included (the board's task text admits NUL and the native wire
#: serializes it escaped), but a whole input is never empty — the library's
#: own length bound enforces it on construction and decode alike. The
#: corrected input of one feedback carries the same allowed set.
NonEmptyInputText = Annotated[RawText(MAX_INPUT_TEXT_BYTES), Field(min_length=1)]


class RunIdentity(InternalModel):
    """The frozen execution identity shared by a request and its result."""

    task_id: Identifier
    attempt_id: Identifier
    generation: NonNegativeInt
    invocation_id: Identifier
    turn_id: OptionalText(128) = None
    input_sha256: Optional[Hex64] = None


class RunConfiguration(InternalModel):
    """The frozen provider/model/effort selection; no default buddy, no fallback."""

    provider: Text(128)
    model: Text(128)
    effort: Text(64)


class FrozenAccountReference(InternalModel):
    """A non-secret reference to the frozen account.

    Only scalar references: adapter, source, revisions, an identity string and a
    native account location reference. No key, token or file content is carried,
    and the executor never opens the user's credential files.
    """

    adapter: HarnessName
    source: OptionalText(64) = None
    revision: Optional[NonNegativeInt] = None
    credential_revision: Optional[NonNegativeInt] = None
    identity: OptionalText(256) = None
    native_location: OptionalText(1024) = None


class PrivateStatePaths(InternalModel):
    """The attempt-private invocation and native roots of one run."""

    invocation_root: AbsolutePath
    native_root: AbsolutePath


class NetworkPolicy(InternalModel):
    """The requested network fact; ``False`` never forbids a model connection."""

    requested: bool
    allowed_domains: Optional[JsonTuple(Text(255), max_items=MAX_NETWORK_DOMAINS)] = None


class RunBudget(InternalModel):
    """The run budget; ``timeoutSeconds: 0`` keeps the existing unlimited meaning."""

    timeout_seconds: Annotated[int, Field(ge=0, le=86400)]
    max_output_bytes: Count
    tool_calls: Optional[Count] = None
    bytes_read: Optional[Count] = None


class RunContinuation(InternalModel):
    """A requested continuation; without evidence no native resume is enabled.

    A native-session resume names the exact previous session it continues; a
    reconstruction may honestly carry none.
    """

    mode: ContinuationMode
    previous_session_id: OptionalText(512) = None
    binding_ref: OptionalText(512) = None
    previous_native_turn_id: OptionalText(512) = None
    previous_attempt_id: Optional[Identifier] = None
    previous_input_sha256: Optional[Hex64] = None

    @model_validator(mode="after")
    def _native_resume_names_its_session(self) -> "RunContinuation":
        if self.mode == "native-session" and not self.previous_session_id:
            raise fail("a native-session continuation requires the exact previous session id",
                       field="previousSessionId")
        return self


class SessionService(InternalModel):
    """One in-run service description; its instance stays with the role."""

    service_id: Identifier
    kind: Text(32)
    tool_names: JsonTuple(Text(512), max_items=MAX_SERVICE_TOOL_NAMES) = ()
    input_schema: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None
    output_schema: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None
    delivery_mode: OptionalText(32) = None


class RunRequest(InternalModel):
    """One harness run request: values only, no role authority of any kind."""

    format_version: FormatVersion = FORMAT_VERSION

    identity: RunIdentity
    harness: HarnessName
    configuration: RunConfiguration
    cwd: AbsolutePath
    private_state: PrivateStatePaths
    input_text: NonEmptyInputText
    tool_scope: ToolScope
    network: NetworkPolicy
    output_schema: FrozenJsonAt(MAX_SCHEMA_BYTES)
    budget: RunBudget
    frozen_account: Optional[FrozenAccountReference] = None
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
    thread_id: OptionalText(512) = None
    turn_id: OptionalText(512) = None
    input_id: OptionalText(512) = None
    call_id: OptionalText(512) = None

    @model_validator(mode="after")
    def _carries_one_field(self) -> "NativeIdentity":
        if not any((self.session_id, self.thread_id, self.turn_id, self.input_id, self.call_id)):
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
    signal: OptionalText(32) = None
    message: Optional[RawText(512)] = None


class ModelStartEvidence(InternalModel):
    """The basis of the ``modelStarted`` boolean; a send is never a model proof."""

    basis: ModelStartBasis
    native_identity: Optional[NativeIdentity] = None
    event_sequence: Optional[Count] = None


class CheckedValue(InternalModel):
    """One checked configuration value with the basis that backs it."""

    value: Text(128)
    basis: CheckBasis
    source: OptionalText(120) = None
    native_identity: Optional[NativeIdentity] = None


class CheckedConfiguration(InternalModel):
    """Per-value checked configuration; a requested value never stands in for one."""

    provider: Optional[CheckedValue] = None
    model: Optional[CheckedValue] = None
    effort: Optional[CheckedValue] = None


class ResultConfiguration(InternalModel):
    """Requested, checked and observed configuration, kept strictly apart."""

    requested: Optional[RunConfiguration] = None
    checked: CheckedConfiguration = Field(default_factory=CheckedConfiguration)
    observed: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None
    checks: JsonTuple(Text(32), max_items=MAX_CHECKS) = ()


class RunValue(InternalModel):
    """The final value of one run and the basis of its schema check."""

    schema_status: SchemaStatus
    mechanism: ValueMechanism
    raw: Optional[RawText(MAX_VALUE_BYTES)] = None
    parsed: OptionalFrozenJsonAt(MAX_VALUE_BYTES) = None
    validation_basis: OptionalText(128) = None
    errors: JsonTuple(Text(MAX_ERROR_CHARS), max_items=MAX_ERROR_ITEMS) = ()
    correction_count: NonNegativeInt = 0


class CompletionEvidence(InternalModel):
    """How the final value was actually delivered, with its native evidence."""

    mechanism: ValueMechanism
    stream_end: Optional[bool] = None
    native_identity: Optional[NativeIdentity] = None
    call_id: OptionalText(512) = None
    event_order: Optional[Count] = None
    receipt_ref: OptionalText(1024) = None
    receipt_verified: Optional[bool] = None
    native_outcome: OptionalText(64) = None


class DeniedInteraction(InternalModel):
    """One native interaction the harness refused; no tool arguments are kept.

    ``request_id`` stays ``None`` when the harness's own record carried no
    native request id; one is never invented for it.
    """

    method: Text(128)
    action: Text(64)
    request_id: OptionalText(128) = None
    native_identity: Optional[NativeIdentity] = None
    reason: OptionalText(400) = None


def _count_pairs(value: Any) -> Any:
    """The wire form of the unknown-event counts is one JSON object.

    This is the one input conversion the strict tuple still needs: a decoded
    JSON object arrives as a ``Mapping`` and becomes the tuple of its pairs.
    Everything else passes to the strict tuple validation unchanged, so a JSON
    array of pairs is refused exactly like any non-object value.
    """
    if isinstance(value, Mapping):
        return tuple(value.items())
    return value


class UnknownEvents(InternalModel):
    """Native events of legal shape that no rule classified; never silently dropped.

    The wire form carries one ``countsByType`` object; the Python value is the
    tuple of its pairs, with unique names and a total that must equal their sum.
    """

    counts: Annotated[Tuple[Tuple[Text(64), Count], ...], BeforeValidator(_count_pairs)] = Field(
        alias="countsByType", default=(), max_length=MAX_UNKNOWN_EVENT_TYPES)
    total: Count
    truncated: bool = False

    @field_serializer("counts")
    def _counts_object(self, value: Any) -> dict:
        return dict(value)

    @model_validator(mode="after")
    def _total_is_the_sum(self) -> "UnknownEvents":
        names = [name for name, _count in self.counts]
        if len(set(names)) != len(names):
            raise fail("the unknown event counts repeat an event type", field="countsByType")
        if sum(count for _name, count in self.counts) != self.total:
            raise fail("the unknown event total must equal the sum of the counts", field="total")
        return self


class PolicyFact(InternalModel):
    """One effective-policy fact; only a native readback may claim enforcement."""

    enforcement: PolicyEnforcement
    requested: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None
    reported: OptionalFrozenJsonAt(MAX_SCHEMA_BYTES) = None
    basis: OptionalText(128) = None
    limitations: JsonTuple(Text(256), max_items=MAX_POLICY_LIMITATIONS) = ()


class EffectivePolicy(InternalModel):
    """Effective policy per tools, filesystem and network; honesty over neatness."""

    tools: Optional[PolicyFact] = None
    filesystem: Optional[PolicyFact] = None
    network: Optional[PolicyFact] = None


class ContinuationFacts(InternalModel):
    """The run's native continuation facts; the role decides whether to use them."""

    resumable: Optional[bool] = None
    native_session_ref: OptionalText(1024) = None
    binding_ref: OptionalText(1024) = None
    checkpoint_ref: OptionalText(1024) = None
    basis: OptionalText(128) = None


class StopLayer(InternalModel):
    """One layer's stop facts; unknown is never read as stopped.

    ``group_state`` is ``gone`` only when this layer's own owned observation
    found the group gone, or when the holding side confirmed the spawn never
    happened. An exited leader, a closed stream or an acknowledged interrupt is
    never, by itself, a gone group.
    """

    group_state: GroupState = "unknown"
    started: Optional[bool] = None
    leader_exited: Optional[bool] = None
    exit_code: Optional[ExitCode] = None
    observation_basis: OptionalText(48) = None


class InterruptEvidence(InternalModel):
    """A native interrupt: the request and its acknowledgement stay separate.

    A missing record stays ``None``/unknown; only an explicit ``True``/``False``
    from the payload or the collector is a fact.
    """

    requested: Optional[bool] = None
    acknowledged: Optional[bool] = None
    basis: OptionalText(48) = None


class StopEvidence(InternalModel):
    """Two conservative layers plus the interrupt record."""

    native: StopLayer = Field(default_factory=StopLayer)
    controller: StopLayer = Field(default_factory=StopLayer)
    interrupt: InterruptEvidence = Field(default_factory=InterruptEvidence)


class EvidenceRef(InternalModel):
    """One private evidence file's binding, size, digest and retention fact."""

    kind: Text(32)
    location: Text(1024)
    retained: bool = True
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
    elif kind == "quota":
        normalized = usage_protocol.normalize_quota(unpacked)
    elif kind == "nativeFailure":
        normalized = usage_protocol.normalize_quota_failure(unpacked)
    elif kind == "lastAssistantMessage":
        normalized = usage_protocol.normalize_last_assistant_message(unpacked)
    else:  # pragma: no cover - the kind set is closed above
        raise fail(f"unknown package kind {kind}")
    if normalized is None:
        raise fail(f"the {kind} package is not a usable canonical projection", field=kind)
    return FrozenJson.from_value(normalized, kind, maximum=MAX_PACKAGE_BYTES)


def _validated_tool_evidence(value: Any) -> Optional[FrozenJson]:
    """Structural validation of one existing version-1 tool-evidence package.

    The check is shape only: exact fields, the current version, bounded
    identifiers and ACP categories. No allowance, completeness or publication
    judgment lives here; that stays with the blackboard's single judge.
    """
    if value is None:
        return None
    package = value.value if isinstance(value, FrozenJson) else value
    fields = {"version", "binding", "nativeIdentity", "streamComplete", "events",
              "toolCalls", "unsettledToolCalls", "truncated"}
    if not isinstance(package, dict) or set(package) != fields:
        raise fail("the tool evidence package must carry its exact field set", field="toolEvidence")
    if type(package["version"]) is not int or package["version"] != tool_evidence_protocol.TOOL_EVIDENCE_VERSION:
        raise fail("the tool evidence package version is not supported", field="toolEvidence")
    binding = package["binding"]
    if not isinstance(binding, dict) or set(binding) != set(tool_evidence_protocol.BINDING_FIELDS):
        raise fail("the tool evidence binding must carry its exact field set", field="toolEvidence.binding")
    if binding["adapter"] not in HARNESS_NAMES:
        raise fail("the tool evidence binding adapter is not a harness name", field="toolEvidence.binding.adapter")
    check_text(binding["taskId"], "toolEvidence.binding.taskId", maximum=128)
    check_text(binding["attemptId"], "toolEvidence.binding.attemptId", maximum=128)
    if isinstance(binding["generation"], bool) or not isinstance(binding["generation"], int) \
            or binding["generation"] < 0:
        raise fail("the tool evidence binding generation must be a nonnegative integer",
                   field="toolEvidence.binding.generation")
    for flag in ("streamComplete", "truncated"):
        if not isinstance(package[flag], bool):
            raise fail(f"toolEvidence.{flag} must be a boolean", field=f"toolEvidence.{flag}")
    for name in ("toolCalls", "unsettledToolCalls"):
        number = package[name]
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= MAX_COUNT:
            raise fail(f"toolEvidence.{name} must be an integer between 0 and {MAX_COUNT}",
                       field=f"toolEvidence.{name}")
    identities = package["nativeIdentity"]
    if not isinstance(identities, list):
        raise fail("toolEvidence.nativeIdentity must be a list of root identities",
                   field="toolEvidence.nativeIdentity")
    # An empty list is a real fact: the existing collector publishes failure and
    # unknown packages before any native root was observed. Whether such a
    # package can pass is the role's and the board's judgment, never the
    # structure's.
    for identity in identities:
        if not isinstance(identity, dict) or not identity or set(identity) - set(NATIVE_ID_KEYS):
            raise fail("a tool evidence root identity is malformed", field="toolEvidence.nativeIdentity")
        for key, item in identity.items():
            check_text(item, f"toolEvidence.nativeIdentity.{key}", maximum=512)
    events = package["events"]
    if not isinstance(events, list) or len(events) > tool_evidence_protocol.MAX_TOOL_EVENTS:
        raise fail(f"toolEvidence.events must be a list of at most "
                   f"{tool_evidence_protocol.MAX_TOOL_EVENTS} events", field="toolEvidence.events")
    for event in events:
        if not isinstance(event, dict) or set(event) != set(tool_evidence_protocol.EVENT_FIELDS):
            raise fail("a tool evidence event must carry its exact field set", field="toolEvidence.events")
        # The projection's own incomplete facts are real evidence and stay
        # lossless here: an event identity may be empty and callId/toolName/
        # phase may be None when the native frame carried none of them. The
        # closed key set, the version and the category/phase enums are exactly
        # the collector's own; whether such an event may pass is judged only by
        # the blackboard.
        identity = event["nativeIdentity"]
        if not isinstance(identity, dict) or set(identity) - set(NATIVE_ID_KEYS):
            raise fail("a tool evidence event identity is malformed", field="toolEvidence.events")
        for key, item in identity.items():
            check_text(item, f"toolEvidence event {key}", maximum=512)
        for key in ("callId", "toolName", "phase"):
            if event[key] is not None:
                check_text(event[key], f"toolEvidence event {key}", maximum=512)
        if event["category"] not in tool_evidence_protocol.ACP_CATEGORIES:
            raise fail("a tool evidence event category is not an ACP category", field="toolEvidence.events")
        if event["phase"] is not None and event["phase"] not in tool_evidence_protocol.TOOL_EVENT_PHASES:
            raise fail("a tool evidence event phase must be start or end", field="toolEvidence.events")
    return FrozenJson.from_value(package, "toolEvidence", maximum=MAX_PACKAGE_BYTES)


def _native_error_record(value: Any) -> Optional[FrozenJson]:
    """One harness's own whitelisted native failure attribution, as JSON.

    This is a second, separate existing fact beside the quota attribution: the
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
QuotaPackage = Annotated[Optional[FrozenJson], BeforeValidator(partial(_normalized_package, "quota"))]
NativeFailurePackage = Annotated[Optional[FrozenJson],
                                 BeforeValidator(partial(_normalized_package, "nativeFailure"))]
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
    model_started: Optional[bool] = None
    model_start_evidence: ModelStartEvidence = Field(
        default_factory=lambda: ModelStartEvidence(basis="unknown"))
    configuration: ResultConfiguration = Field(default_factory=ResultConfiguration)
    native_identity: Optional[NativeIdentity] = None
    root_identities: JsonTuple(NativeIdentity, max_items=MAX_ROOT_IDENTITIES) = ()
    value: Optional[RunValue] = None
    completion_evidence: Optional[CompletionEvidence] = None
    tool_evidence: ToolEvidencePackage = None
    denied_interactions: JsonTuple(DeniedInteraction, max_items=MAX_DENIED_INTERACTIONS) = ()
    unknown_events: Optional[UnknownEvents] = None
    effective_policy: EffectivePolicy = Field(default_factory=EffectivePolicy)
    activity: ActivityPackage = None
    usage: UsagePackage = None
    quota: QuotaPackage = None
    native_failure: NativeFailurePackage = None
    native_error: NativeErrorRecord = None
    last_assistant_message: LastAssistantMessagePackage = None
    continuation: Optional[ContinuationFacts] = None
    stop_evidence: StopEvidence = Field(default_factory=StopEvidence)
    evidence_refs: JsonTuple(EvidenceRef, max_items=MAX_EVIDENCE_REFS) = ()


def encode_run_request(request: RunRequest) -> str:
    """Canonical bounded JSON text of one run request."""
    text = canonical_json(request.to_payload())
    if len(text.encode()) > MAX_RUN_REQUEST_BYTES:
        raise fail("the run request frame exceeds its byte bound", limit=MAX_RUN_REQUEST_BYTES)
    return text


def decode_run_request(value: str | bytes | Mapping) -> RunRequest:
    """Validate and unfreeze one run request from its internal JSON form."""
    return RunRequest.from_payload(
        decode_bounded_frame(value, label="run request", maximum=MAX_RUN_REQUEST_BYTES))


def encode_run_result(result: RunResult) -> str:
    """Canonical bounded JSON text of one run result."""
    text = canonical_json(result.to_payload())
    if len(text.encode()) > MAX_RUN_RESULT_BYTES:
        raise fail("the run result frame exceeds its byte bound", limit=MAX_RUN_RESULT_BYTES)
    return text


def decode_run_result(value: str | bytes | Mapping) -> RunResult:
    """Validate and unfreeze one run result from its internal JSON form."""
    return RunResult.from_payload(
        decode_bounded_frame(value, label="run result", maximum=MAX_RUN_RESULT_BYTES))


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
    the roles. ``cancelled`` is the outer cancel check. Model discovery is not
    part of this step's seam; the later ADR-025 steps place it through the Host.
    """

    def run(self, request: RunRequest, *, observer: Callable[[Mapping[str, Any]], RunFeedback],
            services: Any, cancelled: Callable[[], bool]) -> RunResult: ...


__all__ = [
    "CHECK_BASES", "CONTINUATION_MODES", "END_STATUSES", "FEEDBACK_CONTINUE", "FEEDBACK_STOP",
    "FORMAT_VERSION", "FrozenAccountReference",
    "FrozenJson", "GROUP_STATES", "HARNESS_NAMES", "HarnessRun", "MAX_RUN_REQUEST_BYTES",
    "MAX_RUN_RESULT_BYTES", "MODEL_START_BASES", "MAX_COUNT", "CheckedConfiguration", "CheckedValue",
    "CompletionEvidence", "ContinuationFacts", "DeniedInteraction", "EffectivePolicy", "EvidenceRef",
    "InterruptEvidence", "NativeIdentity", "NetworkPolicy", "POLICY_ENFORCEMENTS", "PrivateStatePaths",
    "RunBudget", "RunConfiguration", "RunContinuation", "RunEnd", "RunFeedback", "RunIdentity",
    "RunRequest", "RunResult", "RunValue", "SCHEMA_STATUSES", "SessionService", "StopEvidence",
    "StopLayer", "TOOL_SCOPES", "UnknownEvents", "VALUE_MECHANISMS", "canonical_json",
    "decode_run_request", "decode_run_result", "encode_run_request", "encode_run_result",
]
