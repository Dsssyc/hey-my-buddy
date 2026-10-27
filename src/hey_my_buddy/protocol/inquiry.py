"""Bounded inquiry timing and native-owner refusal facts."""
DEFAULT_TRANSPORT_TIMEOUT_MS = 1500
MIN_TRANSPORT_TIMEOUT_MS = 100
MAX_TRANSPORT_TIMEOUT_MS = 5000
BRIDGE_ERRORS = (
    "bad-request", "unauthorized", "frame-too-large", "timeout", "unsupported-method",
    "not-ready", "agent-gone", "agent-not-running", "journal-unavailable", "conflict", "too-many", "internal",
)
