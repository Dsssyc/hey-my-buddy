"""Provider cooling from structured execution results."""
def persist(connection, attempt, result, *, status, shutdown_confirmed, now):
    """Update provider cooling inside the committed result transaction."""
    from ...protocol.harness_names import HARNESS_NAMES
    from ..routing.quota_routing import clear_on_success, record_rejection

    harness = attempt["model_harness"] or attempt["adapter"]
    if harness not in HARNESS_NAMES:
        return
    failure = provider_rejection_view(result)
    if failure is not None and failure["code"] == "quota-exceeded":
        record_rejection(connection, harness, attempt["model_provider"], failure, now=now)
    elif status == "ok" and shutdown_confirmed:
        clear_on_success(connection, harness, attempt["model_provider"], started_at=attempt["started_at"])


def provider_rejection_view(result):
    """The common harness result already normalized this structured fact."""
    failure = result.get("providerRejection") if isinstance(result, dict) else None
    return failure if isinstance(failure, dict) and failure.get("code") in ("quota-exceeded", "rate-limited", "overloaded") else None
