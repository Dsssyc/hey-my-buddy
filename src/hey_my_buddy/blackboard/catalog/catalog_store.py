"""Ordered native observations and retained buddy identities."""
from __future__ import annotations

import json
import uuid

from ..evaluation import user_policy
from .catalog import CatalogView, canonical_payload
from ..store.db import canonical_json, sha256_text
from ...errors import BoardError
from ...protocol.reasons import reason


def begin(evaluation, request_id=None, *, harnesses=None):
    request_id = request_id or 'catalog-' + str(uuid.uuid4())
    with evaluation.db.write() as db:
        row = db.execute('SELECT * FROM catalog_observations WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            from ...protocol.harness_names import HARNESS_NAMES
            from ..service.harness_health import read_health
            records = harnesses if harnesses is not None else [read_health(db, name) for name in HARNESS_NAMES]
            db.execute('INSERT INTO catalog_observations(request_id,created_at,harnesses_json) VALUES(?,?,?)',
                       (request_id, evaluation._now(), canonical_json(records)))
            row = db.execute('SELECT * FROM catalog_observations WHERE request_id=?', (request_id,)).fetchone()
        return {'observationId': row['observation_id'], 'response': json.loads(row['response_json']) if row['response_json'] else None,
                'harnesses': json.loads(row['harnesses_json'])}


def current(db):
    providers, results = [], []
    for row in db.execute('SELECT c.*, d.payload_json, o.response_json FROM catalog_current c LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id LEFT JOIN catalog_observations o ON o.observation_id=c.observation_id ORDER BY c.harness'):
        from .catalog import catalog_account_status
        observed = json.loads(row['response_json']) if row['response_json'] else {}
        discovery = next((item for item in observed.get('catalog', {}).get('discoveries', [])
                          if item['harness'] == row['harness']), {})
        results.append({'harness': row['harness'], 'status': row['status'],
                        'accountStatus': catalog_account_status(db, row['harness']),
                        'reason': json.loads(row['reason']) if row['reason'] else None,
                        'content': discovery.get('content')})
        if row['payload_json']:
            from ..service.harness_health import read_health
            health = read_health(db, row['harness'])
            healthy = health['available']
            providers.extend({**p, 'models': [{**m, 'available': bool(m.get('available', True)) and healthy,
                              'unavailableReason': m.get('unavailableReason') if healthy or not m.get('available', True) else health['reason'],
                              'unavailableContent': m.get('unavailableContent') if healthy or not m.get('available', True) else None} for m in p['models']]}
                             for p in json.loads(row['payload_json'])['providers'] if p['harness'] == row['harness'])
    if not results:
        return None
    return CatalogView.from_payload({'source': 'current-native-observations', 'providers': providers, 'discoveries': results})


def _buddy_content(row):
    # Reading provenance does not change a buddy's facts.
    return {key: row[key] for key in row.keys()
            if key != 'source'}


def record(evaluation, discovered, observation_id=None, *, health_generation=None):
    payload = canonical_payload(discovered)
    if observation_id is None:
        observation_id = begin(evaluation)['observationId']
    identity = {k: v for k, v in payload.items() if k != 'discoveredAt'}
    discovery_id = 'cat-' + sha256_text(canonical_json(identity))[:24]
    with evaluation.db.write() as db:
        if (evaluation.board.directory / 'upgrade.json').exists():
            raise BoardError('UPGRADE_IN_PROGRESS', 'Catalog publication is fenced until upgrade verification finishes')
        if health_generation is not None:
            name, generation = health_generation
            health = db.execute('SELECT revision,status FROM harness_health WHERE harness=?', (name,)).fetchone()
            if health is None or health['revision'] != generation or health['status'] != 'ready':
                return {'staleHarnesses': [name], 'appliedHarnesses': []}
        observation = db.execute('SELECT * FROM catalog_observations WHERE observation_id=?', (observation_id,)).fetchone()
        if observation is None:
            raise BoardError('NOT_FOUND', 'Unknown catalog observation')
        if observation['response_json']:
            response = json.loads(observation['response_json'])
            if response['discoveryId'] != discovery_id:
                raise BoardError('CONFLICT', 'This observation already recorded a different discovery')
            return {**response, 'duplicate': True}
        now = evaluation._now()
        duplicate = db.execute('SELECT 1 FROM evaluation_catalog WHERE discovery_id=?', (discovery_id,)).fetchone() is not None
        db.execute('INSERT OR IGNORE INTO evaluation_catalog(discovery_id,discovered_at,source,harness_version,provider_version,payload_json,created_at) VALUES(?,?,?,?,?,?,?)', (discovery_id, now, payload['source'], payload.get('harnessVersion'), payload.get('providerVersion'), canonical_json(payload), now))
        before = {row['buddy_id']: _buddy_content(row)
                  for row in db.execute('SELECT * FROM buddies')}
        pending_before = [tuple(row) for row in db.execute('SELECT * FROM catalog_pending ORDER BY harness,provider,model')]
        applied, stale = [], []
        transitions = []
        from .catalog import (CONFIRMATION_SECONDS, CONFIRMED_ABSENCE_REASON, RETIRED_EFFORT_REASON,
                              TRUSTED_ACCOUNT_STATUSES, _model_service_key, _parse_time, _pending_map,
                              _write_pending_map, identities_from_payload, note_confirmed_read,
                              restore_retained_availability)
        for result in payload['discoveries']:
            name = result['harness']
            prior = db.execute('SELECT c.*, d.payload_json FROM catalog_current c LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.harness=?', (name,)).fetchone()
            if prior and int(prior['observation_id']) > observation_id:
                stale.append(name)
                continue
            # ADR-027 rule 1: the blackboard, not the harness, decides trust. A
            # complete reading without a confirmed (or not-applicable) account
            # fact is recorded as an unknown observation and replaces nothing.
            trusted = result['status'] == 'complete' and result.get('accountStatus') in TRUSTED_ACCOUNT_STATUSES
            stored_reason = None if trusted else (
                reason('catalog.account-status-unknown', harness=name) if result['status'] == 'complete'
                else result.get('reason') or reason('catalog.native-unknown', harness=name))
            selected_discovery = discovery_id if trusted else (prior['discovery_id'] if prior else None)
            db.execute('INSERT INTO catalog_current(harness,observation_id,discovery_id,status,reason,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(harness) DO UPDATE SET observation_id=excluded.observation_id,discovery_id=excluded.discovery_id,status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at', (name, observation_id, selected_discovery, 'complete' if trusted else 'unknown', canonical_json(stored_reason) if stored_reason else None, now))
            applied.append(name)
            if not trusted:
                # Health may have recovered while the account fact stayed
                # unknown: the retained confirmed catalog keeps its model state,
                # and the 0s health left behind are not a disappearance.
                restore_retained_availability(db, name)
                continue
            note_confirmed_read(db, name, now=now, account_status=result.get('accountStatus'))
            if name == 'zcode':
                from ...protocol.billing import zcode_access
                health_row = db.execute("SELECT record_json FROM harness_health WHERE harness='zcode'").fetchone()
                if health_row:
                    record = json.loads(health_row['record_json'])
                    native_access = {p['provider']: zcode_access(p.get('accessType'), now)
                                     for p in discovered.get('providers', [])
                                     if isinstance(p, dict) and p.get('harness') == 'zcode'
                                     and any(item['harness'] == 'zcode' and item['provider'] == p.get('provider')
                                             for item in payload['providers'])}
                    record['billingByProvider'] = native_access
                    db.execute("UPDATE harness_health SET record_json=? WHERE harness='zcode'", (canonical_json(record),))
            view = CatalogView.from_payload({**payload, 'providers': [p for p in payload['providers'] if p['harness'] == name]})
            view.discovery_id = discovery_id
            from ..service.harness_health import read_health
            from ...buddy.harnesses.runtime_selection import bound
            # Publication can run in a background discovery thread with no
            # request context. Derive certificates from this transaction's
            # authoritative health version, not a stale or absent caller bind.
            with bound([read_health(db, name)]):
                entries = view.proposed_buddies()
            seen = {p['buddyId'] for p in entries}
            model_services = {(p['provider'], p['model']) for p in entries}
            pending = _pending_map(db, name)
            # The model services the last adopted catalog still listed as available.
            # Health 0s on buddy rows are deliberately absent here: they are
            # not evidence that a native model disappeared (ADR-027 rule 2).
            retained_identities = identities_from_payload(
                prior['payload_json'] if prior is not None else None, name)
            retained = {(provider, model) for provider, model, _effort in retained_identities}
            existing = db.execute('SELECT buddy_id,provider,model,effort,available,unavailable_code'
                                  ' FROM buddies WHERE harness=?', (name,)).fetchall()
            model_service_rows: dict[tuple[str, str], list] = {}
            for row in existing:
                model_service_rows.setdefault((row['provider'], row['model']), []).append(row)
            absent_before = {model_service for model_service, rows in model_service_rows.items()
                             if rows and all(not row['available'] for row in rows)
                             and any(row['unavailable_code'] == CONFIRMED_ABSENCE_REASON for row in rows)}
            for model_service, rows in model_service_rows.items():
                if model_service in model_services:
                    continue
                if _model_service_key(*model_service) not in pending and model_service not in retained:
                    # An archived board can retain a user's configured buddy
                    # without retaining any catalog history. Its first trusted
                    # reading grounds the absence directly in catalog evidence.
                    db.execute('UPDATE buddies SET available=0,unavailable_code=?,unavailable_params_json=?,unavailable_content=NULL'
                               ' WHERE harness=? AND provider=? AND model=?',
                               (CONFIRMED_ABSENCE_REASON, '{}', name, *model_service))
                    continue
                # ADR-027 rule 2: disappearance is a model-identity question. An
                # effort that vanished while its model remains is a metadata
                # retirement, never a model disappearance.
                key = _model_service_key(*model_service)
                since = pending[key]['since'] if key in pending else None
                first_absence, moment = _parse_time(since), _parse_time(now)
                if since is None:
                    efforts = sorted(effort for provider, model, effort in retained_identities
                                     if (provider, model) == model_service)
                    pending[key] = {'since': now, 'efforts': efforts}
                    # The prior adopted reading proves the legal efforts even
                    # when old health code overwrote a retirement reason. Save
                    # those efforts before the empty reading replaces it.
                    for row in rows:
                        if (row['effort'] in efforts and not row['available'] and
                                row['unavailable_code'] not in (CONFIRMED_ABSENCE_REASON, RETIRED_EFFORT_REASON)):
                            db.execute('UPDATE buddies SET available=1,unavailable_code=NULL,unavailable_params_json=NULL,unavailable_content=NULL'
                                       ' WHERE buddy_id=?', (row['buddy_id'],))
                    transitions.append({'kind': 'catalog.model_pending', 'harness': name,
                                        'provider': model_service[0], 'model': model_service[1], 'pendingSince': now})
                elif (first_absence is not None and moment is not None
                        and (moment - first_absence).total_seconds() >= CONFIRMATION_SECONDS):
                    db.execute('UPDATE buddies SET available=0,unavailable_code=?,unavailable_params_json=?,unavailable_content=NULL'
                               ' WHERE harness=? AND provider=? AND model=? AND available=1',
                               (CONFIRMED_ABSENCE_REASON, '{}', name, *model_service))
                    pending.pop(key, None)
                    transitions.append({'kind': 'catalog.model_unavailable', 'harness': name,
                                        'provider': model_service[0], 'model': model_service[1],
                                        'pendingSince': since, 'confirmedAt': now})
                # Still inside the confirmation window (or an unparseable stamp):
                # the model stays usable and no transition is invented.
            # Effort rows missing while their model remains are metadata
            # retirements; enabled remains the user's intent.
            for old in existing:
                if old['buddy_id'] not in seen and (old['provider'], old['model']) in model_services:
                    db.execute('UPDATE buddies SET available=0,unavailable_code=?,unavailable_params_json=?,unavailable_content=NULL WHERE buddy_id=?',
                               (RETIRED_EFFORT_REASON, '{}', old['buddy_id']))
            for p in entries:
                from ..service.harness_health import read_health
                health = read_health(db, name)
                if p['available'] and not health['available']:
                    p = {**p, 'available': False, 'unavailableReason': health['reason'],
                         'unavailableContent': None}
                existing_row = db.execute('SELECT harness,provider,model,effort FROM buddies WHERE buddy_id=?', (p['buddyId'],)).fetchone()
                if existing_row and tuple(existing_row) != tuple(p[key] for key in ('harness', 'provider', 'model', 'effort')):
                    raise BoardError('CATALOG_INVALID', 'A native buddy ID collided with a different execution identity', buddyId=p['buddyId'])
                unavailable = p['unavailableReason']
                db.execute('INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,capabilities_json,context_window,description,source,unavailable_code,unavailable_params_json,unavailable_content) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?,?,?) ON CONFLICT(buddy_id) DO UPDATE SET label=excluded.label,available=excluded.available,capabilities_json=excluded.capabilities_json,context_window=excluded.context_window,description=excluded.description,source=excluded.source,unavailable_code=excluded.unavailable_code,unavailable_params_json=excluded.unavailable_params_json,unavailable_content=excluded.unavailable_content', (p['buddyId'], p['label'], p['harness'], p['provider'], p['model'], p['effort'], int(p['available']), canonical_json(p['capabilities']), p['contextWindow'], p['description'], p['source'], unavailable['code'] if unavailable else None, canonical_json(unavailable['params']) if unavailable else None, p['unavailableContent']))
            # ADR-027 rules 2 and 6: reappearance inside a trusted reading
            # recovers the model service, clears its first absence and says so once.
            for model_service in model_services:
                key = _model_service_key(*model_service)
                if key not in pending and model_service not in absent_before:
                    continue
                window = pending.pop(key, None)
                since = window['since'] if window is not None else None
                usable = db.execute('SELECT 1 FROM buddies WHERE harness=? AND provider=? AND model=? AND available=1 LIMIT 1',
                                    (name, *model_service)).fetchone() is not None
                if usable:
                    transition = {'kind': 'catalog.model_recovered', 'harness': name,
                                  'provider': model_service[0], 'model': model_service[1], 'recoveredAt': now}
                    if since:
                        transition['pendingSince'] = since
                    transitions.append(transition)
            _write_pending_map(db, name, pending)
        table_changed = False
        for row in db.execute('SELECT * FROM buddies').fetchall():
            if before.get(row['buddy_id']) != _buddy_content(row):
                table_changed = True
        pending_after = [tuple(row) for row in db.execute('SELECT * FROM catalog_pending ORDER BY harness,provider,model')]
        table_changed = table_changed or pending_before != pending_after
        active_count = db.execute('SELECT COUNT(*) FROM buddies WHERE available=1').fetchone()[0]
        if active_count > 200:
            raise BoardError('CATALOG_LIMIT', 'The current native buddy set exceeds 200 buddies; no entries were silently dropped')
        if table_changed:
            db.execute('UPDATE evaluation_state SET updated_at=? WHERE id=1', (now,))
        response = {'discoveryId': discovery_id, 'observationId': observation_id, 'duplicate': duplicate, 'catalog': {**payload, 'discoveryId': discovery_id, 'observationId': observation_id, 'discoveredAt': now}, 'buddies': CatalogView.from_payload(payload).proposed_buddies(), 'appliedHarnesses': applied, 'staleHarnesses': stale}
        db.execute('UPDATE catalog_observations SET completed_at=?,response_json=? WHERE observation_id=?', (now, canonical_json(response), observation_id))
        for transition in transitions:
            kind = transition.pop('kind')
            evaluation.board._append_event(db, kind, payload=transition)
        if table_changed:
            evaluation.board._append_event(db, 'evaluation.catalog_discovered', payload={'discoveryId': discovery_id, 'observationId': observation_id, 'appliedHarnesses': applied, 'staleHarnesses': stale})
        head = evaluation.board._head_of(db)
    if table_changed:
        evaluation.board._notify(head)
    return response


def buddies(evaluation, params):
    from ...protocol import schemas
    schemas.reject_unknown(params, {'limit', 'after', 'includeUnavailable', 'query', 'harness'}, 'buddy.list')
    limit = params.get('limit', 100)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise BoardError('INVALID_ARGUMENT', 'limit must be between 1 and 200')
    after = params.get('after', '')
    if not isinstance(after, str) or len(after) > 128:
        raise BoardError('INVALID_ARGUMENT', 'after must be a buddy cursor')
    include = schemas.optional_bool(params, 'includeUnavailable', False)
    query = schemas.optional_string(params, 'query', max_length=200) or ''
    harness = schemas.optional_string(params, 'harness', max_length=32)
    clauses = ['p.buddy_id>?', "(? OR (p.available=1 AND h.status='ready'))"]
    values = [after, int(include)]
    if harness:
        clauses.append('p.harness=?')
        values.append(harness)
    for term in query.split()[:8]:
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append("(p.buddy_id LIKE ? ESCAPE '\\' OR p.label LIKE ? ESCAPE '\\' OR p.model LIKE ? ESCAPE '\\' OR p.provider LIKE ? ESCAPE '\\' OR p.harness LIKE ? ESCAPE '\\')")
        values.extend(['%' + escaped + '%'] * 5)
    if len(query.split()) > 8:
        raise BoardError('INVALID_ARGUMENT', 'query accepts at most 8 search terms')
    with evaluation.db.read() as db:
        rows = db.execute('SELECT p.*, h.status AS harness_status, c.status AS catalog_state, c.reason AS catalog_reason FROM buddies p LEFT JOIN harness_health h ON h.harness=p.harness LEFT JOIN catalog_current c ON c.harness=p.harness WHERE ' + ' AND '.join(clauses) + ' ORDER BY p.buddy_id LIMIT ?', [*values, limit + 1]).fetchall()
        from .catalog import pending_model_services
        pending = pending_model_services(db)
        current_catalog = current(db)
        effort_ranks = current_catalog.effort_ranks() if current_catalog is not None else {}
        values = [evaluation._buddy_view(row, pending=pending, effort_ranks=effort_ranks) for row in rows[:limit]]
        from ...protocol.billing import for_provider
        from ..routing.quota_routing import buddy_retry
        for value in values:
            value['billing'] = for_provider(db, value['harness'], value['provider'])
            from ..evaluation.native_observations import exhausted
            value['quotaExhausted'] = exhausted(db, value) is not None
            if value['quotaExhausted']:
                value['quotaRetry'] = buddy_retry(db, value)
        ids = [value['buddyId'] for value in values]
        model_services = {(value['harness'], value['provider'], value['model']) for value in values}
        return {'buddies': values, **user_policy.policy_view(db, ids),
                'modelServiceConcurrency': evaluation.board.model_capacity_rows(db, model_services),
                'nextCursor': rows[limit - 1]['buddy_id'] if len(rows) > limit else None}
