"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import (DB_FILE, DECISION_REQUESTS_DDL, EVALUATION_DECISIONS_DDL,
                 EVENTS_ROUTER_CLAIMED_INDEX_DDL, MODEL_SERVICE_CONCURRENCY_DDL,
                 ROUTER_DISPATCHES_DDL, ROUTER_OUTCOMES_DDL, ROUTER_REQUESTS_DDL,
                 SCHEMA_VERSION, UPGRADE_BASE_SCHEMA, WORKFLOW_CHILDREN_DDL,
                 WORKFLOW_RUNS_DDL, canonical_json)
from ... import private_dirs
from ...errors import BoardError

# Task and attempt identity already live in the event columns.
ACTIVITY_EVENT_PAYLOAD = '{}'
EVENT_COLUMNS = ('seq', 'task_id', 'attempt_id', 'revision', 'kind', 'payload_json',
                 'actor_role', 'actor_id', 'created_at')

# Only the common columns cross the pending upgrade's retention comparison.
RETAINED_COLUMN_PROJECTIONS = {
    'evaluation_decisions': ('decision_id', 'status', 'buddy_id', 'reason_code',
                             'reason_params_json', 'note', 'created_at'),
    'decision_requests': ('decision_id', 'request_id', 'input_fingerprint', 'task_id',
                          'attempt_id', 'generation', 'selected_json', 'output_json',
                          'requested_json', 'updated_at'),
    'router_outcomes': ('receipt_id', 'request_id', 'router_index', 'buddy_id', 'phase',
                        'task_id', 'attempt_id', 'outcome', 'code', 'event_seq'),
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

_ROUTER_FACT_FIELDS = ('routerBuddyIds', 'routerIdentities', 'routingMode', 'routingBudget',
                      'routerRetryIntervalSeconds', 'budget')
_ROUTER_IDENTITY_FIELDS = ('harness', 'provider', 'model', 'effort')
_ROUTER_COLUMNS = {
    'router_requests': ('decision_id', 'round', 'snapshot_json', 'frozen_at'),
    'router_dispatches': ('task_id', 'decision_id', 'round', 'router_index', 'buddy_id',
                          *_ROUTER_IDENTITY_FIELDS, 'created_at'),
}


def _historical_router_document(encoded: str) -> dict:
    """Apply the pre-0.46 excluded-buddy conversion already applied to snapshots."""
    document = json.loads(encoded)
    for entry in document['routingBasis']['excludedBuddies']:
        if 'reason' in entry:
            entry['note'] = entry.pop('reason') or None
    return document


@contextmanager
def _router_upgrade_stock(connection):
    """Project the old writer's initial and single-retry packets into round rows.

    Original snapshots keep their rowids. The old writer omitted snapshots when
    zero or one candidates needed no Router; their task-only snapshots are made
    from the retained request text, checked against the task column being retired.
    The old task request key distinguishes
    retry dispatches; their actor-free document and retry inspections recover the
    second snapshot. Temporary tables let SQLite sort fingerprints without
    retaining all Router inputs in Python.
    """
    connection.execute('CREATE TEMP TABLE hmb_upgrade_router_requests AS'
                       ' SELECT rowid AS original_rowid,decision_id,1 AS round,snapshot_json,frozen_at'
                       ' FROM router_requests')
    connection.execute("CREATE TEMP TABLE hmb_upgrade_router_dispatches AS"
                       " SELECT d.rowid AS original_rowid,d.task_id,d.decision_id,"
                       " CASE WHEN t.request_id='decision:'||r.request_id||':1:'||d.router_index"
                       " THEN 2 ELSE 1 END AS round,d.router_index,d.buddy_id,"
                       " d.harness,d.provider,d.model,d.effort,d.created_at"
                       " FROM router_dispatches d JOIN tasks t ON t.task_id=d.task_id"
                       " JOIN decision_requests r ON r.decision_id=d.decision_id")
    try:
        next_rowid = connection.execute('SELECT COALESCE(MAX(rowid),0)+1 FROM router_requests').fetchone()[0]
        for decision_id, encoded, created_at in connection.execute(
            'SELECT r.decision_id,r.requested_json,d.created_at FROM decision_requests r'
            ' JOIN evaluation_decisions d ON d.decision_id=r.decision_id'
            ' LEFT JOIN router_requests s ON s.decision_id=r.decision_id'
            ' WHERE s.decision_id IS NULL ORDER BY r.rowid'):
            request = json.loads(encoded)
            count = request['routingBasis']['candidateCount']
            if request.get('selectionRetry') or count > 1 or connection.execute(
                    'SELECT 1 FROM router_dispatches WHERE decision_id=? LIMIT 1', (decision_id,)).fetchone():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Decision has no Router snapshot', decisionId=decision_id)
            facts = {'routerBuddyId': None, 'routingMode': None, 'budget': None}
            if count == 1:
                facts['routerCalled'] = False
            snapshot = {'baseInput': {'task': request['task']}, 'facts': facts, 'inspections': []}
            connection.execute('INSERT INTO hmb_upgrade_router_requests VALUES(?,?,?,?,?)',
                               (next_rowid, decision_id, 1, canonical_json(snapshot), created_at))
            next_rowid += 1
        for row in connection.execute('SELECT decision_id,requested_json,input_json'
                                      ' FROM decision_requests ORDER BY rowid'):
            decision_id, encoded_request, encoded_input = row
            request = json.loads(encoded_request)
            if not request.get('selectionRetry'):
                continue
            if request['routingBasis']['candidateCount'] <= 1:
                snapshot = {'baseInput': {'task': request['task']}, 'facts': {'routerCalled': False}, 'inspections': []}
            else:
                dispatch = connection.execute(
                    'SELECT d.document_json FROM router_dispatches d JOIN hmb_upgrade_router_dispatches s'
                    ' ON s.task_id=d.task_id WHERE s.decision_id=? AND s.round=2 ORDER BY d.rowid LIMIT 1',
                    (decision_id,)).fetchone()
                if dispatch is None and encoded_input is None:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Selection retry has no retained Router packet',
                                     decisionId=decision_id)
                base_input = _historical_router_document(dispatch[0] if dispatch is not None else encoded_input)
                for field in ('buddy', 'routerBuddyId', 'routerBuddy', 'routerIndex'):
                    base_input.pop(field, None)
                snapshot = {'baseInput': base_input,
                            'facts': {field: base_input[field] for field in _ROUTER_FACT_FIELDS},
                            'inspections': request['selectionRetryInspections']}
            frozen_at = connection.execute(
                "SELECT created_at FROM events WHERE kind='decision.selection_rerouted'"
                " AND json_extract(payload_json,'$.decisionId')=?", (decision_id,)).fetchone()[0]
            connection.execute('INSERT INTO hmb_upgrade_router_requests VALUES(?,?,?,?,?)',
                               (next_rowid, decision_id, 2, canonical_json(snapshot), frozen_at))
            next_rowid += 1
        yield
    finally:
        connection.execute('DROP TABLE hmb_upgrade_router_dispatches')
        connection.execute('DROP TABLE hmb_upgrade_router_requests')


