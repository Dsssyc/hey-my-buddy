"""Unified Router tool-event evidence: pure normalization, collection and judgment.

Per [ADR-021](docs/decisions/021-router-buddy-planes-and-routing-evidence.md) §4
and [ADR-023](docs/decisions/023-harness-integration-principles.md) §3, adapters
only project their harness's native tool events into the shared Agent Client
Protocol vocabulary; the blackboard is the single authority that decides whether
an answer may publish. This module holds both sides of that seam as pure data
code: :func:`normalize_tool_event` and :class:`ToolEventEvidence` summarize the
facts a native controller observed into one ``toolEvidence`` package, and
:func:`judge_tool_evidence` applies the one publication matrix. Neither side
records a verdict of its own, and no tool arguments, output bodies, prompts,
reasoning text or model claims are ever retained.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..blackboard.store.db import canonical_json
from ..errors import BoardError

#: The Agent Client Protocol tool-call categories. The fixed per-adapter maps
#: only ever produce read, search, execute, edit, fetch and other; the remaining
#: names exist so a package carrying one is judged, never silently accepted.
ACP_CATEGORIES = ("read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other")
TOOL_EVENT_PHASES = ("start", "end")
TOOL_EVIDENCE_VERSION = 1
#: The retention bound shared by every harness: at most this many start/end
#: events survive in one package. Reaching it marks further facts ``truncated``
#: and the blackboard invalidates the answer instead of judging a slice.
MAX_TOOL_EVENTS = 128

ROUTING_MODES = ("fast", "review")
#: Review mode with a native system sandbox may read, search and execute
#: commands; without one only read and search are allowed. Fast mode allows no
#: tool call at all, which the same judge expresses as an empty allowance.
SANDBOXED_REVIEW_CATEGORIES = frozenset({"read", "search", "execute"})
UNSANDBOXED_REVIEW_CATEGORIES = frozenset({"read", "search"})

TOOL_EVIDENCE_UNVERIFIED = "router-tool-evidence-unverified"
TOOLS_FORBIDDEN = "router-tools-forbidden"

PACKAGE_FIELDS = frozenset({
    "version", "binding", "nativeIdentity", "streamComplete", "events",
    "toolCalls", "unsettledToolCalls", "truncated",
})
BINDING_FIELDS = frozenset({"adapter", "taskId", "attemptId", "generation"})
EVENT_FIELDS = frozenset({"nativeIdentity", "callId", "toolName", "category", "phase"})

#: The fixed native tool name/type classification per adapter. A name a harness
#: did not register — an MCP server, a subagent, a dynamic tool or any
#: unrecognized name, including one that merely resembles an ACP category —
#: stays ``other``; it is never promoted to a legal category.
NATIVE_TOOL_CATEGORIES: dict[str, dict[str, str]] = {
    "dsh": {"read": "read", "glob": "search", "grep": "search", "bash": "execute"},
    "claude": {
        "Read": "read", "LS": "read",
        "Glob": "search", "Grep": "search",
        "Bash": "execute",
        "Write": "edit", "Edit": "edit", "MultiEdit": "edit",
        "WebFetch": "fetch", "WebSearch": "fetch",
    },
    "codex": {
        "shell": "execute", "code-mode": "execute", "exec": "execute", "commandExecution": "execute",
        "exec_command": "execute", "shell_command": "execute", "write_stdin": "execute",
        "local_shell_call": "execute",
        "fileChange": "edit",
        "web_search_call": "fetch",
    },
    "zcode": {
        "Read": "read",
        "Glob": "search", "Grep": "search",
        "Bash": "execute",
        "Write": "edit", "Edit": "edit",
        "WebFetch": "fetch",
    },
}
#: Model reasoning text is not a tool event in any harness.
NON_TOOL_NAMES = frozenset({"reasoning", "thinking"})
NATIVE_ID_FIELDS = frozenset({"sessionId", "threadId", "turnId", "inputId", "callId"})


def _identity(value: Any, label: str = "nativeIdentity") -> dict:
    """One native-provided identity: only real string fields, never fabricated ones."""
    if (not isinstance(value, dict) or not value or set(value) - NATIVE_ID_FIELDS
            or any(not isinstance(item, str) or not item or len(item) > 512
                   for key, item in value.items())):
        raise BoardError("INVALID_ARGUMENT", f"{label} must be a nonempty object of native-provided string fields")
    return dict(value)


def _binding(value: Any) -> dict:
    if (not isinstance(value, dict) or set(value) != BINDING_FIELDS
            or any(not isinstance(value.get(key), str) or not value[key]
                   for key in ("adapter", "taskId", "attemptId"))
            or type(value.get("generation")) is not int or value["generation"] < 0):
        raise BoardError(
            "INVALID_ARGUMENT",
            "binding must be exactly adapter, taskId, attemptId and generation from the Python control file",
        )
    return dict(value)


def _event(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != EVENT_FIELDS:
        raise BoardError(
            "INVALID_ARGUMENT",
            "a normalized tool event must carry exactly nativeIdentity, callId, toolName, category and phase",
        )
    identity = _identity(value.get("nativeIdentity"))
    call_id, tool_name = value.get("callId"), value.get("toolName")
    if not isinstance(call_id, str) or not call_id or len(call_id) > 512:
        raise BoardError("INVALID_ARGUMENT", "callId must be a nonempty string")
    if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 512:
        raise BoardError("INVALID_ARGUMENT", "toolName must be a nonempty string")
    if value.get("category") not in ACP_CATEGORIES:
        raise BoardError("INVALID_ARGUMENT", "category must be an ACP tool-call category")
    if value.get("phase") not in TOOL_EVENT_PHASES:
        raise BoardError("INVALID_ARGUMENT", "phase must be start or end")
    return {"nativeIdentity": identity, "callId": call_id, "toolName": tool_name,
            "category": value["category"], "phase": value["phase"]}


def _classify(table: dict[str, str], name: Any, native_type: Any) -> str | None:
    """The fixed category for one native call, or ``None`` for reasoning text.

    A name and a type that disagree — including a recognized one beside an
    unrecognized one — cannot be forged into the legal category either suggests;
    they settle as ``other``.
    """
    named = [value for value in (name, native_type) if isinstance(value, str) and value]
    if not named:
        raise BoardError("INVALID_ARGUMENT", "a tool event must carry its native toolName or type")
    if len(named) == 1:
        return table.get(named[0], "other")
    first, second = (table.get(value, "other") for value in named)
    return first if first == second else "other"


def normalize_tool_event(adapter: Any, native_event: Any) -> dict | None:
    """Classify one controller-extracted native tool fact into the ACP vocabulary.

    ``native_event`` carries only the facts the native controller extracted —
    ``nativeIdentity``, ``callId``, ``toolName`` (or the native event ``type``
    where the harness names its calls that way) and ``phase``; every other key,
    including tool arguments, result bodies and model claims, is dropped.
    Returns ``None`` when the fact is model reasoning text rather than a tool
    call, and raises :class:`BoardError` for input no event can be built from.
    """
    table = NATIVE_TOOL_CATEGORIES.get(adapter) if isinstance(adapter, str) else None
    if table is None:
        raise BoardError("UNSUPPORTED_ADAPTER", f"No fixed tool classification for adapter {adapter!r}")
    if not isinstance(native_event, dict):
        raise BoardError("INVALID_ARGUMENT", "native_event must be an object")
    # A named call is still a call, even if its name is "reasoning". Only
    # native reasoning item types, with no tool name, describe non-tool text.
    if not native_event.get("toolName") and native_event.get("type") in NON_TOOL_NAMES:
        return None
    identity = _identity(native_event.get("nativeIdentity"))
    call_id = native_event.get("callId")
    if not isinstance(call_id, str) or not call_id:
        raise BoardError("INVALID_ARGUMENT", "callId must be a nonempty string")
    phase = native_event.get("phase")
    if phase not in TOOL_EVENT_PHASES:
        raise BoardError("INVALID_ARGUMENT", "phase must be start or end")
    name, native_type = native_event.get("toolName"), native_event.get("type")
    category = _classify(table, name, native_type)
    if category is None:
        return None
    return {
        "nativeIdentity": identity,
        "callId": call_id,
        "toolName": name if isinstance(name, str) and name else native_type,
        "category": category,
        "phase": phase,
    }


class ToolEventEvidence:
    """The fact summary for one Router attempt's tool events.

    The controller observes normalized events as the native stream produces them
    and calls :meth:`finish` once the attempt's last stream has closed, passing
    the trusted root identities taken from the root session/turn creation
    receipts — a format correction's root turns included one by one, never
    inferred from the observed events. Events observed after that close are
    recorded as-is and leave the stream incomplete. The package summarizes
    facts only; it never carries a verdict, an allowance or a model claim.
    """

    def __init__(self, binding: dict):
        self.binding = _binding(binding)
        self._events: list[dict] = []
        self._calls: dict[tuple[str, str], dict] = {}
        self._root_identities: list[dict] = []
        self._stream_reported: bool | None = None
        self._finished = False
        self._late = False
        self._truncated = False
        self._incomplete = False
        self._closed_roots: set[str] = set()
        self._excluded: frozenset = frozenset()

    def close_root(self, native_identity: dict) -> None:
        """Record an actual native turn end before transport drain finishes."""
        self._closed_roots.add(canonical_json(_identity(native_identity)))

    @property
    def tool_calls(self) -> int:
        """Observed unique starts, available while enforcing cumulative budgets."""
        return sum(1 for call in self._calls.values()
                   if any(phase == "start" for _fact, phase in call["pairs"]))

    def observe_incomplete(self, adapter: str, native_event: dict) -> None:
        """Keep a broken native tool fact without inventing its missing IDs.

        Controllers call this after normalization cannot form a complete event.
        Retained fields are bounded facts; invalid IDs remain absent and the
        stream cannot pass judgment, even if it subsequently closes normally.
        """
        self._incomplete = True
        if self._finished:
            self._late = True
        native_event = native_event if isinstance(native_event, dict) else {}
        def bounded(value):
            return value if isinstance(value, str) and 0 < len(value) <= 512 else None
        identity = native_event.get("nativeIdentity")
        identity = {key: bounded(value) for key, value in identity.items()
                    if key in NATIVE_ID_FIELDS and bounded(value)} if isinstance(identity, dict) else {}
        name, native_type = bounded(native_event.get("toolName")), bounded(native_event.get("type"))
        try:
            category = _classify(NATIVE_TOOL_CATEGORIES.get(adapter, {}), name, native_type)
        except BoardError:
            category = "other"
        self._retain({"nativeIdentity": identity, "callId": bounded(native_event.get("callId")),
                      "toolName": name or native_type, "category": category or "other",
                      "phase": native_event.get("phase") if native_event.get("phase") in TOOL_EVENT_PHASES else None})
        call_id = bounded(native_event.get("callId"))
        if identity and call_id:
            key = (canonical_json(identity), call_id)
            self._calls.setdefault(key, {"pairs": set()})["incomplete"] = True

    def observe(self, event: dict) -> None:
        """Record one normalized event; identical projections collapse, conflicts stay."""
        record = _event(event)
        if self._finished or canonical_json(record["nativeIdentity"]) in self._closed_roots:
            self._late = True
        key = (canonical_json(record["nativeIdentity"]), record["callId"])
        call = self._calls.setdefault(key, {"pairs": set()})
        # The dedup unit is the whole fact: same identity and callId with the
        # same tool facts in the same phase collapse; any new pairing — a new
        # phase, or known facts in a phase only another fact used — stays.
        pair = ((record["toolName"], record["category"]), record["phase"])
        if pair not in call["pairs"]:
            self._retain(record)
        call["pairs"].add(pair)

    def _retain(self, record: dict) -> None:
        if len(self._events) >= MAX_TOOL_EVENTS:
            self._truncated = True
        else:
            self._events.append(record)

    def finish(self, native_identity: list, stream_complete: bool, *,
               exclude_calls: Iterable[tuple[str, str]] | None = None) -> dict:
        """Close the attempt's stream and return the ``toolEvidence`` package.

        ``native_identity`` is the complete list of this attempt's trusted root
        identities; each later finish replaces it with the then-current list and
        keeps the stream complete only if every close reported completion.

        ``exclude_calls`` names calls whose results the caller verified as the
        completion mechanism's own delivery evidence, as the collector's own
        full call keys — ``(canonical_json(native_identity), callId)`` pairs,
        exactly the identity join ``observe`` uses. Exclusion removes a
        verified delivery call's events from the published package and its
        counts, and nothing else: a foreign root reusing the call id stays a
        fact, a call with conflicting facts is never hidden, and the counts
        always come from the complete internal call table — never recounted
        from the bounded events list. With no exclusion (the default) the
        package is exactly what every current harness publishes.
        """
        if not isinstance(native_identity, list):
            raise BoardError("INVALID_ARGUMENT", "native_identity must be a list of root identity objects")
        roots: list[dict] = []
        for identity in native_identity:
            validated = _identity(identity)
            if validated not in roots:
                roots.append(validated)
        self._root_identities = roots
        self._excluded = self._exclusion_keys(exclude_calls)
        reported = stream_complete is True
        self._stream_reported = reported if self._stream_reported is None else (self._stream_reported and reported)
        self._finished = True
        return self._package()

    def _exclusion_keys(self, exclude_calls: Iterable[tuple[str, str]] | None) -> frozenset[tuple[str, str]]:
        keys = set()
        for item in () if exclude_calls is None else exclude_calls:
            if not isinstance(item, tuple) or len(item) != 2:
                raise BoardError("INVALID_ARGUMENT",
                                 "exclude_calls entries must be (canonical nativeIdentity, callId) pairs")
            identity, call_id = item
            if (not isinstance(identity, str) or not identity
                    or not isinstance(call_id, str) or not 0 < len(call_id) <= 512):
                raise BoardError("INVALID_ARGUMENT", "an excluded call must carry its canonical identity and bounded call id")
            keys.add((identity, call_id))
        return frozenset(keys)

    def _excludable(self, key: tuple[str, str]) -> bool:
        """A verified delivery call hides nothing but itself.

        Incomplete or conflicting tool facts are never excluded: dropping the
        whole call would hide those facts behind the delivery evidence.
        """
        if key not in self._excluded:
            return False
        call = self._calls.get(key)
        if call is None or call.get("incomplete"):
            return False
        facts = {fact for fact, _phase in call["pairs"]}
        return _consistent_call_facts(self.binding["adapter"], facts)

    def _package(self) -> dict:
        excluded = {key for key in self._calls if self._excludable(key)}
        kept_calls = {key: call for key, call in self._calls.items()
                      if key not in excluded}
        started = sum(1 for call in kept_calls.values()
                      if any(phase == "start" for _fact, phase in call["pairs"]))
        unsettled = sum(1 for call in kept_calls.values()
                        if {phase for _fact, phase in call["pairs"]} == {"start"})
        kept_events = [{**event, "nativeIdentity": dict(event["nativeIdentity"])}
                       for event in self._events
                       if (canonical_json(event["nativeIdentity"]), event["callId"]) not in excluded]
        return {
            "version": TOOL_EVIDENCE_VERSION,
            "binding": dict(self.binding),
            "nativeIdentity": [dict(identity) for identity in self._root_identities],
            "streamComplete": bool(self._stream_reported) and not self._late and not self._incomplete,
            "events": kept_events,
            "toolCalls": started,
            "unsettledToolCalls": unsettled,
            "truncated": self._truncated,
        }


def _structural_problem(evidence: Any, mode: Any, has_system_sandbox: Any) -> str | None:
    if mode not in ROUTING_MODES:
        return "the routing mode is not fast or review"
    if type(has_system_sandbox) is not bool:
        return "the system sandbox fact is not a boolean"
    if not isinstance(evidence, dict) or set(evidence) != PACKAGE_FIELDS:
        return "the tool evidence package does not carry exactly its fixed fields"
    version = evidence["version"]
    if type(version) is not int or version != TOOL_EVIDENCE_VERSION:
        return "the tool evidence version is not supported"
    identities, events = evidence["nativeIdentity"], evidence["events"]
    if (type(evidence["toolCalls"]) is not int or evidence["toolCalls"] < 0
            or type(evidence["unsettledToolCalls"]) is not int or evidence["unsettledToolCalls"] < 0
            or type(evidence["streamComplete"]) is not bool or type(evidence["truncated"]) is not bool
            or not isinstance(identities, list) or not identities or not isinstance(events, list)):
        return "the tool evidence summary fields have the wrong types"
    try:
        _binding(evidence["binding"])
        for identity in identities:
            _identity(identity)
        for event in events:
            _event(event)
    except BoardError:
        return "the tool evidence binding, identities or events are malformed"
    return None


def _consistent_call_facts(adapter: str, facts: set) -> bool:
    if len(facts) == 1:
        return True
    # Codex exposes the same call through a typed commandExecution item and
    # its raw native tool name. Keep both facts and join only by the real ID.
    if adapter != "codex" or len(facts) != 2 or ("commandExecution", "execute") not in facts:
        return False
    return all(category == "execute" and NATIVE_TOOL_CATEGORIES["codex"].get(name) == category
               for name, category in facts)


def judge_tool_evidence(evidence: Any, mode: Any, has_system_sandbox: Any) -> str | None:
    """The single publication matrix over one ``toolEvidence`` package.

    Returns ``None`` when the evidence is complete, consistent and every
    recorded call is allowed; ``router-tools-forbidden`` when a recorded call is
    clearly outside the mode's policy (fast mode allows no call at all); and
    ``router-tool-evidence-unverified`` when the package is missing, malformed,
    inconsistent or incomplete. Binding the package to its attempt is the
    caller's job; this function judges the facts a package carries.
    """
    problem = _structural_problem(evidence, mode, has_system_sandbox)
    if problem is not None:
        return TOOL_EVIDENCE_UNVERIFIED
    allowed = (SANDBOXED_REVIEW_CATEGORIES if has_system_sandbox else UNSANDBOXED_REVIEW_CATEGORIES) \
        if mode == "review" else frozenset()
    calls: dict[tuple[str, str], dict] = {}
    opened = set()
    end_before_start = False
    for event in evidence["events"]:
        key = (canonical_json(event["nativeIdentity"]), event["callId"])
        if event["phase"] == "start":
            opened.add(key)
        elif key not in opened:
            end_before_start = True
        call = calls.setdefault(key,
                                {"facts": set(), "phases": set()})
        call["facts"].add((event["toolName"], event["category"]))
        call["phases"].add(event["phase"])
    # A clearly disallowed call invalidates the answer even when the stream also
    # failed to close: the recorded fact stands on its own. Facts that contradict
    # each other name no clear category, so they stay untrusted here and surface
    # as incompleteness below.
    if mode == "fast":
        if evidence["events"]:
            return TOOLS_FORBIDDEN
    else:
        for call in calls.values():
            if (_consistent_call_facts(evidence["binding"]["adapter"], call["facts"])
                    and next(iter(call["facts"]))[1] not in allowed):
                return TOOLS_FORBIDDEN
    if evidence["truncated"] or not evidence["streamComplete"] or end_before_start:
        return TOOL_EVIDENCE_UNVERIFIED
    for event in evidence["events"]:
        if not any(event["nativeIdentity"] == root for root in evidence["nativeIdentity"]):
            # A foreign, sub-agent or old-turn call is kept as a fact and can
            # never pass as complete evidence for this attempt's roots.
            return TOOL_EVIDENCE_UNVERIFIED
    started = unsettled = 0
    for call in calls.values():
        if not _consistent_call_facts(evidence["binding"]["adapter"], call["facts"]):
            return TOOL_EVIDENCE_UNVERIFIED
        has_start, has_end = "start" in call["phases"], "end" in call["phases"]
        if has_end and not has_start:
            return TOOL_EVIDENCE_UNVERIFIED
        if has_start:
            started += 1
            unsettled += 0 if has_end else 1
    if (started != evidence["toolCalls"] or unsettled != evidence["unsettledToolCalls"]
            or len(evidence["events"]) > MAX_TOOL_EVENTS):
        return TOOL_EVIDENCE_UNVERIFIED
    if unsettled:
        # A call still open at the close leaves the evidence incomplete even
        # when every summary field agrees with the recorded facts.
        return TOOL_EVIDENCE_UNVERIFIED
    return None


__all__ = [
    "ACP_CATEGORIES", "BINDING_FIELDS", "EVENT_FIELDS", "MAX_TOOL_EVENTS",
    "NATIVE_TOOL_CATEGORIES", "NON_TOOL_NAMES", "PACKAGE_FIELDS", "ROUTING_MODES",
    "SANDBOXED_REVIEW_CATEGORIES", "TOOL_EVIDENCE_UNVERIFIED", "TOOL_EVIDENCE_VERSION",
    "TOOLS_FORBIDDEN", "UNSANDBOXED_REVIEW_CATEGORIES", "ToolEventEvidence",
    "judge_tool_evidence", "normalize_tool_event",
]
