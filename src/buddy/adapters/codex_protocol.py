"""Bounded Codex App Server JSONL transport and native turn observation."""
from __future__ import annotations

import json
import os
import queue
import select
import threading
import time

from .turn_io import canonical_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
_WINDOWS_PIPE = os.name == "nt"


class CodexProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_json(raw: bytes | str) -> object:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


class Connection:
    def __init__(self, process, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.responses: dict[int, dict] = {}
        self.next_id = 1
        self.on_notification = lambda _message: None
        self.on_request = lambda _message: None
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
            if type(message["id"]) is not int or len(self.responses) >= 16:
                raise CodexProtocolError("invalid-protocol", "invalid native response identity")
            self.responses[message["id"]] = message
        elif isinstance(message.get("method"), str):
            self.on_notification(message)
        else:
            raise CodexProtocolError("invalid-protocol", "unrecognized native message")

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        if "error" in response:
            raise CodexProtocolError("native-rpc-error", f"Codex rejected {method}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexProtocolError("invalid-protocol", f"Codex returned no object for {method}")
        return result


_REQUEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
    "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                   "neededWork": {"type": "string"}, "expectedArtifacts": {"type": "array", "items": {"type": "string"}},
                   "acceptance": {"type": "string"}},
}


def _outcome_branch(dispositions, request_schema):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
        "properties": {
            "disposition": {"type": "string", "enum": dispositions},
            "summary": {"type": "string", "description": "Nonblank report; the entire serialized outcome must fit in 64 KiB of UTF-8. Keep requests and references concise."},
            "remaining": {"type": "array", "items": {"type": "string"}},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "request": request_schema,
        },
    }


# Structured Outputs permits a nested union, not a root union. The tagged
# branches prevent a "completed" result from carrying an unresolved request.
OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["outcome"],
    "properties": {"outcome": {"anyOf": [
        _outcome_branch(["completed"], {"type": "null"}),
        _outcome_branch(["assistance", "attention"], _REQUEST_SCHEMA),
    ]}},
}


def parse_outcome(text: str) -> dict:
    from .turn_io import validate_outcome
    value = decode_json(text)
    if not isinstance(value, dict) or set(value) != {"outcome"}:
        raise ValueError("The native result must contain exactly the structured outcome")
    outcome = value["outcome"]
    error = validate_outcome(outcome)
    if error:
        raise ValueError(error)
    return outcome

class TurnEvidence:
    def __init__(self, thread_id: str, turn_id: str):
        self.thread_id, self.turn_id = thread_id, turn_id
        self.started = False
        self.completed = None
        self.final_item = None
        self.event_seq = 0
        self.model_turns = 0
        self.tool_calls = 0

    def observe(self, message: dict):
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            return None
        turn = params.get("turn")
        native_turn_id = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        if native_turn_id != self.turn_id:
            return None
        self.event_seq += 1
        if method == "turn/started":
            self.started = True
            self.model_turns += 1
            return "waiting-model", None
        if method == "item/started":
            item = params.get("item") or {}
            if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"):
                self.tool_calls += 1
                return "tool-running", item.get("type")
            return "streaming-model", None
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                if self.final_item is not None:
                    raise CodexProtocolError("invalid-result", "multiple native final messages completed")
                self.final_item = item
            return "streaming-model", None
        if method == "turn/completed":
            if self.completed is not None:
                raise CodexProtocolError("invalid-protocol", "duplicate native turn completion")
            self.completed = turn
            return "finishing", None
        return "streaming-model", None
