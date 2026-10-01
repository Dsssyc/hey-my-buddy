"""Native exhaustion persists until its reset or a newer available observation.

Display freshness is separate. Unknown/partial observations cannot clear an
exhaustion, and billing labels never affect these routing facts. Each persistent
record and retry marker belongs to one source and credential revision; historical
untagged records belong only to native revision zero.

An exhaustion without a recorded reset would otherwise exclude its configuration
forever, because the excluded configuration is never routed again and so never
produces a newer observation. Such a record carries exactly one single-use retry
window: one hour after the blocking observation (or after the last consumed
retry) one routing decision may admit the configuration again, and an explicit
re-detect opens that window early. The window never claims the balance recovered
and never calls a model or a balance query; only a newer usable observation
clears the exhaustion itself.
"""
from __future__ import annotations
import json
from .db import canonical_json, utc_now
from .usage import classify_quota_code, identifier

#: How long a no-reset exhaustion (or a consumed retry that produced no newer
#: usable observation) waits before one routing decision may retry it again.
RETRY_WAIT_SECONDS = 3600


def evidence(quota, *, now=None):
    from .native_observations import _time
    if not isinstance(quota, dict):
        return None
    if quota.get('ambiguousLimits') is True:
        if quota.get('allLimitsAvailable') is not True:
            return None
        quota = {**quota, 'scope': {}, 'windows': [], 'ordinaryUsageAllowed': True, 'reachedType': None, 'balanceZero': False}
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
    blocked = classification == 'quota-exceeded' or quota.get('balanceZero') is True
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


def _account(connection, adapter, account=None):
    from .accounts import identity, selection
    return identity(account if account is not None else selection(connection, adapter))


def _stored_account(item):
    # Existing untagged evidence belongs only to the original native credentials.
    return item.get('account') or {'source': 'native', 'credentialRevision': 0}


def _key(prefix, adapter, provider, limit_id, account):
    parts = [adapter, provider, limit_id]
    # Keep the initial-native record address; this is a read-compatible addition
    # in meta, not a schema migration or conversion of historical observations.
    if account != {'source': 'native', 'credentialRevision': 0}:
        parts.extend([account['source'], account['credentialRevision']])
    return prefix + canonical_json(parts)


def _item_key(adapter, provider, limit_id, account):
    return _key('quota-routing:', adapter, provider, limit_id, account)


def _marker_key(adapter, provider, limit_id, account):
    return _key('quota-retry:', adapter, provider, limit_id, account)


def _marker(connection, adapter, provider, limit_id, account):
    """The persisted single-use retry marker of one account's exhaustion."""
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_marker_key(adapter, provider, limit_id, account),)).fetchone()
    value = json.loads(row[0]) if row else None
    return value if isinstance(value, dict) else None


def _write_marker(connection, adapter, provider, limit_id, marker):
    connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (_marker_key(adapter, provider, limit_id, _stored_account(marker)), canonical_json(marker)))


def retry_facts(connection, item, *, now=None):
    """The single-use retry window of one blocked no-reset exhaustion record.

    Returns ``None`` when the record is not retry-eligible: exhaustion with a
    known reset keeps its existing recovery rule, and only persisted records can
    carry a window. The projection is a read; it never claims anything.
    """
    from .native_observations import _time
    if not isinstance(item, dict) or item.get('blocked') is not True or _time(item.get('resetsAt')) is not None:
        return None
    present, observed = _time(now or utc_now()), _time(item.get('observedAt'))
    if present is None or observed is None:
        return None
    adapter, provider, limit_id = item.get('adapter'), item.get('provider'), item.get('limitId')
    marker = _marker(connection, adapter, provider, limit_id, _stored_account(item))
    # A marker bound to an older observation is evidence of a newer exhaustion
    # event; the fresh observation owns a fresh natural window.
    if marker and marker.get('basis') != item.get('observedAt'):
        marker = None
    manual = consumed = consumed_by = None
    if marker:
        manual, consumed = _time(marker.get('manualAt')), _time(marker.get('consumedAt'))
        consumed_by = identifier(marker.get('consumedBy')) if consumed else None
    eligible = observed.timestamp() + RETRY_WAIT_SECONDS
    if consumed:
        eligible = max(eligible, consumed.timestamp() + RETRY_WAIT_SECONDS)
    pending_manual = bool(manual and (consumed is None or manual > consumed))
    if pending_manual:
        eligible = min(eligible, manual.timestamp())
    return {'account': _stored_account(item), 'eligibleAt': _iso(eligible), 'open': present.timestamp() >= eligible,
            'pendingManual': pending_manual and present.timestamp() >= manual.timestamp(),
            'manualAt': _iso(manual.timestamp()) if manual else None,
            'consumedAt': _iso(consumed.timestamp()) if consumed else None, 'consumedBy': consumed_by}


