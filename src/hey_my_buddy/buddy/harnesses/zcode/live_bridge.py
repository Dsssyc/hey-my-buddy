"""The owner-private cooperative inquiry bridge and its existing-facilities live channel.

The bridge lives inside the driver process because only that process holds the
native connection. A Host question is only ever *queued* here; it reaches the
root exclusively through the session-private ``buddy_checkpoint`` tool inside
the one admitted native turn, and only the driver — after verifying that turn's
own ``tool.updated`` evidence and the signed receipt — journals ``delivered``
or ``answered`` state. Nothing here injects input, and no MCP handler writes
this journal. See :data:`hey_my_buddy.buddy.harnesses.zcode.protocol.COOPERATIVE_INQUIRY_NOTE`
for the channel contract.

:func:`bind_live_channel` is the real narrow binding of ADR-025's
:class:`~hey_my_buddy.buddy.harnesses.live.ExistingLiveChannel` over these
current facilities: questions, native observations and answer views go through
the shared bridge transport (:mod:`hey_my_buddy.protocol.inquiry_transport`,
the one socket client of this protocol — this harness keeps no second copy),
activity comes from the attempt's published sidecar and inquiry states from
the durable journal. Step five replaces what sits behind that interface;
until then this is the one binding, and it never starts a native turn or
touches the native deadline.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

from .... import locking
from ....errors import BoardError
from ....json_codec import canonical_json
from ....protocol.inquiry_transport import bridge_request
from ...roles.turn_io import private_json
from ..live import (
    EXISTING_CAPABILITIES,
    ExistingLiveChannel,
    LiveCapabilities,
    RunIdentity,
)
from ..session_receipts import (
    INQUIRY_JOURNAL_VERSION,
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES,
    MAX_INQUIRY_ID_BYTES,
    MAX_JOURNAL_BYTES,
    MAX_QUESTION_BYTES,
    read_shared_snapshot,
)
from .protocol import COOPERATIVE_INQUIRY_NOTE, NativeError, decode_json

MAX_BRIDGE_FRAME_BYTES = 16 * 1024
BRIDGE_PROTOCOL_VERSION = 1
BRIDGE_WAIT_SECONDS = 5.0

#: Terminal states an inquiry entry can never leave.
ANSWERED_STATES = ("answered", "discarded", "unavailable")
#: States a checkpoint may deliver and an answer may act on.
ANSWERABLE_STATES = ("queued", "delivered")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class InquiryBridge:
    """Owner-private cooperative inquiry bridge for one governed ZCode root turn.

    The journal is attempt-private transport evidence. A committed question keeps
    its identity, question hash and delivery record across state changes, so a
    replay of the identical question is always a duplicate instead of a conflict,
    and a changed question under the same id is always a conflict. The socket
    thread, the native pump thread and shutdown all touch this object, so one
    lock guards every entry, journal and state transition.
    """

    def __init__(self, credentials: dict, *, identity: dict, journal_path: str, attention_path: str | None = None):
        self.credentials = credentials
        self.identity = identity
        self.journal_path = path = Path(journal_path)
        self.attention_path = Path(attention_path) if attention_path else None
        self.socket_path = str(credentials.get("socketPath") or "")
        self.token = credentials.get("token")
        self.session_id: str | None = None
        self.entries: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.listener: socket.socket | None = None
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
        try:
            self._load_journal()
            self._clear_stale_socket()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(self.socket_path)
            os.chmod(self.socket_path, 0o600)
            listener.listen(8)
            listener.settimeout(0.2)
        except OSError as error:
            self.error = f"the inquiry bridge socket could not be mounted: {error.__class__.__name__}"
            try:
                listener.close()
            except (OSError, UnboundLocalError):
                pass
            return
        self.listener = listener
        self.mounted = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def _clear_stale_socket(self) -> None:
        """Remove a leftover socket file from a crashed previous controller.

        Only a socket node is unlinked: a regular file in its place is not ours to
        delete, and binding then fails honestly and is reported.
        """
        try:
            if stat.S_ISSOCK(os.lstat(self.socket_path).st_mode):
                os.unlink(self.socket_path)
        except OSError:
            pass

    def close(self) -> None:
        """Stop accepting and terminalize what can no longer be answered.

        Every queued or delivered-but-unanswered entry is journaled
        ``unavailable``: settlement is the honest end of the answer window, and
        marking it requires waking no model. The socket never outlives the owned
        root turn, and a late ask after this point cannot queue anything.
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
                                   "unavailableAt": _now(),
                                   "reason": "the governed root turn ended before this inquiry was answered",
                                   "limitation": COOPERATIVE_INQUIRY_NOTE})
            listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def activate(self, session_id: str) -> None:
        with self.lock:
            self.session_id = session_id
            self.active = True
            self.agent_status = "running"
            self.bridge_started_at = _now()

    # -- journal -------------------------------------------------------------
    def _bound_record(self, record: object) -> bool:
        """A record replays only with this journal version and this attempt.

        Journal replay is strictly attempt-bound: the current record version and
        an exact ``taskId``/``attemptId``/``generation``/``turnId`` binding are
        required, so a malformed, foreign or unbound line — including one from a
        different attempt that happens to share the file — is ignored instead of
        merged. There is no old-format fallback.
        """
        if not isinstance(record, dict) or record.get("version") != INQUIRY_JOURNAL_VERSION:
            return False
        identity = self.identity
        return all(key in record and record[key] == identity.get(key)
                   for key in ("taskId", "attemptId", "generation", "turnId"))

    def _load_journal(self) -> None:
        # The shared-locked read is the reader side of the journal barrier: it
        # waits for any in-flight writer transaction instead of observing a
        # record the writer has not committed (see read_shared_snapshot).
        raw = read_shared_snapshot(self.journal_path, MAX_JOURNAL_BYTES)
        if raw is None or not raw or len(raw) > MAX_JOURNAL_BYTES:
            return
        # A tail without its newline is the remains of an append that died with
        # the process (the rollback path truncates cleanly): the next append
        # isolates that one unparseable line instead of merging into it.
        self._torn_line = not raw.endswith(b"\n")
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn line is ignored, never fatal
            if isinstance(record, dict) and isinstance(record.get("inquiryId"), str) and self._bound_record(record):
                # Merge instead of replace: the committed question hash and
                # delivery survive a later state record and a controller restart.
                self.entries[record["inquiryId"]] = {**self.entries.get(record["inquiryId"], {}), **record}

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
            return False
        committed_length: int | None = None
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
            return False
        finally:
            os.close(fd)  # closing releases the exclusive barrier
        self._torn_line = False
        self.entries[inquiry_id] = merged
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

    def describe_answer(self, inquiry_id: str) -> dict:
        """The live view of one inquiry, including its correlated answer."""
        with self.lock:
            entry = self.entries.get(inquiry_id) or {}
            answer = entry.get("answer")
            value = {"available": False, "reason": entry.get("reason") or "no correlated answer yet"}
            if isinstance(answer, dict) and isinstance(answer.get("text"), str) and answer["text"].strip():
                value = {**answer, "available": True}
            return {"answer": value, "state": entry.get("state") or "unknown", "inquiryId": inquiry_id,
                    "supported": True, "deliveryMode": "cooperative-checkpoint"}

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
                    raise NativeError("invalid-inquiry-evidence",
                                      "a checkpoint receipt referenced an inquiry this attempt never committed")
                if entry.get("state") == "queued":
                    self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "delivered",
                                   "deliveredAt": _now(), "via": "tool:buddy_checkpoint", "toolCallId": tool_call_id})

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
                raise NativeError("invalid-inquiry-evidence",
                                  "an answer receipt referenced an inquiry this attempt never committed")
            if entry.get("state") == "answered":
                recorded = (entry.get("answer") or {}).get("text")
                if recorded == answer:
                    return  # an identical binding replay changes nothing
                raise NativeError("conflicting-inquiry-answer",
                                  "a different answer was already recorded for this inquiry")
            if entry.get("state") in ("discarded", "unavailable"):
                # A valid root answer racing a Host withdrawal or settlement is
                # expected: keep the terminal state and drop the late answer.
                return
            self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "answered",
                           "answeredAt": _now(),
                           "answer": {"text": answer, "bytes": len(answer.encode()), "truncated": False,
                                      "via": "tool:buddy_answer_inquiry", "toolCallId": tool_call_id,
                                      "at": _now()}})

    # -- observation ---------------------------------------------------------
    def note_event(self, message: dict, phase: str) -> None:
        """Keep a bounded, metadata-only live view of the native turn."""
        params = message.get("params") if isinstance(message, dict) else None
        method = message.get("method") if isinstance(message, dict) else None
        kind = params.get("type") if isinstance(params, dict) else None
        if method == "state.updated":
            kind = f"state:{str((params or {}).get('reason'))[:40]}"
        last_event = {"at": _now(), "kind": (kind or method or "event")[:80]}
        entry = {"at": last_event["at"], "kind": last_event["kind"]}
        data = (params or {}).get("payload") if isinstance(params, dict) else None
        if isinstance(data, dict) and isinstance(data.get("toolName"), str) and data["toolName"]:
            entry["toolName"] = data["toolName"][:120]
        with self.lock:
            self.last_event = last_event
            self.activity.append(entry)
            if len(self.activity) > 20:
                self.activity = self.activity[-20:]
                self.activity_dropped += 1
            self.agent_status = "finishing" if phase == "finishing" else ("running" if self.active else self.agent_status)

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
            "replyTool": {"name": "buddy_answer_inquiry", "checkpointTool": "buddy_checkpoint",
                          "delivery": "cooperative-checkpoint"},
            "attention": attention,
            "unavailable": ["nativeReasoning", "toolArguments", "toolOutput", "providerCredentials", "immediateDelivery"],
            "journal": {"enabled": True, "truncated": self.truncated, "entries": len(entries)},
            "capability": "inquiry",
            "supported": True,
            "deliveryMode": "cooperative-checkpoint",
            "limitation": COOPERATIVE_INQUIRY_NOTE,
            "limits": {"maxQuestionBytes": MAX_QUESTION_BYTES, "maxAnswerBytes": MAX_ANSWER_BYTES,
                       "maxInquiriesPerRun": MAX_INQUIRIES, "maxFrameBytes": MAX_BRIDGE_FRAME_BYTES,
                       "inquiry": "cooperative-checkpoint", "deliveryMode": "cooperative-checkpoint",
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
            "inquiry": "cooperative-checkpoint",
            "deliveryMode": "cooperative-checkpoint",
            "limitation": COOPERATIVE_INQUIRY_NOTE,
            "requested": len(entries),
            "queued": counts["queued"],
            "delivered": counts["delivered"],
            "answered": counts["answered"],
            "discarded": counts["discarded"],
            "refused": counts["unavailable"],
            "journalEntries": len(entries),
            "requestedDelivery": None,
            "startsNewTurn": False,
            "extendsDeadline": False,
        }

    def note_attention(self, record: dict) -> None:
        """Keep one bounded record and make it readable while the turn is still live.

        The session-private finish tool refuses a ``completed`` outcome while an
        unsupported interactive request is outstanding, so the file has to exist
        before the tool call, not only in the driver's final report.
        """
        with self.lock:
            self.attention.append(record)
            del self.attention[:-8]
            if self.attention_path is not None:
                try:
                    private_json(self.attention_path, {
                        "version": 1,
                        **{k: self.identity.get(k) for k in ("taskId", "attemptId", "generation", "turnId")},
                        "requests": self.attention,
                    })
                except OSError:
                    self.error = self.error or "the native attention record could not be written"

    def attention_report(self) -> dict:
        with self.lock:
            return {"requests": len(self.attention), "last": self.attention[-1] if self.attention else None}

    # -- socket protocol -----------------------------------------------------
    def _accept_loop(self) -> None:
        while self.listener is not None:
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._serve(connection)
            except (OSError, ValueError):
                pass
            finally:
                connection.close()

    def _serve(self, connection: socket.socket) -> None:
        connection.settimeout(BRIDGE_WAIT_SECONDS)
        raw = bytearray()
        while b"\n" not in raw:
            block = connection.recv(4096)
            if not block:
                break
            raw.extend(block)
            if len(raw) > MAX_BRIDGE_FRAME_BYTES:
                return
        try:
            frame = decode_json(bytes(raw).split(b"\n", 1)[0])
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        reply = self.handle(frame)
        if reply is None:
            return
        try:
            connection.sendall((canonical_json(reply) + "\n").encode())
        except OSError:
            pass

    def handle(self, frame: dict) -> dict | None:
        request_id = frame.get("id")
        if not isinstance(request_id, str):
            return None
        def response(ok: bool, *, value: dict | None = None, error: str | None = None) -> dict:
            return {"version": BRIDGE_PROTOCOL_VERSION, "id": request_id, "ok": ok,
                    **({"value": value} if ok else {"error": error or "internal"})}
        if frame.get("version") != BRIDGE_PROTOCOL_VERSION or frame.get("token") != self.token:
            return response(False, error="unauthorized")
        method = frame.get("method")
        if method == "observe":
            return response(True, value=self.snapshot())
        if method == "ask":
            return self._ask(frame, response)
        if method == "discard":
            return self._discard(frame, response)
        if method == "answer":
            inquiry_id = frame.get("inquiryId")
            if not isinstance(inquiry_id, str) or not inquiry_id:
                return response(False, error="bad-request")
            with self.lock:
                if inquiry_id not in self.entries:
                    return response(False, error="not-ready")
            return response(True, value=self.describe_answer(inquiry_id))
        return response(False, error="unsupported-method")

    def _ask(self, frame: dict, response) -> dict:
        """Queue one question for cooperative delivery at the next checkpoint.

        No native command is ever sent: the question waits in this bridge until
        the root calls ``buddy_checkpoint`` inside the one admitted turn (see
        ``COOPERATIVE_INQUIRY_NOTE``). The first question is journaled once with
        its committed text, question hash and delivery record; an identical
        replay returns the current committed state as a duplicate and a changed
        question under the same id is a conflict. Before the turn is admitted
        there is nothing to deliver into yet, so the ask is refused as not-ready
        and the asking side may retry the identical id.
        """
        inquiry_id, question = frame.get("inquiryId"), frame.get("question")
        if (not isinstance(inquiry_id, str) or not inquiry_id or len(inquiry_id.encode()) > MAX_INQUIRY_ID_BYTES
                or not isinstance(question, str) or not question.strip() or len(question.encode()) > MAX_QUESTION_BYTES):
            return response(False, error="bad-request")
        with self.lock:
            if not self.active or self.closed:
                return response(False, error="not-ready")
            digest = hashlib.sha256(question.encode()).hexdigest()
            existing = self.entries.get(inquiry_id)
            if existing is not None:
                if existing.get("questionSha256") != digest:
                    return response(False, error="conflict")
                return response(True, value=self._question_value(inquiry_id, existing, duplicate=True))
            if len(self.entries) >= MAX_INQUIRIES:
                return response(False, error="too-many")
            delivery = {"requestedDelivery": None, "admittedDelivery": "cooperative-checkpoint",
                        "startsNewTurn": False, "extendsDeadline": False, "supported": True, "at": _now()}
            if not self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "queued",
                                  "question": question, "questionSha256": digest, "askedAt": _now(),
                                  "delivery": delivery, "limitation": COOPERATIVE_INQUIRY_NOTE}):
                # Never report a queued question the journal did not durably
                # record: the MCP tools could not read it back.
                return response(False, error="journal-unavailable")
            return response(True, value=self._question_value(inquiry_id, self.entries[inquiry_id], duplicate=False))

    def _discard(self, frame: dict, response) -> dict:
        """Withdraw one still-unanswered question; it stops blocking completion."""
        inquiry_id = frame.get("inquiryId")
        if not isinstance(inquiry_id, str) or not inquiry_id:
            return response(False, error="bad-request")
        with self.lock:
            entry = self.entries.get(inquiry_id)
            if entry is None:
                return response(False, error="not-ready")
            if entry.get("state") not in ANSWERABLE_STATES:
                return response(False, error="conflict")
            if not self._journal({**self._identity_fields(), "inquiryId": inquiry_id, "state": "discarded",
                                  "discardedAt": _now(),
                                  "reason": "withdrawn by the asking side; it no longer blocks turn completion"}):
                return response(False, error="journal-unavailable")
            return response(True, value={"inquiryId": inquiry_id, "state": "discarded",
                                         "questionSha256": entry.get("questionSha256")})

    @staticmethod
    def _question_value(inquiry_id: str, entry: dict, *, duplicate: bool) -> dict:
        return {
            "accepted": True,
            "supported": True,
            "state": entry.get("state") or "queued",
            "duplicate": duplicate,
            "inquiryId": inquiry_id,
            "questionSha256": entry.get("questionSha256"),
            "reason": entry.get("reason"),
            "delivery": entry.get("delivery") or {},
        }


