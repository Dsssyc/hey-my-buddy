"""Service persistence and freshness of native usage/quota evidence."""
from datetime import datetime, timezone
import json

from .db import canonical_json, utc_now


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def persist(connection, attempt, result):
    """Called once inside the result transaction, after actor and replay checks."""
    if not isinstance(result, dict) or not any(result.get(key) is not None for key in ("tokenUsage", "quota", "quotaFailure")):
        return
    from .usage import normalize_token_usage, normalize_quota, normalize_quota_failure
    usage = normalize_token_usage(result.get("tokenUsage"))
    connection.execute("UPDATE attempts SET token_usage_json=? WHERE attempt_id=?",
                       (canonical_json(usage) if usage is not None else None, attempt["attempt_id"]))
    quota = normalize_quota(result.get("quota"))
    failure = normalize_quota_failure(result.get("quotaFailure"))
    if quota is None and failure is not None and failure["code"] == "quota-exceeded":
        quota = normalize_quota({"source": failure["source"], "observedAt": failure.get("observedAt"),
                                 "reachedType": failure["nativeCode"], 'resetsAt': failure.get('resetsAt'), "windows": []})
    if quota is None or _time(quota.get("observedAt")) is None:
        return
    old = connection.execute("SELECT quota_json FROM harness_health WHERE adapter=?", (attempt["model_adapter"] or attempt["adapter"],)).fetchone()
    if old is None:
        return
    previous = json.loads(old["quota_json"]) if old["quota_json"] else None
    previous_time = _time(previous.get("observedAt")) if previous else None
    observed = {**quota, "attemptId": attempt["attempt_id"], "provider": (quota.get("scope") or {}).get("provider") or attempt["model_provider"]}
    from .quota_routing import record
    record(connection, attempt["model_adapter"] or attempt["adapter"], observed)
    if previous_time and previous_time >= _time(quota["observedAt"]):
        return
    connection.execute("UPDATE harness_health SET quota_json=? WHERE adapter=?",
                       (canonical_json(observed), attempt["model_adapter"] or attempt["adapter"]))


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


def exhausted(connection, configuration, *, now=None):
    from .quota_routing import exhausted as routing_exhausted
    return routing_exhausted(connection, configuration, now=now)

def warnings(connection, configuration, *, now=None):
    if not configuration or not configuration.get("adapter"):
        return []
    row = connection.execute("SELECT quota_json FROM harness_health WHERE adapter=?", (configuration["adapter"],)).fetchone()
    quota = quota_view(json.loads(row[0]) if row and row[0] else None, now=now)
    explicit = exhausted(connection, configuration, now=now)
    if explicit is not None:
        return [{**explicit, "adapter": configuration["adapter"], "provider": configuration["provider"],
                 "message": "A recent native observation reports this selected configuration has exhausted its quota."}]
    if not quota or quota["stale"] or quota.get("provider") not in (None, configuration.get("provider")):
        return []
    from .usage import classify_quota_code
    if quota.get("reachedType") and classify_quota_code(quota["reachedType"]) == "rate-limited":
        return [{"code": "HARNESS_RATE_LIMIT_REPORTED", "adapter": configuration["adapter"],
                 "provider": quota.get("provider"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a temporary rate limit."}]
    if quota.get("reachedType") or quota.get("ordinaryUsageAllowed") is False:
        return [{"code": "HARNESS_QUOTA_LIMIT_REPORTED", "adapter": configuration["adapter"],
                 "provider": quota.get("provider"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a quota limit; its current reset state may be unknown."}]
    return [{"code": "HARNESS_QUOTA_NEAR_LIMIT", "adapter": configuration["adapter"],
             "provider": quota.get("provider"), "source": quota.get("source"),
             "observedAt": quota.get("observedAt"), "window": window,
             "message": "Recent native quota observation is near its limit; review before continuing."}
            for window in quota["windows"] if not window["stale"]
            and isinstance(window.get("usedPercent"), (int, float)) and window["usedPercent"] >= 90]


def failure_view(result, *, adapter=None):
    """Only structured native quota failures affect the visible classification."""
    if not isinstance(result, dict):
        return None
    from .usage import normalize_quota_failure
    failure = normalize_quota_failure(result.get("quotaFailure"))
    if failure is not None and failure["code"] != "unknown":
        return failure
    if result.get("code") == "quota-rejected":
        return {"code": "quota-exceeded", "nativeCode": "quota-rejected", "source": (adapter or "harness") + "/native-quota"}
    return None
