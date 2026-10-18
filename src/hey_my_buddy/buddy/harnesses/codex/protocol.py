"""Bounded Codex App Server JSONL transport and native turn observation."""
from __future__ import annotations

import hashlib
import os
import queue
import re
import select
import threading
import time

from ....json_codec import canonical_json, decode_strict_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
#: The whole native-refusal text — the ``Codex rejected <method>:`` prefix
#: included — is bounded to the UTF-8 byte budget the public ``RunEnd.message``
#: keeps, so a long native reason travels cut but readable, never dropped for
#: size. Nothing past ``error.message`` is expanded (no ``error.data``).
MAX_NATIVE_REFUSAL_BYTES = 512
_WINDOWS_PIPE = os.name == "nt"
_UNREADABLE_RESULT = "rpc/result-unreadable"


class CodexProtocolError(Exception):
    def __init__(self, code: str, message: str, *, native_error: dict | None = None):
        super().__init__(message)
        self.code = code
        self.native_error = native_error


#: The one shared strict decode (duplicate members and non-finite numbers,
#: including the ``1e999`` overflow, refused); the caller keeps its own error
#: mapping around the ``ValueError`` this raises.
decode_json = decode_strict_json


class Connection:
    def __init__(self, process, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.responses: dict[int, dict] = {}
        self.next_id = 1
        self.strict_responses = False
        self.pending_ids: set[int] = set()
        self.on_notification = lambda _message: None
        self.on_request = lambda _message: None
        #: Optional in-process callback invoked after every pump step, the waits
        #: inside ``call`` included. The unified run seam takes the role
        #: observer's pending feedback here, so a stop requested on a recorded
        #: fact takes effect before any further native waiting.
        self.after_pump = None
        self.last_result_readable: bool | None = None
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
            self.messages.put(CodexProtocolError("invalid-protocol", "Codex emitted invalid or oversized JSON"))
        finally:
            self.messages.put(None)

    def _remaining(self):
        if self.cancelled.is_set():
            raise CodexProtocolError("user-cancel", "the Codex execution was cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CodexProtocolError("deadline", "the Codex execution exceeded its deadline")
        return remaining

    def send(self, message: dict):
        raw = memoryview((canonical_json(message) + "\n").encode())
        if len(raw) > MAX_FRAME_BYTES:
            raise CodexProtocolError("invalid-protocol", "native request exceeds its byte bound")
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
            raise CodexProtocolError("transport-error", "Codex input closed") from None

    def pump(self):
        try:
            self._pump_one()
        finally:
            if self.after_pump is not None:
                self.after_pump()

    def _pump_one(self):
        remaining = self._remaining()
        try:
            message = self.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            return
        if message is None:
            raise CodexProtocolError("transport-error", "Codex closed before the request settled")
        if isinstance(message, CodexProtocolError):
            raise message
        if "id" in message and "method" in message:
            self.on_request(message)
        elif "id" in message:
            if type(message["id"]) is not int:
                raise CodexProtocolError("invalid-protocol", "invalid native response identity")
            if self.strict_responses and (message["id"] not in self.pending_ids or message["id"] in self.responses):
                raise CodexProtocolError("invalid-protocol", "uncorrelated or duplicate native sandbox reply")
            if len(self.responses) >= 16:
                raise CodexProtocolError("invalid-protocol", "too many pending native responses")
            self.responses[message["id"]] = message
        else:
            self.on_notification(message)

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.pending_ids.add(request_id)
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        self.pending_ids.discard(request_id)
        if "error" in response:
            error = response["error"]
            if (not isinstance(error, dict) or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)):
                raise CodexProtocolError("invalid-protocol", "Codex returned an invalid RPC error")
            native_error = {"code": error["code"], "message": error["message"]}
            text = f"Codex rejected {method}: {error['message']}"
            text = text.encode("utf-8")[:MAX_NATIVE_REFUSAL_BYTES].decode("utf-8", errors="ignore")
            raise CodexProtocolError("native-rpc-error", text, native_error=native_error)
        result = response.get("result")
        self.last_result_readable = isinstance(result, dict)
        if not self.last_result_readable:
            label = f"{_UNREADABLE_RESULT}/{method}"
            if not re.fullmatch(r"[A-Za-z0-9/._-]{1,64}", label):
                label = _UNREADABLE_RESULT
            self.on_notification({"method": label})
            return {}
        return result


#: One native ``thread/tokenUsage/updated`` breakdown, in camel case.
_USAGE_KEYS = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens",
               "cacheWriteInputTokens", "totalTokens")
