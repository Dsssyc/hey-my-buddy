"""Model-family admission constants and managed worker-pool identity.

Two facts must stay identical in the store and the daemon: how a model family and
its limits are derived, and the stable IDs of the daemon's generic pool. Both live
here so there is exactly one scheduler, one naming rule and no second job engine.

ADR-011 removed the separate business/decision lanes: routing and execution share
one machine-wide ceiling and the same per-family counters. A model family is the
exact adapter/provider/model tuple; effort variants share one limit.

Pool *ownership* is deliberately not inferred from these names: the daemon records
the exact IDs it started in its private manifest (``worker-pool.json``) and only
retires or stops those. A hand-made ``local-99`` folder is never claimed by naming.

Worker identity is the registry's own rule (:data:`schemas.IDENTIFIER_PATTERN`), so a
manifest or environment value can never become a path or an argv value that the
service itself would refuse.
"""
from __future__ import annotations

from . import schemas

#: The internal adapter whose model tuple is resolved from the configured decision
#: profile at claim time, frozen onto the attempt and quota-checked before the
#: selection reader is admitted.
DECISION_ADAPTER = "decision"

#: One machine-wide concurrent-attempt ceiling (BUDDY_MAX_CONCURRENT).
TOTAL_CONCURRENCY_DEFAULT = 8
TOTAL_CONCURRENCY_MIN = 1
TOTAL_CONCURRENCY_MAX = 32

#: Per-family concurrent-attempt limit bounds. Families without an explicit user
#: setting use the default; effort never widens or narrows the family.
MODEL_LIMIT_DEFAULT = 2
MODEL_LIMIT_MIN = 1
MODEL_LIMIT_MAX = 32

#: Queue-reason names. ``capacity`` keeps the historical name for the machine-wide
#: ceiling so existing clients and receipts keep their meaning; a full model family
#: is explicit so an operator can tell which family limit is holding the work.
REASON_TOTAL_CAPACITY = "capacity"
REASON_MODEL_CAPACITY = "model-capacity"

#: The worker ID every pool falls back to when the configured prefix is not a valid
#: worker identity. A malformed environment value is never turned into a path.
DEFAULT_WORKER_PREFIX = "local"


def clamp_total(value: int) -> int:
    """The machine-wide ceiling as a bounded integer."""
    return max(TOTAL_CONCURRENCY_MIN, min(int(value), TOTAL_CONCURRENCY_MAX))


def clamp_model_limit(value: int) -> int:
    """A per-family limit as a bounded integer."""
    return max(MODEL_LIMIT_MIN, min(int(value), MODEL_LIMIT_MAX))


def model_family(spec: dict) -> tuple[str, str, str] | None:
    """The model family a specification will consume, or ``None`` when it has none.

    The family is the exact adapter/provider/model tuple. Partial configurations and
    model-less work (``command``/``external``, an unresolved governed run) return
    ``None``: they are admitted against the machine-wide ceiling only. A governed
    run's effective specification already merges the resolved execution
    configuration over the immutable original request, so this is the tuple the
    attempt will actually run with.
    """
    if not isinstance(spec, dict):
        return None
    adapter = spec.get("adapter")
    provider = spec.get("provider")
    model = spec.get("model")
    if not (isinstance(adapter, str) and adapter and isinstance(provider, str) and provider
            and isinstance(model, str) and model):
        return None
    return (adapter, provider, model)


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
