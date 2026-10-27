"""One NDJSON JSON-RPC 2.0 stdio connection to an owned ACP process.

Frames are validated at the boundary. A single writer gives each frame a
deadline, and growing connection facts are recorded in private JSONL.
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

from .....paths import native_path
from .....private_dirs import ensure_private_dir

#: A single stdout line larger than this is a broken-frame fact, not a message.
MAX_LINE_BYTES = 1024 * 1024

#: Bounded stderr tail kept for failure reports.
STDERR_TAIL_BYTES = 64 * 1024

#: The single writer and the deadline for reader-side replies.
_REPLY_WRITE_SECONDS = 3.0
_NOTIFY_WRITE_SECONDS = 10.0
_WRITER_JOIN_SECONDS = 5.0

_ID_TEXT_LIMIT = 64


def permission_operation(params) -> str:
    """Retain only the native tool title and command or path, bounded once."""
    tool = params.get("toolCall") if isinstance(params, dict) else None
    tool = tool if isinstance(tool, dict) else {}
    title = tool.get("title")
    title = title if isinstance(title, str) else "unknown tool"
    inputs = tool.get("rawInput")
    inputs = inputs if isinstance(inputs, dict) else {}
    detail = "input unavailable"
    for name in ("command", "path"):
        if isinstance(inputs.get(name), str):
            detail = name + "=" + inputs[name]
            break
    return (title + ": " + detail).encode("utf-8", errors="replace")[:512].decode("utf-8", errors="ignore")


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


class _WriteJob:
    """One queued frame with a deadline shared by queuing and writing."""

    __slots__ = ("payload", "deadline", "done", "outcome", "cancelled", "started")

    def __init__(self, payload, deadline: float):
        self.payload = payload
        self.deadline = deadline
        self.done = threading.Event()
        self.outcome = None
        self.cancelled = False
        self.started = False


def _wait_seconds(remaining: float) -> float | None:
    """An event wait budget where an infinite deadline means wait without one.

    An absent hard timeout arrives here as an infinite
    remaining budget; a bounded ``Event.wait`` cannot take it, and the honest
    translation is a wait with no timeout, never an immediate expiry.
    """
    return remaining if 0 <= remaining < float("inf") else None if remaining > 0 else 0.0


class AcpConnection:
    """Owns one launched process's stdio and correlates every JSON-RPC message.

    The connection never signals the child and never derives a stop fact from the protocol:
    EOF, close acknowledgements and cancel acknowledgements are transport facts.
    """

    def __init__(self, process, *, stdin_lock, facts_path: Path,
                 permission_handler=None):
        self.process = process
        self.facts_path = Path(facts_path)
        self.permission_handler = permission_handler
        self._fact_lock = threading.Lock()
        self._pending: dict = {}
        self._pending_lock = threading.Lock()
        self._stdin_lock = stdin_lock
        self._next_id = 0
        self.eof = threading.Event()
        self.shutdown_started = threading.Event()
        self.reader_error: Exception | None = None
        self.protocol_fault_count = 0
        self.unmatched_count = 0
        self.permission_decision_count = 0
        self.denied_interaction_count = 0
        self.notification_counts: dict = {}
        self._observers: list = []
        self.stderr_tail = b""
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr = threading.Thread(target=self._stderr_loop, daemon=True)
        self._write_queue: deque = deque()
        self._write_queue_lock = threading.Lock()
        self._write_wake = threading.Event()
        self._write_stopped = threading.Event()
        self.write_failure_count = 0
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True,
                                               name="acp-writer")

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        ensure_private_dir(self.facts_path.parent)
        with native_path(self.facts_path).open("x", encoding="utf-8"):
            pass
        self._reader.start()
        self._stderr.start()
        self._writer_thread.start()

    def observe(self, callback) -> None:
        """Register a callback receiving every agent notification live."""
        self._observers.append(callback)

    def _record_fact(self, kind: str, fact: dict) -> None:
        with self._fact_lock:
            with native_path(self.facts_path).open("a", encoding="utf-8", newline="\n") as output:
                output.write(json.dumps({"recordType": kind, **fact}, ensure_ascii=False,
                                        sort_keys=True) + "\n")

    def facts(self) -> dict:
        """Return counters and the private file containing complete growing facts."""
        with self._pending_lock:
            pending = len(self._pending)
        return {
            "factsPath": str(self.facts_path),
            "protocolFaultCount": self.protocol_fault_count,
            "unclaimedReplyCount": self.unmatched_count,
            "permissionDecisionCount": self.permission_decision_count,
            "deniedInteractionCount": self.denied_interaction_count,
            "notificationCounts": dict(self.notification_counts),
            "pendingRequestCount": pending,
            "writeFailureCount": self.write_failure_count,
            "eof": self.eof.is_set(),
        }

    # -- one writer -----------------------------------------------------------

    def _enqueue_write(self, payload, *, deadline: float) -> _WriteJob:
        job = _WriteJob(payload, deadline)
        with self._write_queue_lock:
            if self._write_stopped.is_set():
                job.outcome = ("failed", AcpConnectionClosed("connection shutdown before the frame was written"))
                job.done.set()
            else:
                self._write_queue.append(job)
                self._write_wake.set()
        return job

    def _writer_loop(self) -> None:
        while True:
            with self._write_queue_lock:
                if self._write_queue:
                    job = self._write_queue.popleft()
                elif self._write_stopped.is_set():
                    return
                else:
                    job = None
            if job is None:
                self._write_wake.wait(0.05)
                self._write_wake.clear()
                continue
            with self._write_queue_lock:
                if self._write_stopped.is_set():
                    job.outcome = ("failed", AcpConnectionClosed(
                        "connection shutdown before the frame was written"))
                elif job.cancelled:
                    job.outcome = ("expired",)
                elif job.deadline <= time.monotonic():
                    job.outcome = ("expired",)
                else:
                    job.started = True
                if job.outcome is not None:
                    job.done.set()
                    continue
            try:
                outcome = ("sent", self._write_now(job.payload))
            except Exception as error:
                self.write_failure_count += 1
                outcome = ("failed", AcpConnectionClosed(
                    f"writing the child's stdin failed: {error}"))
            with self._write_queue_lock:
                job.outcome = outcome
                job.done.set()

    def _write_now(self, payload) -> int:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        with self._stdin_lock:
            stdin = self.process.stdin
            if stdin is None or stdin.closed:
                raise AcpConnectionClosed("the child's stdin is closed")
            try:
                stdin.write(encoded)
                stdin.flush()
            except (OSError, ValueError) as error:
                raise AcpConnectionClosed(f"writing the child's stdin failed: {error}") from error
        return len(encoded)

    def _await_write(self, job: _WriteJob, label: str, timeout: float) -> None:
        remaining = job.deadline - time.monotonic()
        if not job.done.wait(_wait_seconds(remaining)):
            with self._write_queue_lock:
                if job.outcome is None and not job.started:
                    job.cancelled = True
                outcome = job.outcome
            if outcome is None:
                raise AcpTimeout(f"{label} could not be written within {timeout}s")
        outcome = job.outcome
        if outcome[0] == "expired":
            raise AcpTimeout(f"{label} could not be written within {timeout}s")
        if outcome[0] == "failed":
            raise outcome[1]

    # -- requests and notifications ---------------------------------------------

    def request(self, method: str, params: dict, timeout: float) -> dict:
        """Send one request and wait for its response within one budget that
        covers queuing, writing and waiting; errors raise, late answers arrive
        as facts, and every failure path releases the pending entry."""
        if self.eof.is_set():
            raise AcpConnectionClosed(f"connection closed before {method} was sent")
        deadline = time.monotonic() + float(timeout)
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            waiter = {"event": threading.Event(), "message": None, "resolved": False}
            self._pending[request_id] = waiter
        job = self._enqueue_write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            deadline=deadline)
        try:
            self._await_write(job, method, timeout)
        except BaseException:
            with self._pending_lock:
                resolved = waiter["resolved"]
                if not resolved:
                    self._pending.pop(request_id, None)
            if not resolved:
                raise
        remaining = deadline - time.monotonic()
        if not waiter["event"].wait(_wait_seconds(remaining)):
            with self._pending_lock:
                resolved = waiter["resolved"]
                if not resolved:
                    self._pending.pop(request_id, None)
            if not resolved:
                raise AcpTimeout(f"{method} timed out after {timeout}s")
        with self._pending_lock:
            waiter = self._pending.get(request_id) or waiter
            self._pending.pop(request_id, None)
        message = waiter["message"]
        if message is None or message.get("__eof__"):
            reason = (f"reader failed: {type(self.reader_error).__name__}: {self.reader_error}"
                      if self.reader_error is not None else "child stdout ended")
            raise AcpConnectionClosed(f"{reason} before {method} was answered")
        if "error" in message:
            raise AcpRequestError(method, message["error"])
        return message.get("result")

    def notify(self, method: str, params: dict, timeout: float = _NOTIFY_WRITE_SECONDS) -> None:
        """Send one notification through the writer within its budget."""
        if self.eof.is_set():
            raise AcpConnectionClosed(f"connection closed before {method} was sent")
        job = self._enqueue_write({"jsonrpc": "2.0", "method": method, "params": params},
                                  deadline=time.monotonic() + float(timeout))
        self._await_write(job, method, timeout)

    def _submit_reply(self, payload: dict) -> None:
        """Queue a reader-side reply without blocking the reader."""
        job = self._enqueue_write(payload, deadline=time.monotonic() + _REPLY_WRITE_SECONDS)
        if job.outcome is not None:
            self.write_failure_count += 1

    # -- reading ---------------------------------------------------------------

    def _read_loop(self) -> None:
        try:
            for line in self._bounded_lines(self.process.stdout):
                self._emit_line(line)
        except Exception as error:
            self.reader_error = error
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
        self._record_fact("protocol-fault", entry)

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
        request_id = message["id"]
        with self._pending_lock:
            waiter = self._pending.get(request_id)
            if waiter is not None and not waiter["resolved"]:
                waiter["resolved"] = True
                waiter["message"] = message
            else:
                waiter = None
        if waiter is None:
            self.unmatched_count += 1
            return
        waiter["event"].set()

    def _handle_notification(self, message: dict) -> None:
        method = message["method"]
        self.notification_counts[method] = self.notification_counts.get(method, 0) + 1
        for callback in tuple(self._observers):
            callback(message)

    def _handle_agent_request(self, message: dict) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "session/request_permission" and self.permission_handler is not None:
            operation = permission_operation(params)
            outcome, basis = self.permission_handler(params, operation=operation)
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
            self.permission_decision_count += 1
            self._record_fact("permission-decision", {
                "ts": now(), "requestId": bounded_id(request_id),
                "toolCallId": bounded_id((tool_call or {}).get("toolCallId") or ""),
                "toolCallShape": "object" if tool_call is not None else "missing-or-not-object",
                "operation": operation,
                "titleMeta": content_meta(title) if isinstance(title, str) else None,
                "optionsShape": options_shape,
                "options": [{"optionId": brief(str(option.get("optionId")), 100),
                             "kind": brief(str(option.get("kind")), 40)}
                            for option in option_entries],
                "outcome": outcome, "basis": basis})
            self._submit_reply({"jsonrpc": "2.0", "id": request_id, "result": {"outcome": outcome}})
            return
        self.denied_interaction_count += 1
        self._record_fact("denied-interaction", {
            "ts": now(), "requestId": bounded_id(request_id),
            "method": brief(str(method), 200),
            "paramsMeta": content_meta(params)})
        self._submit_reply({"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32601,
                                      "message": f"this client does not implement {method}; request not executed"}})

    # -- shutdown --------------------------------------------------------------

    def shutdown(self, *, stop, drain_seconds: float = 20.0) -> None:
        """Stop the writer (failing whatever it never wrote), close stdin, drain
        stdout to EOF, and join the threads.

        A stream whose reader is still blocked is never closed here: closing a
        buffered pipe while another thread sits inside ``read`` deadlocks on the
        buffer lock. Each reader closes its own stream at EOF; shutdown closes
        one only when its reader has already finished.
        """
        try:
            self.shutdown_started.set()
            self._write_stopped.set()
            self._write_wake.set()
            self._writer_thread.join(timeout=_WRITER_JOIN_SECONDS)
            with self._write_queue_lock:
                leftover = list(self._write_queue)
                self._write_queue.clear()
                for job in leftover:
                    job.outcome = ("failed", AcpConnectionClosed(
                        "connection shutdown before the frame was written"))
                    job.done.set()
        finally:
            stop()
        self._reader.join(timeout=drain_seconds)
        self._stderr.join(timeout=1.0)
        for stream, thread_done in ((self.process.stdout, not self._reader.is_alive()),
                                    (self.process.stderr, not self._stderr.is_alive())):
            if thread_done and stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


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
    return {
        "leaderExited": code is not None,
        "leaderExitCode": code,
        "groupObserved": observation,
        "shutdownConfirmed": bool(code is not None and observation == "gone"),
    }
