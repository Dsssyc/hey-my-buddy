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

import hashlib
import math
import os
import queue
import select
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ....protocol.usage import identifier as _identifier
from ..native_observations import bound_native_text, native_counter
from ....json_codec import canonical_json, decode_strict_json
from ....protocol.activity import native_tool_name

_WINDOWS_PIPE = os.name == "nt"

MAX_FRAME_BYTES = 8 * 1024 * 1024

#: The exact closed set of statuses that settle one reported native background
# task or workflow. Substring matching would wrongly settle "incomplete" or
# "not_completed", so equality is required. A terminal result frame may arrive
# while such work still runs; the controller must not then declare the turn
# completed over live native children. A ``task_notification`` reports one of
# ``completed``, ``failed`` or ``stopped`` per the Claude Agent SDK types.
BACKGROUND_TASK_TERMINAL_STATUSES = ("completed", "succeeded", "failed", "cancelled", "canceled",
                                     "error", "aborted", "done", "stopped")


class ClaudeProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class QuotaRejected(Exception):
    """A structured native quota rejection; carries only bounded typed facts."""

    def __init__(self, rate_limit_type: str, resets_at: object, *, source: str = "claude/stream-json-rate-limit-event"):
        super().__init__("the Claude provider quota rejected this execution")
        self.rate_limit_type = rate_limit_type
        self.resets_at = resets_at
        self.source = source


