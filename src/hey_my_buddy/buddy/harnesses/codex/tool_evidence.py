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

from pathlib import Path

from ....errors import BoardError
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event
from ...roles.turn_io import canonical_json

HARNESS = "codex"

#: Codex 0.162.1's generated ThreadItem and ResponseItem definitions. Both the
#: projector and the run's marker classifier use these tables. Tool definitions
#: are not invocations; none of the concrete variants contains nested calls.
#: The opaque ResponseItem fallback cannot rule out a hidden call and therefore
#: follows the same incomplete-evidence path as a future, unlisted variant.
THREAD_ITEM_KINDS = {
    "userMessage": "conversation",  # UserInput content.
    "agentMessage": "conversation",  # Agent text and delivery metadata.
    "reasoning": "conversation",  # Reasoning content and summaries.
    "hookPrompt": "conversation",  # HookPromptFragment contains hookRunId and prompt text.
    "functionCallOutput": "output",  # Named FunctionCallOutputBody is a tool result, not a request.
    "plan": "conversation",  # Proposed plan text; the completed text is authoritative.
    "subAgentActivity": "conversation",  # Agent identity and started/interacted/interrupted/completed status.
    "commandExecution": "call",  # Command and its execution status.
    "fileChange": "call",  # File updates and patch status.
    "mcpToolCall": "call",  # MCP server, tool, arguments and result.
    "dynamicToolCall": "call",  # Dynamic tool, arguments and result.
    "collabAgentToolCall": "call",  # Collaboration operation and receiving agents.
    "webSearch": "call",  # Search query and action.
    "imageView": "call",  # Viewing the image at the supplied path.
    "sleep": "call",  # Display item emitted by the interruptible clock.sleep tool.
    "imageGeneration": "call",  # Image generation status and result.
    "enteredReviewMode": "conversation",  # Review-mode entry with review text.
    "exitedReviewMode": "conversation",  # Review-mode exit with review text.
    "contextCompaction": "conversation",  # Context compaction marker with only an id.
}
RAW_ITEM_KINDS = {
    "message": "conversation",  # Role and message content.
    "reasoning": "conversation",  # Reasoning summaries and content.
    "additional_tools": "conversation",  # Additional tool definitions, with no invocation or call id.
    "agent_message": "conversation",  # Agent-to-agent text/encrypted content and author/recipient identities.
    "function_call": "call",  # Named function request and arguments.
    "custom_tool_call": "call",  # Named custom tool request and input.
    "local_shell_call": "call",  # Shell execution action.
    "web_search_call": "call",  # Web search action.
    "image_generation_call": "call",  # Image generation request.
    "mcp_tool_call": "call",  # Existing native MCP call carrier.
    "tool_search_call": "call",  # Tool discovery request with arguments, execution and call identity.
    "function_call_output": "output",  # Function result correlated by call id.
    "custom_tool_call_output": "output",  # Custom tool result correlated by call id.
    "local_shell_call_output": "output",  # Existing native shell result carrier.
    "tool_search_output": "output",  # Tool discovery result with status, tools and call identity.
    "compaction": "conversation",  # Encrypted context compaction content.
    "configuration_update": "conversation",  # Durable backend input control carrying reasoning settings.
    "compaction_trigger": "conversation",  # Context compaction trigger marker.
    "context_compaction": "conversation",  # Context compaction marker and optional encrypted content.
    "other": "opaque",  # Catch-all with no payload definition; possible tool content stays unverified.
}
NON_TOOL_ITEMS = frozenset(kind for kind, disposition in THREAD_ITEM_KINDS.items() if disposition == "conversation")
TYPED_TOOL_ITEMS = frozenset(kind for kind, disposition in THREAD_ITEM_KINDS.items() if disposition == "call")
TYPED_TOOL_OUTPUTS = frozenset(kind for kind, disposition in THREAD_ITEM_KINDS.items() if disposition == "output")
RAW_NON_TOOL_ITEMS = frozenset(kind for kind, disposition in RAW_ITEM_KINDS.items() if disposition == "conversation")
RAW_TOOL_STARTS = frozenset(kind for kind, disposition in RAW_ITEM_KINDS.items() if disposition == "call")
RAW_TOOL_OUTPUTS = frozenset(kind for kind, disposition in RAW_ITEM_KINDS.items() if disposition == "output")
#: Raw call shapes that name their operation in the item's ``name`` field; for
#: the others the raw item type is itself the native operation type.
NAMED_RAW_CALLS = frozenset({"function_call", "custom_tool_call"})


