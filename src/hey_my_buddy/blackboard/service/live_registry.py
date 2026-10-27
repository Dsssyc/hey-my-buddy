"""Service-owned connections to executions currently observed by this service."""
from __future__ import annotations

import threading

LIVE_ATTEMPT_STATES = frozenset({"starting", "executing", "finalizing"})
TERMINAL_TASK_STATES = frozenset({"completed", "concluded", "failed", "cancelled"})


class LiveRegistry:
    """Only owned, connected execution endpoints are answerable here.

    A restart begins empty. A failed endpoint observation removes its channel;
    neither absence nor disconnection is evidence that an attempt has stopped.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._channels: dict[str, object] = {}

    def bind(self, attempt_id: str, channel: object) -> None:
        with self._lock:
            previous = self._channels.get(attempt_id)
            self._channels[attempt_id] = channel
        if previous is not None and previous is not channel:
            previous.close(reason="replaced")

    def unbind(self, attempt_id: str, channel: object) -> None:
        with self._lock:
            if self._channels.get(attempt_id) is not channel:
                return
            del self._channels[attempt_id]
        channel.close(reason="service-detached")

    def channel_for(self, view: dict):
        attempt = view.get("selectedAttempt")
        if (not isinstance(attempt, dict)
                or view.get("state") in TERMINAL_TASK_STATES
                or attempt.get("executionState") not in LIVE_ATTEMPT_STATES
                or attempt.get("ownership") == "uncertain"):
            return None
        with self._lock:
            return self._channels.get(attempt.get("attemptId"))

    def close(self) -> None:
        with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
        for channel in channels:
            channel.close(reason="service-detached")
