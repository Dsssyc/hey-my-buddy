"""ZCode restricted read-only protocol: the review session contract (plan L6-A).

One review attempt opens a fresh root native session per answer round with the
fixed restricted parameters — native ``mode: plan`` so the user's default mode
is never inherited, a Read/Glob/Grep tool allowlist, no MCP servers, no
off-peak or dynamic workflow tools and no title generation. Tool frames are
projected into the shared evidence by
:class:`~buddy.adapters.zcode_tool_evidence.ZcodeToolFacts` before any
rejection or session filter, exactly like the no-tool channel; this module
extends the same canonical session/event rules with the real ``tool.updated``
allowance, the cumulative call budget and the one format correction. Whether a
recorded call was allowed is judged only by the blackboard at publication: the
native snapshot carries no allowlist echo, none is invented, and every state
and identity here comes from the native protocol. The native installation
content is only statically checked (:func:`native_contract_problem`); that
check proves the mechanisms exist, never that a live session enforced them.
"""
from __future__ import annotations

import re
import secrets

from .read_only import correction_code, no_tool_prompt, valid_answer
from .zcode_protocol import NativeConnection, NativeError
from .zcode_runner import NoToolEvidence, configure_session
from .zcode_tool_evidence import ZcodeToolFacts

#: The exact restricted tool set; the native registry filters registrations to
#: these names and nothing else may join the session's tool surface.
READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
#: The explicit plan mode: never the worker's yolo, never a user default.
READ_ONLY_MODE = "plan"
#: One bounded structured answer, identical to the no-tool channel.
MAX_ANSWER_BYTES = 65536
#: At most two answer rounds: the original one and a single format correction.
MAX_ANSWER_ROUNDS = 2

__all__ = ["MAX_ANSWER_BYTES", "READ_ONLY_MODE", "READ_ONLY_TOOLS", "ReadOnlyEvidence",
           "native_contract_problem", "read_only_call", "session_parameters"]


def session_parameters(workspace: dict) -> dict:
    """The fixed restricted ``session/create`` parameters for one review session."""
    return {
        "workspace": workspace,
        "mode": READ_ONLY_MODE,
        "titleGenerationEnabled": False,
        "toolAllowlist": list(READ_ONLY_TOOLS),
        "mcpServers": [],
        "offPeakToolEnabled": False,
        "dynamicWorkflowEnabled": False,
    }


#: The session/create members the public bundle's strict schema must carry. The
#: ``parentSessionId`` member is what lets the runtime check demand a root.
_SCHEMA_FIELDS = ("workspace", "parentSessionId", "mode", "titleGenerationEnabled",
                  "mcpServers", "toolAllowlist", "toolDenylist",
                  "offPeakToolEnabled", "dynamicWorkflowEnabled")


def native_contract_problem(source_text) -> str | None:
    """The first missing read-only mechanism in the public CLI bundle text.

    The check is structural and identifies mechanisms, never names: a strict
    session/create schema carrying the restriction fields with a mode enum that
    offers ``plan``, the named ``registerBuiltInTools``/``resolveBuiltInToolAllowlist``
    pair with the allowedTools Set membership filter over ``metadata.name``, the
    call chain that feeds one from the other, and the three registration names.
    Minified identifiers are read from the text itself, never assumed; versions,
    hashes and certificates play no part. A bundle whose mechanisms cannot be
    identified is ineligible with the specific reason — a bare mention of
    ``toolAllowlist`` proves nothing. The caller supplies the text bounded to
    32 MiB of the public bundle; no credential or provider configuration ever
    enters this check.
    """
    if not isinstance(source_text, str) or not source_text.strip():
        return "no public CLI bundle text was provided for the read-only contract check"
    schema = _strict_session_schema(source_text)
    if schema is None:
        return "the public bundle has no strict session/create schema carrying the restriction fields"
    problem = _schema_mode_problem(source_text, schema)
    if problem is not None:
        return problem
    return _allowlist_chain_problem(source_text) or _registration_problem(source_text)


def _strict_session_schema(text: str) -> str | None:
    """One ``m.object`` schema carrying every restriction field, closed strict."""
    for marker in re.finditer("titleGenerationEnabled", text):
        start = text.rfind("m.object({", max(0, marker.start() - 800), marker.start())
        if start < 0:
            continue
        end = text.find("}).strict()", marker.end())
        if end < 0 or end - start > 1600:
            continue
        body = text[start:end]
        if all(re.search(r"(?<![A-Za-z0-9_])" + re.escape(field) + r"\s*:", body)
               for field in _SCHEMA_FIELDS):
            return body
    return None


