"""Unified Router tool evidence: normalization, call files and judgment.

Per [ADR-021](docs/decisions/021-router-buddy-planes-and-routing-evidence.md) §4
and [ADR-023](docs/decisions/023-harness-integration-principles.md) §3, harnesses
only project their harness's native tool events into the shared Agent Client
Protocol vocabulary; the blackboard is the single authority that decides whether
an answer may publish. This module holds both sides of that seam:
:func:`normalize_tool_event` and :class:`ToolEventEvidence` summarize the
facts a native controller observed into one ``toolEvidence`` package, and
:func:`judge_tool_evidence` applies the one publication matrix. Neither side
records a verdict of its own, and no tool arguments, output bodies, prompts,
reasoning text or model claims are ever retained.
"""
from __future__ import annotations

from collections.abc import Iterable
import hashlib
import os
from pathlib import Path
from typing import Any

from .. import private_dirs
from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..paths import is_absolute_path
from .internal_models import MAX_COUNT, MAX_PATH_BYTES, check_text
from ..private_files import publish_bytes

#: The Agent Client Protocol tool-call categories. The fixed per-harness maps
#: only ever produce read, search, execute, edit, fetch and other; the remaining
#: names exist so a package carrying one is judged, never silently accepted.
ACP_CATEGORIES = ("read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other")
TOOL_EVENT_PHASES = ("start", "end")
TOOL_EVIDENCE_VERSION = 2
#: One call has a fixed identity, category set and phase set. Its serialized
#: row is bounded; the number of rows grows with the work and travels as a file.
MAX_CALL_BYTES = 32 * 1024

# Router runs alone are judged; Worker permissions stay native.
PERMISSION_TOOL_CATEGORIES = {
    "none": frozenset(),
    "read": frozenset({"read", "search", "execute", "fetch"}),
}

TOOL_EVIDENCE_UNVERIFIED = "router-tool-evidence-unverified"
TOOLS_FORBIDDEN = "router-tools-forbidden"

PACKAGE_FIELDS = frozenset({
    "version", "binding", "nativeIdentity", "streamComplete", "calls",
    "toolCalls", "unsettledToolCalls",
})
BINDING_FIELDS = frozenset({"harness", "taskId", "attemptId", "generation"})
EVENT_FIELDS = frozenset({"nativeIdentity", "callId", "toolName", "category", "phase"})
CALL_FIELDS = frozenset({"nativeIdentity", "callId", "categories", "phases",
                         "facts", "consistent", "endBeforeStart", "incomplete", "malformed", "belongsToRoot"})
CALL_FILE_FIELDS = frozenset({"path", "sha256", "bytes"})

#: The fixed native tool name/type classification per harness. A name a harness
#: did not register — an MCP server, a subagent, a dynamic tool or any
#: unrecognized name, including one that merely resembles an ACP category —
#: stays ``other``; it is never promoted to a legal category.
NATIVE_TOOL_CATEGORIES: dict[str, dict[str, str]] = {
    "dsh": {"read": "read", "glob": "search", "grep": "search", "bash": "execute",
            "write": "edit", "edit": "edit"},
    "claude": {
        "Read": "read", "LS": "read", "Skill": "read",
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
                   for key in ("harness", "taskId", "attemptId"))
            or type(value.get("generation")) is not int or value["generation"] < 0):
        raise BoardError(
            "INVALID_ARGUMENT",
            "binding must be exactly harness, taskId, attemptId and generation from the Python control file",
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


def normalize_tool_event(harness: Any, native_event: Any) -> dict | None:
    """Classify one controller-extracted native tool fact into the ACP vocabulary.

    ``native_event`` carries only the facts the native controller extracted —
    ``nativeIdentity``, ``callId``, ``toolName`` (or the native event ``type``
    where the harness names its calls that way) and ``phase``; every other key,
    including tool arguments, result bodies and model claims, is dropped.
    Returns ``None`` when the fact is model reasoning text rather than a tool
    call, and raises :class:`BoardError` for input no event can be built from.
    """
    table = NATIVE_TOOL_CATEGORIES.get(harness) if isinstance(harness, str) else None
    if table is None:
        raise BoardError("UNSUPPORTED_HARNESS", f"No fixed tool classification for harness {harness!r}")
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
    """Per-call facts over a Router attempt, with no limit on call count.

    Native roots come from creation receipts. A call retains its category and
    phase sets, at most three distinct name/category witnesses (Codex allows
    two projections of a call), and irreversible conflict/order/incompleteness flags. Replays
    cannot grow a call. The finished file has one bounded row per call.
    """

    def __init__(self, binding: dict, *, state_dir: Path):
        self.state_dir = state_dir
        self.binding = _binding(binding)
        self._calls: dict[tuple[str, str | None], dict] = {}
        self._root_identities: list[dict] = []
        self._stream_reported: bool | None = None
        self._finished = False
        self._late = False
        self._incomplete = False
        self._closed_roots: set[str] = set()
        self._excluded: frozenset = frozenset()

    def close_root(self, native_identity: dict) -> None:
        """Record an actual native turn end before transport drain finishes."""
        self._closed_roots.add(canonical_json(_identity(native_identity)))

    @property
    def tool_calls(self) -> int:
        """Observed unique identified starts, before verified delivery exclusion."""
        return sum(1 for call in self._calls.values()
                   if call["nativeIdentity"] and call["callId"] and "start" in call["countedPhases"])

    def _observe(self, identity: dict, call_id: str | None, name: str | None,
                 category: str, phase: str | None, *, incomplete: bool, malformed: bool = False) -> None:
        identity_key = canonical_json(identity)
        if self._finished or identity_key in self._closed_roots:
            self._late = True
        call = self._calls.setdefault((identity_key, call_id), {
            "nativeIdentity": identity, "callId": call_id, "categories": set(),
            "phases": set(), "countedPhases": set(), "facts": set(), "consistent": True,
            "endBeforeStart": False, "incomplete": False, "malformed": False,
        })
        call["categories"].add(category)
        if phase == "end" and "start" not in call["phases"]:
            call["endBeforeStart"] = True
        if phase is not None:
            call["phases"].add(phase)
        if not incomplete and phase is not None:
            call["countedPhases"].add(phase)
        fact = (name, category)
        # Three distinct facts suffice to witness every conflict, including
        # Codex's allowed pair. Further replays never grow the witness set.
        if len(call["facts"]) < 3:
            call["facts"].add(fact)
        call["consistent"] = _consistent_call_facts(self.binding["harness"], call["facts"])
        call["malformed"] |= malformed
        call["incomplete"] |= incomplete
        self._incomplete |= incomplete

    def observe_incomplete(self, harness: str, native_event: dict) -> None:
        """Keep bounded facts of a malformed event without inventing its IDs."""
        native_event = native_event if isinstance(native_event, dict) else {}
        def bounded(value):
            return value if isinstance(value, str) and 0 < len(value) <= 512 else None
        identity = native_event.get("nativeIdentity")
        identity = {key: bounded(value) for key, value in identity.items()
                    if key in NATIVE_ID_FIELDS and bounded(value)} if isinstance(identity, dict) else {}
        name, native_type = bounded(native_event.get("toolName")), bounded(native_event.get("type"))
        try:
            category = _classify(NATIVE_TOOL_CATEGORIES.get(harness, {}), name, native_type)
        except BoardError:
            category = "other"
        record = {"nativeIdentity": identity, "callId": bounded(native_event.get("callId")),
                  "toolName": name or native_type, "category": category or "other",
                  "phase": native_event.get("phase") if native_event.get("phase") in TOOL_EVENT_PHASES else None}
        try:
            _event(record)
            malformed = False
        except BoardError:
            malformed = True
        self._observe(identity, record["callId"], record["toolName"], record["category"], record["phase"],
                      incomplete=True, malformed=malformed)

    def observe(self, event: dict) -> None:
        """Collapse repeated projections, preserving conflicts and end order."""
        record = _event(event)
        self._observe(record["nativeIdentity"], record["callId"], record["toolName"],
                      record["category"], record["phase"], incomplete=False)

    def finish(self, native_identity: list, stream_complete: bool, *,
               exclude_calls: Iterable[tuple[str, str]] | None = None) -> dict:
        """Publish a digest-bound private call file and its bounded envelope.

        Only the complete set of root creation receipts can attribute calls to
        this attempt. Verified delivery exclusions name the full native identity
        and call ID; conflicting/incomplete facts cannot be hidden by exclusion.
        Every later close preserves any previous incomplete stream report.
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

    def _package(self) -> dict:
        calls = []
        started = unsettled = 0
        for key, call in self._calls.items():
            if key in self._excluded and call["consistent"] and not call["incomplete"]:
                continue
            identified = bool(call["nativeIdentity"] and call["callId"])
            started += identified and "start" in call["countedPhases"]
            unsettled += identified and call["countedPhases"] == {"start"}
            calls.append({
                "nativeIdentity": dict(call["nativeIdentity"]), "callId": call["callId"],
                "categories": sorted(call["categories"]), "phases": sorted(call["phases"]),
                "facts": [{"toolName": name, "category": category} for name, category in
                          sorted(call["facts"], key=lambda fact: (fact[0] or "", fact[1]))],
                "consistent": call["consistent"], "endBeforeStart": call["endBeforeStart"],
                "incomplete": call["incomplete"], "malformed": call["malformed"],
                "belongsToRoot": call["nativeIdentity"] in self._root_identities,
            })
        return {
            "version": TOOL_EVIDENCE_VERSION, "binding": dict(self.binding),
            "nativeIdentity": [dict(identity) for identity in self._root_identities],
            "streamComplete": bool(self._stream_reported) and not self._late and not self._incomplete,
            "calls": write_tool_calls(self.binding, calls, state_dir=self.state_dir),
            "toolCalls": started, "unsettledToolCalls": unsettled,
        }


def _calls_directory(state_dir: Path, binding: dict) -> Path:
    return private_dirs.attempt_root(state_dir, binding["harness"], binding["taskId"],
                                     binding["attemptId"]) / "tool-evidence"


def write_tool_calls(binding: dict, calls: Iterable[dict], *, state_dir: Path) -> dict:
    """Publish only growing facts as an immutable private JSONL file."""
    content = b"".join((canonical_json(call) + "\n").encode("utf-8") for call in calls)
    digest = hashlib.sha256(content).hexdigest()
    directory = private_dirs.ensure_private_dir(_calls_directory(state_dir, binding))
    path = directory / (digest + ".jsonl")
    publish_bytes(path, content, immutable=True)
    return {"path": str(path), "sha256": digest, "bytes": len(content)}


def validate_tool_evidence_package(evidence: Any) -> None:
    """The envelope boundary; file content is checked by the receiving judge."""
    if not isinstance(evidence, dict) or set(evidence) != PACKAGE_FIELDS:
        raise BoardError("INVALID_ARGUMENT", "the tool evidence package must carry exactly its fixed fields")
    if type(evidence["version"]) is not int or evidence["version"] != TOOL_EVIDENCE_VERSION:
        raise BoardError("INVALID_ARGUMENT", "the tool evidence version is not supported")
    binding = _binding(evidence["binding"])
    for key in ("taskId", "attemptId"):
        check_text(binding[key], "toolEvidence.binding." + key, maximum=128)
    if type(evidence["streamComplete"]) is not bool:
        raise BoardError("INVALID_ARGUMENT", "streamComplete must be a boolean")
    for key in ("toolCalls", "unsettledToolCalls"):
        if type(evidence[key]) is not int or not 0 <= evidence[key] <= MAX_COUNT:
            raise BoardError("INVALID_ARGUMENT", key + " must be a nonnegative count")
    roots = evidence["nativeIdentity"]
    if not isinstance(roots, list):
        raise BoardError("INVALID_ARGUMENT", "nativeIdentity must be a list of root identities")
    for identity in roots:
        _identity(identity)
    reference = evidence["calls"]
    if not isinstance(reference, dict) or set(reference) != CALL_FILE_FIELDS:
        raise BoardError("INVALID_ARGUMENT", "calls must carry exactly path, sha256 and bytes")
    check_text(reference["path"], "toolEvidence.calls.path", maximum=MAX_PATH_BYTES)
    digest = reference["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise BoardError("INVALID_ARGUMENT", "calls.sha256 must be a SHA256 digest")
    if type(reference["bytes"]) is not int or not 0 <= reference["bytes"] <= MAX_COUNT:
        raise BoardError("INVALID_ARGUMENT", "calls.bytes must be a nonnegative byte count")
    if not is_absolute_path(reference["path"]):
        raise BoardError("INVALID_ARGUMENT", "tool calls must name an absolute private file")


def _call_summary(value: Any, harness: str) -> dict:
    if not isinstance(value, dict) or set(value) != CALL_FIELDS:
        raise BoardError("INVALID_ARGUMENT", "a tool call summary must carry exactly its fixed fields")
    identity = value["nativeIdentity"]
    if not isinstance(identity, dict):
        raise BoardError("INVALID_ARGUMENT", "a call identity must carry only bounded native fields")
    if identity:
        _identity(identity)
    if value["callId"] is not None:
        check_text(value["callId"], "tool call id", maximum=512)
    for name, legal, minimum in (("categories", ACP_CATEGORIES, 1), ("phases", TOOL_EVENT_PHASES, 0)):
        items = value[name]
        if (not isinstance(items, list) or not minimum <= len(items) <= len(legal)
                or any(not isinstance(item, str) or item not in legal for item in items)
                or len(set(items)) != len(items)):
            raise BoardError("INVALID_ARGUMENT", "tool call " + name + " must be a set from its fixed vocabulary")
    for name in ("consistent", "endBeforeStart", "incomplete", "malformed", "belongsToRoot"):
        if type(value[name]) is not bool:
            raise BoardError("INVALID_ARGUMENT", "tool call " + name + " must be a boolean")
    facts = value["facts"]
    if not isinstance(facts, list) or not 1 <= len(facts) <= 3:
        raise BoardError("INVALID_ARGUMENT", "a call retains at most three distinct name/category facts")
    pairs = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {"toolName", "category"}:
            raise BoardError("INVALID_ARGUMENT", "a call fact must carry exactly toolName and category")
        if fact["toolName"] is None:
            if not value["malformed"]:
                raise BoardError("INVALID_ARGUMENT", "an unnamed call fact is malformed")
        else:
            check_text(fact["toolName"], "tool fact name", maximum=512)
        if fact["category"] not in value["categories"]:
            raise BoardError("INVALID_ARGUMENT", "a call fact category must be in the call's categories")
        pairs.add((fact["toolName"], fact["category"]))
    if len(pairs) != len(facts) or value["consistent"] != _consistent_call_facts(harness, pairs):
        raise BoardError("INVALID_ARGUMENT", "tool call consistency must match its distinct facts")
    if value["consistent"] and {category for _name, category in pairs} != set(value["categories"]):
        raise BoardError("INVALID_ARGUMENT", "a consistent call must carry exactly its observed category")
    return value


def read_tool_calls(evidence: dict, *, state_dir: Path) -> Iterable[dict]:
    """Read bounded rows, verifying file shape, length and digest at the boundary.

    No total call limit is imposed. A consumer must finish iteration before
    accepting any facts, because the final digest binds the complete file.
    """
    reference = evidence["calls"]
    if Path(reference["path"]) != _calls_directory(state_dir, evidence["binding"]) / (reference["sha256"] + ".jsonl"):
        raise BoardError("INVALID_ARGUMENT", "tool calls must name this attempt's private digest file")
    fd = private_dirs.open_regular_fd(Path(reference["path"]), os.O_RDONLY)
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "rb") as stream:
        while raw := stream.readline(MAX_CALL_BYTES + 1):
            size += len(raw)
            if len(raw) > MAX_CALL_BYTES or size > reference["bytes"] or not raw.endswith(b"\n"):
                raise BoardError("INVALID_ARGUMENT", "tool call file has an oversized or incomplete row")
            digest.update(raw)
            yield _call_summary(decode_strict_json(raw.decode("utf-8")), evidence["binding"]["harness"])
    if size != reference["bytes"] or digest.hexdigest() != reference["sha256"]:
        raise BoardError("INVALID_ARGUMENT", "tool call file does not match its length and digest")


def _consistent_call_facts(harness: str, facts: set) -> bool:
    if len(facts) == 1:
        return True
    # Codex exposes one call as both a typed item and its raw native tool name.
    if harness != "codex" or len(facts) != 2 or ("commandExecution", "execute") not in facts:
        return False
    return all(category == "execute" and NATIVE_TOOL_CATEGORIES["codex"].get(name) == category
               for name, category in facts)


def judge_tool_evidence(evidence: Any, permission: Any, *, state_dir: Path) -> str | None:
    """The one publication matrix over digest-bound per-call facts.

    A clearly forbidden call outranks an unclosed stream. Conflicting facts
    name no clear category and remain unverified. Missing roots, foreign calls,
    broken pairing/order, count mismatches and unreadable/corrupt files cannot
    establish complete evidence. No call-count cutoff affects the decision.
    """
    if not isinstance(permission, str) or permission not in PERMISSION_TOOL_CATEGORIES:
        return TOOL_EVIDENCE_UNVERIFIED
    try:
        validate_tool_evidence_package(evidence)
        if not evidence["nativeIdentity"]:
            return TOOL_EVIDENCE_UNVERIFIED
        allowed = PERMISSION_TOOL_CATEGORIES[permission]
        incomplete = not evidence["streamComplete"]
        forbidden = False
        malformed = False
        keys = set()
        started = unsettled = 0
        for call in read_tool_calls(evidence, state_dir=state_dir):
            key = (canonical_json(call["nativeIdentity"]), call["callId"])
            if key in keys:
                return TOOL_EVIDENCE_UNVERIFIED
            keys.add(key)
            identified = bool(call["nativeIdentity"] and call["callId"])
            malformed |= call["malformed"]
            phases = set(call["phases"])
            categories = set(call["categories"])
            if permission == "none" or call["consistent"] and not categories <= allowed:
                forbidden = True
            root = bool(call["nativeIdentity"]) and call["nativeIdentity"] in evidence["nativeIdentity"]
            if call["belongsToRoot"] != root:
                return TOOL_EVIDENCE_UNVERIFIED
            incomplete |= (not identified or not root or call["incomplete"] or not call["consistent"]
                           or call["endBeforeStart"] or phases != {"start", "end"})
            started += identified and "start" in phases
            unsettled += identified and phases == {"start"}
        if malformed:
            return TOOL_EVIDENCE_UNVERIFIED
        if forbidden:
            return TOOLS_FORBIDDEN
        if (incomplete or started != evidence["toolCalls"]
                or unsettled != evidence["unsettledToolCalls"] or unsettled):
            return TOOL_EVIDENCE_UNVERIFIED
        return None
    except (BoardError, OSError, ValueError, RecursionError):
        return TOOL_EVIDENCE_UNVERIFIED


__all__ = [
    "ACP_CATEGORIES", "BINDING_FIELDS", "CALL_FIELDS", "CALL_FILE_FIELDS", "EVENT_FIELDS", "MAX_CALL_BYTES",
    "NATIVE_TOOL_CATEGORIES", "NON_TOOL_NAMES", "PACKAGE_FIELDS", "PERMISSION_TOOL_CATEGORIES",
    "TOOL_EVIDENCE_UNVERIFIED", "TOOL_EVIDENCE_VERSION", "TOOLS_FORBIDDEN", "ToolEventEvidence",
    "judge_tool_evidence", "normalize_tool_event", "read_tool_calls", "validate_tool_evidence_package",
]
