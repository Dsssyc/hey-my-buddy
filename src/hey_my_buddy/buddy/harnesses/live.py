"""The one live interface of every harness and its existing-facilities adapter.

ADR-025 step 1-A (execution plan section 5). :class:`LiveChannel` is the single
seam for a run's live interaction: capabilities, one request, one observation,
one close. :class:`ExistingLiveChannel` is its only backend for steps one to
four: it adapts the existing activity sidecar, the existing inquiry bridges and
the existing durable inquiry journals through narrow read bindings. It never
upgrades a queued question into a delivered one, never invents a native turn,
and never touches the native deadline or result ownership: closing the channel
leaves the run's files and evidence exactly where they were. ``finishNotice``
and ``sessionContent`` are declared unsupported everywhere; ADR-022 and ADR-024
come later. The C-Two transport of step five replaces only what sits behind
this interface. The frame models are described by pydantic through
:mod:`hey_my_buddy.protocol.internal_models` (ADR-025 decision 6).
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Callable, Literal, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable

from ...errors import BoardError
from ...json_codec import canonical_json, decode_bounded_frame
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
from .run_contract import FORMAT_VERSION, RunIdentity
from pydantic import BeforeValidator, model_validator

#: The only live frame format version; it never changes a public contract.
LIVE_FORMAT_VERSION = FORMAT_VERSION

LIVE_KINDS = ("inquiry", "finish-notice")
INQUIRY_DELIVERY_MODES = ("realtime", "cooperative-checkpoint", "unsupported")
LIVE_REPLY_STATUSES = ("queued", "delivered", "answered", "unavailable", "unsupported")
#: The closed set of inquiry states a snapshot may report. ``unknown`` is an
#: honest state for a journal record this interface does not recognize.
INQUIRY_STATES = ("queued", "delivered", "answered", "discarded", "unavailable", "unknown")

#: The existing inquiry limits, preserved verbatim: the same byte bounds, the
#: same per-run count and the same transport windows the current bridges enforce.
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES_PER_RUN = 32
MIN_TRANSPORT_TIMEOUT_MS = 100
MAX_TRANSPORT_TIMEOUT_MS = 5000
MAX_WAIT_MS = 30000
#: The suggested whole-frame bound across runs; it holds every bounded
#: observation of the existing backends and never widens their own smaller
#: per-bridge frame limits.
MAX_LIVE_FRAME_BYTES = 64 * 1024
MAX_REQUEST_ID = 128
#: Draft bound of one ``observe`` page; the 64 KiB frame bound stays in force.
MAX_OBSERVE_LIMIT = 256
MAX_CLOSE_REASON_BYTES = 200

LiveKind = Literal["inquiry", "finish-notice"]
InquiryDeliveryMode = Literal["realtime", "cooperative-checkpoint", "unsupported"]
LiveReplyStatus = Literal["queued", "delivered", "answered", "unavailable", "unsupported"]
InquiryStateValue = Literal["queued", "delivered", "answered", "discarded", "unavailable", "unknown"]


def _timeout_ms(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_TRANSPORT_TIMEOUT_MS <= value <= MAX_TRANSPORT_TIMEOUT_MS:
        raise fail(f"{label} must be an integer between {MIN_TRANSPORT_TIMEOUT_MS} and "
                   f"{MAX_TRANSPORT_TIMEOUT_MS}", field=label)
    return value


class LiveCapabilities(InternalModel):
    """What one harness's live facilities can do, declared as facts."""

    activity: bool
    inquiry_delivery: InquiryDeliveryMode
    finish_notice: bool = False
    session_content: bool = False


#: What each harness's existing facilities can do today. Codex and Claude Code
#: carry activity only and refuse questions; ZCode delivers at its session's
#: cooperative checkpoint; DSH keeps its current Node realtime delivery until
#: step four moves it to ACP. No harness implements finish notices or session
#: content yet.
EXISTING_CAPABILITIES = {
    "codex": LiveCapabilities(activity=True, inquiry_delivery="unsupported"),
    "claude": LiveCapabilities(activity=True, inquiry_delivery="unsupported"),
    "zcode": LiveCapabilities(activity=True, inquiry_delivery="cooperative-checkpoint"),
    "dsh": LiveCapabilities(activity=True, inquiry_delivery="realtime"),
}


class InquiryPayload(InternalModel):
    """One Host question; the only live payload with existing native facilities."""

    question_id: Identifier
    question: Text(MAX_QUESTION_BYTES)


