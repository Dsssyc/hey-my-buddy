"""Bounded NDJSON transport and root-turn evidence for ZCode's native app server."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import select
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .turn_io import canonical_json, validate_outcome
from ..activity import MAX_SESSION_ID, MAX_TOOL_NAME, MAX_WAITING_REASON, PHASES

MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class NativeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_json(raw: str | bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("non-finite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def sign_receipt(payload: dict, key: str) -> str:
    return hmac.new(bytes.fromhex(key), canonical_json(payload).encode(), hashlib.sha256).hexdigest()


#: Why this adapter cannot ask correlated questions, stated once and reported
#: verbatim. The installed ZCode bundle has no wire-level in-turn input method:
#: the v4 method table exposes only ``v4/command``, and the ``sendText`` command is
#: the sole steer-capable input. Its ``requestedDelivery:"guide"`` is decided by
#: the gateway's ``admitPrompt``: on an unsteerable or already-settled turn the
#: command is queued as a deferred pending input, and with no live turn at all it
#: starts (or with ``startNow`` preempts) a NEW turn. The bundle's internal
#: ``steerTurn`` is the only path that accepts ``expectedTurnId``, and it is not
#: reachable from any client request. A runtime check of the ack cannot make the
#: race safe, so no question is ever injected.
NATIVE_INQUIRY_UNSUPPORTED = (
    "the installed ZCode v4 protocol has no turn-bound in-turn input method: sendText "
    "requestedDelivery=guide is unbound, can be deferred to a later new turn, and starts or "
    "preempts a turn when none is live; the internal steerTurn expectedTurnId is not exposed "
    "to clients, so this adapter observes only and never injects a question"
)


class ActivityProjection:
    """Bounded, metadata-only phase projection from the native event stream.

    It keeps only what :mod:`buddy.activity` allows: a phase, timestamps, an event
    ordinal, the last tool name and non-negative counts. Prompts, tool arguments,
    outputs, credentials and reasoning are never read into it, and
    :class:`buddy.activity.ActivitySidecar` owns validation, atomic replacement and
    throttling, so this module never duplicates the field registry.
    """

    PHASES = PHASES

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id[:MAX_SESSION_ID] if isinstance(session_id, str) else None
        self.phase = "starting"
        self.event_seq = 0
        self.observed_at = _now()
        self.last_native_activity_at: str | None = None
        self.last_tool_activity_at: str | None = None
        self.tool_name: str | None = None
        self.waiting_reason: str | None = None
        self.counts = {"modelTurns": 0, "toolCalls": 0}

    def note(self, message: dict, ordinal: int) -> bool:
        """Fold one native message in; return whether the phase changed."""
        self.event_seq = max(self.event_seq, ordinal)
        self.last_native_activity_at = _now()
        params = message.get("params") if isinstance(message, dict) else None
        method = message.get("method") if isinstance(message, dict) else None
        previous = self.phase
        if method == "state.updated":
            reason = (params or {}).get("reason")
            if reason == "prompt_completed":
                self.phase = "finishing"
            elif reason == "prompt_failed":
                self.phase = "finishing"
        elif method == "session/event" and isinstance(params, dict):
            kind = params.get("type")
            data = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            if kind == "turn.started":
                self.counts["modelTurns"] += 1
                self.phase = "streaming-model"
            elif kind in ("turn.completed", "turn.failed"):
                self.phase = "finishing"
            elif kind == "tool.updated":
                if data.get("kind") == "scheduled":
                    self.counts["toolCalls"] += 1
                    self.phase = "tool-running"
                    name = data.get("toolName")
                    if isinstance(name, str) and name:
                        self.tool_name = name.strip()[:MAX_TOOL_NAME]
                    self.last_tool_activity_at = _now()
                elif data.get("kind") in ("result", "error"):
                    self.phase = "streaming-model"
                    self.last_tool_activity_at = _now()
            elif kind in ("permission.requested", "userInput.requested"):
                self.phase = "waiting-host"
                self.waiting_reason = f"native {kind} requires Host authority"[:MAX_WAITING_REASON]
        self.observed_at = _now()
        return self.phase != previous

    def payload(self) -> dict:
        return {
            "phase": self.phase if self.phase in PHASES else "unknown",
            "observedAt": self.observed_at,
            "eventSeq": self.event_seq,
            "nativeSessionId": self.session_id,
            "lastNativeActivityAt": self.last_native_activity_at,
            "lastToolActivityAt": self.last_tool_activity_at,
            "toolName": self.tool_name,
            "waitingReason": self.waiting_reason,
            "counts": dict(self.counts),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_receipt(raw: object, configuration: dict) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > 70000:
        raise NativeError("invalid-finish", "the finish tool returned no bounded JSON receipt")
    try:
        receipt = decode_json(raw)
        if not isinstance(receipt, dict) or set(receipt) != {"version", "identity", "inputSha256", "outcome", "receiptId", "signature"}:
            raise ValueError("receipt fields")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise ValueError("signature")
        if receipt["version"] != 1 or receipt["identity"] != configuration["identity"] or receipt["inputSha256"] != configuration["inputSha256"]:
            raise ValueError("identity")
        if not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32 or validate_outcome(receipt["outcome"]):
            raise ValueError("outcome")
        return receipt
    except (ValueError, TypeError, KeyError, RecursionError):
        raise NativeError("invalid-finish", "the finish tool receipt failed identity, signature or outcome validation") from None


class NativeConnection:
    """Responses and reverse requests are pumped together; no request holds a reader."""

    def __init__(self, process: subprocess.Popen, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.responses: dict = {}
        self.next_id = 1
        self.ordinal = 0
        self.observe: Callable[[dict, int], None] = lambda _message, _ordinal: None
        self.attention: Callable[[dict], None] = lambda _record: None
        os.set_blocking(process.stdin.fileno(), False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while raw := self.process.stdout.readline(MAX_MESSAGE_BYTES + 1):
                if len(raw) > MAX_MESSAGE_BYTES:
                    raise ValueError("oversized native frame")
                message = decode_json(raw)
                if not isinstance(message, dict):
                    raise ValueError("native frame is not an object")
                self.messages.put(message)
        except (OSError, ValueError, RecursionError):
            self.messages.put(NativeError("invalid-protocol", "the native app server emitted invalid or oversized JSON"))
        finally:
            self.messages.put(None)

    def send(self, message: dict) -> None:
        raw = memoryview((canonical_json(message) + "\n").encode())
        if len(raw) > MAX_MESSAGE_BYTES:
            raise NativeError("invalid-protocol", "the native request exceeds its byte bound")
        try:
            while raw:
                if self.cancelled.is_set():
                    raise NativeError("cancelled", "the owned ZCode execution was cancelled")
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeError("timeout", "the owned ZCode execution exceeded its deadline")
                try:
                    count = os.write(self.process.stdin.fileno(), raw)
                    raw = raw[count:]
                except BlockingIOError:
                    select.select([], [self.process.stdin.fileno()], [], min(0.1, remaining))
        except (OSError, ValueError):
            raise NativeError("native-disconnected", "the native app server input closed") from None

    def pump(self) -> None:
        if self.cancelled.is_set():
            raise NativeError("cancelled", "the owned ZCode execution was cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise NativeError("timeout", "the owned ZCode execution exceeded its deadline")
        try:
            message = self.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            return
        if message is None:
            raise NativeError("native-disconnected", "the native app server closed before settlement")
        if isinstance(message, NativeError):
            raise message
        self.ordinal += 1
        if "id" in message and "method" in message:
            if message["method"] == "session/requestRuntimePreferences":
                self.send({"id": message["id"], "result": {
                    "nativeSearchEnhancementsEnabled": True, "memoryEnabled": False,
                    "askUserQuestionAutoResolutionEnabled": False, "modelContextBudgetStrategy": "preflight-v1",
                }})
                return
            if message["method"] == "interaction/requestProviderRuntimeHeaders":
                # OAuth account providers need a native authentication host that this
                # adapter deliberately cannot impersonate. It stays a hard error.
                self.attention_request(message, outcome="refused-with-jsonrpc-error")
                self.send({"id": message["id"], "error": {"code": -32601, "message": "This Buddy execution requires a structured attention outcome"}})
                raise NativeError("unsupported-provider", "OAuth account providers require a native ZCode authentication host")
            # Interactive host capabilities this execution cannot grant. Answer the
            # two known schemas with their documented refusal body so the owned turn
            # continues and reports attention, instead of crashing the controller.
            if message["method"] == "interaction/requestPermission":
                self.attention_request(message, outcome="denied-with-structured-response")
                self.send({"id": message["id"], "result": {
                    "decision": "deny",
                    "reason": "No interactive Host is attached to this governed Buddy turn; report attention in the turn outcome instead.",
                }})
                return
            if message["method"] == "interaction/requestUserInput":
                self.attention_request(message, outcome="declined-with-structured-response")
                self.send({"id": message["id"], "result": {
                    "action": "decline",
                    "reason": "No interactive Host is attached to this governed Buddy turn; report attention in the turn outcome instead.",
                }})
                return
            # Anything else (browser, official-MCP auth, future methods): the
            # documented unsupported-method refusal, recorded as attention.
            self.attention_request(message, outcome="refused-with-jsonrpc-error")
            self.send({"id": message["id"], "error": {"code": -32601, "message": "This Buddy execution requires a structured attention outcome"}})
            return
        if "id" in message:
            if len(self.responses) >= 16:
                raise NativeError("invalid-protocol", "too many unclaimed native responses")
            self.responses[message["id"]] = message
        else:
            self.observe(message, self.ordinal)

    def attention_request(self, message: dict, *, outcome: str = "refused-with-jsonrpc-error") -> None:
        """Record one unsupported interactive request; never raises on its own."""
        method = message.get("method")
        record = {
            "kind": "unsupported-native-request",
            "method": method[:120] if isinstance(method, str) else "unknown",
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "outcome": outcome,
            "hostAction": "no interactive Host is attached; the turn must report assistance or attention in its own structured outcome",
        }
        try:
            self.attention(record)
        except Exception:  # noqa: BLE001 - recording must never break the pump loop
            pass

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        if "error" in response:
            # Native errors can contain provider request details. Keep them in the
            # native private logs; public failures identify only the operation.
            raise NativeError("native-rpc-error", f"ZCode rejected {method}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise NativeError("invalid-protocol", f"ZCode returned no object for {method}")
        return result


class RootTurnEvidence:
    """Only a signed finish result in the explicitly admitted root turn can count."""

    def __init__(self, session_id: str, input_id: str, tool_name: str, bridge: dict):
        self.session_id, self.input_id, self.tool_name, self.bridge = session_id, input_id, tool_name, bridge
        self.turn_id: str | None = None
        self.call_id: str | None = None
        self.receipt: dict | None = None
        self.finish_failed = False
        self.last_seq = -1
        self.start_seq = self.call_seq = self.result_seq = self.end_seq = -1
        self.completed_ordinal = self.settled_ordinal = self.close_ordinal = 0

    def observe(self, message: dict, ordinal: int) -> None:
        p = message.get("params")
        if not isinstance(p, dict) or p.get("sessionId") != self.session_id:
            return
        if message.get("method") == "state.updated":
            if p.get("reason") == "prompt_failed":
                raise NativeError("native-turn-failed", "the native root prompt failed")
            if p.get("reason") == "prompt_completed":
                if not self.completed_ordinal:
                    raise NativeError("invalid-provenance", "prompt settlement preceded the root terminal event")
                self.settled_ordinal = ordinal
            return
        if message.get("method") != "session/event":
            return
        seq = p.get("seq")
        if type(seq) is not int or seq <= self.last_seq:
            raise NativeError("invalid-provenance", "native root event order is invalid")
        self.last_seq = seq
        data = p.get("payload") or {}
        if not isinstance(data, dict):
            raise NativeError("invalid-protocol", "native event payload is not an object")
        if p.get("type") == "turn.started":
            if data.get("inputId") != self.input_id or self.turn_id is not None or not p.get("turnId"):
                raise NativeError("wrong-native-turn", "the native root turn did not match the submitted input identity")
            self.turn_id, self.start_seq = p["turnId"], seq
            return
        if not self.turn_id or p.get("turnId") != self.turn_id:
            return
        if p.get("type") == "turn.failed":
            raise NativeError("native-turn-failed", "the native root turn failed")
        if p.get("type") == "turn.completed":
            if self.completed_ordinal or data.get("resultType") != "success" or data.get("inputId") != self.input_id:
                raise NativeError("native-turn-failed", "the native root turn did not complete successfully")
            if self.receipt is None:
                if self.finish_failed:
                    raise NativeError("finish-tool-failed", "the native root turn ended without a successful finish tool retry")
                raise NativeError("missing-finish", "the native root turn completed without an accepted finish tool")
            self.end_seq, self.completed_ordinal = seq, ordinal
            return
        if p.get("type") != "tool.updated" or any(data.get(k) for k in ("source", "parentToolCallId", "childSessionId", "childToolCallId", "agentId", "background")):
            return
        kind = data.get("kind")
        if kind == "scheduled":
            if self.call_id is not None:
                raise NativeError("duplicate-finish", "the root scheduled another tool after its finish call")
            if data.get("toolName") == self.tool_name:
                if not isinstance(data.get("toolCallId"), str) or not data["toolCallId"]:
                    raise NativeError("invalid-provenance", "the root finish call has no identity")
                self.call_id, self.call_seq = data["toolCallId"], seq
            return
        if data.get("toolCallId") != self.call_id or self.call_id is None:
            return
        if kind == "error":
            self._retry_failed_finish()
            return
        if kind == "result":
            result = data.get("result") or {}
            if self.receipt is not None or result.get("truncated") is not False:
                raise NativeError("finish-tool-failed", "the native finish result was duplicate, unsuccessful or truncated")
            if result.get("success") is False:
                self._retry_failed_finish()
                return
            if result.get("success") is not True:
                raise NativeError("finish-tool-failed", "the native finish result has no explicit success evidence")
            self.receipt = verify_receipt(result.get("content"), self.bridge)
            self.result_seq = seq

    def _retry_failed_finish(self) -> None:
        # Native schema validation and MCP isError responses are ordinary tool
        # failures. Keep the turn alive so the root can correct its arguments;
        # an already accepted receipt can never be withdrawn or replaced.
        if self.receipt is not None:
            raise NativeError("finish-tool-failed", "the native finish tool failed after an accepted receipt")
        self.finish_failed = True
        self.call_id, self.call_seq = None, -1

    def provenance(self) -> dict:
        if not (self.receipt and self.turn_id and self.call_id and self.start_seq < self.call_seq < self.result_seq < self.end_seq
                and 0 < self.completed_ordinal < self.settled_ordinal < self.close_ordinal):
            raise NativeError("invalid-provenance", "the native root result lacks ordered tool, turn and session settlement evidence")
        return {
            "adapter": "zcode", "tool": "buddy_finish_turn", "turnEnd": "completed", "rootSessionMatched": True,
            "inputId": self.input_id, "nativeSessionId": self.session_id, "nativeTurnId": self.turn_id,
            "toolCallId": self.call_id, "receiptId": self.receipt["receiptId"], "receiptVerified": True,
            "turnStartSeq": self.start_seq, "toolCallSeq": self.call_seq, "toolResultSeq": self.result_seq, "turnEndSeq": self.end_seq,
            "turnCompletedOrdinal": self.completed_ordinal, "promptCompletedOrdinal": self.settled_ordinal,
            "sessionCloseOrdinal": self.close_ordinal, "toolResultSuccess": True, "toolResultTruncated": False,
            "turnResultType": "success", "settlement": "session-closed",
        }
