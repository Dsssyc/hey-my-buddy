"""One bounded NDJSON JSON-RPC 2.0 stdio connection to a launched ACP process.

A reader thread turns the child's stdout into messages. Every frame is
validated against the JSON-RPC 2.0 envelope before it can correlate with
anything: the version member, string methods, id legal types (int or non-empty
string, never bool), the request/response mutual exclusion of result and error
on both directions, array-or-object params, and error objects carrying an
integer code and a string message. A frame failing validation is a bounded
protocol fault and is dropped - it never kills the reader, never resolves a
waiter and never masquerades as this side's id. Invalid UTF-8 and depths that
break the JSON parser are faults too, never silently replaced content. A
response completes atomically: ``resolved``, the message and the wake are one
transition, so a deadline can never observe a response that is half-arrived.
Responses are matched by id (first wins); one for an id this side sent and
already timed out is ``late``, one for an answered id is ``duplicate``,
anything else is ``unknown``. Requests from the agent are answered through
handlers - the permission handler answers with the nested
``RequestPermissionOutcome`` shape, everything else is refused with ``-32601``
- and both are recorded with closed-set metadata only; a malformed
``toolCall`` or ``options`` field is recorded conservatively exactly like the
answer it receives. Observer callbacks receive live notifications; their
failures are recorded facts, never silently swallowed.

All writes - requests, notifications, cancel, and the reader's agent-directed
replies - go through one bounded writer thread with a bounded queue and one
budget per frame that covers queuing and writing. The queued/cancelled/
started/settled transitions are atomic under the queue lock: the requester's
timeout observation and the writer's claim meet in one critical section, so a
cancelled job never starts writing and a completed job is never reversed into
an uncertain one by a late observation; only a genuinely started write whose
completion is unconfirmed becomes an uncertain fact, and it settles as
delivered or failed. A frame that expires while queued is never sent, and
every failure is a fact. The writer is never per-call, so stalled stdin
accumulates neither threads nor expired frames.

Retention carries no content: raw frames are reduced to direction, byte count
and a short digest (an optional metadata-only log persists exactly that), and
every in-memory fact is closed-set metadata, counts, lengths or digests. Raw
text reaches only the live observer callbacks, the exception object handed to
the request caller, and the writer's bytes. Three truncation facts are kept
distinct: a source-stream fault (protocol faults), an observation failure, and
retention truncation of the optional metadata log - one never strengthens or
weakens another. Stderr is kept as a bounded tail. No credential value is ever
recorded.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from ..... import private_dirs
from .....errors import BoardError

#: A single stdout line larger than this is a broken-frame fact, not a message.
MAX_LINE_BYTES = 1024 * 1024

#: Outbound frames larger than this are refused before anything is written.
MAX_OUTBOUND_BYTES = 1024 * 1024

#: Total metadata-log size before truncation; the fact that truncation happened
#: is itself retained.
FRAME_LOG_CAP_BYTES = 8 * 1024 * 1024

#: Bounded stderr tail kept for failure reports.
STDERR_TAIL_BYTES = 64 * 1024

#: The single writer's bounded queue and the deadline for reader-side replies.
_MAX_WRITE_QUEUE = 16
_REPLY_WRITE_SECONDS = 3.0
_NOTIFY_WRITE_SECONDS = 10.0
_WRITER_JOIN_SECONDS = 5.0

_MAX_FAULT_RECORDS = 50
_MAX_UNMATCHED_RECORDS = 50
_MAX_DECISION_RECORDS = 200
_MAX_DENIED_RECORDS = 200
_MAX_OBSERVER_RECORDS = 50
_MAX_WRITE_RECORDS = 50
_MAX_NOTIFICATION_KINDS = 64
_MAX_OPTIONS_PER_DECISION = 16
_ID_TEXT_LIMIT = 64


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def brief(value, limit: int) -> str:
    """A bounded, single-line text rendering used for closed-set labels only."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"...<+{len(text) - limit} chars>"


def content_meta(value) -> dict:
    """Length and short digest of a piece of content; the content itself never
    enters a retained fact."""
    if isinstance(value, str):
        data = value.encode("utf-8", errors="replace")
    else:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return {"bytes": len(data), "digest": hashlib.sha256(data).hexdigest()[:12]}


