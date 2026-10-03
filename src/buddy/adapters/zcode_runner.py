"""Small stdlib controller for one governed ZCode native app-server turn."""
from __future__ import annotations

import argparse
from .. import locking
import hashlib
import json
import math
import os
import queue
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..activity import ActivitySidecar
from ..errors import BoardError
from ..private_dirs import ensure_private_dir
from .base import ProcessHandle
from .windows_process import owned_popen
from .turn_io import ASSISTANCE_HINTS, canonical_json, input_hash, private_json
from .zcode_config import SUPPORTED_ACCESS, cli_command, snapshot_provider_files
from .zcode_protocol import (COOPERATIVE_INQUIRY_NOTE, INQUIRY_JOURNAL_VERSION, MAX_ANSWER_BYTES, MAX_INQUIRIES,
                             MAX_INQUIRY_ID_BYTES, MAX_QUESTION_BYTES, ActivityProjection, NativeConnection,
                             NativeError, RootTurnEvidence, ZcodeAttemptUsage, decode_json, quota_native_code,
                             read_shared_snapshot)
from .zcode_tool_evidence import ZcodeToolFacts

MAX_JOURNAL_BYTES = 1024 * 1024
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

    The bridge lives inside the controller process because only this process holds
    the native connection. A Host question is only ever *queued* here; it reaches
    the root exclusively through the session-private ``buddy_checkpoint`` tool
    inside the one admitted native turn, and only the controller — after
    verifying that turn's own ``tool.updated`` evidence and the signed receipt —
    journals ``delivered`` or ``answered`` state. Nothing here injects input, and
    no MCP handler writes this journal. See
    :data:`zcode_protocol.COOPERATIVE_INQUIRY_NOTE` for the channel contract.

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

    # -- controller-authority transitions ------------------------------------
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
        before the tool call, not only in the controller's final report.
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


def governed_prompt(task_text: str, turn_input: dict, finish_tool: str, *,
                    checkpoint_tool: str | None = None, answer_tool: str | None = None) -> str:
    """Bounded governed root prompt: scope, inquiry channel, finish contract."""
    inquiry = (
        f"Host inquiries arrive cooperatively: call {checkpoint_tool} at natural work milestones and again just "
        f"before finishing to pick up any queued Host questions (an empty list means none). Answer each listed "
        f"question with {answer_tool} using its exact inquiryId. A completed finish is refused while a question "
        f"is still unanswered; a withdrawn or explicitly unavailable question no longer blocks it. Checkpointing "
        "is voluntary and never on a timer, and no input is ever injected into your turn."
    ) if checkpoint_tool and answer_tool else ""
    return "\n\n".join([
        "This is a governed Buddy root turn. Complete the authorized task using the available coding tools and internal subagents. Follow the frozen Host input and its allocated workspace.",
        f"Only the root may conclude this Buddy turn. After your work and internal subagents settle, obtain one successful receipt from {finish_tool} with the complete structured outcome. Include all six fields: disposition, summary, remaining, decisions, artifacts, request. Completed requires request: null. Use assistance for bounded help or attention for a Host decision. If a native permission or user-input request was refused, you must conclude with attention instead of completed. If a session tool refuses your call — including with a signed JSON refusal envelope naming the correction — correct the arguments and retry in this same turn. Plain final text is not a recorded outcome. After a successful finish receipt, do not start more tools; end the native turn.",
        inquiry,
        *ASSISTANCE_HINTS,
        task_text, canonical_json(turn_input),
    ])



def selected(snapshot: dict) -> dict:
    value = snapshot.get("settings", {}).get("model", {}).get("current")
    if not isinstance(value, dict) or not value.get("providerId") or not value.get("modelId"):
        raise NativeError("configuration-unavailable", "ZCode has no usable native model selection")
    return {"provider": value["providerId"], "model": value["modelId"],
            "effort": value.get("options", {}).get("reasoningLevel")}


