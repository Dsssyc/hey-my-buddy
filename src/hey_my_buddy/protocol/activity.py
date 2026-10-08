"""Bounded native activity facts, monotonicity and live publication coalescing."""
from __future__ import annotations

import json

import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from ..errors import BoardError

#: The only phases a caller may report. ``unknown`` is an honest observed state, not
#: permission to invent a percentage or an ETA.
PHASES = (
    "starting",
    "waiting-model",
    "streaming-model",
    "tool-running",
    "waiting-external",
    "waiting-host",
    "finishing",
    "unknown",
)

ACTIVITY_FIELDS = frozenset(
    {
        "phase",
        "observedAt",
        "eventSeq",
        "nativeSessionId",
        "lastNativeActivityAt",
        "lastToolActivityAt",
        "toolName",
        "waitingReason",
        "counts",
    }
)
COUNT_FIELDS = frozenset({"modelTurns", "toolCalls"})

MAX_TIMESTAMP = 64
MAX_SESSION_ID = 256
MAX_TOOL_NAME = 64
MAX_WAITING_REASON = 256
MAX_EVENT_SEQ = 2**53 - 1
MAX_COUNT = 2**31 - 1

#: An ISO-8601 date-time prefix. The exact precision stays the controller's choice.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?P<fraction>\d+))?(?:Z|[+-]\d{2}:\d{2})$")

#: Same-phase token observations are coalesced within this publication window.
DEFAULT_MIN_INTERVAL_SECONDS = 2.0


def _invalid(message: str, **details: Any) -> BoardError:
    return BoardError("INVALID_ARGUMENT", message, **details)