#: The bridge snapshot's fields the channel observation carries — exactly the
#: fields the board's ``live`` view reads. The bridge's own ``limits`` and its
#: start timestamp stay behind: they are dropped here, never hidden in a JSON
#: slot, and the recent-event metadata is renamed to ``recentActivity`` so the
#: snapshot's ``activity`` field keeps meaning the normalized sidecar.
_OBSERVATION_FIELDS = ("ready", "observedAt", "sessionId", "agentStatus", "inbox", "lastEvent",
                       "activityDropped", "replyTool", "capability", "supported", "attention",
                       "journal", "deliveryMode", "limitation", "unavailable", "error")


def _observation_view(value: dict) -> dict:
    view = {key: value[key] for key in _OBSERVATION_FIELDS if key in value}
    if "activity" in value:
        view["recentActivity"] = value["activity"]
    return view


def _bridge_call(credentials: dict, method: str, payload: dict, timeout_ms: int):
    """One observe/answer roundtrip through the shared bridge transport.

    The transport's result fact returns as it is: ``{"ok": True, "value": …}``
    carries the bridge's value, ``{"ok": False, "reason": …, "code": …}`` the
    refusal with its specific code. A successful reply that carries no object
    value is refused here, so the channel reports an honest unavailability
    instead of projecting a look-alike observation.
    """
    result = bridge_request(credentials, method, payload, timeout_ms=timeout_ms)
    if result.get("ok") is True and not isinstance(result.get("value"), dict):
        raise BoardError("INVALID_ARGUMENT", "the inquiry bridge reply carried no value")
    return result


