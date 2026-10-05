"""Role preparation and execution entry points.

The existing Worker and Router carriers enter here. The registered run call
point is retained for the first ZCode consumer in ADR-025 step two; observation
and session-service policy will be added with that consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

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


def worker_executor(name: str) -> Adapter:
    """The Worker runtime's one executor selection point (ADR-025 decision 10).

    A harness extracted by steps two to four runs through its registered run
    seam and its old carrier entry is deleted in the same step, so a registered
    name must never fall through to the legacy carrier: this guard makes a
    half-finished switch loud instead of quietly reviving a deleted entry.
    ``command`` and ``external`` keep the registry carrier for good, and the
    registry stays the only place any executor is selected.
    """
    from ..harnesses.registry import adapter, run_seam

    if run_seam(name) is not None:
        raise BoardError(
            "ROLE_RUN_NOT_MIGRATED",
            f"{name} has a registered run seam; its role execution must replace this legacy carrier",
            adapter=name,
        )
    return adapter(name)


def prepare_worker_run(executor: Adapter, context: ExecutionContext) -> WorkerPreparation:
    """The adapter's availability fact and its own preparation, as one result."""
    usable, reason = executor.available()
    if not usable:
        return WorkerPreparation(executor.name, available=False, reason=reason)
    try:
        executor.prepare(context)
    except BoardError as error:
        return WorkerPreparation(executor.name, available=True, error=error)
    return WorkerPreparation(executor.name, available=True)


def worker_start(executor: Adapter, context: ExecutionContext) -> ProcessHandle:
    """Start one legacy-carrier attempt; the runtime keeps the returned handle."""
    return executor.start(context)


def worker_collect(executor: Adapter, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
    return executor.collect(handle, context)


def worker_cancel(executor: Adapter, handle: ProcessHandle, *, grace_seconds: float | None = None) -> None:
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
    native: Adapter
    request: NoToolStructuredRequest
    context: ExecutionContext
    #: The empty owner-private cwd the native call runs in, removed after a
    #: proven stop; unexpected native files are retained for inspection.
    no_tool_cwd: Path


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
    """The one transitional call point of the Router's two structured entries.

    While a harness is unextracted this starts exactly the carrier the
    preparation selected — no re-resolution, so eligibility and selection are
    not recomputed; when its run seam is registered the old entries are deleted
    and the extracting step replaces this branch with its :func:`run_harness`
    execution. No third entry and no second carrier exists here.
    """
    from ..harnesses.registry import run_seam

    if run_seam(preparation.harness) is not None:
        raise BoardError(
            "ROLE_RUN_NOT_MIGRATED",
            f"{preparation.harness} has a registered run seam; its Router execution must replace this legacy entry",
            adapter=preparation.harness,
        )
    if isinstance(preparation, FastPreparation):
        return preparation.native.start_no_tool_structured(preparation.context, preparation.request)
    if isinstance(preparation, ReviewPreparation):
        return preparation.native.start_read_only_structured(preparation.context, preparation.request)
    raise BoardError("INVALID_ARGUMENT", "unknown Router preparation", harness=preparation.harness)


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
