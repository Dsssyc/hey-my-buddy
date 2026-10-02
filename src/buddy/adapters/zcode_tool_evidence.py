"""ZCode projection of native tool frames into the shared tool evidence.

The no-tool controller routes every notification through this module *before*
its own rejection and session/turn filtering run, so a refused, foreign, child
or malformed native call still reaches the shared
:class:`~buddy.tool_evidence.ToolEventEvidence` collector as a fact. The
projection is classification only: a native tool name goes through the fixed
shared table, a ``tool.updated`` envelope is never itself an operation type,
the tool name comes only from a ``scheduled`` record — never from a title or
result body — and a missing identifier stays missing. Trusted root identities
enter only through the controller's own session/turn receipts, never inferred
from the observed events. Nothing here judges categories; the blackboard does.
"""
from __future__ import annotations

from ..tool_evidence import ToolEventEvidence, normalize_tool_event

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
        if not isinstance(message, dict) or message.get("method") != "session/event":
            return
        params = message.get("params")
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
        phase = ("start" if isinstance(kind, str) and kind in _START_KINDS
                 else "end" if isinstance(kind, str) and kind in _END_KINDS else None)
        call_id = payload.get("toolCallId")
        call_id = call_id if isinstance(call_id, str) and call_id else None
        identity = _identity_of(params)
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
                self._names.setdefault((identity["sessionId"], identity["turnId"], call_id), name)
        elif phase == "end" and identity is not None and call_id is not None:
            key = (identity["sessionId"], identity["turnId"], call_id)
            name = self._names.get(key)
            self._ended.add(key)
        if identity is not None and phase is not None and call_id is not None and name is not None:
            # The envelope type is not an operation type and is never passed as
            # one; the shared table classifies the scheduled tool name alone.
            self.evidence.observe(normalize_tool_event(ADAPTER, {
                "nativeIdentity": dict(identity), "callId": call_id, "toolName": name, "phase": phase}))
            return
        self._incomplete(params, identity, call_id, phase, name)

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

    def finish(self, stream_complete: bool) -> dict:
        """Close the collector and return the receipt's ``toolEvidence`` package.

        ``stream_complete`` is true only when the real native stream drained to
        EOF, every root session closed with an acknowledged close, and the owned
        process stopped; the collector keeps its facts verbatim either way.
        """
        return self.evidence.finish(list(self.roots), stream_complete is True)


__all__ = ["ADAPTER", "ZcodeToolFacts"]
