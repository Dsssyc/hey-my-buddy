"""Native exhaustion persists until its reset or a newer available observation.

Display freshness is separate. Unknown/partial observations cannot clear an
exhaustion, and billing labels never affect these routing facts. Each persistent
record and retry window belongs to one harness, provider and native limit.

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
from ..store.db import utc_now
from ...protocol.usage import classify_quota_code, identifier

#: How long a no-reset exhaustion (or a consumed retry that produced no newer
#: usable observation) waits before one routing decision may retry it again.
RETRY_WAIT_SECONDS = 3600


def evidence(quota, *, now=None):
    from ..evaluation.native_observations import _time
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


def _item(row):
    return {'adapter': row['adapter'], 'provider': row['provider'], 'limitId': row['limit_id'] or None,
            'blocked': bool(row['blocked']), 'available': bool(row['available']),
            'code': row['code'], 'source': row['source'], 'observedAt': row['observed_at'],
            'lastObservedAt': row['last_observed_at'], 'resetsAt': row['resets_at'],
            'nativeAccountId': row['native_account_id']}


def _marker(connection, adapter, provider, limit_id):
    row = connection.execute('SELECT retry_basis,retry_manual_at,retry_consumed_at,retry_consumed_by '
                             'FROM quota_current WHERE adapter=? AND provider=? AND limit_id=?',
                             (adapter, provider, limit_id or '')).fetchone()
    if row is None or row['retry_basis'] is None:
        return None
    return {'basis': row['retry_basis'], 'manualAt': row['retry_manual_at'],
            'consumedAt': row['retry_consumed_at'], 'consumedBy': row['retry_consumed_by']}


def _write_marker(connection, adapter, provider, limit_id, marker):
    connection.execute('UPDATE quota_current SET retry_basis=?,retry_manual_at=?,retry_consumed_at=?,'
                       'retry_consumed_by=? WHERE adapter=? AND provider=? AND limit_id=?',
                       (marker['basis'], marker['manualAt'], marker['consumedAt'], marker['consumedBy'],
                        adapter, provider, limit_id or ''))


def retry_facts(connection, item, *, now=None):
    """The single-use retry window of one blocked no-reset exhaustion record.

    Returns ``None`` when the record is not retry-eligible: exhaustion with a
    known reset keeps its existing recovery rule, and only persisted records can
    carry a window. The projection is a read; it never claims anything.
    """
    from ..evaluation.native_observations import _time
    if not isinstance(item, dict) or item.get('blocked') is not True or _time(item.get('resetsAt')) is not None:
        return None
    present, observed = _time(now or utc_now()), _time(item.get('observedAt'))
    if present is None or observed is None:
        return None
    adapter, provider, limit_id = item.get('adapter'), item.get('provider'), item.get('limitId')
    marker = _marker(connection, adapter, provider, limit_id)
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
    return {'eligibleAt': _iso(eligible), 'open': present.timestamp() >= eligible,
            'pendingManual': pending_manual and present.timestamp() >= manual.timestamp(),
            'manualAt': _iso(manual.timestamp()) if manual else None,
            'consumedAt': _iso(consumed.timestamp()) if consumed else None, 'consumedBy': consumed_by}


def _iso(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def record(connection, adapter, quota, *, now=None):
    from ..evaluation.native_observations import _time
    if quota.get('ambiguousLimits') is True and quota.get('allLimitsAvailable') is True:
        for limit in [None, *quota.get('coveredLimits', [])]:
            record(connection, adapter, {**quota, 'ambiguousLimits': False, 'windows': [],
                'scope': {**quota.get('scope', {}), 'limitId': limit}, 'ordinaryUsageAllowed': True,
                'balanceZero': False, 'reachedType': None}, now=now)
        return
    item = evidence(quota, now=now)
    if item is None:
        return
    if item['limitId'] in ('codex' if adapter == 'codex' else item['provider'], item['provider']):
        item['limitId'] = None
    binding = (adapter, item['provider'], item['limitId'] or '')
    row = connection.execute('SELECT * FROM quota_current WHERE adapter=? AND provider=? AND limit_id=?',
                             binding).fetchone()
    if row and _time(row['last_observed_at']) >= _time(item['observedAt']):
        return
    native_id = (quota.get('scope') or {}).get('nativeAccountId')
    if not item['blocked'] and not item['available']:
        if row:
            connection.execute('UPDATE quota_current SET last_observed_at=?,native_account_id=? '
                               'WHERE adapter=? AND provider=? AND limit_id=?',
                               (item['observedAt'], native_id, *binding))
        return
    connection.execute('INSERT INTO quota_current(adapter,provider,limit_id,blocked,available,code,source,'
                       'observed_at,last_observed_at,resets_at,native_account_id) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(adapter,provider,limit_id) DO UPDATE SET blocked=excluded.blocked,'
                       'available=excluded.available,code=excluded.code,source=excluded.source,'
                       'observed_at=excluded.observed_at,last_observed_at=excluded.last_observed_at,'
                       'resets_at=excluded.resets_at,native_account_id=excluded.native_account_id,'
                       'retry_basis=NULL,retry_manual_at=NULL,retry_consumed_at=NULL,retry_consumed_by=NULL',
                       (*binding, int(item['blocked']), int(item['available']), item['code'], item['source'],
                        item['observedAt'], item['observedAt'], item['resetsAt'], native_id))


def blocking_records(connection, configuration, *, now=None):
    """Current native exhaustions blocking this harness/provider/model."""
    from ..evaluation.native_observations import _time
    if not configuration or not configuration.get('adapter') or not configuration.get('provider'):
        return []
    adapter, provider, model = (configuration.get(key) for key in ('adapter', 'provider', 'model'))
    allowed_limits = {None, model, provider, 'codex' if adapter == 'codex' else None}
    present = _time(now or utc_now())
    records = []
    for row in connection.execute('SELECT * FROM quota_current WHERE adapter=? AND provider=? '
                                  'AND blocked=1 ORDER BY observed_at DESC,limit_id', (adapter, provider)):
        item = _item(row)
        reset = _time(item['resetsAt'])
        if item['limitId'] in allowed_limits and present and (reset is None or reset > present):
            records.append({'item': item})
    records.sort(key=lambda record: (_time(record['item']['observedAt']).timestamp(),
                                     record['item']['limitId'] or ''), reverse=True)
    return records


def _compact(item):
    return {key: item.get(key) for key in ('code', 'source', 'observedAt', 'resetsAt')}


def exhausted(connection, configuration, *, now=None):
    """Whether one configuration is currently excluded from normal routing.

    An open retry window does not change this answer: reads, reminders and the
    explicit-choice warning keep reporting the recorded exhaustion until a newer
    usable observation clears it. Only :func:`claim` admits a decision.
    """
    records = blocking_records(connection, configuration, now=now)
    return _compact(records[0]['item']) if records else None


def claim(connection, configuration, *, decision_id, consume=True, now=None):
    """Admit one configuration's blocked no-reset exhaustions into a decision.

    Every blocking record must carry an open single-use window — or, for a
    publish-time bounds re-check, the window this very ``decision_id`` already
    consumed. With ``consume`` the open windows are claimed durably inside the
    caller's transaction, exactly once: a concurrent decision re-reads the marker
    and stays excluded. Returns the claimed/allowed facts (one
    per blocking record) or ``None`` when the configuration stays excluded.
    """
    records = blocking_records(connection, configuration, now=now)
    if not records:
        return []
    admitted = []
    for record in records:
        facts = retry_facts(connection, record['item'], now=now)
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
                  'basis': item.get('observedAt'), 'manualAt': None,
                  'consumedAt': now or utc_now(), 'consumedBy': decision_id}
        _write_marker(connection, item['adapter'], item['provider'], item.get('limitId'), marker)
        claimed.append({**facts, 'claimed': True, 'consumedAt': marker['consumedAt'], 'consumedBy': decision_id})
    return claimed


def redetect(connection, adapter, provider, *, now=None):
    """Open each recorded no-reset exhaustion's single retry window."""
    records, opened, already = [], [], []
    for row in connection.execute('SELECT * FROM quota_current WHERE adapter=? AND provider=? '
                                  'ORDER BY limit_id', (adapter, provider)):
        item = _item(row)
        view = {key: item.get(key) for key in ('provider', 'limitId', 'code', 'source', 'observedAt', 'resetsAt')}
        facts = retry_facts(connection, item, now=now)
        if facts is None:
            view['retry'] = None
        elif facts['open']:
            already.append(item['limitId'])
            view['retry'] = facts
        else:
            marker = {'basis': item['observedAt'], 'manualAt': now or utc_now(),
                      'consumedAt': None, 'consumedBy': None}
            _write_marker(connection, adapter, provider, item['limitId'], marker)
            opened.append(item['limitId'])
            view['retry'] = retry_facts(connection, item, now=now)
        records.append(view)
    return {'adapter': adapter, 'provider': provider, 'records': records,
            'opened': opened, 'alreadyOpen': already}


def routing_facts(connection, adapter, *, now=None):
    views = []
    for row in connection.execute('SELECT * FROM quota_current WHERE adapter=? ORDER BY provider,limit_id', (adapter,)):
        item = _item(row)
        views.append({**{key: item[key] for key in ('provider', 'limitId', 'code', 'source', 'observedAt',
                                                   'lastObservedAt', 'resetsAt', 'blocked', 'available', 'nativeAccountId')},
                      'retry': retry_facts(connection, item, now=now)})
    return views


def configuration_retry(connection, configuration, *, now=None):
    """The retry window a routed profile view explains to its user, or ``None``.

    ``None`` means the configuration is not blocked, its recovery is already
    scheduled by a known reset. Otherwise the binding record is
    the blocking one whose window opens last.
    """
    records = blocking_records(connection, configuration, now=now)
    windows = [retry_facts(connection, record['item'], now=now) for record in records]
    if not records or not windows or any(facts is None for facts in windows):
        return None
    from ..evaluation.native_observations import _time
    return max(windows, key=lambda facts: _time(facts['eligibleAt']))
