"""Ordered native observations and retained profile identities."""
from __future__ import annotations

import json
import uuid

from . import user_policy
from .catalog import CatalogView, canonical_payload
from .db import canonical_json, sha256_text
from .errors import BoardError


def begin(evaluation, request_id=None):
    request_id = request_id or 'catalog-' + str(uuid.uuid4())
    with evaluation.db.write() as db:
        db.execute('INSERT OR IGNORE INTO catalog_observations(request_id,created_at) VALUES(?,?)', (request_id, evaluation._now()))
        row = db.execute('SELECT * FROM catalog_observations WHERE request_id=?', (request_id,)).fetchone()
        return {'observationId': row['observation_id'], 'response': json.loads(row['response_json']) if row['response_json'] else None}


def current(db):
    providers, results = [], []
    for row in db.execute('SELECT c.*, d.payload_json FROM catalog_current c LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id ORDER BY c.adapter'):
        results.append({'adapter': row['adapter'], 'status': row['status'], 'reason': row['reason']})
        if row['payload_json']:
            providers.extend(p for p in json.loads(row['payload_json'])['providers'] if p['adapter'] == row['adapter'])
    if not results:
        return None
    return CatalogView.from_payload({'source': 'current-native-observations', 'providers': providers, 'discoveries': results})


def record(evaluation, discovered, observation_id=None):
    payload = canonical_payload(discovered)
    if observation_id is None:
        observation_id = begin(evaluation)['observationId']
    identity = {k: v for k, v in payload.items() if k != 'discoveredAt'}
    discovery_id = 'cat-' + sha256_text(canonical_json(identity))[:24]
    with evaluation.db.write() as db:
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
        for result in payload['discoveries']:
            name = result['adapter']
            prior = db.execute('SELECT * FROM catalog_current WHERE adapter=?', (name,)).fetchone()
            if prior and int(prior['observation_id']) > observation_id:
                stale.append(name)
                continue
            complete = result['status'] == 'complete'
            selected_discovery = discovery_id if complete else (prior['discovery_id'] if prior else None)
            db.execute('INSERT INTO catalog_current(adapter,observation_id,discovery_id,status,reason,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(adapter) DO UPDATE SET observation_id=excluded.observation_id,discovery_id=excluded.discovery_id,status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at', (name, observation_id, selected_discovery, result['status'], result.get('reason'), now))
            applied.append(name)
            if not complete:
                continue
            view = CatalogView.from_payload({**payload, 'providers': [p for p in payload['providers'] if p['adapter'] == name]})
            view.discovery_id = discovery_id
            entries = view.proposed_profiles()
            seen = {p['profileId'] for p in entries}
            # Missing entries become unavailable; enabled remains the user's intent.
            for old in db.execute('SELECT profile_id FROM evaluation_profiles WHERE adapter=?', (name,)).fetchall():
                if old['profile_id'] not in seen:
                    db.execute('UPDATE evaluation_profiles SET available=0,unavailable_reason=?,updated_revision=? WHERE profile_id=?', ('not present in the latest complete native discovery', revision, old['profile_id']))
            for p in entries:
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
    clauses = ['p.profile_id>?', '(? OR p.available=1)']
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
        rows = db.execute('SELECT p.*, c.status AS catalog_state, c.reason AS catalog_reason FROM evaluation_profiles p LEFT JOIN catalog_current c ON c.adapter=p.adapter WHERE ' + ' AND '.join(clauses) + ' ORDER BY p.profile_id LIMIT ?', [*values, limit + 1]).fetchall()
        values = [evaluation._profile_view(row) for row in rows[:limit]]
        ids = [value['profileId'] for value in values]
        marks = ','.join('?' for _ in ids) or 'NULL'
        cards = [evaluation._card_view(row) for row in db.execute(f'SELECT * FROM evaluation_cards WHERE profile_id IN ({marks})', ids)]
        families = {(value['adapter'], value['provider'], value['model']) for value in values}
        return {'profiles': values, 'cards': cards, **user_policy.policy_view(db, ids),
                'modelConcurrency': evaluation.board.model_capacity_rows(db, families),
                'sampleCounts': {profile_id: evaluation._sample_count(db, profile_id) for profile_id in ids},
                'tableRevision': int(evaluation._state(db)['table_revision']),
                'nextCursor': rows[limit - 1]['profile_id'] if len(rows) > limit else None}
