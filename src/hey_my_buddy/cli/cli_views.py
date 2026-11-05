"""CLI read projection that keeps the service's actionable facts and exact ids."""
from __future__ import annotations

from typing import Any

OUTPUT_FULL = "full"
OUTPUT_BRIEF = "brief"

_RUN_BRIEF = (
    "runId", "state", "status", "actions", "macroTaskId", "project", "title", "goal", "task", "cancellation",
    "queueReason", "access", "accessNote", "checkout", "unsealedChanges", "buddy", "delegationAuthority",
    "leadOwnership", "ownedMicroTasks", "activeRequest", "currentTurn",
    "finalArtifactId", "shutdown", "counts", "truncated", "routing", "workspace",
    "artifacts", "children", "workspaceConflicts", "integrations", "hostConclusion",
    "inquiries", "providerCooling", "providerRejection", "inputFailure", "executionIssues", "serviceIssues", "fixes",
    "fixedBy", "routingHistory", "audit",
)

#: The service records a buddy identity as its four columns; this is the one
#: key set that names a complete buddy, the same shape every view carries.
_IDENTITY_FIELDS = frozenset({"harness", "provider", "model", "effort"})


def buddy_name(identity: dict) -> str:
    """The Host-facing buddy writing: 'harness / provider / model / effort'."""
    return " / ".join(identity[key] for key in ("harness", "provider", "model", "effort"))


def _named_buddies(value: Any) -> Any:
    """Write every buddy identity in a response by its single Host-facing name."""
    if isinstance(value, dict):
        if set(value) == _IDENTITY_FIELDS and all(isinstance(item, str) for item in value.values()):
            return buddy_name(value)
        return {key: _named_buddies(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_named_buddies(item) for item in value]
    return value


def render(method: str, response: Any, mode: str = OUTPUT_BRIEF) -> Any:
    """Keep the service's actionable facts; full retains its complete read view."""
    if mode == OUTPUT_BRIEF and method == "get" and isinstance(response, dict) and "microTask" in response:
        return _named_buddies({key: response[key] for key in _RUN_BRIEF if key in response})
    return _named_buddies(response)
