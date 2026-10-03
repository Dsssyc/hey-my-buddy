"""Projection of Codex native tool events into the unified Router evidence.

Per [ADR-021](docs/decisions/021-router-buddy-planes-and-routing-evidence.md) §4
and [ADR-023](docs/decisions/023-harness-integration-principles.md) §3, this is
the Codex half of the shared tool-evidence seam: the controller feeds every App
Server notification frame through :class:`CodexToolEventProjector` before its own
filters, and the projector records typed thread items and raw response items as
normalized facts over the harness's real identities. A raw call item is the
request (start) and only the matching raw output item is its end. The projector
reports facts, categories and counts only; whether an answer may publish is
judged once, by the blackboard.
"""
from __future__ import annotations

from ....errors import BoardError
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event
from ...roles.turn_io import canonical_json

ADAPTER = "codex"

#: Typed thread items that are one native tool call: ``item/started`` is the
#: call's start and ``item/completed`` its end. Conversation items are not tool
#: events, and any other item type is kept as a fact the fixed table classifies
#: as ``other``.
TYPED_TOOL_ITEMS = frozenset({
    "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
    "webSearch", "imageGeneration", "collabAgentToolCall",
})
NON_TOOL_ITEMS = frozenset({"agentMessage", "reasoning", "userMessage"})
#: Raw response items that carry a tool request or its result.
RAW_TOOL_STARTS = frozenset({
    "function_call", "custom_tool_call", "local_shell_call", "web_search_call",
    "image_generation_call", "mcp_tool_call",
})
RAW_TOOL_OUTPUTS = frozenset({
    "function_call_output", "custom_tool_call_output", "local_shell_call_output",
})
#: Raw call shapes that name their operation in the item's ``name`` field; for
#: the others the raw item type is itself the native operation type.
NAMED_RAW_CALLS = frozenset({"function_call", "custom_tool_call"})


def control_binding(control: dict) -> dict | None:
    """The evidence binding from the private Python control file, or ``None``.

    The binding is program identity that never reaches the model prompt or
    schema; a control written before bindings existed projects no evidence
    rather than inventing one.
    """
    try:
        binding = {"adapter": ADAPTER, "taskId": control["taskId"],
                   "attemptId": control["attemptId"], "generation": control["generation"]}
    except (KeyError, TypeError):
        return None
    if (any(not isinstance(binding[key], str) or not binding[key]
            for key in ("taskId", "attemptId"))
            or type(binding["generation"]) is not int or binding["generation"] < 0):
        return None
    return binding