def _validate_router_stock(connection) -> None:
    missing = connection.execute(
        'SELECT d.decision_id FROM evaluation_decisions d LEFT JOIN hmb_upgrade_router_requests r'
        ' ON r.decision_id=d.decision_id WHERE r.decision_id IS NULL LIMIT 1').fetchone()
    if missing is not None:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Decision has no Router snapshot', decisionId=missing[0])
    # The only redundant task copy is checked before evaluation_decisions is rebuilt.
    for decision_id, task, snapshot in connection.execute(
        'SELECT d.decision_id,d.task,s.snapshot_json FROM evaluation_decisions d'
        ' JOIN hmb_upgrade_router_requests s ON s.decision_id=d.decision_id'
        ' WHERE s.round=(SELECT MAX(current.round) FROM hmb_upgrade_router_requests current'
        ' WHERE current.decision_id=d.decision_id)'):
        if task != json.loads(snapshot)['baseInput']['task']:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Decision task differs from its Router snapshot',
                             decisionId=decision_id)
    for row in connection.execute(
        'SELECT d.task_id,d.document_json,s.snapshot_json,d.buddy_id,d.harness,d.provider,d.model,d.effort,'
        ' d.router_index FROM router_dispatches d JOIN hmb_upgrade_router_dispatches projected'
        ' ON projected.task_id=d.task_id JOIN hmb_upgrade_router_requests s'
        ' ON s.decision_id=projected.decision_id AND s.round=projected.round'):
        task_id, encoded, snapshot, buddy_id, *identity, router_index = row
        buddy = dict(zip(_ROUTER_IDENTITY_FIELDS, identity))
        expected = {**json.loads(snapshot)['baseInput'], 'buddy': buddy,
                    'routerBuddyId': buddy_id, 'routerBuddy': buddy, 'routerIndex': router_index}
        if _historical_router_document(encoded) != expected:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Router dispatch differs from its round snapshot',
                             taskId=task_id)
    # input_json held either a retry's actor-free packet or its current dispatch.
    for decision_id, encoded, snapshot, document in connection.execute(
        'SELECT r.decision_id,r.input_json,s.snapshot_json,d.document_json FROM decision_requests r'
        ' JOIN hmb_upgrade_router_requests s ON s.decision_id=r.decision_id'
        ' LEFT JOIN router_dispatches d ON d.task_id=r.task_id'
        ' WHERE r.input_json IS NOT NULL AND s.round=(SELECT MAX(current.round)'
        ' FROM hmb_upgrade_router_requests current WHERE current.decision_id=r.decision_id)'):
        actual = _historical_router_document(encoded)
        if actual != json.loads(snapshot)['baseInput'] and (
                document is None or actual != _historical_router_document(document)):
            raise BoardError('UPGRADE_INVALID_BOARD', 'Decision input differs from its retained Router packet',
                             decisionId=decision_id)
    if connection.execute("SELECT 1 FROM router_outcomes WHERE plane!='routing' LIMIT 1").fetchone():
        raise BoardError('UPGRADE_INVALID_BOARD', 'Router outcome is outside the routing plane')


