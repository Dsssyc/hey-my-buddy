"""DSH ACP native-shape projection: tool facts, activity and receipt verification.

This is the harness-side half of the ADR-025 decision 11 integration: it
projects the shapes the installed ``dsh --profile acp`` agent actually speaks —
``session/update`` notifications whose MCP tools appear as
``mcp__<server>__<tool>`` and whose ``kind`` is always ``other`` — into the
shared vocabularies, and it verifies what the role's session tools returned.
Classification only: a native tool name goes through the fixed shared table and
an unknown name stays ``other``; a new tool row the agent reports is retained
exactly like a known one and is never passed off as restricted. Whether any
observed fact is allowed is judged by the blackboard, never here.

The receipt checks are the native driver's independent half of the session-tool
seam (ADR-025 decision 7): the role decides what a receipt means
(:mod:`hey_my_buddy.buddy.roles.worker_services`), this side re-verifies the
signature, the attempt binding and the tool binding through the shared
:mod:`hey_my_buddy.buddy.harnesses.session_receipts` primitives before anything
counts. Trusted root identities enter only through the driver's own
``session/new`` receipts, never from an observed frame.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Callable

from ....errors import BoardError
from ....json_codec import canonical_json, decode_strict_json
from ....protocol.activity import (
    MAX_EVENT_SEQ,
    MAX_SESSION_ID,
    MAX_TOOL_NAME,
)
from ....protocol.tool_evidence import MAX_TOOL_EVENTS, ToolEventEvidence, normalize_tool_event
from .. import session_receipts
from ..session_receipts import refusal_shaped

ADAPTER = "dsh"

#: The ``session/update`` kinds the protocol's public schema names. Any other
#: kind is legal-shaped but unclassified: it is retained as an unknown-event
#: fact for the role observer, never silently dropped and never guessed.
UPDATE_KINDS = frozenset({
    "user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call",
    "tool_call_update", "plan", "available_commands_update", "current_mode_update", "usage_update",
})
#: The kinds that prove the native model turn produced activity. A sent prompt,
#: a handshake or a discovery response is never a model start (ADR-025 decision 6).
MODEL_ACTIVITY_KINDS = frozenset({
    "agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update",
    "plan", "usage_update",
})
_TERMINAL_STATUS = frozenset({"completed", "failed"})
#: Only the most recent terminal call identities per session tool are kept; a
#: much later duplicate terminal result for an evicted identity is ignored.
MAX_RETAINED_INQUIRY_CALLS = 64


class NativeError(Exception):
    """One driver-owned protocol failure; ``code`` is the run's reason code."""

    def __init__(self, code: str, message: str, failure: dict | None = None):
        super().__init__(message)
        self.code = code
        self.failure = failure


decode_json = decode_strict_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# -- the session-tool receipt verification ---------------------------------------
#
# ADR-025 step 5: the finish receipt, the signed tool-refusal envelope and the
# inquiry receipts verify through exactly the shared implementation in
# :mod:`hey_my_buddy.buddy.harnesses.session_receipts` — one signature rule,
# one attempt-identity judgment and one byte budget for every harness,
# ZCode's included. These names run it, pass this driver's registered rule
# bundle, and only convert the shared failure into this protocol's own
# :class:`NativeError`, so the run's stop handling and every caller keep the
# failure type they already catch. ``refusal_shaped`` is the shared dispatch
# function itself.


def _receipt_failure(error: session_receipts.ReceiptError) -> NativeError:
    """The shared verification failure as this protocol's own native error."""
    return NativeError(error.code, str(error))


def verify_finish_receipt(raw: object, configuration: dict,
                          validate_outcome: Callable[[object], str | None]) -> dict:
    """Verify the signed finish receipt through the shared implementation."""
    try:
        return session_receipts.verify_receipt(raw, configuration, validate_outcome)
    except session_receipts.ReceiptError as error:
        raise _receipt_failure(error) from None


def verify_tool_refusal(raw: object, configuration: dict, native_tool: str | None,
                        mounted_tools: tuple[str, ...] | list[str]) -> dict:
    """Verify one signed tool-refusal envelope through the shared implementation.

    This driver's registered rule type-checks the refusal detail and refuses a
    non-string native tool name at the envelope stage.
    """
    try:
        return session_receipts.verify_tool_refusal(raw, configuration, native_tool, mounted_tools,
                                                    rules=session_receipts.TYPED_REFUSAL_DETAIL)
    except session_receipts.ReceiptError as error:
        raise _receipt_failure(error) from None


def verify_inquiry_receipt(raw: object, configuration: dict, kind: str) -> dict:
    """Verify one signed checkpoint or answer receipt through the shared implementation."""
    try:
        return session_receipts.verify_inquiry_receipt(raw, configuration, kind,
                                                       rules=session_receipts.TYPED_REFUSAL_DETAIL)
    except session_receipts.ReceiptError as error:
        raise _receipt_failure(error) from None


# -- the tool-fact projection -----------------------------------------------------


def _bounded_text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 else None


class DshToolFacts:
    """One run's tool-fact projection across its correction sessions.

    The same collector spans the original root session and any later correction
    session, so identities and call counts accumulate over the attempt's shared
    deadline. A native tool name is classified through the fixed shared table —
    DSH reports the ``kind`` ``other`` for every call, so the name alone
    decides — and an unrecognized name, an MCP tool and a brand-new tool row
    all stay ``other`` while still being retained as facts. Trusted root
    identities enter only through :meth:`add_root`, which the driver calls from
    its own ``session/new`` receipts.
    """

    def __init__(self, binding: dict):
        self.evidence = ToolEventEvidence(binding)
        self.roots: list[dict] = []
        self.close_pending = False
        self._names: dict[tuple[str, str], str] = {}

    def add_root(self, session_id: object) -> None:
        """Record one trusted root identity from the driver's own session receipt."""
        session = _bounded_text(session_id)
        identity = {"sessionId": session} if session else {}
        if identity and identity not in self.roots:
            self.roots.append(identity)

    def close_root(self, session_id: object) -> None:
        """Record an actual native turn end before transport drain finishes."""
        session = _bounded_text(session_id)
        if session:
            self.evidence.close_root({"sessionId": session})

    def observe(self, message: dict) -> None:
        """Project the tool facts of one raw notification, judging nothing."""
        if not isinstance(message, dict):
            return
        params = message.get("params")
        if message.get("method") != "session/update" or not isinstance(params, dict):
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind not in ("tool_call", "tool_call_update"):
            return
        identity = {}
        session = _bounded_text(params.get("sessionId"))
        if session:
            identity = {"sessionId": session}
        call_id = _bounded_text(update.get("toolCallId"))
        name = _bounded_text(update.get("title"))
        if kind == "tool_call":
            self._project(identity, call_id, name, "start")
            if isinstance(update.get("status"), str) and update["status"] in _TERMINAL_STATUS:
                # A one-frame call: the opening frame already carries the terminal
                # status, so the same named call gets its end fact from it.
                self._project(identity, call_id, name, "end")
            return
        if update.get("status") not in _TERMINAL_STATUS:
            return  # pending/in_progress: no settled fact exists yet
        remembered = self._names.get((session, call_id)) if session and call_id else None
        self._project(identity, call_id, remembered, "end")

    def _project(self, identity: dict, call_id: str | None, name: str | None,
                 phase: str) -> None:
        if identity and call_id and name:
            key = (identity["sessionId"], call_id)
            if phase == "start":
                if key not in self._names and len(self._names) >= MAX_TOOL_EVENTS:
                    self._incomplete(identity, call_id, name, phase)
                    return
                self._names.setdefault(key, name)
            try:
                event = normalize_tool_event(ADAPTER, {
                    "nativeIdentity": identity, "callId": call_id, "toolName": name, "phase": phase})
            except BoardError:
                self._incomplete(identity, call_id, name, phase)
                return
            if event is not None:
                self.evidence.observe(event)
            return
        self._incomplete(identity, call_id, name, phase)

    def _incomplete(self, identity: dict, call_id: str | None, name: str | None,
                    phase: str | None) -> None:
        self.evidence.observe_incomplete(ADAPTER, {
            "nativeIdentity": dict(identity), "callId": call_id, "toolName": name, "phase": phase})

    @property
    def tool_calls(self) -> int:
        """Observed unique call starts, for the receipt's cumulative count."""
        return self.evidence.tool_calls

    def finish(self, stream_complete: bool, *,
               exclude_calls=None) -> dict:
        """Close the collector and return the receipt's ``toolEvidence`` package.

        ``stream_complete`` is true only when the native stream drained to EOF,
        every root turn ended, and the owned process stopped confirmed. The
        collector keeps its facts verbatim either way; ``exclude_calls`` names
        the verified delivery calls as the collector's own full
        ``(canonical nativeIdentity, callId)`` keys.
        """
        return self.evidence.finish(list(self.roots), stream_complete is True,
                                    exclude_calls=exclude_calls)


# -- the activity projection -------------------------------------------------------


