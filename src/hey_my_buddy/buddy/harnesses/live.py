"""Strict bounded live facts and the common C-Two-facing channel interface."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Protocol, runtime_checkable

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
from ...protocol.inquiry import MIN_TRANSPORT_TIMEOUT_MS, MAX_TRANSPORT_TIMEOUT_MS
from .run_contract import RunIdentity
from pydantic import Field, model_validator

#: The existing inquiry limits, preserved verbatim: the same byte bounds, the
#: same per-run count and the same transport windows the current bridges enforce.
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES_PER_RUN = 32
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
#: refuse questions; ZCode and DSH deliver them at their session's cooperative
#: checkpoint.
EXISTING_CAPABILITIES = {
    "codex": LiveCapabilities(inquiry_delivery="unsupported"),
    "claude": LiveCapabilities(inquiry_delivery="unsupported"),
    "zcode": LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
    "dsh": LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
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
    normalized activity publication remains the snapshot's ``activity`` field and
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






__all__ = [
    "EXISTING_CAPABILITIES", "JOURNAL_REASONS",
    "LIVE_FIELDS", "LiveCapabilities",
    "LiveChannel", "InquiryJournalRejection",
    "InquiryPayload", "InquiryState", "LiveEvent", "LiveJournal", "LiveObservation", "LiveReply",
    "LiveRequest", "LiveSnapshot",
    "MAX_ANSWER_BYTES", "MAX_INQUIRIES_PER_RUN", "MAX_LIVE_FRAME_BYTES", "MAX_QUESTION_BYTES",
    "MAX_TRANSPORT_TIMEOUT_MS", "MIN_TRANSPORT_TIMEOUT_MS",
]
