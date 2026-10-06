"""The role-side live seam: every generic consumer's one way into a live channel.

ADR-025 step 2-C2. The blackboard's inquiry and the Worker runtime's activity
forwarding take a harness's live channel only through this seam and the harness
registry — never by importing a specific harness module. A channel is bound to
the complete execution identity of the real run's stored public request (the
``role-run-request.json`` the role controller writes and keeps); the consumer
verifies that identity against the ones it already holds — the board against
the run's own private control binding and governed turn input, the Worker
against the identity it received with the handle — so no invocation is ever
fabricated and nothing is derived from an arbitrary tool frame. The
role-private control binding supplies the attempt's own bridge credentials and
paths, and no token travels in any public value.

An extracted harness's live path has no direct-bridge fallback: when its stored
request is not readable or does not verify, the binding reports unavailable and
the consumer reports no update — never a second channel, never the old direct
facilities, never a stop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ..harnesses.live import LiveChannel
from ..harnesses.run_contract import RunIdentity, RunRequest, decode_run_request

#: The three states of one handle's live binding: the harness's registered run
#: module declares no live binding, the stored request verified and the channel
#: is bound, or the binding is pending or refused for this tick and may be
#: retried on the next one.
LIVE_BOUND = "bound"
LIVE_UNAVAILABLE = "unavailable"
LIVE_UNEXTRACTED = "unextracted"

__all__ = ["LIVE_BOUND", "LIVE_UNAVAILABLE", "LIVE_UNEXTRACTED", "build_live_channel",
           "handle_live_binding", "handle_live_channel", "stored_run_request"]


def build_live_channel(harness: str, request: RunRequest, *, credentials: Mapping | None,
                       activity_dir: str | Path, journal_path: str | None = None) -> LiveChannel | None:
    """One harness's live channel over its registered binding, or ``None``.

    ``None`` is the honest answer for a harness whose registered run module
    declares no live binding: the caller keeps its existing facilities path.
    The request must be this harness's own; the binding receives the request's
    complete execution identity and the attempt's own narrow materials.
    """
    if request.harness != harness:
        raise BoardError("INVALID_ARGUMENT", "a live channel binds its own harness", harness=harness)
    from ..harnesses.registry import live_binding

    binding = live_binding(harness)
    if binding is None:
        return None
    from ...protocol import activity as activity_protocol

    return binding(request.identity, credentials=dict(credentials or {}), journal_path=journal_path,
                   activity_path=activity_protocol.sidecar_path(activity_dir))


def stored_run_request(attempt_dir: str | Path) -> RunRequest | None:
    """The stored public run request of one attempt, verified against its own control.

    Reads the files the accepted role launch wrote — the public
    ``role-run-request.json`` and the private ``role-run-control.json`` with the
    governed turn input it names — and verifies the complete stored identity
    across them: the request's invocation must be the control's invocation, and
    its task, attempt, generation, turn id and input digest must be the actual
    turn input's. ``None`` is the honest answer for anything missing, unreadable
    or mismatched; nothing here fabricates an identity or derives one from a
    tool frame.
    """
    root = Path(attempt_dir)
    try:
        request = decode_run_request((root / "role-run-request.json").read_bytes())
        control = decode_strict_json((root / "role-run-control.json").read_bytes())
    except (OSError, ValueError, BoardError, RecursionError):
        return None
    if not isinstance(control, dict) or control.get("invocationId") != request.identity.invocation_id \
            or control.get("harness") != request.harness:
        return None
    input_file = control.get("inputFile")
    if not isinstance(input_file, str) or not input_file:
        return None
    try:
        turn_input = decode_strict_json(Path(input_file).read_bytes())
    except (OSError, ValueError, BoardError, RecursionError):
        return None
    if not isinstance(turn_input, dict):
        return None
    from .turn_io import input_hash

    if (request.identity.task_id != turn_input.get("taskId")
            or request.identity.attempt_id != turn_input.get("attemptId")
            or request.identity.generation != turn_input.get("generation")
            or request.identity.turn_id != turn_input.get("turnId")
            or request.identity.input_sha256 != input_hash(turn_input)):
        return None
    return request


def handle_live_binding(handle) -> tuple[str, LiveChannel | None]:
    """One owned role run's live binding state and channel, if bound.

    ``LIVE_UNEXTRACTED`` is the answer for a harness whose registered run module
    declares no live binding — the caller keeps its existing facilities path.
    ``LIVE_BOUND`` carries the channel decoded from the stored public request,
    whose complete identity must equal the identity the holder received with the
    handle. Anything else — a missing, unreadable, foreign or altered request —
    is ``LIVE_UNAVAILABLE``: no update this tick, the binding retried on the
    next one, never a look-alike channel and never the old direct facilities.
    """
    control = getattr(handle, "role_run_control", None)
    expected = getattr(handle, "role_run_identity", None)
    if not isinstance(control, dict) or not isinstance(expected, RunIdentity):
        return LIVE_UNEXTRACTED, None
    harness = control.get("harness")
    from ..harnesses.registry import live_binding

    if not isinstance(harness, str) or live_binding(harness) is None:
        return LIVE_UNEXTRACTED, None
    request_file = control.get("requestFile")
    if not isinstance(request_file, str) or not request_file:
        return LIVE_UNAVAILABLE, None
    try:
        request = decode_run_request(Path(request_file).read_bytes())
    except (OSError, ValueError, BoardError, RecursionError):
        return LIVE_UNAVAILABLE, None
    if request.identity != expected or request.harness != harness:
        return LIVE_UNAVAILABLE, None
    inquiry = control.get("inquiry") if isinstance(control.get("inquiry"), dict) else {}
    channel = build_live_channel(harness, request, credentials=inquiry,
                                 activity_dir=control.get("directory"),
                                 journal_path=inquiry.get("resultsPath"))
    if channel is None:
        return LIVE_UNAVAILABLE, None
    return LIVE_BOUND, channel


def handle_live_channel(handle) -> LiveChannel | None:
    """The bound live channel of one owned role run, or ``None`` while not bound."""
    state, channel = handle_live_binding(handle)
    return channel if state == LIVE_BOUND else None