class Connection:
    """One bounded JSONL conversation with the native CLI's stdio streams."""

    def __init__(self, process, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.pending: dict[str, dict | None] = {}
        self.next_id = 1
        self.unknown_events = 0
        self.unread_responses = 0
        self.shape_observations: list[dict] = []
        self.on_request = lambda _frame: None
        self.on_message = lambda _frame: None
        os.set_blocking(process.stdin.fileno(), False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while raw := self.process.stdout.readline(MAX_FRAME_BYTES + 1):
                if len(raw) > MAX_FRAME_BYTES:
                    raise ValueError("oversized native frame")
                message = decode_strict_json(raw)
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
                    if _WINDOWS_PIPE:
                        self.cancelled.wait(min(0.05, remaining))
                    else:
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
        else:
            if not isinstance(frame_type, str):
                self.unknown_events += 1
                self._observe_shape("event-type-unread", message,
                                    typeShape=type(frame_type).__name__)
            self.on_message(message)

    def _observe_shape(self, kind: str, frame: dict, **fields) -> None:
        # Frames have already crossed the bounded strict-JSON transport. Keep
        # only shape and a digest: arbitrary native text can contain secrets.
        # The fixed sample cap does not grow with the number of unread frames.
        if len(self.shape_observations) < 16:
            raw = canonical_json(frame).encode()
            self.shape_observations.append({"kind": kind, "frameBytes": len(raw),
                                            "frameSha256": hashlib.sha256(raw).hexdigest(), **fields})

    def observations(self) -> dict:
        return {"status": "incomplete" if self.unknown_events or self.unread_responses else "complete",
                "unknownEvents": self.unknown_events, "unreadResponses": self.unread_responses,
                "samples": list(self.shape_observations),
                "samplesTruncated": self.unknown_events + self.unread_responses > len(self.shape_observations)}

    def call(self, request: dict) -> dict | None:
        request_id = f"hey-my-buddy-{self.next_id}"
        self.next_id += 1
        self.pending[request_id] = None
        self.send({"type": "control_request", "request_id": request_id, "request": request})
        while self.pending[request_id] is None:
            self.pump()
        response = self.pending.pop(request_id)
        if response.get("subtype") == "error":
            raise ClaudeProtocolError("native-rpc-error", f"Claude rejected {request.get('subtype')}")
        if response.get("subtype") != "success" or not isinstance(response.get("response"), dict):
            self.unread_responses += 1
            self._observe_shape("control-response-unread", response, requestId=request_id,
                                subtypeShape=type(response.get("subtype")).__name__,
                                responseShape=type(response.get("response")).__name__)
            return None
        return response["response"]


def _bounded_resets_at(value: object):
    if isinstance(value, str) and value and len(value) <= 64:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def result_quota_denial(result: dict) -> bool:
    """A structured native quota denial in the final result; never prose-based."""
    status = result.get("api_error_status")
    if status == 429 or status == "429":
        return True
    subtype = result.get("subtype")
    return isinstance(subtype, str) and any(marker in subtype.lower() for marker in ("quota", "usage_limit"))


def model_usage_keys(result: dict) -> list[str]:
    """The observed-model set; never one attested model identity."""
    usage = result.get("modelUsage")
    if not isinstance(usage, dict):
        return []
    keys = sorted(key for key in usage if isinstance(key, str) and key)
    return keys


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

    def __init__(self, session_id: str, cwd: str, *, provider: str = "unknown"):
        self.session_id, self.cwd = session_id, cwd
        self.provider = provider
        self.init_observed = False
        self.init_session_id: str | None = None
        self.session_model: str | None = None
        self.permission_mode: str | None = None
        self.result: dict | None = None
        self.event_seq = 0
        self.model_messages = 0
        self.tool_calls = 0
        self.root_model_activity = False
        self.permission_denials = 0
        self.background_tasks: dict[str, str] = {}
        self.anonymous_task_starts = 0
        # Native observation material for ADR-018 items 22/23 and the retained
        # assistant text. These never change the turn's status; they are facts
        # the adapter publishes, or nothing at all.
        self.usage_result: dict | None = None
        self.assistant_usage: dict[str, dict] = {}
        self.assistant_text: dict | None = None

    def observe(self, frame: dict):
        """Record one frame; returns an optional (phase, tool) activity update."""
        self.event_seq += 1
        frame_type = frame.get("type")
        if frame_type == "rate_limit_event":
            info = frame.get("rate_limit_info")
            if isinstance(info, dict) and info.get("status") == "rejected":
                # A rejection fails the attempt even when the rate-limit identity
                # is absent or malformed; the published facts stay sanitized.
                rate_limit_type = _identifier(info.get("rateLimitType")) or "unknown"
                raise QuotaRejected(rate_limit_type, _bounded_resets_at(info.get("resetsAt")))
            return None
        # Frames under a subagent's tool call are not root-turn evidence; a
        # subagent result never ends the root turn.
        parent = frame.get("parent_tool_use_id")
        if isinstance(parent, str) and parent:
            return None
        frame_session = frame.get("session_id")
        root_session = not isinstance(frame_session, str) or not frame_session or frame_session == self.session_id
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
                mode = frame.get("permissionMode")
                if isinstance(mode, str) and mode in (
                        "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"):
                    self.permission_mode = mode
                self.init_observed = True
                self.init_session_id = init_session
                return "starting", None
            if subtype == "task_started":
                identity = _task_identity(frame)
                if identity is None:
                    # Without an identity a later status cannot prove which
                    # task stopped. Keep the unresolved observation sticky.
                    self.anonymous_task_starts = 1
                else:
                    self.background_tasks[identity] = "running"
                return None
            if subtype in ("task_updated", "task_notification"):
                # The native CLI ends a foreground task that ran long enough to
                # get a task identity with ``task_notification`` and never sends
                # ``task_updated``; both end frames settle through this one path.
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
                elif not terminal:
                    self.background_tasks[identity] = status or "running"
                return None
            return None
        if frame_type == "assistant":
            self.model_messages += 1
            self._observe_assistant(frame)
            message = frame.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                if root_session and any(isinstance(block, dict) and block.get("type") in ("text", "thinking", "reasoning", "tool_use")
                       for block in content):
                    self.root_model_activity = True
                names = [block.get("name") for block in content
                         if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str)]
                if names:
                    self.tool_calls += len(names)
                    return "tool-running", native_tool_name(names[0])
            if root_session:
                self._observe_output_tokens(message.get("usage") if isinstance(message, dict) else None)
            return "streaming-model", None
        if frame_type == "stream_event":
            event = frame.get("event")
            if root_session and isinstance(event, dict):
                block = event.get("content_block")
                delta = event.get("delta")
                if (event.get("type") == "content_block_start" and isinstance(block, dict)
                        and block.get("type") in ("text", "thinking", "reasoning", "tool_use")) or (
                        event.get("type") == "content_block_delta" and isinstance(delta, dict)
                        and delta.get("type") in ("text_delta", "thinking_delta", "input_json_delta")):
                    self.root_model_activity = True
                if event.get("type") == "message_delta":
                    self._observe_output_tokens(event.get("usage"))
            return "streaming-model", None
        if frame_type == "result":
            if self.result is not None:
                raise ClaudeProtocolError("invalid-protocol", "duplicate native result")
            self.result = frame
            usage = frame.get("usage")
            if isinstance(usage, dict):
                self.usage_result = usage
                self._observe_output_tokens(usage)
            return "finishing", None
        return None

    def _observe_output_tokens(self, usage: object) -> None:
        if not isinstance(usage, dict):
            return
        details = usage.get("output_tokens_details")
        reasoning = native_counter(details.get("thinking_tokens")) if isinstance(details, dict) else None
        if (native_counter(usage.get("output_tokens")) or 0) > 0 or (reasoning or 0) > 0:
            self.root_model_activity = True

    def rejection_phase(self) -> str:
        return "during-model" if self.root_model_activity else "before-model"

    def _observe_assistant(self, frame: dict) -> None:
        """Keep one root assistant frame's text and deduplicated usage.

        A frame tied to another native session, or a subagent/tool frame (already
        filtered by ``parent_tool_use_id`` in :meth:`observe`), contributes
        neither text nor usage. Text is only ever taken from ``text`` content
        blocks; reasoning and tool blocks are never assistant output. Usage is
        deduplicated by the native message identity, so a replayed frame never
        counts twice and a replayed frame with different counters makes the
        fallback sum unprovable instead of being silently accumulated.
        """
        session_id = frame.get("session_id")
        if isinstance(session_id, str) and session_id and session_id != self.session_id:
            return
        message = frame.get("message")
        if not isinstance(message, dict):
            return
        message_id = message.get("id")
        message_id = message_id if isinstance(message_id, str) and message_id and len(message_id) <= 128 else None
        content = message.get("content")
        if isinstance(content, list):
            texts = [block["text"] for block in content
                     if isinstance(block, dict) and block.get("type") == "text"
                     and isinstance(block.get("text"), str) and block["text"].strip()]
            if texts:
                source_id = message_id or (frame.get("uuid") if isinstance(frame.get("uuid"), str) else None)
                retained = bound_native_text("\n".join(texts), source_id=source_id)
                if retained is not None:
                    self.assistant_text = retained
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return
        if message_id is None:
            # Without a native message identity a replay cannot be told from a new
            # observation, so it never contributes to the fallback sum.
            return
        if message_id in self.assistant_usage:
            # A replay with different counters stays unprovable; the first
            # observation is kept and the fallback is published as partial.
            return
        self.assistant_usage[message_id] = usage

    @staticmethod
    def _counters(usage: object) -> dict | None:
        """One Anthropic usage object's counters, or ``None`` when unusable.

        ``input_tokens`` excludes cached input by the provider's own definition;
        the cache read/creation counters are separately nullable, so a missing
        one stays unknown rather than becoming zero.
        """
        if not isinstance(usage, dict):
            return None
        input_tokens = native_counter(usage.get("input_tokens"))
        output_tokens = native_counter(usage.get("output_tokens"))
        if input_tokens is None and output_tokens is None:
            return None
        details = usage.get("output_tokens_details")
        reasoning = native_counter(details.get("thinking_tokens")) if isinstance(details, dict) else None
        return {"input": input_tokens, "output": output_tokens, "reasoning": reasoning,
                "read": native_counter(usage.get("cache_read_input_tokens")),
                "write": native_counter(usage.get("cache_creation_input_tokens"))}

    @staticmethod
    def _usage_document(counters: dict, *, source: str, records: int, completeness: str) -> dict:
        """One canonical-bound raw usage document; never a partial cache total."""
        read, write = counters["read"], counters["write"]
        cached = read + write if read is not None and write is not None else None
        document = {"source": source, "scope": "attempt", "coverage": "native-attempt" if completeness == "complete" else "native-root-session", "inputBasis": "excludes-cached",
                    "outputTokens": counters["output"], "nativeRecords": records,
                    "completeness": completeness if cached is not None else "partial"}
        if cached is None:
            # The provider's cache counters are unknown, so the canonical total
            # (input including cache) cannot be proven. The raw prompt count is
            # deliberately not published as if it were that total.
            document["cachedInputTokens"] = None
        else:
            document["inputTokens"] = counters["input"]
            document["cachedInputTokens"] = cached
            document["cacheReadTokens"] = read
            document["cacheWriteTokens"] = write
        if counters["reasoning"] is not None and counters["output"] is not None:
            document["reasoningOutputTokens"] = counters["reasoning"]
        return document

    def token_usage(self) -> dict | None:
        """This CLI execution's usage, or the deduplicated assistant fallback.

        The final ``result`` frame's ``usage`` is the CLI execution summary, so it
        is preferred and marked complete. When it is absent — a quota rejection, a
        deadline or a process failure never produce one — the bound, deduplicated
        root ``assistant.message.usage`` observations are summed and marked
        partial. A summary that is all zeros while real assistant observations
        exist is the documented crash/startup-error shape and is not preferred.
        """
        summary = self._counters(self.usage_result)
        if summary is not None:
            observed = any(summary[key] for key in ("input", "output", "read", "write"))
            if observed or not self.assistant_usage:
                return self._usage_document(summary, source="claude/stream-json-result-usage",
                                            records=1, completeness="complete")
        records = list(self.assistant_usage.values())
        if not records:
            return None
        counters = {"input": 0, "output": 0, "reasoning": 0, "read": 0, "write": 0}
        known = {key: True for key in counters}
        contributing = 0
        for usage in records:
            item = self._counters(usage)
            if item is None:
                continue
            contributing += 1
            for key in counters:
                if item[key] is None:
                    known[key] = False
                else:
                    counters[key] += item[key]
        if not contributing:
            return None
        for key in counters:
            if not known[key]:
                counters[key] = None
        return self._usage_document(counters, source="claude/stream-json-assistant-usage",
                                    records=contributing, completeness="partial")

    def last_assistant_message(self) -> dict | None:
        """The bounded last root assistant text observed before the result."""
        return dict(self.assistant_text) if isinstance(self.assistant_text, dict) else None

    def unsettled_background_tasks(self) -> list[str]:
        """Reported native background work that has not settled; bounded identities."""
        return [*sorted(self.background_tasks), *["<unnamed>"] * self.anonymous_task_starts]
