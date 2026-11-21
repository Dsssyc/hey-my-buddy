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
from pathlib import Path
from ....json_codec import canonical_json
from ....timestamps import utc_now as _now
from ....protocol.activity import (
    MAX_EVENT_SEQ,
    native_tool_name,
)
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event
from ..session_receipts import refusal_shaped, verify_inquiry_receipt, verify_receipt, verify_tool_refusal
from ..snapshot_mount import ObservedSnapshotCalls

HARNESS = "dsh"

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


# -- the tool-fact projection -----------------------------------------------------


def _bounded_text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 else None


def _status_outcome(status: str) -> str:
    return "success" if status == "completed" else "failure"


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

    def __init__(self, binding: dict, *, state_dir: Path, session_tools=()):
        self.evidence = ToolEventEvidence(binding, state_dir=state_dir,
                                          session_service_tools=session_tools)
        self.roots: list[dict] = []
        self.close_pending = False
        #: The mounted snapshot service's qualified tool names, with the
        #: observed native calls the driver later verifies against the ledger.
        self.session_tools = frozenset(session_tools)
        self.snapshot_calls = ObservedSnapshotCalls(names=self.session_tools)
        self._session_starts: set[tuple[str, str]] = set()
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
            self._project(identity, call_id, name, "start", "unknown")
            self._observe_snapshot_start(identity, call_id, name, update)
            if isinstance(update.get("status"), str) and update["status"] in _TERMINAL_STATUS:
                # A one-frame call: the opening frame already carries the terminal
                # status, so the same named call gets its end fact from it.
                self._project(identity, call_id, name, "end", _status_outcome(update["status"]))
                self._observe_snapshot_end(identity, call_id, name, update)
            return
        if update.get("status") not in _TERMINAL_STATUS:
            return  # pending/in_progress: no settled fact exists yet
        remembered = self._names.get((session, call_id)) if session and call_id else None
        self._project(identity, call_id, remembered, "end", _status_outcome(update["status"]))
        self._observe_snapshot_end(identity, call_id, remembered, update)

    def _observe_snapshot_start(self, identity: dict, call_id: str | None,
                                name: str | None, update: dict) -> None:
        if identity and call_id and name in self.session_tools:
            self._session_starts.add((identity["sessionId"], call_id))
            # A mounted snapshot call keeps its native input beside the shared
            # facts; the driver verifies it against the signed ledger once the
            # run's stream has ended.
            self.snapshot_calls.observe(identity, call_id, name,
                                        arguments=update.get("rawInput"), started=True)

    def _observe_snapshot_end(self, identity: dict, call_id: str | None,
                              name: str | None, update: dict) -> None:
        if not identity or not call_id or name not in self.session_tools:
            return
        result = update.get("rawOutput")
        if not isinstance(result, str):
            # A failed MCP page arrives as the raw output behind an
            # ``Error: `` prefix (DSH CLI 0.2.2-rc.1, probes note 2026-11-19).
            result = _content_text(update)
        self.snapshot_calls.observe(identity, call_id, name, result=result, completed=True,
                                    ok=update.get("status") == "completed")

    def _project(self, identity: dict, call_id: str | None, name: str | None,
                 phase: str, outcome: str) -> None:
        if identity and call_id and name:
            key = (identity["sessionId"], call_id)
            if phase == "start":
                self._names.setdefault(key, name)
            event = normalize_tool_event(HARNESS, {
                "nativeIdentity": identity, "callId": call_id, "toolName": name,
                "phase": phase, "outcome": outcome})
            self.evidence.observe(event)
            return
        self._incomplete(identity, call_id, name, phase)

    def _incomplete(self, identity: dict, call_id: str | None, name: str | None,
                    phase: str | None) -> None:
        self.evidence.observe_incomplete(HARNESS, {
            "nativeIdentity": dict(identity), "callId": call_id, "toolName": name, "phase": phase})

    @property
    def tool_calls(self) -> int:
        """Observed unique call starts, for the receipt's cumulative count."""
        return self.evidence.tool_calls

    @property
    def session_tool_calls(self) -> int:
        """Observed starts of mounted snapshot service calls, verbatim."""
        return len(self._session_starts)

    def finish(self, stream_complete: bool, *,
               exclude_calls=None, verified_session_calls=()) -> dict:
        """Close the collector and return the receipt's ``toolEvidence`` package.

        ``stream_complete`` is true only when the native stream drained to EOF,
        every root turn ended, and the owned process stopped confirmed. The
        collector keeps its facts verbatim either way; ``exclude_calls`` names
        the verified delivery calls as the collector's own full
        ``(canonical nativeIdentity, callId)`` keys, and
        ``verified_session_calls`` names the mounted snapshot calls whose signed
        ledger receipts the driver verified in both directions.
        """
        return self.evidence.finish(list(self.roots), stream_complete is True,
                                    exclude_calls=exclude_calls,
                                    verified_session_calls=verified_session_calls)


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
        self.session_id = session_id if isinstance(session_id, str) else None
        self.phase = "starting"
        self.event_seq = 0
        self.observed_at = _now()
        self.last_native_activity_at: str | None = None
        self.last_tool_activity_at: str | None = None
        self.tool_name: str | None = None
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
                        self.tool_name = native_tool_name(name)
                    self.last_tool_activity_at = _now()
                elif kind == "tool_call_update" and isinstance(update.get("status"), str) \
                        and update["status"] in _TERMINAL_STATUS:
                    self.phase = "streaming-model"
                    self.last_tool_activity_at = _now()
        self.observed_at = _now()
        return self.phase != previous

    def payload(self) -> dict:
        return {
            "phase": self.phase,
            "observedAt": self.observed_at,
            "eventSeq": self.event_seq,
            "nativeSessionId": self.session_id,
            "lastNativeActivityAt": self.last_native_activity_at,
            "lastToolActivityAt": self.last_tool_activity_at,
            "toolName": self.tool_name,
            "counts": dict(self.counts),
        }


