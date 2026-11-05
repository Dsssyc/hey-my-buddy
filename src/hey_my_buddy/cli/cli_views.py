"""CLI read projection that keeps the service's exact ids, actions and evidence."""
from __future__ import annotations

from typing import Any

OUTPUT_FULL = "full"
OUTPUT_BRIEF = "brief"

_RUN_BRIEF = (
    "runId", "state", "status", "actions", "macroTaskId", "project", "title", "goal", "task", "cancellation",
    "queueReason", "access", "accessNote", "checkout", "unsealedChanges", "folderChangesNotice", "buddy", "delegationAuthority",
    "leadOwnership", "ownedMicroTasks", "activeRequest", "currentTurn",
    "finalArtifactId", "shutdown", "counts", "truncated", "routing", "workspace",
    "artifacts", "children", "workspaceConflicts", "integrations", "hostConclusion",
    "inquiries", "providerCooling", "providerRejection", "inputFailure", "executionIssues", "serviceIssues", "fixes",
    "fixedBy", "routingHistory", "audit",
)


def render(method: str, response: Any, mode: str = OUTPUT_BRIEF) -> Any:
    """Keep the service's actionable facts; full retains its complete read view."""
    if mode == OUTPUT_BRIEF and method == "get" and isinstance(response, dict) and "microTask" in response:
        return {key: response[key] for key in _RUN_BRIEF if key in response}
    return response