def _rebuild_table(connection, name: str, ddl: str, *, projected: bool = False) -> None:
    previous = name + '_upgrade'
    indexes = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index'"
                                 " AND tbl_name=? AND sql IS NOT NULL", (name,)).fetchall()
    connection.execute(f'ALTER TABLE {name} RENAME TO {previous}')
    for index_name, _ in indexes:
        connection.execute(f'DROP INDEX {index_name}')
    connection.execute(ddl)
    columns = ','.join(row[1] for row in connection.execute(f'PRAGMA table_info({name})'))
    source, rowid = ('hmb_upgrade_' + name, 'original_rowid') if projected else (previous, 'rowid')
    connection.execute(f'INSERT INTO {name}(rowid,{columns}) SELECT {rowid},{columns} FROM {source}')
    connection.execute(f'DROP TABLE {previous}')
    for _, index_sql in indexes:
        connection.execute(index_sql)


def expected_router_fingerprints(connection) -> tuple[dict[str, str], dict[str, int]]:
    """Hash the projected round rows with the backup's sorted JSON row format."""
    fingerprints, counts = {}, {}
    with _router_upgrade_stock(connection):
        connection.create_function('hmb_upgrade_router_row', -1,
                                   lambda *values: json.dumps(values, ensure_ascii=False, separators=(',', ':')))
        try:
            for name, columns in _ROUTER_COLUMNS.items():
                projected = 'hmb_upgrade_' + name
                counts[name] = connection.execute(f'SELECT COUNT(*) FROM {projected}').fetchone()[0]
                cursor = connection.execute('SELECT hmb_upgrade_router_row(' + ','.join(columns)
                                            + f') AS encoded FROM {projected} ORDER BY encoded COLLATE BINARY')
                digest, separator = hashlib.sha256(), b''
                for row in cursor:
                    digest.update(separator)
                    digest.update(row[0].encode())
                    separator = b'\n'
                fingerprints[name] = digest.hexdigest()
        finally:
            connection.create_function('hmb_upgrade_router_row', -1, None)
    return fingerprints, counts

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
                with _router_upgrade_stock(connection):
                    _validate_router_stock(connection)
                    _rebuild_table(connection, 'router_requests', ROUTER_REQUESTS_DDL, projected=True)
                    _rebuild_table(connection, 'router_dispatches', ROUTER_DISPATCHES_DDL, projected=True)
                    _rebuild_table(connection, 'evaluation_decisions', EVALUATION_DECISIONS_DDL)
                    _rebuild_table(connection, 'decision_requests', DECISION_REQUESTS_DDL)
                    _rebuild_table(connection, 'router_outcomes', ROUTER_OUTCOMES_DDL)
                    _rebuild_table(connection, 'model_service_concurrency', MODEL_SERVICE_CONCURRENCY_DDL)
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
