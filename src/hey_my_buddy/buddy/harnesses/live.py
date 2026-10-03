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
this interface.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ...errors import BoardError
from ...protocol.activity import is_newer as _activity_is_newer
from .run_contract import FORMAT_VERSION, FrozenJson, RunIdentity, _fail, canonical_json

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

_IDENTIFIER_OK = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class LiveCapabilities:
    """What one harness's live facilities can do, declared as facts."""

    activity: bool
    inquiry_delivery: str
    finish_notice: bool = False
    session_content: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.activity, bool):
            raise _fail("activity must be a boolean", field="activity")
        if self.inquiry_delivery not in INQUIRY_DELIVERY_MODES:
            raise _fail(f"inquiryDelivery must be one of {', '.join(INQUIRY_DELIVERY_MODES)}",
                        field="inquiryDelivery")
        for name in ("finish_notice", "session_content"):
            if not isinstance(getattr(self, name), bool):
                raise _fail(f"{name} must be a boolean", field=name)

    def to_payload(self) -> dict:
        return {"activity": self.activity, "inquiryDelivery": self.inquiry_delivery,
                "finishNotice": self.finish_notice, "sessionContent": self.session_content}

    @classmethod
    def from_payload(cls, value: Any) -> "LiveCapabilities":
        if not isinstance(value, dict) or set(value) != {"activity", "inquiryDelivery", "finishNotice",
                                                         "sessionContent"}:
            raise _fail("capabilities must carry exactly activity, inquiryDelivery, finishNotice "
                        "and sessionContent")
        return cls(activity=value["activity"], inquiry_delivery=value["inquiryDelivery"],
                   finish_notice=value["finishNotice"], session_content=value["sessionContent"])


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_OK.fullmatch(value):
        raise _fail(f"{label} must be an identifier of 1..128 characters", field=label)
    return value


def _bounded_text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > maximum:
        raise _fail(f"{label} must be a nonempty string of at most {maximum} UTF-8 bytes", field=label)
    return value


def _timeout_ms(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_TRANSPORT_TIMEOUT_MS <= value <= MAX_TRANSPORT_TIMEOUT_MS:
        raise _fail(f"{label} must be an integer between {MIN_TRANSPORT_TIMEOUT_MS} and "
                    f"{MAX_TRANSPORT_TIMEOUT_MS}", field=label)
    return value


def _exact_version(value: Any, label: str) -> None:
    """``formatVersion`` is the exact integer 1: ``True`` and ``1.0`` compare
    equal to ``1`` in Python and are refused all the same."""
    if type(value) is not int or value != LIVE_FORMAT_VERSION:
        raise _fail(f"the {label} format version is not supported", formatVersion=value)


def _decode_strict(raw: str | bytes) -> object:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def reject_constant(name: str) -> None:
        raise ValueError(f"non-finite JSON number {name}")

    return json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def _bounded_frame(payload: dict, label: str) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(text.encode()) > MAX_LIVE_FRAME_BYTES:
        raise _fail(f"the {label} frame exceeds its {MAX_LIVE_FRAME_BYTES}-byte bound", field=label)
    return text


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


@dataclass(frozen=True)
class InquiryPayload:
    """One Host question; the only live payload with existing native facilities."""

    question_id: str
    question: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _identifier(self.question_id, "payload.questionId"))
        object.__setattr__(self, "question", _bounded_text(self.question, "payload.question",
                                                           maximum=MAX_QUESTION_BYTES))

    def to_payload(self) -> dict:
        return {"questionId": self.question_id, "question": self.question}

    @classmethod
    def from_payload(cls, value: Any) -> "InquiryPayload":
        if not isinstance(value, dict) or set(value) != {"questionId", "question"}:
            raise _fail("an inquiry payload carries exactly questionId and question")
        return cls(question_id=value["questionId"], question=value["question"])


@dataclass(frozen=True)
class FinishNoticePayload:
    """One reserved finish-notice payload; no existing facility accepts one."""

    notice_id: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "notice_id", _identifier(self.notice_id, "payload.noticeId"))
        object.__setattr__(self, "message", _bounded_text(self.message, "payload.message",
                                                          maximum=MAX_QUESTION_BYTES))

    def to_payload(self) -> dict:
        return {"noticeId": self.notice_id, "message": self.message}

    @classmethod
    def from_payload(cls, value: Any) -> "FinishNoticePayload":
        if not isinstance(value, dict) or set(value) != {"noticeId", "message"}:
            raise _fail("a finish-notice payload carries exactly noticeId and message")
        return cls(notice_id=value["noticeId"], message=value["message"])


