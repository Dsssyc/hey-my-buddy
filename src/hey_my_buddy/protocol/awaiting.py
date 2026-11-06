"""The message boundaries shared by Host observation and Lead doorbells."""
from __future__ import annotations

TERMINAL_STATUSES = frozenset({"completed", "concluded", "failed", "cancelled", "reconciliation-needed"})


def shutdown_confirmed(run: dict) -> bool:
    evidence = run.get("workflowShutdown")
    return (evidence.get("selfConfirmed") is True and evidence.get("descendantsConfirmed") is True
            if isinstance(evidence, dict) else run.get("shutdownConfirmed") is True)


def message_kind(run: dict) -> str | None:
    """Revisions prompt observation; only a message boundary ends an await.

    The service marks reminders and issue occurrences only for the wait that
    claims them. Activity and an unchanged, already delivered issue are not
    messages. A result still waiting for shutdown proof is not a delivery.
    """
    if isinstance(run.get("reminder"), dict):
        return run["reminder"]["kind"]
    if run.get("issue"):
        return "issue"
    workflow = run.get("workflowState")
    if workflow == "awaiting-host":
        return "waiting-host"
    if workflow is None:
        if run.get("resultAvailable") or run.get("state") in TERMINAL_STATUSES:
            return run.get("state", "unknown")
    elif workflow in ("delivered", "accepted", "concluded", "cancelled", "failed"):
        if not isinstance(run.get("workflowShutdown"), dict) or shutdown_confirmed(run):
            return workflow
    return None


def message_key(run: dict) -> list:
    """The workflow facts whose revision or delivery can carry a new message."""
    workflow = run.get('workflow') or {}
    return [run['revision'], run.get('workflowState'), run.get('state'),
            workflow.get('activeRequestId'), run.get('selectedAttemptId'), run.get('resultAvailable'),
            run.get('workflowShutdown')]
