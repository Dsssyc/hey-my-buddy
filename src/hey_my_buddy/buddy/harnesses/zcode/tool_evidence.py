"""ZCode projection of native tool frames into the shared tool evidence.

The no-tool controller routes every notification through this module *before*
its own rejection and session/turn filtering run, so a refused, foreign, child
or malformed native call still reaches the shared
:class:`~hey_my_buddy.protocol.tool_evidence.ToolEventEvidence` collector as a fact. The
projection is classification only: a native tool name goes through the fixed
shared table, a ``tool.updated`` envelope is never itself an operation type,
the tool name comes only from a ``scheduled`` record — never from a title or
result body — and a missing identifier stays missing. Trusted root identities
enter only through the controller's own session/turn receipts, never inferred
from the observed events. Nothing here judges categories; the blackboard does.
"""
from __future__ import annotations

from collections.abc import Iterable

from ....protocol.tool_evidence import MAX_TOOL_EVENTS, ToolEventEvidence, normalize_tool_event

ADAPTER = "zcode"

#: The real session/event ``tool.updated`` payload kinds: ``scheduled`` opens a
#: call and ``result``/``error`` closes it. Any other kind is an unknown frame
#: that is retained as an incomplete fact, never guessed into a phase.
_START_KINDS = frozenset({"scheduled"})
_END_KINDS = frozenset({"result", "error"})


def _identity_of(params: dict) -> dict | None:
    """The frame's own session/turn identity, or ``None`` when incomplete.

    A canonical ZCode frame carries both its session and its turn; a missing
    one leaves the fact unidentified, and no identity field is ever invented.
    """
    identity = {}
    for key in ("sessionId", "turnId"):
        value = params.get(key)
        if isinstance(value, str) and value:
            identity[key] = value
    return identity if len(identity) == 2 else None