class DshActivity:
    """Bounded, metadata-only phase projection from the ACP update stream.

    It keeps only what :mod:`hey_my_buddy.protocol.activity` allows: a phase,
    timestamps, an event ordinal, the last tool name and non-negative counts.
    Prompts, tool arguments, outputs, credentials and reasoning never enter it;
    the shared :class:`~hey_my_buddy.protocol.activity.ActivityPublisher` owns
    validation, live publication and throttling.
    """

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
        self._seen_calls: set[tuple[str, str]] = set()

    def note_prompt(self) -> None:
        """The prompt was sent: the turn is waiting for the model, nothing more."""
        self.counts["modelTurns"] += 1
        self.phase = "waiting-model"
        self.observed_at = _now()

    def note(self, message: dict, ordinal: int) -> bool:
        """Fold one native message in; return whether the phase changed."""
        self.event_seq = min(max(self.event_seq, ordinal), MAX_EVENT_SEQ)
        self.last_native_activity_at = _now()
        previous = self.phase
        if isinstance(message, dict):
            params = message.get("params")
            if message.get("method") == "session/update" and isinstance(params, dict):
                update = params.get("update")
                kind = update.get("sessionUpdate") if isinstance(update, dict) else None
                if kind in ("agent_message_chunk", "agent_thought_chunk", "plan"):
                    self.phase = "streaming-model"
                elif kind == "tool_call":
                    session, call_id = params.get("sessionId"), update.get("toolCallId")
                    if isinstance(session, str) and isinstance(call_id, str) and (session, call_id) not in self._seen_calls:
                        self._seen_calls.add((session, call_id))
                        self.counts["toolCalls"] += 1
                    self.phase = "tool-running"
                    name = _bounded_text(update.get("title"))
                    if name:
                        self.tool_name = name[:MAX_TOOL_NAME]
                    self.last_tool_activity_at = _now()
                elif kind == "tool_call_update" and isinstance(update.get("status"), str) \
                        and update["status"] in _TERMINAL_STATUS:
                    self.phase = "streaming-model"
                    self.last_tool_activity_at = _now()
        self.observed_at = _now()
        return self.phase != previous

    def payload(self) -> dict:
        return {
            "phase": self.phase if self.phase in (
                "starting", "waiting-model", "streaming-model", "tool-running",
                "waiting-external", "waiting-host", "finishing", "unknown") else "unknown",
            "observedAt": self.observed_at,
            "eventSeq": self.event_seq,
            "nativeSessionId": self.session_id,
            "lastNativeActivityAt": self.last_native_activity_at,
            "lastToolActivityAt": self.last_tool_activity_at,
            "toolName": self.tool_name,
            "waitingReason": self.waiting_reason,
            "counts": dict(self.counts),
        }


# -- the governed root-turn evidence ------------------------------------------------