def _schema_mode_problem(text: str, schema: str) -> str | None:
    member = re.search(r"(?<![A-Za-z0-9_])mode\s*:\s*([\w$]+)\.optional\(\)", schema)
    if member is None:
        return "the session/create schema binds mode to no named enum schema"
    enum = re.search(re.escape(member.group(1)) + r"\s*=\s*m\.enum\(\[([^\]]*)\]\)", text)
    if enum is None:
        return "the session/create mode is not bound to an enum in the public bundle"
    if not re.search(r'"plan"', enum.group(1)):
        return "the session/create mode enum does not offer plan"
    return None


def _allowlist_chain_problem(text: str) -> str | None:
    register = re.search(r'r\(([\w$]+),\s*"registerBuiltInTools"\)', text)
    resolve = re.search(r'r\(([\w$]+),\s*"resolveBuiltInToolAllowlist"\)', text)
    if register is None:
        return "the public bundle does not name registerBuiltInTools"
    if resolve is None:
        return "the public bundle does not name resolveBuiltInToolAllowlist"
    body = _function_body(text, register.group(1))
    if body is None:
        return "registerBuiltInTools is not a function definition in the public bundle"
    if not (re.search(r"new Set\(\w+\.allowedTools\)", body)
            and re.search(r"\.has\(\w+\.metadata\.name\)", body)):
        return "registerBuiltInTools registers without the allowedTools Set membership filter over metadata.name"
    body = _function_body(text, resolve.group(1))
    if body is None:
        return "resolveBuiltInToolAllowlist is not a function definition in the public bundle"
    if not re.search(r"(?<![\w$])[\w$]+\.toolAllowlist\b", body):
        return "resolveBuiltInToolAllowlist does not read the config toolAllowlist"
    if not re.search(r"allowedTools\s*:\s*" + re.escape(resolve.group(1)) + r"\s*\(", text):
        return "registerBuiltInTools is not called with allowedTools from resolveBuiltInToolAllowlist"
    return None


def _registration_problem(text: str) -> str | None:
    for name in READ_ONLY_TOOLS:
        # The span stops at the metadata object's first closing brace, so the
        # read-only flag is judged within this registration alone.
        registered = re.search(r'metadata\s*:\s*\{\s*name\s*:\s*"' + name + r'"[^{}]{0,400}', text)
        if registered is None:
            return f"the {name} built-in tool is not registered in the public bundle"
        if not re.search(r"readOnly\s*:\s*!0", registered.group(0)):
            return f"the {name} built-in tool is not registered as read-only in the public bundle"
    return None


