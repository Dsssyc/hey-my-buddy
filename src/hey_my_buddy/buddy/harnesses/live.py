"""The one live interface of every harness and its existing-facilities adapter.

ADR-025 step 1-A (execution plan section 5). :class:`LiveChannel` is the single
seam for a run's live interaction: capabilities, one request, one observation,
one close. :class:`ExistingLiveChannel` is its only backend for steps one to
four: it adapts the existing activity sidecar, the existing inquiry bridges and
the existing durable inquiry journals through narrow read bindings. It never
upgrades a queued question into a delivered one, never invents a native turn,
and never touches the native deadline or result ownership: closing the channel
leaves the run's files and evidence exactly where they were. The one live
interaction of steps one to four is the inquiry: the reserved finish-notice
kind, its payload, the never-implemented capability bits and the reserved
event slot went with step 2-D, and ADR-022 and ADR-024 add what they need only
when a real implementation arrives. The C-Two transport of step five replaces
only what sits behind this interface. The frame models are described by
pydantic through :mod:`hey_my_buddy.protocol.internal_models` (ADR-025
decision 6).

Step 2-C2 wires the real ZCode consumers through this interface. ``observe``
selects its sources — ``activity``, ``inquiries``, ``observation``, all when
omitted — or answers one ``inquiry_id`` point query, so a worker activity poll
reads only the sidecar and an answer wait reads only that one native answer,
exactly the I/O the direct paths had. The observation carries the fields the
board's ``live`` view already reads, with the bridge's recent-event metadata
under ``recentActivity``; the normalized activity sidecar remains the
snapshot's ``activity`` field and is never replaced by event metadata.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Callable, Literal, Mapping, Optional, Protocol, runtime_checkable

from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol.activity import is_newer as _activity_is_newer
from ...protocol.internal_models import (
    FrozenJson,
    Identifier,
    InternalModel,
    JsonTuple,
    NonNegativeInt,
    OptionalFrozenJsonAt,
    OptionalText,
    Text,
    check_text,
    fail,
)
from .run_contract import RunIdentity
from pydantic import Field, model_validator

#: The existing inquiry limits, preserved verbatim: the same byte bounds, the
#: same per-run count and the same transport windows the current bridges enforce.
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES_PER_RUN = 32
MIN_TRANSPORT_TIMEOUT_MS = 100
MAX_TRANSPORT_TIMEOUT_MS = 5000
#: The suggested whole-frame bound across runs; it holds every bounded
#: observation of the existing backends and never widens their own smaller
#: per-bridge frame limits.
MAX_LIVE_FRAME_BYTES = 64 * 1024
MAX_REQUEST_ID = 128
#: Draft bound of one ``observe`` page; the 64 KiB frame bound stays in force.
MAX_OBSERVE_LIMIT = 256
MAX_CLOSE_REASON_BYTES = 200

LiveKind = Literal["inquiry"]
InquiryDeliveryMode = Literal["realtime", "cooperative-checkpoint", "unsupported"]
LiveReplyStatus = Literal["queued", "delivered", "answered", "discarded", "unavailable", "unsupported"]
ReplyState = Literal["queued", "claimed", "delivered", "answered", "discarded"]
InquiryStateValue = Literal["queued", "delivered", "answered", "discarded", "unavailable", "unknown"]


def _timeout_ms(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_TRANSPORT_TIMEOUT_MS <= value <= MAX_TRANSPORT_TIMEOUT_MS:
        raise fail(f"{label} must be an integer between {MIN_TRANSPORT_TIMEOUT_MS} and "
                   f"{MAX_TRANSPORT_TIMEOUT_MS}", field=label)
    return value


class LiveCapabilities(InternalModel):
    """What one harness's live facilities can do, declared as facts.

    The one live interaction of steps one to four is the inquiry, and the one
    declared fact is how its question is delivered. A later step adds
    capability bits — ADR-022 and ADR-024 included — only when a real
    implementation arrives; the existing activity reading is a facility of the
    channel bindings, not an unread declaration bit.
    """

    inquiry_delivery: InquiryDeliveryMode


#: What each harness's existing facilities can do today. Codex and Claude Code
#: refuse questions; ZCode delivers them at its session's cooperative
#: checkpoint; DSH keeps its current Node realtime delivery until step four
#: moves it to ACP.
EXISTING_CAPABILITIES = {
    "codex": LiveCapabilities(inquiry_delivery="unsupported"),
    "claude": LiveCapabilities(inquiry_delivery="unsupported"),
    "zcode": LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
    "dsh": LiveCapabilities(inquiry_delivery="realtime"),
}


class InquiryPayload(InternalModel):
    """One Host question; the only live payload with existing native facilities."""

    question_id: Identifier
    question: Text(MAX_QUESTION_BYTES)


class LiveRequest(InternalModel):
    """One live request, bound to the execution identity and its request id."""

    identity: RunIdentity
    request_id: Identifier
    kind: LiveKind
    payload: InquiryPayload


#: A bridge state maps onto a reply status without any upgrade: only the
#: bridge's own committed, journal-backed state can report ``delivered``.
_REPLY_STATE_STATUSES = {"queued": "queued", "claimed": "queued", "delivered": "delivered",
                         "answered": "answered", "discarded": "discarded"}

#: The statuses that mean the bridge interaction itself was observed.
_OBSERVED_STATUSES = ("queued", "delivered", "answered", "discarded")


class LiveReply(InternalModel):
    """One live request's reply fact; ``queued`` is never reported as delivered.

    The reply answers the request the caller still holds, so it carries only
    the facts: status, the transport/committed distinction, the bridge's
    correlation and the refusal reasons. The transport facts are independent
    of the committed inquiry state: a withdrawn question's replay is an
    ``observed`` success whose actual state is ``discarded`` — not an
    unavailable transport (step 2-C2). ``observed`` must agree with the
    status, ``state`` carries the bridge's own committed word and only rides
    an observed interaction, and ``error_code`` is the peer's own refusal code
    from the bridge's ``BRIDGE_ERRORS`` vocabulary when the reason is the
    transport classification of a refused ask.
    """

    status: LiveReplyStatus
    observed: bool = False
    state: Optional[ReplyState] = None
    native_correlation: OptionalFrozenJsonAt(MAX_LIVE_FRAME_BYTES // 2) = None
    reason_code: OptionalText(128) = None
    error_code: OptionalText(128) = None

    @model_validator(mode="after")
    def _observed_agrees_with_status(self) -> "LiveReply":
        if self.observed != (self.status in _OBSERVED_STATUSES):
            raise fail("observed must agree with the reply status", field="observed")
        if self.state is not None and not self.observed:
            raise fail("a state projection needs an observed interaction", field="state")
        return self


class InquiryState(InternalModel):
    """One question's committed state as the durable journal holds it.

    ``seq`` is the draft paging position this interface assigns: each entry
    keeps the sequence number it was created with and takes a fresh, higher one
    whenever its state changes, so an observer that has read up to ``after_seq``
    can fetch exactly the entries it has not seen yet.

    The answer's and the delivery record's own source facts ride beside the
    text exactly as the producing bridge wrote them — the byte count, the reply
    or checkpoint tool evidence, the timestamp, the truncation flag, the
    bridge's own bounded reason and its delivery record. A field the record
    never carried stays ``None``; nothing here derives or backfills one.
    """

    question_id: Identifier
    status: InquiryStateValue
    answer: OptionalText(MAX_ANSWER_BYTES) = None
    seq: NonNegativeInt = 0
    bytes: Optional[NonNegativeInt] = None
    via: OptionalText(512) = None
    tool_call_id: OptionalText(512) = None
    at: OptionalText(64) = None
    truncated: Optional[bool] = None
    reason: OptionalText(400) = None
    limitation: OptionalText(2048) = None
    delivery: OptionalFrozenJsonAt(1024) = None

    def _carried_facts(self) -> tuple:
        """Everything whose change takes a fresh sequence, ``seq`` itself aside."""
        return (self.status, self.answer, self.bytes, self.via, self.tool_call_id,
                self.at, self.truncated, self.reason, self.limitation, self.delivery)


class LiveEvent(InternalModel):
    """One bounded, metadata-only native event the existing bridge keeps.

    The bounds are the producing bridge's own truncations, kept as character
    bounds exactly where it truncates characters: ``kind`` is cut at 80
    characters and ``toolName`` at 120. Only event metadata is carried — never
    tool arguments or output.
    """

    at: Text(64)
    kind: Annotated[str, Field(min_length=1, max_length=80)]
    tool_name: Annotated[Optional[str], Field(min_length=1, max_length=120)] = None


class LiveObservation(InternalModel):
    """The bridge's own live observation, in the board ``live`` view's fields.

    These are exactly the fields the existing public ``live`` projection
    consumes (step 2-C2); the small sanitized metadata objects keep their
    bounded JSON form. The recent-event metadata is ``recentActivity`` — the
    normalized activity sidecar remains the snapshot's ``activity`` field and
    is never replaced by this metadata. A field the native bridge does not
    report stays absent or ``None``; nothing is fabricated.
    """

    ready: bool
    observed_at: OptionalText(64) = None
    session_id: OptionalText(512) = None
    agent_status: OptionalText(32) = None
    inbox: OptionalFrozenJsonAt(1024) = None
    last_event: Optional[LiveEvent] = None
    recent_activity: JsonTuple(LiveEvent, max_items=20) = ()
    activity_dropped: NonNegativeInt = 0
    reply_tool: OptionalFrozenJsonAt(1024) = None
    capability: OptionalText(32) = None
    supported: Optional[bool] = None
    attention: OptionalFrozenJsonAt(4096) = None
    journal: OptionalFrozenJsonAt(1024) = None
    delivery_mode: Optional[InquiryDeliveryMode] = None
    limitation: OptionalText(2048) = None
    unavailable: JsonTuple(Text(64), max_items=16) = ()
    error: OptionalText(512) = None


#: The observation sources one ``observe`` may select; an omitted selection
#: reads them all (step 2-C2).
LIVE_FIELDS = ("activity", "inquiries", "observation")

#: The journal availability reasons the existing direct reader published, kept
#: verbatim so the channel's projection stays lossless against it.
JOURNAL_REASONS = ("no-journal-path", "journal-not-written", "journal-exceeds-limit",
                   "journal-unreadable", "journal-unavailable")

JournalReason = Literal["no-journal-path", "journal-not-written", "journal-exceeds-limit",
                        "journal-unreadable", "journal-unavailable"]


class InquiryJournalRejection(InternalModel):
    """One question's journal record the identity binding refused, and why.

    The reason is the public string the direct reader's own mismatch check
    produced; nothing here invents a different vocabulary.
    """

    question_id: Identifier
    reason: Text(128)


class LiveJournal(InternalModel):
    """One journal projection's availability fact (step 2-C2).

    ``entries`` is the producing reader's own deduplicated count over every
    well-formed record — rejected ones included — exactly the count the direct
    file reader always published. ``rejections`` names the questions whose
    records the version/identity binding refused, so a foreign record surfaces
    as the public fact it always was instead of a silent loss. The raw records
    never travel here; only the bound records are projected as inquiry states.
    """

    available: bool
    reason: Optional[JournalReason] = None
    entries: NonNegativeInt = 0
    rejections: JsonTuple(InquiryJournalRejection, max_items=MAX_INQUIRIES_PER_RUN) = ()


def _observe_fields(fields: Any) -> frozenset:
    if fields is None:
        return frozenset(LIVE_FIELDS)
    if isinstance(fields, str) or not isinstance(fields, (list, tuple, set, frozenset)):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    selected = frozenset(fields)
    if not selected <= frozenset(LIVE_FIELDS):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    return selected or frozenset(LIVE_FIELDS)


class LiveSnapshot(InternalModel):
    """One observation of the run's live facts, bounded to one frame.

    Paging runs over the entries' own ``seq`` beside ``truncated``: an
    observer continues with ``after_seq`` = the highest returned entry
    ``seq`` and never skips an undelivered fact; the channel's watermark
    stays internal to that mechanism.
    """

    activity: OptionalFrozenJsonAt(MAX_LIVE_FRAME_BYTES // 2) = None
    inquiries: JsonTuple(InquiryState, max_items=MAX_INQUIRIES_PER_RUN) = ()
    observation: Optional[LiveObservation] = None
    journal: Optional[LiveJournal] = None
    unavailable: OptionalText(128) = None
    truncated: bool = False
    #: The shared transport facts of this snapshot's reads: ``observed`` is
    #: whether the requested native observation read succeeded (``None`` when
    #: the field selection did not read one), ``reason`` the failed read's
    #: transport classification and ``error`` the peer's specific refusal code.
    observed: Optional[bool] = None
    reason: OptionalText(128) = None
    error: OptionalText(128) = None


@runtime_checkable
class LiveChannel(Protocol):
    """The one live interface of every harness (ADR-025 execution plan §5).

    ``identity`` is the complete execution identity the channel is bound to —
    the real run request's own, never a fabricated one (step 2-C2).
    """

    @property
    def identity(self) -> RunIdentity: ...

    def capabilities(self) -> LiveCapabilities: ...

    def request(self, request: LiveRequest, *, timeout_ms: int) -> LiveReply: ...

    def observe(self, *, after_seq: int | None = None, limit: int | None = None, timeout_ms: int,
                fields: Any = None, inquiry_id: str | None = None) -> LiveSnapshot: ...

    def close(self, *, reason: str) -> None: ...


class ExistingLiveChannel:
    """The steps-one-to-four live backend over the existing facilities.

    The narrow bindings are the whole adapter surface: ``read_activity`` returns
    one validated activity payload (or ``None``), ``ask`` queues one question
    through the existing inquiry bridge within the requested transport window
    (``ask(question_id, question, timeout_ms)``) and returns its committed
    reply value — or the shared bridge transport's result fact, whose
    ``{"ok": False}`` refusals keep their reason and specific code — ``read_journal``
    returns the durable inquiry journal's records, ``read_observation(timeout_ms)``
    returns the bridge's own observation value (or its transport result fact)
    and ``read_answer(inquiry_id, timeout_ms)`` one native answer view. Nothing
    here starts a native turn, holds a process handle or touches a deadline;
    closing the channel changes no result ownership.

    Requests are bound by identity and ``requestId`` over the full
    kind/payload pair: the same request again returns its recorded reply
    without reaching the bridge, a different payload under a committed
    ``requestId`` is refused before the bridge is called, and only an accepted
    ask is remembered — a refused one stays retryable, exactly as the existing
    bridges allow.
    """

    def __init__(self, identity: RunIdentity, capabilities: LiveCapabilities, *,
                 read_activity: Callable[[], Mapping[str, Any] | None] | None = None,
                 ask: Callable[[str, str, int], Mapping[str, Any]] | None = None,
                 read_journal: Callable[[], list[Mapping[str, Any]]] | None = None,
                 read_observation: Callable[[int], Any] | None = None,
                 read_answer: Callable[[str, int], Any] | None = None):
        if not isinstance(identity, RunIdentity):
            raise fail("identity must be a RunIdentity")
        if not isinstance(capabilities, LiveCapabilities):
            raise fail("capabilities must be a LiveCapabilities value")
        self._identity = identity
        self._capabilities = capabilities
        self._read_activity = read_activity
        self._ask = ask
        self._read_journal = read_journal
        self._read_observation = read_observation
        self._read_answer = read_answer
        self._sequence = 0
        self._next_seq = 1
        self._activity: FrozenJson | None = None
        self._entries: tuple[InquiryState, ...] = ()
        self._requests: dict[str, str] = {}
        self._replies: dict[str, LiveReply] = {}
        self._closed_reason: str | None = None

    @property
    def identity(self) -> RunIdentity:
        """The complete execution identity this channel is bound to."""
        return self._identity

    def capabilities(self) -> LiveCapabilities:
        return self._capabilities

    def request(self, request: LiveRequest, *, timeout_ms: int) -> LiveReply:
        _timeout_ms(timeout_ms, "timeoutMs")
        if self._closed_reason is not None:
            return self._unavailable(request.request_id, "channel-closed")
        if request.identity != self._identity:
            return self._unavailable(request.request_id, "identity-mismatch")
        digest = hashlib.sha256(canonical_json(
            {"kind": request.kind, "payload": request.payload.to_payload()}).encode()).hexdigest()
        previous = self._requests.get(request.request_id)
        if previous is not None and previous != digest:
            # A changed kind or payload under a committed requestId is refused
            # before the bridge is ever called again.
            return self._unavailable(request.request_id, "request-payload-conflict")
        recorded = self._replies.get(request.request_id)
        if recorded is not None:
            # The identical request replays its recorded reply without a
            # second delivery.
            return recorded
        if self._capabilities.inquiry_delivery == "unsupported":
            return LiveReply(status="unsupported", reason_code="inquiry-unsupported")
        if self._ask is None:
            # The harness supports inquiries but this channel carries no wired
            # bridge binding: an availability fact, not a capability fact.
            return self._unavailable(request.request_id, "inquiry-binding-unavailable")
        try:
            value = self._ask(request.payload.question_id, request.payload.question, timeout_ms)
        except BoardError as error:
            # A refused ask is never remembered: the asking side may retry the
            # identical request, exactly as the existing bridges allow.
            return self._unavailable(request.request_id, str(error.code)[:64])
        if isinstance(value, Mapping) and value.get("ok") is False:
            # The shared transport's refusal fact: the transport classification
            # and the peer's own specific code are carried beside each other
            # instead of the code being folded into one generic refusal.
            return self._unavailable(
                request.request_id,
                check_text(str(value.get("reason") or "bridge-write-failed"), "bridge reason", maximum=64),
                error_code=value.get("code") if isinstance(value.get("code"), str) else None)
        if isinstance(value, Mapping) and value.get("ok") is True:
            value = value.get("value")
        state = value.get("state") if isinstance(value, dict) else None
        accepted = isinstance(value, dict) and value.get("accepted") is True
        if not accepted:
            reason = value.get("reason") if isinstance(value, dict) else None
            return self._unavailable(request.request_id, check_text(
                str(reason or "not-accepted"), "bridge reason", maximum=64))
        status = _REPLY_STATE_STATUSES.get(state)
        if status is None:
            if state in (None, "queued"):
                status = "queued"
            else:
                return self._unavailable(request.request_id, "unknown-bridge-state")
        correlation = {key: value[key] for key in ("inquiryId", "questionSha256", "state", "duplicate",
                                                   "delivery", "reason")
                       if isinstance(value, dict) and value.get(key) is not None}
        # The interaction was observed whatever committed state it showed: a
        # withdrawn question's replay carries ``discarded`` as a success fact,
        # never as a transport refusal.
        reply = LiveReply(status=status, observed=True, state=state,
                          native_correlation=correlation or None)
        self._requests[request.request_id] = digest
        self._replies[request.request_id] = reply
        return reply

    def observe(self, *, after_seq: int | None = None, limit: int | None = None, timeout_ms: int,
                fields: Any = None, inquiry_id: str | None = None) -> LiveSnapshot:
        """One bounded page of the run's live facts, over the selected sources.

        With ``fields`` the caller selects which sources this read touches —
        ``activity``, ``inquiries``, ``observation`` — and an omitted selection
        reads them all, as every step-one caller did. With ``inquiry_id`` the
        read is a point query for that one question's native answer and nothing
        else is read, so an answer wait costs exactly the one socket roundtrip
        the direct path cost (step 2-C2).

        Entries carry their own ``seq``; a page holds the entries after
        ``after_seq`` up to ``limit`` and up to what one frame holds. When
        ``truncated`` is set, the caller continues with
        ``after_seq`` = the highest returned entry ``seq``, which yields the
        remaining facts without loss; the 64 KiB frame never silently drops an
        entry because pages can always continue.
        """
        _timeout_ms(timeout_ms, "timeoutMs")
        selected = _observe_fields(fields)
        if inquiry_id is not None:
            return self._observe_answer(inquiry_id, timeout_ms)
        if after_seq is not None and (isinstance(after_seq, bool) or not isinstance(after_seq, int)
                                      or after_seq < 0):
            raise fail("afterSeq must be null or a nonnegative integer", field="afterSeq")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OBSERVE_LIMIT:
            raise fail(f"limit must be an integer between 1 and {MAX_OBSERVE_LIMIT}", field="limit")
        unavailable: list[str] = []
        observed: bool | None = None
        reason: str | None = None
        error: str | None = None
        observation: LiveObservation | None = None
        journal: LiveJournal | None = None
        if self._closed_reason is not None:
            unavailable.append("channel-closed")
        if "activity" in selected and self._read_activity is not None and self._closed_reason is None:
            try:
                payload = self._read_activity()
            except BoardError:
                unavailable.append("activity-unavailable")
            else:
                if isinstance(payload, dict) and (self._activity is None
                                                  or _activity_is_newer(payload, self._activity.value)):
                    self._activity = FrozenJson(payload)
                    self._sequence = self._next_seq
                    self._next_seq += 1
        if "inquiries" in selected and self._read_journal is not None and self._closed_reason is None:
            try:
                journal = self._read_journal_fact()
            except BoardError:
                unavailable.append("journal-unavailable")
        if "observation" in selected and self._closed_reason is None:
            if self._read_observation is None:
                observed, reason = False, "observation-unavailable"
            else:
                observed, reason, error, observation = self._read_observation_facts(timeout_ms)
        # Page the unseen entries: every page fits one frame with the metadata
        # it will actually return — the journal fact and the observation ride
        # in the candidate, so a page is never encoded over the bound it was
        # measured without (step 2-C2) — and what does not fit stays reachable
        # through the next after_seq instead of being dropped.
        matching = [entry for entry in self._entries
                    if after_seq is None or entry.seq > after_seq]
        page: list[InquiryState] = []
        for entry in matching:
            if len(page) >= limit:
                break
            candidate = LiveSnapshot(activity=self._activity,
                                     inquiries=tuple(page + [entry]),
                                     observation=observation, journal=journal)
            if page:
                try:
                    _bounded_frame(candidate.to_payload(), "live page")
                except BoardError:
                    break
            page.append(entry)
        truncated = len(page) < len(matching)
        # A caller pages by the entries' own ``seq`` beside ``truncated``: a
        # truncated page is continued with ``after_seq`` = the highest
        # returned entry seq, which reaches every fact without loss.
        return LiveSnapshot(activity=self._activity, inquiries=tuple(page),
                            observation=observation, journal=journal,
                            unavailable="+".join(unavailable) or None,
                            truncated=truncated, observed=observed, reason=reason, error=error)

    def _read_journal_fact(self) -> LiveJournal:
        """One journal read folded into entries, returned as its availability fact.

        The binding returns the fact mapping — availability, reason, the
        reader's own deduplicated record count and the per-question rejections
        its identity binding produced, exactly the shape the real bindings
        write. The bound records merge into the entries exactly as before; only
        the availability fact rides beside them.
        """
        fact = self._read_journal()
        if not (isinstance(fact, Mapping) and "records" in fact):
            raise fail("the inquiry journal binding must return its availability fact")
        records = fact.get("records")
        if not isinstance(records, list):
            raise fail("the inquiry journal fact's records must be a list")
        self._merge_journal(_journal_states(records))
        raw_rejections = fact.get("rejections")
        if raw_rejections is None:
            raw_rejections = []
        if not isinstance(raw_rejections, list):
            raise fail("the inquiry journal fact's rejections must be a list")
        rejections = tuple(InquiryJournalRejection(question_id=item["questionId"], reason=item["reason"])
                           if isinstance(item, dict) else fail("a journal rejection must be an object")
                           for item in raw_rejections)
        entries = fact.get("entries")
        if isinstance(entries, bool) or not isinstance(entries, int) or entries < 0:
            raise fail("the inquiry journal fact's entries must be a nonnegative integer",
                       field="entries")
        reason = fact.get("reason")
        if reason is not None and reason not in JOURNAL_REASONS:
            raise fail("the inquiry journal fact carries an unknown availability reason",
                       field="reason")
        return LiveJournal(available=fact.get("available") is True,
                           reason=reason, entries=entries, rejections=rejections)

    def _read_observation_facts(self, timeout_ms: int) -> tuple[bool, str | None, str | None,
                                                                LiveObservation | None]:
        """One observation read and its shared transport facts.

        A refused or failed read is a fact, never an invented observation: the
        transport classification lands in ``reason`` and the peer's own code in
        ``error``; a read whose value the strict model refuses is reported as
        ``observation-unavailable`` instead of being reshaped into look-alike
        metadata.
        """
        try:
            result = self._read_observation(timeout_ms)
        except BoardError:
            return False, "observation-unavailable", None, None
        if isinstance(result, Mapping) and result.get("ok") is False:
            code = result.get("code")
            return (False,
                    check_text(str(result.get("reason") or "bridge-write-failed"), "bridge reason", maximum=128),
                    code if isinstance(code, str) and code else None,
                    None)
        value = result.get("value") if isinstance(result, Mapping) and result.get("ok") is True else result
        if not isinstance(value, Mapping):
            return False, "observation-unavailable", None, None
        try:
            return True, None, None, LiveObservation.from_payload(dict(value))
        except BoardError:
            return False, "observation-unavailable", None, None

    def _observe_answer(self, inquiry_id: str, timeout_ms: int) -> LiveSnapshot:
        """One native answer point query: that inquiry only, nothing else read."""
        if len(inquiry_id.encode()) > MAX_REQUEST_ID or not inquiry_id:
            raise fail("inquiryId must be a nonempty string of at most "
                       f"{MAX_REQUEST_ID} bytes", field="inquiryId")
        if self._closed_reason is not None:
            return LiveSnapshot(unavailable="channel-closed")
        if self._read_answer is None:
            return LiveSnapshot(observed=False, reason="answer-unavailable")
        try:
            result = self._read_answer(inquiry_id, timeout_ms)
        except BoardError:
            return LiveSnapshot(observed=False, reason="answer-unavailable")
        if isinstance(result, Mapping) and result.get("ok") is False:
            code = result.get("code")
            return LiveSnapshot(observed=False,
                                reason=check_text(str(result.get("reason") or "bridge-write-failed"),
                                                  "bridge reason", maximum=128),
                                error=code if isinstance(code, str) and code else None)
        value = result.get("value") if isinstance(result, Mapping) and result.get("ok") is True else result
        return LiveSnapshot(inquiries=(_answer_state(inquiry_id, value),), observed=True)

    def _merge_journal(self, states: tuple[InquiryState, ...]) -> None:
        """Fold deduplicated journal states into entries with stable seqs.

        A question keeps its sequence while every carried fact is unchanged and
        takes a fresh, higher one whenever any of them changes, so observers
        that have read up to a sequence see exactly the changes after it.
        """
        replaced: list[InquiryState] = []
        changed = False
        previous = {entry.question_id: entry for entry in self._entries}
        for state in states:
            old = previous.get(state.question_id)
            if old is not None and old._carried_facts() == state._carried_facts():
                replaced.append(old)
                continue
            replaced.append(InquiryState(question_id=state.question_id, status=state.status,
                                         answer=state.answer, bytes=state.bytes, via=state.via,
                                         tool_call_id=state.tool_call_id, at=state.at,
                                         truncated=state.truncated, reason=state.reason,
                                         limitation=state.limitation, delivery=state.delivery,
                                         seq=self._next_seq))
            self._next_seq += 1
            changed = True
        if changed:
            replaced.sort(key=lambda entry: entry.seq)
            self._entries = tuple(replaced)
            self._sequence = self._next_seq - 1

    def close(self, *, reason: str) -> None:
        check_text(reason, "reason", maximum=MAX_CLOSE_REASON_BYTES)
        if self._closed_reason is None:
            self._closed_reason = reason

    def _unavailable(self, request_id: str, reason_code: str, *, error_code: str | None = None) -> LiveReply:
        return LiveReply(status="unavailable", reason_code=reason_code, error_code=error_code)


#: Journal record states map onto the closed snapshot set; an unrecognized state
#: stays ``unknown`` instead of being silently promoted.
_JOURNAL_STATE_STATUSES = {"queued": "queued", "claimed": "queued", "delivered": "delivered",
                           "answered": "answered", "discarded": "discarded", "unavailable": "unavailable"}


def _bounded_bytes(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _bounded_word(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _record_facts(entry: dict) -> dict:
    """One journal record's carried facts, exactly as the record states them.

    The answer's own source fields come from the answer object when the record
    carries one and from the record's own sibling fields when it carries a bare
    answer text — the two shapes the existing bridges write. Anything the
    record never stated stays ``None``.
    """
    answer = entry.get("answer")
    nested = isinstance(answer, dict)
    source = answer if nested else entry
    text = answer.get("text") if nested else answer
    facts: dict[str, Any] = {
        "answer": text if isinstance(text, str) and text else None,
        "bytes": _bounded_bytes(source.get("bytes") if nested else entry.get("answerBytes")),
        "via": _bounded_word(source.get("via")),
        "tool_call_id": _bounded_word(source.get("toolCallId")),
        "at": _bounded_word(source.get("at") if nested else entry.get("answeredAt")),
        "truncated": (source.get("truncated") if isinstance(source.get("truncated"), bool)
                      else (entry.get("truncated") is True if not nested else None)),
        "reason": _bounded_word(entry.get("reason")),
        "limitation": _bounded_word(entry.get("limitation")),
        "delivery": entry.get("delivery") if isinstance(entry.get("delivery"), dict) else None,
    }
    return facts


def _journal_states(entries: Any) -> tuple[InquiryState, ...]:
    """Deduplicate journal records into one state per question, last record wins.

    A later record overwrites what it carries; a source field it does not
    carry keeps the earlier record's value, the way the producing bridge merges
    its own entries. Only whole facts move forward — nothing is inferred.
    """
    if not isinstance(entries, list):
        raise fail("the inquiry journal binding must return a list of records")
    states: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise fail("an inquiry journal record must be an object")
        question_id = entry.get("inquiryId") or entry.get("questionId") or entry.get("id")
        state = entry.get("state") or entry.get("status")
        if not isinstance(question_id, str) or not question_id or len(question_id.encode()) > MAX_REQUEST_ID:
            raise fail("an inquiry journal record carries no bounded question id")
        status = _JOURNAL_STATE_STATUSES.get(state, "unknown")
        if question_id not in states:
            order.append(question_id)
            states[question_id] = {"status": status, **_record_facts(entry)}
            continue
        merged = {"status": status, **_record_facts(entry)}
        for key, value in states[question_id].items():
            if key != "status" and merged.get(key) is None:
                merged[key] = value
        states[question_id] = merged
    if len(states) > MAX_INQUIRIES_PER_RUN:
        raise fail("the inquiry journal carries more than "
                    f"{MAX_INQUIRIES_PER_RUN} questions")
    return tuple(InquiryState(question_id=question_id, **states[question_id])
                 for question_id in order)


def _answer_state(inquiry_id: str, value: Any) -> InquiryState:
    """One native answer view mapped onto the closed inquiry-state set.

    The existing ``answer`` binding's value shape is kept: an available answer
    carries its text with its own source fields, an unavailable one the
    bridge's reason. An unrecognized state stays ``unknown``.
    """
    state = value.get("state") if isinstance(value, dict) else None
    status = _JOURNAL_STATE_STATUSES.get(state, "unknown")
    answer = value.get("answer") if isinstance(value, dict) else None
    fields: dict[str, Any] = {"answer": None, "bytes": None, "via": None, "tool_call_id": None,
                              "at": None, "truncated": None, "reason": None}
    if isinstance(answer, dict):
        text = answer.get("text")
        if isinstance(text, str) and text and answer.get("available") is not False:
            fields["answer"] = text
            fields["bytes"] = _bounded_bytes(answer.get("bytes"))
            fields["via"] = _bounded_word(answer.get("via"))
            fields["tool_call_id"] = _bounded_word(answer.get("toolCallId"))
            fields["at"] = _bounded_word(answer.get("at"))
            fields["truncated"] = answer.get("truncated") if isinstance(answer.get("truncated"), bool) else None
        else:
            fields["reason"] = _bounded_word(answer.get("reason"))
    return InquiryState(question_id=inquiry_id, status=status, **fields)


def _bounded_frame(payload: dict, label: str) -> str:
    """The production paging bound: canonical text of one candidate page,
    refused over the 64 KiB frame instead of trimmed (called by
    :meth:`ExistingLiveChannel.observe` while measuring every page)."""
    text = canonical_json(payload)
    if len(text.encode()) > MAX_LIVE_FRAME_BYTES:
        raise fail(f"the {label} frame exceeds its {MAX_LIVE_FRAME_BYTES}-byte bound", field=label)
    return text


__all__ = [
    "EXISTING_CAPABILITIES", "JOURNAL_REASONS",
    "LIVE_FIELDS", "LiveCapabilities",
    "LiveChannel", "ExistingLiveChannel", "InquiryJournalRejection",
    "InquiryPayload", "InquiryState", "LiveEvent", "LiveJournal", "LiveObservation", "LiveReply",
    "LiveRequest", "LiveSnapshot",
    "MAX_ANSWER_BYTES", "MAX_INQUIRIES_PER_RUN", "MAX_LIVE_FRAME_BYTES", "MAX_QUESTION_BYTES",
    "MAX_TRANSPORT_TIMEOUT_MS", "MIN_TRANSPORT_TIMEOUT_MS",
]