def configure_session(connection: NativeConnection, snapshot: dict, spec: dict, access: dict) -> dict:
    session_id = snapshot["session"]["sessionId"]
    if any(not isinstance(spec.get(k), str) or not spec[k].strip() for k in ("provider", "model", "effort")):
        raise NativeError("invalid-configuration", "ZCode coding requires a complete provider, model and effort after routing")
    provider, model, effort = spec["provider"], spec["model"], spec["effort"]
    if access.get(provider) not in SUPPORTED_ACCESS:
        raise NativeError("unsupported-provider", "this ZCode adapter supports API-key providers; OAuth account providers require a native authentication host")
    choices = snapshot.get("settings", {}).get("model", {}).get("available", [])
    choice = next((x for x in choices if x.get("ref") == {"providerId": provider, "modelId": model}), None)
    if choice is None:
        raise NativeError("configuration-unavailable", "the requested ZCode provider/model is not in the native available catalog")
    reasoning = choice.get("reasoning", {})
    efforts = [x["value"] for x in reasoning.get("levels", []) if isinstance(x, dict) and isinstance(x.get("value"), str)]
    if effort not in efforts:
        raise NativeError("invalid-configuration", "the requested reasoning effort is not supported by this native model")
    target = {"providerId": provider, "modelId": model, "options": {"reasoningLevel": effort}}
    snapshot = connection.call("session/setModel", {"sessionId": session_id, "model": target, "persistAsWorkspaceLastUsed": False})
    snapshot = connection.call("session/setThoughtLevel", {"sessionId": session_id, "thoughtLevel": effort, "persistAsWorkspaceLastUsed": False})
    actual = selected(snapshot)
    expected = {"provider": provider, "model": model, "effort": effort}
    if actual != expected or snapshot.get("settings", {}).get("thoughtLevel", {}).get("current") != effort:
        raise NativeError("configuration-mismatch", "native ZCode did not retain the requested model and reasoning settings")
    return actual


def catalog(snapshot: dict, access: dict, version: str) -> dict:
    providers: dict[str, dict] = {}
    missing_effort = False
    for model in snapshot.get("settings", {}).get("model", {}).get("available", []):
        ref = model.get("ref") or {}
        provider = ref.get("providerId")
        if access.get(provider) not in SUPPORTED_ACCESS or not ref.get("modelId"):
            continue
        efforts = [x["value"] for x in model.get("reasoning", {}).get("levels", [])
                   if isinstance(x, dict) and isinstance(x.get("value"), str) and x["value"].strip()]
        if not efforts:
            missing_effort = True
            continue
        entry = providers.setdefault(provider, {"provider": provider, "displayName": model.get("providerLabel") or provider,
                                                 "adapter": "zcode", "packageVersion": version, "accessType": access[provider], "models": []})
        entry["models"].append({"id": ref["modelId"], "name": model.get("label") or ref["modelId"],
                                "efforts": efforts,
                                "contextWindow": model.get("contextWindow"), "available": True,
                                "inputModalities": [name for name in ("text", "image", "audio", "video", "pdf")
                                                    if model.get("properties", {}).get("inputFormat", {}).get("supports" + name.capitalize()) is True]})
    warnings = ["OAuth account providers are unavailable through this adapter"] if any(t == "zhipu-account" for t in access.values()) else []
    if missing_effort:
        warnings.append("Models exposing no configurable native reasoning efforts were omitted")
    if not providers:
        # A successful native snapshot with no usable API-key provider is a
        # complete observation, not a discovery failure: the service can retire
        # profiles that are missing from it instead of leaving them unknown
        # forever. The explicit ``discoveries`` status states that boundary.
        warnings.append("The native app server returned no API-key provider; this complete empty observation retires missing ZCode profiles")
    return {"source": "zcode-native-app-server", "adapter": "zcode", "harnessVersion": version,
            "discoveredAt": datetime.now(timezone.utc).isoformat(), "providers": list(providers.values()),
            "discoveries": [{"adapter": "zcode", "status": "complete"}], "warnings": warnings}


def execution_deadline(timeout_seconds) -> float:
    """The one overall native execution deadline; an explicit 0 means unlimited.

    Only this deadline becomes infinite. The version probe and the per-request,
    cancel and shutdown waits keep their own finite bounds, and the ``cancelled``
    event still ends an unlimited turn.
    """
    return math.inf if timeout_seconds == 0 else time.monotonic() + timeout_seconds


class NoToolEvidence:
    """Fail closed on every tool event, including child and unbound tool calls."""

    # Model text may be streamed in these native event families. Unknown events
    # cannot establish zero-tool provenance on a future protocol revision.
    MODEL_EVENTS = {"message.upserted", "message.removed", "part.started", "part.delta",
                    "part.upserted", "part.removed", "model.streaming"}
    SESSION_EVENTS = {"session.created", "session.resumed", "session.updated", "session.titleUpdated", "session.closed"}
    OPERATION_EVENTS = {"turn-started", "turn-completed", "turn-failed", "session-closed"}
    TELEMETRY_EVENTS = {"turn.started", "model.request.status", "stream.chunk", "usage.delta", "turn.terminal"}

    def __init__(self, session_id: str, input_id: str, tools: ZcodeToolFacts | None = None):
        self.session_id, self.input_id = session_id, input_id
        self.tools = tools
        self.turn_id = None
        self.last_seq = -1
        self.completed = False
        self.settled = False
        self.raw_answer = None
        self.events = 0
        self.metadata_sequences = {}
        self.metadata_turn = None

    def observe_metadata(self, method: str, params: dict) -> None:
        """Native lifecycle projections precede canonical events; they prove no answer."""
        operation = method == "computer-use/operation-event"
        kinds = self.OPERATION_EVENTS if operation else self.TELEMETRY_EVENTS
        sequence = params.get("sequenceNumber" if operation else "eventSeq")
        if (params.get("kind") not in kinds or params.get("sessionId") != self.session_id
                or type(sequence) is not int or sequence <= self.metadata_sequences.get(method, -1)):
            raise NativeError("invalid-protocol", "invalid no-tool native lifecycle metadata")
        self.metadata_sequences[method] = sequence
        if params["kind"] == "session-closed":
            return
        turn = params.get("turnId")
        if (not isinstance(turn, str) or not turn
                or self.turn_id is not None and turn != self.turn_id
                or self.metadata_turn is not None and turn != self.metadata_turn):
            raise NativeError("wrong-native-turn", "no-tool lifecycle metadata differs from the admitted turn")
        self.metadata_turn = turn
        if params["kind"] == "turn-failed":
            raise NativeError("native-turn-failed", "no-tool native turn failed")

    def observe(self, message: dict, _ordinal: int) -> None:
        if self.tools is not None:
            # Native tool facts are projected before any rejection or
            # session/turn filter: a refused, foreign or child call stays in
            # the evidence the receipt carries, whatever this method decides.
            self.tools.observe(message)
        _reject_no_tool_events(message)
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            raise NativeError("invalid-protocol", "no-tool native event has no object parameters")
        if method in ('startup/storageState', 'process/mcpTelemetry', 'process/mcpResourceSamples', 'process/resourceSample'):
            return
        if method in ("computer-use/operation-event", "v4/telemetry/event"):
            self.observe_metadata(method, params)
            return
        if method == "session/event":
            kind = params.get("type")
            # Inspect before session/turn filtering: relayed child and MCP calls
            # are still violations even without a toolCallId.
            if kind == "tool.updated" or isinstance(kind, str) and (kind.startswith("tool.") or kind.startswith("agent.")):
                raise NativeError("no-tool-violation", "native tool or subagent event in a no-tool call")
            if kind not in {"turn.started", "turn.completed", "turn.failed", *self.MODEL_EVENTS, *self.SESSION_EVENTS}:
                raise NativeError("invalid-protocol", "unknown no-tool native event")
            if params.get("sessionId") != self.session_id:
                raise NativeError("invalid-protocol", "foreign session event in a no-tool call")
            seq = params.get("seq")
            if type(seq) is not int or seq <= self.last_seq:
                raise NativeError("invalid-protocol", "no-tool native event order is invalid")
            self.last_seq = seq
            self.events += 1
            data = params.get("payload")
            if not isinstance(data, dict):
                raise NativeError("invalid-protocol", "no-tool native event payload is invalid")
            if kind in self.SESSION_EVENTS:
                return
            if kind == "turn.started":
                if (self.turn_id is not None or data.get("inputId") != self.input_id
                        or not isinstance(params.get("turnId"), str)
                        or self.metadata_turn is not None and params.get("turnId") != self.metadata_turn):
                    raise NativeError("wrong-native-turn", "no-tool turn identity differs")
                self.turn_id = params["turnId"]
                if self.tools is not None:
                    # The trusted root identity comes only from this verified
                    # canonical turn start, never from the observed events.
                    self.tools.add_root(self.session_id, self.turn_id)
            elif not self.turn_id or params.get("turnId") != self.turn_id:
                raise NativeError("wrong-native-turn", "no-tool event differs from the admitted turn")
            elif kind == "turn.failed":
                raise NativeError("native-turn-failed", "no-tool native turn failed")
            elif kind == "turn.completed":
                if self.completed or data.get("inputId") != self.input_id or data.get("resultType") != "success":
                    raise NativeError("native-turn-failed", "no-tool native turn did not complete successfully")
                self.raw_answer = data.get("response")
                if not isinstance(self.raw_answer, str) or len(self.raw_answer.encode()) > 65536:
                    raise NativeError("invalid-native-result", "no bounded native answer")
                self.completed = True
        elif method == "state.updated":
            if params.get("sessionId") not in (None, self.session_id):
                raise NativeError("invalid-protocol", "foreign no-tool state event")
            if params.get("reason") == "prompt_failed":
                raise NativeError("native-turn-failed", "no-tool prompt failed")
            if params.get("reason") == "prompt_completed":
                if not self.completed or self.settled:
                    raise NativeError("invalid-protocol", "no-tool settlement lacks a completed turn")
                self.settled = True
        else:
            label = method if isinstance(method, str) and re.fullmatch(r'[A-Za-z0-9/._-]{1,80}', method) else 'unknown'
            raise NativeError("invalid-protocol", "unknown no-tool native notification: " + label)


def _reject_no_tool_events(value) -> None:
    """Native tool parts count even before admission, without ids or in child events."""
    if isinstance(value, dict):
        kinds = (value.get('type', ''), value.get('kind', ''))
        if (any(isinstance(kind, str) and kind.startswith(
                ('tool', 'agent.', 'permission.', 'userInput.', 'subagent.', 'workflow.')) for kind in kinds)
                or value.get('toolCallId') is not None or value.get('role') == 'tool'
                or any(type(value.get(key)) is int and value[key] > 0 for key in ('toolCalls', 'toolCallCount'))):
            raise NativeError('no-tool-violation', 'native tool or interaction event in a no-tool call')
        for item in value.values():
            if isinstance(item, (dict, list)):
                _reject_no_tool_events(item)
    elif isinstance(value, list):
        for item in value:
            _reject_no_tool_events(item)


def _no_tool_preflight(message: dict, _ordinal: int) -> None:
    _reject_no_tool_events(message)
    params = message.get("params") or {}
    if message.get('method') in ('startup/storageState', 'process/mcpTelemetry', 'process/mcpResourceSamples', 'process/resourceSample') and isinstance(params, dict):
        return
    if message.get("method") == "session/event" and isinstance(params, dict):
        kind = params.get("type")
        if kind in NoToolEvidence.SESSION_EVENTS:
            return
    if message.get('method') == 'state.updated' and isinstance(params, dict):
        return
    if (message.get('method') == 'computer-use/operation-event' and isinstance(params, dict)
            and params.get('kind') == 'session-closed' and isinstance(params.get('sessionId'), str)
            and type(params.get('sequenceNumber')) is int):
        return
    method = message.get('method')
    label = method if isinstance(method, str) and re.fullmatch(r'[A-Za-z0-9/._-]{1,80}', method) else 'unknown'
    raise NativeError("invalid-protocol", "unexpected native event before no-tool admission: " + label)


def _no_tool_call(connection: NativeConnection, control: dict, result: dict, workspace: dict, access: dict,
                  tools: ZcodeToolFacts) -> str:
    from .read_only import correction_code, no_tool_prompt, valid_answer
    request = control["noToolRequest"]
    spec = control["spec"]
    base_prompt = no_tool_prompt(request["prompt"], request["outputSchema"])
    prompt = base_prompt
    for attempt in range(2):
        connection.observe = tools.observe_with(_no_tool_preflight)
        snapshot = connection.call("session/create", {
            "workspace": workspace, "titleGenerationEnabled": False, "toolAllowlist": [],
            "mcpServers": [], "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False})
        session = snapshot.get("session") or {}
        session_id = session.get("sessionId")
        if not isinstance(session_id, str) or not session_id or session.get("parentSessionId"):
            raise NativeError("wrong-native-session", "no-tool call requires a root native session")
        if (session.get("workspace") or {}).get("workspacePath") != control["cwd"]:
            raise NativeError("wrong-native-workspace", "no-tool native workspace differs")
        result["resolved"] = configure_session(connection, snapshot, spec, access)
        result["sessionId"] = session_id
        input_id = "buddy-no-tool-" + secrets.token_hex(16)
        evidence = NoToolEvidence(session_id, input_id, tools)
        connection.observe = evidence.observe
        connection.call("session/subscribe", {"sessionId": session_id,
                        "deliveryKind": "web-remote-replayable", "includeSnapshot": False})
        requests = []
        connection.attention = requests.append
        result["modelStarted"] = True
        accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id, "content": prompt})
        if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
            raise NativeError("native-admission-failed", "no-tool input was not admitted")
        while not evidence.settled:
            connection.pump()
            if requests:
                raise NativeError("no-tool-violation", "native interaction requested in a no-tool call")
        tools.close_pending = True
        closed = connection.call("session/close", {"sessionId": session_id})
        if closed.get("closed") is not True:
            raise NativeError("session-close-unconfirmed", "no-tool session close was not acknowledged")
        tools.close_pending = False
        result.update(rawAnswer=evidence.raw_answer, answerValid=valid_answer(evidence.raw_answer, request["outputSchema"]),
                      nativeIdentity={"sessionId": session_id, "turnId": evidence.turn_id},
                      usage={"toolCalls": tools.tool_calls}, correctionCount=attempt,
                      nativeEventCount=evidence.events)
        correction = correction_code(evidence.raw_answer, request["outputSchema"])
        if correction is None or attempt:
            return session_id
        prompt = base_prompt + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    return session_id


