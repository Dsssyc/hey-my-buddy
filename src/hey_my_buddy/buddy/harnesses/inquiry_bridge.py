"""Owner of correlated inquiries inside one admitted native turn.

C-Two RPCs enqueue requests; this owner alone commits questions under the
journal lock and fsync barrier before settling them. A native input acceptance
or verified checkpoint receipt proves delivery; a verified MCP answer receipt or
a bound root client-tool request proves an answer. Local runs retain the same journal.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from ... import locking
from ...protocol.inquiry_journal import (INQUIRY_JOURNAL_VERSION, MAX_JOURNAL_BYTES,
                                         read_inquiry_journal, read_shared_snapshot)
from ...errors import BoardError
from ...json_codec import canonical_json
from ...timestamps import utc_now as _now
from .live import (
    LiveJournal,
    LiveObservation,
    LiveReply,
    LiveSnapshot,
    _journal_states,
)
from .c_two_live import CTwoLiveEndpoint
from .session_receipts import (
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES,
    MAX_QUESTION_BYTES,
)

#: States a checkpoint may deliver and an answer may act on.
ANSWERABLE_STATES = ("queued", "delivered")


class InquiryBridge:
    """Owner-private inquiry bridge for one micro task root turn.

    The journal is attempt-private transport evidence. A committed question keeps
    its identity, question hash and delivery record across state changes, so a
    replay of the identical question is always a duplicate instead of a conflict,
    and a changed question under the same id is always a conflict. The owner
    thread, the native pump thread and shutdown all touch this object, so one
    lock guards every entry, journal and state transition.
    """

    def __init__(self, *, identity: dict, journal_path: str,
                 error_factory, event_metadata, attention_path: str | None = None,
                 live: CTwoLiveEndpoint | None = None,
                 delivery_mode: str = "cooperative-checkpoint",
                 checkpoint_tool: str | None = "hey_my_buddy_checkpoint"):
        self.error_factory = error_factory
        self.event_metadata = event_metadata
        self.delivery_mode = delivery_mode
        self.checkpoint_tool = checkpoint_tool
        self.identity = identity
        self.journal_path = Path(journal_path)
        self.attention_path = Path(attention_path) if attention_path else None
        if live is not None:
            for key, attribute in (("taskId", "task_id"), ("attemptId", "attempt_id"),
                                   ("generation", "generation"), ("turnId", "turn_id")):
                if identity[key] != getattr(live.identity, attribute):
                    raise BoardError("INVALID_ARGUMENT", f"inquiry owner identity mismatch: {key}")
        self.live = live
        self._stop = threading.Event()
        self.session_id: str | None = None
        self.entries: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.active = False
        self.closed = False
        self.mounted = False
        self.bridge_started_at: str | None = None
        self.agent_status = "starting"
        self.last_event: dict | None = None
        self.activity: list[dict] = []
        self.activity_dropped = 0
        self.attention: list[dict] = []
        self.started_at = _now()
        self.error: str | None = None
        self.truncated = False
        self._torn_line = False

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self.closed or self.mounted:
                return
            self._load_journal()
            self.mounted = True
            self._publish()
            if self.live is not None:
                self.thread = threading.Thread(target=self._consume_loop, daemon=True)
                self.thread.start()

    def _consume_loop(self) -> None:
        while not self._stop.is_set():
            request = self.live.consume_request(timeout_s=0.1)
            if request is None:
                continue
            # _ask owns both validation and the durable commit. RPC threads
            # never call it and never touch the native connection.
            result = self._ask({"inquiryId": request.payload.question_id,
                                "question": request.payload.question}, _owner_response)
            if result["ok"]:
                value = result["value"]
                state = value["state"]
                if state in ("queued", "delivered", "answered", "discarded"):
                    reply = LiveReply(status=state, state=state, observed=True,
                                      native_correlation={key: value[key] for key in
                                          ("inquiryId", "questionSha256", "state", "duplicate", "delivery")
                                          if value.get(key) is not None})
                else:
                    reply = LiveReply(status="unavailable", reason_code="unknown-bridge-state")
            else:
                reply = LiveReply(status="unavailable", reason_code="bridge-refused",
                                  error_code=result["error"])
            self.live.settle_request(request.request_id, reply)

    def close(self) -> None:
        """Terminalize the answer window before stopping the owner consumer.

        This is no evidence of native process shutdown. The controller owns the
        endpoint lifecycle and closes it in its finally block.
        """
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.active = False
            self.agent_status = "ended"
            for inquiry_id, entry in list(self.entries.items()):
                if entry.get("state") in ANSWERABLE_STATES:
                    self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "unavailable",
                                   "unavailableAt": _now()})
            self._publish()
            self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)

    def activate(self, session_id: str) -> None:
        with self.lock:
            self.session_id = session_id
            self.active = True
            self.agent_status = "running"
            self.bridge_started_at = _now()
            self._publish()

    def deactivate(self) -> None:
        """Close admission between native turns without ending this execution."""
        with self.lock:
            self.active = False
            self.agent_status = "finishing"
            self._publish()

    def pending_inquiries(self) -> list[dict]:
        """The native pump alone sends these committed questions in its active turn."""
        with self.lock:
            if not self.active or self.closed:
                return []
            return [dict(entry) for entry in self.entries.values() if entry.get("state") == "queued"]

    def deliver_native(self, inquiry_id: str, native_correlation: dict) -> None:
        """Commit delivery after the native carrier accepted the active-turn input."""
        with self.lock:
            entry = self.entries[inquiry_id]
            if entry["state"] == "queued":
                self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "delivered",
                               "deliveredAt": _now(), "via": "native:in-turn",
                               "nativeCorrelation": native_correlation})

    # -- journal -------------------------------------------------------------
    def _load_journal(self) -> None:
        fact = read_inquiry_journal(self.live.identity if self.live else self.identity, self.journal_path)
        if not fact["available"]:
            if fact["reason"] != "journal-not-written":
                self.error = self.error or fact["reason"]
                self.truncated = fact["reason"] == "journal-exceeds-limit"
            return
        # Keep the existing crash-tail isolation on the writer's next append.
        try:
            raw = read_shared_snapshot(self.journal_path, MAX_JOURNAL_BYTES)
        except FileNotFoundError:
            raw = None
        self._torn_line = bool(raw and not raw.endswith(b"\n"))
        for record in fact["records"]:
            inquiry_id = record["inquiryId"]
            self.entries[inquiry_id] = {**self.entries.get(inquiry_id, {}), **record}

    def _identity_fields(self) -> dict:
        return {"taskId": self.identity.get("taskId"), "attemptId": self.identity.get("attemptId"),
                "generation": self.identity.get("generation"), "turnId": self.identity.get("turnId"),
                "sessionId": self.session_id}

    def _journal(self, record: dict) -> bool:
        """Durably append one transport record, then commit it in memory.

        The whole transaction — byte-cap check, append, fsync — runs under an
        exclusive ``flock`` on the journal file, the writer side of the
        cross-process barrier whose reader side is ``read_shared_snapshot``:
        while it is held, the MCP session tools' shared-locked reads wait, so a
        reader can never observe an in-flight append. A failed append is rolled
        back to the pre-transaction length under the same still-held lock
        (truncate + fsync), so no torn fragment survives either; only when the
        rollback itself fails are the remains marked torn and isolated by the
        next append's leading newline, losing at most that one line. The
        in-memory entry commits only after the durable append, so an ``ask``
        can never report a question as queued that the MCP tools cannot read,
        and a delivery or answer is never fabricated on a failed write. The
        caller learns the outcome from the return value and surfaces a bounded
        ``journal-unavailable`` error instead of inventing success.
        """
        inquiry_id = record["inquiryId"]
        merged = {**self.entries.get(inquiry_id, {}), **record}
        raw = (canonical_json({"version": INQUIRY_JOURNAL_VERSION, **record}) + "\n").encode()
        try:
            fd = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            self.error = self.error or "journal-unavailable: the inquiry record could not be appended durably"
            self._publish()
            return False
        committed_length: int | None = None
        failed = False
        try:
            locking.lock(fd)
            committed_length = os.fstat(fd).st_size
            if committed_length + len(raw) > MAX_JOURNAL_BYTES:
                self.truncated = True
                raise OSError("the inquiry journal reached its byte cap")
            if self._torn_line:
                # Isolate the remains of an append whose rollback also failed,
                # so at most that one unreadable line is ever lost.
                os.write(fd, b"\n")
                committed_length = os.fstat(fd).st_size
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("the inquiry journal accepted a short write")
                view = view[count:]
            os.fsync(fd)
        except OSError:
            if committed_length is not None:
                # A failure before the lock was taken wrote nothing; otherwise
                # the append is rolled back while the barrier is still held.
                self._rollback_append(fd, committed_length)
            self.error = self.error or "journal-unavailable: the inquiry record could not be appended durably"
            failed = True
            return False
        finally:
            os.close(fd)  # closing releases the exclusive barrier
            if failed:
                self._publish()
        self._torn_line = False
        self.entries[inquiry_id] = merged
        self._publish()
        return True

    def _rollback_append(self, fd: int, committed_length: int) -> None:
        """Truncate a failed append back to the committed length, still locked.

        A rollback that itself fails leaves the fragment in place and marks the
        line torn; the next append isolates it with a leading newline, and every
        reader already ignores an unparseable line instead of failing.
        """
        try:
            os.ftruncate(fd, committed_length)
            os.fsync(fd)
        except OSError:
            self._torn_line = True

    # -- driver-authority transitions ----------------------------------------
    def deliver_inquiries(self, receipt: dict, tool_call_id: str) -> None:
        """Mark exactly the receipt's inquiries delivered, after root evidence.

        Called only from :class:`RootTurnEvidence` with a receipt whose signature
        and attempt identity already verified and whose native ``tool.updated``
        result was a successful, untruncated root-turn checkpoint call. Each
        listed inquiry must match a committed question by id and hash; an unknown
        id or a changed hash is forgery and fails the turn. Entries that left the
        answerable window while the call was in flight (answered, withdrawn or
        made unavailable) keep their terminal state instead of failing the turn.
        A delivery whose journal append cannot be made durable is not fabricated:
        the entry stays queued, the bounded journal-unavailable error becomes
        visible, and the root can checkpoint again.
        """
        with self.lock:
            for item in receipt["inquiries"]:
                inquiry_id = item["inquiryId"]
                entry = self.entries.get(inquiry_id)
                if entry is None or entry.get("questionSha256") != item["questionSha256"]:
                    raise self.error_factory("invalid-inquiry-evidence",
                                      "a checkpoint receipt referenced an inquiry this attempt never committed")
                if entry.get("state") == "queued":
                    self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "delivered",
                                   "deliveredAt": _now(), "via": "tool:hey_my_buddy_checkpoint", "toolCallId": tool_call_id})

    def record_answer(self, receipt: dict, tool_call_id: str) -> None:
        """Record one correlated answer after verified root tool evidence.

        The receipt's signature, attempt identity, inquiry id, question hash and
        exact answer text bind the answer. The first verified answer is terminal;
        a byte-identical replay is an idempotent no-op, and a different answer
        under an already-answered inquiry is a conflict that fails the turn
        rather than silently replacing it. A valid answer that arrives after the
        Host withdrew the question or the turn closed is a normal race, not
        forgery: the terminal ``discarded``/``unavailable`` state is preserved,
        the late answer is ignored and the coding work may finish. A journal
        append failure never fabricates the answered state; the entry stays
        answerable so the root can legitimately retry.
        """
        inquiry_id = receipt["inquiryId"]
        answer = receipt["answer"]
        with self.lock:
            entry = self.entries.get(inquiry_id)
            if entry is None or entry.get("questionSha256") != receipt["questionSha256"]:
                raise self.error_factory("invalid-inquiry-evidence",
                                  "an answer receipt referenced an inquiry this attempt never committed")
            if entry.get("state") == "answered":
                recorded = (entry.get("answer") or {}).get("text")
                if recorded == answer:
                    return  # an identical binding replay changes nothing
                raise self.error_factory("conflicting-inquiry-answer",
                                  "a different answer was already recorded for this inquiry")
            if entry.get("state") in ("discarded", "unavailable"):
                # A valid root answer racing a Host withdrawal or settlement is
                # expected: keep the terminal state and drop the late answer.
                return
            self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "answered",
                           "answer": {"text": answer, "bytes": len(answer.encode()), "truncated": False,
                                      "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": tool_call_id,
                                      "at": _now()}})

    def answer_native(self, inquiry_id: str, answer: str, tool_call_id: str) -> str | None:
        """Apply an already validated root client-tool call under the state lock."""
        with self.lock:
            if not self.active or self.closed:
                return "the root turn is no longer running"
            entry = self.entries.get(inquiry_id)
            if entry is None:
                return "unknown inquiryId"
            if entry.get("state") != "delivered":
                return "the inquiry must be delivered and still unanswered"
            if not self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "answered",
                                  "answer": {"text": answer, "bytes": len(answer.encode()), "truncated": False,
                                             "via": "tool:hey_my_buddy_answer_inquiry",
                                             "toolCallId": tool_call_id, "at": _now()}}):
                return "the answer could not be recorded durably"
            return None

    # -- observation ---------------------------------------------------------
    def note_event(self, message: dict, phase: str) -> None:
        """Keep a bounded, metadata-only live view of the native turn."""
        metadata = self.event_metadata(message)
        last_event = {"at": _now(), "kind": metadata["kind"][:80]}
        entry = {"at": last_event["at"], "kind": last_event["kind"]}
        tool_name = metadata.get("toolName")
        if isinstance(tool_name, str) and tool_name:
            entry["toolName"] = tool_name[:120]
        with self.lock:
            self.last_event = last_event
            self.activity.append(entry)
            if len(self.activity) > 20:
                self.activity = self.activity[-20:]
                self.activity_dropped += 1
            self.agent_status = "finishing" if phase == "finishing" else ("running" if self.active else self.agent_status)
            self._publish()

    def snapshot(self) -> dict:
        with self.lock:
            entries = list(self.entries.values())
            counts = {state: sum(1 for entry in entries if entry.get("state") == state)
                      for state in ("queued", "delivered", "answered", "discarded", "unavailable")}
            activity = list(self.activity)
            last_event = dict(self.last_event) if self.last_event else None
            attention = ({"requests": len(self.attention), "last": self.attention[-1]} if self.attention else None)
        return {
            "ready": self.active,
            "observedAt": _now(),
            "sessionId": self.session_id,
            "agentStatus": self.agent_status,
            "bridgeStartedAt": self.bridge_started_at,
            "inbox": {"pending": counts["queued"], "delivered": counts["delivered"], "answered": counts["answered"],
                      "discarded": counts["discarded"], "refused": counts["unavailable"]},
            "lastEvent": last_event,
            "activity": activity,
            "activityDropped": self.activity_dropped,
            "replyTool": {"name": "hey_my_buddy_answer_inquiry",
                          **({"checkpointTool": self.checkpoint_tool} if self.checkpoint_tool is not None else {}),
                          "delivery": self.delivery_mode},
            "attention": attention,
            "unavailable": ["nativeReasoning", "toolArguments", "toolOutput", "providerCredentials", "immediateDelivery"],
            "journal": {"enabled": True, "truncated": self.truncated, "entries": len(entries)},
            "capability": "inquiry",
            "supported": True,
            "deliveryMode": self.delivery_mode,
            "limits": {"maxQuestionBytes": MAX_QUESTION_BYTES, "maxAnswerBytes": MAX_ANSWER_BYTES,
                       "maxInquiriesPerRun": MAX_INQUIRIES,
                       "inquiry": self.delivery_mode, "deliveryMode": self.delivery_mode,
                       "requestedDelivery": None, "startsNewTurn": False, "extendsDeadline": False},
            "error": self.error,
        }

    def report(self) -> dict:
        with self.lock:
            entries = list(self.entries.values())
            counts = {state: sum(1 for entry in entries if entry.get("state") == state)
                      for state in ("queued", "delivered", "answered", "discarded", "unavailable")}
        return {
            "enabled": self.mounted or self.error is not None,
            "mounted": self.mounted,
            "error": self.error,
            "capability": "inquiry",
            "supported": True,
            "inquiry": self.delivery_mode,
            "deliveryMode": self.delivery_mode,
            "requested": len(entries),
            "queued": counts["queued"],
            "delivered": counts["delivered"],
            "answered": counts["answered"],
            "discarded": counts["discarded"],
            "refused": counts["unavailable"],
            "unansweredDelivered": sum(1 for entry in entries if entry.get("deliveredAt")
                                       and entry.get("state") not in ("answered", "discarded")),
            "journalEntries": len(entries),
            "requestedDelivery": None,
            "startsNewTurn": False,
            "extendsDeadline": False,
        }

    def publish_attention(self) -> None:
        """Publish the attention list after its independent recorder committed it."""
        with self.lock:
            self._publish()

    def attention_report(self) -> dict:
        with self.lock:
            return {"requests": len(self.attention), "last": self.attention[-1] if self.attention else None}

    def _publish(self) -> None:
        """Publish actual owner reads, keeping backend sequencing in the endpoint."""
        if self.live is None:
            return
        fact = read_inquiry_journal(self.live.identity, self.journal_path)
        if self.error and self.error.startswith("journal-unavailable"):
            fact = {**fact, "available": False, "reason": "journal-unavailable"}
        try:
            journal = LiveJournal.from_payload(
                {key: fact[key] for key in ("available", "reason", "entries", "rejections")})
            states = _journal_states(fact["records"])
            observation = LiveObservation.from_payload(_observation_view(self.snapshot()))
        except BoardError as error:
            # A strict projection failure is unavailable evidence. Keep the
            # owner alive and the last committed state; never invent fields.
            self.live.publish_snapshot(LiveSnapshot(observed=False, reason="journal-unavailable",
                                                    error=error.code))
            self.live.publish_journal(LiveJournal(available=False, reason="journal-unavailable",
                                                 entries=fact["entries"]))
            return
        self.live.publish_journal(journal)
        for state in states:
            self.live.publish_inquiry_state(state)
        self.live.publish_observation(observation)

    def _ask(self, request: dict, response) -> dict:
        """Queue one question for this run's declared native delivery path.

        This owner only commits the question: the native pump sends it during
        the active turn, or the root picks it up at its cooperative checkpoint.
        The first question is journaled once with
        its committed text, question hash and delivery record; an identical
        replay returns the current committed state as a duplicate and a changed
        question under the same id is a conflict. Before the turn is admitted
        there is nothing to deliver into yet, so the ask is refused as not-ready
        and the asking side may retry the identical id.
        """
        inquiry_id, question = request["inquiryId"], request["question"]
        if not question.strip():
            return response(False, error="bad-request")
        with self.lock:
            if self.closed or (not self.active and self.agent_status == "finishing"):
                return response(False, error="agent-not-running")
            if not self.active:
                return response(False, error="not-ready")
            digest = hashlib.sha256(question.encode()).hexdigest()
            existing = self.entries.get(inquiry_id)
            if existing is not None:
                if existing.get("questionSha256") != digest:
                    return response(False, error="conflict")
                return response(True, value=self._question_value(inquiry_id, existing, duplicate=True))
            if len(self.entries) >= MAX_INQUIRIES:
                return response(False, error="too-many")
            delivery = {"requestedDelivery": None, "admittedDelivery": self.delivery_mode,
                        "startsNewTurn": False, "extendsDeadline": False, "supported": True, "at": _now()}
            if not self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "queued",
                                  "question": question, "questionSha256": digest, "askedAt": _now(),
                                  "delivery": delivery}):
                # Never report a queued question the journal did not durably
                # record: the MCP tools could not read it back.
                return response(False, error="journal-unavailable")
            return response(True, value=self._question_value(inquiry_id, self.entries[inquiry_id], duplicate=False))

    @staticmethod
    def _question_value(inquiry_id: str, entry: dict, *, duplicate: bool) -> dict:
        return {
            "accepted": True,
            "supported": True,
            "state": entry.get("state") or "queued",
            "duplicate": duplicate,
            "inquiryId": inquiry_id,
            "questionSha256": entry.get("questionSha256"),
            "delivery": entry.get("delivery") or {},
        }


#: The bridge snapshot's fields the channel observation carries — exactly the
#: fields the board's ``live`` view reads. The bridge's own ``limits`` and its
#: start timestamp stay behind: they are dropped here, never hidden in a JSON
#: slot, and the recent-event metadata is renamed to ``recentActivity`` so the
#: snapshot's ``activity`` field keeps meaning the normalized activity publication.
_OBSERVATION_FIELDS = ("ready", "observedAt", "sessionId", "agentStatus", "inbox", "lastEvent",
                       "activityDropped", "replyTool", "capability", "supported", "attention",
                       "journal", "deliveryMode", "unavailable", "error")


def _observation_view(value: dict) -> dict:
    view = {key: value[key] for key in _OBSERVATION_FIELDS if key in value}
    if "activity" in value:
        view["recentActivity"] = value["activity"]
    return view


def _owner_response(ok: bool, *, value: dict | None = None, error: str | None = None) -> dict:
    """Local transition result; this is never a wire envelope."""
    return {"ok": ok, **({"value": value} if ok else {"error": error or "internal"})}


__all__ = ["ANSWERABLE_STATES", "InquiryBridge"]
