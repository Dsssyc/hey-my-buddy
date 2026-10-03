"""Service persistence, credential lineage and freshness of native evidence."""
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


def _quota_key(adapter, account):
    return 'account-quota:' + canonical_json([adapter, account['source'], account['credentialRevision']])


def latest_quota(connection, adapter, account=None):
    """Read the latest observation for one service-selected credential lineage."""
    from ..catalog.accounts import identity, selection
    account = identity(account if account is not None else selection(connection, adapter))
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_quota_key(adapter, account),)).fetchone()
    if row:
        return json.loads(row[0])
    row = connection.execute('SELECT quota_json FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    quota = json.loads(row[0]) if row and row[0] else None
    if not isinstance(quota, dict):
        return None
    tagged = quota.get('account') or {'source': 'native', 'credentialRevision': 0}
    return quota if tagged == account else None


def record_quota(connection, adapter, quota, account=None, *, now=None):
    """Persist trusted attribution; native payload account labels are ignored.

    Every lineage keeps its own monotone observation in meta. quota_json remains
    the current display projection; an old attempt's receipt cannot replace it.
    """
    from ..catalog.accounts import identity, selection
    from ..routing.quota_routing import record
    account = identity(account if account is not None else selection(connection, adapter))
    if not isinstance(quota, dict) or _time(quota.get('observedAt')) is None:
        return
    observed = {**quota, 'account': account}
    record(connection, adapter, observed, account=account, now=now)
    previous = latest_quota(connection, adapter, account=account)
    previous_time = _time(previous.get('observedAt')) if previous else None
    if previous_time and previous_time >= _time(observed['observedAt']):
        return
    connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (_quota_key(adapter, account), canonical_json(observed)))
    if account == identity(selection(connection, adapter)):
        connection.execute('UPDATE harness_health SET quota_json=? WHERE adapter=?', (canonical_json(observed), adapter))


def persist(connection, attempt, result):
    """Called once inside the result transaction, after actor and replay checks."""
    if not isinstance(result, dict) or not any(result.get(key) is not None for key in ("tokenUsage", "quota", "quotaFailure")):
        return
    from ...protocol.usage import normalize_token_usage, normalize_quota, normalize_quota_failure
    from ..catalog.accounts import attempt_account
    usage = normalize_token_usage(result.get("tokenUsage"))
    connection.execute("UPDATE attempts SET token_usage_json=? WHERE attempt_id=?",
                       (canonical_json(usage) if usage is not None else None, attempt["attempt_id"]))
    quota = normalize_quota(result.get("quota"))
    failure = normalize_quota_failure(result.get("quotaFailure"))
    if quota is None and failure is not None and failure["code"] == "quota-exceeded":
        quota = normalize_quota({"source": failure["source"], "observedAt": failure.get("observedAt"),
                                 "reachedType": failure["nativeCode"], 'resetsAt': failure.get('resetsAt'), "windows": []})
    account = attempt_account(connection, attempt)
    if quota is None or account is None:
        return
    # The claim's service-owned account, not the receipt's claimed account,
    # identifies this observation even if the effective selection has changed.
    observed = {**quota, "attemptId": attempt["attempt_id"], "provider": (quota.get("scope") or {}).get("provider") or attempt["model_provider"]}
    record_quota(connection, attempt["model_adapter"] or attempt["adapter"], observed,
                 account=account)


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


def exhausted(connection, configuration, *, account=None, now=None):
    from ..routing.quota_routing import exhausted as routing_exhausted
    return routing_exhausted(connection, configuration, account=account, now=now)

def warnings(connection, configuration, *, account=None, now=None):
    if not configuration or not configuration.get("adapter"):
        return []
    quota = quota_view(latest_quota(connection, configuration["adapter"], account=account), now=now)
    explicit = exhausted(connection, configuration, account=account, now=now)
    if explicit is not None:
        return [{**explicit, "adapter": configuration["adapter"], "provider": configuration["provider"],
                 "message": "A recent native observation reports this selected configuration has exhausted its quota."}]
    if not quota or quota["stale"] or quota.get("provider") not in (None, configuration.get("provider")):
        return []
    from ...protocol.usage import classify_quota_code
    if quota.get("reachedType") and classify_quota_code(quota["reachedType"]) == "rate-limited":
        return [{"code": "HARNESS_RATE_LIMIT_REPORTED", "adapter": configuration["adapter"],
                 "provider": quota.get("provider"), "account": quota.get("account"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a temporary rate limit."}]
    if quota.get("reachedType") or quota.get("ordinaryUsageAllowed") is False:
        return [{"code": "HARNESS_QUOTA_LIMIT_REPORTED", "adapter": configuration["adapter"],
                 "provider": quota.get("provider"), "account": quota.get("account"), "source": quota.get("source"), "observedAt": quota.get("observedAt"),
                 "reachedType": quota.get("reachedType"), "message": "A recent native observation reported a quota limit; its current reset state may be unknown."}]
    return [{"code": "HARNESS_QUOTA_NEAR_LIMIT", "adapter": configuration["adapter"],
             "provider": quota.get("provider"), "account": quota.get("account"), "source": quota.get("source"),
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
    if failure is not None and failure["code"] != "unknown":
        return failure
    if result.get("code") == "quota-rejected":
        return {"code": "quota-exceeded", "nativeCode": "quota-rejected", "source": (adapter or "harness") + "/native-quota"}
    return None