class RootTurnEvidence:
    """Only signed results inside the admitted root session can count.

    The ACP prompt is one synchronous request: the trusted root identity is the
    driver's own ``session/new`` receipt, the turn is that one prompt, and its
    end is the prompt response's stop reason — never an observed frame. Besides
    the finish tool this tracker owns the inquiry channel's authority: a
    question becomes delivered only through a ``buddy_checkpoint`` result whose
    signed receipt verifies here, and an answer counts only through a verified
    ``buddy_answer_inquiry`` receipt. Child or foreign sessions never match —
    the projection keeps their tool facts, and they contribute no turn evidence.
    """

    def __init__(self, session_id: str, tool_name: str, bridge: dict, *,
                 checkpoint_name: str | None = None, answer_name: str | None = None,
                 on_delivery=None, on_answer=None, validate_outcome=None,
                 mounted_tools: tuple[str, ...] | list[str] = ()):
        self.session_id, self.tool_name, self.bridge = session_id, tool_name, bridge
        self.checkpoint_name, self.answer_name = checkpoint_name, answer_name
        self.on_delivery, self.on_answer = on_delivery, on_answer
        self.validate_outcome = validate_outcome
        #: The bare session tools this run actually mounted — a required,
        #: caller-provided set with no default.
        self.mounted_tools = tuple(mounted_tools)
        self.call_id: str | None = None
        self.call_ordinal: int | None = None
        self.result_ordinal: int | None = None
        self.receipt: dict | None = None
        self.finish_failed = False
        self.stop_reason: str | None = None
        self.settled_ordinal: int | None = None
        #: Root-session calls whose result carried verified delivery evidence.
        self.verified_delivery_calls: set[str] = set()
        self.checkpoint_calls: dict[str, dict] = {}
        self.answer_calls: dict[str, dict] = {}
        self._terminal_checkpoint: deque = deque()
        self._terminal_answer: deque = deque()

    # -- the one observed surface -------------------------------------------------
    def observe(self, message: dict, ordinal: int) -> None:
        """Fold one session/update in; protocol violations raise NativeError."""
        params = message.get("params") if isinstance(message, dict) else None
        if not isinstance(params, dict) or params.get("sessionId") != self.session_id:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        call_id = _bounded_text(update.get("toolCallId"))
        title = _bounded_text(update.get("title"))
        if kind == "tool_call":
            if title == self.tool_name:
                self._schedule_finish(call_id, ordinal)
            elif title == self.checkpoint_name and self.checkpoint_name is not None:
                self._schedule_session_tool(self.checkpoint_calls, call_id)
            elif title == self.answer_name and self.answer_name is not None:
                self._schedule_session_tool(self.answer_calls, call_id)
            return
        if kind != "tool_call_update":
            return
        if self.call_id is not None and call_id == self.call_id:
            self._finish_result(update, ordinal)
        elif call_id in self.checkpoint_calls:
            self._inquiry_result(self.checkpoint_calls, self._terminal_checkpoint,
                                 update, "inquiry-checkpoint", call_id, self.checkpoint_name)
        elif call_id in self.answer_calls:
            self._inquiry_result(self.answer_calls, self._terminal_answer,
                                 update, "inquiry-answer", call_id, self.answer_name)

    # -- settlement (the driver's own prompt receipt, never an observed frame) -----
    def settle(self, ordinal: int, stop_reason: object) -> None:
        """The prompt returned: the one turn end this tracker accepts."""
        if not isinstance(stop_reason, str) or not stop_reason:
            raise NativeError("invalid-protocol", "the native prompt returned no stop reason")
        if self.receipt is None:
            if self.finish_failed:
                raise NativeError("finish-tool-failed", "the native root turn ended without a successful finish tool retry")
            raise NativeError("missing-finish", "the native root turn completed without an accepted finish tool")
        self.stop_reason, self.settled_ordinal = stop_reason, ordinal

    def verified_delivery(self) -> frozenset:
        """The verified delivery calls, as the collector's own full call keys."""
        identity = canonical_json({"sessionId": self.session_id})
        return frozenset((identity, call_id) for call_id in self.verified_delivery_calls)

    def provenance(self, *, close_ordinal: int, event_count: int) -> dict:
        """The ordered native evidence of the accepted finish, as one fact dict.

        Every field is evidence this ACP carrier actually produced: the root's
        finish call, its completed verified result inside the one admitted turn,
        the prompt settlement, and the acknowledged session close that only
        happens after all of them. The ACP prompt turn carries no native turn id,
        so none is claimed; the call identity and the ordinals are the ordered
        proof. The role owns what this record means, the same way it owns the
        outcome.
        """
        if not (self.receipt and self.stop_reason == "end_turn" and self.call_id
                and self.call_ordinal is not None and self.result_ordinal is not None
                and self.settled_ordinal is not None
                and self.call_ordinal < self.result_ordinal <= self.settled_ordinal <= close_ordinal):
            raise NativeError("invalid-provenance",
                              "the native root result lacks ordered tool, turn and session settlement evidence")
        return {
            "version": 1,
            "adapter": ADAPTER, "tool": "buddy_finish_turn", "nativeTool": self.tool_name,
            "turnEnd": "completed", "stopReason": self.stop_reason, "rootSessionMatched": True,
            "nativeSessionId": self.session_id, "toolCallId": self.call_id,
            "receiptId": self.receipt["receiptId"], "receiptVerified": True,
            "callOrdinal": self.call_ordinal, "resultOrdinal": self.result_ordinal,
            "settledOrdinal": self.settled_ordinal, "sessionCloseOrdinal": close_ordinal,
            "nativeEventCount": event_count, "sessionClose": "acknowledged",
        }

    # -- internals -------------------------------------------------------------
    def _schedule_finish(self, call_id: str | None, ordinal: int) -> None:
        if self.receipt is not None:
            raise NativeError("duplicate-finish", "the root scheduled another finish call after its accepted receipt")
        if self.call_id is not None:
            raise NativeError("duplicate-finish", "the root scheduled a second finish call before any result")
        if call_id is None:
            raise NativeError("invalid-provenance", "the root finish call has no identity")
        self.call_id, self.call_ordinal = call_id, ordinal

    def _schedule_session_tool(self, calls: dict, call_id: str | None) -> None:
        if call_id is None or call_id in calls:
            raise NativeError("invalid-provenance", "the root session-tool call has no usable identity")
        calls[call_id] = {"result": None}

    def _finish_result(self, update: dict, ordinal: int) -> None:
        status = update.get("status")
        if status == "failed":
            self._retry_failed_finish()
            return
        if status != "completed":
            return  # pending/in_progress: the call is still running
        if self.receipt is not None:
            raise NativeError("finish-tool-failed", "the native finish result was duplicate")
        content = _content_text(update)
        if refusal_shaped(content):
            # Verified before any retryable ordinary-error path: a forged,
            # tampered or wrong-tool envelope can never slip through as a
            # plain retryable failure.
            verify_tool_refusal(content, self.bridge, self.tool_name, self.mounted_tools)
            self.verified_delivery_calls.add(self.call_id)
            self._retry_failed_finish()
            return
        if content is None:
            raise NativeError("invalid-finish",
                              "the completed finish tool result carried no verifiable receipt content")
        self.receipt = verify_finish_receipt(content, self.bridge, self.validate_outcome)
        self.verified_delivery_calls.add(self.call_id)
        self.result_ordinal = ordinal

    def _retry_failed_finish(self) -> None:
        # Native schema validation, MCP isError responses and verified signed
        # refusal envelopes delivered inside a completed native result are
        # ordinary tool failures. Keep the turn alive so the root can correct
        # its arguments; an already accepted receipt can never be replaced.
        if self.receipt is not None:
            raise NativeError("finish-tool-failed", "the native finish tool failed after an accepted receipt")
        self.finish_failed = True
        self.call_id, self.call_ordinal = None, None

    def _retain_terminal(self, calls: dict, terminal: deque, call_id: str) -> None:
        terminal.append(call_id)
        while len(terminal) > MAX_RETAINED_INQUIRY_CALLS:
            calls.pop(terminal.popleft(), None)

    def _inquiry_result(self, calls: dict, terminal: deque, update: dict,
                        receipt_kind: str, call_id: str, native_name: str | None) -> None:
        """Import one checkpoint/answer tool result under root-turn authority."""
        call = calls[call_id]
        if call["result"] is not None:
            raise NativeError("invalid-provenance", f"the native {receipt_kind} call produced a duplicate terminal result")
        status = update.get("status")
        if status == "failed":
            call["result"] = "tool-error"
            self._retain_terminal(calls, terminal, call_id)
            return
        if status != "completed":
            return
        content = _content_text(update)
        if refusal_shaped(content):
            verify_tool_refusal(content, self.bridge, native_name, self.mounted_tools)
            call["result"] = "tool-refusal"
            self._retain_terminal(calls, terminal, call_id)
            self.verified_delivery_calls.add(call_id)
            return
        if content is None:
            raise NativeError("invalid-inquiry-receipt", f"the native {receipt_kind} result carried no receipt content")
        receipt = verify_inquiry_receipt(content, self.bridge, receipt_kind)
        call["result"] = "receipt-verified"
        self._retain_terminal(calls, terminal, call_id)
        self.verified_delivery_calls.add(call_id)
        callback = self.on_delivery if receipt_kind == "inquiry-checkpoint" else self.on_answer
        if callback is not None:
            callback(receipt, call_id)


