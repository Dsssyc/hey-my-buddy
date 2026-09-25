"""Bounded Claude Code stream-json control transport and native turn observation.

The controller speaks the documented stdio control protocol: every controller
request is a ``control_request`` envelope answered by a correlated
``control_response``; the native CLI may send its own ``control_request`` frames
(``can_use_tool``, hook callbacks), which the controller answers without ever
granting extra permission. Frames are bounded, duplicate JSON members and
non-finite numbers are rejected, and response identities must match a pending
request exactly.
"""
from __future__ import annotations

import json
import math
import os
import queue
import select
import threading
import time
from pathlib import Path

from .turn_io import canonical_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_PENDING_RESPONSES = 16
MAX_RATE_LIMIT_TYPES = 16
MAX_BACKGROUND_TASKS = 32

#: The exact closed set of statuses that settle one reported native background
# task or workflow. Substring matching would wrongly settle "incomplete" or
# "not_completed", so equality is required. A terminal result frame may arrive
# while such work still runs; the controller must not then declare the turn
# completed over live native children.
BACKGROUND_TASK_TERMINAL_STATUSES = ("completed", "succeeded", "failed", "cancelled", "canceled",
                                     "error", "aborted", "done")

#: Quota exhaustion is temporary infrastructure unavailability, never a model
#: capability result. The existing evaluation-store exclusion matches error text
#: containing ``ADAPTER_UNAVAILABLE``, so this honest constant keeps quota
#: failures out of capability statistics without core evaluation edits.
QUOTA_REJECTED_ERROR = "ADAPTER_UNAVAILABLE: Claude provider quota rejected"


class ClaudeProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class QuotaRejected(Exception):
    """A structured native quota rejection; carries only bounded typed facts."""

    def __init__(self, rate_limit_type: str, resets_at: object):
        super().__init__("the Claude provider quota rejected this execution")
        self.rate_limit_type = rate_limit_type
        self.resets_at = resets_at


def decode_json(raw: bytes | str) -> object:
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON")
        return number

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite_float,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


