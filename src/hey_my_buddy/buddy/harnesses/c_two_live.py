"""In-process live facts and bounded inquiry handoff for one native run.

The execution process owns the C-Two resource and authenticates each inbound
frame once before forwarding its validated request into this object. The
native owner consumes and durably settles inquiries; publication and reads
never drive the native connection or extend its deadlines.
"""
from __future__ import annotations

import hashlib
import queue
import threading
import time
from typing import Any, Mapping

from ...json_codec import canonical_json
from ...protocol import activity as activity_protocol
from ...protocol.internal_models import FrozenJson, check_text
from .live import (
    LIVE_FIELDS, MAX_CLOSE_REASON_BYTES, MAX_INQUIRIES_PER_RUN, MAX_LIVE_FRAME_BYTES,
    InquiryState, LiveCapabilities, LiveJournal, LiveObservation, LiveReply,
    LiveRequest, LiveSnapshot,
)
from .run_contract import RunIdentity


class _PendingRequest:
    """One admitted request between its enqueue and the owner's settlement.

    The slot is the whole hand-off: the execution call enqueues it,
    then waits on ``done`` inside the caller's transport window; only
    :meth:`CTwoLiveEndpoint.settle_request` — the owner's local answer after
    its own journal commit — sets ``reply``. Nothing about the native side is
    ever inferred from the buffer insertion itself.
    """

    __slots__ = ("question_id", "digest", "deadline", "done", "reply", "expired")

    def __init__(self, question_id: str, digest: str, deadline: float):
        self.question_id = question_id
        self.digest = digest
        self.deadline = deadline
        self.done = threading.Event()
        self.reply: LiveReply | None = None
        self.expired = False


_REPLY_STATE_STATUSES = {"queued": "queued", "delivered": "delivered",
                         "answered": "answered", "discarded": "discarded"}


def _carried_facts(entry: InquiryState) -> dict:
    """One entry's facts without its paging position, through the public dump."""
    dump = entry.model_dump()
    dump.pop("seq")
    return dump


def _request_digest(request: LiveRequest) -> str:
    """The existing request digest over the full kind/payload pair."""
    return hashlib.sha256(canonical_json(
        {"kind": request.kind, "payload": request.payload.to_payload()}).encode()).hexdigest()


def _question_reply(entry: InquiryState, *, duplicate: bool) -> LiveReply:
    """One duplicate's reply from the question's own current published state.

    The reply never re-enqueues and never upgrades the state: whatever the
    entry last published is the fact, and its delivery record rides along in
    the correlation exactly as the existing ask value reads it. An unrecognized
    state stays a refusal instead of being projected onto the reply set.
    """
    correlation: dict[str, Any] = {"questionId": entry.question_id, "duplicate": duplicate}
    if entry.delivery is not None:
        correlation["delivery"] = entry.delivery.value
    status = _REPLY_STATE_STATUSES.get(entry.status)
    if status is None:
        return LiveReply(status="unavailable", reason_code="question-state-unknown")
    return LiveReply(status=status, observed=True, state=entry.status,
                     native_correlation=correlation)


