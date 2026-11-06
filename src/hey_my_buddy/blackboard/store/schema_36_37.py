"""Schema 36 → 37: model identities and append-only service mappings.

This frozen step owns its table definitions and conversion; it imports no current
schema or product code. Existing names each become one model.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4


TABLE_DDL = {
    'models': 'CREATE TABLE models (\n    model_id TEXT PRIMARY KEY,\n    display_name TEXT NOT NULL\n)',
    'model_mappings': "CREATE TABLE model_mappings (\n    mapping_id TEXT PRIMARY KEY,\n    harness TEXT NOT NULL,\n    provider TEXT NOT NULL,\n    model TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    created_at TEXT NOT NULL,\n    source TEXT NOT NULL CHECK (source IN ('same-name','profile','user')),\n    evidence_json TEXT\n)",
    'model_notes': 'CREATE TABLE model_notes (\n    model_id   TEXT PRIMARY KEY REFERENCES models(model_id) ON DELETE RESTRICT,\n    text       TEXT NOT NULL,\n    updated_at TEXT NOT NULL\n)',
    'attempts': "CREATE TABLE attempts (\n    attempt_id          TEXT PRIMARY KEY,\n    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,\n    generation          INTEGER NOT NULL,\n    nonce_verifier      TEXT NOT NULL,\n    execution_state     TEXT NOT NULL\n        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),\n    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),\n    runtime_identity    TEXT,\n    adapter             TEXT NOT NULL,\n    -- Frozen model-service identity (harness/provider/model, no effort dimension).\n    -- Written once by the claim transaction that resolved it and never updated\n    -- afterwards, so a historical or shutdown-uncertain attempt can never change\n    -- which model service's concurrency slot it occupies. NULL for model-less work\n    -- (commands) which counts only against the machine-wide ceiling.\n    model_harness       TEXT,\n    model_provider      TEXT,\n    model_model         TEXT,\n    model_id            TEXT REFERENCES models(model_id) ON DELETE RESTRICT,\n    result_json         TEXT,\n    result_form         TEXT CHECK (result_form IN ('archived-report-v1','current-report-v2')),\n    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,\n    cancel_requested_at TEXT,\n    stop_initiator      TEXT CHECK (stop_initiator IN ('host','lead','console','service','unknown')),\n    grace_deadline      TEXT,\n    force_reason        TEXT,\n    force_requested_at  TEXT,\n    identity_confirmed_at TEXT,\n    cleanup_pending     INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),\n    started_at          TEXT,\n    finished_at         TEXT,\n    created_at          TEXT NOT NULL,\n    updated_at          TEXT NOT NULL,\n    revision            INTEGER NOT NULL DEFAULT 1,\n    CHECK ((result_json IS NULL AND result_form IS NULL)\n        OR (result_json IS NOT NULL AND result_form IS NOT NULL)),\n    UNIQUE(task_id, generation)\n)",
    'kind_summaries': 'CREATE TABLE kind_summaries (\n    harness            TEXT NOT NULL,\n    model_id           TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort             TEXT NOT NULL,\n    text               TEXT NOT NULL,\n    maintenance_run_id TEXT NOT NULL REFERENCES maintenance_runs(maintenance_run_id) ON DELETE RESTRICT,\n    PRIMARY KEY (harness, model_id, effort)\n)',
    'kind_summary_evidence': 'CREATE TABLE kind_summary_evidence (\n    harness TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort  TEXT NOT NULL,\n    run_id  TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,\n    cited   INTEGER NOT NULL CHECK (cited IN (0, 1)),\n    PRIMARY KEY (harness, model_id, effort, run_id),\n    FOREIGN KEY (harness, model_id, effort) REFERENCES kind_summaries(harness, model_id, effort) ON DELETE RESTRICT\n)',
    'kind_statistics': "CREATE TABLE kind_statistics (\n    harness TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort TEXT NOT NULL,\n    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),\n    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),\n    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),\n    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),\n    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),\n    defects INTEGER NOT NULL DEFAULT 0 CHECK (typeof(defects)='integer' AND defects >= 0 AND defects <= successes),\n    PRIMARY KEY (harness, model_id, effort),\n    CHECK (successes + failures = samples)\n)",
    'kind_cell_statistics': "CREATE TABLE kind_cell_statistics (\n    harness TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort TEXT NOT NULL,\n    domain_node_id TEXT NOT NULL REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,\n    task_type TEXT NOT NULL CHECK (task_type IN ('create','change','debug','verify','review','research','write')),\n    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),\n    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),\n    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),\n    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),\n    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),\n    defects INTEGER NOT NULL DEFAULT 0 CHECK (typeof(defects)='integer' AND defects >= 0 AND defects <= successes),\n    PRIMARY KEY (harness, model_id, effort, domain_node_id, task_type),\n    CHECK (successes + failures = samples)\n)",
    'kind_statistics_buckets': "CREATE TABLE kind_statistics_buckets (\n    harness TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort TEXT NOT NULL,\n    metric TEXT NOT NULL CHECK (metric IN ('delivery_seconds','input_tokens','cached_input_tokens','output_tokens')),\n    sign INTEGER NOT NULL CHECK (typeof(sign)='integer' AND sign IN (-1,0,1)),\n    bucket INTEGER NOT NULL CHECK (typeof(bucket)='integer'),\n    count INTEGER NOT NULL CHECK (typeof(count)='integer' AND count > 0),\n    CHECK (sign != 0 OR bucket = 0),\n    CHECK (sign != -1 OR metric = 'delivery_seconds'),\n    PRIMARY KEY (harness, model_id, effort, metric, sign, bucket),\n    FOREIGN KEY (harness, model_id, effort) REFERENCES kind_statistics(harness, model_id, effort) ON DELETE RESTRICT\n)",
    'kind_cell_statistics_buckets': "CREATE TABLE kind_cell_statistics_buckets (\n    harness TEXT NOT NULL,\n    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,\n    effort TEXT NOT NULL,\n    domain_node_id TEXT NOT NULL,\n    task_type TEXT NOT NULL,\n    metric TEXT NOT NULL CHECK (metric IN ('delivery_seconds','input_tokens','cached_input_tokens','output_tokens')),\n    sign INTEGER NOT NULL CHECK (typeof(sign)='integer' AND sign IN (-1,0,1)),\n    bucket INTEGER NOT NULL CHECK (typeof(bucket)='integer'),\n    count INTEGER NOT NULL CHECK (typeof(count)='integer' AND count > 0),\n    CHECK (sign != 0 OR bucket = 0),\n    CHECK (sign != -1 OR metric = 'delivery_seconds'),\n    PRIMARY KEY (harness, model_id, effort, domain_node_id, task_type, metric, sign, bucket),\n    FOREIGN KEY (harness, model_id, effort, domain_node_id, task_type)\n        REFERENCES kind_cell_statistics(harness, model_id, effort, domain_node_id, task_type) ON DELETE RESTRICT\n)",
}

INDEX_DDL = {
    'attempts_router_binding_unique': 'CREATE UNIQUE INDEX attempts_router_binding_unique ON attempts(attempt_id, task_id, generation)',
    'attempts_router_outcome_binding_unique': 'CREATE UNIQUE INDEX attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id)',
    'model_mappings_service_idx': 'CREATE INDEX model_mappings_service_idx\n    ON model_mappings(harness, provider, model)',
    'attempts_effective_unique': "CREATE UNIQUE INDEX attempts_effective_unique ON attempts(task_id)\n    WHERE execution_state IN ('starting','executing','finalizing','uncertain')",
    'attempts_active_idx': 'CREATE INDEX attempts_active_idx ON attempts(execution_state)',
    'attempts_model_service_idx': 'CREATE INDEX attempts_model_service_idx\n    ON attempts(model_harness, model_provider, model_model) WHERE model_model IS NOT NULL',
    'kind_summary_evidence_run_idx': 'CREATE INDEX kind_summary_evidence_run_idx ON kind_summary_evidence(run_id)',
}

TRIGGER_DDL = {
    'model_mappings_no_update': "CREATE TRIGGER model_mappings_no_update BEFORE UPDATE ON model_mappings\nBEGIN SELECT RAISE(ABORT, 'model mappings are append-only'); END",
    'model_mappings_no_delete': "CREATE TRIGGER model_mappings_no_delete BEFORE DELETE ON model_mappings\nBEGIN SELECT RAISE(ABORT, 'model mappings are append-only'); END",
}

KIND_TABLES = (
    'kind_summaries', 'kind_summary_evidence', 'kind_statistics',
    'kind_cell_statistics', 'kind_statistics_buckets',
    'kind_cell_statistics_buckets',
)


def _rows(connection, table):
    return [dict(row) for row in connection.execute(
        f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]


def _insert_rows(connection, table, rows):
    columns = ['rowid', *(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))]
    placeholders = ','.join('?' for _ in columns)
    statement = f'INSERT INTO "{table}"({",".join(columns)}) VALUES({placeholders})'
    connection.executemany(statement, ([row.get(column) for column in columns] for row in rows))


def upgrade(connection):
    stock = {table: _rows(connection, table) for table in ('attempts', 'model_service_notes', *KIND_TABLES)}
    names = {row['model'] for row in stock['model_service_notes']}
    names.update(row['model'] for row in connection.execute('SELECT model FROM buddies'))
    names.update(row['model_model'] for row in stock['attempts'] if row['model_model'] is not None)
    for table in KIND_TABLES:
        names.update(row['model'] for row in stock[table])
    model_ids = {name: str(uuid4()) for name in sorted(names)}

    services = {(row['harness'], row['provider'], row['model'])
                for row in connection.execute('SELECT harness,provider,model FROM buddies')}
    services.update((row['model_harness'], row['model_provider'], row['model_model'])
                    for row in stock['attempts'] if row['model_model'] is not None)
    services.update((row['harness'], row['provider'], row['model'])
                    for row in stock['model_service_notes'])
    # These mappings are established by this upgrade; older execution and note
    # timestamps do not claim a mapping history that did not yet exist.
    mapped_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    # Drop children first. The installer has foreign key enforcement disabled
    # for the transaction and checks every reference before committing.
    for table in ('kind_summary_evidence', 'kind_summaries',
                  'kind_cell_statistics_buckets', 'kind_statistics_buckets',
                  'kind_cell_statistics', 'kind_statistics', 'attempts',
                  'model_service_notes'):
        connection.execute(f'DROP TABLE "{table}"')
    for table in TABLE_DDL:
        connection.execute(TABLE_DDL[table])
    for ddl in INDEX_DDL.values():
        connection.execute(ddl)
    for ddl in TRIGGER_DDL.values():
        connection.execute(ddl)

    connection.executemany('INSERT INTO models(model_id,display_name) VALUES(?,?)',
                           ((model_ids[name], name) for name in sorted(names)))
    connection.executemany(
        'INSERT INTO model_mappings(mapping_id,harness,provider,model,model_id,created_at,source)'
        " VALUES(?,?,?,?,?,?,'same-name')",
        ((str(uuid4()), harness, provider, name, model_ids[name],
          mapped_at)
         for harness, provider, name in sorted(services)),
    )

    notes = defaultdict(list)
    for row in stock['model_service_notes']:
        notes[row['model']].append(row)
    for name, rows in sorted(notes.items()):
        rows.sort(key=lambda row: (row['harness'], row['provider'], row['model']))
        texts = {row['text'] for row in rows}
        if len(texts) == 1:
            value = rows[0]['text']
        else:
            value = '\n\n'.join(
                f"[{row['harness']} / {row['provider']} / {row['model']}]\n{row['text']}"
                for row in rows)
        connection.execute('INSERT INTO model_notes(model_id,text,updated_at) VALUES(?,?,?)',
                           (model_ids[name], value, max(row['updated_at'] for row in rows)))

    for row in stock['attempts']:
        row['model_id'] = model_ids[row['model_model']] if row['model_model'] is not None else None
    _insert_rows(connection, 'attempts', stock['attempts'])
    for table in KIND_TABLES:
        for row in stock[table]:
            row['model_id'] = model_ids[row['model']]
        _insert_rows(connection, table, stock[table])
    connection.execute("UPDATE meta SET value='37' WHERE key='schema_version'")
