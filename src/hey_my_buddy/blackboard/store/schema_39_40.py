"""Schema 39 → 40: replace the derived buddy label with a user alias.

Any retained catalog payload can prove that a description was assembled from
an empty native description for the same execution identity. Sealed payloads
and prior routing inputs remain untouched.
"""
from __future__ import annotations

import json


BUDDIES_DDL = """
CREATE TABLE IF NOT EXISTS buddies (
    buddy_id          TEXT PRIMARY KEY,
    alias               TEXT,
    harness             TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    effort              TEXT NOT NULL,
    available           INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    context_window      INTEGER,
    description         TEXT NOT NULL DEFAULT '',
    source              TEXT NOT NULL DEFAULT '',
    unavailable_code        TEXT,
    unavailable_params_json TEXT,
    unavailable_content     TEXT,
    CHECK ((unavailable_code IS NULL) = (unavailable_params_json IS NULL)),
    CHECK (unavailable_content IS NULL OR unavailable_code IS 'catalog.native-unavailable')
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS buddies_current_idx ON buddies(available, buddy_id)",
    "CREATE INDEX IF NOT EXISTS buddies_harness_idx ON buddies(harness, buddy_id)",
)


def _assembled_descriptions(connection):
    """Return descriptions backed by any recorded discovery payload."""
    rows = connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog')
    proven = set()
    for _discovery_id, payload_json in rows:
        payload = json.loads(payload_json)
        for provider in payload['providers']:
            for model in provider['models']:
                if model['description']:
                    continue
                synthetic = f"{model['name']} via {provider['displayName']}"[:4000]
                for effort in model['efforts']:
                    proven.add((provider['harness'], provider['provider'], model['id'],
                                effort, synthetic))
    return proven


def upgrade(connection):
    proven = _assembled_descriptions(connection)
    # Keep source rowids: frozen records can name a buddy, but this table's
    # physical rows also remain stable for local inspection across an install.
    connection.execute('CREATE TEMP TABLE buddy_39_stock AS SELECT rowid AS old_rowid,* FROM buddies')
    connection.execute('DROP TABLE buddies')
    connection.execute(BUDDIES_DDL)
    for sql in INDEXES:
        connection.execute(sql)
    connection.execute('''INSERT INTO buddies(
        rowid,buddy_id,alias,harness,provider,model,effort,available,enabled,
        capabilities_json,context_window,description,source,unavailable_code,
        unavailable_params_json,unavailable_content)
        SELECT old_rowid,buddy_id,NULL,harness,provider,model,effort,available,enabled,
               capabilities_json,context_window,description,source,unavailable_code,
               unavailable_params_json,unavailable_content FROM buddy_39_stock''')
    connection.execute('DROP TABLE buddy_39_stock')
    for row in connection.execute('SELECT buddy_id,harness,provider,model,effort,description FROM buddies'):
        if tuple(row[1:]) in proven:
            connection.execute('UPDATE buddies SET description=? WHERE buddy_id=?', ('', row[0]))
    connection.execute("UPDATE meta SET value='40' WHERE key='schema_version'")
