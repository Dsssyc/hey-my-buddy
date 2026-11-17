"""Schema 37 → 38: replace Router budgets and permit the latest correspondence undo.

This frozen step owns its trigger definitions and conversion; it imports no
current schema or product code. Updates stay forbidden;
deletion is open only to a service's latest row when it is not the only one.
"""
from __future__ import annotations


TRIGGER_DDL = {
    'model_correspondences_delete_latest_only': (
        "CREATE TRIGGER model_correspondences_delete_latest_only BEFORE DELETE ON model_correspondences\n"
        "WHEN NOT EXISTS(SELECT 1 FROM model_correspondences h WHERE h.harness=OLD.harness AND h.provider=OLD.provider AND h.model=OLD.model AND h.rowid<OLD.rowid)\n"
        "    OR EXISTS(SELECT 1 FROM model_correspondences h WHERE h.harness=OLD.harness AND h.provider=OLD.provider AND h.model=OLD.model AND h.rowid>OLD.rowid)\n"
        "BEGIN SELECT RAISE(ABORT, 'model correspondences only delete the latest change of a service'); END"),
}

DEFAULT_MODEL_RESPONSE_WAIT_SECONDS = 60
MAX_MODEL_RESPONSE_WAIT_SECONDS = 86400

ROUTER_SETTINGS_DDL = f"""CREATE TABLE router_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    default_mode TEXT NOT NULL CHECK (default_mode IN ('fast','review')),
    model_response_wait_seconds INTEGER NOT NULL DEFAULT {DEFAULT_MODEL_RESPONSE_WAIT_SECONDS} CHECK (typeof(model_response_wait_seconds)='integer' AND model_response_wait_seconds BETWEEN 1 AND {MAX_MODEL_RESPONSE_WAIT_SECONDS}),
    retry_interval_seconds INTEGER NOT NULL CHECK (retry_interval_seconds >= 1)
, maintenance_buddy_id TEXT, maintenance_interval_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (maintenance_interval_seconds >= 1)
, exploration_every INTEGER DEFAULT 5 CHECK (exploration_every IS NULL OR exploration_every >= 2)
, profile_buddy_id TEXT REFERENCES buddies(buddy_id) ON DELETE RESTRICT);"""


def upgrade(connection):
    settings = connection.execute(
        'SELECT rowid,id,default_mode,retry_interval_seconds,maintenance_buddy_id,'
        'maintenance_interval_seconds,exploration_every,profile_buddy_id FROM router_settings'
    ).fetchall()
    connection.execute('DROP TABLE router_settings')
    connection.execute(ROUTER_SETTINGS_DDL)
    connection.executemany(
        'INSERT INTO router_settings(rowid,id,default_mode,retry_interval_seconds,'
        'maintenance_buddy_id,maintenance_interval_seconds,exploration_every,profile_buddy_id)'
        ' VALUES(?,?,?,?,?,?,?,?)',
        settings,
    )
    connection.execute('DROP TRIGGER model_correspondences_no_delete')
    for ddl in TRIGGER_DDL.values():
        connection.execute(ddl)
    connection.execute("UPDATE meta SET value='38' WHERE key='schema_version'")