@dataclass(frozen=True)
class LiveRequest:
    """One live request, bound to the execution identity and its request id."""

    identity: RunIdentity
    request_id: str
    kind: str
    payload: InquiryPayload | FinishNoticePayload

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunIdentity):
            raise _fail("identity must be a RunIdentity")
        object.__setattr__(self, "request_id", _identifier(self.request_id, "requestId"))
        object.__setattr__(self, "kind", _bounded_text(self.kind, "kind", maximum=32))
        if self.kind not in LIVE_KINDS:
            raise _fail(f"kind must be one of {', '.join(LIVE_KINDS)}", field="kind")
        if self.kind == "inquiry" and not isinstance(self.payload, InquiryPayload):
            raise _fail("an inquiry request carries an InquiryPayload", field="payload")
        if self.kind == "finish-notice" and not isinstance(self.payload, FinishNoticePayload):
            raise _fail("a finish-notice request carries a FinishNoticePayload", field="payload")

    def to_payload(self) -> dict:
        return {"formatVersion": LIVE_FORMAT_VERSION, "identity": self.identity.to_payload(),
                "requestId": self.request_id, "kind": self.kind, "payload": self.payload.to_payload()}

    @classmethod
    def from_payload(cls, value: Any) -> "LiveRequest":
        if not isinstance(value, dict) or set(value) != {"formatVersion", "identity", "requestId",
                                                         "kind", "payload"}:
            raise _fail("a live request carries exactly formatVersion, identity, requestId, kind and payload")
        _exact_version(value["formatVersion"], "live request")
        kind = value["kind"]
        payload = value["payload"]
        if kind == "inquiry":
            payload_value: InquiryPayload | FinishNoticePayload = InquiryPayload.from_payload(payload)
        elif kind == "finish-notice":
            payload_value = FinishNoticePayload.from_payload(payload)
        else:
            raise _fail(f"kind must be one of {', '.join(LIVE_KINDS)}", field="kind")
        return cls(identity=RunIdentity.from_payload(value["identity"]), request_id=value["requestId"],
                   kind=kind, payload=payload_value)