class FinishNoticePayload(InternalModel):
    """One reserved finish-notice payload; no existing facility accepts one."""

    notice_id: Identifier
    message: Text(MAX_QUESTION_BYTES)


class LiveRequest(InternalModel):
    """One live request, bound to the execution identity and its request id."""

    WIRE_FORMAT_VERSION = LIVE_FORMAT_VERSION

    identity: RunIdentity
    request_id: Identifier
    kind: LiveKind
    payload: Union[InquiryPayload, FinishNoticePayload]

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> "LiveRequest":
        if self.kind == "inquiry" and not isinstance(self.payload, InquiryPayload):
            raise fail("an inquiry request carries an InquiryPayload", field="payload")
        if self.kind == "finish-notice" and not isinstance(self.payload, FinishNoticePayload):
            raise fail("a finish-notice request carries a FinishNoticePayload", field="payload")
        return self


class LiveReply(InternalModel):
    """One live request's reply fact; ``queued`` is never reported as delivered."""

    WIRE_FORMAT_VERSION = LIVE_FORMAT_VERSION

    identity: RunIdentity
    request_id: Identifier
    status: LiveReplyStatus
    delivery_mode: Optional[InquiryDeliveryMode] = None
    native_correlation: OptionalFrozenJsonAt(MAX_LIVE_FRAME_BYTES // 2) = None
    reason_code: OptionalText(128) = None


class InquiryState(InternalModel):
    """One question's committed state as the durable journal holds it.

    ``seq`` is the draft paging position this interface assigns: each entry
    keeps the sequence number it was created with and takes a fresh, higher one
    whenever its state changes, so an observer that has read up to ``after_seq``
    can fetch exactly the entries it has not seen yet.
    """

    question_id: Identifier
    status: InquiryStateValue
    answer: OptionalText(MAX_ANSWER_BYTES) = None
    seq: NonNegativeInt = 0


def _no_events(value: Any) -> Any:
    """The reserved session-event slot admits no content type in this step
    (ADR-024 comes later): capacity exists, admission is closed, so any
    non-empty value is refused instead of being waved through."""
    items = tuple(value) if isinstance(value, (list, tuple)) else value
    if items == ():
        return ()
    raise fail("session events are reserved and this step admits no event content types", field="events")


#: The reserved event slot: empty on the wire is the only legal value.
ReservedEvents = Annotated[Tuple[FrozenJson, ...], BeforeValidator(_no_events)]


class LiveSnapshot(InternalModel):
    """One observation of the run's live facts, bounded to one frame.

    ``sequence`` is the safe cursor of this page: on a truncated page it is
    the highest returned entry ``seq``, and only a complete page carries the
    channel watermark, so following ``sequence`` (or the returned entries'
    own ``seq``) never skips an undelivered fact.
    """

    WIRE_FORMAT_VERSION = LIVE_FORMAT_VERSION

    identity: RunIdentity
    sequence: NonNegativeInt
    activity: OptionalFrozenJsonAt(MAX_LIVE_FRAME_BYTES // 2) = None
    inquiries: JsonTuple(InquiryState, max_items=MAX_INQUIRIES_PER_RUN) = ()
    events: ReservedEvents = ()
    unavailable: OptionalText(128) = None
    truncated: bool = False


@runtime_checkable
class LiveChannel(Protocol):
    """The one live interface of every harness (ADR-025 execution plan §5)."""

    def capabilities(self) -> LiveCapabilities: ...

    def request(self, request: LiveRequest, *, timeout_ms: int) -> LiveReply: ...

    def observe(self, *, after_seq: int | None, limit: int, timeout_ms: int) -> LiveSnapshot: ...

    def close(self, *, reason: str) -> None: ...


class ExistingLiveChannel:
    """The steps-one-to-four live backend over the existing facilities.

    The narrow bindings are the whole adapter surface: ``read_activity`` returns
    one validated activity payload (or ``None``), ``ask`` queues one question
    through the existing inquiry bridge within the requested transport window
    (``ask(question_id, question, timeout_ms)``) and returns its committed
    reply value (raising :class:`BoardError` with the bridge's error code), and
    ``read_journal`` returns the durable inquiry journal's records. Nothing
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
                 read_journal: Callable[[], list[Mapping[str, Any]]] | None = None):
        if not isinstance(identity, RunIdentity):
            raise fail("identity must be a RunIdentity")
        if not isinstance(capabilities, LiveCapabilities):
            raise fail("capabilities must be a LiveCapabilities value")
        self._identity = identity
        self._capabilities = capabilities
        self._read_activity = read_activity
        self._ask = ask
        self._read_journal = read_journal
        self._sequence = 0
        self._next_seq = 1
        self._activity: FrozenJson | None = None
        self._entries: tuple[InquiryState, ...] = ()
        self._requests: dict[str, str] = {}
        self._replies: dict[str, LiveReply] = {}
        self._closed_reason: str | None = None

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
        if request.kind == "finish-notice":
            return LiveReply(identity=self._identity, request_id=request.request_id, status="unsupported",
                             reason_code="finish-notice-unsupported")
        if self._capabilities.inquiry_delivery == "unsupported":
            return LiveReply(identity=self._identity, request_id=request.request_id, status="unsupported",
                             delivery_mode="unsupported", reason_code="inquiry-unsupported")
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
        correlation = {key: value[key] for key in ("inquiryId", "questionSha256", "state", "duplicate")
                       if isinstance(value, dict) and value.get(key) is not None}
        reply = LiveReply(identity=self._identity, request_id=request.request_id, status=status,
                          delivery_mode=self._capabilities.inquiry_delivery,
                          native_correlation=correlation or None)
        self._requests[request.request_id] = digest
        self._replies[request.request_id] = reply
        return reply

    def observe(self, *, after_seq: int | None, limit: int, timeout_ms: int) -> LiveSnapshot:
        """One bounded page of the run's live facts.

        Entries carry their own ``seq``; a page holds the entries after
        ``after_seq`` up to ``limit`` and up to what one frame holds. When
        ``truncated`` is set, the caller continues with
        ``after_seq`` = the highest returned entry ``seq``, which yields the
        remaining facts without loss; the 64 KiB frame never silently drops an
        entry because pages can always continue.
        """
        _timeout_ms(timeout_ms, "timeoutMs")
        if after_seq is not None and (isinstance(after_seq, bool) or not isinstance(after_seq, int)
                                      or after_seq < 0):
            raise fail("afterSeq must be null or a nonnegative integer", field="afterSeq")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OBSERVE_LIMIT:
            raise fail(f"limit must be an integer between 1 and {MAX_OBSERVE_LIMIT}", field="limit")
        unavailable: list[str] = []
        if self._closed_reason is not None:
            unavailable.append("channel-closed")
        if self._read_activity is not None and self._closed_reason is None:
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
        if self._read_journal is not None and self._closed_reason is None:
            try:
                entries = self._read_journal()
            except BoardError:
                unavailable.append("journal-unavailable")
            else:
                self._merge_journal(_journal_states(entries))
        # Page the unseen entries: every page fits one frame, and what does not
        # fit stays reachable through the next after_seq instead of being
        # dropped.
        matching = [entry for entry in self._entries
                    if after_seq is None or entry.seq > after_seq]
        page: list[InquiryState] = []
        for entry in matching:
            if len(page) >= limit:
                break
            candidate = LiveSnapshot(identity=self._identity, sequence=self._sequence,
                                     activity=self._activity,
                                     inquiries=tuple(page + [entry]))
            if page and len(canonical_json(candidate.to_payload()).encode()) > MAX_LIVE_FRAME_BYTES:
                break
            page.append(entry)
        truncated = len(page) < len(matching)
        # The snapshot's sequence must never let an ordinary caller skip facts
        # it has not been shown: on a truncated page it is this page's own
        # cursor (the highest returned entry seq), and only a complete page
        # carries the channel watermark, by which every fact is delivered.
        if truncated and page:
            sequence = max(entry.seq for entry in page)
        elif truncated:
            sequence = after_seq or 0
        else:
            sequence = self._sequence
        return LiveSnapshot(identity=self._identity, sequence=sequence, activity=self._activity,
                            inquiries=tuple(page), unavailable="+".join(unavailable) or None,
                            truncated=truncated)

    def _merge_journal(self, states: tuple[InquiryState, ...]) -> None:
        """Fold deduplicated journal states into entries with stable seqs.

        A question keeps its sequence while its state is unchanged and takes a
        fresh, higher one whenever the journal shows a new state, so observers
        that have read up to a sequence see exactly the changes after it.
        """
        replaced: list[InquiryState] = []
        changed = False
        previous = {entry.question_id: entry for entry in self._entries}
        for state in states:
            old = previous.get(state.question_id)
            if old is not None and (old.status, old.answer) == (state.status, state.answer):
                replaced.append(old)
                continue
            replaced.append(InquiryState(question_id=state.question_id, status=state.status,
                                         answer=state.answer, seq=self._next_seq))
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

    def _unavailable(self, request_id: str, reason_code: str) -> LiveReply:
        return LiveReply(identity=self._identity, request_id=request_id, status="unavailable",
                         reason_code=reason_code)


#: A bridge state maps onto a reply status without any upgrade: only the
#: bridge's own committed, journal-backed state can report ``delivered``.
_REPLY_STATE_STATUSES = {"queued": "queued", "claimed": "queued", "delivered": "delivered",
                         "answered": "answered"}

#: Journal record states map onto the closed snapshot set; an unrecognized state
#: stays ``unknown`` instead of being silently promoted.
_JOURNAL_STATE_STATUSES = {"queued": "queued", "claimed": "queued", "delivered": "delivered",
                           "answered": "answered", "discarded": "discarded", "unavailable": "unavailable"}


def _journal_states(entries: Any) -> tuple[InquiryState, ...]:
    """Deduplicate journal records into one state per question, last record wins."""
    if not isinstance(entries, list):
        raise fail("the inquiry journal binding must return a list of records")
    states: dict[str, InquiryState] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise fail("an inquiry journal record must be an object")
        question_id = entry.get("inquiryId") or entry.get("questionId") or entry.get("id")
        state = entry.get("state") or entry.get("status")
        if not isinstance(question_id, str) or not question_id or len(question_id.encode()) > MAX_REQUEST_ID:
            raise fail("an inquiry journal record carries no bounded question id")
        status = _JOURNAL_STATE_STATUSES.get(state, "unknown")
        answer = entry.get("answer")
        answer = answer if isinstance(answer, str) and answer else None
        if question_id not in states:
            order.append(question_id)
        states[question_id] = InquiryState(question_id=question_id, status=status, answer=answer)
    if len(states) > MAX_INQUIRIES_PER_RUN:
        raise fail("the inquiry journal carries more than "
                    f"{MAX_INQUIRIES_PER_RUN} questions")
    return tuple(states[question_id] for question_id in order)


def _bounded_frame(payload: dict, label: str) -> str:
    text = canonical_json(payload)
    if len(text.encode()) > MAX_LIVE_FRAME_BYTES:
        raise fail(f"the {label} frame exceeds its {MAX_LIVE_FRAME_BYTES}-byte bound", field=label)
    return text


def encode_live_request(request: LiveRequest) -> str:
    return _bounded_frame(request.to_payload(), "live request")


def decode_live_request(value: str | bytes | Mapping) -> LiveRequest:
    return LiveRequest.from_payload(
        decode_bounded_frame(value, label="live request", maximum=MAX_LIVE_FRAME_BYTES))


def encode_live_reply(reply: LiveReply) -> str:
    return _bounded_frame(reply.to_payload(), "live reply")


def decode_live_reply(value: str | bytes | Mapping) -> LiveReply:
    return LiveReply.from_payload(
        decode_bounded_frame(value, label="live reply", maximum=MAX_LIVE_FRAME_BYTES))


def encode_live_snapshot(snapshot: LiveSnapshot) -> str:
    return _bounded_frame(snapshot.to_payload(), "live snapshot")


def decode_live_snapshot(value: str | bytes | Mapping) -> LiveSnapshot:
    return LiveSnapshot.from_payload(
        decode_bounded_frame(value, label="live snapshot", maximum=MAX_LIVE_FRAME_BYTES))


__all__ = [
    "EXISTING_CAPABILITIES", "INQUIRY_DELIVERY_MODES", "INQUIRY_STATES", "LIVE_FORMAT_VERSION",
    "LIVE_KINDS", "LIVE_REPLY_STATUSES", "LiveCapabilities", "LiveChannel", "ExistingLiveChannel",
    "FinishNoticePayload", "InquiryPayload", "InquiryState", "LiveReply", "LiveRequest", "LiveSnapshot",
    "MAX_ANSWER_BYTES", "MAX_INQUIRIES_PER_RUN", "MAX_LIVE_FRAME_BYTES", "MAX_QUESTION_BYTES",
    "MAX_TRANSPORT_TIMEOUT_MS", "MAX_WAIT_MS", "MIN_TRANSPORT_TIMEOUT_MS",
    "decode_live_reply", "decode_live_request", "decode_live_snapshot", "encode_live_reply",
    "encode_live_request", "encode_live_snapshot",
]
