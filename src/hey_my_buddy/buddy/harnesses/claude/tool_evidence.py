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
"""
from __future__ import annotations

from ....errors import BoardError
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event

ADAPTER = "claude"
#: The identity bound the shared package itself enforces; IDs are bounded here
#: the same way so nothing is collected that could never be observed.
_MAX_ID_LENGTH = 512
#: A start's name is correlated to its call so a later ``tool_result`` can name
#: its own end; the bound keeps a runaway stream growing the table without end.
_MAX_CORRELATED_CALLS = 512


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

    def __init__(self, binding: dict):
        self._evidence = ToolEventEvidence(binding)
        self._roots: list[dict] = []
        self._foreign_session = False
        self._correlated: dict[tuple, str] = {}
        self._package: dict | None = None

    @property
    def tool_calls(self) -> int:
        """The unified started-call count the runtime budgets are enforced against."""
        return self._evidence.tool_calls

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
        for call_id, name, phase in self._facts(frame):
            self._record(identity, provable, call_id, name, phase)

    def finish(self, stream_complete: bool) -> dict:
        """Close the collector once and return the ``toolEvidence`` fact package.

        The first finish fixes the package: a later failure re-reports it, and
        a stream that did not provably close can never be upgraded afterwards.
        """
        if self._package is None:
            self._package = self._evidence.finish(list(self._roots), stream_complete is True)
        return self._package

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
        """The native tool facts one frame carries, as (call id, name, phase)."""
        frame_type = frame.get("type")
        if frame_type == "assistant":
            for block in _content(frame):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    yield _native_id(block.get("id")), _native_id(block.get("name")), "start"
        elif frame_type == "user":
            for block in _content(frame):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    yield _native_id(block.get("tool_use_id")), None, "end"
        elif frame_type == "stream_event":
            event = frame.get("event")
            block = event.get("content_block") if isinstance(event, dict) else None
            # Only a real tool-use start projects here; the tool_use block type
            # is a frame envelope, never a native operation type.
            if isinstance(event, dict) and event.get("type") == "content_block_start" \
                    and isinstance(block, dict) and block.get("type") == "tool_use":
                yield _native_id(block.get("id")), _native_id(block.get("name")), "start"

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
            self._evidence.observe_incomplete(ADAPTER, fact)
            return
        if event is None:
            return
        self._evidence.observe(event)
        if phase == "start" and key is not None and len(self._correlated) < _MAX_CORRELATED_CALLS:
            self._correlated[key] = event["toolName"]


def _native_id(value) -> str | None:
    """A native-provided identifier exactly as the shared package accepts it."""
    return value if isinstance(value, str) and 0 < len(value) <= _MAX_ID_LENGTH else None


def _content(frame: dict) -> list:
    message = frame.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []
