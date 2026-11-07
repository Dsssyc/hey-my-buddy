"""Schema 37 → 38: undo deletes a service's latest correspondence change.

This frozen step owns its trigger definitions and conversion; it imports no
current schema or product code and converts no rows. Updates stay forbidden;
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


def upgrade(connection):
    connection.execute('DROP TRIGGER model_correspondences_no_delete')
    for ddl in TRIGGER_DDL.values():
        connection.execute(ddl)
    connection.execute("UPDATE meta SET value='38' WHERE key='schema_version'")