class ZcodeToolFacts:
    """One no-tool call's tool-fact projection across its correction sessions.

    The same collector spans the original root session and the one root session
    a format correction creates after the first is closed, so identities and
    call counts accumulate over the attempt's shared deadline. ``violation``
    records that the controller refused a tool interaction whose shape the
    projection cannot name, keeping the receipt's call count at least one.
    """

    def __init__(self, binding: dict):
        self.evidence = ToolEventEvidence(binding)
        self.roots: list[dict] = []
        self.close_pending = False
        self.violation = False
        self._names: dict[tuple[str, str, str], str] = {}
        self._ended = set()
        self._closed = set()
        self._canonical_starts = set()
        self._metadata_starts = set()
        self._metadata_outcomes = {}
        self._outcomes = {}
        self._batched = set()

    # -- controller receipts ---------------------------------------------------
    def add_root(self, session_id: str, turn_id: str) -> None:
        """Record one trusted root identity from the controller's own receipts.

        Only a verified root ``session/create`` together with its canonical
        ``turn.started`` reaches this; observed tool frames never do. A value
        outside the shared identity bounds is refused here so ``finish`` can
        never fail on a root the controller itself admitted.
        """
        identity = {}
        for key, value in (("sessionId", session_id), ("turnId", turn_id)):
            if isinstance(value, str) and 0 < len(value) <= 512:
                identity[key] = value
        if len(identity) == 2 and identity not in self.roots:
            self.roots.append(identity)

    def observe_with(self, observer):
        """Wrap a controller observer so facts are projected before its checks."""
        def wrapped(message: dict, ordinal: int) -> None:
            self.observe(message)
            observer(message, ordinal)
        return wrapped

    # -- projection ------------------------------------------------------------
    def observe(self, message: dict) -> None:
        """Project the tool facts of one raw notification, judging nothing."""
        if not isinstance(message, dict):
            return
        params = message.get("params")
        method = message.get("method")
        if method in ("computer-use/operation-event", "v4/telemetry/event"):
            if isinstance(params, dict):
                self._metadata_tool(method, params)
            return
        if method != "session/event":
            return
        if isinstance(params, dict) and params.get("type") == "turn.completed":
            identity = _identity_of(params)
            if identity in self.roots:
                self.evidence.close_root(identity)
                self._closed.add((identity['sessionId'], identity['turnId']))
            return
        if not isinstance(params, dict) or params.get("type") != "tool.updated":
            return
        payload = params.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        kind = payload.get("kind")
        identity = _identity_of(params)
        if kind == "batch":
            self._batch(params, identity, payload)
            return
        phase = ("start" if isinstance(kind, str) and kind in _START_KINDS
                 else "end" if isinstance(kind, str) and kind in _END_KINDS else None)
        call_id = payload.get("toolCallId")
        call_id = call_id if isinstance(call_id, str) and call_id else None
        name = None
        if kind in ("started", "progress"):
            key = (identity["sessionId"], identity["turnId"], call_id) if identity and call_id else None
            known = self._names.get(key)
            raw = payload.get("toolName")
            if (known is not None and key not in self._ended and key[:2] not in self._closed
                    and (raw is None or raw == known)):
                if kind == "progress":
                    return  # The known call's start/end remain its evidence.
                phase, name = "start", known
            else:
                self._incomplete(params, identity, call_id, None, None)
                return
        if phase == "start":
            # Only a scheduled record names the call; a title or a result body
            # is never promoted to a tool name.
            raw = payload.get("toolName")
            name = name or (raw if isinstance(raw, str) and raw else None)
            if identity is not None and call_id is not None and name is not None:
                key = (identity["sessionId"], identity["turnId"], call_id)
                if key not in self._names and len(self._names) >= MAX_TOOL_EVENTS:
                    self._incomplete(params, identity, call_id, phase, name)
                    return
                self._names.setdefault(key, name)
                if kind == "scheduled":
                    self._canonical_starts.add(key)
        elif phase == "end" and identity is not None and call_id is not None:
            key = (identity["sessionId"], identity["turnId"], call_id)
            name = self._names.get(key)
            self._ended.add(key)
            result = payload.get("result")
            outcome = False if kind == "error" else result.get("success") if isinstance(result, dict) else None
            outcome = outcome if type(outcome) is bool else None
            if key in self._outcomes and self._outcomes[key] is not outcome:
                self._incomplete(params, identity, call_id, phase, name)
            self._outcomes.setdefault(key, outcome)
            if key in self._metadata_outcomes and self._metadata_outcomes[key] is not self._outcomes[key]:
                self._incomplete(params, identity, call_id, phase, name)
        if identity is not None and phase is not None and call_id is not None and name is not None:
            # The envelope type is not an operation type and is never passed as
            # one; the shared table classifies the scheduled tool name alone.
            self.evidence.observe(normalize_tool_event(ADAPTER, {
                "nativeIdentity": dict(identity), "callId": call_id, "toolName": name, "phase": phase}))
            return
        self._incomplete(params, identity, call_id, phase, name)

    def _metadata_tool(self, method: str, params: dict) -> None:
        """Pair native projections with canonical facts, never manufacture an end.

        The public emitter sends operation, telemetry, then canonical frames.
        A fully identified scheduled projection is a real start; its canonical
        counterpart is still mandatory. Optional turn IDs only check a unique
        already named native call and are never copied into a canonical frame.
        """
        kind = params.get("kind")
        if method == "computer-use/operation-event":
            if kind not in ("tool-scheduled", "tool-started"):
                return
            phase = "scheduled" if kind == "tool-scheduled" else "started"
        else:
            if kind != "tool.lifecycle":
                return
            phase = params.get("phase")
        identity = _identity_of(params)
        call_id = params.get("toolCallId")
        call_id = call_id if isinstance(call_id, str) and 0 < len(call_id) <= 512 else None
        raw = params.get("toolName")
        name = raw if isinstance(raw, str) and 0 < len(raw) <= 512 else None
        key = (identity["sessionId"], identity["turnId"], call_id) if identity and call_id else None
        if (key is None and params.get("turnId") is None and call_id
                and isinstance(params.get("sessionId"), str)):
            matches = [known for known in self._names
                       if known[0] == params["sessionId"] and known[2] == call_id]
            key = matches[0] if len(matches) == 1 else None
        if any(params.get(field) not in (None, False, "") for field in (
                "parentToolCallId", "childToolCallId", "childSessionId", "agentId", "background")):
            self._incomplete(params, identity, call_id, None, name)
        if (phase not in ("scheduled", "started", "progress", "completed", "failed")
                or key is None or key[:2] in self._closed or key in self._ended
                or raw is not None and name is None):
            self._incomplete(params, identity, call_id, None, name)
            return
        if phase == "scheduled":
            known = self._names.get(key)
            if (name is None or known is not None and name != known
                    or known is None and (identity is None or len(self._names) >= MAX_TOOL_EVENTS)):
                self._incomplete(params, identity, call_id, "start", name)
                return
            self._names.setdefault(key, name)
            self._metadata_starts.add(key)
            if identity is not None:
                self.evidence.observe(normalize_tool_event(ADAPTER, {
                    "nativeIdentity": identity, "callId": call_id, "toolName": name, "phase": "start"}))
            return
        known = self._names.get(key)
        if known is None or name is not None and name != known:
            self._incomplete(params, identity, call_id, None, name)
            return
        if phase in ("completed", "failed"):
            outcome = phase == "completed"
            if key in self._metadata_outcomes and self._metadata_outcomes[key] is not outcome:
                self._incomplete(params, identity, call_id, None, name)
            self._metadata_outcomes[key] = outcome

    def _batch(self, params: dict, identity: dict | None, payload: dict) -> None:
        """Validate an aggregate acknowledgment without creating tool events."""
        ids = payload.get("toolCallIds")
        success, error = payload.get("successCount"), payload.get("errorCount")
        valid = (identity in self.roots and identity is not None
                 and (identity["sessionId"], identity["turnId"]) not in self._closed
                 and isinstance(ids, list) and 0 < len(ids) <= MAX_TOOL_EVENTS
                 and all(isinstance(value, str) and 0 < len(value) <= 512 for value in ids)
                 and len(set(ids)) == len(ids)
                 and type(success) is int and success >= 0 and type(error) is int and error >= 0)
        keys = [(identity["sessionId"], identity["turnId"], value) for value in ids] if valid else []
        if (not valid or any(key not in self._canonical_starts or key not in self._ended
                             or key in self._batched or type(self._outcomes.get(key)) is not bool for key in keys)
                or success != sum(self._outcomes[key] is True for key in keys)
                or error != sum(self._outcomes[key] is False for key in keys)):
            self._incomplete(params, identity, None, None, None)
            return
        self._batched.update(keys)

    def _incomplete(self, params: dict, identity: dict | None, call_id: str | None,
                    phase: str | None, name: str | None) -> None:
        bounded = {}
        for key in ("sessionId", "turnId"):
            value = params.get(key)
            if isinstance(value, str) and value:
                bounded[key] = value
        self.evidence.observe_incomplete(ADAPTER, {
            "nativeIdentity": bounded, "callId": call_id, "toolName": name, "phase": phase})

    # -- receipt ---------------------------------------------------------------
    @property
    def tool_calls(self) -> int:
        """Observed unique call starts, for the receipt's cumulative count."""
        return self.evidence.tool_calls

    def finish(self, stream_complete: bool, *,
               exclude_calls: Iterable[tuple[str, str]] | None = None) -> dict:
        """Close the collector and return the receipt's ``toolEvidence`` package.

        ``stream_complete`` is true only when the real native stream drained to
        EOF, every root session closed with an acknowledged close, and the owned
        process stopped; the collector keeps its facts verbatim either way.
        ``exclude_calls`` names the verified delivery calls as the collector's
        own full ``(canonical_json(native_identity), callId)`` keys: the shared collector
        removes exactly those calls' events and counts from its complete call
        table, while every unverified, foreign or conflicting fact the
        projection observed stays.
        """
        for key in self._metadata_starts - self._canonical_starts:
            self._incomplete({"sessionId": key[0], "turnId": key[1]},
                             {"sessionId": key[0], "turnId": key[1]}, key[2], None, self._names.get(key))
        return self.evidence.finish(list(self.roots), stream_complete is True,
                                    exclude_calls=exclude_calls)


__all__ = ["ADAPTER", "ZcodeToolFacts"]