#: Codex ``input_tokens`` already contains ``cached_input_tokens``; these are the
#: fields every breakdown must carry for the delta arithmetic to be exact.
_USAGE_REQUIRED = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens")


def _breakdown(value) -> dict | None:
    """One validated native usage breakdown, or ``None`` when it is unusable."""
    if not isinstance(value, dict):
        return None
    counters = {}
    for key in _USAGE_KEYS:
        item = value.get(key)
        if item is None and key not in _USAGE_REQUIRED:
            counters[key] = 0
            continue
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        counters[key] = item
    return counters


def attempt_token_usage(evidence) -> dict | None:
    """This attempt's own Codex usage, never the thread-cumulative total.

    ``thread/tokenUsage/updated`` carries the latest request's breakdown in
    ``last`` and the thread-cumulative breakdown in ``total``. The first
    notification of the bound native turn establishes the baseline
    (``total - last``), and the attempt is the delta from that baseline to the
    latest observed total. A repeated or replayed notification has a total that
    does not advance, so it can never be counted twice. When no baseline is
    provable — missing, or self-contradictory native totals — the usage stays
    unknown instead of being estimated from the cumulative total.
    """
    baseline, totals, events = evidence.usage_baseline, evidence.usage_total, evidence.usage_events
    if baseline is None or totals is None or not events:
        return None
    delta = {}
    for key in _USAGE_KEYS:
        value = totals[key] - baseline[key]
        if value < 0:
            return None
        delta[key] = value
    if delta["cachedInputTokens"] > delta["inputTokens"]:
        return None
    return {
        "source": "codex/app-server-thread-token-usage",
        "scope": "attempt",
        "coverage": "native-root-thread",
        "inputBasis": "includes-cached",
        "inputTokens": delta["inputTokens"],
        "cachedInputTokens": delta["cachedInputTokens"],
        "outputTokens": delta["outputTokens"],
        "reasoningOutputTokens": delta["reasoningOutputTokens"],
        "nativeRecords": evidence.usage_events,
        "completeness": "complete" if not evidence.usage_anomaly and isinstance(evidence.completed, dict) and evidence.completed.get("status") == "completed" else "partial",
    }


def turn_failure_code(turn) -> str | None:
    """The native structured error code of a failed native turn, when it has one."""
    error = turn.get("error") if isinstance(turn, dict) else None
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    return info if isinstance(info, str) and info else None

