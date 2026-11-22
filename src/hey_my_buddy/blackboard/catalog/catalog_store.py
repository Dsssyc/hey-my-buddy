"""Current native readings and normalized model-service facts."""
from __future__ import annotations

import json
from itertools import islice

from ..evaluation import user_policy
from . import buddy_state
from .catalog import CatalogView
from ...json_codec import canonical_json
from ...errors import BoardError
from ...protocol import schemas
from ...protocol.reasons import reason, reason_from_json


REMOVAL_REASONS = {
    0: 'buddy-removal.in-router-list',
    1: 'buddy-removal.is-maintenance-buddy',
    2: 'buddy-removal.is-profile-buddy',
    3: 'buddy-removal.has-maintenance-runs',
    4: 'buddy-removal.has-profile-runs',
    5: 'buddy-removal.active-execution',
}


def removal_references(db):
    """Read removal blockers in priority order for all buddies and model services."""
    references = {}
    for priority, buddy_id, harness, provider, model in db.execute(
        'SELECT 0 AS priority, buddy_id, NULL, NULL, NULL FROM router_buddies'
        ' UNION ALL SELECT 1, maintenance_buddy_id, NULL, NULL, NULL FROM router_settings'
        ' WHERE maintenance_buddy_id IS NOT NULL'
        ' UNION ALL SELECT 2, profile_buddy_id, NULL, NULL, NULL FROM router_settings'
        ' WHERE profile_buddy_id IS NOT NULL'
        ' UNION ALL SELECT DISTINCT 3, buddy_id, NULL, NULL, NULL FROM maintenance_runs'
        ' UNION ALL SELECT DISTINCT 4, buddy_id, NULL, NULL, NULL FROM profile_runs'
        ' UNION ALL SELECT DISTINCT 5, NULL, model_harness, model_provider, model_model FROM attempts'
        " WHERE model_model IS NOT NULL AND (execution_state != 'finished' OR shutdown_confirmed != 1)"
        ' ORDER BY priority'
    ):
        key = buddy_id if buddy_id is not None else (harness, provider, model)
        references.setdefault(key, priority)
    return references


def is_removal_candidate(row):
    return row['retired'] == 1 or row['confirmed_absent_at'] is not None


def removal_blocker(row, references):
    service = (row['harness'], row['provider'], row['model'])
    priority = references.get(row['buddy_id'], references.get(service))
    return reason(REMOVAL_REASONS[priority]) if priority is not None else None


def begin(evaluation, *, harnesses=None):
    """Reserve an ordered reading without retaining its request or payload."""
    from ...protocol.harness_names import HARNESS_NAMES
    from ..service.harness_health import read_health
    with evaluation.db.write() as db:
        records = harnesses if harnesses is not None else [read_health(db, name) for name in HARNESS_NAMES]
        sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM catalog_current').fetchone()[0]
        for record in records:
            name = record['harness']
            db.execute('INSERT INTO catalog_current(harness,sequence,status,reason,updated_at) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(harness) DO UPDATE SET sequence=excluded.sequence',
                       (name, sequence, 'unread', None, evaluation._now()))
        return {'sequence': sequence, 'harnesses': records}


def current(db):
    """Reconstruct the catalog view solely from current normalized facts."""
    providers, discoveries = {}, []
    for reading in db.execute("SELECT * FROM catalog_current WHERE status!='unread' ORDER BY harness"):
        discoveries.append({'harness': reading['harness'], 'status': reading['status'],
                            'accountStatus': reading['account_status'],
                            'reason': json.loads(reading['reason']) if reading['reason'] else None,
                            'content': reading['content']})
    if not discoveries:
        return None
    states = {}
    for buddy in buddy_state.rows(db):
        if not buddy['retired']:
            states.setdefault(tuple(buddy[key] for key in ('harness', 'provider', 'model')), []).append(buddy)
    for model in db.execute('SELECT * FROM model_services WHERE pending_since IS NULL AND confirmed_absent_at IS NULL ORDER BY harness,provider,model'):
        key = (model['harness'], model['provider'])
        efforts = sorted(states.get(key + (model['model'],), []), key=lambda buddy: (buddy['effort_rank'], buddy['buddy_id']))
        if not efforts:
            continue
        state = efforts[0]
        provider = providers.setdefault(key, {'harness': key[0], 'provider': key[1], 'displayName': key[1], 'models': []})
        provider['models'].append({
            'id': model['model'], 'name': model['model'], 'description': state['description'],
            'contextWindow': state['context_window'], 'inputModalities': json.loads(model['input_modalities_json']),
            'efforts': [item['effort'] for item in efforts],
            'available': bool(state['available']),
            'unavailableReason': reason_from_json(state['unavailable_code'], state['unavailable_params_json']),
            'unavailableContent': state['unavailable_content'],
        })
    return CatalogView({'source': 'current-native-observations', 'harnessVersion': None,
                        'discoveredAt': None, 'providers': list(providers.values()),
                        'discoveries': discoveries, 'warnings': []})


