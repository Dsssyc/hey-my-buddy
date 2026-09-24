"""Lane classification and managed worker-pool identity.

Two facts must stay identical in the store and the daemon: which execution lane an
adapter belongs to, and the stable IDs of the daemon's generic pool. Both live here
so there is exactly one scheduler, one naming rule and no second job engine.

Pool *ownership* is deliberately not inferred from these names: the daemon records
the exact IDs it started in its private manifest (``worker-pool.json``) and only
retires or stops those. A hand-made ``local-99`` folder is never claimed by naming.

Worker identity is the registry's own rule (:data:`schemas.IDENTIFIER_PATTERN`), so a
manifest or environment value can never become a path or an argv value that the
service itself would refuse.
"""
from __future__ import annotations

from . import schemas

#: The internal adapter whose attempts always have their own reserved lane.
DECISION_ADAPTER = "decision"

LANE_BUSINESS = "business"
LANE_DECISION = "decision"
LANES = (LANE_BUSINESS, LANE_DECISION)

#: Queue-reason names. The business lane keeps the historical ``capacity`` name so
#: existing clients and receipts keep their meaning; the decision lane is explicit
#: so an operator can tell which limit is holding the work.
REASON_BUSINESS_CAPACITY = "capacity"
REASON_DECISION_CAPACITY = "decision-capacity"

#: The worker ID every pool falls back to when the configured prefix is not a valid
#: worker identity. A malformed environment value is never turned into a path.
DEFAULT_WORKER_PREFIX = "local"


def lane_for_adapter(adapter: object) -> str:
    """The lane an adapter belongs to.

    Only ``task.adapter == 'decision'`` is the decision lane; every other adapter,
    including ``external``/``command``, an unresolved adapter and the coding
    harnesses, counts as business work.
    """
    return LANE_DECISION if adapter == DECISION_ADAPTER else LANE_BUSINESS


def capacity_reason(lane: str) -> str:
    """The queue reason that names the full lane."""
    return REASON_DECISION_CAPACITY if lane == LANE_DECISION else REASON_BUSINESS_CAPACITY


def valid_worker_id(value: object) -> str | None:
    """A pool worker ID, or ``None`` for any other JSON value.

    The check is the worker-ID validation the named C-Two operations apply, plus one
    path-safety rule those operations do not need: a bare ``.`` or ``..`` is a valid
    identifier but not a valid directory segment, and ``state/workers/<id>`` would
    escape the worker directory for it. Nothing is stringified: a number, object,
    list or malformed string is simply not an identity.
    """
    if not isinstance(value, str) or value in (".", "..") or not schemas.IDENTIFIER_PATTERN.fullmatch(value):
        return None
    return value


def pool_worker_ids(prefix: object, total: int) -> list[str]:
    """The stable IDs of a pool of ``total`` slots under one configured prefix.

    The first slot keeps the configured ID exactly (``local``); the others append
    their 1-based index (``local-2``, ``local-3``). The IDs never depend on which
    processes happen to be running, so a restarted daemon recognizes its own
    supervisors by lock ownership instead of spawning duplicates.

    A prefix that is not a valid worker identity — or whose generated IDs would
    exceed the registry's bound — falls back to :data:`DEFAULT_WORKER_PREFIX`, so a
    malformed ``BUDDY_WORKER_ID`` can never escape the state directory.
    """
    count = max(1, int(total))
    base = valid_worker_id(prefix) or DEFAULT_WORKER_PREFIX
    generated = [base, *(f"{base}-{index}" for index in range(2, count + 1))]
    if any(valid_worker_id(worker_id) is None for worker_id in generated):
        base = DEFAULT_WORKER_PREFIX
        generated = [base, *(f"{base}-{index}" for index in range(2, count + 1))]
    return generated