class CTwoLiveEndpoint:
    """One run's owner-published facts and bounded inquiry admission queue.

    Requests wait for the owner's durable settlement inside their original
    transport window. Settled questions and request ids retain their bindings
    for replay and conflict checks over the run's inquiry budget.
    """

    def __init__(self, identity: RunIdentity, capabilities: LiveCapabilities):
        self._identity = identity
        self._capabilities = capabilities
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[LiveRequest, _PendingRequest]] = \
            queue.Queue(maxsize=MAX_INQUIRIES_PER_RUN)
        #: The committed admission indexes of the existing contract, bounded by
        #: the per-run budget of 32: one payload digest per settled question
        #: and one question per settled request id. Nothing is ever evicted —
        #: an old request id keeps its replay-or-conflict binding forever — so
        #: a NEW request id past the budget is refused as ``request-limit``
        #: while every already-known id stays idempotent. The public CLI's 32
        #: inquiries are unchanged; this is the same budget applied to the
        #: internal transport's distinct request ids, which the production
        #: caller derives from its inquiry ids anyway.
        self._admitted: dict[str, str] = {}
        self._requests: dict[str, str] = {}
        #: The in-flight hand-offs, keyed by request id. Each slot exists from
        #: its enqueue until the owner settles it (or it is dropped expired),
        #: so the pending count is bounded by the admission queue beside it.
        self._pending: dict[str, _PendingRequest] = {}
        self._entries: tuple[InquiryState, ...] = ()
        self._next_seq = 1
        self._activity = None
        self._observation: LiveObservation | None = None
        #: The observation read facts exactly as the owner last published
        #: them — ``(observed, reason, error)`` — or ``None`` before any read:
        #: the endpoint then reports the existing no-source fact instead of
        #: inferring success from its own buffer.
        self._observation_read: tuple[bool, str | None, str | None] | None = None
        self._journal: LiveJournal | None = None
        self._closed_reason: str | None = None

    @property
    def identity(self) -> RunIdentity:
        return self._identity


    def consume_request(self, timeout_s: float) -> LiveRequest | None:
        """One owner-loop consumption from the bounded admission queue.

        ``0`` is the original non-blocking drain — the owner's native poll
        empties what is already queued without waiting — and a positive value
        is one total wait window. A dead entry is dropped instead of handed
        over: one whose window expired unconsumed, one already settled, one
        closed with its waiters woken, or one whose slot no longer belongs to
        the current pending mapping. A filled refusal reply never lifts that
        expiry gate.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    request, slot = self._queue.get_nowait()
                else:
                    request, slot = self._queue.get(timeout=remaining)
            except queue.Empty:
                return None
            with self._lock:
                if time.monotonic() >= slot.deadline:
                    slot.expired = True
                if slot.expired or slot.reply is not None \
                        or self._pending.get(request.request_id) is not slot:
                    self._forget_slot(slot)
                    continue
            return request

    def settle_request(self, request_id: str, reply: LiveReply) -> None:
        """The owner's settlement of one consumed request — a local method.

        This is the one answer mechanism, not a new RPC operation: the owner
        loop calls it with the fact its own side actually achieved. A settled
        observation (``queued`` at the earliest, after the native bridge's
        durable journal commit) binds every request id that joined the
        delivery — each within the budget, so every alias keeps the
        replay-or-conflict gate — publishes the question's first inquiry
        state only when the owner has not already published a richer one from
        the journal callback, and never overwrites that published state; a
        settled refusal clears the slot untouched, so the unsuccessful
        request stays retryable exactly as the existing bridges allow.
        """
        with self._lock:
            slot = self._pending.get(request_id)
            if slot is None or slot.reply is not None:
                return
            aliases = [pending_id for pending_id, other in self._pending.items()
                       if other is slot]
            if reply.observed:
                status = reply.state or reply.status
                for pending_id in aliases:
                    self._requests[pending_id] = slot.question_id
                self._admitted[slot.question_id] = slot.digest
                if self._question_entry(slot.question_id) is None:
                    self._merge_states((InquiryState(question_id=slot.question_id,
                                                     status=status),))
                if reply.native_correlation is None:
                    # The settled fact identifies its question the way the
                    # existing ask value does; the owner's own correlation,
                    # when it carries one, stays untouched.
                    reply = LiveReply(**reply.model_dump(exclude={"native_correlation"}),
                                      native_correlation={"questionId": slot.question_id,
                                                          "duplicate": False})
            slot.reply = reply
            slot.done.set()
            for pending_id in aliases:
                self._pending.pop(pending_id, None)

    def publish_activity(self, payload: Mapping[str, Any]) -> bool:
        """Keep one newer canonical activity receipt; the bounded latest only.

        The merge rule is the activity protocol's own: normalize, then keep the
        candidate only when it advances the projection — an identical repeat is
        idempotent and an older receipt never replaces a newer one.
        """
        normalized = activity_protocol.normalize_activity(payload)
        with self._lock:
            previous = self._activity.value if self._activity is not None else None
            if not activity_protocol.is_newer(normalized, previous):
                return False
            self._activity = FrozenJson.from_value(
                normalized, "activity", maximum=MAX_LIVE_FRAME_BYTES // 2)
            return True

    def current_activity(self) -> dict | None:
        """Return the latest activity to this process's execution owner."""
        with self._lock:
            return self._activity.value if self._activity is not None else None

    def publish_inquiry_state(self, state: InquiryState) -> None:
        """Publish one question's newest facts; a change takes a fresh seq."""
        with self._lock:
            self._merge_states((state,))

    def publish_observation(self, observation: LiveObservation) -> None:
        """Keep one bounded observation value; a success read establishes it.

        A published value is the owner's own successful native read, so it
        carries the ``observed=True`` fact with it; the value is the newest
        published only and nothing is inferred beside it.
        """
        with self._lock:
            self._observation = observation
            self._observation_read = (True, None, None)

    def publish_snapshot(self, snapshot: LiveSnapshot) -> None:
        """Adopt one owner-published snapshot's read facts — a local method.

        The owner's actual observation read outcome rides in the snapshot's
        own ``observed``/``reason``/``error`` fields exactly as the existing
        channel reports them: a failed read is preserved verbatim, keeps the
        last activity untouched and serves no look-alike observation value,
        while a carried observation value or journal fact is adopted as its
        own publication would be. The inquiries and the paging position are
        the owner's own sequence of settled publications and never ride here.
        """
        with self._lock:
            if snapshot.observed is not None:
                self._observation_read = (snapshot.observed, snapshot.reason, snapshot.error)
                if not snapshot.observed:
                    self._observation = None
            if snapshot.observation is not None:
                self._observation = snapshot.observation
                if snapshot.observed is None:
                    self._observation_read = (True, None, None)
            if snapshot.journal is not None:
                self._journal = snapshot.journal

    def publish_journal(self, journal: LiveJournal) -> None:
        """Keep the durable journal's own availability fact; the owner's input only.

        The journal projection is exactly what the owner's existing locked
        read produced — availability, reason, its own deduplicated count and
        the per-question rejections its identity binding refused. Nothing is
        inferred here, no zero is invented for an unread journal and the fact
        is never mixed into the inquiry states: an endpoint whose owner has
        published nothing simply carries no journal fact.
        """
        with self._lock:
            self._journal = journal

    def close(self, *, reason: str) -> None:
        """Refuse further requests, wake every waiter; published facts stay readable."""
        check_text(reason, "reason", maximum=MAX_CLOSE_REASON_BYTES)
        with self._lock:
            if self._closed_reason is None:
                self._closed_reason = reason
            for request_id, slot in list(self._pending.items()):
                if slot.reply is None:
                    # The owner is gone: the waiters learn the channel closed
                    # instead of hanging, and their unconsumed entries die as
                    # expired rather than reaching the native side.
                    slot.expired = True
                    slot.reply = LiveReply(status="unavailable", reason_code="channel-closed")
                    slot.done.set()
                self._pending.pop(request_id, None)


    # -- execution-local operations -----------------------------------------


    def capabilities_local(self) -> str:
        """Read the already selected adapter's declaration inside this process."""
        return canonical_json(self._capabilities.to_payload())


    def request_local(self, request: LiveRequest, *, timeout_ms: int,
                      deadline_monotonic: float, started: float | None = None) -> str:
        """Admit a request after its only wire authentication at ExecutionLive."""
        deadline = min((started if started is not None else time.monotonic()) + timeout_ms / 1000.0,
                       deadline_monotonic)
        question_id = request.payload.question_id
        digest = _request_digest(request)
        with self._lock:
            if self._closed_reason is not None:
                return self._refusal_reply("channel-closed")
            pending = self._pending.get(request.request_id)
            if pending is not None:
                # The same request id is already in flight: a changed payload
                # conflicts now, an identical one joins the same delivery.
                if pending.digest != digest:
                    return self._refusal_reply("request-payload-conflict")
                slot = pending
            else:
                bound_question = self._requests.get(request.request_id)
                if bound_question is not None:
                    # A committed request id is bound over its whole payload: an
                    # identical replay answers from the bound question's own
                    # state and a changed payload — a different question id
                    # included — is a conflict before anything is enqueued.
                    if self._admitted.get(bound_question) != digest:
                        return self._refusal_reply("request-payload-conflict")
                    entry = self._question_entry(bound_question)
                    if entry is None:  # pragma: no cover - a settled commit publishes first
                        return self._refusal_reply("question-state-unknown")
                    return canonical_json(_question_reply(entry, duplicate=True).to_payload())
                slot = None
                for other in self._pending.values():
                    # The same question already in flight under another request
                    # id: join that delivery rather than enqueue a second one —
                    # the join is itself a budgeted alias, so the settled
                    # binding covers it and its conflict gate holds.
                    if other.question_id == question_id:
                        if other.digest != digest:
                            return self._refusal_reply("question-payload-conflict")
                        if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                            return self._refusal_reply("request-limit")
                        slot = other
                        self._pending[request.request_id] = slot
                        break
                if slot is None:
                    admitted_digest = self._admitted.get(question_id)
                    if admitted_digest is not None:
                        # The question is already committed: the same payload
                        # under a fresh request id is a budgeted alias — a
                        # duplicate without a second delivery — and a different
                        # payload under it is the question's own conflict.
                        if admitted_digest != digest:
                            return self._refusal_reply("question-payload-conflict")
                        if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                            return self._refusal_reply("request-limit")
                        self._requests[request.request_id] = question_id
                        entry = self._question_entry(question_id)
                        if entry is None:  # pragma: no cover - a settled commit publishes first
                            return self._refusal_reply("question-state-unknown")
                        return canonical_json(_question_reply(entry, duplicate=True).to_payload())
                    if self._capabilities.inquiry_delivery == "unsupported":
                        return canonical_json(LiveReply(
                            status="unsupported", reason_code="inquiry-unsupported").to_payload())
                    if len(self._admitted) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                        return self._refusal_reply("inquiry-limit")
                    if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                        return self._refusal_reply("request-limit")
                    if time.monotonic() >= deadline:
                        return self._refusal_reply("request-window-expired")
                    slot = _PendingRequest(question_id, digest, deadline)
                    try:
                        self._queue.put_nowait(
                            (request, slot))
                    except queue.Full:
                        return self._refusal_reply("queue-full")
                    self._pending[request.request_id] = slot
        if not slot.done.wait(timeout=max(0.0, deadline - time.monotonic())):
            with self._lock:
                if slot.reply is None:
                    # The window passed without the owner's answer: the caller
                    # learns the expiry, and the unconsumed queue entry dies
                    # with the slot instead of reaching the native side late.
                    slot.expired = True
            return self._refusal_reply("request-window-expired")
        reply = slot.reply
        if reply is None:  # pragma: no cover - every wake sets a reply
            return self._refusal_reply("channel-closed")
        return canonical_json(reply.to_payload())


    def observe_local(self, *, after_seq=None, limit=None, fields=None,
                      inquiry_id=None) -> str:
        """Read local published facts after the outer endpoint authenticated once."""
        with self._lock:
            unavailable: list[str] = []
            if self._closed_reason is not None:
                unavailable.append("channel-closed")
            if inquiry_id is not None:
                entry = self._question_entry(inquiry_id)
                if entry is None:
                    return canonical_json(LiveSnapshot(
                        unavailable="+".join(unavailable) or None, observed=True).to_payload())
                snapshot = LiveSnapshot(inquiries=(entry,),
                                        unavailable="+".join(unavailable) or None, observed=True)
                if _frame_fits(snapshot):
                    return canonical_json(snapshot.to_payload())
                return _oversized_snapshot_reply()
            if limit is None:
                return canonical_json(LiveSnapshot(
                    unavailable="frame-invalid", observed=False,
                    reason="frame-invalid").to_payload())
            selected = frozenset(fields) if fields else frozenset(LIVE_FIELDS)
            activity = self._activity if "activity" in selected else None
            journal = self._journal if "inquiries" in selected else None
            if "observation" in selected:
                observed, reason, error, observation = self._observation_facts()
            else:
                # The selection did not read the observation, so its read
                # facts — a published failure included — are not carried.
                observed, reason, error, observation = None, None, None, None
            # The paging bound covers the first candidate and the whole final
            # frame: every probe carries the snapshot's own worst-case small
            # fields — the longer ``false`` literal, the journal fact, the
            # observation read facts and the availability text with the
            # overflow reason appended — so a page that passes the probe
            # passes the final encoding beside it.
            probe_unavailable = "+".join([*unavailable, "frame-too-large"]) or None
            page: list[InquiryState] = []
            matching: list[InquiryState] = []
            overflow = False
            if "inquiries" in selected:
                matching = [entry for entry in self._entries
                            if after_seq is None or entry.seq > after_seq]
                for entry in matching:
                    if len(page) >= limit:
                        break
                    if not _frame_fits(LiveSnapshot(
                            activity=activity, inquiries=tuple(page + [entry]),
                            observation=observation, journal=journal,
                            unavailable=probe_unavailable, truncated=False, observed=observed,
                            reason=reason, error=error)):
                        overflow = True
                        break
                    page.append(entry)
            truncated = len(page) < len(matching)
            final_unavailable = probe_unavailable if overflow and not page \
                else ("+".join(unavailable) or None)
            snapshot = LiveSnapshot(activity=activity, inquiries=tuple(page),
                                    observation=observation, journal=journal,
                                    unavailable=final_unavailable, truncated=truncated,
                                    observed=observed, reason=reason, error=error)
            if _frame_fits(snapshot):
                return canonical_json(snapshot.to_payload())
            return _oversized_snapshot_reply()

    # -- internals ------------------------------------------------------------


    def _question_entry(self, question_id: str) -> InquiryState | None:
        for entry in self._entries:
            if entry.question_id == question_id:
                return entry
        return None

    def _forget_slot(self, slot: "_PendingRequest") -> None:
        """Drop every pending mapping that still points at one dead slot."""
        for pending_id in [pending_id for pending_id, other in self._pending.items()
                           if other is slot]:
            self._pending.pop(pending_id, None)

    def _observation_facts(self) -> tuple[bool, str | None, str | None, LiveObservation | None]:
        """The observation read facts, exactly the existing channel's three.

        A never-published read is the existing no-source fact — observed
        ``False`` with ``observation-unavailable`` — never an inferred
        success; an owner-published failure carries its own transport
        classification and specific code verbatim and serves no look-alike
        observation value; an owner-published value is a successful read.
        """
        if self._observation_read is not None:
            observed, reason, error = self._observation_read
            return observed, reason, error, self._observation if observed else None
        if self._observation is not None:
            return True, None, None, self._observation
        return False, "observation-unavailable", None, None

    def _merge_states(self, states: tuple[InquiryState, ...]) -> tuple[InquiryState, ...]:
        """Fold states into the entries; a change takes a fresh seq.

        The mechanism is the live seam's own: a question keeps its sequence
        while every carried fact is unchanged and takes a fresh, higher one
        whenever any of them changes, so an observer that has read up to a
        sequence sees exactly the changes after it. Unlike the journal merge,
        one call may carry a single question's new fact beside untouched
        others, so the untouched entries always survive it.
        """
        merged = list(self._entries)
        position = {entry.question_id: index for index, entry in enumerate(merged)}
        for state in states:
            index = position.get(state.question_id)
            if index is not None:
                if _carried_facts(merged[index]) == _carried_facts(state):
                    continue
                merged[index] = state.model_copy(update={"seq": self._next_seq})
            else:
                merged.append(state.model_copy(update={"seq": self._next_seq}))
                position[state.question_id] = len(merged) - 1
            self._next_seq += 1
        self._entries = tuple(sorted(merged, key=lambda entry: entry.seq))
        return self._entries

    def _refusal_reply(self, reason_code: str) -> str:
        return canonical_json(
            LiveReply(status="unavailable", reason_code=reason_code).to_payload())


def _frame_fits(snapshot: LiveSnapshot) -> bool:
    """The paging bound: one whole candidate frame within the 64 KiB limit."""
    return len(canonical_json(snapshot.to_payload()).encode()) <= MAX_LIVE_FRAME_BYTES


def _oversized_snapshot_reply() -> str:
    """The explicit fact when even an empty page cannot fit one frame.

    The facts are not trimmed to squeeze through: the read reports the frame
    bound it could not meet, the caller narrows the field selection or pages
    the inquiries alone, and every published fact stays reachable that way.
    """
    return canonical_json(LiveSnapshot(unavailable="frame-too-large", observed=False,
                                       reason="frame-too-large").to_payload())


__all__ = ["CTwoLiveEndpoint"]
