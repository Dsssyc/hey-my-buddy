"""Role preparation and execution entry points over the harness registry."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import signal
import time
from typing import Callable, Mapping, TYPE_CHECKING

from ...errors import BoardError
from ...protocol.stop import WRAP_UP_INITIATORS
from ..harnesses.base import (
    Adapter,
    AdapterOutcome,
    ExecutionContext,
    NoToolStructuredRequest,
    ProcessHandle,
    ReadOnlyStructuredRequest,
)
from ..harnesses.run_contract import HARNESS_NAMES, HarnessRun, RunFeedback, RunRequest, RunResult

if TYPE_CHECKING:
    from .run_execution import WorkerRunExecutor

__all__ = [
    'FastPreparation',
    'ReviewPreparation',
    'WorkerPreparation',
    'prepare_worker_run',
    'run_harness',
    'start_router_preparation',
    'worker_cancel',
    'worker_stop',
    'worker_collect',
    'worker_executor',
    'worker_start',
]


# -- the Worker runtime's execution seam -------------------------------------


@dataclass(frozen=True)
class WorkerPreparation:
    """What preparing one Worker attempt established, before any process exists.

    ``reason`` is the adapter's own availability fact and ``error`` its
    ``prepare()`` refusal; both stay ``None`` on a prepared attempt. The runtime
    maps them onto its exact receipts — the seam reports the result, the runtime
    keeps its receipt, retry and supervision rules — and takes the receipt's
    name from the executor it selected, not from this value.
    """

    available: bool
    reason: str | None = None
    error: BoardError | None = None


def worker_executor(name: str) -> Adapter | WorkerRunExecutor:
    """The Worker runtime's one executor selection point (ADR-025 decision 10).

    An extracted harness uses the generic role executor and its registered
    native run; it never falls through to a deleted legacy carrier entry.
    ``command`` and ``external`` keep the registry carrier for good, and the
    registry stays the only place any executor is selected.
    """
    from ..harnesses.registry import adapter, run_seam

    module = run_seam(name)
    if module is not None:
        from .run_execution import WorkerRunExecutor
        return WorkerRunExecutor(adapter(name), module)
    if name in HARNESS_NAMES:
        raise BoardError("ROLE_RUN_UNREGISTERED", "The harness has no registered run", harness=name)
    return adapter(name)


def prepare_worker_run(executor: Adapter | WorkerRunExecutor, context: ExecutionContext) -> WorkerPreparation:
    """The adapter's availability fact and its own preparation, as one result."""
    usable, reason = executor.available()
    if not usable:
        return WorkerPreparation(available=False, reason=reason)
    try:
        executor.prepare(context)
    except BoardError as error:
        return WorkerPreparation(available=True, error=error)
    return WorkerPreparation(available=True)


def worker_start(executor: Adapter | WorkerRunExecutor, context: ExecutionContext) -> ProcessHandle:
    """Start one role attempt; the runtime keeps the returned handle."""
    return executor.start(context)