class CodexToolEventProjector:
    """One attempt's Codex tool facts, collected before the controller's filters.

    Root identities enter only from the native thread/turn creation receipts —
    a format correction's root turn included in turn order — never from
    observed events. Every method records facts and counts; none decides what
    is allowed.
    """

    def __init__(self, binding: dict):
        self.evidence = ToolEventEvidence(binding)
        self.roots: list[dict] = []
        self._started: dict[tuple[str, str], tuple[str, str | None]] = {}

    @property
    def tool_calls(self) -> int:
        """Deduplicated observed starts; the cumulative tool budget uses this."""
        return self.evidence.tool_calls

    def observe_root(self, session_id: str, turn_id: str) -> None:
        """Record one root turn identity exactly as its native receipt carried it."""
        root = {"sessionId": session_id, "turnId": turn_id}
        if root not in self.roots:
            self.roots.append(root)

    def finish(self, stream_complete: bool) -> dict:
        """Close the stream and return the ``toolEvidence`` package.

        ``stream_complete`` reports that every observed root turn completed and
        the controller held the stream to its end — drained to EOF on the
        no-tool branch, completed turns on the review branch.
        """
        return self.evidence.finish(list(self.roots), stream_complete)

    def observe_notification(self, message: dict) -> None:
        """Project the tool facts of one notification frame, before any filtering."""
        if not isinstance(message, dict):
            return
        method = message.get("method")
        params = message.get("params")
        if method == "turn/completed" and isinstance(params, dict):
            turn = params.get("turn") or {}
            identity = self._identity({**params, "turnId": params.get("turnId") or turn.get("id")})
            if identity in self.roots:
                self.evidence.close_root(identity)
            return
        if method in ("item/started", "item/updated", "item/completed"):
            self._typed(method, params)
        elif isinstance(method, str) and method.startswith("rawResponseItem/"):
            self._raw(params)

    def _typed(self, method: str, params) -> None:
        item = params.get("item") if isinstance(params, dict) else None
        kind = item.get("type") if isinstance(item, dict) else None
        if not isinstance(kind, str) or kind in NON_TOOL_ITEMS:
            return
        if method == "item/updated":
            # A progress frame carries neither a start nor an end; the stream
            # stays incomplete, so the call cannot pass as either.
            self._incomplete(params, tool_name=kind, phase=None)
            return
        self._observe(params, phase="start" if method == "item/started" else "end",
                      tool_name=kind, call_id=item.get("id") or item.get("call_id"))

    def _raw(self, params) -> None:
        item = params.get("item") if isinstance(params, dict) else None
        raw_type = item.get("type") if isinstance(item, dict) else None
        if not isinstance(raw_type, str) or raw_type in NON_TOOL_ITEMS or raw_type == "message":
            return
        if raw_type in RAW_TOOL_STARTS:
            name = item.get("name") if raw_type in NAMED_RAW_CALLS else None
            if raw_type in NAMED_RAW_CALLS and not (isinstance(name, str) and name):
                # A call envelope without its operation name names no operation.
                self._incomplete(params, tool_name=None, phase="start")
                return
            self._observe(params, phase="start",
                          tool_name=name if name is not None else raw_type,
                          call_id=item.get("call_id") or item.get("id"),
                          native_type="mcpToolCall" if item.get("namespace") not in (None, "functions") else None)
        elif raw_type in RAW_TOOL_OUTPUTS:
            call_id = item.get("call_id") or item.get("id")
            identity = self._identity(params)
            correlated = None
            if identity and isinstance(call_id, str) and call_id:
                correlated = self._started.get((canonical_json(identity), call_id))
            # The end fact repeats the correlated start's operation; without a
            # proven association only the frame's own type is reported.
            self._observe(params, phase="end", tool_name=correlated[0] if correlated else raw_type,
                          native_type=correlated[1] if correlated else None, call_id=call_id)
        else:
            # An unrecognized raw item shape has no provable phase.
            self._incomplete(params, tool_name=None, phase=None)

    def _observe(self, params, *, phase: str, tool_name, call_id, native_type=None) -> None:
        identity = self._identity(params)
        fact = {"nativeIdentity": identity or {}, "callId": call_id,
                "toolName": tool_name, "phase": phase, **({"type": native_type} if native_type else {})}
        if (identity is None or "sessionId" not in identity or "turnId" not in identity
                or not isinstance(call_id, str) or not call_id or len(call_id) > 512):
            # A frame without a provable session/turn pair cannot be bound to
            # this attempt's root turns, so it is retained as incomplete.
            self.evidence.observe_incomplete(ADAPTER, fact)
            return
        try:
            event = normalize_tool_event(ADAPTER, fact)
        except BoardError:
            self.evidence.observe_incomplete(ADAPTER, fact)
            return
        self.evidence.observe(event)
        if phase == "start":
            self._started[(canonical_json(identity), call_id)] = (tool_name, native_type)

    def _incomplete(self, params, *, tool_name, phase) -> None:
        self.evidence.observe_incomplete(ADAPTER, {"nativeIdentity": self._identity(params) or {},
                                                   "callId": None, "toolName": tool_name, "phase": phase})

    @staticmethod
    def _identity(params) -> dict | None:
        """The frame's real session/turn pair, or ``None`` when both are absent.

        A frame missing either identity cannot be bound to this attempt's root
        turns and stays incomplete; identities are never fabricated.
        """
        if not isinstance(params, dict):
            return None
        identity = {}
        session = params.get("threadId")
        if isinstance(session, str) and session:
            identity["sessionId"] = session
        turn = params.get("turnId")
        if not isinstance(turn, str) or not turn:
            native_turn = params.get("turn")
            turn = native_turn.get("id") if isinstance(native_turn, dict) else None
        if isinstance(turn, str) and turn:
            identity["turnId"] = turn
        return identity or None


__all__ = [
    "ADAPTER", "CodexToolEventProjector", "NAMED_RAW_CALLS", "NON_TOOL_ITEMS",
    "RAW_TOOL_OUTPUTS", "RAW_TOOL_STARTS", "TYPED_TOOL_ITEMS", "control_binding",
]
