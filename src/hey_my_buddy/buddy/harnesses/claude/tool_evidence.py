"""Claude stream-json tool facts projected into the shared evidence vocabulary.

The read-only controller hands every native frame to :class:`ReadOnlyToolEvidence`
before any other check filters or rejects it. An ``assistant`` ``tool_use`` block
and a stream ``content_block_start`` projection are starts, a ``user``
``tool_result`` is the one real execution end, and a stream ``block_stop`` is
frame transport, never tool completion. Facts are only projected: foreign
sessions, subagent substreams, unknown tools and conflicting replays are all
kept as facts, a fact whose IDs cannot be proven is retained unnamed through
``observe_incomplete`` rather than given invented ones, and the module never
judges what only the blackboard may judge.

The CLI's own ``--json-schema`` value carrier is the built-in ``StructuredOutput``
tool (ADR-012, Host-verified on the real CLI). A caller that armed the collector
with that fact may verify the delivery call against the final structured value
and exclude it from the ordinary tool facts through the shared package's own
``exclude_calls`` seam — never by a name whitelist: the call must sit on a
handshake-confirmed root, carry the built-in's exact name with no conflicting
fact, and its delivered input must equal the run's final structured output.
"""
from __future__ import annotations

import hashlib

from ....errors import BoardError
from ....json_codec import canonical_json
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event

ADAPTER = "claude"
#: The exact name of the built-in tool the CLI's ``--json-schema`` contributes
#: by itself. An MCP server's tool never bears this bare name: the CLI spells
#: mounted tools ``mcp__<server>__<tool>``, so the exact match is one native
#: fact of the built-in source, never a whitelist of its own.
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
#: The identity bound the shared package itself enforces; IDs are bounded here
#: the same way so nothing is collected that could never be observed.
_MAX_ID_LENGTH = 512
#: A start's name is correlated to its call so a later ``tool_result`` can name
#: its own end; the bound keeps a runaway stream growing the table without end.
_MAX_CORRELATED_CALLS = 512
#: Delivery candidates and their retained inputs are bounded the same way: a
#: pathological retry loop cannot grow the collector without end, and a dropped
#: input simply leaves its call unverifiable — it stays an ordinary tool fact.
_MAX_DELIVERY_CANDIDATES = 16
_MAX_DELIVERY_INPUTS = 8
_MAX_DELIVERY_INPUT_BYTES = 8 * 1024 * 1024

#: Sentinel for a delivery use whose input this collector never captured (a
#: stream-only projection, or an input dropped by the retention bound).
_INPUT_NOT_CAPTURED = object()


