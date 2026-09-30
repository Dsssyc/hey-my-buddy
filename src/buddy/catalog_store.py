"""Ordered native observations and retained profile identities."""
from __future__ import annotations

import json
import uuid

from . import user_policy
from .catalog import CatalogView, canonical_payload
from .db import canonical_json, sha256_text
from .errors import BoardError


def begin(evaluation, request_id=None, *, harnesses=None):
    request_id = request_id or 'catalog-' + str(uuid.uuid4())
    with evaluation.db.write() as db:
        db.execute('INSERT OR IGNORE INTO catalog_observations(request_id,created_at) VALUES(?,?)', (request_id, evaluation._now()))
        row = db.execute('SELECT * FROM catalog_observations WHERE request_id=?', (request_id,)).fetchone()
        from .accounts import ADAPTERS
        from .harness_health import read_health
        records = harnesses if harnesses is not None else [read_health(db, name) for name in ADAPTERS]
        db.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', ('catalog-accounts:' + str(row['observation_id']),
                   canonical_json({record['adapter']: record['account'] for record in records})))
        db.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', ('catalog-harnesses:' + str(row['observation_id']), canonical_json(records)))
        bindings = db.execute('SELECT value FROM meta WHERE key=?', ('catalog-accounts:' + str(row['observation_id']),)).fetchone()
        frozen = db.execute('SELECT value FROM meta WHERE key=?', ('catalog-harnesses:' + str(row['observation_id']),)).fetchone()
        return {'observationId': row['observation_id'], 'response': json.loads(row['response_json']) if row['response_json'] else None,
                'accounts': json.loads(bindings[0]), 'harnesses': json.loads(frozen[0])}