# -- the micro task root-turn evidence ------------------------------------------------


class RootTurnEvidence:
    """Only signed results inside the admitted root session can count.

    The ACP prompt is one synchronous request: the trusted root identity is the
    driver's own ``session/new`` receipt, the turn is that one prompt, and its
    end is the prompt response's stop reason — never an observed frame. Besides
    the finish tool this tracker owns the inquiry channel's authority: a
    question becomes delivered only through a ``hey_my_buddy_checkpoint`` result whose
    signed receipt verifies here, and an answer counts only through a verified
    ``hey_my_buddy_answer_inquiry`` receipt. The first verified finish result fixes
    the turn's answer for good: every root tool call after it — and a finish
    call repeated before any result — is a bounded retained fact in the
    provenance, never a second result and never a failed turn. Child or
    foreign sessions never match — the projection keeps their tool facts, and
    they contribute no turn evidence.
    """

    def __init__(self, session_id: str, tool_name: str, bridge: dict, *,
                 checkpoint_name: str | None = None, answer_name: str | None = None,
                 validate_outcome, mounted_tools: tuple[str, ...] | list[str],
                 on_delivery=None, on_answer=None, on_finish_receipt=None):
        self.session_id, self.tool_name, self.bridge = session_id, tool_name, bridge
        self.checkpoint_name, self.answer_name = checkpoint_name, answer_name
        self.on_delivery, self.on_answer = on_delivery, on_answer
        self.validate_outcome = validate_outcome
        #: Called once, the moment a finish receipt verifies and becomes this
        #: turn's fixed answer. The driver publishes the acceptance the
        #: session-tools bridge reads, so a repeated finish call is answered
        #: with the truth instead of a second receipt.
        self.on_finish_receipt = on_finish_receipt
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
        #: Root tool calls the root made after its finish was fixed: after the
        #: accepted receipt every later call, and before any result a repeated
        #: finish call. Each is a bounded fact — never a second result.
        self.post_finish_calls: list[dict] = []
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
            elif self.receipt is not None:
                # The accepted receipt is this turn's answer, so any later root
                # tool call is a retained fact, never a second result; the
                # checkpoint and answer tools keep their own verified paths.
                self._post_finish_fact(title, call_id)
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
        return {
            "version": 1,
            "harness": HARNESS, "tool": "hey_my_buddy_finish_turn", "nativeTool": self.tool_name,
            "turnEnd": "completed", "stopReason": self.stop_reason, "rootSessionMatched": True,
            "nativeSessionId": self.session_id, "toolCallId": self.call_id,
            "receiptId": self.receipt["receiptId"], "receiptVerified": True,
            "callOrdinal": self.call_ordinal, "resultOrdinal": self.result_ordinal,
            "settledOrdinal": self.settled_ordinal, "sessionCloseOrdinal": close_ordinal,
            "nativeEventCount": event_count, "sessionClose": "acknowledged",
            "postFinishToolCalls": {"count": len(self.post_finish_calls),
                                    "records": list(self.post_finish_calls)},
        }

    # -- internals -------------------------------------------------------------
    def _schedule_finish(self, call_id: str | None, ordinal: int) -> None:
        if self.receipt is not None or self.call_id is not None:
            # A finish call repeated after the accepted receipt — or before the
            # first call's result — is a retained fact; the first call's
            # verified result stays the turn's only answer.
            self._post_finish_fact(self.tool_name, call_id)
            return
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
        self.receipt = verify_receipt(content, self.bridge, self.validate_outcome)
        self.verified_delivery_calls.add(self.call_id)
        self.result_ordinal = ordinal
        if self.on_finish_receipt is not None:
            self.on_finish_receipt(self.receipt)

    def _retry_failed_finish(self) -> None:
        # Native schema validation, MCP isError responses and verified signed
        # refusal envelopes delivered inside a completed native result are
        # ordinary tool failures. Keep the turn alive so the root can correct
        # its arguments; an already accepted receipt can never be replaced.
        if self.receipt is not None:
            raise NativeError("finish-tool-failed", "the native finish tool failed after an accepted receipt")
        self.finish_failed = True
        self.call_id, self.call_ordinal = None, None

    def _post_finish_fact(self, title: str | None, call_id: str | None) -> None:
        # One bounded identifier pair per later root call; arguments, outputs
        # and raw frames never enter evidence.
        self.post_finish_calls.append({
            "toolName": title if title else "<unknown>",
            "toolCallId": call_id,
        })

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
    "HARNESS", "MODEL_ACTIVITY_KINDS", "UPDATE_KINDS", "DshActivity", "DshToolFacts",
    "NativeError", "RootTurnEvidence", "refusal_shaped", "text_blocks",
    "verify_receipt", "verify_inquiry_receipt", "verify_tool_refusal",
]