class Connection:
    """One bounded JSONL conversation with the native CLI's stdio streams."""

    def __init__(self, process, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.pending: dict[str, dict | None] = {}
        self.next_id = 1
        self.on_request = lambda _frame: None
        self.on_message = lambda _frame: None
        os.set_blocking(process.stdin.fileno(), False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while raw := self.process.stdout.readline(MAX_FRAME_BYTES + 1):
                if len(raw) > MAX_FRAME_BYTES:
                    raise ValueError("oversized native frame")
                message = decode_json(raw)
                if not isinstance(message, dict):
                    raise ValueError("native frame is not an object")
                self.messages.put(message)
        except (OSError, ValueError, RecursionError):
            self.messages.put(ClaudeProtocolError("invalid-protocol", "Claude emitted invalid or oversized JSON"))
        finally:
            self.messages.put(None)

    def _remaining(self):
        if self.cancelled.is_set():
            raise ClaudeProtocolError("user-cancel", "the Claude execution was cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ClaudeProtocolError("deadline", "the Claude execution exceeded its deadline")
        return remaining

    def send(self, message: dict):
        raw = memoryview((canonical_json(message) + "\n").encode())
        if len(raw) > MAX_FRAME_BYTES:
            raise ClaudeProtocolError("invalid-protocol", "native request exceeds its byte bound")
        try:
            while raw:
                remaining = self._remaining()
                try:
                    size = os.write(self.process.stdin.fileno(), raw)
                    raw = raw[size:]
                except BlockingIOError:
                    select.select([], [self.process.stdin.fileno()], [], min(0.1, remaining))
        except OSError:
            raise ClaudeProtocolError("transport-error", "Claude input closed") from None

    def pump(self):
        remaining = self._remaining()
        try:
            message = self.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            return
        self._deliver(message, close_is_error=True)

    def pump_available(self):
        """Deliver frames the reader has already buffered, without waiting.

        Used at the user-message boundary so everything the CLI emitted before
        that point is tagged as pre-user output, no matter when the reader
        thread happened to enqueue it.
        """
        while True:
            self._remaining()
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                return
            self._deliver(message, close_is_error=True)

    def drain_until_closed(self, seconds: float):
        """Deliver trailing frames until the native stream provably ends.

        A short quiet window cannot prove the stream ended: a delayed duplicate
        result or quota frame must still be validated, so only the observed
        end-of-stream sentinel settles the drain. A bounded cap without that
        proof rejects the turn instead of publishing it.
        """
        deadline = time.monotonic() + seconds
        while True:
            self._remaining()
            timeout = min(0.2, max(0.0, deadline - time.monotonic()))
            if timeout <= 0:
                raise ClaudeProtocolError("transport-error", "Claude did not close its stream after the result")
            try:
                message = self.messages.get(timeout=timeout)
            except queue.Empty:
                continue
            if message is None:
                return
            self._deliver(message, close_is_error=True)

    def _deliver(self, message, *, close_is_error: bool):
        if message is None:
            if close_is_error:
                raise ClaudeProtocolError("transport-error", "Claude closed before the request settled")
            return
        if isinstance(message, ClaudeProtocolError):
            raise message
        frame_type = message.get("type")
        if frame_type == "control_response":
            response = message.get("response")
            if not isinstance(response, dict):
                raise ClaudeProtocolError("invalid-protocol", "Claude returned a malformed control response")
            request_id = response.get("request_id")
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                raise ClaudeProtocolError("invalid-protocol", "Claude returned an invalid control response identity")
            if request_id not in self.pending:
                raise ClaudeProtocolError("invalid-protocol", "Claude answered an unknown control request")
            if self.pending[request_id] is not None:
                raise ClaudeProtocolError("invalid-protocol", "Claude answered one control request twice")
            self.pending[request_id] = response
        elif frame_type == "control_request":
            request_id = message.get("request_id")
            request = message.get("request")
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(request, dict):
                raise ClaudeProtocolError("invalid-protocol", "Claude sent an invalid native control request")
            self.on_request(message)
        elif isinstance(frame_type, str):
            self.on_message(message)
        else:
            raise ClaudeProtocolError("invalid-protocol", "Claude sent a frame without a type")

    def call(self, request: dict) -> dict:
        if len(self.pending) >= MAX_PENDING_RESPONSES:
            raise ClaudeProtocolError("invalid-protocol", "too many pending Claude control requests")
        request_id = f"buddy-{self.next_id}"
        self.next_id += 1
        if self.next_id > 100000:
            raise ClaudeProtocolError("invalid-protocol", "the Claude control request budget is exhausted")
        self.pending[request_id] = None
        self.send({"type": "control_request", "request_id": request_id, "request": request})
        while self.pending[request_id] is None:
            self.pump()
        response = self.pending.pop(request_id)
        if response.get("subtype") == "error":
            raise ClaudeProtocolError("native-rpc-error", f"Claude rejected {request.get('subtype')}")
        if response.get("subtype") != "success" or not isinstance(response.get("response"), dict):
            raise ClaudeProtocolError("invalid-protocol", "Claude returned no success object")
        return response["response"]


_REQUEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
    "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                   "neededWork": {"type": "string"}, "expectedArtifacts": {"type": "array", "items": {"type": "string"}},
                   "acceptance": {"type": "string"},
                   # The shared assistance hints name this field; validate_outcome
                   # accepts it as an optional string or an explicit null.
                   "suggestedProfileId": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
}


def _outcome_branch(dispositions, request_schema):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
        "properties": {
            "disposition": {"type": "string", "enum": dispositions},
            "summary": {"type": "string"},
            "remaining": {"type": "array", "items": {"type": "string"}},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "request": request_schema,
        },
    }


# The same nested-union shape the Codex harness uses: ``--json-schema`` permits
# a nested anyOf but not a root union, and the tagged branches keep a
# "completed" result from carrying an unresolved request. The native value is
# always re-validated with ``turn_io.validate_outcome`` after import.
OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["outcome"],
    "properties": {"outcome": {"anyOf": [
        _outcome_branch(["completed"], {"type": "null"}),
        _outcome_branch(["assistance", "attention"], _REQUEST_SCHEMA),
    ]}},
}