def _iso(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def record(connection, adapter, quota, *, account=None, now=None):
    from .native_observations import _time
    account = _account(connection, adapter, account)
    if quota.get('ambiguousLimits') is True and quota.get('allLimitsAvailable') is True:
        for limit in [None, *quota.get('coveredLimits', [])]:
            record(connection, adapter, {**quota, 'ambiguousLimits': False, 'windows': [],
                'scope': {'limitId': limit}, 'ordinaryUsageAllowed': True, 'balanceZero': False, 'reachedType': None}, account=account, now=now)
        return
    item = evidence(quota, now=now)
    if item is None:
        return
    if item['limitId'] in ('codex' if adapter == 'codex' else item['provider'], item['provider']):
        item['limitId'] = None
    key = _item_key(adapter, item['provider'], item['limitId'], account)
    row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    prior = json.loads(row[0]) if row else None
    if prior and _time(prior.get('lastObservedAt') or prior['observedAt']) >= _time(item['observedAt']):
        return
    if not item['blocked'] and not item['available']:
        if not prior:
            return
        item = {**prior, 'lastObservedAt': item['observedAt']}
    else:
        # A definitive newer observation replaces the record: exhaustion clears,
        # or a fresh exhaustion event starts a fresh natural retry window. Either
        # way the previous window's marker no longer describes this record.
        connection.execute('DELETE FROM meta WHERE key=?', (_marker_key(adapter, item['provider'], item['limitId'], account),))
        item = {**item, 'adapter': adapter, 'account': account, 'lastObservedAt': item['observedAt']}
    connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, canonical_json(item)))


def blocking_records(connection, configuration, *, account=None, now=None):
    """Every exhaustion record currently blocking one configuration (read-only).

    Persisted ``meta`` records come first (newest blocking observation first);
    the retained display observation contributes one last record only when no
    persisted processing superseded it. Each entry is ``{'item', 'meta'}``.
    """
    from .native_observations import _time
    configuration = dict(configuration) if configuration is not None else {}
    adapter, provider, model = (configuration.get(key) for key in ('adapter', 'provider', 'model'))
    if not adapter or not provider:
        return []
    account = _account(connection, adapter, account)
    allowed_limits = {None, model, provider, 'codex' if adapter == 'codex' else None}
    present = _time(now or utc_now())
    seen, blocking = {}, []
    for row in connection.execute("SELECT value FROM meta WHERE key LIKE 'quota-routing:%'"):
        item = json.loads(row[0])
        if (item.get('adapter') != adapter or item.get('provider') != provider or item.get('limitId') not in allowed_limits
                or _stored_account(item) != account):
            continue
        seen[item.get('limitId')] = _time(item.get('lastObservedAt') or item.get('observedAt'))
        reset = _time(item.get('resetsAt'))
        if item.get('blocked') is True and present and (reset is None or reset > present):
            observed = _time(item.get('observedAt'))
            blocking.append({'item': item, 'meta': True, 'observed': observed.timestamp() if observed else 0})
    blocking.sort(key=lambda record: (record['observed'], record['item'].get('limitId') or ''), reverse=True)
    for record in blocking:
        record.pop('observed', None)
    # Historical latest records remain readable; reads never manufacture state.
    from .native_observations import latest_quota
    quota = latest_quota(connection, adapter, account=account)
    item = evidence(quota, now=now)
    if item and item['limitId'] in ('codex' if adapter == 'codex' else provider, provider):
        item['limitId'] = None
    if item and item['provider'] == provider and item['limitId'] in allowed_limits and item['blocked']:
        processed = seen.get(item['limitId'])
        if not (processed and processed >= _time(item['observedAt'])):
            reset = _time(item.get('resetsAt'))
            if present and (reset is None or reset > present):
                blocking.append({'item': {**item, 'adapter': adapter, 'account': account}, 'meta': False})
    return blocking


def _compact(item):
    return {**{key: item.get(key) for key in ('code', 'source', 'observedAt', 'resetsAt')},
            'account': _stored_account(item)}


def exhausted(connection, configuration, *, account=None, now=None):
    """Whether one configuration is currently excluded from normal routing.

    An open retry window does not change this answer: reads, reminders and the
    explicit-choice warning keep reporting the recorded exhaustion until a newer
    usable observation clears it. Only :func:`claim` admits a decision.
    """
    records = blocking_records(connection, configuration, account=account, now=now)
    return _compact(records[0]['item']) if records else None


