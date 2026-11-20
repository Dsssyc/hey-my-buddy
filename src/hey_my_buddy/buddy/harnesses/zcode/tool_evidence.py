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
import json
import os
from pathlib import Path

from ....paths import native_path
from ....private_files import open_read
from ....protocol.tool_evidence import (
    PERMISSION_TOOL_CATEGORIES, ToolEventEvidence, classify_tool_name, normalize_tool_event,
)
from ..session_receipts import snapshot_result_failed
from ..snapshot_mount import ObservedSnapshotCalls

HARNESS = "zcode"

#: The real session/event ``tool.updated`` payload kinds: ``scheduled`` opens a
#: call and ``result``/``error`` closes it. Any other kind is an unknown frame
#: that is retained as an incomplete fact, never guessed into a phase.
_START_KINDS = frozenset({"scheduled"})
_END_KINDS = frozenset({"result", "error"})
_METADATA_PHASES = ("scheduled", "started", "progress", "completed", "failed")
_STREAMED_TOOL_KINDS = ("tool_call", "tool_input_start", "tool_input_delta", "tool_input_end")


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


def _child_tool_frame(params: dict) -> bool:
    return any(record.get(field) not in (None, False, "")
               for record in (params, params.get("payload")) if isinstance(record, dict)
               for field in ("source", "parentToolCallId", "childToolCallId", "childSessionId", "agentId", "background"))


def _tool_call_id(frame: dict) -> str | None:
    call_id = frame.get("toolCallId")
    return call_id if isinstance(call_id, str) and 0 < len(call_id) <= 512 else None