def _bounded_string(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise _invalid(f"activity.{field} must be a nonempty string", field=f"activity.{field}")
    value = value.strip()
    if len(value) > maximum:
        raise _invalid(f"activity.{field} must be at most {maximum} characters", field=f"activity.{field}")
    return value


def _timestamp(value: Any, field: str) -> str:
    value = _bounded_string(value, field, maximum=MAX_TIMESTAMP)
    if not _TIMESTAMP.fullmatch(value):
        raise _invalid(f"activity.{field} must be an ISO-8601 timestamp", field=f"activity.{field}")
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise _invalid(f"activity.{field} must be an ISO-8601 timestamp", field=f"activity.{field}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid(f"activity.{field} must be a real ISO-8601 timestamp", field=f"activity.{field}") from None
    return value


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= MAX_COUNT):
        raise _invalid(
            f"activity.counts.{field} must be a nonnegative integer no larger than {MAX_COUNT}",
            field=f"activity.counts.{field}",
        )
    return value


def normalize_activity(value: Any) -> dict:
    """Validate one activity payload into its canonical, bounded projection.

    ``None`` on a known key means "unknown" and is preserved as an omitted field;
    it is never turned into a zero count. Unknown keys and invalid values are
    rejected instead of being stored or silently dropped.
    """
    if not isinstance(value, dict):
        raise _invalid("activity must be a JSON object", field="activity")
    unknown = sorted(set(value) - ACTIVITY_FIELDS)
    if unknown:
        raise _invalid(f"Unknown activity field: {unknown[0]}", field=unknown[0])
    if value.get("phase") is None:
        raise _invalid("activity.phase is required", field="activity.phase")
    phase = value["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise _invalid(
            f"activity.phase must be one of {', '.join(PHASES)}",
            field="activity.phase",
        )
    normalized: dict[str, Any] = {"phase": phase}
    if value.get("observedAt") is not None:
        normalized["observedAt"] = _timestamp(value["observedAt"], "observedAt")
    elif value.get("eventSeq") is None:
        # Ordering needs *something* durable: an observed timestamp or an event
        # sequence. A phase alone cannot be placed on the monotone timeline.
        raise _invalid("activity.observedAt or activity.eventSeq is required to order the receipt")
    if value.get("eventSeq") is not None:
        event_seq = value["eventSeq"]
        if isinstance(event_seq, bool) or not isinstance(event_seq, int) or not (0 <= event_seq <= MAX_EVENT_SEQ):
            raise _invalid(
                f"activity.eventSeq must be a nonnegative integer no larger than {MAX_EVENT_SEQ}",
                field="activity.eventSeq",
            )
        normalized["eventSeq"] = event_seq
    if value.get("nativeSessionId") is not None:
        normalized["nativeSessionId"] = _bounded_string(value["nativeSessionId"], "nativeSessionId", maximum=MAX_SESSION_ID)
    if value.get("lastNativeActivityAt") is not None:
        normalized["lastNativeActivityAt"] = _timestamp(value["lastNativeActivityAt"], "lastNativeActivityAt")
    if value.get("lastToolActivityAt") is not None:
        normalized["lastToolActivityAt"] = _timestamp(value["lastToolActivityAt"], "lastToolActivityAt")
    if value.get("toolName") is not None:
        normalized["toolName"] = _bounded_string(value["toolName"], "toolName", maximum=MAX_TOOL_NAME)
    if value.get("waitingReason") is not None:
        normalized["waitingReason"] = _bounded_string(value["waitingReason"], "waitingReason", maximum=MAX_WAITING_REASON)
    counts = value.get("counts")
    if counts is not None:
        if not isinstance(counts, dict):
            raise _invalid("activity.counts must be a JSON object", field="activity.counts")
        unknown_counts = sorted(set(counts) - COUNT_FIELDS)
        if unknown_counts:
            raise _invalid(f"Unknown activity.counts field: {unknown_counts[0]}", field=unknown_counts[0])
        bounded: dict[str, int] = {}
        for name in sorted(COUNT_FIELDS):
            if counts.get(name) is not None:
                bounded[name] = _count(counts[name], name)
        if bounded:
            normalized["counts"] = bounded
    return normalized


def _instant(value: str) -> tuple[int, Decimal]:
    """Exact UTC second and fractional second for an already validated timestamp."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    seconds = parsed.toordinal() * 86400 + parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    seconds -= int(parsed.utcoffset().total_seconds())
    fraction = _TIMESTAMP.fullmatch(value).group("fraction")
    return seconds, Decimal(f"0.{fraction}") if fraction else Decimal(0)


def recency(activity: dict) -> tuple[int, tuple[int, Decimal]]:
    """Compare native sequence first, then the actual UTC instant."""
    stamp = activity.get("observedAt")
    return (int(activity.get("eventSeq") or 0), _instant(stamp) if stamp else (0, Decimal(0)))


def equality_key(activity: dict | None) -> str:
    """A stable comparison key, so an identical repeat is recognizable."""
    return json.dumps(activity or {}, sort_keys=True, separators=(",", ":"))


def is_newer(candidate: dict, previous: dict | None) -> bool:
    """True when ``candidate`` advances the projection.

    The comparison is deterministic: an identical receipt is an idempotent repeat,
    an explicit event sequence wins otherwise, an observed timestamp breaks its
    ties, and an older receipt is never newer. An unknown ordering key is never
    invented.
    """
    if previous is None:
        return True
    if equality_key(candidate) == equality_key(previous):
        return False
    return recency(candidate) > recency(previous)










class ActivityPublisher:
    """Normalize and coalesce actual observations before the live owner receives them."""

    def __init__(self, publish: Callable[[dict], bool] | None, *,
                 min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._publish = publish
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._clock = clock
        self._last: dict | None = None
        self._written_at: float | None = None

    def publish(self, activity: dict) -> bool:
        payload = normalize_activity(activity)
        if not is_newer(payload, self._last):
            return False
        now = self._clock()
        phase_changed = self._last is None or payload["phase"] != self._last.get("phase")
        if not phase_changed and self._written_at is not None and now - self._written_at < self.min_interval_seconds:
            return False
        if self._publish is not None and not self._publish(payload):
            return False
        self._last = payload
        self._written_at = now
        return True

    def current(self) -> dict | None:
        return None if self._last is None else dict(self._last)




__all__ = [
    "ACTIVITY_FIELDS",
    "ActivityPublisher",
    "COUNT_FIELDS",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "PHASES",
    "equality_key",
    "is_newer",
    "normalize_activity",
    "recency",
]