def _content(db):
    # Reading provenance is evidence, not a change to selection facts.
    buddies = {row['buddy_id']: {key: value for key, value in row.items() if key not in ('source', 'catalog_state', 'catalog_reason')}
               for row in buddy_state.rows(db)}
    models = [tuple(row) for row in db.execute(
        'SELECT harness,provider,model,description,context_window,input_modalities_json,unavailable_code,'
        'unavailable_params_json,unavailable_content,pending_since,confirmed_absent_at '
        'FROM model_services ORDER BY harness,provider,model')]
    return buddies, models


def _response(db, *, applied, stale):
    view = current(db)
    if view is None:
        from ..service.harness_health import read_health
        from ...protocol.harness_names import HARNESS_NAMES
        view = CatalogView({'source': 'harness-health', 'harnessVersion': None, 'discoveredAt': None,
                            'providers': [], 'discoveries': [
                                {'harness': name, 'status': 'unknown', 'accountStatus': 'unknown',
                                 'reason': read_health(db, name)['reason']} for name in HARNESS_NAMES], 'warnings': []})
    payload = view.payload
    return {'catalog': payload, 'buddies': view.proposed_buddies(),
            'appliedHarnesses': applied, 'staleHarnesses': stale}


def response(evaluation):
    """Read the current refresh reply after native health refreshes finish."""
    with evaluation.db.read() as db:
        return _response(db, applied=[], stale=[])


