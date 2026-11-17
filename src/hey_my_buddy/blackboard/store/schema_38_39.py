"""Schema 38 → 39: remove model notes and state correspondence immutability.

This step owns its schema-38 reference list and replacement trigger. A model
kept alive only by a removed note has no remaining fact to describe.
"""
from __future__ import annotations


MODEL_REFERENCES = (
    'attempts', 'model_correspondences', 'profile_runs', 'model_profiles',
    'kind_summaries', 'kind_summary_evidence', 'kind_statistics',
    'kind_cell_statistics', 'kind_statistics_buckets', 'kind_cell_statistics_buckets',
)

NO_UPDATE_TRIGGER_DDL = """CREATE TRIGGER model_correspondences_no_update BEFORE UPDATE ON model_correspondences
BEGIN SELECT RAISE(ABORT, 'model correspondence rows cannot be updated; undo deletes the latest change'); END"""


def upgrade(connection):
    model_ids = [row[0] for row in connection.execute('SELECT model_id FROM model_notes')]
    connection.execute('DROP TABLE model_notes')
    for model_id in model_ids:
        if all(connection.execute(f'SELECT 1 FROM {table} WHERE model_id=? LIMIT 1',
                                  (model_id,)).fetchone() is None
               for table in MODEL_REFERENCES):
            connection.execute('DELETE FROM models WHERE model_id=?', (model_id,))
    connection.execute('DROP TRIGGER model_correspondences_no_update')
    connection.execute(NO_UPDATE_TRIGGER_DDL)
    connection.execute("UPDATE meta SET value='39' WHERE key='schema_version'")