def _function_body(text: str, name: str) -> str | None:
    """One function's source between its name and its balanced closing brace.

    The scan enters the body only at a brace outside the parameter list, so a
    default-parameter object literal cannot end the body early, and skips over
    string literals while balancing.
    """
    head = re.search(r"function\s+" + re.escape(name) + r"\s*\(", text)
    if head is None:
        return None
    # The regex consumed the parameter list's opening paren: we start one deep,
    # so a default-parameter object literal cannot end the scan early.
    index, parens = head.end(), 1
    while index < len(text):
        character = text[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens -= 1
        elif character == "{" and parens == 0:
            break
        index += 1
    depth, quote = 0, None
    while index < len(text) and index - head.start() <= 65536:
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 1
            elif character == quote:
                quote = None
        elif character in "'\"`":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[head.start():index + 1]
        index += 1
    return None


class ReadOnlyEvidence:
    """Canonical no-tool native evidence extended with real tool calls.

    The event families, sequence discipline, turn identity, completion and
    settlement rules stay the no-tool channel's; a canonical ``tool.updated``
    frame is allowed in every session because its facts were already projected,
    so a foreign, child, MCP or late call reaches the blackboard for judgment
    instead of disappearing into a rejection. The deduplicated call count of
    the shared collector carries the run's N/N+1 budget across every correction
    session; exceeding it fails the call with ``readonly-budget-exhausted``.
    """

    MODEL_EVENTS = NoToolEvidence.MODEL_EVENTS
    SESSION_EVENTS = NoToolEvidence.SESSION_EVENTS
    OPERATION_EVENTS = NoToolEvidence.OPERATION_EVENTS
    TELEMETRY_EVENTS = NoToolEvidence.TELEMETRY_EVENTS

    def __init__(self, session_id: str, input_id: str, tools: ZcodeToolFacts, tool_budget: int):
        self.session_id, self.input_id = session_id, input_id
        self.tools = tools
        self.tool_budget = tool_budget
        self.turn_id = None
        self.last_seq = -1
        self.completed = False
        self.settled = False
        self.raw_answer = None
        self.events = 0
        self.metadata_sequences = {}
        self.metadata_turn = None

    def observe_metadata(self, method: str, params: dict) -> None:
        """Native lifecycle projections precede canonical events; they prove no answer."""
        operation = method == "computer-use/operation-event"
        kinds = self.OPERATION_EVENTS if operation else self.TELEMETRY_EVENTS
        sequence = params.get("sequenceNumber" if operation else "eventSeq")
        if (params.get("kind") not in kinds or params.get("sessionId") != self.session_id
                or type(sequence) is not int or sequence <= self.metadata_sequences.get(method, -1)):
            raise NativeError("invalid-protocol", "invalid read-only native lifecycle metadata")
        self.metadata_sequences[method] = sequence
        if params["kind"] == "session-closed":
            return
        turn = params.get("turnId")
        if (not isinstance(turn, str) or not turn
                or self.turn_id is not None and turn != self.turn_id
                or self.metadata_turn is not None and turn != self.metadata_turn):
            raise NativeError("wrong-native-turn", "read-only lifecycle metadata differs from the admitted turn")
        self.metadata_turn = turn
        if params["kind"] == "turn-failed":
            raise NativeError("native-turn-failed", "read-only native turn failed")

    def observe(self, message: dict, _ordinal: int) -> None:
        if self.tools is not None:
            # Native tool facts are projected before any rejection or session
            # filter, whatever this method then decides; the cumulative count
            # from every projected start enforces the N/N+1 run budget.
            self.tools.observe(message)
            if self.tools.tool_calls > self.tool_budget:
                raise NativeError("readonly-budget-exhausted",
                                  "the read-only native call exceeded its tool-call budget")
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            raise NativeError("invalid-protocol", "read-only native event has no object parameters")
        if method in ('startup/storageState', 'process/mcpTelemetry', 'process/mcpResourceSamples',
                      'process/resourceSample'):
            return
        if method in ("computer-use/operation-event", "v4/telemetry/event"):
            self.observe_metadata(method, params)
            return
        if method == "session/event":
            kind = params.get("type")
            if kind == "tool.updated":
                # A real tool frame is a fact, never a rejection: its identity,
                # completeness and category were projected above and only the
                # blackboard judges them at publication.
                return
            if isinstance(kind, str) and (kind.startswith("tool.") or kind.startswith("agent.")):
                raise NativeError("invalid-protocol", "unknown native tool or agent frame in a read-only call")
            if kind not in {"turn.started", "turn.completed", "turn.failed",
                            *self.MODEL_EVENTS, *self.SESSION_EVENTS}:
                raise NativeError("invalid-protocol", "unknown read-only native event")
            if params.get("sessionId") != self.session_id:
                raise NativeError("invalid-protocol", "foreign session event in a read-only call")
            seq = params.get("seq")
            if type(seq) is not int or seq <= self.last_seq:
                raise NativeError("invalid-protocol", "read-only native event order is invalid")
            self.last_seq = seq
            self.events += 1
            data = params.get("payload")
            if not isinstance(data, dict):
                raise NativeError("invalid-protocol", "read-only native event payload is invalid")
            if kind in self.SESSION_EVENTS:
                return
            if kind == "turn.started":
                if (self.turn_id is not None or data.get("inputId") != self.input_id
                        or not isinstance(params.get("turnId"), str)
                        or self.metadata_turn is not None and params.get("turnId") != self.metadata_turn):
                    raise NativeError("wrong-native-turn", "read-only turn identity differs")
                self.turn_id = params["turnId"]
                if self.tools is not None:
                    # The trusted root identity comes only from this verified
                    # canonical turn start, never from the observed events.
                    self.tools.add_root(self.session_id, self.turn_id)
            elif not self.turn_id or params.get("turnId") != self.turn_id:
                raise NativeError("wrong-native-turn", "read-only event differs from the admitted turn")
            elif kind == "turn.failed":
                raise NativeError("native-turn-failed", "read-only native turn failed")
            elif kind == "turn.completed":
                if self.completed or data.get("inputId") != self.input_id or data.get("resultType") != "success":
                    raise NativeError("native-turn-failed", "read-only native turn did not complete successfully")
                self.raw_answer = data.get("response")
                if not isinstance(self.raw_answer, str) or len(self.raw_answer.encode()) > MAX_ANSWER_BYTES:
                    raise NativeError("invalid-native-result", "no bounded read-only native answer")
                self.completed = True
        elif method == "state.updated":
            if params.get("sessionId") not in (None, self.session_id):
                raise NativeError("invalid-protocol", "foreign read-only state event")
            if params.get("reason") == "prompt_failed":
                raise NativeError("native-turn-failed", "read-only prompt failed")
            if params.get("reason") == "prompt_completed":
                if not self.completed or self.settled:
                    raise NativeError("invalid-protocol", "read-only settlement lacks a completed turn")
                self.settled = True
        else:
            label = method if isinstance(method, str) and re.fullmatch(r'[A-Za-z0-9/._-]{1,80}', method) else 'unknown'
            raise NativeError("invalid-protocol", "unknown read-only native notification: " + label)


def _read_only_preflight(message: dict, _ordinal: int) -> None:
    """The admission filter before this attempt's own input is sent.

    Canonical ``tool.updated`` frames pass so their facts stay projected even
    before admission; everything else follows the no-tool admission set.
    """
    params = message.get("params") or {}
    if (message.get('method') in ('startup/storageState', 'process/mcpTelemetry',
                                  'process/mcpResourceSamples', 'process/resourceSample')
            and isinstance(params, dict)):
        return
    if message.get("method") == "session/event" and isinstance(params, dict):
        kind = params.get("type")
        if kind == "tool.updated" or kind in ReadOnlyEvidence.SESSION_EVENTS:
            return
    if message.get('method') == 'state.updated' and isinstance(params, dict):
        return
    if (message.get('method') == 'computer-use/operation-event' and isinstance(params, dict)
            and params.get('kind') == 'session-closed' and isinstance(params.get('sessionId'), str)
            and type(params.get('sequenceNumber')) is int):
        return
    method = message.get('method')
    label = method if isinstance(method, str) and re.fullmatch(r'[A-Za-z0-9/._-]{1,80}', method) else 'unknown'
    raise NativeError("invalid-protocol", "unexpected native event before read-only admission: " + label)


def read_only_call(connection: NativeConnection, control: dict, result: dict, workspace: dict,
                   access: dict, tools: ZcodeToolFacts) -> str:
    """One structured review call over restricted root sessions, corrections included.

    Every answer round creates a fresh root session with the fixed restricted
    parameters, verifies the native model/effort report, subscribes and only
    then sends. The single connection deadline spans the preflight checks,
    every native multi-tool step and the at most one format correction; tool
    counts accumulate in ``tools`` and are never reset. ``modelStarted`` is
    claimed only after a real admitted send. A correction round requires the
    previous session's acknowledged close and reuses the same policy; a failed
    close leaves ``tools.close_pending`` set and keeps every collected fact.
    The stream EOF drain and the owned process stop stay with the caller's
    finish — this function never claims the native group stopped.
    """
    request = control["readOnlyRequest"]
    spec = control["spec"]
    budget = (request.get("budget") or {}).get("toolCalls")
    if type(budget) is not int or budget < 0:
        raise NativeError("invalid-control", "the read-only request carries no nonnegative tool-call budget")
    if workspace.get("workspacePath") != control["cwd"]:
        raise NativeError("wrong-native-workspace", "the read-only workspace does not match the allocated checkout")
    parameters = session_parameters(workspace)
    base_prompt = no_tool_prompt(request["prompt"], request["outputSchema"])
    prompt = base_prompt
    session_id = None
    for attempt in range(MAX_ANSWER_ROUNDS):
        connection.observe = tools.observe_with(_read_only_preflight)
        snapshot = connection.call("session/create", parameters)
        session = snapshot.get("session") or {}
        session_id = session.get("sessionId")
        if not isinstance(session_id, str) or not session_id or session.get("parentSessionId"):
            raise NativeError("wrong-native-session", "the read-only call requires a root native session")
        native_path = (session.get("workspace") or {}).get("workspacePath")
        if native_path != control["cwd"]:
            raise NativeError("wrong-native-workspace", "the read-only native workspace differs from the frozen copy")
        result["resolved"] = configure_session(connection, snapshot, spec, access)
        result["observed"] = {**result["resolved"], "workspacePath": native_path}
        result["sessionId"] = session_id
        input_id = "buddy-read-only-" + secrets.token_hex(16)
        evidence = ReadOnlyEvidence(session_id, input_id, tools, budget)
        connection.observe = evidence.observe
        connection.call("session/subscribe", {"sessionId": session_id,
                                              "deliveryKind": "web-remote-replayable",
                                              "includeSnapshot": False})
        accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id,
                                                    "content": prompt})
        if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
            raise NativeError("native-admission-failed", "the read-only input was not admitted")
        # Only a real admitted send may claim the model started.
        result["modelStarted"] = True
        while not evidence.settled:
            connection.pump()
        tools.close_pending = True
        closed = connection.call("session/close", {"sessionId": session_id})
        if closed.get("closed") is not True:
            raise NativeError("session-close-unconfirmed", "the read-only session close was not acknowledged")
        tools.close_pending = False
        result.update(rawAnswer=evidence.raw_answer,
                      answerValid=valid_answer(evidence.raw_answer, request["outputSchema"]),
                      nativeIdentity={"sessionId": session_id, "turnId": evidence.turn_id},
                      usage={"toolCalls": tools.tool_calls}, correctionCount=attempt)
        correction = correction_code(evidence.raw_answer, request["outputSchema"])
        if correction is None or attempt:
            return session_id
        prompt = base_prompt + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    return session_id