class ZcodeToolFacts:
    """One no-tool call's tool-fact projection across its correction sessions.

    The same collector spans the original root session and the one root session
    a format correction creates after the first is closed, so identities and
    call counts accumulate over the attempt's shared deadline. ``violation``
    records that the controller refused a tool interaction whose shape the
    projection cannot name, keeping the receipt's call count at least one.
    """

    def __init__(self, binding: dict, native_log_dir: Path | None = None, *, state_dir: Path,
                 session_tools: Iterable[str] = ()):
        self.evidence = ToolEventEvidence(binding, state_dir=state_dir,
                                          session_service_tools=session_tools)
        self.roots: list[dict] = []
        self.close_pending = False
        self.violation = False
        #: The mounted snapshot service's qualified tool names, with the
        #: observed native calls the driver later verifies against the ledger.
        self.session_tools = frozenset(session_tools)
        self.snapshot_calls = ObservedSnapshotCalls(names=self.session_tools)
        self._streamed_names: dict[tuple[str, str, str], str] = {}
        self._snapshot_inputs: dict[tuple[str, str, str], tuple[str, dict]] = {}
        self._session_starts: set[tuple[str, str, str]] = set()
        self._names: dict[tuple[str, str, str], str] = {}
        self._ended = set()
        self._closed = set()
        self._canonical_starts = set()
        self._metadata_starts = set()
        self._metadata_outcomes = {}
        self._outcomes = {}
        self._batched = set()
        self._denied = set()
        self._pending_denial_batches = []
        self._native_log_dir = native_log_dir
        self._log_offsets: dict[str, int] = {}

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
            self.read_denials()
            identity = _identity_of(params)
            if identity in self.roots:
                self.evidence.close_root(identity)
                self._closed.add((identity['sessionId'], identity['turnId']))
            return
        if isinstance(params, dict) and params.get("type") == "model.streaming":
            self._streamed_tool(params)
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
                self._names.setdefault(key, name)
                if kind == "scheduled":
                    self._canonical_starts.add(key)
                if name in self.session_tools:
                    # A mounted snapshot call keeps its native arguments beside
                    # the shared facts; the driver verifies them against the
                    # signed ledger after the run ends.
                    self._session_starts.add(key)
                    streamed = self._snapshot_inputs.get(key)
                    arguments = payload.get("input", payload.get("arguments"))
                    if arguments is None and streamed is not None and streamed[0] == name:
                        arguments = streamed[1]
                    self.snapshot_calls.observe(identity, call_id, name, arguments=arguments, started=True)
        elif phase == "end" and identity is not None and call_id is not None:
            key = (identity["sessionId"], identity["turnId"], call_id)
            name = self._names.get(key)
            self._ended.add(key)
            result = payload.get("result")
            failed = None
            if name in self.session_tools:
                failed = snapshot_result_failed(result)
                self.snapshot_calls.observe(identity, call_id, name, result=result, completed=True,
                                            ok=(not failed) if failed is not None else None)
            native_outcome = False if kind == "error" else result.get("success") if isinstance(result, dict) else None
            native_outcome = native_outcome if type(native_outcome) is bool else None
            if key in self._outcomes and self._outcomes[key] is not native_outcome:
                self._incomplete(params, identity, call_id, phase, name)
            self._outcomes.setdefault(key, native_outcome)
            if key in self._metadata_outcomes and self._metadata_outcomes[key] is not native_outcome:
                self._incomplete(params, identity, call_id, phase, name)
            outcome = not failed if failed is not None else native_outcome
        if identity is not None and phase is not None and call_id is not None and name is not None:
            # The envelope type is not an operation type and is never passed as
            # one; the shared table classifies the scheduled tool name alone.
            self.evidence.observe(normalize_tool_event(HARNESS, {
                "nativeIdentity": dict(identity), "callId": call_id, "toolName": name,
                "phase": phase,
                "outcome": ("failure" if outcome is False else "success" if outcome is True
                            else "unknown") if phase == "end" else "unknown"}))
            return
        self._incomplete(params, identity, call_id, phase, name)

    def _streamed_tool(self, params: dict) -> None:
        identity = _identity_of(params)
        payload = params.get("payload")
        if (identity not in self.roots or not isinstance(payload, dict)
                or _child_tool_frame(params)
                or payload.get("kind") not in ("tool_input_start", "tool_call")):
            return
        call_id, name = _tool_call_id(payload), payload.get("toolName")
        if call_id is None or not isinstance(name, str) or not name:
            return
        key = (identity["sessionId"], identity["turnId"], call_id)
        self._streamed_names.setdefault(key, name)
        arguments = payload.get("input")
        if payload["kind"] == "tool_call" and name in self.session_tools and isinstance(arguments, dict):
            self._snapshot_inputs[key] = (name, arguments)

    def _metadata_key(self, params: dict, identity: dict | None,
                      call_id: str | None) -> tuple[str, str, str] | None:
        if call_id is None:
            return None
        if identity is not None:
            return identity["sessionId"], identity["turnId"], call_id
        if params.get("turnId") is None and isinstance(params.get("sessionId"), str):
            matches = [key for key in self._names if key[0] == params["sessionId"] and key[2] == call_id]
            if len(matches) == 1:
                return matches[0]
        return None

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
        call_id = _tool_call_id(params)
        raw = params.get("toolName")
        name = raw if isinstance(raw, str) and 0 < len(raw) <= 512 else None
        key = self._metadata_key(params, identity, call_id)
        if _child_tool_frame(params):
            self._incomplete(params, identity, call_id, None, name)
            return
        if (phase not in _METADATA_PHASES
                or key is None or key[:2] in self._closed or key in self._ended
                or raw is not None and name is None):
            self._incomplete(params, identity, call_id, None, name)
            return
        if phase == "scheduled":
            known = self._names.get(key)
            if (name is None or known is not None and name != known
                    or known is None and identity is None):
                self._incomplete(params, identity, call_id, "start", name)
                return
            self._names.setdefault(key, name)
            self._metadata_starts.add(key)
            if identity is not None:
                if name in self.session_tools:
                    self._session_starts.add(key)
                self.evidence.observe(normalize_tool_event(HARNESS, {
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

    def observe_denial(self, record: dict) -> None:
        """Close the named call recorded by the native permission executor."""
        identity = _identity_of(record)
        call_id = record.get("toolCallId")
        context = record.get("context")
        name = context.get("toolName") if isinstance(context, dict) else None
        call_id = call_id if isinstance(call_id, str) and 0 < len(call_id) <= 512 else None
        name = name if isinstance(name, str) and 0 < len(name) <= 512 else None
        key = (identity["sessionId"], identity["turnId"], call_id) if identity and call_id else None
        if (record.get("event") != "tool.permission.denied" or record.get("status") != "failed"
                or not isinstance(context, dict) or context.get("decision") != "deny"
                or identity not in self.roots or key is None or name is None
                or self._names.get(key) != name or key not in self._canonical_starts
                or key[:2] in self._closed):
            self._incomplete(record, identity, call_id, None, name)
            return
        if key in self._denied:
            return
        if key in self._ended:
            self._incomplete(record, identity, call_id, None, name)
            return
        self._ended.add(key)
        self._denied.add(key)
        self._outcomes[key] = False
        if key in self._metadata_outcomes and self._metadata_outcomes[key] is not False:
            self._incomplete(record, identity, call_id, "end", name)
        self.evidence.observe(normalize_tool_event(HARNESS, {
            "nativeIdentity": identity, "callId": call_id, "toolName": name,
            "phase": "end", "outcome": "failure"}))

    def read_denials(self) -> None:
        """Project permission refusals from this invocation's native JSONL log."""
        if self._native_log_dir is None or not native_path(self._native_log_dir).exists():
            return
        for name in sorted(path.name for path in native_path(self._native_log_dir).glob("zcode-*.jsonl")):
            path = self._native_log_dir / name
            try:
                with open_read(path) as stream:
                    details = os.fstat(stream.fileno())
                    offset = self._log_offsets.get(path.name, 0)
                    if details.st_size < offset:
                        raise ValueError("native log was truncated")
                    stream.seek(offset)
                    while line := stream.readline():
                        if not line.endswith(b"\n"):
                            break
                        self._log_offsets[path.name] = stream.tell()
                        try:
                            record = json.loads(line)
                        except (UnicodeDecodeError, ValueError):
                            self._incomplete({}, None, None, None, None)
                            continue
                        if not isinstance(record, dict):
                            self._incomplete({}, None, None, None, None)
                        elif record.get("event") == "tool.permission.denied":
                            self.observe_denial(record)
            except (OSError, ValueError):
                self._incomplete({}, None, None, None, None)

    def _batch(self, params: dict, identity: dict | None, payload: dict, *, deferred: bool = False) -> None:
        """Validate an aggregate acknowledgment without creating tool events."""
        ids = payload.get("toolCallIds")
        success, error = payload.get("successCount"), payload.get("errorCount")
        valid = (identity in self.roots and identity is not None
                 and (deferred or (identity["sessionId"], identity["turnId"]) not in self._closed)
                 and isinstance(ids, list) and len(ids) > 0
                 and all(isinstance(value, str) and 0 < len(value) <= 512 for value in ids)
                 and len(set(ids)) == len(ids)
                 and type(success) is int and success >= 0 and type(error) is int and error >= 0)
        keys = [(identity["sessionId"], identity["turnId"], value) for value in ids] if valid else []
        missing = [key for key in keys if key not in self._ended]
        if (not deferred and valid and missing and error > 0 and len(missing) <= error
                and all(key in self._canonical_starts and key not in self._batched for key in keys)):
            self._pending_denial_batches.append((params, identity, payload, frozenset(missing)))
            return
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
        self.evidence.observe_incomplete(HARNESS, {
            "nativeIdentity": bounded, "callId": call_id, "toolName": name, "phase": phase})

    # -- receipt ---------------------------------------------------------------
    def explains_tool_frame(self, message: dict, *, read: bool) -> bool:
        params = message.get("params")
        if not isinstance(params, dict) or _child_tool_frame(params):
            return False
        method = message.get("method")
        identity = _identity_of(params)
        names = self._names if read else self._session_starts
        if (method == "computer-use/operation-event" and params.get("kind") in ("tool-scheduled", "tool-started")
                or method == "v4/telemetry/event" and params.get("kind") == "tool.lifecycle"
                and params.get("phase") in _METADATA_PHASES):
            key = self._metadata_key(params, identity, _tool_call_id(params))
            if key not in names or {"sessionId": key[0], "turnId": key[1]} not in self.roots:
                return False
            known, name = self._names[key], params.get("toolName")
            return (name is None or name == known) and (read or known in self.session_tools)

        if identity not in self.roots:
            return False
        root = (identity["sessionId"], identity["turnId"])
        payload = params.get("payload")
        kind = params.get("type")
        if (method == "v4/telemetry/event" and params.get("kind") == "turn.terminal"
                or method == "session/event" and kind == "turn.completed"):
            counts = [record[field] for record in (params, payload) if isinstance(record, dict)
                      for field in ("toolCallCount", "toolCalls") if field in record]
            observed = sum(key[:2] == root for key in names)
            return bool(counts) and all(type(count) is int and 0 <= count <= observed for count in counts)

        if method != "session/event" or not isinstance(payload, dict):
            return False
        if kind == "model.streaming" and payload.get("kind") in _STREAMED_TOOL_KINDS:
            if root in self._closed:
                return False
            call_id = _tool_call_id(payload)
            if call_id is None:
                return False
            known = self._streamed_names.get((*root, call_id))
            raw_name = payload.get("toolName")
            name = known if raw_name is None else raw_name
            return (isinstance(name, str) and (known is None or name == known)
                    and (name in self.session_tools or read and classify_tool_name(HARNESS, name)
                         in PERMISSION_TOOL_CATEGORIES["read"]))
        if kind != "tool.updated":
            return False
        ids = payload.get("toolCallIds") if payload.get("kind") == "batch" else [payload.get("toolCallId")]
        return isinstance(ids, list) and bool(ids) and all(
            isinstance(call_id, str) and (*root, call_id) in names for call_id in ids)

    @property
    def tool_calls(self) -> int:
        """Observed unique call starts, for the receipt's cumulative count."""
        return self.evidence.tool_calls

    @property
    def session_tool_calls(self) -> int:
        """Observed starts of mounted snapshot service calls, verbatim."""
        return len(self._session_starts)

    def finish(self, stream_complete: bool, *,
               exclude_calls: Iterable[tuple[str, str]] | None = None,
               verified_session_calls: Iterable[tuple[str, str]] = ()) -> dict:
        """Close the collector and return the receipt's ``toolEvidence`` package.

        ``stream_complete`` is true only when the real native stream drained to
        EOF, every root session closed with an acknowledged close, and the owned
        process stopped; the collector keeps its facts verbatim either way.
        ``exclude_calls`` names the verified delivery calls as the collector's
        own full ``(canonical_json(native_identity), callId)`` keys: the shared collector
        removes exactly those calls' events and counts from its complete call
        table, while every unverified, foreign or conflicting fact the
        projection observed stays. ``verified_session_calls`` names the mounted
        snapshot calls whose signed ledger receipts the driver verified in both
        directions; only those rows carry ``sessionServiceVerified``.
        """
        self.read_denials()
        for params, identity, payload, missing in self._pending_denial_batches:
            if missing <= self._denied:
                self._batch(params, identity, payload, deferred=True)
            else:
                self._incomplete(params, identity, None, None, None)
        for key in self._metadata_starts - self._canonical_starts:
            self._incomplete({"sessionId": key[0], "turnId": key[1]},
                             {"sessionId": key[0], "turnId": key[1]}, key[2], None, self._names.get(key))
        return self.evidence.finish(list(self.roots), stream_complete is True,
                                    exclude_calls=exclude_calls,
                                    verified_session_calls=verified_session_calls)


__all__ = ["HARNESS", "ZcodeToolFacts"]
