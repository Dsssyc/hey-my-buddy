"""Bounded NDJSON transport and root-turn evidence for ZCode's native app server."""
from __future__ import annotations

import hmac
import os
import queue
import re
import select
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from ..native_observations import bound_native_text, native_counter
from ..session_receipts import (
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES,
    MAX_INQUIRY_ID_BYTES,
    MAX_INQUIRY_RECEIPT_BYTES,
    MAX_QUESTION_BYTES,
    MAX_TOOL_REFUSAL_BYTES,
    MAX_TOOL_REFUSAL_DETAIL_BYTES,
    MAX_TOOL_REFUSAL_PREFIX_BYTES,
    TOOL_REFUSAL_REASONS,
    sign_receipt,
)
from ....json_codec import canonical_json, decode_strict_json
from ....protocol.activity import MAX_SESSION_ID, MAX_TOOL_NAME, MAX_WAITING_REASON, PHASES

MAX_MESSAGE_BYTES = 8 * 1024 * 1024

#: Bound on the pre-model ``session/messages`` cursor read: enough to prove which
#: messages already existed, never an unbounded history read.
MAX_BASELINE_MESSAGES = 32
#: Bound on the post-settlement ``session/messages`` page. A page at this bound is
#: treated as possibly truncated, so a long turn is published as partial.
MAX_MESSAGE_PAGE = 64
#: Bound on the deduplicated root ``usage.delta`` identities of one attempt.
MAX_USAGE_DELTA_RECORDS = 4096
#: Every optional native metadata read gets its own wall-clock budget at most this
#: long and always restores the main execution deadline afterwards.
NATIVE_METADATA_TIMEOUT_SECONDS = 2.0

#: The counter set of one native ``usage.delta`` telemetry event. The native GV
#: function computes ``total = input + output`` with ``input`` already including
#: cached input, so ``cacheReadTokens``/``cacheWriteTokens`` are informational and
#: are never added to the input total a second time.
USAGE_DELTA_COUNTERS = ("inputTokens", "outputTokens", "totalTokens", "reasoningTokens",
                        "cacheReadTokens", "cacheWriteTokens")

#: Native structured fields a ``turn.failed`` attribution may carry a quota cause
#: on, most specific first. Only a code that classifies as a quota/rate failure is
#: used; provider prose is never read.
QUOTA_FAILURE_CODE_FIELDS = ("code", "errorType")
QUOTA_FAILURE_ATTRIBUTION_FIELDS = ("providerErrorCode", "reason")

#: Bounded retention for completed inquiry tool calls inside one native turn.
#: Only the most recent terminal call identities per tool are kept (to reject an
#: immediate duplicate terminal result); a verified receipt is handed to its
#: callback instead of being retained. Pending scheduled calls are never evicted
#: and no cumulative call count ever ends a long ``timeoutSeconds: 0`` turn.
MAX_RETAINED_INQUIRY_CALLS = 64


_WINDOWS_PIPE = os.name == "nt"

class NativeError(Exception):
    def __init__(self, code: str, message: str, failure: dict | None = None):
        super().__init__(message)
        self.code = code
        self.failure = failure


#: The one shared strict decode (duplicate members and non-finite numbers,
#: including the ``1e999`` overflow, refused); the caller keeps its own error
#: mapping around the ``ValueError`` this raises.
decode_json = decode_strict_json


#: How this adapter carries Host questions, stated once and reported verbatim.
#: The native protocol still has no turn-bound in-turn input: ``session/send``
#: has no delivery/expectedTurn fields and rejects a send during an active
#: prompt, and the v4 sendText guide can only defer unbound input or start a
#: new turn. The cooperative channel never injects anything: a question is
#: queued by the bridge and reaches the root only through the session-private
#: ``buddy_checkpoint``/``buddy_finish_turn`` tools inside the one admitted
#: native turn, and only ``buddy_answer_inquiry`` evidence verified against
#: that turn's own tool events counts as an answer.
COOPERATIVE_INQUIRY_NOTE = (
    "Host questions are queued by the bridge and delivered only at the root's next buddy_checkpoint "
    "or finish refusal inside the same admitted native turn; they are never injected through "
    "session/send, a v4 command, a stop, a restart or a new turn, and an answer counts only through "
    "buddy_answer_inquiry verified against the root turn's own tool evidence"
)