@dataclass(frozen=True)
class LiveReply:
    """One live request's reply fact; ``queued`` is never reported as delivered."""

    identity: RunIdentity
    request_id: str
    status: str
    delivery_mode: str | None = None
    native_correlation: FrozenJson | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunIdentity):
            raise _fail("identity must be a RunIdentity")
        object.__setattr__(self, "request_id", _identifier(self.request_id, "requestId"))
        object.__setattr__(self, "status", _bounded_text(self.status, "status", maximum=32))
        if self.status not in LIVE_REPLY_STATUSES:
            raise _fail(f"status must be one of {', '.join(LIVE_REPLY_STATUSES)}", field="status")
        if self.delivery_mode is not None:
            object.__setattr__(self, "delivery_mode", _bounded_text(self.delivery_mode, "deliveryMode", maximum=32))
            if self.delivery_mode not in INQUIRY_DELIVERY_MODES:
                raise _fail(f"deliveryMode must be one of {', '.join(INQUIRY_DELIVERY_MODES)}",
                            field="deliveryMode")
        if self.native_correlation is not None and not isinstance(self.native_correlation, FrozenJson):
            object.__setattr__(self, "native_correlation",
                               FrozenJson.from_value(self.native_correlation, "nativeCorrelation",
                                                     maximum=MAX_LIVE_FRAME_BYTES // 2))
        object.__setattr__(self, "reason_code",
                           None if self.reason_code is None
                           else _bounded_text(self.reason_code, "reasonCode", maximum=128))

    def to_payload(self) -> dict:
        return {"formatVersion": LIVE_FORMAT_VERSION, "identity": self.identity.to_payload(),
                "requestId": self.request_id, "status": self.status, "deliveryMode": self.delivery_mode,
                "nativeCorrelation": None if self.native_correlation is None
                else self.native_correlation.value,
                "reasonCode": self.reason_code}

    @classmethod
    def from_payload(cls, value: Any) -> "LiveReply":
        if not isinstance(value, dict) or set(value) != {"formatVersion", "identity", "requestId", "status",
                                                         "deliveryMode", "nativeCorrelation", "reasonCode"}:
            raise _fail("a live reply carries exactly formatVersion, identity, requestId, status, "
                        "deliveryMode, nativeCorrelation and reasonCode")
        _exact_version(value["formatVersion"], "live reply")
        return cls(identity=RunIdentity.from_payload(value["identity"]), request_id=value["requestId"],
                   status=value["status"], delivery_mode=value["deliveryMode"],
                   native_correlation=value["nativeCorrelation"], reason_code=value["reasonCode"])


@dataclass(frozen=True)
class InquiryState:
    """One question's committed state as the durable journal holds it.

    ``seq`` is the draft paging position this interface assigns: each entry
    keeps the sequence number it was created with and takes a fresh, higher one
    whenever its state changes, so an observer that has read up to ``after_seq``
    can fetch exactly the entries it has not seen yet.
    """

    question_id: str
    status: str
    answer: str | None = None
    seq: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _identifier(self.question_id, "inquiry.questionId"))
        object.__setattr__(self, "status", _bounded_text(self.status, "inquiry.status", maximum=32))
        if self.status not in INQUIRY_STATES:
            raise _fail(f"inquiry status must be one of {', '.join(INQUIRY_STATES)}", field="inquiry.status")
        if self.answer is not None:
            object.__setattr__(self, "answer", _bounded_text(self.answer, "inquiry.answer",
                                                             maximum=MAX_ANSWER_BYTES))
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 0:
            raise _fail("inquiry.seq must be a nonnegative integer", field="inquiry.seq")

    def to_payload(self) -> dict:
        return {"questionId": self.question_id, "status": self.status, "answer": self.answer,
                "seq": self.seq}

    @classmethod
    def from_payload(cls, value: Any) -> "InquiryState":
        if not isinstance(value, dict) or set(value) != {"questionId", "status", "answer", "seq"}:
            raise _fail("an inquiry state carries exactly questionId, status, answer and seq")
        return cls(question_id=value["questionId"], status=value["status"], answer=value["answer"],
                   seq=value["seq"])


@dataclass(frozen=True)
class LiveSnapshot:
    """One observation of the run's live facts, bounded to one frame.

    ``sequence`` is the safe cursor of this page: on a truncated page it is
    the highest returned entry ``seq``, and only a complete page carries the
    channel watermark, so following ``sequence`` (or the returned entries'
    own ``seq``) never skips an undelivered fact.
    """

    identity: RunIdentity
    sequence: int
    activity: FrozenJson | None = None
    inquiries: tuple[InquiryState, ...] = ()
    events: tuple[FrozenJson, ...] = ()
    unavailable: str | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunIdentity):
            raise _fail("identity must be a RunIdentity")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise _fail("sequence must be a nonnegative integer", field="sequence")
        if self.activity is not None and not isinstance(self.activity, FrozenJson):
            object.__setattr__(self, "activity", FrozenJson.from_value(self.activity, "activity",
                                                                       maximum=MAX_LIVE_FRAME_BYTES // 2))
        inquiries = self.inquiries
        if not isinstance(inquiries, tuple):
            inquiries = tuple(inquiries)
        if len(inquiries) > MAX_INQUIRIES_PER_RUN:
            raise _fail(f"a snapshot carries at most {MAX_INQUIRIES_PER_RUN} inquiries",
                        field="inquiries")
        for inquiry in inquiries:
            if not isinstance(inquiry, InquiryState):
                raise _fail("inquiries entries must be InquiryState values")
        object.__setattr__(self, "inquiries", inquiries)
        events = self.events
        if not isinstance(events, tuple):
            events = tuple(events)
        # The reserved session-event slot admits no content type in this step
        # (ADR-024 comes later): capacity exists, admission is closed, so any
        # non-empty events value is refused instead of being waved through.
        if events:
            raise _fail("session events are reserved and this step admits no event content types",
                        field="events")
        object.__setattr__(self, "events", ())
        object.__setattr__(self, "unavailable",
                           None if self.unavailable is None
                           else _bounded_text(self.unavailable, "unavailable", maximum=128))
        if not isinstance(self.truncated, bool):
            raise _fail("truncated must be a boolean", field="truncated")

    def to_payload(self) -> dict:
        return {"formatVersion": LIVE_FORMAT_VERSION, "identity": self.identity.to_payload(),
                "sequence": self.sequence,
                "activity": None if self.activity is None else self.activity.value,
                "inquiries": [inquiry.to_payload() for inquiry in self.inquiries],
                "events": [event.value for event in self.events],
                "unavailable": self.unavailable, "truncated": self.truncated}

    @classmethod
    def from_payload(cls, value: Any) -> "LiveSnapshot":
        if not isinstance(value, dict) or set(value) != {"formatVersion", "identity", "sequence", "activity",
                                                         "inquiries", "events", "unavailable", "truncated"}:
            raise _fail("a live snapshot carries exactly formatVersion, identity, sequence, activity, "
                        "inquiries, events, unavailable and truncated")
        _exact_version(value["formatVersion"], "live snapshot")
        inquiries = value["inquiries"]
        events = value["events"]
        if not isinstance(inquiries, list) or not isinstance(events, list):
            raise _fail("inquiries and events must be lists")
        return cls(identity=RunIdentity.from_payload(value["identity"]), sequence=value["sequence"],
                   activity=value["activity"],
                   inquiries=tuple(InquiryState.from_payload(inquiry) for inquiry in inquiries),
                   events=tuple(events), unavailable=value["unavailable"], truncated=value["truncated"])


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
            raise _fail("identity must be a RunIdentity")
        if not isinstance(capabilities, LiveCapabilities):
            raise _fail("capabilities must be a LiveCapabilities value")
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
            return self._unavailable(request.request_id, _bounded_text(str(reason or "not-accepted"),
                                                                       "bridge reason", maximum=64))
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
            raise _fail("afterSeq must be null or a nonnegative integer", field="afterSeq")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OBSERVE_LIMIT:
            raise _fail(f"limit must be an integer between 1 and {MAX_OBSERVE_LIMIT}", field="limit")
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
        _bounded_text(reason, "reason", maximum=MAX_CLOSE_REASON_BYTES)
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
        raise _fail("the inquiry journal binding must return a list of records")
    states: dict[str, InquiryState] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _fail("an inquiry journal record must be an object")
        question_id = entry.get("inquiryId") or entry.get("questionId") or entry.get("id")
        state = entry.get("state") or entry.get("status")
        if not isinstance(question_id, str) or not question_id or len(question_id.encode()) > MAX_REQUEST_ID:
            raise _fail("an inquiry journal record carries no bounded question id")
        status = _JOURNAL_STATE_STATUSES.get(state, "unknown")
        answer = entry.get("answer")
        answer = answer if isinstance(answer, str) and answer else None
        if question_id not in states:
            order.append(question_id)
        states[question_id] = InquiryState(question_id=question_id, status=status, answer=answer)
    if len(states) > MAX_INQUIRIES_PER_RUN:
        raise _fail("the inquiry journal carries more than "
                    f"{MAX_INQUIRIES_PER_RUN} questions")
    return tuple(states[question_id] for question_id in order)


def encode_live_request(request: LiveRequest) -> str:
    return _bounded_frame(request.to_payload(), "live request")


def _decode_frame(value: str | bytes | Mapping, label: str, build: Any) -> Any:
    """One bounded, strict decode path shared by text, bytes and Mapping forms."""
    if isinstance(value, Mapping):
        try:
            text = canonical_json(dict(value))
        except (TypeError, ValueError, RecursionError) as error:
            raise _fail(f"the {label} frame is not bounded JSON", reason=str(error)[:200]) from None
    elif isinstance(value, bytes):
        try:
            text = value.decode()
        except UnicodeDecodeError:
            raise _fail(f"the {label} frame is not valid UTF-8", field=label) from None
    elif isinstance(value, str):
        text = value
    else:
        raise _fail(f"the {label} frame is missing")
    if len(text.encode()) > MAX_LIVE_FRAME_BYTES:
        raise _fail(f"the {label} frame exceeds its {MAX_LIVE_FRAME_BYTES}-byte bound")
    try:
        payload = _decode_strict(text)
    except ValueError as error:
        raise _fail(f"the {label} frame is not strict JSON", reason=str(error)[:200]) from None
    except RecursionError:
        raise _fail(f"the {label} frame nests too deeply", field=label) from None
    return build(payload)


def decode_live_request(value: str | bytes | Mapping) -> LiveRequest:
    return _decode_frame(value, "live request", LiveRequest.from_payload)


def encode_live_reply(reply: LiveReply) -> str:
    return _bounded_frame(reply.to_payload(), "live reply")


def decode_live_reply(value: str | bytes | Mapping) -> LiveReply:
    return _decode_frame(value, "live reply", LiveReply.from_payload)


def encode_live_snapshot(snapshot: LiveSnapshot) -> str:
    return _bounded_frame(snapshot.to_payload(), "live snapshot")


def decode_live_snapshot(value: str | bytes | Mapping) -> LiveSnapshot:
    return _decode_frame(value, "live snapshot", LiveSnapshot.from_payload)


__all__ = [
    "EXISTING_CAPABILITIES", "INQUIRY_DELIVERY_MODES", "INQUIRY_STATES", "LIVE_FORMAT_VERSION",
    "LIVE_KINDS", "LIVE_REPLY_STATUSES", "LiveCapabilities", "LiveChannel", "ExistingLiveChannel",
    "FinishNoticePayload", "InquiryPayload", "InquiryState", "LiveReply", "LiveRequest", "LiveSnapshot",
    "MAX_ANSWER_BYTES", "MAX_INQUIRIES_PER_RUN", "MAX_LIVE_FRAME_BYTES", "MAX_QUESTION_BYTES",
    "MAX_TRANSPORT_TIMEOUT_MS", "MAX_WAIT_MS", "MIN_TRANSPORT_TIMEOUT_MS",
    "decode_live_reply", "decode_live_request", "decode_live_snapshot", "encode_live_reply",
    "encode_live_request", "encode_live_snapshot",
]