def record(evaluation, discovered, sequence=None, *, health_generation=None):
    if sequence is None:
        sequence = begin(evaluation)['sequence']
    from .catalog import CONFIRMATION_SECONDS, TRUSTED_ACCOUNT_STATUSES, _parse_time
    from ..evaluation.models import ensure_model_service
    from ..service.harness_health import read_health
    with evaluation.db.write() as db:
        if (evaluation.board.directory / 'upgrade.json').exists():
            raise BoardError('UPGRADE_IN_PROGRESS', 'Catalog publication is fenced until upgrade verification finishes')
        if health_generation is not None:
            name, generation = health_generation
            health = db.execute('SELECT revision,status FROM harness_health WHERE harness=?', (name,)).fetchone()
            if health is None or health['revision'] != generation or health['status'] != 'ready':
                return {'staleHarnesses': [name], 'appliedHarnesses': []}
        before = _content(db)
        now = evaluation._now()
        applied, stale, transitions = [], [], []
        for result in discovered['discoveries']:
            name = result['harness']
            prior = db.execute('SELECT * FROM catalog_current WHERE harness=?', (name,)).fetchone()
            if prior is None or prior['sequence'] != sequence:
                stale.append(name)
                continue
            trusted = result['status'] == 'complete' and result.get('accountStatus') in TRUSTED_ACCOUNT_STATUSES
            stored_reason = None if trusted else (
                reason('catalog.account-status-unknown', harness=name) if result['status'] == 'complete'
                else result.get('reason') or reason('catalog.native-unknown', harness=name))
            db.execute('UPDATE catalog_current SET status=?,reason=?,content=?,updated_at=? WHERE harness=?',
                       ('complete' if trusted else 'unknown', canonical_json(stored_reason) if stored_reason else None,
                        result.get('content'), now, name))
            applied.append(name)
            if not trusted:
                continue
            db.execute('UPDATE catalog_current SET read_at=?,account_status=? WHERE harness=?',
                       (now, result['accountStatus'], name))
            if name == 'zcode':
                from ...protocol.billing import zcode_access
                health_row = db.execute("SELECT record_json FROM harness_health WHERE harness='zcode'").fetchone()
                if health_row:
                    health_record = json.loads(health_row['record_json'])
                    health_record['billingByProvider'] = {
                        p['provider']: zcode_access(p.get('accessType'), now)
                        for p in discovered['providers'] if p['harness'] == name}
                    db.execute("UPDATE harness_health SET record_json=? WHERE harness='zcode'", (canonical_json(health_record),))
            native = [p for p in discovered['providers'] if p['harness'] == name]
            listed = {(p['provider'], m['id']): m for p in native for m in p['models']}
            existing = db.execute('SELECT * FROM model_services WHERE harness=?', (name,)).fetchall()
            for old in existing:
                key = (old['provider'], old['model'])
                if key in listed or old['confirmed_absent_at'] is not None:
                    continue
                if old['pending_since'] is None:
                    # Without an adopted available reading there is no legal
                    # model state to retain during a confirmation window.
                    if old['source_sequence'] is None or old['unavailable_code'] is not None:
                        db.execute('UPDATE model_services SET confirmed_absent_at=? WHERE harness=? AND provider=? AND model=?', (now, name, *key))
                    else:
                        db.execute('UPDATE model_services SET pending_since=? WHERE harness=? AND provider=? AND model=?', (now, name, *key))
                        transitions.append({'kind': 'catalog.model_pending', 'harness': name,
                                            'provider': key[0], 'model': key[1], 'pendingSince': now})
                else:
                    first, moment = _parse_time(old['pending_since']), _parse_time(now)
                    if first is not None and moment is not None and (moment - first).total_seconds() >= CONFIRMATION_SECONDS:
                        db.execute('UPDATE model_services SET pending_since=NULL,confirmed_absent_at=? WHERE harness=? AND provider=? AND model=?', (now, name, *key))
                        transitions.append({'kind': 'catalog.model_unavailable', 'harness': name,
                                            'provider': key[0], 'model': key[1], 'pendingSince': old['pending_since'], 'confirmedAt': now})
            old_by_key = {(row['provider'], row['model']): row for row in existing}
            view = CatalogView({**discovered, 'providers': native})
            entries = view.proposed_buddies()
            for (provider, model), metadata in listed.items():
                unavailable = metadata['unavailableReason']
                facts = {
                    'description': metadata['description'], 'context_window': metadata['contextWindow'],
                    'input_modalities_json': canonical_json(metadata['inputModalities']),
                    'unavailable_code': unavailable['code'] if unavailable else None,
                    'unavailable_params_json': canonical_json(unavailable['params']) if unavailable else None,
                    'unavailable_content': metadata['unavailableContent'],
                    'pending_since': None, 'confirmed_absent_at': None,
                }
                old = old_by_key.get((provider, model))
                efforts = [row['effort'] for row in db.execute(
                    'SELECT effort FROM buddies WHERE harness=? AND provider=? AND model=? AND retired=0 ORDER BY effort_rank,buddy_id',
                    (name, provider, model))]
                if (old is None or old['source_sequence'] is None
                        or any(old[field] != value for field, value in facts.items())
                        or efforts != metadata['efforts']):
                    db.execute('INSERT INTO model_services(harness,provider,model,description,context_window,input_modalities_json,'
                               'unavailable_code,unavailable_params_json,unavailable_content,pending_since,confirmed_absent_at,source_sequence) '
                               'VALUES(?,?,?,?,?,?,?,?,?,?,?,?) '
                               'ON CONFLICT(harness,provider,model) DO UPDATE SET description=excluded.description,context_window=excluded.context_window,'
                               'input_modalities_json=excluded.input_modalities_json,unavailable_code=excluded.unavailable_code,'
                               'unavailable_params_json=excluded.unavailable_params_json,unavailable_content=excluded.unavailable_content,'
                               'pending_since=NULL,confirmed_absent_at=NULL,source_sequence=excluded.source_sequence',
                               (name, provider, model, *facts.values(), sequence))
                marks = ','.join('?' for _ in metadata['efforts'])
                db.execute('UPDATE buddies SET retired=1 WHERE harness=? AND provider=? AND model=? '
                           f'AND retired=0 AND effort NOT IN ({marks})', (name, provider, model, *metadata['efforts']))
                if old is not None and (old['pending_since'] is not None or old['confirmed_absent_at'] is not None):
                    if metadata['available'] and read_health(db, name)['status'] == 'ready':
                        transition = {'kind': 'catalog.model_recovered', 'harness': name, 'provider': provider,
                                      'model': model, 'recoveredAt': now}
                        if old['pending_since'] is not None:
                            transition['pendingSince'] = old['pending_since']
                        transitions.append(transition)
            ranks = view.effort_ranks()
            for entry in entries:
                identity = tuple(entry[key] for key in ('harness', 'provider', 'model', 'effort'))
                old = db.execute('SELECT harness,provider,model,effort FROM buddies WHERE buddy_id=?', (entry['buddyId'],)).fetchone()
                if old is not None and tuple(old) != identity:
                    raise BoardError('CATALOG_INVALID', 'A native buddy ID collided with a different execution identity', buddyId=entry['buddyId'])
                db.execute('INSERT INTO buddies(buddy_id,harness,provider,model,effort,effort_rank,retired,enabled) VALUES(?,?,?,?,?,?,0,0) '
                           'ON CONFLICT(buddy_id) DO UPDATE SET effort_rank=excluded.effort_rank,retired=0 '
                           'WHERE buddies.effort_rank!=excluded.effort_rank OR buddies.retired!=0',
                           (entry['buddyId'], *identity, ranks[identity[:3]][entry['effort']]))
                ensure_model_service(db, *identity[:3], now)
            # Retired efforts follow all efforts in the current native order.
            for provider, model in listed:
                offset = len(listed[(provider, model)]['efforts'])
                retired = db.execute('SELECT buddy_id FROM buddies WHERE harness=? AND provider=? AND model=? AND retired=1 ORDER BY effort_rank,buddy_id', (name, provider, model)).fetchall()
                for rank, old in enumerate(retired, offset):
                    db.execute('UPDATE buddies SET effort_rank=? WHERE buddy_id=? AND effort_rank!=?', (rank, old['buddy_id'], rank))
        changed = before != _content(db)
        for transition in transitions:
            kind = transition.pop('kind')
            evaluation.board._append_event(db, kind, payload=transition)
        response = _response(db, applied=applied, stale=stale)
        if changed:
            evaluation.board._append_event(db, 'evaluation.catalog_discovered', payload={
                'appliedHarnesses': applied, 'staleHarnesses': stale})
        head = evaluation.board._head_of(db)
    if changed:
        evaluation.board._notify(head)
    return response