#: The rejection strings of the direct reader's own mismatch check, kept
#: verbatim; a record that carries neither field was never foreign to it, so a
#: binding refusal on such a record says exactly what it is.
_UNBOUND_REASON = "the journal record is not bound to this execution"


def _journal_rejection_reason(record: dict, bound: dict) -> str:
    for key, label in (("taskId", "task"), ("attemptId", "attempt")):
        value = record.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or value != bound[key]:
            return f"the journal record belongs to another {label}"
    return _UNBOUND_REASON


def bind_live_channel(identity: RunIdentity, *, credentials: dict, journal_path: str | None,
                      activity_path: Path) -> ExistingLiveChannel:
    """The real existing-facilities live binding of one governed zcode run.

    ``ask`` queues through the shared bridge transport inside its own transport
    window and returns the transport's result fact, ``read_observation`` and
    ``read_answer`` read the same socket through the same one client, activity
    reads the attempt's published sidecar, and inquiry states replay the
    durable journal through the shared-locked snapshot read. Nothing here
    upgrades a queued question, starts a native turn or touches the deadline.
    """

    def ask(question_id: str, question: str, timeout_ms: int):
        return bridge_request(credentials, "ask",
                              {"inquiryId": question_id, "question": question}, timeout_ms=timeout_ms)

    def read_activity():
        # The existing attempt-bound sidecar reader — validation, binding and
        # throttling belong to it; a foreign attempt's file reads as nothing.
        from ....protocol import activity as activity_protocol
        return activity_protocol.read_sidecar(Path(activity_path),
                                              task_id=identity.task_id,
                                              attempt_id=identity.attempt_id,
                                              generation=identity.generation)

    def read_journal():
        """The journal as one availability fact over the shared-locked snapshot read.

        The availability reasons follow the direct reader's own semantics,
        decided by a minimal state check beside the shared-locked read: no
        path, not written (the state check itself fails), unreadable (the state
        check passed but the locked open did not), actually over the byte cap,
        and — a real, readable file — empty with zero entries. ``entries`` is
        the reader's own deduplicated per-question count over every
        well-formed record, rejected ones included.

        Each question's fact follows its final effective record, the same
        last-record-per-id rule the direct reader applies: a latest legal
        record clears the id's older rejection and contributes its bound
        records (their source continuity intact), a latest foreign record
        contributes exactly one rejection with its own reason — never an
        accumulation of every historical refusal, and never an assumption that
        the last write was legal.
        """
        if not journal_path:
            return {"available": False, "reason": "no-journal-path", "entries": 0,
                    "records": [], "rejections": []}
        try:
            info = os.stat(journal_path)
        except OSError:
            return {"available": False, "reason": "journal-not-written", "entries": 0,
                    "records": [], "rejections": []}
        if not stat.S_ISREG(info.st_mode):
            return {"available": False, "reason": "journal-unreadable", "entries": 0,
                    "records": [], "rejections": []}
        if info.st_size > MAX_JOURNAL_BYTES:
            return {"available": False, "reason": "journal-exceeds-limit", "entries": 0,
                    "records": [], "rejections": []}
        raw = read_shared_snapshot(journal_path, MAX_JOURNAL_BYTES)
        if raw is None:
            # The state check passed and the locked open still failed: the
            # direct reader called this unreadable, not missing.
            return {"available": False, "reason": "journal-unreadable", "entries": 0,
                    "records": [], "rejections": []}
        bound = {"taskId": identity.task_id, "attemptId": identity.attempt_id,
                 "generation": identity.generation, "turnId": identity.turn_id}
        bound_records: dict[str, list[dict]] = {}
        rejection: dict[str, str] = {}
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn line is ignored, never fatal
            if not isinstance(record, dict):
                continue
            question_id = record.get("inquiryId")
            if not isinstance(question_id, str) or not question_id:
                continue
            if record.get("version") != INQUIRY_JOURNAL_VERSION or any(
                    key not in record or record[key] != bound[key]
                    for key in ("taskId", "attemptId", "generation", "turnId")):
                rejection[question_id] = _journal_rejection_reason(record, bound)
                continue
            # This legal record is the id's latest fact: it clears any older
            # rejection and keeps the id's valid-record source continuity.
            bound_records.setdefault(question_id, []).append(record)
            rejection.pop(question_id, None)
        # The final effective record per id decides, exactly like the direct
        # reader's last-record rule: an id whose latest record was foreign
        # contributes its one rejection and none of its earlier records.
        records = [record for question_id, question_records in bound_records.items()
                   if question_id not in rejection
                   for record in question_records]
        rejections = [{"questionId": question_id, "reason": rejection[question_id]}
                      for question_id in rejection]
        return {"available": True, "reason": None,
                "entries": len(bound_records.keys() | rejection.keys()),
                "records": records, "rejections": rejections}

    def read_observation(timeout_ms: int):
        result = _bridge_call(credentials, "observe", {}, timeout_ms)
        if result.get("ok") is True:
            return {"ok": True, "value": _observation_view(result["value"])}
        return result

    def read_answer(inquiry_id: str, timeout_ms: int):
        return _bridge_call(credentials, "answer", {"inquiryId": inquiry_id}, timeout_ms)

    capabilities: LiveCapabilities = EXISTING_CAPABILITIES["zcode"]
    return ExistingLiveChannel(
        identity, capabilities, ask=ask, read_activity=read_activity, read_journal=read_journal,
        read_observation=read_observation, read_answer=read_answer)


__all__ = [
    "ANSWERABLE_STATES", "ANSWERED_STATES", "BRIDGE_PROTOCOL_VERSION", "BRIDGE_WAIT_SECONDS",
    "InquiryBridge", "MAX_BRIDGE_FRAME_BYTES", "bind_live_channel",
]