def current(db, *, accounts=None):
    providers, results = [], []
    for row in db.execute('SELECT c.*, d.payload_json FROM catalog_current c LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id ORDER BY c.adapter'):
        from .accounts import identity
        saved = db.execute('SELECT value FROM meta WHERE key=?', ('catalog-account:' + row['adapter'],)).fetchone()
        account = json.loads(saved[0]) if saved else {'source': 'native', 'credentialRevision': 0}
        if accounts is not None and (row['adapter'] not in accounts or account != identity(accounts[row['adapter']])):
            results.append({'adapter': row['adapter'], 'status': 'unknown', 'reason': 'ACCOUNT_BINDING_CHANGED'})
            continue
        results.append({'adapter': row['adapter'], 'status': row['status'], 'reason': row['reason']})
        if row['payload_json']:
            from .harness_health import read_health
            healthy = read_health(db, row['adapter'])['available']
            from .accounts import selection
            healthy = healthy and account == identity(selection(db, row['adapter']))
            providers.extend({**p, 'models': [{**m, 'available': bool(m.get('available', True)) and healthy,
                              'unavailableReason': m.get('unavailableReason') if healthy else 'HARNESS_UNHEALTHY'} for m in p['models']]}
                             for p in json.loads(row['payload_json'])['providers'] if p['adapter'] == row['adapter'])
    if not results:
        return None
    return CatalogView.from_payload({'source': 'current-native-observations', 'providers': providers, 'discoveries': results})


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
            health = db.execute('SELECT revision,status FROM harness_health WHERE adapter=?', (name,)).fetchone()
            if health is None or health['revision'] != generation or health['status'] != 'ready':
                return {'staleAdapters': [name], 'appliedAdapters': []}
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
        revision = int(evaluation._state(db)['table_revision']) + 1
        applied, stale, changed = [], [], 0
        from .accounts import identity, selection
        saved_accounts = db.execute('SELECT value FROM meta WHERE key=?', ('catalog-accounts:' + str(observation_id),)).fetchone()
        frozen_accounts = json.loads(saved_accounts[0]) if saved_accounts else {}
        for result in payload['discoveries']:
            name = result['adapter']
            account = frozen_accounts.get(name, {'source': 'native', 'credentialRevision': 0})
            if identity(account) != identity(selection(db, name)):
                stale.append(name)
                continue
            prior = db.execute('SELECT * FROM catalog_current WHERE adapter=?', (name,)).fetchone()
            if prior and int(prior['observation_id']) > observation_id:
                stale.append(name)
                continue
            complete = result['status'] == 'complete'
            if complete:
                db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           ('catalog-account:' + name, canonical_json(identity(account))))
            selected_discovery = discovery_id if complete else (prior['discovery_id'] if prior else None)
            db.execute('INSERT INTO catalog_current(adapter,observation_id,discovery_id,status,reason,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(adapter) DO UPDATE SET observation_id=excluded.observation_id,discovery_id=excluded.discovery_id,status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at', (name, observation_id, selected_discovery, result['status'], result.get('reason'), now))
            applied.append(name)
            if not complete:
                continue
            if name == 'zcode':
                from .billing import zcode_access
                health_row = db.execute("SELECT record_json FROM harness_health WHERE adapter='zcode'").fetchone()
                if health_row:
                    record = json.loads(health_row['record_json'])
                    native_access = {p['provider']: zcode_access(p.get('accessType'), now)
                                     for p in discovered.get('providers', [])
                                     if isinstance(p, dict) and p.get('adapter') == 'zcode'
                                     and any(item['adapter'] == 'zcode' and item['provider'] == p.get('provider')
                                             for item in payload['providers'])}
                    record['billingByProvider'] = native_access
                    db.execute("UPDATE harness_health SET record_json=? WHERE adapter='zcode'", (canonical_json(record),))
            view = CatalogView.from_payload({**payload, 'providers': [p for p in payload['providers'] if p['adapter'] == name]})
            view.discovery_id = discovery_id
            from .harness_health import read_health
            from .harness_runtime import bound
            # Publication can run in a background discovery thread with no
            # request context. Derive certificates from this transaction's
            # authoritative health version, not a stale or absent caller bind.
            with bound([read_health(db, name)]):
                entries = view.proposed_profiles()
            seen = {p['profileId'] for p in entries}
            # Missing entries become unavailable; enabled remains the user's intent.
            for old in db.execute('SELECT profile_id FROM evaluation_profiles WHERE adapter=?', (name,)).fetchall():
                if old['profile_id'] not in seen:
                    db.execute('UPDATE evaluation_profiles SET available=0,unavailable_reason=?,updated_revision=? WHERE profile_id=?', ('not present in the latest complete native discovery', revision, old['profile_id']))
            for p in entries:
                health = db.execute('SELECT status FROM harness_health WHERE adapter=?', (name,)).fetchone()
                if health is not None and health['status'] != 'ready':
                    p = {**p, 'available': False, 'unavailableReason': 'HARNESS_UNHEALTHY'}
                existing = db.execute('SELECT adapter,provider,model,effort FROM evaluation_profiles WHERE profile_id=?', (p['profileId'],)).fetchone()
                if existing and tuple(existing) != tuple(p[key] for key in ('adapter', 'provider', 'model', 'effort')):
                    raise BoardError('CATALOG_INVALID', 'A native profile ID collided with a different execution identity', profileId=p['profileId'])
                db.execute('INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,available,enabled,capabilities_json,context_window,description,source,unavailable_reason,created_revision,updated_revision) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET label=excluded.label,available=excluded.available,capabilities_json=excluded.capabilities_json,context_window=excluded.context_window,description=excluded.description,source=excluded.source,unavailable_reason=excluded.unavailable_reason,updated_revision=excluded.updated_revision', (p['profileId'], p['label'], p['adapter'], p['provider'], p['model'], p['effort'], int(p['available']), canonical_json(p['capabilities']), p['contextWindow'], p['description'], p['source'], p['unavailableReason'], revision, revision))
            changed += len(entries)
        active_count = db.execute('SELECT COUNT(*) FROM evaluation_profiles WHERE available=1').fetchone()[0]
        if active_count > 200:
            raise BoardError('CATALOG_LIMIT', 'The current native profile set exceeds 200 configurations; no entries were silently dropped')
        if applied:
            db.execute('UPDATE evaluation_state SET table_revision=?,updated_at=? WHERE id=1', (revision, now))
            db.execute('INSERT INTO evaluation_revisions(revision,kind,writer_id,actor,counts_json,created_at) VALUES(?,?,?,?,?,?)', (revision, 'catalog', None, 'native-discovery', canonical_json({'profiles': changed, 'observationId': observation_id, 'provided': ['catalog']}), now))
        actual_revision = int(evaluation._state(db)['table_revision'])
        response = {'discoveryId': discovery_id, 'observationId': observation_id, 'duplicate': duplicate, 'tableRevision': actual_revision, 'catalog': {**payload, 'discoveryId': discovery_id, 'observationId': observation_id, 'discoveredAt': now}, 'profiles': CatalogView.from_payload(payload).proposed_profiles(), 'appliedAdapters': applied, 'staleAdapters': stale, 'note': 'Native catalog facts were refreshed; existing user settings and evidence were retained. No model call was made.'}
        db.execute('UPDATE catalog_observations SET completed_at=?,response_json=? WHERE observation_id=?', (now, canonical_json(response), observation_id))
        evaluation.board._append_event(db, 'evaluation.catalog_discovered', revision=actual_revision, payload={'discoveryId': discovery_id, 'observationId': observation_id, 'appliedAdapters': applied, 'staleAdapters': stale})
        head = evaluation.board._head_of(db)
    evaluation.board._notify(head)
    return response


