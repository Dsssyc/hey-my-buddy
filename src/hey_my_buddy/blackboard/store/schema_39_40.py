"""Schema 39 → 40: store catalog facts once per model service.

This unreleased step owns its target definitions and conversion rules. Sealed
routing and execution evidence is left as written.
"""
from __future__ import annotations

import json


MODEL_SERVICES_DDL = """
CREATE TABLE IF NOT EXISTS model_services (
    harness           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    context_window INTEGER,
    input_modalities_json TEXT NOT NULL DEFAULT '[]',
    unavailable_code TEXT,
    unavailable_params_json TEXT,
    unavailable_content TEXT,
    pending_since TEXT,
    confirmed_absent_at TEXT,
    concurrency_limit INTEGER CHECK (concurrency_limit >= 1),
    source_sequence INTEGER,
    CHECK ((unavailable_code IS NULL) = (unavailable_params_json IS NULL)),
    PRIMARY KEY (harness, provider, model)
)
"""
BUDDIES_DDL = """
CREATE TABLE IF NOT EXISTS buddies (
    buddy_id          TEXT PRIMARY KEY,
    alias             TEXT,
    harness             TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    effort              TEXT NOT NULL,
    effort_rank         INTEGER NOT NULL DEFAULT 0,
    retired             INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (harness,provider,model)
        REFERENCES model_services(harness,provider,model) ON DELETE RESTRICT
)
"""
CATALOG_CURRENT_DDL = """
CREATE TABLE IF NOT EXISTS catalog_current (
    harness TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    content TEXT,
    updated_at TEXT NOT NULL,
    read_at TEXT,
    account_status TEXT NOT NULL DEFAULT 'unknown'
)
"""
INDEXES = ("CREATE INDEX IF NOT EXISTS buddies_harness_idx ON buddies(harness, buddy_id)",)
ABSENT = 'catalog.model-absent'
RETIRED = 'catalog.effort-retired'
NATIVE = 'catalog.native-unavailable'


