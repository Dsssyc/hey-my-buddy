"""The small fragments the four native run bodies had already spelled alike.

ADR-025 step five's opening cleanup. Every harness keeps its own
``native_run`` as the single native execution body of its vendor protocol;
this module holds only the mechanical fragments that were identical in those
bodies and are consumed by production: the cancel flag view of the seam's
cancel callable, the one overall deadline rule, the observer-stop marker, the
native-identity and package-shape droppers, the owned-group halt and the
configuration spec dict. Nothing vendor-shaped lives here — no native method
name, no error code, no catalog, no policy — and nothing that differs between
harnesses is unified: a fragment with differing semantics stays in the
harness that owns it, and no switch matrix is offered to force one shape.

Each harness imports the fragment it consumes under a module-global name, so
its internal call sites keep resolving that harness's own global at call
time; a test that patches one harness's alias still intercepts only that
harness.
"""
from __future__ import annotations

import math
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import TypeAdapter

from ...errors import BoardError
from .base import ProcessHandle
from .run_contract import NativeIdentity, RunRequest

__all__ = [
    "CancelFlag", "ObserverInterrupt", "configuration_spec", "execution_deadline",
    "halt_owned_group", "identity_or_none", "shape_guard", "utc_now",
]


def utc_now() -> str:
    """The UTC timestamp string the run facts share (ISO 8601, ``Z`` suffix)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def execution_deadline(timeout_seconds) -> float:
    """The one overall native execution deadline; an explicit 0 means unlimited.

    Only this deadline becomes infinite. The version probe and the per-request,
    cancel and shutdown waits keep their own finite bounds, and the cancel
    flag still ends an unlimited turn.
    """
    return math.inf if timeout_seconds == 0 else time.monotonic() + timeout_seconds


def configuration_spec(request: RunRequest) -> dict:
    """The frozen request's provider/model/effort selection as the plain spec
    dict the native bindings and readbacks compare."""
    return {"provider": request.configuration.provider, "model": request.configuration.model,
            "effort": request.configuration.effort}


class CancelFlag:
    """A ``threading.Event`` view of the seam's cancel callable."""

    def __init__(self, cancelled: Callable[[], bool]):
        self._cancelled = cancelled
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set() or bool(self._cancelled())

    def set(self) -> None:
        self._event.set()

    def wait(self, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))


class ObserverInterrupt(Exception):
    """The role observer asked the driver to stop the native run.

    Drivers raise it only at their own feedback boundaries: the refused
    interaction or retained fact that prompted the stop is recorded first, and
    no control flow bypasses the recorder or the refusal I/O.
    """


def identity_or_none(fields: dict) -> NativeIdentity | None:
    try:
        return NativeIdentity(**fields)
    except BoardError:
        return None


def shape_guard(shape: Any) -> Callable[[Any], Any]:
    """One package field's own canonical-shape guard.

    A value the package's own projection refuses is dropped alone; every other
    observed fact of the run keeps its place, so no fallback blankets known
    facts into unknowns.
    """
    adapter = TypeAdapter(shape)

    def check(value: Any) -> Any:
        if value is None:
            return None
        try:
            return adapter.validate_python(value)
        except BoardError:
            return None
    return check


def halt_owned_group(process: subprocess.Popen, handle: ProcessHandle, deadline: float) -> tuple[bool, bool]:
    """The one actual stop of an owned group: close, wait, terminate, confirm.

    Returns ``(shutdown, signalled)`` — the group's confirmed disappearance and
    whether a signal was actually sent. Every owner (a run's stop collection
    and a spawn helper's failure path) stops its child through this primitive
    alone.
    """
    try:
        process.stdin.close()
    except OSError:
        pass
    handle.wait(min(3.0, max(0.0, deadline - time.monotonic())))
    signalled = False
    if not handle.shutdown_confirmed(settle_seconds=0.2):
        handle.terminate(grace_seconds=1.0)
        signalled = True
    shutdown = handle.shutdown_confirmed(settle_seconds=0.5)
    return shutdown, signalled