def bounded_id(request_id) -> str:
    return brief(str(request_id), _ID_TEXT_LIMIT)


class AcpTimeout(TimeoutError):
    """One request or notification exceeded its budget - including the time to
    write it."""


class AcpConnectionClosed(RuntimeError):
    """The child's stdout reached EOF (or the connection was shut down) while a
    request was still pending; EOF is a transport fact, never a stop proof."""


class AcpRequestError(RuntimeError):
    """The agent answered a request with a JSON-RPC error object.

    The string form carries metadata only (the method and the error code);
    the raw error object is the live ``.error`` attribute for the trusted
    caller and never enters facts, logs or exception text.
    """

    def __init__(self, method: str, error):
        self.method = method
        self.error = error if isinstance(error, dict) else {}
        code = self.error.get("code")
        super().__init__(f"{method} failed with JSON-RPC error code {code!r}; "
                         "the raw error object is on .error")


class FrameMetaLog:
    """Byte-capped metadata log in the run's private directory.

    Each frame becomes one line of direction, byte count and digest - never the
    frame content - so retention cannot leak a parameter, an output or a secret.
    Truncation of this optional log is a retention fact only: it says nothing
    about the source stream or about the completeness of the in-memory facts.
    """

    def __init__(self, path, cap_bytes: int = FRAME_LOG_CAP_BYTES):
        self.path = path
        self.cap_bytes = cap_bytes
        self.bytes_written = 0
        self.truncated = False
        self._lock = threading.Lock()
        self._handle = None

    def start(self) -> None:
        if self._handle is None:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._handle = private_dirs.open_regular_fd(
                Path(self.path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)

    def write(self, direction: str, raw: str) -> None:
        with self._lock:
            if self._handle is None or self.truncated:
                return
            data = raw.encode("utf-8", errors="replace")
            meta = {"ts": now(), "dir": direction, "bytes": len(data),
                    "digest": hashlib.sha256(data).hexdigest()[:12]}
            line = (json.dumps(meta, sort_keys=True) + "\n").encode("utf-8")
            if self.bytes_written + len(line) > self.cap_bytes:
                self.truncated = True
                return
            try:
                os.write(self._handle, line)
            except OSError:
                self.truncated = True
                return
            self.bytes_written += len(line)

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    os.close(self._handle)
                except OSError:
                    pass
                self._handle = None


class _WriteJob:
    """One frame the single writer owes the child's stdin.

    Every state transition - queued to cancelled, queued to started, started
    to settled - happens under the connection's write-queue lock, so the
    requester's timeout observation and the writer's claim can never interleave
    half-way: a cancelled job never starts, and a settled job is never
    reversed into an uncertain one.
    """

    __slots__ = ("kind", "payload", "deadline", "done", "outcome", "cancelled", "started",
                 "uncertain")

    def __init__(self, kind: str, payload, deadline: float):
        self.kind = kind
        self.payload = payload
        self.deadline = deadline
        self.done = threading.Event()
        #: None while pending; ("sent", bytes) | ("cancelled",) | ("expired",)
        #: | ("failed", exception) once settled.
        self.outcome = None
        self.cancelled = False
        self.started = False
        self.uncertain = False


def _wait_seconds(remaining: float) -> float | None:
    """An event wait budget where an infinite deadline means wait without one.

    The normalized ``timeoutSeconds=0`` sentinel arrives here as an infinite
    remaining budget; a bounded ``Event.wait`` cannot take it, and the honest
    translation is a wait with no timeout, never an immediate expiry.
    """
    return remaining if 0 <= remaining < float("inf") else None if remaining > 0 else 0.0


class AcpConnection:
    """Owns one launched process's stdio and correlates every JSON-RPC message.

    ``handle`` is the ``ProcessHandle`` of the launched group; the connection
    never signals the child and never derives a stop fact from the protocol:
    EOF, close acknowledgements and cancel acknowledgements are transport facts.
    """

    def __init__(self, process, handle, *, frame_log: FrameMetaLog | None = None,
                 permission_handler=None, max_outbound_bytes: int = MAX_OUTBOUND_BYTES):
        self.process = process
        self.handle = handle
        self.frame_log = frame_log
        self.permission_handler = permission_handler
        self.max_outbound_bytes = max_outbound_bytes
        self._pending: dict = {}
        self._pending_lock = threading.Lock()
        self._answered: set = set()
        self._answered_order: deque = deque(maxlen=64)
        self._timed_out: set = set()
        self._timed_out_order: deque = deque(maxlen=64)
        self._stdin_lock = threading.Lock()
        self._next_id = 0
        self.eof = threading.Event()
        self.shutdown_started = threading.Event()
        self.protocol_faults: deque = deque(maxlen=_MAX_FAULT_RECORDS)
        self.protocol_fault_count = 0
        self.unmatched: deque = deque(maxlen=_MAX_UNMATCHED_RECORDS)
        self.unmatched_count = 0
        self.permission_decisions: deque = deque(maxlen=_MAX_DECISION_RECORDS)
        self.denied_interactions: deque = deque(maxlen=_MAX_DENIED_RECORDS)
        self.observation_failures: deque = deque(maxlen=_MAX_OBSERVER_RECORDS)
        self.observation_failure_count = 0
        self.notification_counts: dict = {}
        self.notification_kinds_truncated = False
        self._observers: list = []
        self._stdin_busy = False
        self._uncertain_writes = 0
        self.stderr_tail = b""
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr = threading.Thread(target=self._stderr_loop, daemon=True)
        # The one bounded writer: every frame to the child goes through it.
        self._write_queue: deque = deque()
        self._write_queue_lock = threading.Lock()
        self._write_wake = threading.Event()
        self._write_stopped = threading.Event()
        self.write_uncertain = False
        self.write_events: deque = deque(maxlen=_MAX_WRITE_RECORDS)
        self.write_failure_count = 0
        self._write_active = False
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True,
                                               name="acp-writer")

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self.frame_log is not None:
            self.frame_log.start()
        self._reader.start()
        self._stderr.start()
        self._writer_thread.start()

    def observe(self, callback) -> None:
        """Register a callback receiving every agent notification message live.

        Callbacks get the raw message; nothing else does. A failing callback is
        recorded as an observation failure and never stops the reader.
        """
        self._observers.append(callback)

    def facts(self) -> dict:
        """The bounded, content-free protocol facts this connection accumulated."""
        with self._pending_lock:
            pending = len(self._pending)
        with self._write_queue_lock:
            queued = len(self._write_queue)
        return {
            "protocolFaults": list(self.protocol_faults),
            "protocolFaultCount": self.protocol_fault_count,
            "unmatchedResponses": list(self.unmatched),
            "unmatchedResponseCount": self.unmatched_count,
            "permissionDecisions": list(self.permission_decisions),
            "deniedInteractions": list(self.denied_interactions),
            "observationFailures": list(self.observation_failures),
            "observationFailureCount": self.observation_failure_count,
            "notificationCounts": dict(self.notification_counts),
            "notificationKindsTruncated": self.notification_kinds_truncated,
            "pendingRequestCount": pending,
            "writeUncertain": self.write_uncertain,
            "writeEvents": list(self.write_events),
            "writeFailureCount": self.write_failure_count,
            "pendingWrites": queued + (1 if self._write_active else 0),
            "metaLogTruncated": bool(self.frame_log and self.frame_log.truncated),
            "eof": self.eof.is_set(),
        }

    # -- the one bounded writer --------------------------------------------------

    def _enqueue_write(self, payload, *, deadline: float, kind: str) -> tuple[_WriteJob, bool]:
        job = _WriteJob(kind, payload, deadline)
        with self._write_queue_lock:
            if self._write_stopped.is_set():
                job.outcome = ("failed", AcpConnectionClosed("connection shutdown before the frame was written"))
                job.done.set()
                return job, False
            if len(self._write_queue) >= _MAX_WRITE_QUEUE:
                job.outcome = ("failed", BoardError("ACP_WRITE_QUEUE_FULL",
                                                    "the write queue is full; the child is not consuming stdin"))
                job.done.set()
                return job, False
            self._write_queue.append(job)
        self._write_wake.set()
        return job, True

    def _record_write_event(self, kind: str, write_kind: str, **extra) -> None:
        self.write_events.append({"ts": now(), "kind": kind, "write": write_kind, **extra})

    def _writer_loop(self) -> None:
        while True:
            job = None
            with self._write_queue_lock:
                if self._write_queue:
                    job = self._write_queue.popleft()
                elif self._write_stopped.is_set():
                    return
            if job is None:
                self._write_wake.wait(0.05)
                self._write_wake.clear()
                continue
            if self._write_stopped.is_set():
                self._settle_job(job, ("failed",
                                       AcpConnectionClosed("connection shutdown before the frame was written")))
                continue
            self._run_job(job)

    def _claim_job(self, job: _WriteJob) -> bool:
        """Atomically move one queued job to started, or settle it unsent.

        The requester's timeout cancellation and the writer's start meet in this
        critical section: a job observed cancelled here is settled immediately
        and never written; a claimed job can no longer be cancelled. Returns
        whether the caller should write the frame.
        """
        with self._write_queue_lock:
            if job.cancelled:
                job.outcome = ("cancelled",)
                job.done.set()
                self._record_write_event("cancelled-unsent", job.kind)
                return False
            if job.deadline - time.monotonic() <= 0:
                job.outcome = ("expired",)
                job.done.set()
                self._record_write_event("expired-queued", job.kind)
                return False
            job.started = True
            return True

    def _settle_job(self, job: _WriteJob, outcome) -> None:
        """Record one final outcome; the requester observes it atomically."""
        with self._write_queue_lock:
            job.outcome = outcome
            job.done.set()
        if job.uncertain:
            self._record_write_event(
                "uncertain-settled-failed" if outcome[0] == "failed"
                else "uncertain-settled", job.kind)
            self._uncertain_writes = max(0, self._uncertain_writes - 1)
            if self._uncertain_writes == 0:
                self.write_uncertain = False

    def _observe_write_after_deadline(self, job: _WriteJob) -> str:
        """The requester's view when its budget expired: settled, cancelled, or
        uncertain - read atomically against the writer's claim and completion.
        The uncertain fact is set here, in the same critical section, so it can
        never apply to a job that already settled."""
        with self._write_queue_lock:
            if job.outcome is not None:
                return "settled"  # completion raced in; never reversed to uncertain
            if not job.started:
                job.cancelled = True
                return "cancelled"
            job.uncertain = True
            self._uncertain_writes += 1
            self.write_uncertain = True
            return "uncertain"

    def _run_job(self, job: _WriteJob) -> None:
        if not self._claim_job(job):
            return  # settled unsent by the claim; the frame never reaches the wire
        self._write_active = True
        try:
            written = self._write_now(job.payload)
        except Exception as error:  # noqa: BLE001 - every write failure is a recorded fact
            if isinstance(error, BoardError):
                self._settle_job(job, ("failed", error))
            else:
                self._settle_job(job, ("failed", AcpConnectionClosed(
                    f"writing the child's stdin failed: {error}")))
            self.write_failure_count += 1
            self._record_write_event("write-failed", job.kind, bytes=None)
            return
        finally:
            self._write_active = False
        self._settle_job(job, ("sent", written))

    def _write_now(self, payload) -> int:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > self.max_outbound_bytes:
            raise BoardError(
                "ACP_OUTBOUND_FRAME_TOO_LARGE",
                f"outbound frame is {len(encoded)} bytes, over the {self.max_outbound_bytes}-byte cap")
        with self._stdin_lock:
            stdin = self.process.stdin
            if stdin is None or stdin.closed:
                raise AcpConnectionClosed("the child's stdin is closed")
            try:
                stdin.write(encoded)
                stdin.flush()
            except (OSError, ValueError) as error:
                raise AcpConnectionClosed(f"writing the child's stdin failed: {error}") from error
        if self.frame_log is not None:
            self.frame_log.write("out", line.rstrip("\n"))
        return len(encoded)


    def _await_write(self, job: _WriteJob, label: str, timeout: float) -> None:
        """Wait for one write job inside its budget. On expiry the atomic
        observation decides: a queued job is cancelled (never written), a job
        whose completion raced in is simply settled, and only a genuinely
        started-but-unconfirmed write becomes an uncertain fact."""
        remaining = job.deadline - time.monotonic()
        if not job.done.wait(_wait_seconds(remaining)):
            observation = self._observe_write_after_deadline(job)
            if observation == "cancelled":
                raise AcpTimeout(f"{label} could not be written within {timeout}s")
            if observation == "uncertain":
                self._record_write_event("uncertain-write", job.kind)
                raise AcpTimeout(f"{label} could not be written within {timeout}s")
            # settled: the completion raced in between the timeout and this
            # observation; fall through to the normal outcome handling.
        if job.outcome[0] in ("cancelled", "expired"):
            raise AcpTimeout(f"{label} could not be written within {timeout}s")
        if job.outcome[0] == "failed":
            raise job.outcome[1]

    # -- requests and notifications ---------------------------------------------

    def request(self, method: str, params: dict, timeout: float) -> dict:
        """Send one request and wait for its response within one budget that
        covers queuing, writing and waiting; errors raise, late answers arrive
        as facts, and every failure path releases the pending entry."""
        deadline = time.monotonic() + float(timeout)
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            waiter = {"event": threading.Event(), "message": None, "resolved": False}
            self._pending[request_id] = waiter
        try:
            job, enqueued = self._enqueue_write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                deadline=deadline, kind="request")
        except BaseException:
            self._release(request_id, timed_out=False)
            raise
        if not enqueued:
            self._release(request_id, timed_out=True)
            raise job.outcome[1]
        try:
            self._await_write(job, method, timeout)
        except BaseException:
            with self._pending_lock:
                resolved = waiter["resolved"]
                if not resolved:
                    self._pending.pop(request_id, None)
                    self._mark(self._timed_out, self._timed_out_order, request_id)
            if not resolved:
                raise
        remaining = deadline - time.monotonic()
        if not waiter["event"].wait(_wait_seconds(remaining)):
            with self._pending_lock:
                resolved = waiter["resolved"]
                if not resolved:
                    self._pending.pop(request_id, None)
                    self._mark(self._timed_out, self._timed_out_order, request_id)
            if not resolved:
                raise AcpTimeout(f"{method} timed out after {timeout}s")
        with self._pending_lock:
            waiter = self._pending.get(request_id) or waiter
            self._pending.pop(request_id, None)
        message = waiter["message"]
        if message is None or message.get("__eof__"):
            raise AcpConnectionClosed(f"the child's stdout ended before {method} was answered")
        if "error" in message:
            raise AcpRequestError(method, message["error"])
        return message.get("result")

    def notify(self, method: str, params: dict, timeout: float = _NOTIFY_WRITE_SECONDS) -> None:
        """Send one notification through the bounded writer within its budget."""
        job, enqueued = self._enqueue_write({"jsonrpc": "2.0", "method": method, "params": params},
                                            deadline=time.monotonic() + float(timeout),
                                            kind=f"notify:{method}")
        if not enqueued:
            raise AcpTimeout(f"{method} could not be written within {timeout}s")
        self._await_write(job, method, timeout)

    def _submit_reply(self, payload: dict) -> None:
        """Enqueue a reader-side reply (permission answer or refusal) without
        ever blocking the reader; a reply that cannot be delivered is a fact."""
        job, enqueued = self._enqueue_write(payload, deadline=time.monotonic() + _REPLY_WRITE_SECONDS,
                                            kind="reply")
        if not enqueued:
            self.write_failure_count += 1
            detail = job.outcome[1]
            self._record_write_event("reply-not-delivered", "reply",
                                     reason=type(detail).__name__)

    def _release(self, request_id: int, *, timed_out: bool) -> None:
        with self._pending_lock:
            waiter = self._pending.pop(request_id, None)
            if timed_out and waiter is not None and not waiter["resolved"]:
                self._mark(self._timed_out, self._timed_out_order, request_id)

    @staticmethod
    def _mark(seen: set, order: deque, request_id) -> None:
        if request_id in seen:
            return
        if len(order) == order.maxlen:
            seen.discard(order[0])
        order.append(request_id)
        seen.add(request_id)

    # -- reading ---------------------------------------------------------------

    def _read_loop(self) -> None:
        try:
            for line in self._bounded_lines(self.process.stdout):
                self._emit_line(line)
        except (OSError, ValueError):
            pass
        finally:
            self.eof.set()
            with self._pending_lock:
                waiters = [(request_id, waiter) for request_id, waiter in self._pending.items()
                           if not waiter["resolved"]]
            for _request_id, waiter in waiters:
                waiter["message"] = {"__eof__": True}
                waiter["event"].set()
            stream = self.process.stdout
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

    def _bounded_lines(self, stream, limit: int = MAX_LINE_BYTES):
        """Yield raw line bytes; a line over the limit becomes one fault and its
        remainder up to the next newline is discarded with fixed memory while an
        incremental digest is still computed - the fault is recorded at the
        newline or, for a line that never ends, at EOF."""
        pending = bytearray()
        oversized = False
        discarded = 0
        hasher = None
        reader = getattr(stream, "read1", stream.read)
        while True:
            # read1 keeps the reader responsive: a blocking full-buffer read would
            # hold a small NDJSON frame until 64 KiB accumulate or EOF.
            chunk = reader(65536)
            if not chunk:
                if oversized:
                    self._record_fault("oversized-line", discarded=discarded,
                                       digest=hasher.hexdigest()[:12])
                    hasher = None
                    discarded = 0
                    oversized = False
                if pending:
                    yield bytes(pending)
                    pending.clear()
                return
            start = 0
            while True:
                index = chunk.find(b"\n", start)
                if index < 0:
                    piece = chunk[start:]
                    if oversized:
                        discarded += len(piece)
                        hasher.update(piece)
                    else:
                        pending.extend(piece)
                        if len(pending) > limit:
                            hasher = hashlib.sha256()
                            hasher.update(pending)
                            discarded = len(pending)
                            pending.clear()
                            oversized = True
                    break
                piece = chunk[start:index]
                start = index + 1
                if oversized:
                    discarded += len(piece)
                    hasher.update(piece)
                    self._record_fault("oversized-line", discarded=discarded,
                                       digest=hasher.hexdigest()[:12])
                    hasher = None
                    discarded = 0
                    oversized = False
                else:
                    pending.extend(piece)
                    if len(pending) > limit:
                        self._record_fault("oversized-line", discarded=len(pending))
                    else:
                        yield bytes(pending)
                pending.clear()

    def _emit_line(self, data: bytes) -> None:
        """Decode one line strictly; invalid UTF-8 is a fault, not new content."""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self._record_fault("invalid-utf8", raw_bytes=data)
            return
        if self.frame_log is not None:
            self.frame_log.write("in", text)
        self._dispatch(text)

    def _record_fault(self, kind: str, *, raw: str | None = None,
                      raw_bytes: bytes | None = None,
                      discarded: int | None = None, digest: str | None = None) -> None:
        """Record one protocol fault as metadata only - never the content."""
        self.protocol_fault_count += 1
        entry = {"ts": now(), "kind": kind}
        if raw is not None:
            entry.update(content_meta(raw))
        elif raw_bytes is not None:
            entry["bytes"] = len(raw_bytes)
            entry["digest"] = hashlib.sha256(raw_bytes).hexdigest()[:12]
        if discarded is not None:
            entry["bytes"] = discarded
        if digest is not None:
            entry["digest"] = digest
        self.protocol_faults.append(entry)

    def _dispatch(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except RecursionError:
            self._record_fault("too-deep", raw=text)
            return
        except ValueError:
            self._record_fault("invalid-json", raw=text)
            return
        if not isinstance(message, dict):
            self._record_fault("not-an-object", raw=text)
            return
        if message.get("jsonrpc") != "2.0":
            self._record_fault("bad-jsonrpc", raw=text)
            return
        method = message.get("method")
        has_method = "method" in message
        if has_method and (not isinstance(method, str) or not method):
            self._record_fault("bad-method", raw=text)
            return
        has_id = "id" in message
        if has_id:
            request_id = message["id"]
            if isinstance(request_id, bool) or not isinstance(request_id, (int, str)) \
                    or (isinstance(request_id, str) and not request_id):
                # A bool id would alias this side's integers (True == 1); it is
                # a fault and can never correlate.
                self._record_fault("bad-id", raw=text)
                return
        if has_method:
            if "result" in message or "error" in message:
                # A request or notification never carries a result or an error.
                self._record_fault("bad-request-shape", raw=text)
                return
            params = message.get("params")
            if params is not None and not isinstance(params, (dict, list)):
                self._record_fault("bad-params", raw=text)
                return
            if has_id:
                self._handle_agent_request(message)
            else:
                self._handle_notification(message)
        elif has_id:
            has_result = "result" in message
            has_error = "error" in message
            if has_result == has_error:
                self._record_fault("bad-response-shape", raw=text)
                return
            if has_error:
                error = message["error"]
                code = error.get("code") if isinstance(error, dict) else None
                if not isinstance(error, dict) or isinstance(code, bool) \
                        or not isinstance(code, int) or not isinstance(error.get("message"), str):
                    self._record_fault("bad-error-shape", raw=text)
                    return
            self._handle_response(message)
        else:
            self._record_fault("shapeless-message", raw=text)

    def _handle_response(self, message: dict) -> None:
        request_id = message.get("id")
        with self._pending_lock:
            waiter = self._pending.get(request_id)
            if waiter is None:
                kind = ("duplicate" if request_id in self._answered
                        else "late" if request_id in self._timed_out else "unknown")
            elif waiter["resolved"]:
                kind = "duplicate"
                waiter = None
            else:
                # One atomic transition: a deadline observing resolved=True can
                # never see a message that is not there yet.
                waiter["resolved"] = True
                waiter["message"] = message
                kind = None
                self._mark(self._answered, self._answered_order, request_id)
        if kind is not None:
            self.unmatched_count += 1
            self.unmatched.append({
                "ts": now(), "id": bounded_id(request_id), "kind": kind,
                "resultMeta": content_meta(message.get("result")) if "result" in message else None,
                "errorMeta": content_meta(message.get("error")) if "error" in message else None})
            return
        waiter["event"].set()

    def _handle_notification(self, message: dict) -> None:
        method = message.get("method")
        if method not in self.notification_counts and len(self.notification_counts) >= _MAX_NOTIFICATION_KINDS:
            self.notification_kinds_truncated = True
            self.notification_counts["<overflow>"] = self.notification_counts.get("<overflow>", 0) + 1
        else:
            self.notification_counts[method] = self.notification_counts.get(method, 0) + 1
        for index, callback in enumerate(tuple(self._observers)):
            try:
                callback(message)
            except Exception as error:  # noqa: BLE001 - an observer defect must not kill the reader,
                # but losing a notification must not be invisible either
                self.observation_failure_count += 1
                self.observation_failures.append({
                    "ts": now(), "observer": index, "error": type(error).__name__,
                    "method": bounded_id(method)})

    def _handle_agent_request(self, message: dict) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "session/request_permission" and self.permission_handler is not None:
            try:
                outcome, basis = self.permission_handler(params)
            except Exception as error:  # noqa: BLE001 - a handler defect must still answer
                outcome, basis = {"outcome": "cancelled"}, f"handler error: {type(error).__name__}"
            # The record is as conservative as the answer: every field is
            # guarded, a malformed shape is named, no field can raise.
            raw_options = params.get("options") if isinstance(params, dict) else None
            options_shape = "list" if isinstance(raw_options, list) else \
                "missing" if raw_options is None else type(raw_options).__name__
            option_entries = [option for option in (raw_options or [])
                              if isinstance(option, dict)] if isinstance(raw_options, list) else []
            tool_call = params.get("toolCall") if isinstance(params, dict) else None
            tool_call = tool_call if isinstance(tool_call, dict) else None
            title = tool_call.get("title") if tool_call is not None else None
            self.permission_decisions.append({
                "ts": now(), "requestId": bounded_id(request_id),
                "toolCallId": bounded_id((tool_call or {}).get("toolCallId") or ""),
                "toolCallShape": "object" if tool_call is not None else "missing-or-not-object",
                "titleMeta": content_meta(title) if isinstance(title, str) else None,
                "optionsShape": options_shape,
                "options": [{"optionId": brief(str(option.get("optionId")), 100),
                             "kind": brief(str(option.get("kind")), 40)}
                            for option in option_entries[:_MAX_OPTIONS_PER_DECISION]],
                "optionsTruncated": len(option_entries) > _MAX_OPTIONS_PER_DECISION,
                "outcome": outcome, "basis": basis})
            self._submit_reply({"jsonrpc": "2.0", "id": request_id, "result": {"outcome": outcome}})
            return
        self.denied_interactions.append({
            "ts": now(), "requestId": bounded_id(request_id),
            "method": brief(str(method), 200),
            "paramsMeta": content_meta(params)})
        self._submit_reply({"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32601,
                                      "message": f"this client does not implement {method}; request not executed"}})

    # -- shutdown --------------------------------------------------------------

    def close_stdin(self) -> None:
        """Close the child's stdin unless a writer is still inside it; the reader
        keeps draining stdout to EOF either way."""
        acquired = self._stdin_lock.acquire(timeout=5.0)
        if not acquired:
            self._stdin_busy = True  # a stalled write owns the pipe; group stop will release it
            return
        try:
            stdin = self.process.stdin
            if stdin is not None and not stdin.closed:
                try:
                    stdin.close()
                except OSError:
                    pass
        finally:
            self._stdin_lock.release()

    def shutdown(self, *, drain_seconds: float = 20.0) -> dict:
        """Stop the writer (failing whatever it never wrote), close stdin, drain
        stdout to EOF, and join the threads.

        A stream whose reader is still blocked is never closed here: closing a
        buffered pipe while another thread sits inside ``read`` deadlocks on the
        buffer lock. Each reader closes its own stream at EOF; shutdown closes
        one only when its reader has already finished.
        """
        self.shutdown_started.set()
        self._write_stopped.set()
        self._write_wake.set()
        self._writer_thread.join(timeout=_WRITER_JOIN_SECONDS)
        with self._write_queue_lock:
            leftover = list(self._write_queue)
            self._write_queue.clear()
        for job in leftover:
            if not job.done.is_set():
                self._settle_job(job, ("failed",
                                       AcpConnectionClosed("connection shutdown before the frame was written")))
        self.close_stdin()
        self._reader.join(timeout=drain_seconds)
        self._stderr.join(timeout=1.0)
        for stream, thread_done in ((self.process.stdout, not self._reader.is_alive()),
                                    (self.process.stderr, not self._stderr.is_alive())):
            if thread_done and stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        if self.frame_log is not None:
            self.frame_log.close()
        return {"stdinCloseSkippedWriterBusy": self._stdin_busy,
                "writerStopped": not self._writer_thread.is_alive()}

    def stderr_text(self) -> str:
        return self.stderr_tail.decode("utf-8", errors="replace")

    def _stderr_loop(self) -> None:
        try:
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                self.stderr_tail = (self.stderr_tail + chunk)[-STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            return
        finally:
            stream = self.process.stderr
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def group_observation(handle) -> str:
    """One direct observation of the owned process group: gone/alive/unknown.

    Unknown is conservative: an unavailable observation is never termination
    evidence. A positive "gone" is only trustworthy beside the leader's exit;
    stop facts combine both.
    """
    if handle.job is not None:
        try:
            return "gone" if handle.job.active() == 0 else "alive"
        except OSError:
            return "unknown"
    if os.name == "nt":
        return "alive" if handle.group_alive() else "gone"
    if handle.pgid is None:
        return "unknown"
    try:
        os.killpg(handle.pgid, 0)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "alive"
    except OSError:
        return "unknown"
    return "alive"


def stop_evidence(handle) -> dict:
    """Leader and group observed separately; conservative on every unknown.

    ``shutdownConfirmed`` is true only when the leader has been reaped and the
    owned group is observed gone. EOF, a session close, a cancel acknowledgement
    or an unreachable channel never appears here as a stop proof.
    """
    code = handle.process.poll()
    observation = group_observation(handle)
    conservative_alive = handle.group_alive()
    return {
        "leaderExited": code is not None,
        "leaderExitCode": code,
        "groupObserved": observation,
        "groupAliveConservative": conservative_alive,
        "shutdownConfirmed": bool(code is not None and observation == "gone"),
    }
