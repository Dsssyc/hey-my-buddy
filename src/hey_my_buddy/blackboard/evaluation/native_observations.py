"""Execution attribution and freshness of native usage and quota evidence."""
from datetime import datetime, timezone
import json

from ..store.db import canonical_json, utc_now


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def latest_quota(connection, adapter):
    """The latest normalized observation reported by this harness."""
    row = connection.execute('SELECT quota_json FROM harness_quota_observations WHERE adapter=?', (adapter,)).fetchone()
    return json.loads(row[0]) if row else None


def record_quota(connection, adapter, quota, *, now=None):
    from ..routing.quota_routing import record
    if not isinstance(quota, dict) or _time(quota.get('observedAt')) is None:
        return
    record(connection, adapter, quota, now=now)
    previous = latest_quota(connection, adapter)
    if previous is not None and _time(previous['observedAt']) >= _time(quota['observedAt']):
        return
    connection.execute('INSERT INTO harness_quota_observations(adapter,quota_json) '
                       'VALUES(?,?) ON CONFLICT(adapter) DO UPDATE SET quota_json=excluded.quota_json',
                       (adapter, canonical_json(quota)))


def persist(connection, attempt, result):
    """Called once inside the result transaction, after actor and replay checks."""
    if not isinstance(result, dict) or not any(result.get(key) is not None for key in ("tokenUsage", "quota", "quotaFailure")):
        return
    from ...protocol.usage import normalize_token_usage, normalize_quota, normalize_quota_failure
    usage = normalize_token_usage(result.get("tokenUsage"))
    connection.execute("UPDATE attempts SET token_usage_json=? WHERE attempt_id=?",
                       (canonical_json(usage) if usage is not None else None, attempt["attempt_id"]))
    quota = normalize_quota(result.get("quota"))
    failure = normalize_quota_failure(result.get("quotaFailure"))
    if quota is None and failure is not None and failure["code"] == "quota-exceeded":
        quota = normalize_quota({"source": failure["source"], "observedAt": failure.get("observedAt"),
                                 "reachedType": failure["nativeCode"], "ordinaryUsageAllowed": False,
                                 'resetsAt': failure.get('resetsAt'), "windows": []})
    adapter = attempt['model_adapter'] or attempt['adapter']
    from ...protocol.harness_names import HARNESS_NAMES
    if quota is None or adapter not in HARNESS_NAMES:
        return
    # Attribute to the frozen execution; a later model selection cannot relabel it.
    observed = {**quota, "attemptId": attempt["attempt_id"], "provider": (quota.get("scope") or {}).get("provider") or attempt["model_provider"]}
    record_quota(connection, adapter, observed)


def quota_view(value, *, now=None):
    if not isinstance(value, dict):
        return None
    present = _time(now or utc_now())
    observed = _time(value.get("observedAt"))
    stale = observed is None or present is None or not 0 <= (present - observed).total_seconds() <= 3600
    windows = []
    for window in value.get("windows", []):
        reset = _time(window.get("resetsAt"))
        windows.append({**window, "stale": stale or window.get("invalidReset") is True or
                        ("resetsAt" in window and reset is None) or bool(reset and present and reset <= present)})
    return {**value, "windows": windows, "stale": stale or bool(windows and all(w["stale"] for w in windows))}


def exhausted(connection, buddy, *, now=None):
    from ..routing.quota_routing import exhausted as routing_exhausted
    return routing_exhausted(connection, buddy, now=now)

def warnings(connection, buddy, *, now=None):
    from ...protocol.harness_names import HARNESS_NAMES
    if not buddy or buddy.get("adapter") not in HARNESS_NAMES:
        return []
    quota = quota_view(latest_quota(connection, buddy["adapter"]), now=now)
    explicit = exhausted(connection, buddy, now=now)
    if explicit is not None:
        return [{**explicit, "adapter": buddy["adapter"], "provider": buddy["provider"],
                 "message": "A recent native observation reports this selected buddy has exhausted its quota."}]
    if not quota or quota["stale"] or quota.get("provider") not in (None, buddy.get("provider")):
        return []
    from ...protocol.usage import classify_quota_code
    if quota.get("reachedType") and classify_quota_code(quota["reachedType"]) == "rate-limited":
        return [{"code": "HARNESS_RATE_LIMIT_REPORTED", "adapter": buddy["adapter"],
                 "provider": quota.get("provider"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a temporary rate limit."}]
    if quota.get("reachedType") or quota.get("ordinaryUsageAllowed") is False:
        return [{"code": "HARNESS_QUOTA_LIMIT_REPORTED", "adapter": buddy["adapter"],
                 "provider": quota.get("provider"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a quota limit; its current reset state may be unknown."}]
    return [{"code": "HARNESS_QUOTA_NEAR_LIMIT", "adapter": buddy["adapter"],
             "provider": quota.get("provider"), "source": quota.get("source"),
             "observedAt": quota.get("observedAt"), "window": window,
             "message": "Recent native quota observation is near its limit; review before continuing."}
            for window in quota["windows"] if not window["stale"]
            and isinstance(window.get("usedPercent"), (int, float)) and window["usedPercent"] >= 90]


def failure_view(result, *, adapter=None):
    """Only structured native quota failures affect the visible classification."""
    if not isinstance(result, dict):
        return None
    from ...protocol.usage import normalize_quota_failure
    failure = normalize_quota_failure(result.get("quotaFailure"))
    if failure is not None:
        return failure
    if result.get("code") == "quota-rejected":
        return {"code": "quota-exceeded", "nativeCode": "quota-rejected", "source": (adapter or "harness") + "/native-quota"}
    return None
