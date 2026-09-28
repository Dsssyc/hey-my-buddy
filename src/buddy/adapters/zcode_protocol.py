"""Bounded NDJSON transport and root-turn evidence for ZCode's native app server."""
from __future__ import annotations

from .. import locking
import hashlib
import hmac
import json
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

from .turn_io import MAX_OUTCOME_BYTES, canonical_json, validate_outcome
from ..activity import MAX_SESSION_ID, MAX_TOOL_NAME, MAX_WAITING_REASON, PHASES

MAX_MESSAGE_BYTES = 8 * 1024 * 1024

#: Shared inquiry bounds, identical to the board and the DSH bridge. The MCP
#: tools, the controller's verification and the observation bridge all enforce
#: the same budget so a bounded value can never be rejected after mutation.
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES = 32
MAX_INQUIRY_ID_BYTES = 128

#: The only inquiry-journal record format this source writes and replays. Every
#: reader requires exactly this version and the record's attempt identity, so a
#: malformed, foreign or unbound line is ignored instead of merged.
INQUIRY_JOURNAL_VERSION = 1

#: Bounded retention for completed inquiry tool calls inside one native turn.
#: Only the most recent terminal call identities per tool are kept (to reject an
#: immediate duplicate terminal result); a verified receipt is handed to its
#: callback instead of being retained. Pending scheduled calls are never evicted
#: and no cumulative call count ever ends a long ``timeoutSeconds: 0`` turn.
MAX_RETAINED_INQUIRY_CALLS = 64


class NativeError(Exception):
    def __init__(self, code: str, message: str, failure: dict | None = None):
        super().__init__(message)
        self.code = code
        self.failure = failure


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


def serialized_footprint(text: str) -> int:
    """The byte length ``text`` occupies inside a canonical JSON document.

    The raw UTF-8 length cannot bound a signed envelope's wire size: JSON
    escaping costs six bytes per C0 control character (``\\u0001``), two per
    quote or backslash, while non-ASCII text stays literal under
    ``ensure_ascii=False``. Every budget that has to survive signing plus the
    native wrapper's framing is computed on this footprint, never the raw
    length.
    """
    return len(canonical_json(text).encode()) - 2


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

#: Honest bounds of the signed inquiry receipts the MCP tools return. A
#: checkpoint receipt may carry every queued question, so its budget covers
#: ``MAX_INQUIRIES`` x ``MAX_QUESTION_BYTES`` plus framing.
MAX_INQUIRY_RECEIPT_BYTES = 200 * 1024

#: The bare session-tool names the private MCP exposes inside one native root
#: turn. The runner mounts them as ``mcp__<server>__<bare name>``, so a refusal
#: envelope is bound to its tool by requiring the tracked native tool name to
#: end with the envelope's bare name.
SESSION_TOOLS = ("buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn")

#: The closed set of reasons a signed tool-refusal envelope may carry. Every
#: member is an expected, bounded refusal our own MCP mints for a correctable
#: caller mistake or an outstanding Host condition; anything else is not a
#: refusal this controller will recover from.
TOOL_REFUSAL_REASONS = ("invalid-arguments", "attention-outstanding", "inquiry-pending",
                        "unknown-inquiry", "inquiry-state", "inquiry-channel-absent")

#: A refusal detail is the same bounded correction text the plain MCP errors
#: carried (validator output, the pending-inquiry refusal, the attention note),
#: so it inherits the outcome byte bound; the envelope adds only fixed framing.
MAX_TOOL_REFUSAL_DETAIL_BYTES = MAX_OUTCOME_BYTES
MAX_TOOL_REFUSAL_BYTES = MAX_OUTCOME_BYTES + 4096

#: The installed native wrapper frames an MCP ``isError`` text with one bounded
#: plain header line (observed: ``MCP tool returned an error:``) before the
#: content. The controller tolerates exactly that much transport framing when
#: dispatching to refusal verification; the framing never enters the signature,
#: and only the full HMAC over the attempt, input and tool binding decides.
MAX_TOOL_REFUSAL_PREFIX_BYTES = 256


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
    """Verify the signed finish receipt; malformed and forged failures stay distinct.

    Every stage keeps the fatal ``invalid-finish`` code — only a fully verified
    receipt is a success — but the bounded message distinguishes an unusable
    payload (no bounded JSON, wrong object shape) from a signature, attempt-
    identity or outcome failure, without ever quoting the raw content.
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


def verify_tool_refusal(raw: object, configuration: dict, native_tool: str) -> dict:
    """Verify one signed tool-refusal envelope against this attempt, input and tool.

    The private MCP mints these for expected argument, attention and inquiry
    refusals because the native wrapper surfaces an MCP ``isError`` response as
    a *successful* tool result framed with a plain header; the signature binds
    the attempt identity, the turn input and the exact session tool, so a
    tampered, cross-attempt or cross-tool envelope fails here fatally instead
    of becoming a recoverable refusal. A verified refusal only ever means
    "correct the call and retry in this same native turn"; it can never carry
    an outcome or mutate state.
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
        if (not isinstance(envelope["tool"], str) or envelope["tool"] not in SESSION_TOOLS
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


def read_shared_snapshot(path, max_bytes: int) -> bytes | None:
    """One bounded, shared-locked read of the inquiry journal file.

    This is the reader side of the journal's cross-process barrier (POSIX flock,
    the same primitive the service uses for its lifetime locks): while the
    controller writer holds the exclusive side through its append/fsync/commit
    transaction, this read waits, so a reader can never observe a record that
    the writer has not fully committed — neither an in-flight append nor the
    remains of a failed one. Returns ``None`` when the file cannot be opened and
    ``b""`` when it exceeds its byte bound.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        locking.lock(fd, shared=True)
        if os.fstat(fd).st_size > max_bytes:
            return b""
        chunks = bytearray()
        while len(chunks) <= max_bytes:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.extend(block)
        return bytes(chunks[:max_bytes + 1])
    finally:
        os.close(fd)  # closing releases the shared lock


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
                 on_answer: Callable[[dict, str], None] | None = None):
        self.session_id, self.input_id, self.tool_name, self.bridge = session_id, input_id, tool_name, bridge
        self.checkpoint_name, self.answer_name = checkpoint_name, answer_name
        self.on_delivery, self.on_answer = on_delivery, on_answer
        self.turn_id: str | None = None
        self.call_id: str | None = None
        self.receipt: dict | None = None
        self.finish_failed = False
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
                    verify_tool_refusal(content, self.bridge, self.tool_name)
                    self._retry_failed_finish()
                    return
                if result.get("success") is False:
                    self._retry_failed_finish()
                    return
                if result.get("success") is not True:
                    raise NativeError("finish-tool-failed", "the native finish result has no explicit success evidence")
                self.receipt = verify_receipt(content, self.bridge)
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
            verify_tool_refusal(content, self.bridge, native_name)
            call["result"] = "tool-refusal"
            self._retain_terminal(calls, terminal, tool_call_id)
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