def profiles(evaluation, params):
    from . import schemas
    schemas.reject_unknown(params, {'limit', 'after', 'includeUnavailable', 'query', 'adapter'}, 'model.profiles')
    limit = params.get('limit', 100)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise BoardError('INVALID_ARGUMENT', 'limit must be between 1 and 200')
    after = params.get('after', '')
    if not isinstance(after, str) or len(after) > 128:
        raise BoardError('INVALID_ARGUMENT', 'after must be a profile cursor')
    include = schemas.optional_bool(params, 'includeUnavailable', False)
    query = schemas.optional_string(params, 'query', max_length=200) or ''
    adapter = schemas.optional_string(params, 'adapter', max_length=32)
    clauses = ['p.profile_id>?', "(? OR (p.available=1 AND h.status='ready'))"]
    values = [after, int(include)]
    if adapter:
        clauses.append('p.adapter=?')
        values.append(adapter)
    for term in query.split()[:8]:
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append("(p.profile_id LIKE ? ESCAPE '\\' OR p.label LIKE ? ESCAPE '\\' OR p.model LIKE ? ESCAPE '\\' OR p.provider LIKE ? ESCAPE '\\' OR p.adapter LIKE ? ESCAPE '\\')")
        values.extend(['%' + escaped + '%'] * 5)
    if len(query.split()) > 8:
        raise BoardError('INVALID_ARGUMENT', 'query accepts at most 8 search terms')
    with evaluation.db.read() as db:
        rows = db.execute('SELECT p.*, h.status AS harness_status, c.status AS catalog_state, c.reason AS catalog_reason FROM evaluation_profiles p LEFT JOIN harness_health h ON h.adapter=p.adapter LEFT JOIN catalog_current c ON c.adapter=p.adapter WHERE ' + ' AND '.join(clauses) + ' ORDER BY p.profile_id LIMIT ?', [*values, limit + 1]).fetchall()
        values = [evaluation._profile_view(row) for row in rows[:limit]]
        from .billing import for_provider
        from .quota_routing import configuration_retry
        for value in values:
            value['billing'] = for_provider(db, value['adapter'], value['provider'])
            from .native_observations import exhausted
            value['quotaExhausted'] = exhausted(db, value) is not None
            if value['quotaExhausted']:
                value['quotaRetry'] = configuration_retry(db, value)
        ids = [value['profileId'] for value in values]
        marks = ','.join('?' for _ in ids) or 'NULL'
        cards = [evaluation._card_view(row) for row in db.execute(f'SELECT * FROM evaluation_cards WHERE profile_id IN ({marks})', ids)]
        families = {(value['adapter'], value['provider'], value['model']) for value in values}
        return {'profiles': values, 'cards': cards, **user_policy.policy_view(db, ids),
                'modelConcurrency': evaluation.board.model_capacity_rows(db, families),
                'sampleCounts': {profile_id: evaluation._sample_count(db, profile_id) for profile_id in ids},
                'tableRevision': int(evaluation._state(db)['table_revision']),
                'nextCursor': rows[limit - 1]['profile_id'] if len(rows) > limit else None}