class ActivityProjection:
    """Bounded, metadata-only phase projection from the native event stream.

    It keeps only what :mod:`hey_my_buddy.protocol.activity` allows: a phase, timestamps, an event
    ordinal, the last tool name and non-negative counts. Prompts, tool arguments,
    outputs, credentials and reasoning are never read into it, and
    :class:`hey_my_buddy.protocol.activity.ActivitySidecar` owns validation, atomic replacement and
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


def verify_receipt(raw: object, configuration: dict, validate_outcome: Callable[[object], str | None]) -> dict:
    """Verify the signed finish receipt; malformed and forged failures stay distinct.

    Every stage keeps the fatal ``invalid-finish`` code — only a fully verified
    receipt is a success — but the bounded message distinguishes an unusable
    payload (no bounded JSON, wrong object shape) from a signature, attempt-
    identity or outcome failure, without ever quoting the raw content. The
    six-field outcome rule itself belongs to the Worker role: the current
    caller injects its narrow validator, so this protocol verifies the receipt
    with it instead of re-deciding what a legal outcome is, and a role-signed
    receipt still never skips the signature, binding or order checks here.
    """
    if not isinstance(raw, str) or len(raw.encode()) > 70000:
        raise NativeError("invalid-finish", "the finish tool returned no bounded JSON receipt")
    try:
        receipt = decode_json(raw)
        if not isinstance(receipt, dict) or set(receipt) != {"version", "identity", "inputSha256", "outcome", "receiptId", "signature"}:
            raise NativeError("invalid-finish", "the finish tool receipt was not the current signed receipt object")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise NativeError("invalid-finish", "the finish tool receipt failed its signature verification")
        if receipt["version"] != 1 or receipt["identity"] != configuration["identity"] or receipt["inputSha256"] != configuration["inputSha256"]:
            raise NativeError("invalid-finish", "the finish tool receipt failed its attempt-identity binding")
        if not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32 or validate_outcome(receipt["outcome"]):
            raise NativeError("invalid-finish", "the finish tool receipt failed its outcome validation")
        return receipt
    except NativeError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise NativeError("invalid-finish", "the finish tool receipt was malformed") from None


def _refusal_payload(raw: object) -> dict | None:
    """The candidate refusal object, tolerating only the wrapper's bounded framing.

    The installed native wrapper delivers an MCP ``isError`` text as a tool
    result prefixed with one bounded plain header line. This extracts the JSON
    candidate starting at the first ``{`` — no prose is interpreted, no
    substring is searched — and accepts it only when it decodes to a complete
    object (nothing but whitespace may follow) that explicitly claims the
    refusal format. Whether that candidate is genuine is decided solely by
    :func:`verify_tool_refusal`'s signature and binding checks.
    """
    if not isinstance(raw, str) or len(raw.encode()) > MAX_TOOL_REFUSAL_BYTES:
        return None
    text = raw.lstrip()
    start = text.find("{")
    if start < 0 or len(text[:start].encode()) > MAX_TOOL_REFUSAL_PREFIX_BYTES:
        return None
    try:
        value = decode_json(text[start:])
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and value.get("kind") == "tool-refusal" else None


def refusal_shaped(raw: object) -> bool:
    """True only when ``raw`` carries an object explicitly claiming the refusal format.

    This is dispatch between the two signed formats our own MCP emits, never
    prose recognition: a refusal-shaped payload still has to pass
    :func:`verify_tool_refusal` before anything recovers, and anything else
    keeps flowing to the (fatal) receipt verification.
    """
    return _refusal_payload(raw) is not None


def verify_tool_refusal(raw: object, configuration: dict, native_tool: str,
                        mounted_tools: tuple[str, ...] | list[str]) -> dict:
    """Verify one signed tool-refusal envelope against this attempt, input and tool.

    The private MCP mints these for expected argument, attention and inquiry
    refusals because the native wrapper surfaces an MCP ``isError`` response as
    a *successful* tool result framed with a plain header; the signature binds
    the attempt identity, the turn input and the exact session tool, so a
    tampered, cross-attempt or cross-tool envelope fails here fatally instead
    of becoming a recoverable refusal. The tool binding is checked against the
    session tools this run actually mounted — never a fixed global set — so a
    refusal verifies only for the service the driver really bound. A verified
    refusal only ever means "correct the call and retry in this same native
    turn"; it can never carry an outcome or mutate state.
    """
    tool = native_tool.rsplit("__", 1)[-1] if isinstance(native_tool, str) else None
    envelope = _refusal_payload(raw)
    if envelope is None:
        raise NativeError("invalid-tool-refusal", "the session tool returned no bounded JSON refusal envelope")
    try:
        if set(envelope) != {"version", "kind", "identity", "inputSha256",
                             "tool", "reason", "detail", "receiptId", "signature"}:
            raise NativeError("invalid-tool-refusal", "the tool refusal envelope was not the current signed refusal object")
        signature = envelope.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(envelope, configuration["key"])):
            raise NativeError("invalid-tool-refusal", "the tool refusal envelope failed its signature verification")
        if (envelope["version"] != 1 or envelope["kind"] != "tool-refusal"
                or envelope["identity"] != configuration["identity"]
                or envelope["inputSha256"] != configuration["inputSha256"]):
            raise NativeError("invalid-tool-refusal", "the tool refusal envelope failed its attempt-identity binding")
        if (not isinstance(envelope["tool"], str) or envelope["tool"] not in tuple(mounted_tools)
                or envelope["tool"] != tool or not native_tool.endswith("__" + envelope["tool"])):
            raise NativeError("invalid-tool-refusal", "the tool refusal envelope was signed for a different session tool")
        if (envelope["reason"] not in TOOL_REFUSAL_REASONS
                or not isinstance(envelope["receiptId"], str) or len(envelope["receiptId"]) != 32
                or not isinstance(envelope["detail"], str) or not envelope["detail"].strip()
                or len(envelope["detail"].encode()) > MAX_TOOL_REFUSAL_DETAIL_BYTES or "\0" in envelope["detail"]):
            raise NativeError("invalid-tool-refusal", "the tool refusal envelope failed its bounded reason validation")
        return envelope
    except NativeError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise NativeError("invalid-tool-refusal", "the tool refusal envelope was malformed") from None


def _valid_inquiry_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.encode()) <= MAX_INQUIRY_ID_BYTES


def _valid_question_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


#: The failure attribution this adapter imports from the exported native
#: ``turn.failed`` session event. The whitelist mirrors the installed
#: app-server's strict exported ``error.attribution`` schema: enum members,
#: bounded identifiers, ``statusCode`` 100..599 and ``retryable``. Every other
#: member of the native error object — ``message``, ``detail``, ``stack``,
#: ``underlyingErrorMessage``, ``underlyingErrorDetail`` and the opaque
#: ``data`` — can carry raw provider text, credentials, URLs or prompt
#: fragments and is never read into a result, log or summary.
NATIVE_ATTRIBUTION_ENUMS = {
    "source": {"provider", "runtime", "tool", "network"},
    "errorPhase": {"prepare", "configuration", "connect", "response", "stream", "parse", "validation", "unhandled"},
    "exceptionKind": {"api_call", "generic", "protocol", "provider_business", "transport", "type_error", "validation"},
    "transport": {"http", "sse", "websocket"},
}
NATIVE_ATTRIBUTION_TEXT = ("reason", "providerId", "modelId", "providerKind", "providerErrorCode")
#: The exported schema bounds each attribution identifier to 160 characters;
#: this decoder enforces the same bound on values it did not see validated.
MAX_NATIVE_FAILURE_TEXT = 160
MAX_NATIVE_FAILURE_SUMMARY = 240


def _failure_text(value: object, *, truncate: bool = False) -> str | None:
    """One bounded, printable failure identifier.

    Values the exported schema bounds to 160 characters are dropped rather than
    truncated when they exceed that bound (a longer value could not have come
    from the live protocol); natively unbounded strings are truncated.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Exported identifier fields must not become a second free-text error
    # channel. Reject URLs, prose and recognizable credential prefixes.
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", text) or text.lower().startswith(("sk-", "sk_", "bearer", "eyj")):
        return None
    if len(text) > MAX_NATIVE_FAILURE_TEXT:
        return text[:MAX_NATIVE_FAILURE_TEXT] if truncate else None
    return text


def _failure_summary(failure: dict) -> str:
    attribution = failure["attribution"]
    parts = []
    for key in ("source", "reason"):
        if attribution.get(key):
            parts.append(str(attribution[key]))
    if failure.get("code"):
        parts.append(f"code {failure['code']}")
    for key, label in (("statusCode", "status"), ("providerErrorCode", "providerCode"), ("errorPhase", "phase")):
        if attribution.get(key) is not None:
            parts.append(f"{label} {attribution[key]}")
    if attribution.get("retryable") is not None:
        parts.append("retryable" if attribution["retryable"] else "not-retryable")
    return " ".join(parts)[:MAX_NATIVE_FAILURE_SUMMARY]


def decode_native_failure(payload: object) -> dict | None:
    """Whitelisted attribution from one exported native ``turn.failed`` payload.

    The exported event supplies ``error`` (with ``type``, ``code``,
    ``attribution`` and ``retryable``) and ``turnPhase``; only those fields are
    imported, re-validated against the exported schema's own bounds, and
    composed into a bounded summary built exclusively from whitelisted values.
    When the event carries no attribution — the documented shape of a
    ``state.updated`` ``prompt_failed``, whose envelope has only a reason
    string and an opaque patch — the summary states that absence instead of
    manufacturing a cause, and no raw provider message is ever surfaced.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None
    error = payload["error"]
    raw = error.get("attribution")
    raw = raw if isinstance(raw, dict) else {}
    attribution = {}
    for key, allowed in NATIVE_ATTRIBUTION_ENUMS.items():
        if isinstance(raw.get(key), str) and raw[key] in allowed:
            attribution[key] = raw[key]
    for key in NATIVE_ATTRIBUTION_TEXT:
        text = _failure_text(raw.get(key))
        if text is not None:
            attribution[key] = text
    status = raw.get("statusCode")
    if type(status) is int and 100 <= status <= 599:
        attribution["statusCode"] = status
    retryable = raw.get("retryable")
    if type(retryable) is not bool:
        retryable = error.get("retryable")
    if type(retryable) is bool:
        attribution["retryable"] = retryable
    failure = {"errorType": _failure_text(error.get("type"), truncate=True),
               "code": _failure_text(error.get("code"), truncate=True),
               "turnPhase": _failure_text(payload.get("turnPhase"), truncate=True),
               "attribution": attribution}
    failure["summary"] = _failure_summary(failure) or "no structured failure attribution was exported"
    return failure


def quota_native_code(failure: dict) -> str | None:
    """The first native structured code of a ``turn.failed`` that means quota.

    The whitelisted attribution is scanned most-specific first; only a value the
    canonical classifier recognises as a quota or rate-limit failure is returned,
    so an unrelated failure (a context-window or transport error) never becomes a
    quota reason and no provider prose is ever read.
    """
    from ....protocol.usage import classify_quota_code
    attribution = failure.get("attribution") if isinstance(failure.get("attribution"), dict) else {}
    candidates = [failure.get(key) for key in QUOTA_FAILURE_CODE_FIELDS]
    candidates.extend(attribution.get(key) for key in QUOTA_FAILURE_ATTRIBUTION_FIELDS)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate and classify_quota_code(candidate) != "unknown":
            return candidate
    return None


def optional_native_call(connection, method: str, params: dict,
                         *, timeout: float = NATIVE_METADATA_TIMEOUT_SECONDS) -> dict | None:
    """One optional native metadata read with its own bounded wall-clock budget.

    Native usage and message metadata must never change the business result or
    hang the execution: a refused, malformed or timed-out read returns ``None``,
    and the main execution deadline is restored immediately afterwards. The read
    is bounded by the smaller of its own budget and the main deadline, so it can
    never extend the execution either.
    """
    original = connection.deadline
    connection.deadline = min(original, time.monotonic() + max(0.0, timeout))
    try:
        return connection.call(method, params)
    except (NativeError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        # An optional metadata read is never allowed to become the turn's result.
        return None
    finally:
        connection.deadline = original


def _bounded_message_page(response: object, *, session_id: str, limit: int) -> list[dict] | None:
    """One validated ``session/messages`` page, or ``None`` when unusable.

    Every entry must belong to the root session and carry a bounded string
    ``messageId``; a page larger than the requested bound is refused rather than
    trimmed, because the native server honours its own limit.
    """
    if not isinstance(response, dict):
        return None
    page = response.get("messages")
    if not isinstance(page, list) or len(page) > limit:
        return None
    for item in page:
        info = item.get("info") if isinstance(item, dict) else None
        if not isinstance(info, dict) or info.get("sessionId") != session_id:
            return None
        message_id = info.get("messageId")
        if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
            return None
    return page


def _root_assistant_tokens(message: dict) -> dict | None:
    """The native per-message token counters of one root assistant message.

    The native ``tokens`` object reports ``input`` already including cached input
    (the native GV rule), with ``cache.read``/``cache.write`` informational. All
    counters are required by the exported schema; any missing or invalid counter
    makes this message unusable instead of contributing zeros.
    """
    info = message.get("info")
    tokens = info.get("tokens") if isinstance(info, dict) else None
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        return None
    counters = {"input": native_counter(tokens.get("input")), "output": native_counter(tokens.get("output")),
                "reasoning": native_counter(tokens.get("reasoning")),
                "read": native_counter(cache.get("read")), "write": native_counter(cache.get("write"))}
    if any(value is None for value in counters.values()):
        return None
    return counters


def _root_assistant_text(message: dict) -> dict | None:
    """One root assistant message's own text parts, bounded, never reasoning/tool."""
    info = message.get("info")
    if not isinstance(info, dict):
        return None
    message_id = info.get("messageId")
    if not isinstance(message_id, str) or not message_id:
        return None
    parts = message.get("parts")
    if not isinstance(parts, list) or len(parts) > 256:
        return None
    texts = []
    for part in parts:
        if (not isinstance(part, dict) or part.get("type") != "text"
                or part.get("messageId") != message_id or part.get("sessionId") != info.get("sessionId")
                or not isinstance(part.get("text"), str) or not part["text"].strip()):
            continue
        texts.append(part["text"])
    if not texts:
        return None
    return bound_native_text("\n".join(texts), source_id=message_id)


class ZcodeAttemptUsage:
    """Attempt-scoped ZCode usage from root-turn telemetry and boundary reads.

    Two native sources are used, in this order of preference:

    1. ``session/messages`` read at both the pre-model boundary and the settlement
       (or failure) boundary. Only messages strictly after the pre-model cursor,
       belonging to this root session and carrying a non-empty native
       ``parentMessageId`` may contribute. The cursor must not reappear in the
       second page — the native server returns the whole conversation when it
       cannot resolve it — so a resumed session never re-counts earlier turns.
    2. The ``v4/telemetry/event`` ``usage.delta`` notifications of the already
       started root turn, deduplicated by native event identity. The stream is not
       a durable record, so this fallback is always published as partial.

    Nothing is estimated: a counter no contributing record reported stays absent,
    a contradictory replay makes the delta sum unknown, and a failed or missing
    baseline makes the message sum unprovable instead of "all messages".
    """

    def __init__(self, session_id: str, *, resumed: bool):
        self.session_id = session_id
        self.resumed = resumed
        self.delta_records: dict[str, dict] = {}
        self.delta_conflict = False
        self.baseline_ids: frozenset[str] = frozenset()
        self.baseline_ready = False
        self.baseline_last: str | None = None
        self.message_counters: dict | None = None
        self.message_records = 0
        self.message_truncated = False
        self.message_complete = False
        self.last_assistant_message: dict | None = None
        self.child_activity = False

    # -- telemetry -----------------------------------------------------------
    def observe(self, message: dict, turn_id: str | None) -> None:
        """Fold one native notification into the attempt's observations."""
        method = message.get("method") if isinstance(message, dict) else None
        params = message.get("params") if isinstance(message, dict) else None
        if method == "v4/telemetry/event":
            self._observe_usage_delta(params, turn_id)
            return
        if method == "session/event" and isinstance(params, dict):
            data = params.get("payload")
            if isinstance(data, dict) and any(
                    data.get(key) for key in ("source", "parentToolCallId", "childSessionId",
                                              "childToolCallId", "agentId", "background")):
                # Child/subagent work lives in another native session; its usage is
                # never mixed into this root attempt, and its presence makes the
                # root-only sum partial.
                self.child_activity = True

    def _observe_usage_delta(self, params: object, turn_id: object) -> None:
        if not isinstance(params, dict) or params.get("kind") != "usage.delta":
            return
        if not isinstance(turn_id, str) or not turn_id:
            # The delta must belong to an already started root turn.
            return
        if type(params.get("version")) is not int or params["version"] != 1:
            return
        if params.get("sessionId") != self.session_id:
            return
        native_turn = params.get("turnId")
        if native_turn != turn_id:
            return
        event_id = params.get("eventId")
        if isinstance(event_id, str) and 0 < len(event_id) <= 128:
            key = "event:" + event_id
        else:
            sequence = params.get("eventSeq")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                return
            key = f"seq:{sequence}"
        counters = {}
        for name in USAGE_DELTA_COUNTERS:
            value = native_counter(params.get(name))
            if value is None:
                return
            counters[name] = value
        existing = self.delta_records.get(key)
        if existing is not None:
            if existing != counters:
                # One native identity with two different payloads cannot be summed.
                self.delta_conflict = True
            return
        if len(self.delta_records) >= MAX_USAGE_DELTA_RECORDS:
            return
        self.delta_records[key] = counters

    def delta_usage(self) -> dict | None:
        if self.delta_conflict or not self.delta_records:
            return None
        totals = {name: 0 for name in USAGE_DELTA_COUNTERS}
        for counters in self.delta_records.values():
            for name, value in counters.items():
                totals[name] += value
        return {"source": "zcode/v4-telemetry-usage-delta", "scope": "attempt", "coverage": "native-root-session",
                "inputBasis": "includes-cached",
                "inputTokens": totals["inputTokens"], "outputTokens": totals["outputTokens"],
                "cachedInputTokens": totals["cacheReadTokens"] + totals["cacheWriteTokens"],
                "reasoningOutputTokens": totals["reasoningTokens"],
                "nativeRecords": len(self.delta_records), "completeness": "partial"}

    # -- message boundary reads ---------------------------------------------
    def capture_baseline(self, connection) -> None:
        """One bounded pre-model read of the messages that already existed."""
        response = optional_native_call(connection, "session/messages",
                                        {"sessionId": self.session_id, "limit": MAX_BASELINE_MESSAGES})
        page = _bounded_message_page(response, session_id=self.session_id, limit=MAX_BASELINE_MESSAGES)
        if page is None:
            return
        self.baseline_ids = frozenset(item["info"]["messageId"] for item in page)
        self.baseline_last = page[-1]["info"]["messageId"] if page else None
        self.baseline_ready = True

    def capture_final(self, connection) -> None:
        """One bounded post-settlement read of this attempt's new root messages."""
        if not self.baseline_ready or (self.baseline_last is None and self.resumed):
            # Without a provable cursor nothing may be attributed to this attempt:
            # a resumed session whose read lost its cursor would otherwise be
            # counted from its beginning.
            return
        params = {"sessionId": self.session_id, "limit": MAX_MESSAGE_PAGE}
        if self.baseline_last is not None:
            params["afterMessageId"] = self.baseline_last
        response = optional_native_call(connection, "session/messages", params)
        page = _bounded_message_page(response, session_id=self.session_id, limit=MAX_MESSAGE_PAGE)
        if page is None:
            return
        if any(item["info"]["messageId"] in self.baseline_ids for item in page):
            # The native server could not resolve the cursor and returned the
            # whole conversation; no entry in this page is provably new.
            return
        self.message_truncated = len(page) >= MAX_MESSAGE_PAGE
        new_messages = [item for item in page
                        if item["info"].get("role") == "assistant"
                        and isinstance(item["info"].get("parentMessageId"), str)
                        and item["info"]["parentMessageId"].strip()]
        totals = {"input": 0, "output": 0, "reasoning": 0, "read": 0, "write": 0}
        contributing = 0
        usable = True
        for item in new_messages:
            # The retained text is evidence of its own: a message whose token
            # counters are unusable still leaves its root text for a continuation.
            retained = _root_assistant_text(item)
            if retained is not None:
                self.last_assistant_message = retained
            counters = _root_assistant_tokens(item)
            if counters is None:
                # A root assistant message without usable native counters means the
                # root-only sum is a lower bound, not a total.
                usable = False
                continue
            contributing += 1
            for key in totals:
                totals[key] += counters[key]
        if contributing:
            self.message_counters = totals
            self.message_records = contributing
            self.message_complete = usable and not self.message_truncated and not self.child_activity

    def message_usage(self) -> dict | None:
        if self.message_counters is None:
            return None
        totals = self.message_counters
        return {"source": "zcode/session-messages-root-assistant-tokens", "scope": "attempt", "coverage": "native-root-session",
                "inputBasis": "includes-cached",
                "inputTokens": totals["input"], "outputTokens": totals["output"],
                "cachedInputTokens": totals["read"] + totals["write"],
                "reasoningOutputTokens": totals["reasoning"],
                "nativeRecords": self.message_records,
                "completeness": "complete" if self.message_complete else "partial"}

    def raw_usage(self) -> dict | None:
        """The strictly bound message tokens when provable, else the delta fallback."""
        return self.message_usage() or self.delta_usage()


def verify_inquiry_receipt(raw: object, configuration: dict, kind: str) -> dict:
    """Verify one signed checkpoint or answer receipt from the session tools.

    The MCP handler only ever returns a tentative signed receipt; this is the
    controller-side authority check. The signature binds the attempt identity
    and the exact payload, so a tampered, stale or cross-attempt receipt fails
    here before any bridge state changes.
    """
    if kind not in ("inquiry-checkpoint", "inquiry-answer"):
        raise ValueError("unknown inquiry receipt kind")
    fields = ({"version", "kind", "identity", "inquiries", "receiptId", "signature"} if kind == "inquiry-checkpoint"
              else {"version", "kind", "identity", "inquiryId", "questionSha256", "answer", "receiptId", "signature"})
    #: A checkpoint receipt may name how many queued questions did not fit its
    #: serialized batch budget, so explicit batching never silently hides them.
    optional = {"morePending"} if kind == "inquiry-checkpoint" else set()
    if not isinstance(raw, str) or len(raw.encode()) > MAX_INQUIRY_RECEIPT_BYTES:
        raise NativeError("invalid-inquiry-receipt", f"the {kind} tool returned no bounded JSON receipt")
    try:
        receipt = decode_json(raw)
        if (not isinstance(receipt, dict) or set(receipt) - optional != fields or not fields <= set(receipt)
                or ("morePending" in receipt
                    and (type(receipt["morePending"]) is not int or not 0 <= receipt["morePending"] <= MAX_INQUIRIES))):
            raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt was not the current signed receipt object")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt failed its signature verification")
        if receipt["version"] != 1 or receipt["kind"] != kind or receipt["identity"] != configuration["identity"]:
            raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt failed its attempt-identity binding")
        if not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32:
            raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt has no usable receipt identity")
        if kind == "inquiry-answer":
            if (not _valid_inquiry_id(receipt["inquiryId"]) or not _valid_question_sha(receipt["questionSha256"])
                    or not isinstance(receipt["answer"], str) or not receipt["answer"].strip()
                    or len(receipt["answer"].encode()) > MAX_ANSWER_BYTES):
                raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt failed its answer binding")
        else:
            inquiries = receipt["inquiries"]
            if not isinstance(inquiries, list) or len(inquiries) > MAX_INQUIRIES:
                raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt failed its inquiry-list binding")
            for item in inquiries:
                if (not isinstance(item, dict)
                        or set(item) - {"inquiryId", "question", "questionSha256", "state", "askedAt", "deliveredAt"}
                        or not {"inquiryId", "question", "questionSha256", "state", "askedAt"} <= set(item)
                        or not _valid_inquiry_id(item["inquiryId"]) or not _valid_question_sha(item["questionSha256"])
                        or item["state"] not in ("queued", "delivered")
                        or not isinstance(item["question"], str) or not item["question"].strip()
                        or len(item["question"].encode()) > MAX_QUESTION_BYTES
                        or not isinstance(item["askedAt"], str)
                        or not isinstance(item.get("deliveredAt", ""), str)):
                    raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt failed its inquiry-entry binding")
        return receipt
    except NativeError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise NativeError("invalid-inquiry-receipt", f"the {kind} receipt was malformed") from None


