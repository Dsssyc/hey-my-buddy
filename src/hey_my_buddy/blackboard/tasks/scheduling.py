"""Machine and model-service admission limits shared by all executions."""
from __future__ import annotations

from ...protocol import schemas

#: The internal adapter whose model tuple is resolved from the configured decision
#: buddy at claim time, frozen onto the attempt and quota-checked before the
#: selection reader is admitted.
DECISION_ADAPTER = "decision"

#: One machine-wide concurrent-attempt ceiling (HMB_MAX_CONCURRENT).
TOTAL_CONCURRENCY_DEFAULT = 8
TOTAL_CONCURRENCY_MIN = 1
TOTAL_CONCURRENCY_MAX = 32

#: Per-model-service concurrent-attempt limit bounds. Model services without an explicit user
#: setting use the default; effort never widens or narrows the model service.
MODEL_LIMIT_DEFAULT = 2
MODEL_LIMIT_MIN = 1
MODEL_LIMIT_MAX = 32

#: Queue-reason names. ``capacity`` keeps the historical name for the machine-wide
#: ceiling so existing clients and receipts keep their meaning; a full model service
#: is explicit so an operator can tell which model service limit is holding the work.
REASON_TOTAL_CAPACITY = "capacity"
REASON_MODEL_CAPACITY = "model-capacity"

def clamp_total(value: int) -> int:
    """The machine-wide ceiling as a bounded integer."""
    return max(TOTAL_CONCURRENCY_MIN, min(int(value), TOTAL_CONCURRENCY_MAX))


def clamp_model_limit(value: int) -> int:
    """A per-model-service limit as a bounded integer."""
    return max(MODEL_LIMIT_MIN, min(int(value), MODEL_LIMIT_MAX))


def model_service(spec: dict) -> tuple[str, str, str] | None:
    """The model service a specification will consume, or ``None`` when it has none.

    The model service is the exact harness/provider/model tuple. Partial buddies and
    model-less work (``command``, an unresolved micro-task run) return
    ``None``: they are admitted against the machine-wide ceiling only. A micro-task
    run's effective specification already merges the resolved execution
    buddy over the immutable original request, so this is the tuple the
    attempt will actually run with.
    """
    if not isinstance(spec, dict):
        return None
    harness = spec.get("harness")
    if harness not in schemas.CODING_HARNESSES:
        return None
    provider = spec.get("provider")
    model = spec.get("model")
    if not (isinstance(harness, str) and harness and isinstance(provider, str) and provider
            and isinstance(model, str) and model):
        return None
    return (harness, provider, model)
