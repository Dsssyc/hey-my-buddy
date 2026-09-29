"""Native token usage, harness quota and last-assistant-message normalization.

Adapters observe native harness records; they never invent numbers. This module
turns one observed record into the bounded, canonical shape the board persists,
and returns ``None`` whenever no usable native fact exists. It is the single
place where the per-harness accounting rules meet:

* DSH reports ``inputTokens`` **excluding** cache read/write tokens, so the
  canonical ``inputTokens`` (a total that includes cached input) adds them once.
* Codex reports ``input_tokens`` **already including** ``cached_input_tokens``,
  so its cache counters are never added again.

The canonical token usage therefore always means ``inputBasis == "includes-cached"``,
``scope == "attempt"`` (one governed attempt, never a whole native session) and an
explicit ``completeness``. Unknown fields stay unknown; a missing cache counter is
never turned into zero, and a session-cumulative record is refused instead of being
counted as this attempt.

Quota is a native fact too: an observation time, a native source, the applicable
provider/account scope and the reported windows. A window without a native
percentage is dropped rather than reported as ``0``; a quota failure keeps its
native structured code.

Every ``normalize_*`` function is idempotent: replaying the same record through it
again returns the same canonical value and never accumulates a second time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Canonical document versions. A reader accepts exactly these versions.
TOKEN_USAGE_VERSION = 1
QUOTA_VERSION = 1
QUOTA_FAILURE_VERSION = 1
LAST_ASSISTANT_MESSAGE_VERSION = 1

#: The only attempt scope this module accepts. A session- or task-cumulative
#: record must never be reported as one attempt's usage.
ATTEMPT_SCOPE = "attempt"

#: Canonical input accounting: the reported total already contains cached input.
INPUT_BASIS_INCLUDES_CACHED = "includes-cached"
#: Native DSH accounting: the reported total excludes cache read/write tokens.
INPUT_BASIS_EXCLUDES_CACHED = "excludes-cached"
INPUT_BASES = (INPUT_BASIS_INCLUDES_CACHED, INPUT_BASIS_EXCLUDES_CACHED)

COMPLETENESS_VALUES = ("complete", "partial", "unknown")

#: Largest counter accepted from a native record; larger values are not trusted.
MAX_COUNT = 2**53 - 1
MAX_SOURCE = 120
MAX_TIMESTAMP = 64
MAX_WINDOW_NAME = 64
MAX_WINDOWS = 8
#: The retained native root assistant text, matching the Codex checkpoint bound.
MAX_ASSISTANT_MESSAGE_BYTES = 65536
MAX_SIDECAR_BYTES = 96 * 1024

#: Sidecar document fields for one attempt's native usage observation.
SIDECAR_VERSION = 1
SIDECAR_FIELDS = frozenset({"version", "taskId", "attemptId", "generation", "updatedAt", "nativeUsage"})

TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Native codes that mean "the account quota or budget is exhausted".
_QUOTA_NATIVE_CODES = frozenset({
    "QUOTA",
    "insufficient_quota",
    "quota_exceeded",
    "usage_limit_reached",
    "usage_limit_exceeded",
    "usagelimitexceeded",
    "sessionbudgetexceeded",
    "workspace_owner_usage_limit_reached",
    "workspace_member_usage_limit_reached",
    "workspace_owner_credits_depleted",
    "workspace_member_credits_depleted",
})
#: Native codes that mean "transiently rate limited" rather than exhausted quota.
_RATE_NATIVE_CODES = frozenset({"RATE_LIMIT", "rate_limit_reached", "ratelimitexceeded", "model_rate_limited"})


def _is_count(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= MAX_COUNT


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _bounded_string(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return None
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    return value if len(value) <= maximum else None


def _timestamp(value: Any) -> str | None:
    """Canonical UTC ISO-8601 for a native timestamp; never a fabricated one."""
    if _is_number(value):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    text = _bounded_string(value, maximum=MAX_TIMESTAMP)
    if text is None or not TIMESTAMP.fullmatch(text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate_utf8(text: str, maximum: int) -> tuple[str, int]:
    """Head-truncate to a byte bound without splitting a character; return bytes kept."""
    raw = text.encode()
    if len(raw) <= maximum:
        return text, len(raw)
    kept = raw[:maximum]
    while kept:
        try:
            return kept.decode(), len(kept)
        except UnicodeDecodeError:
            kept = kept[:-1]
    return "", 0


def normalize_token_usage(value: Any) -> dict | None:
    """Validate one observed usage record into the canonical attempt projection.

    Accepted input fields (unknown keys are ignored, never stored):

    ``inputTokens``
        The native prompt-side count. Its meaning is declared by ``inputBasis``.
    ``cachedInputTokens``
        The native cached-input count. ``None`` or absent means unknown.
    ``outputTokens``
        The native output count.
    ``reasoningOutputTokens``
        The native reasoning-output count when the harness reports one.
    ``cacheReadTokens`` / ``cacheWriteTokens``
        Native DSH cache counters, used to derive ``cachedInputTokens`` when the
        native record did not report it directly.
    ``inputBasis``
        ``"excludes-cached"`` (DSH: add cache once) or ``"includes-cached"``
        (Codex and every canonical record: never add cache again). When absent,
        a record carrying native cache counters is treated as ``excludes-cached``
        so a raw DSH record can never be silently undercounted.
    ``source``
        Bounded label of the native origin; ``"unknown"`` when absent.
    ``scope``
        Must be ``"attempt"`` (the default). Any other scope is refused: a
        session-cumulative record is not this attempt's usage.
    ``completeness``
        ``"complete"``/``"partial"``/``"unknown"`` (default ``"unknown"``).
    ``nativeRecords``
        How many native usage observations contributed, at least ``1``.

    Returns ``None`` when the value carries no usable counter or contradicts
    itself. It never estimates: an unknown counter stays ``None``.
    """
    if not isinstance(value, dict):
        return None
    if "version" in value and (type(value["version"]) is not int or value["version"] != 1):
        return None
    scope = value.get("scope")
    if scope is not None and scope != ATTEMPT_SCOPE:
        return None
    for key in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens",
                "cacheReadTokens", "cacheWriteTokens"):
        present = value.get(key)
        if present is not None and not _is_count(present):
            return None
    input_tokens = value.get("inputTokens")
    output_tokens = value.get("outputTokens")
    if input_tokens is None and output_tokens is None:
        return None
    cached = value.get("cachedInputTokens")
    if cached is None:
        read = value.get("cacheReadTokens")
        write = value.get("cacheWriteTokens")
        total = value.get("totalTokens")
        if _is_count(total) and input_tokens is not None and output_tokens is not None and total >= input_tokens + output_tokens:
            cached = total - input_tokens - output_tokens
        elif read is not None and write is not None:
            cached = read + write
    if cached is not None and not _is_count(cached):
        return None
    basis = value.get("inputBasis")
    if basis is not None and basis not in INPUT_BASES:
        return None
    if basis is None:
        basis = INPUT_BASIS_EXCLUDES_CACHED if "cacheReadTokens" in value or "cacheWriteTokens" in value \
            else INPUT_BASIS_INCLUDES_CACHED
    if basis == INPUT_BASIS_EXCLUDES_CACHED and cached is None:
        input_tokens = None
    if input_tokens is None and output_tokens is None:
        return None
    if basis == INPUT_BASIS_EXCLUDES_CACHED and cached is not None and input_tokens is not None:
        unified = input_tokens + cached
        if unified > MAX_COUNT:
            return None
        input_tokens = unified
    if input_tokens is not None and cached is not None and cached > input_tokens:
        # An internally inconsistent record is not a fact this module may repair.
        return None
    source = _bounded_string(value.get("source"), maximum=MAX_SOURCE) or "unknown"
    completeness = value.get("completeness")
    if completeness is None:
        completeness = "unknown"
    if completeness not in COMPLETENESS_VALUES:
        return None
    if completeness == "complete" and any(count is None for count in (input_tokens, cached, output_tokens)):
        completeness = "partial"
    records = value.get("nativeRecords")
    if records is None:
        records = 1
    if not _is_count(records) or records < 1:
        return None
    normalized = {
        "version": TOKEN_USAGE_VERSION,
        "inputTokens": input_tokens,
        "cachedInputTokens": cached,
        "outputTokens": output_tokens,
        "inputBasis": INPUT_BASIS_INCLUDES_CACHED,
        "source": source,
        "scope": ATTEMPT_SCOPE,
        "completeness": completeness,
        "nativeRecords": records,
    }
    coverage = value.get("coverage")
    if coverage in ("native-root-session", "native-root-thread", "native-attempt"):
        normalized["coverage"] = coverage
    reasoning = value.get("reasoningOutputTokens")
    if reasoning is not None:
        normalized["reasoningOutputTokens"] = reasoning
    return normalized


def normalize_quota_window(value: Any) -> dict | None:
    """One native quota window, or ``None`` when no percentage was reported."""
    if not isinstance(value, dict):
        return None
    name = _bounded_string(value.get("name"), maximum=MAX_WINDOW_NAME)
    used = value.get("usedPercent")
    if name is None or not _is_number(used) or not (0 <= used <= 100):
        # A window without its native percentage is dropped, never shown as 0.
        return None
    used_percent: int | float = int(used) if float(used).is_integer() else float(used)
    window = {"name": name, "usedPercent": used_percent}
    resets = value.get("resetsAt")
    if resets is not None:
        timestamp = _timestamp(resets)
        if timestamp is not None:
            window["resetsAt"] = timestamp
    duration = value.get("windowDurationMins")
    if _is_count(duration) and duration > 0:
        window["windowDurationMins"] = duration
    limit_id = _bounded_string(value.get("limitId"), maximum=MAX_WINDOW_NAME)
    if limit_id is not None:
        window["limitId"] = limit_id
    return window


def normalize_quota(value: Any) -> dict | None:
    """Validate one native quota observation into its canonical bounded record.

    Accepted input fields: ``source``, ``observedAt`` (ISO-8601 or Unix seconds),
    ``provider``, ``nativeAccountId``, ``limitId``, ``planType``,
    ``ordinaryUsageAllowed``, ``reachedType`` and ``windows``. Each window is
    ``{name, usedPercent, resetsAt?, windowDurationMins?, limitId?}``.

    Returns ``None`` when the value carries no fact at all: no window, no
    ordinary-usage permission and no reached state. Missing fields are never
    filled in with zero, and a window without a native percentage is dropped.
    """
    if not isinstance(value, dict):
        return None
    if "version" in value and (type(value["version"]) is not int or value["version"] != 1):
        return None
    observed_at = _timestamp(value.get("observedAt"))
    if observed_at is None:
        return None
    windows: list[dict] = []
    raw_windows = value.get("windows")
    if raw_windows is not None:
        if not isinstance(raw_windows, list) or len(raw_windows) > MAX_WINDOWS:
            # An unbounded window list is refused instead of silently trimmed.
            return None
        for item in raw_windows:
            window = normalize_quota_window(item)
            if window is not None:
                windows.append(window)
    allowed = value.get("ordinaryUsageAllowed")
    ordinary: bool | None = allowed if isinstance(allowed, bool) else None
    reached = _bounded_string(value.get("reachedType"), maximum=MAX_WINDOW_NAME)
    if not windows and ordinary is None and reached is None:
        return None
    scope_source = value.get("scope") if isinstance(value.get("scope"), dict) else value
    scope = {
        "provider": _bounded_string(scope_source.get("provider"), maximum=MAX_WINDOW_NAME),
        "nativeAccountId": _bounded_string(scope_source.get("nativeAccountId"), maximum=MAX_WINDOW_NAME),
        "limitId": _bounded_string(scope_source.get("limitId"), maximum=MAX_WINDOW_NAME),
        "planType": _bounded_string(scope_source.get("planType"), maximum=MAX_WINDOW_NAME),
    }
    normalized = {
        "version": QUOTA_VERSION,
        "source": _bounded_string(value.get("source"), maximum=MAX_SOURCE) or "unknown",
        "observedAt": observed_at,
        "scope": scope,
        "windows": windows,
    }
    if ordinary is not None:
        normalized["ordinaryUsageAllowed"] = ordinary
    if reached is not None:
        normalized["reachedType"] = reached
    return normalized


def classify_quota_code(native_code: str) -> str:
    """Map one native structured code onto the bounded quota classification."""
    if native_code in _QUOTA_NATIVE_CODES:
        return "quota-exceeded"
    if native_code in _RATE_NATIVE_CODES:
        return "rate-limited"
    # Camel-case harness codes (``usageLimitExceeded``) fold onto the same words
    # as snake_case ones (``usage_limit_reached``); the native code is unchanged.
    folded = re.sub(r"[_-]", "", native_code).lower()
    if folded in {re.sub(r"[_-]", "", code).lower() for code in _QUOTA_NATIVE_CODES}:
        return "quota-exceeded"
    if folded in {re.sub(r"[_-]", "", code).lower() for code in _RATE_NATIVE_CODES}:
        return "rate-limited"
    return "unknown"


def normalize_quota_failure(value: Any) -> dict | None:
    """One native quota/rate-limit failure, keeping its structured native code.

    The canonical ``code`` is a bounded classification; ``nativeCode`` is the
    harness's own machine code and is never translated away.
    """
    if not isinstance(value, dict):
        return None
    if "version" in value and (type(value["version"]) is not int or value["version"] != 1):
        return None
    native_code = _bounded_string(value.get("nativeCode"), maximum=MAX_SOURCE)
    if native_code is None:
        return None
    observed_at = _timestamp(value.get("observedAt"))
    failure = {
        "version": QUOTA_FAILURE_VERSION,
        "code": classify_quota_code(native_code),
        "nativeCode": native_code,
        "source": _bounded_string(value.get("source"), maximum=MAX_SOURCE) or "unknown",
    }
    if observed_at is not None:
        failure["observedAt"] = observed_at
    return failure


def normalize_last_assistant_message(value: Any, *, source: str | None = None) -> dict | None:
    """Bound one native root assistant text for cross-harness reconstruction.

    The text is the harness's own root assistant output, retained as evidence for
    a continuation that cannot resume the native session. It is never a tool
    result and never a Host instruction. ``sourceBytes`` and ``sha256`` always
    describe the full untruncated native text; ``text`` is head-truncated to
    ``MAX_ASSISTANT_MESSAGE_BYTES`` on a UTF-8 boundary when needed.
    """
    if not isinstance(value, dict):
        return None
    if "version" in value and (type(value["version"]) is not int or value["version"] != 1):
        return None
    raw_text = value.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip() or "\0" in raw_text:
        return None
    try:
        full = raw_text.encode()
    except UnicodeError:
        return None
    digest = value.get("sha256")
    declared_bytes = value.get("sourceBytes")
    declared_truncated = value.get("truncated")
    if digest is None and declared_bytes is None and declared_truncated is None:
        source_bytes, sha256, truncated = len(full), hashlib.sha256(full).hexdigest(), False
    else:
        if not _is_count(declared_bytes) or declared_bytes < 0:
            return None
        if not isinstance(digest, str) or not _SHA256.match(digest):
            return None
        if not isinstance(declared_truncated, bool):
            return None
        if not declared_truncated and (declared_bytes != len(full) or digest != hashlib.sha256(full).hexdigest()):
            return None
        if declared_bytes < len(full):
            return None
        source_bytes, sha256, truncated = declared_bytes, digest, declared_truncated
    text, kept = _truncate_utf8(raw_text, MAX_ASSISTANT_MESSAGE_BYTES)
    if len(json.dumps(text, ensure_ascii=False).encode()) > MAX_ASSISTANT_MESSAGE_BYTES:
        lower, upper = 0, len(text)
        while lower < upper:
            middle = (lower + upper + 1) // 2
            if len(json.dumps(text[:middle], ensure_ascii=False).encode()) <= MAX_ASSISTANT_MESSAGE_BYTES:
                lower = middle
            else:
                upper = middle - 1
        text = text[:lower]
        truncated = True
    if kept < len(full):
        truncated = True
    label = _bounded_string(value.get("source"), maximum=MAX_SOURCE) if source is None \
        else _bounded_string(source, maximum=MAX_SOURCE)
    source_id = value.get("sourceId")
    if source_id is None:
        source_id = value.get("itemId")
    normalized = {
        "version": LAST_ASSISTANT_MESSAGE_VERSION,
        "text": text,
        "source": label or "unknown",
        "sourceId": _bounded_string(source_id, maximum=256),
        "sourceBytes": source_bytes,
        "sha256": sha256,
        "truncated": truncated,
    }
    phase = _bounded_string(value.get("phase"), maximum=MAX_WINDOW_NAME)
    if phase is not None:
        normalized["phase"] = phase
    return normalized


def read_sidecar(path: str | Path, *, task_id: str, attempt_id: str, generation: int) -> dict | None:
    """Read one attempt-bound native-usage sidecar, or ``None``.

    Mirrors the activity sidecar boundary: no symlink, a regular file, a bounded
    size, valid JSON, the current document version and an exact attempt binding.
    A foreign, malformed or oversized sidecar never contributes usage.
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
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != SIDECAR_VERSION:
        return None
    if set(value) - SIDECAR_FIELDS:
        return None
    if value.get("taskId") != task_id or value.get("attemptId") != attempt_id:
        return None
    if not _is_count(generation) or generation < 1 or type(value.get("generation")) is not int or value.get("generation") != generation:
        return None
    return value


__all__ = [
    "ATTEMPT_SCOPE",
    "COMPLETENESS_VALUES",
    "INPUT_BASES",
    "INPUT_BASIS_EXCLUDES_CACHED",
    "INPUT_BASIS_INCLUDES_CACHED",
    "LAST_ASSISTANT_MESSAGE_VERSION",
    "MAX_ASSISTANT_MESSAGE_BYTES",
    "QUOTA_FAILURE_VERSION",
    "QUOTA_VERSION",
    "SIDECAR_FIELDS",
    "SIDECAR_VERSION",
    "TOKEN_USAGE_VERSION",
    "classify_quota_code",
    "normalize_last_assistant_message",
    "normalize_quota",
    "normalize_quota_failure",
    "normalize_quota_window",
    "normalize_token_usage",
    "read_sidecar",
]
