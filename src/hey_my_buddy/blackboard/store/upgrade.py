"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import (DB_FILE, EVENTS_ROUTER_CLAIMED_INDEX_DDL, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA,
                 WORKFLOW_CHILDREN_DDL, WORKFLOW_RUNS_DDL)
from ... import private_dirs
from ...errors import BoardError

# Task and attempt identity already live in the event columns.
ACTIVITY_EVENT_PAYLOAD = '{}'
EVENT_COLUMNS = ('seq', 'task_id', 'attempt_id', 'revision', 'kind', 'payload_json',
                 'actor_role', 'actor_id', 'created_at')

# Only the common columns cross the pending upgrade's retention comparison.
RETAINED_COLUMN_PROJECTIONS = {
    'workflow_runs': (
        'run_id', 'macro_task_id', 'title', 'activity_seq', 'activity_at', 'host_id',
        'owner_generation', 'control_verifier', 'request_fingerprint', 'buddy_json',
        'buddy_revision', 'validated_buddy_revision', 'current_routing_id', 'submission_verifier',
        'execution_workspace_json', 'workspace_manifest_json', 'state', 'active_request_id',
        'current_turn_id', 'current_attempt_id', 'continuation_count', 'final_artifact_id',
        'final_attempt_id', 'revision', 'created_at', 'updated_at', 'buddy_locked', 'delegation_authority',
    ),
    'workflow_children': (
        'child_task_id', 'parent_run_id', 'request_id', 'decision_command_id', 'role', 'state',
        'workspace_manifest_json', 'created_at', 'updated_at',
    ),
}

# This is the schema-28 reader's inference, consumed once rather than on reads.
SOURCE_HOST_SQL = """
COALESCE(
    (SELECT CASE event.kind
                WHEN 'task.submitted' THEN json_extract(event.payload_json, '$.microTask.sourceHostId')
                ELSE json_extract(event.payload_json, '$.sourceHostId')
            END
       FROM events event
      WHERE event.task_id = source.run_id AND (
          (event.kind = 'task.submitted'
           AND json_type(event.payload_json, '$.microTask.sourceHostId') = 'text'
           AND length(json_extract(event.payload_json, '$.microTask.sourceHostId')) > 0)
          OR (event.kind = 'workflow.helper_admitted'
              AND json_type(event.payload_json, '$.sourceHostId') = 'text'
              AND length(json_extract(event.payload_json, '$.sourceHostId')) > 0)
      ) ORDER BY event.seq LIMIT 1),
    (SELECT json_extract(event.payload_json, '$.previousHostId') FROM events event
      WHERE event.task_id = source.run_id AND event.kind = 'workflow.takeover'
        AND json_type(event.payload_json, '$.previousOwnerGeneration') = 'integer'
        AND json_extract(event.payload_json, '$.previousOwnerGeneration') = 1
        AND json_type(event.payload_json, '$.previousHostId') = 'text'
        AND length(json_extract(event.payload_json, '$.previousHostId')) > 0
      ORDER BY event.seq LIMIT 1),
    CASE WHEN source.owner_generation = 1 THEN source.host_id END
)
"""


# These retired entries belong to the schema-28 stock, not to future installs.
LEGACY_LAYOUT = (
    ('harnesses/codex/goals', True),
    ('harnesses/claude/goals', True),
    ('harnesses/zcode/goals', True),
    ('harnesses/dsh/goals', True),
    ('decisions', True),
    ('legacy-worker-cleanup.lock', False),
    ('worker.log', False),
)


def cleanup_legacy_layout(state: Path) -> dict:
    """Remove retired private entries after the pending conversion is verified."""
    state = private_dirs._absolute(state)
    removed, failures = [], []
    for relative, directory in LEGACY_LAYOUT:
        path = state / relative
        try:
            # Check parents from state outward before the private remover can
            # reach a linked harness directory or the target of any link.
            parts = Path(relative).parts
            for depth in range(len(parts) + 1):
                parent = state.joinpath(*parts[:depth])
                if private_dirs.linked(parent):
                    raise BoardError('PRIVATE_PATH_UNSAFE', 'Legacy cleanup path contains a link or reparse point',
                                     path=str(parent))
            if directory:
                deleted = private_dirs.remove_tree(path)
            else:
                deleted = path.exists()
                path.unlink(missing_ok=True)
            if deleted:
                removed.append(relative)
        except (BoardError, OSError) as error:
            failures.append({'path': relative,
                             'code': error.code if isinstance(error, BoardError) else type(error).__name__,
                             'message': str(error)})
    return {'complete': not failures, 'removed': removed, 'failures': failures}


def _rebuild_workflow_tables(connection) -> None:
    for name, ddl in (('workflow_runs', WORKFLOW_RUNS_DDL), ('workflow_children', WORKFLOW_CHILDREN_DDL)):
        previous = name + '_upgrade'
        indexes = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index'"
                                     " AND tbl_name=? AND sql IS NOT NULL", (name,)).fetchall()
        connection.execute(f'ALTER TABLE {name} RENAME TO {previous}')
        for index_name, _ in indexes:
            connection.execute(f'DROP INDEX {index_name}')
        connection.execute(ddl)
        columns = ','.join(RETAINED_COLUMN_PROJECTIONS[name])
        added_column, added_value = ((',source_host_id', ',' + SOURCE_HOST_SQL)
                                     if name == 'workflow_runs' else ('', ''))
        connection.execute(f'INSERT INTO {name}(rowid,{columns}{added_column})'
                           f' SELECT rowid,{columns}{added_value} FROM {previous} source')
        connection.execute(f'DROP TABLE {previous}')
        for _, index_sql in indexes:
            connection.execute(index_sql)


def expected_events_fingerprint(connection, *, event_head: int) -> str:
    """Hash upgraded event rows in the same bounded sorted format as the backup."""
    def encode(*values):
        row = list(values)
        if row[4] == 'attempt.activity':
            row[5] = ACTIVITY_EVENT_PAYLOAD
        return json.dumps(row, ensure_ascii=False, separators=(',', ':'))

    connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), encode)
    try:
        cursor = connection.execute('SELECT hmb_upgrade_event_row('
                                    + ','.join(EVENT_COLUMNS)
                                    + ') AS encoded FROM events WHERE seq<=? ORDER BY encoded COLLATE BINARY',
                                    (event_head,))
        digest = hashlib.sha256()
        separator = b''
        for row in cursor:
            digest.update(separator)
            digest.update(row[0].encode())
            separator = b'\n'
        return digest.hexdigest()
    finally:
        connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), None)


def migrate_schema(state: Path) -> None:
    """Consume the single pending schema-28 upgrade after a verified backup."""
    with closing(sqlite3.connect(state / DB_FILE)) as connection:
        # SQLite's parent-table rebuild requires foreign keys disabled before
        # BEGIN; the complete foreign_key_check below gates the atomic commit.
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('PRAGMA legacy_alter_table=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Board schema cannot be upgraded')
            if version[0] == str(UPGRADE_BASE_SCHEMA):
                _rebuild_workflow_tables(connection)
                connection.execute("UPDATE events SET payload_json=? WHERE kind='attempt.activity'",
                                   (ACTIVITY_EVENT_PAYLOAD,))
                connection.execute('DROP INDEX events_router_claimed_idx')
                connection.execute(EVENTS_ROUTER_CLAIMED_INDEX_DDL)
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Migrated board integrity or foreign key checks failed')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