class NativeConnection:
    """Responses and reverse requests are pumped together; no request holds a reader."""

    def __init__(self, process: subprocess.Popen, deadline: float, cancelled: threading.Event, *, no_tools: bool = False):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.no_tools = no_tools
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.responses: dict = {}
        self.next_id = 1
        self.ordinal = 0
        self.observe: Callable[[dict, int], None] = lambda _message, _ordinal: None
        self.attention: Callable[[dict], None] = lambda _record: None
        self.after_pump: Callable[[], None] = lambda: None
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
                    if _WINDOWS_PIPE:
                        self.cancelled.wait(min(0.05, remaining))
                    else:
                        select.select([], [self.process.stdin.fileno()], [], min(0.1, remaining))
        except (OSError, ValueError):
            raise NativeError("native-disconnected", "the native app server input closed") from None

    def pump(self) -> None:
        """Dispatch feedback after each I/O step, including a call's reply wait.

        Reverse requests record facts and finish their same-id refusal before
        this callback runs; an ordinary observer exception then interrupts the
        waiting call without bypassing the refusal or the attention recorder.
        """
        try:
            self._pump()
        finally:
            self.after_pump()

    def _pump(self) -> None:
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
                    "nativeSearchEnhancementsEnabled": not self.no_tools, "memoryEnabled": False,
                    "askUserQuestionAutoResolutionEnabled": False, "modelContextBudgetStrategy": "preflight-v1",
                }})
                return
            if self.no_tools:
                # Every native interaction in a no-tool call — provider headers
                # included — is answered with the documented refusal and
                # recorded as a denied interaction. Whether it ends the run is
                # the role observer's immediate decision over the retained
                # fact, never this pump's; the fast rule keeps its own legacy
                # stop reason instead of unsupported-provider.
                self.attention_request(message, outcome="refused-with-jsonrpc-error")
                self.send({"id": message["id"], "error": {"code": -32601, "message": "No interactions are allowed in a no-tool call"}})
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
    """Only signed results in the explicitly admitted root turn can count.

    Besides the finish tool this tracker owns the inquiry channel's authority:
    a question only becomes ``delivered`` when a ``buddy_checkpoint`` result in
    this root session and native turn succeeded untruncated with a signed
    receipt, and an answer only counts when a ``buddy_answer_inquiry`` result
    in the same turn carried a signed receipt the controller re-verified against
    the bridge's committed state. Child sessions, relayed sources, agent and
    background calls are excluded exactly like finish evidence.
    """

    def __init__(self, session_id: str, input_id: str, tool_name: str, bridge: dict, *,
                 checkpoint_name: str | None = None, answer_name: str | None = None,
                 on_delivery: Callable[[dict, str], None] | None = None,
                 on_answer: Callable[[dict, str], None] | None = None,
                 validate_outcome: Callable[[object], str | None],
                 mounted_tools: tuple[str, ...] | list[str]):
        self.session_id, self.input_id, self.tool_name, self.bridge = session_id, input_id, tool_name, bridge
        self.checkpoint_name, self.answer_name = checkpoint_name, answer_name
        self.on_delivery, self.on_answer = on_delivery, on_answer
        self.validate_outcome = validate_outcome
        #: The bare session tools this run actually mounted — a required,
        #: caller-provided set with no default: a signed refusal envelope
        #: verifies only for a tool this run really bound, and the verifier
        #: never assumes which tools a carrier mounts.
        self.mounted_tools = tuple(mounted_tools)
        self.turn_id: str | None = None
        self.call_id: str | None = None
        self.receipt: dict | None = None
        self.finish_failed = False
        #: Root-session calls whose result carried verified delivery evidence
        #: (a verified receipt or a verified signed refusal envelope). Only
        #: these are the completion mechanism's own calls; everything else the
        #: projection saw — child relays, foreign sessions, unverified or
        #: forged same-name calls — stays a task-tool fact.
        self.verified_delivery_calls: set[str] = set()
        self.last_seq = -1
        self.start_seq = self.call_seq = self.result_seq = self.end_seq = -1
        self.completed_ordinal = self.settled_ordinal = self.close_ordinal = 0
        self.checkpoint_calls: dict[str, dict] = {}
        self.answer_calls: dict[str, dict] = {}
        self._terminal_checkpoint_calls: deque[str] = deque()
        self._terminal_answer_calls: deque[str] = deque()

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
            # Only the exported turn.failed event carries structured failure
            # attribution; the summary is composed of whitelisted values only.
            failure = decode_native_failure(data)
            detail = f" ({failure['summary']})" if failure is not None else ""
            raise NativeError("native-turn-failed", f"the native root turn failed{detail}", failure=failure)
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
        tool_call_id = data.get("toolCallId")
        if kind == "scheduled":
            if self.call_id is not None:
                raise NativeError("duplicate-finish", "the root scheduled another tool after its finish call")
            name = data.get("toolName")
            if name == self.tool_name:
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise NativeError("invalid-provenance", "the root finish call has no identity")
                self.call_id, self.call_seq = tool_call_id, seq
            elif name == self.checkpoint_name and self.checkpoint_name is not None:
                if not isinstance(tool_call_id, str) or not tool_call_id or tool_call_id in self.checkpoint_calls:
                    raise NativeError("invalid-provenance", "the root checkpoint call has no usable identity")
                self.checkpoint_calls[tool_call_id] = {"seq": seq, "result": None}
            elif name == self.answer_name and self.answer_name is not None:
                if not isinstance(tool_call_id, str) or not tool_call_id or tool_call_id in self.answer_calls:
                    raise NativeError("invalid-provenance", "the root answer call has no usable identity")
                self.answer_calls[tool_call_id] = {"seq": seq, "result": None}
            return
        if self.call_id is not None and tool_call_id == self.call_id:
            if kind == "error":
                self._retry_failed_finish()
                return
            if kind == "result":
                result = data.get("result") or {}
                if self.receipt is not None or result.get("truncated") is not False:
                    raise NativeError("finish-tool-failed", "the native finish result was duplicate, unsuccessful or truncated")
                content = result.get("content")
                if refusal_shaped(content):
                    # A content that claims the signed refusal format is verified
                    # for either native success marker before any retryable
                    # ordinary-error path, so a forged, tampered or wrong-tool
                    # envelope can never slip through as a plain retryable
                    # failure. Only a fully verified, attempt/tool/input-bound
                    # envelope recovers the turn for a corrected retry.
                    verify_tool_refusal(content, self.bridge, self.tool_name, self.mounted_tools)
                    self.verified_delivery_calls.add(tool_call_id)
                    self._retry_failed_finish()
                    return
                if result.get("success") is False:
                    self._retry_failed_finish()
                    return
                if result.get("success") is not True:
                    raise NativeError("finish-tool-failed", "the native finish result has no explicit success evidence")
                self.receipt = verify_receipt(content, self.bridge, self.validate_outcome)
                self.verified_delivery_calls.add(tool_call_id)
                self.result_seq = seq
            return
        if kind in ("result", "error") and tool_call_id in self.checkpoint_calls:
            self._inquiry_result(self.checkpoint_calls, self._terminal_checkpoint_calls, data, kind,
                                 "inquiry-checkpoint", tool_call_id)
            return
        if kind in ("result", "error") and tool_call_id in self.answer_calls:
            self._inquiry_result(self.answer_calls, self._terminal_answer_calls, data, kind,
                                 "inquiry-answer", tool_call_id)
            return

    def verified_delivery(self) -> frozenset[tuple[str, str]]:
        """The verified delivery calls, as the collector's own full call keys.

        Each entry is ``(canonical_json(native_identity), callId)`` on the
        verified root, so an exclusion cannot catch a foreign root that merely
        reuses the call id. An unstarted root has no verified delivery calls.
        """
        identity = canonical_json({"sessionId": self.session_id, "turnId": self.turn_id})
        return frozenset((identity, call_id) for call_id in self.verified_delivery_calls)

    def _retain_terminal(self, calls: dict[str, dict], terminal: deque[str], tool_call_id: str) -> None:
        """Keep only the most recent terminal call identities, never a payload.

        Pending scheduled calls are never evicted; an evicted terminal identity
        means a much later duplicate result for it is simply ignored (never
        imported) instead of raising, and no cumulative count limits the turn.
        """
        terminal.append(tool_call_id)
        while len(terminal) > MAX_RETAINED_INQUIRY_CALLS:
            calls.pop(terminal.popleft(), None)

    def _inquiry_result(self, calls: dict[str, dict], terminal: deque[str], data: dict, kind: str,
                        receipt_kind: str, tool_call_id: str) -> None:
        """Import one checkpoint/answer tool result under root-turn authority.

        An ordinary tool error keeps the turn alive for a corrected retry, exactly
        like a failed finish, and so does a verified signed tool-refusal envelope
        arriving inside a native wrapper result marked successful. A successful
        result must otherwise carry a signed receipt the controller re-verifies;
        the bridge callback then applies the state change or raises for a forged,
        stale or conflicting binding. A duplicate terminal result for one retained
        call identity is a protocol violation. The verified receipt itself is
        handed to its callback and never retained, so an unlimited turn cannot
        accumulate checkpoint question text.
        """
        call = calls[tool_call_id]
        if call["result"] is not None:
            raise NativeError("invalid-provenance", f"the native {receipt_kind} call produced a duplicate terminal result")
        if kind == "error":
            call["result"] = "tool-error"
            self._retain_terminal(calls, terminal, tool_call_id)
            return
        result = data.get("result") or {}
        if result.get("truncated") is not False:
            raise NativeError("invalid-inquiry-receipt", f"the native {receipt_kind} result was truncated")
        content = result.get("content")
        native_name = self.checkpoint_name if receipt_kind == "inquiry-checkpoint" else self.answer_name
        if refusal_shaped(content):
            # Verified for either native success marker before any retryable
            # ordinary-error path, exactly like the finish tool: a forged or
            # wrong-tool envelope is fatal even when the wrapper marked the
            # result failed.
            verify_tool_refusal(content, self.bridge, native_name, self.mounted_tools)
            call["result"] = "tool-refusal"
            self._retain_terminal(calls, terminal, tool_call_id)
            self.verified_delivery_calls.add(tool_call_id)
            return
        if result.get("success") is False:
            call["result"] = "tool-error"
            self._retain_terminal(calls, terminal, tool_call_id)
            return
        if result.get("success") is not True:
            raise NativeError("invalid-inquiry-receipt", f"the native {receipt_kind} result has no explicit success evidence")
        receipt = verify_inquiry_receipt(content, self.bridge, receipt_kind)
        call["result"] = "receipt-verified"
        self._retain_terminal(calls, terminal, tool_call_id)
        self.verified_delivery_calls.add(tool_call_id)
        callback = self.on_delivery if receipt_kind == "inquiry-checkpoint" else self.on_answer
        if callback is not None:
            callback(receipt, tool_call_id)

    def _retry_failed_finish(self) -> None:
        # Native schema validation, MCP isError responses and verified signed
        # refusal envelopes delivered inside a successful native wrapper result
        # are ordinary tool failures. Keep the turn alive so the root can correct
        # its arguments; an already accepted receipt can never be withdrawn or
        # replaced.
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