class TurnEvidence:
    def __init__(self, thread_id: str, turn_id: str):
        self.thread_id, self.turn_id = thread_id, turn_id
        self.started = False
        self.completed = None
        self.final_item = None
        self.superseded_final_answers = []
        self.last_agent_item = None
        self.event_seq = 0
        self.model_turns = 0
        self.tool_calls = 0
        # Per-turn native token usage: the observed baseline and latest thread
        # totals, never a session-cumulative figure reported as one attempt.
        self.usage_baseline = None
        self.usage_total = None
        self.usage_events = 0
        self.usage_anomaly = False
        self.usage_unprovable = False
        self.model_activity = False
        self.collector_failed = False

    def _observe_token_usage(self, token_usage) -> None:
        """Fold one native usage notification into this attempt's delta."""
        if not isinstance(token_usage, dict) or self.usage_unprovable:
            return
        last = _breakdown(token_usage.get("last"))
        total = _breakdown(token_usage.get("total"))
        if last is None or total is None:
            return
        if self.usage_baseline is None:
            baseline = {key: total[key] - last[key] for key in _USAGE_KEYS}
            if any(value < 0 for value in baseline.values()):
                # The native totals contradict themselves; no interval is
                # provable, so this attempt's usage must stay unknown rather
                # than being estimated from the cumulative total.
                self.usage_unprovable = True
                return
            self.usage_baseline = baseline
        if self.usage_total is not None:
            if total["totalTokens"] <= self.usage_total["totalTokens"]:
                # An identical or replayed notification never counts twice.
                return
            if any(total[key] < self.usage_total[key] for key in _USAGE_KEYS):
                self.usage_anomaly = True
        self.usage_total = total
        self.usage_events += 1

    @property
    def rejection_phase(self) -> str:
        if self.collector_failed:
            return "unknown"
        return "during-model" if self.model_activity else "before-model"

    def _observe_activity(self, method: str, params: dict) -> None:
        item = params.get("item")
        if isinstance(item, dict):
            kind = item.get("type")
            if kind in ("reasoning", "commandExecution", "fileChange", "mcpToolCall",
                        "dynamicToolCall", "collabAgentToolCall", "webSearch", "imageGeneration",
                        "function_call", "custom_tool_call"):
                self.model_activity = True
            elif kind == "agentMessage" and item.get("text"):
                self.model_activity = True
        if method in ("item/agentMessage/delta", "item/reasoning/textDelta",
                      "item/reasoning/summaryTextDelta") and params.get("delta"):
            self.model_activity = True
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            last = usage.get("last") if isinstance(usage, dict) else None
            if isinstance(last, dict) and any(type(last.get(key)) is int and last[key] > 0
                                            for key in ("outputTokens", "reasoningOutputTokens")):
                self.model_activity = True

    def _complete_final(self, item: dict) -> None:
        if self.final_item is not None:
            text = self.final_item.get("text")
            raw = text.encode("utf-8") if isinstance(text, str) else None
            parsable = False
            if raw is not None:
                try:
                    decode_json(raw)
                except (ValueError, RecursionError):
                    pass
                else:
                    parsable = True
            self.superseded_final_answers.append({
                "itemId": self.final_item.get("id"),
                "sourceBytes": len(raw) if raw is not None else None,
                "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "jsonParsable": parsable})
        self.final_item = item

    def observe(self, message: dict):
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            return None
        if params.get("threadId") != self.thread_id:
            return None
        turn = params.get("turn")
        native_turn_id = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        if native_turn_id != self.turn_id:
            return None
        self.event_seq += 1
        self._observe_activity(method, params)
        if method == "thread/tokenUsage/updated":
            self._observe_token_usage(params.get("tokenUsage"))
            return "streaming-model", None
        if method == "turn/started":
            self.started = True
            self.model_turns += 1
            return "waiting-model", None
        if method == "item/started":
            item = params.get("item")
            if not isinstance(item, dict):
                return None
            if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"):
                self.tool_calls += 1
                return "tool-running", item.get("type")
            return "streaming-model", None
        if method == "item/completed":
            item = params.get("item")
            if not isinstance(item, dict):
                return None
            if item.get("type") == "agentMessage":
                self.last_agent_item = item
            if self.completed is None and item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                self._complete_final(item)
            return "streaming-model", None
        if method == "turn/completed":
            if self.completed is not None:
                raise CodexProtocolError("invalid-protocol", "duplicate native turn completion")
            if isinstance(turn, dict) and isinstance(turn.get("items"), list):
                recover_final = self.final_item is None
                for item in turn["items"]:
                    self._observe_activity("item/completed", {"item": item})
                    if recover_final and isinstance(item, dict) and item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                        self._complete_final(item)
            self.completed = turn
            return "finishing", None
        return "streaming-model", None
