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
from ..snapshot_mount import ObservedSnapshotCalls

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

    def __init__(self, binding: dict, *, state_dir: Path, session_tools=()):
        self.evidence = ToolEventEvidence(binding, state_dir=state_dir,
                                          session_service_tools=session_tools)
        self.roots: list[dict] = []
        #: The mounted snapshot service's bare dynamic tool names, with the
        #: observed native calls the driver later verifies against the ledger.
        self.session_tools = frozenset(session_tools)
        self.snapshot_calls = ObservedSnapshotCalls(names=self.session_tools)
        self._session_starts: set[tuple[str, str]] = set()
        self._dispatcher_starts: set[tuple[str, str]] = set()
        self._snapshot_requests: dict[tuple[str, str], tuple[str, object]] = {}
        self._dynamic_phases: dict[tuple[str, str], dict] = {}
        self._dynamic_tools: dict[tuple[str, str], str] = {}
        self._started: dict[tuple[str, str], tuple[str, str | None]] = {}

    @property
    def tool_calls(self) -> int:
        """Deduplicated observed starts; the cumulative tool budget uses this."""
        return self.evidence.tool_calls

    @property
    def session_tool_calls(self) -> int:
        """Observed starts of mounted snapshot calls and Codex's JS dispatcher."""
        return len(self._session_starts | self._dynamic_phases.keys() | self._dispatcher_starts)

    def observe_root(self, session_id: str, turn_id: str) -> None:
        """Record one root turn identity exactly as its native receipt carried it."""
        root = {"sessionId": session_id, "turnId": turn_id}
        if root not in self.roots:
            self.roots.append(root)

    def observe_snapshot_request(self, identity: dict, call_id: str, tool_name: str,
                                 arguments: object) -> None:
        """Retain the item/tool/call request's own arguments for one mounted call.

        The request frame is the only place the native protocol shows the
        exact arguments the model sent; the call's phases and result still
        come from the streamed items alone.
        """
        key = (canonical_json(identity), call_id)
        self._snapshot_requests[key] = (tool_name, arguments)
        typed_tool = self._dynamic_tools.get(key)
        phases = self._dynamic_phases.get(key, {}) if (typed_tool is None or typed_tool == tool_name) else {}
        self.snapshot_calls.observe(identity, call_id, tool_name, arguments=arguments, **phases)

    def snapshot_frame(self, message: dict) -> bool:
        if not self.session_tools:
            return False
        params = message.get("params")
        if not isinstance(params, dict):
            return False
        identity = native_identity(params)
        item = params.get("item")
        if not isinstance(item, dict) or identity is None:
            return False
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id:
            return False
        key = (canonical_json(identity), call_id)
        if item.get("type") == "dynamicToolCall":
            # The typed start can precede the RPC naming the tool. An unnamed
            # dynamic item is provisionally held, then judged from its phases
            # and receipt when the root settles.
            return item.get("tool") is None or (isinstance(item["tool"], str) and item["tool"] in self.session_tools)
        if item.get("type") == "function_call":
            return (isinstance(item.get("name"), str) and item["name"] in self.session_tools
                    and item.get("namespace") in (None, "functions"))
        if item.get("type") == "function_call_output":
            return key in self._snapshot_requests or key in self._session_starts
        if item.get("type") == "custom_tool_call":
            return item.get("name") == "exec" and item.get("namespace") in (None, "functions")
        return item.get("type") == "custom_tool_call_output" and key in self._dispatcher_starts

    def _matches_observed_item(self, identity: dict | None, item: object) -> bool:
        if identity is None or identity not in self.roots or not isinstance(item, dict):
            return False
        kind = item.get("type")
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(kind, str) or not isinstance(call_id, str) or not call_id:
            return False
        key = (canonical_json(identity), call_id)
        started = self._started.get(key)
        if started is None:
            return False
        if kind == "dynamicToolCall":
            tool = item.get("tool")
            if self.session_tools:
                if key not in self._dynamic_phases:
                    return False
                typed_tool = self._dynamic_tools.get(key)
                if typed_tool == "":
                    return False
                request = self._snapshot_requests.get(key)
                if tool is not None:
                    return (isinstance(tool, str) and tool in self.session_tools
                            and typed_tool in (None, tool)
                            and (request is None or request[0] == tool))
                return True
            return started[0] == "dynamicToolCall"
        if kind in TYPED_TOOL_ITEMS:
            return started[0] == kind
        if kind in RAW_TOOL_STARTS:
            name = item.get("name") if kind in NAMED_RAW_CALLS else kind
            expected_native = "mcpToolCall" if item.get("namespace") not in (None, "functions") else None
            return isinstance(name, str) and started == (name, expected_native)
        return kind in TYPED_TOOL_OUTPUTS or kind in RAW_TOOL_OUTPUTS

    def matches_observed_completed_item(self, params: object, item: object) -> bool:
        if not isinstance(params, dict):
            return False
        turn = params.get("turn") or {}
        turn_params = {**params, "turnId": params.get("turnId") or turn.get("id")}
        return self._matches_observed_item(native_identity(turn_params), item)

    def completed_snapshot_item(self, params: object, item: object) -> bool:
        if not self.session_tools or not isinstance(params, dict) or not isinstance(item, dict):
            return False
        turn = params.get("turn") or {}
        turn_params = {**params, "turnId": params.get("turnId") or turn.get("id")}
        identity = native_identity(turn_params)
        if not self._matches_observed_item(identity, item):
            return False
        call_id = item.get("call_id") or item.get("id")
        key = (canonical_json(identity), call_id)
        kind = item.get("type")
        if kind == "dynamicToolCall":
            return key in self._dynamic_phases and self.snapshot_frame({"params": {**turn_params, "item": item}})
        if kind in ("function_call", "function_call_output"):
            return key in self._session_starts and self.snapshot_frame({"params": {**turn_params, "item": item}})
        if kind in ("custom_tool_call", "custom_tool_call_output"):
            return key in self._dispatcher_starts and self.snapshot_frame({"params": {**turn_params, "item": item}})
        return False

    def finish(self, stream_complete: bool, *, verified_session_calls=()) -> dict:
        """Close the stream and return the ``toolEvidence`` package.

        ``stream_complete`` reports that every observed root turn completed and
        the controller held the stream to its end — drained to EOF on the
        no-tool branch, completed turns on the review branch.
        ``verified_session_calls`` names the mounted snapshot calls whose signed
        ledger receipts the driver verified in both directions.
        """
        return self.evidence.finish(list(self.roots), stream_complete,
                                    verified_session_calls=verified_session_calls)

    def observe_notification(self, message: dict) -> None:
        """Project the tool facts of one notification frame, before any filtering."""
        if not isinstance(message, dict):
            return
        method = message.get("method")
        params = message.get("params")
        if method == "turn/completed" and isinstance(params, dict):
            turn = params.get("turn") or {}
            turn_params = {**params, "turnId": params.get("turnId") or turn.get("id")}
            identity = native_identity(turn_params)
            items = turn.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("type") in NON_TOOL_ITEMS:
                        continue
                    if not self._matches_observed_item(identity, item):
                        tool_name = (item.get("tool") or item.get("name") or item.get("type")) if isinstance(item, dict) else None
                        self._incomplete(turn_params, tool_name=tool_name if isinstance(tool_name, str) else None, phase=None)
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
        if kind == "dynamicToolCall" and self.session_tools:
            # The typed item carries the native call id in ``item["id"]`` —
            # exactly the ``item/tool/call`` request's ``callId``. Codex CLI
            # 0.169.1 can name the tool on the typed item or a matching raw item.
            self._snapshot_typed(params, item, method)
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
        name = item.get("tool") if kind == "dynamicToolCall" else None
        self._observe(params, phase="start" if method == "item/started" else "end",
                      tool_name=name if isinstance(name, str) and name in self.session_tools else kind,
                      call_id=item.get("id") or item.get("call_id"),
                      outcome=_native_outcome(item, kind) if method == "item/completed" else "unknown")

    def _snapshot_typed(self, params, item: dict, method: str) -> None:
        identity = native_identity(params)
        call_id = item.get("id") or item.get("call_id")
        if identity is None or not isinstance(call_id, str) or not call_id:
            return
        key = (canonical_json(identity), call_id)
        tool = item.get("tool")
        if tool is not None:
            if not isinstance(tool, str) or tool not in self.session_tools:
                return
            prior = self._dynamic_tools.get(key)
            if prior is not None and prior != tool:
                self._dynamic_tools[key] = ""
                return
            self._dynamic_tools[key] = tool
        phases = self._dynamic_phases.setdefault(key, {})
        if method == "item/started":
            phases["started"] = True
        elif method == "item/completed":
            status = item.get("status")
            success = True if status == "completed" else False if status == "failed" else None
            if type(item.get("success")) is bool and item["success"] is not success:
                success = None
            phases.update(completed=True, result={"success": success,
                                                  "contentItems": item.get("contentItems")},
                          ok=success)
        request = self._snapshot_requests.get(key)
        typed_tool = self._dynamic_tools.get(key)
        if request is not None and (typed_tool is None or typed_tool == request[0]):
            self.snapshot_calls.observe(identity, call_id, request[0], **phases)

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
            identity = native_identity(params)
            call_id = item.get("call_id") or item.get("id")
            if name in self.session_tools:
                if raw_type == "function_call" and item.get("namespace") in (None, "functions"):
                    if identity is not None and isinstance(call_id, str) and call_id:
                        self._session_starts.add((canonical_json(identity), call_id))
                else:
                    # A registered bare name on another native tool carrier
                    # cannot supply the raw half of a dynamic query call.
                    self._incomplete(params, tool_name=name, phase="start")
            elif (self.session_tools and raw_type == "custom_tool_call" and name == "exec"
                  and item.get("namespace") in (None, "functions")
                  and identity is not None and isinstance(call_id, str) and call_id):
                self._dispatcher_starts.add((canonical_json(identity), call_id))
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
                      native_type=correlated[1] if correlated else None, call_id=call_id,
                      outcome=_native_outcome(item, kind))

    def _observe(self, params, *, phase: str, tool_name, call_id, native_type=None,
                 outcome="unknown") -> None:
        identity = native_identity(params)
        fact = {"nativeIdentity": identity or {}, "callId": call_id,
                "toolName": tool_name, "phase": phase, "outcome": outcome,
                **({"type": native_type} if native_type else {})}
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


def _native_outcome(item: dict, kind: str) -> str:
    """Use terminal native metadata; never inspect output text or result bodies."""
    status = item.get("status")
    if status in ("failed", "declined"):
        return "failure"
    exit_code = item.get("exitCode")
    if kind == "commandExecution":
        if type(exit_code) is int and exit_code != 0:
            return "failure"
        return "success" if status == "completed" and type(exit_code) is int and exit_code == 0 else "unknown"
    return "success" if status == "completed" else "unknown"

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
