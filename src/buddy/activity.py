"""Bounded native-activity projection shared by adapters, Workers and the board.

A native controller (DSH, ZCode, Codex, ...) observes its own phases, tool calls and
waiting reasons. It publishes them through this module: one attempt-private
``activity.json`` sidecar, updated atomically and throttled, which the Worker that
owns the attempt forwards with ``worker_progress``. Nothing here is a scheduler and
nothing here is a second source of truth — the service keeps only the latest bounded
projection per attempt.

The payload is a whitelist, never free text: phases come from a fixed set, counters
are nonnegative integers, strings have explicit bounds, and unknown keys are
rejected. Prompts, tool arguments, output text, credentials and hidden reasoning are
never part of an activity, and a heartbeat is never fabricated into one.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .db import utc_now
from .errors import BoardError

#: The sidecar document version. A reader accepts exactly this version; there is no
#: compatibility branch for older documents.
ACTIVITY_VERSION = 1
SIDECAR_FILE = "activity.json"

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
MAX_SIDECAR_BYTES = 16 * 1024

#: The complete sidecar document: the version, the attempt binding and the payload.
SIDECAR_FIELDS = frozenset({"version", "taskId", "attemptId", "generation", "updatedAt", "activity"})

#: An ISO-8601 date-time prefix. The exact precision stays the controller's choice.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?P<fraction>\d+))?(?:Z|[+-]\d{2}:\d{2})$")

#: How often a controller may rewrite the sidecar without a phase change. Repeated
#: token-level progress is coalesced into one durable observation.
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


def sidecar_path(directory: str | Path) -> Path:
    """The attempt-private sidecar path; it is never part of a public response."""
    return Path(directory) / SIDECAR_FILE


def validate_sidecar(value: Any, *, task_id: str, attempt_id: str, generation: int) -> dict:
    """Validate one sidecar document against its attempt binding.

    Any version, binding or payload mismatch is an error: a sidecar from another
    attempt, generation or task may never be forwarded as this attempt's activity.
    """
    if not isinstance(value, dict):
        raise _invalid("an activity sidecar must be a JSON object")
    unknown = sorted(set(value) - SIDECAR_FIELDS)
    if unknown:
        raise _invalid(f"Unknown activity sidecar field: {unknown[0]}", field=unknown[0])
    if type(value.get("version")) is not int or value["version"] != ACTIVITY_VERSION:
        raise _invalid("the activity sidecar version is not current", version=value.get("version"))
    if value.get("taskId") != task_id or value.get("attemptId") != attempt_id:
        raise _invalid("the activity sidecar belongs to a different task or attempt")
    if type(generation) is not int or type(value.get("generation")) is not int or value["generation"] != generation:
        raise _invalid("the activity sidecar belongs to a different attempt generation")
    return normalize_activity(value.get("activity"))


def read_sidecar(
    path: str | Path,
    *,
    task_id: str,
    attempt_id: str,
    generation: int,
) -> dict | None:
    """Read one sidecar, returning ``None`` for anything unreadable or unbound.

    A caller forwarding activity treats every failure the same way: nothing is
    published, and an absent, malformed or foreign sidecar never becomes an event.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SIDECAR_BYTES:
                return None
            raw = os.read(descriptor, MAX_SIDECAR_BYTES + 1)
            if len(raw) > MAX_SIDECAR_BYTES:
                return None
        finally:
            os.close(descriptor)
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    try:
        return validate_sidecar(value, task_id=task_id, attempt_id=attempt_id, generation=generation)
    except (BoardError, RecursionError):
        return None


def write_json_atomic(path: str | Path, value: dict) -> Path:
    """Durably replace one small JSON file: temp + fsync + rename + fsync parent."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path


class ActivitySidecar:
    """Atomic, throttled, attempt-bound writer for one native controller.

    The controller owns the file; the Worker only reads it. A write happens when the
    phase changes or when the throttle window has elapsed, and an identical or older
    receipt is never rewritten, so token-level progress cannot flood the file.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        task_id: str,
        attempt_id: str,
        generation: int,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.path = sidecar_path(directory)
        self.task_id = task_id
        self.attempt_id = attempt_id
        self.generation = int(generation)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._clock = clock
        self._last: dict | None = None
        self._written_at: float | None = None

    def publish(self, activity: dict) -> Path | None:
        """Publish one receipt, returning the written path or ``None`` when coalesced."""
        payload = normalize_activity(activity)
        if not is_newer(payload, self._last):
            return None
        now = self._clock()
        phase_changed = self._last is None or payload["phase"] != self._last.get("phase")
        if not phase_changed and self._written_at is not None:
            if now - self._written_at < self.min_interval_seconds:
                return None
        document = {
            "version": ACTIVITY_VERSION,
            "taskId": self.task_id,
            "attemptId": self.attempt_id,
            "generation": self.generation,
            "updatedAt": utc_now(),
            "activity": payload,
        }
        write_json_atomic(self.path, document)
        self._last = payload
        self._written_at = now
        return self.path

    def current(self) -> dict | None:
        return None if self._last is None else dict(self._last)

    def clear(self) -> None:
        """Remove the sidecar; nothing is invented afterwards."""
        self.path.unlink(missing_ok=True)


__all__ = [
    "ACTIVITY_FIELDS",
    "ACTIVITY_VERSION",
    "ActivitySidecar",
    "COUNT_FIELDS",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "PHASES",
    "SIDECAR_FIELDS",
    "SIDECAR_FILE",
    "equality_key",
    "is_newer",
    "normalize_activity",
    "read_sidecar",
    "recency",
    "sidecar_path",
    "validate_sidecar",
    "write_json_atomic",
]