def _payloads(connection):
    return {row['discovery_id']: json.loads(row['payload_json'])
            for row in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog')}


def _proven_descriptions(payloads):
    proven = set()
    for payload in payloads.values():
        for provider in payload['providers']:
            for model in provider['models']:
                if model['description']:
                    continue
                synthetic = f"{model['name']} via {provider['displayName']}"[:4000]
                for effort in model['efforts']:
                    proven.add((provider['harness'], provider['provider'], model['id'], effort, synthetic))
    return proven


def _observations(connection):
    by_discovery = {}
    by_sequence = {}
    for row in connection.execute('SELECT observation_id,response_json FROM catalog_observations ORDER BY observation_id'):
        if row['response_json']:
            response = json.loads(row['response_json'])
            discovery = response.get('discoveryId')
            if discovery:
                by_discovery[discovery] = row['observation_id']
            by_sequence[row['observation_id']] = discovery
    return by_discovery, by_sequence


def _current_models(payloads, current):
    models = {}
    for harness, row in current.items():
        payload = payloads.get(row['discovery_id'])
        if payload is None:
            continue
        for provider in payload['providers']:
            if provider['harness'] == harness:
                for model in provider['models']:
                    models[harness, provider['provider'], model['id']] = model
    return models


def _model_in_payload(payload, key):
    if payload is None:
        return None
    return next((model for provider in payload['providers']
                 if (provider['harness'], provider['provider']) == key[:2]
                 for model in provider['models'] if model['id'] == key[2]), None)


def _discovery_content(payloads, observation_discovery, current):
    result = {}
    for harness, row in current.items():
        observed = payloads.get(observation_discovery.get(row['observation_id']))
        if observed:
            discovery = next((item for item in observed['discoveries'] if item['harness'] == harness), None)
            if discovery:
                result[harness] = discovery.get('content')
    return result


def _confirmed_times(connection):
    confirmed = {}
    for row in connection.execute("SELECT payload_json,created_at FROM events WHERE kind='catalog.model_unavailable' ORDER BY seq"):
        payload = json.loads(row['payload_json'])
        key = (payload.get('harness'), payload.get('provider'), payload.get('model'))
        confirmed[key] = payload.get('confirmedAt') or row['created_at']
    return confirmed


def upgrade(connection):
    payloads = _payloads(connection)
    proven = _proven_descriptions(payloads)
    source_sequence, observation_discovery = _observations(connection)
    current = {row['harness']: dict(row) for row in connection.execute('SELECT * FROM catalog_current')}
    current_models = _current_models(payloads, current)
    content = _discovery_content(payloads, observation_discovery, current)
    confirmed_times = _confirmed_times(connection)
    pending = {(r['harness'], r['provider'], r['model']): dict(r)
               for r in connection.execute('SELECT * FROM catalog_pending')}
    concurrency = {(r['harness'], r['provider'], r['model']): r['concurrency_limit']
                   for r in connection.execute('SELECT * FROM model_service_concurrency')}
    groups = {}
    for row in connection.execute('SELECT rowid AS old_rowid,* FROM buddies ORDER BY rowid'):
        key = (row['harness'], row['provider'], row['model'])
        groups.setdefault(key, []).append(dict(row))

    # The old catalog tables and indexes can be replaced inside the install's
    # single transaction. Preserve buddy rowids for local physical inspection.
    connection.execute('DROP TABLE buddies')
    connection.execute('DROP TABLE catalog_pending')
    connection.execute('DROP TABLE catalog_current')
    connection.execute('DROP TABLE catalog_observations')
    connection.execute('DROP TABLE evaluation_catalog')
    connection.execute('DROP TABLE model_service_concurrency')
    connection.execute(MODEL_SERVICES_DDL)
    connection.execute(BUDDIES_DDL)
    for ddl in INDEXES:
        connection.execute(ddl)
    connection.execute(CATALOG_CURRENT_DDL)

    for key in sorted(groups.keys() | concurrency.keys() | pending.keys()):
        rows = groups.get(key, [])
        current_model = current_models.get(key)
        rank = {effort: index for index, effort in enumerate(current_model['efforts'])} if current_model else {}
        current_id = current.get(key[0], {}).get('discovery_id')
        current_source = f'catalog:{current_id}' if current_id else None
        matching = [row for row in rows if current_model and row['source'] == current_source
                    and row['effort'] in rank]
        selected = matching[0] if matching else (rows[-1] if rows else None)
        source = selected['source'] if selected else ''
        discovery = source.removeprefix('catalog:') if source.startswith('catalog:') else None
        model = current_model or _model_in_payload(payloads.get(discovery), key)
        description = selected['description'] if selected else ''
        if selected and (*key, selected['effort'], description) in proven:
            description = ''
        modalities = ([capability[6:] for capability in json.loads(selected['capabilities_json'])
                       if capability.startswith('input:')] if selected else [])
        unavailable_code = selected['unavailable_code'] if selected else None
        unavailable_params = selected['unavailable_params_json'] if selected else None
        unavailable_content = selected['unavailable_content'] if selected else None
        # A current native reading is authoritative even if a later health
        # update overwrote every buddy's old availability columns.
        if model is not None:
            native_reason = model.get('unavailableReason')
            if native_reason and (current_model is not None or unavailable_code != ABSENT):
                unavailable_code = native_reason['code']
                unavailable_params = json.dumps(native_reason.get('params', {}), ensure_ascii=False,
                                                sort_keys=True, separators=(',', ':'))
                unavailable_content = model.get('unavailableContent')
            elif current_model is not None or unavailable_code != ABSENT:
                unavailable_code = unavailable_params = unavailable_content = None
        elif unavailable_code not in (ABSENT, NATIVE):
            unavailable_code = unavailable_params = unavailable_content = None
        if unavailable_code == ABSENT:
            current_row = current.get(key[0], {})
            confirmed_at = confirmed_times.get(key) or current_row.get('read_at') or current_row.get('updated_at')
            unavailable_code = unavailable_params = unavailable_content = None
        else:
            confirmed_at = None
        if unavailable_code is None:
            unavailable_params = unavailable_content = None
        sequence = source_sequence.get(discovery) if discovery in payloads else None
        connection.execute('''INSERT INTO model_services
            (harness,provider,model,description,context_window,input_modalities_json,
             unavailable_code,unavailable_params_json,unavailable_content,pending_since,
             confirmed_absent_at,concurrency_limit,source_sequence)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (*key, description, selected['context_window'] if selected else None,
             json.dumps(modalities, ensure_ascii=False, separators=(',', ':')),
             unavailable_code, unavailable_params, unavailable_content,
             pending[key]['since'] if key in pending else None,
             confirmed_at, concurrency.get(key), sequence))
        # A historical health writer could overwrite an effort's retirement
        # reason. Current/pending native identities still prove which efforts
        # were legal; keep that one-time recovery in this old-form conversion.
        missing_rank = len(rank)
        for row in rows:
            effort_rank = rank.get(row['effort'], missing_rank)
            if row['effort'] not in rank:
                missing_rank += 1
            connection.execute('''INSERT INTO buddies
                (rowid,buddy_id,alias,harness,provider,model,effort,effort_rank,retired,enabled)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (row['old_rowid'], row['buddy_id'], None, *key, row['effort'], effort_rank,
                 int(row['unavailable_code'] == RETIRED
                     or (current_model is not None and row['effort'] not in rank)
                     or (key in pending and row['effort'] not in json.loads(pending[key]['efforts_json']))),
                 row['enabled']))
    for harness, row in current.items():
        connection.execute('''INSERT INTO catalog_current
            (harness,sequence,status,reason,content,updated_at,read_at,account_status)
            VALUES(?,?,?,?,?,?,?,?)''',
            (harness, row['observation_id'], row['status'], row['reason'], content.get(harness),
             row['updated_at'], row['read_at'], row['account_status']))
    connection.execute("UPDATE meta SET value='40' WHERE key='schema_version'")