def worker_collect(executor: Adapter | WorkerRunExecutor, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
    return executor.collect(handle, context)


def stop_termination_reason(*, shutdown_confirmed: bool, stop_initiator: str | None,
                            grace_expired: bool = False, wrap_up_delivered: bool = False) -> str | None:
    """Classify a stop once, separately from its initiator and shutdown evidence."""
    if shutdown_confirmed is not True:
        return None
    if wrap_up_delivered:
        return "wrap-up"
    if grace_expired:
        return "grace-expired"
    if stop_initiator in WRAP_UP_INITIATORS:
        return "forced-stop"
    if stop_initiator == "console":
        return "console-stop"
    if stop_initiator == "service":
        return "service-stop"
    return "unknown-stop"


def worker_cancel(executor: Adapter | WorkerRunExecutor, handle: ProcessHandle, *, grace_seconds: float | None = None) -> None:
    """Cancel through the executor; a None grace keeps each adapter's own default."""
    with handle.stop_lock:
        if handle.force_stop_requested:
            return
        handle.force_stop_requested = True
        if handle.stop_initiator is None:
            handle.stop_initiator = "unknown"
        if grace_seconds is None:
            executor.cancel(handle)
        else:
            executor.cancel(handle, grace_seconds=grace_seconds)


def worker_stop(executor: Adapter | WorkerRunExecutor, handle: ProcessHandle, *,
                stop_initiator: str, grace_deadline: float | None) -> None:
    """Route the first stop request through the selected executor's policy."""
    with handle.stop_lock:
        if handle.force_stop_requested or (handle.cancel_requested and
                (not handle.wrap_up_started or stop_initiator in WRAP_UP_INITIATORS)):
            return
        handle.stop_initiator = stop_initiator
        if handle.cancel_requested:
            handle.force_stop_requested = True
        executor.stop(handle, stop_initiator=stop_initiator, grace_deadline=grace_deadline)


def start_wrap_up(handle: ProcessHandle, deadline: float, *, stop_initiator: str = "host") -> None:
    """Interrupt the current native turn once, with its bounded wrap-up intent."""
    from .turn_io import private_json
    from .run_execution import _STOP_CONTROL

    control = handle.role_run_control
    private_json(Path(control["privateRoot"]) / _STOP_CONTROL, {
        "kind": "host-wrap-up", "graceDeadline": deadline,
        "binding": handle.role_run_identity.to_payload(),
    }, exclusive=True)
    handle.stop_initiator = stop_initiator
    handle.wrap_up_started = True
    handle.grace_deadline = deadline
    handle.cancel_requested = True
    handle.signalled_at = time.monotonic()
    handle.signal_group(signal.SIGTERM)


# -- the Router's preparation results and carrier call point ------------------


@dataclass(frozen=True)
class FastPreparation:
    """The Router fast mode's prepared call with its frozen registered run.
    """

    harness: str
    request: NoToolStructuredRequest
    context: ExecutionContext
    #: The empty owner-private cwd the native call runs in, removed after a
    #: proven stop; unexpected native files are retained for inspection.
    no_tool_cwd: Path
    run_module: HarnessRun | None = None

    def __post_init__(self):
        from ..harnesses.registry import run_seam
        if self.run_module is None:
            object.__setattr__(self, "run_module", run_seam(self.harness))


@dataclass(frozen=True)
class ReviewPreparation:
    """The Router review mode's prepared native call over a frozen mirror.

    ``native`` is the carrier selected once during preparation; the one call
    point below starts exactly this instance, so eligibility and selection are
    frozen and never recomputed.
    """

    harness: str
    native: Adapter
    request: ReadOnlyStructuredRequest
    context: ExecutionContext
    #: The frozen input binding the collection re-verifies: (manifest, root, digest).
    mirror: tuple[dict | None, Path, str]
    run_module: HarnessRun | None = None

    def __post_init__(self):
        from ..harnesses.registry import run_seam
        if self.run_module is None:
            object.__setattr__(self, "run_module", run_seam(self.harness))


def start_router_preparation(preparation: FastPreparation | ReviewPreparation) -> ProcessHandle:
    """Start the prepared role through its frozen registered run.

    Eligibility and the selected description stay frozen at preparation. A
    registered harness takes only the generic role controller path.
    """
    from ..harnesses.registry import run_seam

    if not isinstance(preparation, (FastPreparation, ReviewPreparation)):
        raise BoardError("INVALID_ARGUMENT", "unknown Router preparation", harness=preparation.harness)
    module = preparation.run_module
    if module is None or run_seam(preparation.harness) is not module:
        raise BoardError("ROLE_RUN_UNREGISTERED", "The prepared run is no longer the registered execution body")
    if isinstance(preparation, FastPreparation):
        from .run_execution import start_fast
        return start_fast(module, preparation.harness, preparation.context, preparation.request)
    from .run_execution import start_review
    if not preparation.native.read_only_structured:
        raise BoardError("router-review-unsupported", "Review on this registered harness is not implemented")
    return start_review(preparation.harness, preparation.context, preparation.request)


# -- the new run seam's one call point ----------------------------------------


def run_harness(
    module: HarnessRun,
    request: RunRequest,
    *,
    observer: Callable[[Mapping[str, Any]], RunFeedback],
    services: object | None,
    cancelled: Callable[[], bool],
) -> RunResult:
    """The one common call point of the accepted run seam (ADR-025 decision 1).

    ``module`` must be exactly the run module the registry has registered under
    ``request.harness`` — never an attribute probe result, a look-alike, or an
    unregistered object — so a command adapter or an arbitrary object is never
    mistaken for a run. The observer receives normalized, retained fact mappings
    and answers with one :class:`RunFeedback` (continue, stop, or one in-run
    correction). The caller owns its session services; this entry point passes
    them through without defining unused role policy.
    """
    from ..harnesses.registry import run_seam

    if run_seam(request.harness) is not module:
        raise BoardError("ROLE_RUN_UNREGISTERED",
                         f"the run of {request.harness} must go through its registered run seam",
                         harness=request.harness)
    requested_controls = {
        name for name, requested in (
            ("network_allowed_domains", request.network_allowed_domains is not None),
            ("additional_denied_tools", bool(request.additional_denied_tools)),
            ("resume_checkpoint", request.continuation is not None
             and request.continuation.checkpoint is not None),
        ) if requested
    }
    unsupported = requested_controls - set(getattr(module, "supported_request_controls", ()))
    if unsupported:
        raise BoardError("ROLE_RUN_UNSUPPORTED_CONTROL",
                         "The registered run does not support the requested native controls",
                         harness=request.harness, controls=sorted(unsupported))
    return module.run(request, observer=observer, services=services, cancelled=cancelled)