class ReadOnlyToolEvidence:
    """One read-only call's tool-fact collector over the shared package.

    Root identities come only from the native ``system/init`` handshake, never
    from the preallocated control session or from observed events. A root-level
    frame without its own ``session_id`` joins the one handshake-confirmed root
    while the stream has shown no other session, and frames under a
    ``parent_tool_use_id`` are a substream that never inherits the root
    identity. :meth:`finish` is called once, after the transport's observed end:
    a complete stream is claimed only when the stream itself closed.
    """

    def __init__(self, binding: dict, *, native_schema_delivery: bool = False):
        self._evidence = ToolEventEvidence(binding)
        self._roots: list[dict] = []
        self._foreign_session = False
        self._correlated: dict[tuple, str] = {}
        self._package: dict | None = None
        # The delivery-candidate facts, kept only when the caller armed the
        # collector with this run's own ``--json-schema`` command line. Each
        # entry is keyed the way the shared package keys a call; the candidate
        # never changes the projected facts, only their verified exclusion.
        # ``settle_delivery`` freezes that verification once, before the settled
        # facts reach the role: from then on the live count and the finished
        # package hold exactly the same verified calls and nothing else.
        self._delivery_armed = native_schema_delivery is True
        self._delivery_candidates: dict[tuple[str, str], dict] = {}
        self._delivery_input_order: list[tuple[str, str]] = []
        self._delivery_incomplete: set[tuple[str, str]] = set()
        self._delivery_settled = False
        self._delivery_verified: tuple[tuple[str, str], ...] = ()

    @property
    def tool_calls(self) -> int:
        """The unified started-call count the runtime budgets are enforced against.

        Before settlement an exemptable candidate — root-attributed, exactly
        named, unconflicted, complete — waits for its final association rather
        than charging a budget a legal zero-tool delivery would break. Once
        :meth:`settle_delivery` has frozen the verification, the count holds
        exactly the verified delivery calls: every candidate that did not prove
        itself is released, so the count the role last saw and the finished
        package always agree.
        """
        total = self._evidence.tool_calls
        if not self._delivery_candidates:
            return total
        if self._delivery_settled:
            return max(0, total - len(self._delivery_verified))
        held = sum(1 for candidate in self._delivery_candidates.values()
                   if self._exemptable(candidate))
        return max(0, total - held)

    def observe_frame(self, frame: dict) -> None:
        """Collect one stream frame's tool facts; never raises, never filters."""
        if not isinstance(frame, dict):
            return
        if frame.get("type") == "system" and frame.get("subtype") == "init":
            session = _native_id(frame.get("session_id"))
            if session is not None:
                identity = {"sessionId": session}
                if identity not in self._roots:
                    self._roots.append(identity)
            return
        parent = _native_id(frame.get("parent_tool_use_id"))
        identity, provable = self._identity(frame, parent)
        if frame.get("type") == "result" and parent is None and identity in self._roots:
            self._evidence.close_root(identity)
            return
        for call_id, name, phase, use_input in self._facts(frame):
            if phase == "start":
                self._observe_delivery_use(identity, provable, parent, call_id, name, use_input)
            self._record(identity, provable, call_id, name, phase)

    def finish(self, stream_complete: bool, *,
               exclude_calls: tuple[tuple[str, str], ...] | None = None) -> dict:
        """Close the collector once and return the ``toolEvidence`` fact package.

        The first finish fixes the package: a later failure re-reports it, and
        a stream that did not provably close can never be upgraded afterwards.
        ``exclude_calls`` carries the delivery calls the caller verified against
        the final structured value — the shared package's own exclusion seam;
        without it the package is exactly what every current caller publishes.
        """
        if self._package is None:
            self._package = self._evidence.finish(list(self._roots), stream_complete is True,
                                                  exclude_calls=exclude_calls)
        return self._package

    def settle_delivery(self, delivered, confirmed_root: dict) -> tuple[tuple[str, str], ...]:
        """Freeze the delivery verification once, against the run's final value.

        The caller supplies the structured output the terminal result frame
        carried and the session the initialize handshake itself confirmed, and
        calls this before the settled facts reach the role observer: the
        verification and the release of every unverified candidate both happen
        here, so the settled count the role acts on and the finished package
        report the same classification. A candidate proves itself only by
        association: handshake-confirmed root, the built-in's exact name with
        no conflicting fact or input, a complete call, and a captured use input
        canonically equal to the delivered value. Anything less — a bare name,
        a clashing name, a second different input (remembered by fingerprint
        even past the retention bound), a substream, a foreign root, an input
        whose full text the bound dropped and so can no longer prove the
        value, no final value at all — verifies nothing, and the call stays an
        ordinary tool fact for the role and the blackboard to judge. The tuple
        keeps observation order, so the last entry is the final delivering
        call.
        """
        if self._delivery_settled:
            return self._delivery_verified
        self._delivery_settled = True
        self._delivery_verified = self._verified(delivered, confirmed_root)
        return self._delivery_verified

    def _verified(self, delivered, confirmed_root) -> tuple[tuple[str, str], ...]:
        if not self._delivery_armed or not isinstance(confirmed_root, dict) or not confirmed_root:
            return ()
        try:
            delivered_text = canonical_json(delivered)
            root_text = canonical_json(confirmed_root)
        except (ValueError, TypeError, RecursionError):
            return ()
        verified = []
        for (identity_text, call_id), candidate in self._delivery_candidates.items():
            if identity_text != root_text or not self._exemptable(candidate):
                continue
            if candidate["input"] is not _INPUT_NOT_CAPTURED and candidate["input"] == delivered_text:
                verified.append((identity_text, call_id))
        return tuple(verified)

    @staticmethod
    def _exemptable(candidate: dict) -> bool:
        """One candidate still able to prove itself the built-in delivery.

        A clashing name, a second different full input, or an incomplete fact
        on the same native call each refuse the exemption on their own: the
        shared package's own exclusion guard would refuse them too, so the
        candidate is never held out of any count in the first place.
        """
        return len(candidate["names"]) == 1 and not candidate["conflict"] and not candidate["incomplete"]

    def _observe_delivery_use(self, identity: dict, provable: bool, parent: str | None,
                              call_id, name, use_input) -> None:
        """Keep one candidate fact for the built-in delivery, projected facts untouched."""
        if not self._delivery_armed or name != STRUCTURED_OUTPUT_TOOL or call_id is None:
            return
        if not provable or not identity or identity not in self._roots or parent is not None:
            # A subagent substream, a foreign session or an unattributable
            # frame is a fact like any other; the built-in delivery mechanism
            # belongs to the root the handshake confirmed.
            return
        key = (canonical_json(identity), call_id)
        candidate = self._delivery_candidates.get(key)
        if candidate is None:
            if len(self._delivery_candidates) >= _MAX_DELIVERY_CANDIDATES:
                return
            candidate = {"names": set(), "input": _INPUT_NOT_CAPTURED,
                         "first_fp": None, "conflict": False, "incomplete": False}
            # A call that already started under another name is conflicted from
            # birth: the delivery exemption never hides a second, clashing fact.
            prior = self._correlated.get((tuple(sorted(identity.items())), call_id))
            if prior is not None and prior != name:
                candidate["names"].add(prior)
            # A broken fact already seen on this call — an uncorrelatable end —
            # makes the call incomplete; the shared exclusion guard would
            # refuse it, so it is never held out of a count either.
            if key in self._delivery_incomplete:
                candidate["incomplete"] = True
            self._delivery_candidates[key] = candidate
        candidate["names"].add(name)
        if use_input is not None:
            self._retain_delivery_input(key, candidate, use_input)

    def _retain_delivery_input(self, key: tuple[str, str], candidate: dict, use_input) -> None:
        """Keep one use input as bounded canonical text; any different one conflicts.

        The same call's repeated full projections must agree, whatever their
        order and whatever the retention bound did in between: the fingerprint
        of the first complete input stays on the candidate forever (one sha256
        over the canonical text — the bound may drop the proof, never the
        memory of a conflict), so a second, different complete input refuses
        the exemption even after the first's text was evicted, while the same
        input again may re-prove the value by recapturing its full text.
        """
        try:
            text = canonical_json(use_input)
        except (ValueError, TypeError, RecursionError):
            return
        if len(text) > _MAX_DELIVERY_INPUT_BYTES:
            return
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        first = candidate["first_fp"]
        if first is None:
            candidate["first_fp"] = fingerprint
        elif fingerprint != first:
            candidate["conflict"] = True
        if candidate["input"] is not _INPUT_NOT_CAPTURED:
            return
        while len(self._delivery_input_order) >= _MAX_DELIVERY_INPUTS:
            oldest = self._delivery_input_order.pop(0)
            held = self._delivery_candidates.get(oldest)
            if held is not None:
                held["input"] = _INPUT_NOT_CAPTURED
        candidate["input"] = text
        self._delivery_input_order.append(key)

    def _identity(self, frame: dict, parent: str | None) -> tuple[dict, bool]:
        """The identity this frame's facts bind to, and whether that is provable."""
        if parent is not None:
            # A substream keeps its native parent-call identity beside any
            # carried session and never inherits the root session's.
            identity = {"callId": parent}
            session = _native_id(frame.get("session_id"))
            if session is not None:
                identity = {"sessionId": session, "callId": parent}
            return identity, True
        session = _native_id(frame.get("session_id"))
        if session is not None:
            if len(self._roots) == 1 and self._roots[0] == {"sessionId": session}:
                return dict(self._roots[0]), True
            self._foreign_session = True
            return {"sessionId": session}, True
        # A single handshake-confirmed root may adopt the frames the CLI sent
        # without a per-frame session, until another session appears.
        if len(self._roots) == 1 and not self._foreign_session:
            return dict(self._roots[0]), True
        return {}, False

    @staticmethod
    def _facts(frame: dict):
        """The native tool facts one frame carries, as (call id, name, phase, use input)."""
        frame_type = frame.get("type")
        if frame_type == "assistant":
            for block in _content(frame):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    yield (_native_id(block.get("id")), _native_id(block.get("name")), "start",
                           block.get("input"))
        elif frame_type == "user":
            for block in _content(frame):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    yield _native_id(block.get("tool_use_id")), None, "end", None
        elif frame_type == "stream_event":
            event = frame.get("event")
            block = event.get("content_block") if isinstance(event, dict) else None
            # Only a real tool-use start projects here; the tool_use block type
            # is a frame envelope, never a native operation type. A stream
            # projection carries no complete input: the full assistant block is
            # the one place the delivered value is observed.
            if isinstance(event, dict) and event.get("type") == "content_block_start" \
                    and isinstance(block, dict) and block.get("type") == "tool_use":
                yield _native_id(block.get("id")), _native_id(block.get("name")), "start", None

    def _record(self, identity: dict, provable: bool, call_id, name, phase) -> None:
        key = None
        if call_id is not None:
            key = (tuple(sorted(identity.items())), call_id)
            if phase == "end":
                # An end carries no name of its own: the observed start of the
                # same native call names it, and an uncorrelatable end stays
                # unnamed instead of borrowing one.
                name = self._correlated.get(key)
        fact = {"nativeIdentity": identity, "callId": call_id, "phase": phase}
        if name is not None:
            fact["toolName"] = name
        try:
            event = normalize_tool_event(ADAPTER, fact)
        except BoardError:
            # A fact no event can be built from makes its call incomplete: the
            # shared exclusion guard refuses such a call, so a delivery
            # candidate on it is never held or verified either. Both orders —
            # the broken fact before or after the candidate's registration —
            # reach the same flag.
            if call_id is not None:
                shared_key = (canonical_json(identity), call_id)
                if len(self._delivery_incomplete) < _MAX_CORRELATED_CALLS:
                    self._delivery_incomplete.add(shared_key)
                candidate = self._delivery_candidates.get(shared_key)
                if candidate is not None:
                    candidate["incomplete"] = True
            self._evidence.observe_incomplete(ADAPTER, fact)
            return
        if event is None:
            return
        self._evidence.observe(event)
        if phase == "start" and key is not None:
            if self._delivery_candidates:
                # Every observed start name of a candidate call belongs to its
                # candidate, so a later clashing fact under the same native id
                # refuses the exemption instead of hiding behind it.
                candidate = self._delivery_candidates.get((canonical_json(identity), call_id))
                if candidate is not None:
                    candidate["names"].add(event["toolName"])
            if len(self._correlated) < _MAX_CORRELATED_CALLS:
                self._correlated[key] = event["toolName"]


def _native_id(value) -> str | None:
    """A native-provided identifier exactly as the shared package accepts it."""
    return value if isinstance(value, str) and 0 < len(value) <= _MAX_ID_LENGTH else None


def _content(frame: dict) -> list:
    message = frame.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []
