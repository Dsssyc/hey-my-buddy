"""ZCode restricted read-only protocol: the review session contract (plan L6-A).

One review attempt opens a fresh root native session per answer round with the
fixed restricted parameters — native ``mode: plan`` so the user's default mode
is never inherited, a Read/Glob/Grep tool allowlist, no MCP servers, no
off-peak or dynamic workflow tools and no title generation. Tool frames are
projected into the shared evidence by
:class:`~buddy.adapters.zcode_tool_evidence.ZcodeToolFacts` before any
rejection or session filter, exactly like the no-tool channel; this module
extends the same canonical session/event rules with the real ``tool.updated``
allowance — the current root's own tool frames carry the same strictly
increasing integer sequence as every other canonical frame — the verified
subscription handshake, the cumulative call budget and the one format
correction. Whether a recorded call was allowed is judged only by the
blackboard at publication: the
native snapshot carries no allowlist echo, none is invented, and every state
and identity here comes from the native protocol. The native installation
content is only statically checked (:func:`native_contract_problem`); that
check proves the packaged restriction mechanisms exist through complete
template matching over tokenized code, never that a live session enforced
them.
"""
from __future__ import annotations

import re
import secrets

from .read_only import correction_code, no_tool_prompt, valid_answer
from .zcode_protocol import NativeConnection, NativeError
from .zcode_runner import NoToolEvidence, configure_session
from .zcode_static_contract import read_only_contract_problem
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


#: The public bundle text this contract ever reads, bounded by the L6 design.
_MAX_BUNDLE_TEXT_BYTES = 32 * 1024 * 1024


def native_contract_problem(source_text) -> str | None:
    """The first missing read-only mechanism in the public CLI bundle text.

    The check is structural and identifies mechanisms, never names: a strict
    session/create schema carrying the restriction fields with a mode enum that
    offers ``plan``, the named ``registerBuiltInTools``/``resolveBuiltInToolAllowlist``
    pair whose complete registration flow, tool-preserving transform, resolver
    return chain and call-site wiring are verified by
    :mod:`~buddy.adapters.zcode_static_contract` through string/comment/bracket-aware
    tokenization and complete supported templates over one shared outer code
    context — a discarded membership test, an ``allowedTools`` object the
    registration never reads, a rewritten Set, a decoy in a string, comment,
    template or regex literal or a side-effecting rejection operand each fail
    with their own reason — and the three read-only registration names.
    Minified identifiers are read from the text and followed to their
    declarations in their bound scopes, never assumed; versions, hashes and
    certificates play no part. The caller supplies the text bounded to 32 MiB
    of the public bundle; no credential or provider configuration ever enters
    this check.
    """
    if not isinstance(source_text, str) or not source_text.strip():
        return "no public CLI bundle text was provided for the read-only contract check"
    if len(source_text) > _MAX_BUNDLE_TEXT_BYTES:
        return "the public CLI bundle text exceeds the 32 MiB read-only contract bound"
    # The schema, mode-enum and tool-registration discoveries share the same
    # outer code context as the allowlist chain: nothing found inside a
    # string, comment, template or regex literal proves a mechanism.
    return read_only_contract_problem(source_text, _SCHEMA_FIELDS, READ_ONLY_TOOLS)



class ReadOnlyEvidence:
    """Canonical no-tool native evidence extended with real tool calls.

    The event families, sequence discipline, turn identity, completion and
    settlement rules stay the no-tool channel's; a ``tool.updated`` frame is a
    fact in every session because its facts were already projected, so a
    foreign, child, MCP or late call reaches the blackboard for judgment
    instead of disappearing into a rejection. The current root's own tool
    frames follow the same strictly increasing integer sequence as every other
    canonical frame before they are allowed. The deduplicated call count of
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
                # blackboard judges them at publication. The current root's own
                # frames carry the same strictly increasing integer sequence as
                # every other canonical frame; foreign, child and late frames
                # keep their facts without a session filter here.
                if params.get("sessionId") == self.session_id:
                    seq = params.get("seq")
                    if type(seq) is not int or seq <= self.last_seq:
                        raise NativeError("invalid-protocol",
                                          "read-only native tool frame order is invalid")
                    self.last_seq = seq
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
    parameters, verifies the native model/effort report, subscribes — the
    handshake's report must name this root session, carry a nonnegative
    integer event sequence and replay nothing into the fresh session — and only
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
        # ``resolved`` keeps the native configuration facts; ``observed`` stays
        # null — this protocol reports no served identity, so a refused send
        # below must never find one filled in.
        result["resolved"] = configure_session(connection, snapshot, spec, access)
        result["sessionId"] = session_id
        input_id = "buddy-read-only-" + secrets.token_hex(16)
        evidence = ReadOnlyEvidence(session_id, input_id, tools, budget)
        connection.observe = evidence.observe
        report = connection.call("session/subscribe", {"sessionId": session_id,
                                                       "deliveryKind": "web-remote-replayable",
                                                       "includeSnapshot": False})
        # The native handshake reports the session's own identity, a nonnegative
        # integer event sequence and the replay window. This is a fresh root
        # session that requested no snapshot and no afterSeq, so a non-empty
        # replay is a protocol failure; no subscribed or allowlist echo field
        # exists in the native protocol and none is invented here. A report
        # without this shape fails before any model input is sent.
        if (not isinstance(report, dict) or report.get("sessionId") != session_id
                or type(report.get("eventSeq")) is not int or report["eventSeq"] < 0
                or not isinstance(report.get("events"), list)):
            raise NativeError("invalid-protocol",
                              "the read-only subscription handshake returned an unknown report shape")
        if report["events"]:
            raise NativeError("invalid-protocol",
                              "the read-only subscription replayed events into a fresh root session")
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