def parse_structured_output(value: object) -> dict:
    from .turn_io import validate_outcome
    if not isinstance(value, dict) or set(value) != {"outcome"}:
        raise ValueError("The native structured output must contain exactly the outcome")
    error = validate_outcome(value["outcome"])
    if error:
        raise ValueError(error)
    return value["outcome"]


def _bounded_resets_at(value: object):
    if isinstance(value, str) and value and len(value) <= 64:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def _finite_utilization(value: object):
    """A finite numeric observation: one number or a flat map of numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, dict) and len(value) <= MAX_RATE_LIMIT_TYPES:
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64 or isinstance(item, bool) \
                    or not isinstance(item, (int, float)) or not math.isfinite(item):
                return None
            result[key] = item
        return result
    return None


def rate_limit_observation(frame: dict) -> tuple[str, dict] | None:
    """One bounded, typed observation of a native ``rate_limit_event`` frame."""
    info = frame.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    rate_limit_type, status = info.get("rateLimitType"), info.get("status")
    if not isinstance(rate_limit_type, str) or not rate_limit_type or len(rate_limit_type) > 64:
        return None
    if status not in ("allowed", "allowed_warning", "rejected"):
        return None
    observation = {"status": status, "resetsAt": _bounded_resets_at(info.get("resetsAt"))}
    utilization = _finite_utilization(info.get("utilization"))
    if utilization is not None:
        observation["utilization"] = utilization
    return rate_limit_type, observation


def result_quota_denial(result: dict) -> bool:
    """A structured native quota denial in the final result; never prose-based."""
    status = result.get("api_error_status")
    if status == 429 or status == "429":
        return True
    subtype = result.get("subtype")
    return isinstance(subtype, str) and any(marker in subtype.lower() for marker in ("quota", "usage_limit"))


def model_usage_keys(result: dict) -> list[str]:
    """The bounded observed-model set; never one attested model identity."""
    usage = result.get("modelUsage")
    if not isinstance(usage, dict):
        return []
    keys = sorted(key for key in usage if isinstance(key, str) and key and len(key) <= 128)
    return keys[:16]


def total_cost_usd(result: dict) -> float | None:
    value = result.get("total_cost_usd")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _task_identity(frame: dict) -> str | None:
    for source in (frame, frame.get("task") if isinstance(frame.get("task"), dict) else None):
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "taskId", "id"):
            value = source.get(key)
            if isinstance(value, str) and value and len(value) <= 128:
                return value
    return None


def _task_status(frame: dict) -> str | None:
    for source in (frame.get("patch") if isinstance(frame.get("patch"), dict) else None,
                   frame.get("task") if isinstance(frame.get("task"), dict) else None,
                   frame.get("update") if isinstance(frame.get("update"), dict) else None,
                   frame):
        if isinstance(source, dict):
            value = source.get("status")
            if isinstance(value, str) and value:
                return value.lower()
    return None


class TurnEvidence:
    """What the controller observed of one native root turn."""

    def __init__(self, session_id: str, cwd: str):
        self.session_id, self.cwd = session_id, cwd
        self.init_observed = False
        self.init_session_id: str | None = None
        self.session_model: str | None = None
        self.result: dict | None = None
        self.event_seq = 0
        self.model_messages = 0
        self.tool_calls = 0
        self.rate_limits: dict[str, dict] = {}
        self.permission_denials = 0
        self.background_tasks: dict[str, str] = {}
        self.anonymous_task_starts = 0

    def observe(self, frame: dict):
        """Record one frame; returns an optional (phase, tool) activity update."""
        self.event_seq += 1
        frame_type = frame.get("type")
        if frame_type == "rate_limit_event":
            info = frame.get("rate_limit_info")
            if isinstance(info, dict) and info.get("status") == "rejected":
                # A rejection fails the attempt even when the rate-limit identity
                # is absent or malformed; the published facts stay sanitized.
                rate_limit_type = info.get("rateLimitType")
                rate_limit_type = rate_limit_type if isinstance(rate_limit_type, str) and rate_limit_type \
                    and len(rate_limit_type) <= 64 else "unknown"
                raise QuotaRejected(rate_limit_type, _bounded_resets_at(info.get("resetsAt")))
            observation = rate_limit_observation(frame)
            if observation is not None:
                rate_limit_type, item = observation
                if rate_limit_type in self.rate_limits or len(self.rate_limits) < MAX_RATE_LIMIT_TYPES:
                    self.rate_limits[rate_limit_type] = item
            return None
        # Frames under a subagent's tool call are not root-turn evidence; a
        # subagent result never ends the root turn.
        parent = frame.get("parent_tool_use_id")
        if isinstance(parent, str) and parent:
            return None
        if frame_type == "system":
            subtype = frame.get("subtype")
            if subtype == "init":
                if self.init_observed:
                    raise ClaudeProtocolError("invalid-protocol", "duplicate native session initialization")
                init_session = frame.get("session_id")
                if not isinstance(init_session, str) or not init_session:
                    raise ClaudeProtocolError("native-init-missing", "Claude session initialization carries no session identity")
                if init_session != self.session_id:
                    raise ClaudeProtocolError("wrong-native-session",
                                              "Claude session initialization differs from the preallocated session")
                cwd_value = frame.get("cwd")
                if not isinstance(cwd_value, str) or not cwd_value or Path(cwd_value).resolve() != Path(self.cwd).resolve():
                    raise ClaudeProtocolError("wrong-native-workspace", "Claude session cwd differs from the allocated workspace")
                model = frame.get("model")
                # The init readback is the observation of the session's model; the
                # argv is never treated as attestation, and context-suffix
                # differences stay explicit until a live probe verifies them.
                self.session_model = model if isinstance(model, str) and model and len(model) <= 128 else None
                self.init_observed = True
                self.init_session_id = init_session
                return "starting", None
            if subtype == "task_started":
                identity = _task_identity(frame)
                if identity is None:
                    # Without an identity a later status cannot prove which
                    # task stopped. Keep the unresolved observation sticky.
                    self.anonymous_task_starts = 1
                elif identity in self.background_tasks or len(self.background_tasks) < MAX_BACKGROUND_TASKS:
                    self.background_tasks[identity] = "running"
                else:
                    raise ClaudeProtocolError("invalid-protocol", "Claude exceeded the background task bound")
                return None
            if subtype == "task_updated":
                identity = _task_identity(frame)
                status = _task_status(frame)
                terminal = status in BACKGROUND_TASK_TERMINAL_STATUSES
                if identity is None:
                    self.anonymous_task_starts = 1
                elif identity in self.background_tasks:
                    if terminal:
                        del self.background_tasks[identity]
                    else:
                        self.background_tasks[identity] = status or "running"
                elif not terminal and len(self.background_tasks) < MAX_BACKGROUND_TASKS:
                    self.background_tasks[identity] = status or "running"
                elif not terminal:
                    raise ClaudeProtocolError("invalid-protocol", "Claude exceeded the background task bound")
                return None
            return None
        if frame_type == "assistant":
            self.model_messages += 1
            message = frame.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                names = [block.get("name") for block in content
                         if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str)]
                if names:
                    self.tool_calls += len(names)
                    return "tool-running", names[0][:80]
            return "streaming-model", None
        if frame_type == "stream_event":
            return "streaming-model", None
        if frame_type == "result":
            if self.result is not None:
                raise ClaudeProtocolError("invalid-protocol", "duplicate native result")
            self.result = frame
            return "finishing", None
        return None

    def unsettled_background_tasks(self) -> list[str]:
        """Reported native background work that has not settled; bounded identities."""
        return [*sorted(self.background_tasks), *["<unnamed>"] * self.anonymous_task_starts]
