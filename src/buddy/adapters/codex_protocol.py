"""Bounded Codex App Server JSONL transport and native turn observation."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import select
import threading
import time

from .turn_io import canonical_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINT_MESSAGE_BYTES = 65536
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


def native_checkpoint(evidence, turn_input: dict) -> dict:
    """Native history is evidence for continuation, never a workflow outcome."""
    from .turn_io import input_hash
    checkpoint = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    checkpoint.update(version=1, inputSha256=input_hash(turn_input), sessionId=evidence.thread_id,
                      nativeTurnId=evidence.turn_id, nativeTurnStarted=evidence.started,
                      nativeTurnStatus=(evidence.completed or {}).get("status", "incomplete"), eventSeq=evidence.event_seq,
                      bindingSaved=False)
    item = evidence.final_item or getattr(evidence, "last_agent_item", None)
    if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
        raw = item["text"].encode()
        text = raw[:MAX_CHECKPOINT_MESSAGE_BYTES].decode("utf-8", errors="ignore")
        # Bound the serialized string too: escaping can multiply its byte size.
        while len(canonical_json(text).encode()) > MAX_CHECKPOINT_MESSAGE_BYTES:
            text = text[:len(text) // 2]
        checkpoint["lastAssistantMessage"] = {"itemId": item["id"], "text": text, "phase": item.get("phase"), "sourceBytes": len(raw),
                                      "sha256": hashlib.sha256(raw).hexdigest(), "truncated": text != item["text"]}
    return checkpoint


def validated_checkpoint(payload: dict, turn_input: dict) -> dict | None:
    """Read only a stopped, exact-attempt native observation from its receipt."""
    from .turn_io import input_hash
    value = payload.get("nativeCheckpoint")
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    expected = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    expected["inputSha256"] = input_hash(turn_input)
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        return None
    process = payload.get("processState")
    if (not isinstance(process, dict) or process.get("shutdownConfirmed") is not True
            or value.get("nativeTurnStarted") is not True
            or value.get("nativeTurnStatus") not in ("completed", "failed", "interrupted", "incomplete")
            or type(value.get("bindingSaved")) is not bool
            or type(value.get("eventSeq")) is not int or value["eventSeq"] < 2):
        return None
    for key in ("sessionId", "nativeTurnId"):
        if not isinstance(value.get(key), str) or not value[key] or value[key] != payload.get(key):
            return None
    message = value.get("lastAssistantMessage")
    if message is not None:
        if (not isinstance(message, dict) or not isinstance(message.get("itemId"), str) or not message["itemId"]
                or not isinstance(message.get("text"), str) or type(message.get("truncated")) is not bool
                or type(message.get("sourceBytes")) is not int
                or not isinstance(message.get("sha256"), str) or len(message["sha256"]) != 64
                or len(canonical_json(message["text"]).encode()) > MAX_CHECKPOINT_MESSAGE_BYTES):
            return None
        raw = message["text"].encode()
        if message["sourceBytes"] < len(raw) or (not message["truncated"] and (
                message["sourceBytes"] != len(raw) or hashlib.sha256(raw).hexdigest() != message["sha256"])):
            return None
    return value


def checkpoint_resumable(payload: dict, checkpoint: dict) -> bool:
    process = payload.get("processState")
    return (checkpoint.get("nativeTurnStatus") == "completed" and checkpoint.get("bindingSaved") is True
            and isinstance(process, dict) and type(process.get("nativeExitCode")) is int and process["nativeExitCode"] == 0)

class TurnEvidence:
    def __init__(self, thread_id: str, turn_id: str):
        self.thread_id, self.turn_id = thread_id, turn_id
        self.started = False
        self.completed = None
        self.final_item = None
        self.last_agent_item = None
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
            if item.get("type") == "agentMessage":
                self.last_agent_item = item
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
