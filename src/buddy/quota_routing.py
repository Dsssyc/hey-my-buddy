"""Native exhaustion persists until its reset or a newer available observation.

Display freshness is separate. Unknown/partial observations cannot clear an
exhaustion, and billing labels never affect these routing facts.
"""
from __future__ import annotations
import json
from .db import canonical_json, utc_now
from .usage import classify_quota_code, identifier


def evidence(quota, *, now=None):
    from .native_observations import _time
    if not isinstance(quota, dict) or quota.get('ambiguousLimits') is True:
        return None
    observed, present = _time(quota.get('observedAt')), _time(now or utc_now())
    if observed is None or present is None or observed > present:
        return None
    scope = quota.get('scope') or {}
    provider = identifier(quota.get('provider')) or identifier(scope.get('provider'))
    if provider is None:
        return None
    windows = [window for window in quota.get('windows', []) if isinstance(window, dict)]
    exhausted_windows = [w for w in windows if type(w.get('usedPercent')) in (int, float) and w['usedPercent'] >= 100]
    reached = quota.get('reachedType')
    classification = classify_quota_code(reached) if isinstance(reached, str) else 'unknown'
    blocked = classification == 'quota-exceeded' or quota.get('balanceZero') is True or bool(exhausted_windows)
    blocked = blocked or quota.get('ordinaryUsageAllowed') is False and classification != 'rate-limited'
    available = quota.get('ordinaryUsageAllowed') is True or quota.get('balanceZero') is False
    available = available or bool(windows and all(type(w.get('usedPercent')) in (int, float) and 0 <= w['usedPercent'] < 100
        and not w.get('invalidReset') and ('resetsAt' not in w or _time(w['resetsAt']) is not None and _time(w['resetsAt']) > present) for w in windows))
    reset = _time(quota.get('resetsAt'))
    if reset is None:
        relevant = exhausted_windows or windows
        resets = [_time(w.get('resetsAt')) for w in relevant]
        reset = max(resets) if resets and all(resets) else None
    return {'provider': provider, 'limitId': identifier(scope.get('limitId')),
            'observedAt': quota['observedAt'], 'blocked': blocked, 'available': available and not blocked,
            'resetsAt': reset.isoformat().replace('+00:00', 'Z') if reset and reset > observed else None,
            'source': identifier(quota.get('source')) or 'unknown',
            'code': 'HARNESS_BALANCE_ZERO' if quota.get('balanceZero') is True else 'HARNESS_QUOTA_EXHAUSTED'}


def record(connection, adapter, quota):
    from .native_observations import _time
    item = evidence(quota)
    if item is None:
        return
    if item['limitId'] in ('codex' if adapter == 'codex' else item['provider'], item['provider']):
        item['limitId'] = None
    key = 'quota-routing:' + canonical_json([adapter, item['provider'], item['limitId']])
    row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    prior = json.loads(row[0]) if row else None
    if prior and _time(prior.get('lastObservedAt') or prior['observedAt']) >= _time(item['observedAt']):
        return
    if not item['blocked'] and not item['available']:
        if not prior:
            return
        item = {**prior, 'lastObservedAt': item['observedAt']}
    else:
        item = {**item, 'adapter': adapter, 'lastObservedAt': item['observedAt']}
    connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, canonical_json(item)))


def exhausted(connection, configuration, *, now=None):
    from .native_observations import _time
    configuration = dict(configuration) if configuration is not None else {}
    adapter, provider, model = (configuration.get(key) for key in ('adapter', 'provider', 'model'))
    if not adapter or not provider:
        return None
    allowed_limits = {None, model, provider, 'codex' if adapter == 'codex' else None}
    present = _time(now or utc_now())
    seen = {}
    for row in connection.execute("SELECT value FROM meta WHERE key LIKE 'quota-routing:%'"):
        item = json.loads(row[0])
        if (item.get('adapter') != adapter or item.get('provider') != provider or item.get('limitId') not in allowed_limits):
            continue
        seen[item.get('limitId')] = _time(item.get('lastObservedAt') or item.get('observedAt'))
        reset = _time(item.get('resetsAt'))
        if item.get('blocked') is True and present and (reset is None or reset > present):
            return {key: item.get(key) for key in ('code', 'source', 'observedAt', 'resetsAt')}
    # Historical latest records remain readable; reads never manufacture state.
    row = connection.execute('SELECT quota_json FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    quota = json.loads(row[0]) if row and row[0] else None
    item = evidence(quota, now=now)
    if item and item['limitId'] in ('codex' if adapter == 'codex' else provider, provider):
        item['limitId'] = None
    if item and item['provider'] == provider and item['limitId'] in allowed_limits and item['blocked']:
        processed = seen.get(item['limitId'])
        if processed and processed >= _time(item['observedAt']):
            return None
        reset = _time(item.get('resetsAt'))
        if present and (reset is None or reset > present):
            return {key: item.get(key) for key in ('code', 'source', 'observedAt', 'resetsAt')}
    return None