def text_blocks(value: object) -> list[str]:
    """The text of the ACP content blocks, without interpreting prose.

    A message-chunk update carries one content block; a tool result carries an
    array of them. Both shapes reduce to their text blocks here — anything else
    (absent, empty, non-text) contributes nothing, and no prose is interpreted.
    """
    blocks = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    texts: list[str] = []
    for block in blocks:
        inner = block.get("content") if isinstance(block, dict) and block.get("type") == "content" else block
        if isinstance(inner, dict) and inner.get("type") == "text" and isinstance(inner.get("text"), str):
            texts.append(inner["text"])
    return texts


def _content_text(update: dict) -> str | None:
    """The one bounded text block of a tool result, without interpreting prose.

    The signed receipt or refusal envelope is the result's text block. Anything
    else — absent, empty, or non-text — is ``None``, and the caller treats it as
    no verifiable content.
    """
    blocks = text_blocks(update.get("content"))
    return blocks[0] if blocks else None


__all__ = [
    "ADAPTER", "MODEL_ACTIVITY_KINDS", "UPDATE_KINDS", "DshActivity", "DshToolFacts",
    "NativeError", "RootTurnEvidence", "refusal_shaped", "text_blocks",
    "verify_finish_receipt", "verify_inquiry_receipt", "verify_tool_refusal",
]