class CodexToolEventProjector:
    """One attempt's Codex tool facts, collected before the controller's filters.

    Root identities enter only from the native thread/turn creation receipts —
    a format correction's root turn included in turn order — never from
    observed events. Every method records facts and counts; none decides what
    is allowed.
    """

    def __init__(self, binding: dict, *, state_dir: Path):
        self.evidence = ToolEventEvidence(binding, state_dir=state_dir)
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
            identity = native_identity({**params, "turnId": params.get("turnId") or turn.get("id")})
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
        if not isinstance(kind, str):
            self._incomplete(params, tool_name=None, phase=None)
            return
        if kind in NON_TOOL_ITEMS:
            return
        if kind in TYPED_TOOL_OUTPUTS:
            # A rendered output starts no call. Its completed item is only an
            # end fact, joined to a request by the native id, never by its text.
            if method == "item/completed":
                self._output(params, item, kind)
            elif method == "item/updated":
                self._incomplete(params, tool_name=kind, phase=None)
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
        if not isinstance(raw_type, str):
            self._incomplete(params, tool_name=None, phase=None)
            return
        if raw_type in RAW_NON_TOOL_ITEMS:
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
            self._output(params, item, raw_type)
        else:
            # An unrecognized raw item shape has no provable phase.
            self._incomplete(params, tool_name=None, phase=None)

    def _output(self, params, item: dict, kind: str) -> None:
        call_id = item.get("call_id") or item.get("id")
        identity = native_identity(params)
        correlated = None
        if identity and isinstance(call_id, str) and call_id:
            correlated = self._started.get((canonical_json(identity), call_id))
        # Without a proven association only the output's own type is reported.
        self._observe(params, phase="end", tool_name=correlated[0] if correlated else kind,
                      native_type=correlated[1] if correlated else None, call_id=call_id)

    def _observe(self, params, *, phase: str, tool_name, call_id, native_type=None) -> None:
        identity = native_identity(params)
        fact = {"nativeIdentity": identity or {}, "callId": call_id,
                "toolName": tool_name, "phase": phase, **({"type": native_type} if native_type else {})}
        if (identity is None or "sessionId" not in identity or "turnId" not in identity
                or not isinstance(call_id, str) or not call_id or len(call_id) > 512):
            # A frame without a provable session/turn pair cannot be bound to
            # this attempt's root turns, so it is retained as incomplete.
            self.evidence.observe_incomplete(HARNESS, fact)
            return
        try:
            event = normalize_tool_event(HARNESS, fact)
        except BoardError:
            self.evidence.observe_incomplete(HARNESS, fact)
            return
        self.evidence.observe(event)
        if phase == "start":
            self._started[(canonical_json(identity), call_id)] = (tool_name, native_type)

    def _incomplete(self, params, *, tool_name, phase) -> None:
        self.evidence.observe_incomplete(HARNESS, {"nativeIdentity": native_identity(params) or {},
                                                   "callId": None, "toolName": tool_name, "phase": phase})

def native_identity(params) -> dict | None:
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
    "HARNESS", "CodexToolEventProjector", "native_identity", "NAMED_RAW_CALLS", "NON_TOOL_ITEMS",
    "RAW_ITEM_KINDS", "RAW_NON_TOOL_ITEMS", "RAW_TOOL_OUTPUTS", "RAW_TOOL_STARTS",
    "THREAD_ITEM_KINDS", "TYPED_TOOL_ITEMS", "TYPED_TOOL_OUTPUTS",
]
