"""Initiators whose stop request permits a bounded wrap-up turn."""

WRAP_UP_INITIATORS = frozenset({"host", "lead"})

#: The observed outcome of one attempt. The stop initiator is recorded separately.
TERMINATION_REASONS = (
    "completed", "wrap-up", "grace-expired", "forced-stop", "console-stop",
    "service-stop", "unknown-stop", "deadline", "harness-error", "transport-error", "interrupted",
    "workspace-error",
)
