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


def command_envelope(*, command_id: str, client_id: str, session_id: str, type_: str, payload: dict,
                     issued_at_ms: int | None = None) -> dict:
    """Build the confirmed ``v4/command`` request envelope.

    ``sendText`` is not a CAS command, so no baseRevision/baseLogEpoch is claimed;
    the native ack reports ``revisionAtDecision`` for the caller to journal.
    """
    return {
        "commandId": command_id,
        "clientId": client_id,
        "sessionId": session_id,
        "type": type_,
        "payload": payload,
        "issuedAt": int(time.time() * 1000) if issued_at_ms is None else int(issued_at_ms),
    }


class ActivityProjection:
    """Bounded, metadata-only phase projection from the native event stream.

    It keeps only what the frozen activity contract allows: a phase, timestamps,
    an event ordinal, the last tool name and non-negative counts. Prompts, tool
    arguments, outputs, credentials and reasoning are never read into it. The
    ``buddy.activity`` helper owns validation, atomic replacement and throttling.
    """

    PHASES = ("starting", "waiting-model", "streaming-model", "tool-running", "waiting-external",
              "waiting-host", "finishing", "unknown")

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id
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
                    self.tool_name = name[:120] if isinstance(name, str) and name else self.tool_name
                    self.last_tool_activity_at = _now()
                elif data.get("kind") in ("result", "error"):
                    self.phase = "streaming-model"
                    self.last_tool_activity_at = _now()
            elif kind in ("permission.requested", "userInput.requested"):
                self.phase = "waiting-host"
                self.waiting_reason = f"native {kind} requires Host authority"[:160]
        self.observed_at = _now()
        return self.phase != previous

    def payload(self) -> dict:
        return {
            "phase": self.phase if self.phase in self.PHASES else "unknown",
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


def verify_inquiry_reply(raw: object, configuration: dict) -> dict:
    """Validate one signed ``buddy_inquiry_reply`` receipt.

    The receipt binds the attempt identity and the exact inquiry, so a reply from
    another attempt, a rewritten answer or a look-alike tool result can never be
    recorded as this run's answer. Correlation with the native root session/turn
    happens in :class:`InquiryReplyEvidence`, not here.
    """
    if not isinstance(raw, str) or len(raw.encode()) > 70000:
        raise NativeError("invalid-reply", "the inquiry reply tool returned no bounded JSON receipt")
    try:
        receipt = decode_json(raw)
        expected = {"version", "kind", "identity", "inputSha256", "inquiryId", "answer", "answerBytes", "receiptId", "signature"}
        if not isinstance(receipt, dict) or set(receipt) != expected:
            raise ValueError("receipt fields")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise ValueError("signature")
        if (receipt["version"] != 1 or receipt["kind"] != "inquiry-reply"
                or receipt["identity"] != configuration["identity"] or receipt["inputSha256"] != configuration["inputSha256"]):
            raise ValueError("identity")
        inquiry_id, answer = receipt["inquiryId"], receipt["answer"]
        if not isinstance(inquiry_id, str) or not inquiry_id or len(inquiry_id) > 128 or not inquiry_id.isprintable():
            raise ValueError("inquiry")
        if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > 4000:
            raise ValueError("answer")
        if receipt["answerBytes"] != len(answer.encode()) or not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32:
            raise ValueError("size")
        return receipt
    except (ValueError, TypeError, KeyError, RecursionError):
        raise NativeError("invalid-reply", "the inquiry reply receipt failed identity, signature or content validation") from None


class InquiryReplyEvidence:
    """Only a signed reply invoked by the admitted root session's own turn counts.

    A ``tool.updated`` event relayed from an internal subagent carries child/agent
    identity and never correlates; a scheduled call is admitted only for the exact
    root turn and the exact MCP tool name. An already answered inquiry, an unknown
    inquiry and a forged receipt are counted honestly and never overwrite a recorded
    answer.
    """

    def __init__(self, session_id: str, tool_name: str, bridge: dict, record: Callable[[dict, str], str | None]):
        self.session_id, self.tool_name, self.bridge, self.record = session_id, tool_name, bridge, record
        self.calls: dict[str, None] = {}
        self.answered: dict[str, dict] = {}
        self.refused: list[dict] = []

    def _refuse(self, reason: str, tool_call_id: str | None, inquiry_id: str | None = None) -> None:
        self.refused.append({"reason": reason[:160], "toolCallId": tool_call_id, "inquiryId": inquiry_id,
                             "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
        del self.refused[:-16]

    def observe(self, message: dict, ordinal: int) -> None:
        params = message.get("params")
        if not isinstance(params, dict) or params.get("sessionId") != self.session_id:
            return
        if message.get("method") != "session/event" or params.get("type") != "tool.updated":
            return
        data = params.get("payload") or {}
        if not isinstance(data, dict):
            return
        if any(data.get(key) for key in ("source", "parentToolCallId", "childSessionId", "childToolCallId", "agentId", "background")):
            return  # a relayed subagent call is never the root's own reply
        kind = data.get("kind")
        if kind == "scheduled":
            tool_call_id = data.get("toolCallId")
            if data.get("toolName") == self.tool_name and isinstance(tool_call_id, str) and tool_call_id:
                self.calls[tool_call_id] = None
                while len(self.calls) > 16:
                    self.calls.pop(next(iter(self.calls)))
            return
        tool_call_id = data.get("toolCallId")
        if kind != "result" or not isinstance(tool_call_id, str) or tool_call_id not in self.calls:
            return
        result = data.get("result") or {}
        if result.get("success") is not True or result.get("truncated") is not False:
            self._refuse("the native inquiry reply tool result was unsuccessful or truncated", tool_call_id)
            return
        try:
            receipt = verify_inquiry_reply(result.get("content"), self.bridge)
        except NativeError as error:
            self._refuse(error.code, tool_call_id)
            return
        inquiry_id = receipt["inquiryId"]
        if inquiry_id in self.answered:
            self._refuse("the inquiry already has a recorded answer", tool_call_id, inquiry_id)
            return
        reason = self.record(receipt, tool_call_id)
        if reason is not None:
            self._refuse(reason, tool_call_id, inquiry_id)
            return
        self.answered[inquiry_id] = receipt

    def report(self) -> dict:
        return {"answered": sorted(self.answered), "refused": len(self.refused),
                "lastRefusal": self.refused[-1] if self.refused else None}


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

    def send_text(self, envelope: dict) -> dict:
        """Deliver one bounded input through the confirmed ``v4/command`` surface.

        ``requestedDelivery`` is always an explicit guide/queue choice supplied by
        the caller; ``startNow`` is never requested because it preempts an active
        turn. The returned object is the native ack, so the caller can report the
        admitted delivery and refuse a preempted admission honestly.
        """
        ack = self.call("v4/command", envelope)
        return ack


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