def claim(connection, configuration, *, decision_id, consume=True, account=None, now=None):
    """Admit one configuration's blocked no-reset exhaustions into a decision.

    Every blocking record must carry an open single-use window — or, for a
    publish-time bounds re-check, the window this very ``decision_id`` already
    consumed. With ``consume`` the open windows are claimed durably inside the
    caller's transaction, exactly once: a concurrent decision re-reads the marker
    and stays excluded. The retained display observation alone never carries a
    retry window, so it keeps blocking. Returns the claimed/allowed facts (one
    per blocking record) or ``None`` when the configuration stays excluded.
    """
    records = blocking_records(connection, configuration, account=account, now=now)
    if not records:
        return []
    admitted = []
    for record in records:
        facts = retry_facts(connection, record['item'], now=now) if record['meta'] else None
        allowed = facts is not None and (facts['open'] or facts['consumedBy'] == decision_id)
        if not allowed:
            return None
        admitted.append((record, facts))
    claimed = []
    for record, facts in admitted:
        item = record['item']
        if not consume or not facts['open'] or facts['consumedBy'] == decision_id:
            claimed.append({**facts, 'claimed': False})
            continue
        marker = {'adapter': item['adapter'], 'provider': item['provider'], 'limitId': item.get('limitId'),
                  'account': _stored_account(item),
                  'basis': item.get('observedAt'), 'manualAt': None,
                  'consumedAt': now or utc_now(), 'consumedBy': decision_id}
        _write_marker(connection, item['adapter'], item['provider'], item.get('limitId'), marker)
        claimed.append({**facts, 'claimed': True, 'consumedAt': marker['consumedAt'], 'consumedBy': decision_id})
    return claimed


def materialize(connection, adapter, provider, *, account=None, now=None):
    """Import retained pre-meta native exhaustion only inside a write action."""
    from .native_observations import latest_quota
    quota = latest_quota(connection, adapter, account=account)
    item = evidence(quota, now=now)
    if item and item['provider'] == provider and item['blocked']:
        record(connection, adapter, quota, account=account, now=now)


def redetect(connection, adapter, provider, *, account=None, now=None):
    """Open the retry window of one provider's blocked no-reset exhaustions now.

    The explicit re-detect is idempotent and honest: a window that is already
    open stays unchanged, no model or balance query is made, and nothing claims
    the balance recovered — the next normal routing decision simply gets one
    chance to produce a real observation. Records with a known reset keep their
    scheduled recovery and are reported without any state change.
    """
    account = _account(connection, adapter, account)
    materialize(connection, adapter, provider, account=account, now=now)
    records, opened, already = [], [], []
    for row in connection.execute("SELECT value FROM meta WHERE key LIKE 'quota-routing:%'"):
        item = json.loads(row[0])
        if item.get('adapter') != adapter or item.get('provider') != provider or _stored_account(item) != account:
            continue
        view = {key: item.get(key) for key in ('provider', 'limitId', 'code', 'source', 'observedAt', 'resetsAt', 'account')}
        facts = retry_facts(connection, item, now=now)
        if facts is None:
            view['retry'] = None
        elif facts['open']:
            already.append(item.get('limitId'))
            view['retry'] = facts
        else:
            # A fresh manual window supersedes any consumed one: the earlier
            # consumption's wait is intentionally short-circuited, and the next
            # claim will record its own consumption from scratch.
            marker = {'adapter': adapter, 'provider': provider, 'limitId': item.get('limitId'), 'account': account,
                      'basis': item.get('observedAt'), 'manualAt': now or utc_now(),
                      'consumedAt': None, 'consumedBy': None}
            _write_marker(connection, adapter, provider, item.get('limitId'), marker)
            opened.append(item.get('limitId'))
            view['retry'] = retry_facts(connection, item, now=now)
        records.append(view)
    return {'adapter': adapter, 'provider': provider, 'account': account, 'records': records,
            'opened': opened, 'alreadyOpen': already}


def routing_facts(connection, adapter, *, account=None, now=None):
    """Every persisted exhaustion record of one adapter with its retry window."""
    account = _account(connection, adapter, account)
    views = []
    for row in connection.execute("SELECT value FROM meta WHERE key LIKE 'quota-routing:%'"):
        item = json.loads(row[0])
        if item.get('adapter') != adapter or _stored_account(item) != account:
            continue
        views.append({'provider': item.get('provider'), 'limitId': item.get('limitId'), 'account': account,
                      'code': item.get('code'), 'source': item.get('source'),
                      'observedAt': item.get('observedAt'), 'lastObservedAt': item.get('lastObservedAt'),
                      'resetsAt': item.get('resetsAt'), 'blocked': item.get('blocked') is True,
                      'available': item.get('available') is True,
                      'retry': retry_facts(connection, item, now=now)})
    return views


def configuration_retry(connection, configuration, *, account=None, now=None):
    """The retry window a routed profile view explains to its user, or ``None``.

    ``None`` means the configuration is not blocked, its recovery is already
    scheduled by a known reset, or only a retained display observation blocks it
    (no persisted record could carry a window). Otherwise the binding record is
    the blocking one whose window opens last.
    """
    records = blocking_records(connection, configuration, account=account, now=now)
    windows = [retry_facts(connection, record['item'], now=now) if record['meta'] else None for record in records]
    if not records or not windows or any(facts is None for facts in windows):
        return None
    from .native_observations import _time
    return max(windows, key=lambda facts: _time(facts['eligibleAt']))
