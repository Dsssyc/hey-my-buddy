"""Frozen execution attribution of token usage and structured quota failures."""
from ..store.db import canonical_json


def persist(connection, attempt, result, *, status, shutdown_confirmed, now):
    """Called once inside the result transaction, after actor and replay checks."""
    from ...protocol.usage import normalize_token_usage
    from ...protocol.harness_names import HARNESS_NAMES
    from ..routing.quota_routing import clear_on_success, record_rejection

    usage = normalize_token_usage(result.get("tokenUsage")) if isinstance(result, dict) else None
    connection.execute("UPDATE attempts SET token_usage_json=? WHERE attempt_id=?",
                       (canonical_json(usage) if usage is not None else None, attempt["attempt_id"]))
    harness = attempt["model_harness"] or attempt["adapter"]
    if harness not in HARNESS_NAMES:
        return
    failure = failure_view(result)
    if failure is not None and failure["code"] == "quota-exceeded":
        record_rejection(connection, harness, attempt["model_provider"], failure, now=now)
    elif status == "ok" and shutdown_confirmed:
        clear_on_success(connection, harness, attempt["model_provider"], started_at=attempt["started_at"])


def failure_view(result):
    """The common harness result already normalized this structured fact."""
    failure = result.get("quotaFailure") if isinstance(result, dict) else None
    return failure if isinstance(failure, dict) and failure.get("code") in ("quota-exceeded", "rate-limited") else None
