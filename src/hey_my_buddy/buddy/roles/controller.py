"""Role preparation and execution entry points over the harness registry."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TYPE_CHECKING

from ...errors import BoardError
from ..harnesses.base import (
    Adapter,
    AdapterOutcome,
    ExecutionContext,
    NoToolStructuredRequest,
    ProcessHandle,
    ReadOnlyStructuredRequest,
)
from ..harnesses.run_contract import HarnessRun, RunFeedback, RunRequest, RunResult

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
    keeps its receipt, retry and supervision rules.
    """

    name: str
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
    return adapter(name)


def prepare_worker_run(executor: Adapter | WorkerRunExecutor, context: ExecutionContext) -> WorkerPreparation:
    """The adapter's availability fact and its own preparation, as one result."""
    usable, reason = executor.available()
    if not usable:
        return WorkerPreparation(executor.name, available=False, reason=reason)
    try:
        executor.prepare(context)
    except BoardError as error:
        return WorkerPreparation(executor.name, available=True, error=error)
    return WorkerPreparation(executor.name, available=True)


def worker_start(executor: Adapter | WorkerRunExecutor, context: ExecutionContext) -> ProcessHandle:
    """Start one role attempt; the runtime keeps the returned handle."""
    return executor.start(context)


def worker_collect(executor: Adapter | WorkerRunExecutor, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
    return executor.collect(handle, context)


def worker_cancel(executor: Adapter | WorkerRunExecutor, handle: ProcessHandle, *, grace_seconds: float | None = None) -> None:
    """Cancel through the executor; a None grace keeps each adapter's own default."""
    if grace_seconds is None:
        executor.cancel(handle)
    else:
        executor.cancel(handle, grace_seconds=grace_seconds)


# -- the Router's preparation results and carrier call point ------------------


@dataclass(frozen=True)
class FastPreparation:
    """The Router fast mode's prepared native call: everything but the start.

    ``native`` is the carrier selected once during preparation; the one call
    point below starts exactly this instance, so eligibility and selection are
    frozen and never recomputed.
    """

    harness: str
    native: Any
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


def start_router_preparation(preparation: FastPreparation | ReviewPreparation) -> ProcessHandle:
    """Start the prepared role through its run seam or unextracted carrier.

    Eligibility and the selected description stay frozen at preparation. A
    registered harness takes only the generic role controller path.
    """
    from ..harnesses.registry import run_seam

    if not isinstance(preparation, (FastPreparation, ReviewPreparation)):
        raise BoardError("INVALID_ARGUMENT", "unknown Router preparation", harness=preparation.harness)
    if isinstance(preparation, FastPreparation):
        module = preparation.run_module
        if run_seam(preparation.harness) is not module:
            raise BoardError("ROLE_RUN_UNREGISTERED", "The prepared run is no longer the registered execution body")
    else:
        module = run_seam(preparation.harness)
    if module is not None:
        if isinstance(preparation, FastPreparation):
            from .run_execution import start_fast
            return start_fast(module, preparation.harness, preparation.context, preparation.request)
        raise BoardError("router-review-unsupported", "Review on the registered Worker carrier is not implemented")
    if isinstance(preparation, FastPreparation):
        return preparation.native.start_no_tool_structured(preparation.context, preparation.request)
    if isinstance(preparation, ReviewPreparation):
        return preparation.native.start_read_only_structured(preparation.context, preparation.request)


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
    return module.run(request, observer=observer, services=services, cancelled=cancelled)