def buddies(evaluation, params):
    schemas.reject_unknown(params, {'limit', 'after', 'includeUnavailable', 'query', 'harness', 'enabled'}, 'buddy.list')
    limit = params.get('limit', 100)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise BoardError('INVALID_ARGUMENT', 'limit must be between 1 and 200')
    after = params.get('after', '')
    if not isinstance(after, str) or len(after) > 128:
        raise BoardError('INVALID_ARGUMENT', 'after must be a buddy cursor')
    include = schemas.optional_bool(params, 'includeUnavailable', False)
    query = schemas.optional_string(params, 'query', max_length=200) or ''
    harness = schemas.optional_string(params, 'harness', max_length=32)
    enabled = schemas.optional_bool(params, 'enabled', False)
    terms = [term.casefold() for term in query.split()]
    if len(terms) > 8:
        raise BoardError('INVALID_ARGUMENT', 'query accepts at most 8 search terms')
    with evaluation.db.read() as db:
        def matches(row):
            if row['buddy_id'] <= after or (not include and not row['available']):
                return False
            if enabled and not row['enabled']:
                return False
            fields = tuple(str(row[field] or '').casefold() for field in
                           ('alias', 'model', 'provider', 'harness', 'effort'))
            complete_name = f"{row['harness']} / {row['provider']} / {row['model']} / {row['effort']}".casefold()
            return all(any(term in field for field in (*fields, complete_name)) for term in terms)
        candidates = sorted(buddy_state.rows(db, harness=harness), key=lambda row: row['buddy_id'])
        rows = list(islice((row for row in candidates if matches(row)), limit + 1))
        references = removal_references(db)
        values = [evaluation._buddy_view(row, references) for row in rows[:limit]]
        from ..service.harness_health import for_provider
        from ..routing.quota_routing import cooling
        for value in values:
            value['billing'] = for_provider(db, value['harness'], value['provider'])
            value['providerCooling'] = cooling(db, value)
        ids = [value['buddyId'] for value in values]
        model_services = {(value['harness'], value['provider'], value['model']) for value in values}
        return {'buddies': values, **user_policy.policy_view(db, ids),
                'modelServiceConcurrency': evaluation.board.model_capacity_rows(db, model_services),
                'nextCursor': rows[limit - 1]['buddy_id'] if len(rows) > limit else None}