def _drain_structured(connection, result: dict, tools: ZcodeToolFacts, channel: str) -> bool:
    """Keep every stopped stream frame, even after an earlier failure."""
    complete = True

    def failed(error):
        nonlocal complete
        complete = False
        if result["status"] == "ok":
            result.update(status="error", code=error.code, error=str(error))
        if error.code == "no-tool-violation":
            tools.violation = True

    while True:
        try:
            message = connection.messages.get(timeout=1)
        except queue.Empty:
            failed(NativeError("invalid-protocol", f"{channel} native stream ended without EOF"))
            return False
        if message is None:
            return complete
        if isinstance(message, NativeError):
            failed(message)
            continue
        try:
            # Projection precedes rejection, and an observer failure never
            # prevents the following frames from being projected too.
            connection.observe(message, 0)
            if "id" in message and "method" in message:
                raise NativeError("no-tool-violation", f"native interaction after {channel} settlement")
            if "id" in message:
                raise NativeError("invalid-protocol", f"unclaimed native response after {channel} settlement")
        except NativeError as error:
            failed(error)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, BoardError):
            tools.evidence.observe_incomplete("zcode", {})
            failed(NativeError("invalid-protocol", f"{channel} native frame could not be recorded"))


def run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    if "readOnlyRequest" in control:
        return {"status": "error", "code": "readonly-worker-carrier-unimplemented", "modelStarted": False,
                "processState": {"shutdownConfirmed": True}}, 1
    started_at = time.monotonic()
    deadline = execution_deadline(control["timeoutSeconds"])
    directory = Path(control["directory"])
    root = Path(control["nativeRoot"])
    for path in (directory, root):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    from ..harness_discovery import native_environment
    incoming = dict(os.environ)
    command = cli_command(incoming)
    clean = native_environment(incoming, command=command)
    private = ensure_private_dir(Path(control.get("privateRoot") or directory))
    environment, access = snapshot_provider_files(private, {**incoming, **clean})
    # These paths were generated by the controller above, not inherited model endpoints.
    clean.update({key: environment[key] for key in ('ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE')})
    environment = clean
    if incoming.get("BUDDY_DEV_SOURCE") == "1" and "BUDDY_ZCODE_TEST_CASE" in incoming:
        environment["BUDDY_ZCODE_TEST_CASE"] = incoming["BUDDY_ZCODE_TEST_CASE"]
    # ZCODE_MODEL_TELEMETRY_ENABLED=0 short-circuits the single telemetry gate in
    # the installed bundle before it reads ZCODE_HOME (telemetry-state.json is only
    # touched by that path), so the previous ZCODE_HOME override is unnecessary and
    # no telemetry state can reach the real user home. The session DB, storage and
    # logs stay in this attempt's private native root.
    environment.update({"ZCODE_STORAGE_DIR": str(root / "storage"), "ZCODE_SESSION_DB_PATH": str(root / "sessions.sqlite"),
                        "ZCODE_LOG_DIR": str(private / "native-logs"), "ZCODE_LOG_CONSOLE": "0",
                        "ZCODE_MODEL_TELEMETRY_ENABLED": "0"})
    try:
        version_result = subprocess.run([*command, "--version"], env=environment, cwd=control["cwd"], capture_output=True, timeout=5)
        version = version_result.stdout.decode(errors="replace").strip()[:80] if version_result.returncode == 0 else "unknown"
    except subprocess.TimeoutExpired:
        # Version text is optional metadata. A slow --version probe must not
        # suppress the actual native capability/catalog handshake below.
        version = "unknown"
    native_stderr = directory / "native.stderr.log"
    fd = os.open(native_stderr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        process = owned_popen([*command, "app-server", "--cwd", control["cwd"]], env=environment,
                                   cwd=control["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                                   start_new_session=True, close_fds=True)
    except OSError:
        return {'status': 'error', 'code': 'adapter-unavailable', 'modelStarted': False,
                'error': 'The selected ZCode executable could not start', 'processState': {'shutdownConfirmed': True}}, 1
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    result = {"status": "error", "mode": "zcode", "harnessVersion": version,
              "requested": control.get("spec"), "resolved": None, "observed": None, "modelStarted": False}
    # The fast structured entry keeps one fact collector before
    # the native handshake, no MCP finish bridge, no governed turn authority.
    structured = bool(control.get("noToolRequest"))
    record = None
    session_id = None
    connection = None
    tools: ZcodeToolFacts | None = None
    inquiry_bridge: InquiryBridge | None = None
    attempt_usage: ZcodeAttemptUsage | None = None
    try:
        connection = NativeConnection(process, deadline, cancelled, no_tools=structured)
        if structured:
            # The collector's binding is the control file's own program
            # identity; the projection spans every correction session and is
            # installed before the runtime handshake so no native tool fact is
            # lost. Refused tool.updated calls remain in the evidence.
            tools = ZcodeToolFacts({"adapter": "zcode", "taskId": control["taskId"],
                                    "attemptId": control["attemptId"], "generation": control["generation"]})
            connection.observe = tools.observe_with(_no_tool_preflight)
        connection.call("runtime/capabilities", {})
        workspace = {"workspacePath": control["cwd"], "workspaceKey": control["cwd"]}
        if control.get("discover"):
            snapshot = connection.call("session/create", {"workspace": workspace, "titleGenerationEnabled": False, "toolAllowlist": []})
            session_id = snapshot["session"]["sessionId"]
            result = {**result, "status": "ok", "catalog": catalog(snapshot, access, version)}
        elif control.get("noToolRequest"):
            session_id = _no_tool_call(connection, control, result, workspace, access, tools)
            result.update(status="ok")
        else:
            turn_input = decode_json(Path(control["inputFile"]).read_bytes())
            if not isinstance(turn_input, dict):
                raise NativeError("invalid-input", "the governed input is not an object")
            identity = {k: turn_input[k] for k in ("taskId", "attemptId", "generation", "turnId")}
            inquiry = control.get("inquiry") if isinstance(control.get("inquiry"), dict) else None
            attention_path = directory / "attention.json"
            bridge = {"identity": identity, "inputSha256": input_hash(turn_input), "key": secrets.token_hex(32),
                      "attentionPath": str(attention_path)}
            if inquiry is not None and isinstance(inquiry.get("resultsPath"), str):
                # The MCP tools only read this journal; every record in it is
                # written by this controller after root-turn evidence verified.
                bridge["inquiryJournalPath"] = inquiry["resultsPath"]
            bridge_path = private / "finish-bridge.json"
            private_json(bridge_path, bridge, exclusive=True)
            server_name = "buddy_" + hashlib.sha256(identity["attemptId"].encode()).hexdigest()[:16]
            finish_tool = f"mcp__{server_name}__buddy_finish_turn"
            checkpoint_tool = f"mcp__{server_name}__buddy_checkpoint"
            answer_tool = f"mcp__{server_name}__buddy_answer_inquiry"
            mcp = [{"name": server_name, "command": sys.executable,
                    "args": ["-m", "buddy.adapters.zcode_mcp", "--config", str(bridge_path)],
                    "env": [{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}] if os.environ.get("PYTHONPATH") else [],
                    "isolation": "session", "protocolVersion": "legacy"}]
            mode = turn_input.get("resumeMode")
            previous = turn_input.get("previousSessionId")
            configuration = control.get("spec", {})
            if previous is not None and (not isinstance(previous, str) or not previous.strip()):
                raise NativeError("invalid-resume-mode", "the previous native session identity must be null or a nonblank string")
            if mode == "native-session":
                if not isinstance(previous, str) or not previous:
                    raise NativeError("native-resume-unavailable", "native resume requires the exact previous session identity")
                binding_path = root / (hashlib.sha256(previous.encode()).hexdigest() + ".json")
                try:
                    binding = decode_json(binding_path.read_bytes())
                except (OSError, ValueError):
                    raise NativeError("native-resume-unavailable", "the previous native session has no private goal binding") from None
                if binding != {"taskId": identity["taskId"], "sessionId": previous, "cwd": control["cwd"], "configuration": configuration}:
                    raise NativeError("native-resume-unavailable", "the previous native session does not match this goal, checkout and configuration")
                snapshot = connection.call("session/resume", {"sessionId": previous, "workspace": workspace, "mcpServers": mcp})
            elif mode == "reconstructed-new-session" or mode == "initial" and previous is None:
                snapshot = connection.call("session/create", {"workspace": workspace, "mode": "yolo", "titleGenerationEnabled": False, "mcpServers": mcp})
            else:
                raise NativeError("invalid-resume-mode", "ZCode requires initial, an explicitly bound native-session or a reconstructed-new-session turn")
            session = snapshot.get("session") or {}
            session_id = session.get("sessionId")
            if not isinstance(session_id, str) or not session_id or session.get("parentSessionId") or session.get("sessionKind") not in (None, "interactive"):
                raise NativeError("wrong-native-session", "ZCode did not create or restore a root session")
            if mode == "native-session" and session_id != previous:
                raise NativeError("wrong-native-session", "ZCode resumed a different native session")
            if mode == "reconstructed-new-session" and session_id == previous:
                raise NativeError("wrong-native-session", "ZCode reused the previous session instead of creating the requested new root")
            native_cwd = (session.get("workspace") or {}).get("workspacePath")
            if not native_cwd or Path(native_cwd).resolve() != Path(control["cwd"]).resolve():
                raise NativeError("wrong-native-workspace", "ZCode session checkout does not match the allocated workspace")
            result["resolved"] = configure_session(connection, snapshot, configuration, access)
            binding_path = root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")
            if mode != "native-session":
                private_json(binding_path, {"taskId": identity["taskId"], "sessionId": session_id, "cwd": control["cwd"],
                                           "configuration": result["resolved"]}, exclusive=True)
            input_id = "buddy-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
            evidence = RootTurnEvidence(
                session_id, input_id, finish_tool, bridge,
                checkpoint_name=checkpoint_tool if inquiry is not None else None,
                answer_name=answer_tool if inquiry is not None else None,
                on_delivery=(lambda receipt, call_id: inquiry_bridge.deliver_inquiries(receipt, call_id)) if inquiry is not None else None,
                on_answer=(lambda receipt, call_id: inquiry_bridge.record_answer(receipt, call_id)) if inquiry is not None else None,
            )
            projection = ActivityProjection(session_id)
            attempt_usage = ZcodeAttemptUsage(session_id, resumed=mode == "native-session")
            # The real helper owns validation, atomic replacement and throttling;
            # its state lives for the whole controller so same-phase updates are
            # coalesced instead of rewriting the sidecar for every token event.
            sidecar = ActivitySidecar(directory, task_id=identity["taskId"], attempt_id=identity["attemptId"],
                                      generation=identity["generation"])

            def publish_activity() -> None:
                try:
                    written = sidecar.publish(projection.payload())
                except BoardError as error:
                    # Metadata must never fail the native turn; the reason is
                    # reported instead of writing a look-alike sidecar.
                    result["activity"] = {"published": False, "reason": f"buddy.activity rejected the receipt ({error.code})"}
                    return
                result["activity"] = {"published": True, "coalesced": written is None,
                                      "phase": projection.phase, "eventSeq": projection.event_seq}

            if inquiry is not None:
                inquiry_bridge = InquiryBridge(inquiry, identity=identity,
                                               journal_path=str(inquiry.get("resultsPath") or ""),
                                               attention_path=str(attention_path))
                inquiry_bridge.start()
                result["inquiry"] = inquiry_bridge.report()

            def observe(message: dict, ordinal: int) -> None:
                evidence.observe(message, ordinal)
                if attempt_usage is not None:
                    # Attempt usage is observed next to the root-turn evidence; a
                    # foreign session or an unstarted turn contributes nothing.
                    attempt_usage.observe(message, evidence.turn_id)
                projection.note(message, ordinal)
                if inquiry_bridge is not None:
                    inquiry_bridge.note_event(message, projection.phase)
                # Native events in a long phase still advance the observation.
                # The sidecar coalesces token-level updates within its own window.
                publish_activity()

            connection.observe = observe
            connection.attention = (lambda record: inquiry_bridge.note_attention(record)) if inquiry_bridge else (lambda _record: None)
            connection.call("session/subscribe", {"sessionId": session_id, "deliveryKind": "web-remote-replayable", "includeSnapshot": False})
            projection.phase = "waiting-model"
            publish_activity()
            prompt = governed_prompt(Path(control["taskFile"]).read_text(), turn_input, finish_tool,
                                     checkpoint_tool=checkpoint_tool if inquiry is not None else None,
                                     answer_tool=answer_tool if inquiry is not None else None)
            # The pre-model cursor is a bounded, optional read: it can never fail
            # or delay the turn, and without it no message is ever attributed.
            attempt_usage.capture_baseline(connection)
            result["modelStarted"] = True
            accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id, "content": prompt})
            if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
                raise NativeError("native-admission-failed", "ZCode did not admit the intended root input")
            if inquiry_bridge is not None:
                inquiry_bridge.activate(session_id)
            while not evidence.settled_ordinal:
                connection.pump()
            # The settlement read is also bounded and optional; the root turn is
            # already settled, so nothing here may change its result.
            attempt_usage.capture_final(connection)
            if inquiry_bridge is not None:
                # Stop accepting observations the instant the root turn settled:
                # an idle or finished agent is never woken for an inquiry.
                inquiry_bridge.close()
            projection.phase = "finishing"
            publish_activity()
            record = {"version": 1, **identity, "inputSha256": bridge["inputSha256"],
                      "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(), "sessionId": session_id,
                      "previousSessionId": previous, "resumeMode": mode, "outcome": evidence.receipt["outcome"]}
            result.update(status="ok", sessionId=session_id)
        # The no-tool and read-only loops close every session themselves,
        # including a corrected turn; only the governed turn is closed here.
        if not structured:
            closed = connection.call("session/close", {"sessionId": session_id})
            if closed.get("closed") is not True:
                raise NativeError("session-close-unconfirmed", "ZCode did not acknowledge closing the native session")
        if record is not None:
            evidence.close_ordinal = connection.ordinal
            record["provenance"] = evidence.provenance()
    except NativeError as error:
        result.update(status="cancelled" if error.code == "cancelled" else "error", code=error.code, error=str(error))
        if control.get("noToolRequest") and error.code == "no-tool-violation" and tools is not None:
            # The receipt's count comes from the projected facts; a refused
            # interaction the projection cannot name still counts as one.
            tools.violation = True
        if error.code == "native-disconnected":
            result["failureKind"] = "transport"
        # Whitelisted native failure attribution (only the exported turn.failed
        # event supplies it) is observable on the failed result; it never
        # changes the status, code or failureKind, so this stays a harness
        # error distinct from a deadline, a user cancel or a transport break.
        failure = getattr(error, "failure", None)
        if isinstance(failure, dict):
            result["nativeFailure"] = failure
            # A structured native failure code that means quota exhaustion is
            # published as the explicit quota reason; provider prose is never read
            # and no retry or probe is attempted.
            native_code = quota_native_code(failure)
            if native_code is not None:
                result["quotaFailure"] = {"nativeCode": native_code, "source": "zcode/session-turn-failed",
                                          "observedAt": _now()}
        # A failed turn still retains its native observations: the bounded read is
        # optional, so it can never turn this failure into another one.
        if attempt_usage is not None and connection is not None:
            attempt_usage.capture_final(connection)
        record = None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result.update(status="error", code="invalid-native-result", error="the native execution returned invalid or incomplete data")
        record = None
    finally:
        if attempt_usage is not None:
            # ADR-018 items 22/23 and the retained root assistant text: raw native
            # observations for every path, including a quota or process failure. A
            # value the native records never proved stays null.
            result["tokenUsage"] = attempt_usage.raw_usage()
            result["lastAssistantMessage"] = attempt_usage.last_assistant_message
        if inquiry_bridge is not None:
            # Closing before the process disappears keeps an after-end question
            # honest instead of leaving a dangling observation.
            inquiry_bridge.close()
            result["inquiry"] = inquiry_bridge.report()
            result["nativeAttention"] = inquiry_bridge.attention_report()
        try:
            process.stdin.close()
        except OSError:
            pass
        handle.wait(min(3.0, max(0.0, deadline - time.monotonic())))
        if not handle.shutdown_confirmed(settle_seconds=0.2):
            handle.terminate(grace_seconds=1.0)
        shutdown = handle.shutdown_confirmed(settle_seconds=0.5)
        result["processState"] = {"shutdownConfirmed": shutdown, "nativeExitCode": process.returncode}
        if result["status"] == "ok" and (not shutdown or process.returncode != 0):
            result.update(status="error", code="native-shutdown-failed", error="the native app server did not exit normally with confirmed group shutdown")
            record = None
        eof = False
        if structured and shutdown and connection is not None and tools is not None:
            # The native server has exited. Read through its terminal EOF so an
            # event queued after settlement cannot hide behind close/ack. A late
            # tool frame keeps its projected fact and marks the stream
            # incomplete; whether any recorded call was allowed is judged only
            # by the blackboard, never here.
            channel = "no-tool" if control.get("noToolRequest") else "read-only"
            eof = _drain_structured(connection, result, tools, channel)
            if (result["status"] == "ok" and isinstance(control.get("noToolRequest"), dict)
                    and control["noToolRequest"].get("captureEvidence")):
                result["nativeEvidence"] = {"eventCount": result.get("nativeEventCount"),
                                            "toolAllowlist": [], "titleGenerationEnabled": False,
                                            "streamEof": True}
        if tools is not None:
            # Every structured receipt carries the attempt's toolEvidence: the
            # stream is complete only when it drained to EOF, each root session
            # closed with an acknowledged close and the owned process stopped.
            # usage counts come from the collector alone, the accumulated time
            # spans every answer round, and unread bytes stay honestly null.
            calls = tools.tool_calls
            result["usage"] = {**(result.get("usage") or {}), "toolCalls": calls, "bytesRead": None,
                               "elapsedMs": round((time.monotonic() - started_at) * 1000)}
            if result["status"] == "ok" and control.get("noToolRequest"):
                # Only the no-tool channel claims zero tools; a read-only call's
                # allowance is judged by the blackboard from the recorded facts.
                result["zeroToolVerified"] = calls == 0
        process.stdout.close()
    if cancelled.is_set():
        result.update(status="cancelled", code="cancelled", error="the owned ZCode execution was cancelled")
        record = None
    if result["status"] == "ok" and (not shutdown or process.returncode != 0):
        result.update(status="error", code="native-shutdown-failed", error="the native app server did not exit normally with confirmed group shutdown")
        record = None
    if result["status"] != "ok":
        result.pop("zeroToolVerified", None)
    if tools is not None:
        result["toolEvidence"] = tools.finish(bool(result["status"] == "ok"
                                                  and eof and shutdown and not tools.close_pending))
    if record is not None:
        private_json(Path(control["outputFile"]), record, exclusive=True)
        result["nativeTurnId"] = record["provenance"]["nativeTurnId"]
    return result, 0 if result["status"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = run(json.loads(Path(args.control).read_text()), cancelled)
    except (NativeError, OSError, ValueError, subprocess.TimeoutExpired):
        result, code = {"status": "error", "code": "zcode-controller-failed", "error": "ZCode controller failed before verified native settlement",
                        "processState": {"shutdownConfirmed": False}}, 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
