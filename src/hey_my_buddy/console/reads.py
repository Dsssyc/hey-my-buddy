"""The console's periodic read projection: slim by construction.

The old periodic snapshot re-derived three expensive things every poll: the
latest hundred fully decorated task rows, the backup preflight walk of every
evidence directory, and the full evaluation/routing/harness projection. This
module builds the projection the console actually polls:

* the evaluation core (``EvaluationStore.console_core``) — configuration,
  routed profiles with quota facts, cards, policy, evidence and decision pages,
  the write gate and model-family capacity;
* harness health and routing health, as before;
* ``tasks`` reduced to ``pendingCount`` — the full-board count of delegation
  roots awaiting their Host, with the exact dedup semantics the top bar used.

Full execution records stay on the existing paginated ``task_list`` route, and
the backup preflight moved to its own authenticated on-demand route that reuses
the original ``backup.preflight``. Each projection also reports the earliest
time at which one of its own fields can flip (lease expiries, quota retry and
reset windows, router skip windows), so a read cache can serve it unchanged
without freezing state that must expire.
"""
from __future__ import annotations

import time as _time


def console_snapshot_projection(service, *, clock=_time.time) -> tuple[dict, float]:
    """Assemble the slim console snapshot and its earliest change boundary.

    Returns ``(projection, deadline)`` where ``deadline`` is epoch seconds: the
    earliest future moment at which some field of the projection can flip on
    time alone. Database changes are not tracked here — the caller's marker
    covers them — and neither is the console's own session/access state.

    While discovery is automatic, the projection also borrows the rate-limited
    harness scan: the deadline is capped at the scan interval so an idle
    console that keeps answering from the cache still re-enters this function
    (and kicks the scan) instead of starving the 180-second cadence — including
    the cold case where every health record is still unknown and carries no
    ``scanAfter`` of its own.
    """
    # A periodic read still borrows the rate-limited harness scan exactly as
    # the previous implementation did; the scan itself is bounded by its own
    # 180-second markers and writes land as ordinary database changes.
    automatic = bool(getattr(service, "automatic_discovery", False))
    if automatic:
        service.harnesses.kick()
    now = clock()
    from ..blackboard.service.harness_health import SCAN_SECONDS
    from ..blackboard.tasks import objectives
    from .read_cache import collect_deadline, gate_lease_deadline

    projection = service.evaluation.console_core()
    projection["harnesses"] = service.harnesses.all()
    projection["routingHealth"] = service.decisions.health_summary()
    projection["capabilities"] = service.evaluation.capabilities()
    projection["tasks"] = {"pendingCount": objectives.pending_root_count(service.store)}
    lease = gate_lease_deadline(service.store.db, now)
    deadline = collect_deadline(projection, after=now, known=lease)
    if automatic:
        deadline = min(deadline, now + SCAN_SECONDS)
    return projection, deadline


def backup_preflight(store) -> dict:
    """The original backup preflight, computed on explicit request only."""
    from ..blackboard.store.backup import preflight

    return preflight(store.directory)