def remove_buddies(evaluation, params):
    """Remove retired or confirmed absent entries while retaining evaluation history."""
    user_policy.require_console()
    operation = 'buddy.remove'
    schemas.reject_unknown(params, {'commandId', 'buddyIds'}, operation)
    command_id = schemas.required_string(params, 'commandId', max_length=128)
    buddy_ids = params.get('buddyIds')
    if not isinstance(buddy_ids, list) or not 1 <= len(buddy_ids) <= 200:
        raise BoardError('INVALID_ARGUMENT', 'buddyIds must contain between 1 and 200 buddy IDs')
    if any(not isinstance(buddy_id, str) or not schemas.IDENTIFIER_PATTERN.fullmatch(buddy_id)
           for buddy_id in buddy_ids):
        raise BoardError('INVALID_ARGUMENT', 'buddyIds contains an invalid buddy ID')
    if len(set(buddy_ids)) != len(buddy_ids):
        raise BoardError('INVALID_ARGUMENT', 'buddyIds must not contain duplicates')
    request = {'buddyIds': buddy_ids}
    board = evaluation.board
    with evaluation.db.write() as db:
        receipt = board._receipt(db, command_id, operation, request)
        if receipt is not None:
            return {**receipt, 'duplicate': True}
        if board.upgrading():
            raise BoardError('UPGRADE_IN_PROGRESS', 'Buddy removal is fenced until upgrade verification finishes')

        marks = ','.join('?' for _ in buddy_ids)
        rows = {row['buddy_id']: row for row in db.execute(
            'SELECT b.buddy_id, b.harness, b.provider, b.model, b.retired, m.confirmed_absent_at'
            ' FROM buddies b JOIN model_services m USING (harness, provider, model)'
            f' WHERE b.buddy_id IN ({marks})', buddy_ids)}
        references = removal_references(db)
        affected_services = {}
        for buddy_id in buddy_ids:
            row = rows.get(buddy_id)
            if row is None:
                raise BoardError('NOT_FOUND', 'Unknown buddy ID', buddyId=buddy_id)
            if not is_removal_candidate(row):
                raise BoardError('BUDDY_NOT_REMOVABLE', 'Buddy is neither retired nor confirmed absent',
                                 buddyId=buddy_id, reason=reason('buddy-removal.not-retired'))
            blocker = removal_blocker(row, references)
            if blocker is not None:
                raise BoardError('BUDDY_IN_USE', 'Buddy still has references that prevent removal',
                                 buddyId=buddy_id, reason=blocker)
            affected_services[(row['harness'], row['provider'], row['model'])] = None

        db.execute(f'DELETE FROM evaluation_preferences WHERE buddy_id IN ({marks})', buddy_ids)
        db.execute(f'DELETE FROM buddies WHERE buddy_id IN ({marks})', buddy_ids)
        removed_model_services = []
        for service in affected_services:
            deleted = db.execute(
                'DELETE FROM model_services WHERE harness=? AND provider=? AND model=?'
                ' AND confirmed_absent_at IS NOT NULL'
                ' AND NOT EXISTS (SELECT 1 FROM buddies WHERE harness=? AND provider=? AND model=?)',
                (*service, *service))
            if deleted.rowcount:
                harness, provider, model = service
                removed_model_services.append({'harness': harness, 'provider': provider, 'model': model})

        board._append_event(db, 'catalog.buddies_removed',
                            payload={'buddyIds': buddy_ids, 'modelServices': removed_model_services})
        response = {'removedBuddyIds': buddy_ids, 'removedModelServices': removed_model_services, 'duplicate': False}
        board._store_receipt(db, command_id, operation, request, response)
        head = board._head_of(db)
    board._notify(head)
    return response
