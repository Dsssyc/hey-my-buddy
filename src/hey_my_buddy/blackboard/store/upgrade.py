"""The pending, offline schema-22 to schema-23 conversion."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from .db import SCHEMA, SCHEMA_VERSION

BASE_SCHEMA_VERSION = 22
_REMOVED_VIEW_KEYS = {'workerId', 'workerIdentity', 'workerInstance', 'leaseSeconds', 'leaseExpiresAt',
                      'claimRequestId'}


def _load(raw: str):
    try:
        return decode_strict_json(raw)
    except (ValueError, RecursionError) as error:
        raise BoardError('UPGRADE_INVALID_BOARD', f'Invalid retained JSON: {error}') from error


def _interruption_result(value):
    # Native/model results are opaque. Only this service-owned recovery payload
    # could carry the old interruption evidence; no other result is traversed.
    if (isinstance(value, dict) and value.get('status') == 'failed'
            and value.get('terminationReason') == 'interrupted'
            and 'shutdownConfirmed' in value):
        value = dict(value)
        result = value.get('result')
        if isinstance(result, dict) and isinstance(result.get('interruptionEvidence'), dict):
            evidence = result['interruptionEvidence']
            if evidence.get('kind') == 'worker-stopped':
                value['result'] = {**result, 'interruptionEvidence': {**evidence, 'kind': 'orphan-stopped'}}
    return value


def _view(value):
    """Convert only the named task/attempt projections in retained responses."""
    if not isinstance(value, dict):
        return value
    converted = dict(value)
    if {'attemptId', 'generation', 'executionState', 'ownership'} <= value.keys():
        for key in _REMOVED_VIEW_KEYS:
            converted.pop(key, None)
        identity = value.get('processIdentity')
        if isinstance(identity, dict):
            converted['processIdentity'] = {key: item for key, item in identity.items() if key != 'worker'}
        if 'result' in value:
            converted['result'] = _interruption_result(value['result'])
    if (({'taskId', 'requestId'} <= value.keys() or {'runId', 'requestId'} <= value.keys())
            and {'state', 'queueReason', 'interruptionEvidence'} & value.keys()):
        converted.pop('workerId', None)
        if value.get('queueReason') == 'awaiting-worker':
            converted['queueReason'] = 'awaiting-execution'
        evidence = value.get('interruptionEvidence')
        if isinstance(evidence, dict) and evidence.get('kind') == 'worker-stopped':
            converted['interruptionEvidence'] = {**evidence, 'kind': 'orphan-stopped'}
        # task_result unwraps the service recovery result onto a flat task view.
        # Its stopped/interrupted envelope still identifies the owned result;
        # arbitrary model and command output remains opaque.
        converted = _interruption_result(converted)
    for key in ('task', 'attempt', 'selectedAttempt', 'workflow'):
        if isinstance(value.get(key), dict):
            converted[key] = _view(value[key])
    for key in ('tasks', 'attempts', 'queuedTasks', 'runs', 'children', 'results', 'ownedMicroTasks'):
        if isinstance(value.get(key), list):
            converted[key] = [_view(item) for item in value[key]]
    return converted


def _event(kind: str, value):
    if not isinstance(value, dict):
        return value
    if kind.startswith(('task.', 'attempt.', 'workflow.', 'macro_task.')):
        value = _view(value)
    if kind in ('task.submitted', 'task.retried') and value.get('queueReason') == 'awaiting-worker':
        value = {**value, 'queueReason': 'awaiting-execution'}
    if kind == 'task.failed' and isinstance(value.get('interruptionEvidence'), dict):
        evidence = value['interruptionEvidence']
        if evidence.get('kind') == 'worker-stopped':
            value = {**value, 'interruptionEvidence': {**evidence, 'kind': 'orphan-stopped'}}
    if kind == 'attempt.interruption_seal_pending' and value.get('evidence') == 'worker-stopped':
        value = {**value, 'evidence': 'orphan-stopped'}
    if kind == 'attempt.released' and value.get('releaseEvidence') == 'spawnIntentWritten=false':
        value = {**value, 'releaseEvidence': 'processCreated=false'}
    return value


def upgrade_database(directory: Path) -> None:
    """Convert a backed-up, idle board without exposing a mixed stored form."""
    with closing(sqlite3.connect(Path(directory) / 'board.sqlite3', isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        # Referencing tables keep their names and rows while the three replaced
        # tables are rebuilt from the exact fresh-board DDL in one transaction.
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise BoardError('UNSUPPORTED_SCHEMA', f'Expected schema {BASE_SCHEMA_VERSION} for the pending upgrade')
            attempts = [dict(row) for row in connection.execute('SELECT * FROM attempts')]
            identities = {}
            for row in connection.execute('SELECT * FROM attempt_process_identities ORDER BY ordinal'):
                identities[row['attempt_id']] = dict(row)
            interruptions = [dict(row) for row in connection.execute('SELECT * FROM attempt_interruptions')]
            for table in ('attempt_process_identities', 'attempt_interruptions', 'attempts', 'workers'):
                connection.execute(f'DROP TABLE {table}')
            for statement in SCHEMA.split(';'):
                sql = statement.strip()
                if any(f'CREATE TABLE IF NOT EXISTS {table} (' in sql for table in
                       ('attempts', 'attempt_process_identities', 'attempt_interruptions',
                        'attempt_shutdown_evidence', 'execution_issues', 'service_issues')) or (
                        'CREATE ' in sql and 'INDEX IF NOT EXISTS attempts_' in sql):
                    connection.execute(sql)
            for table, rows in (('attempts', attempts), ('attempt_process_identities', identities.values()),
                                ('attempt_interruptions', interruptions)):
                columns = [row['name'] for row in connection.execute(f'PRAGMA table_info({table})')]
                placeholders = ','.join('?' for _ in columns)
                for row in rows:
                    values = []
                    for column in columns:
                        value = row.get(column)
                        if column == 'cleanup_pending':
                            value = 0  # The idle schema-22 board has no execution material to reap.
                        if column == 'evidence_kind' and value == 'worker-stopped':
                            value = 'orphan-stopped'
                        if column == 'result_json' and value is not None:
                            original = _load(value)
                            converted = _interruption_result(original)
                            value = value if converted == original else canonical_json(converted)
                        values.append(value)
                    connection.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", values)
            # These receipts authorize retired calls or contain their old subject.
            connection.execute("UPDATE attempts SET result_command_id=NULL WHERE result_command_id IN "
                               "(SELECT command_id FROM commands WHERE kind LIKE 'worker.%' OR kind='harness.prepare')")
            connection.execute("DELETE FROM commands WHERE kind LIKE 'worker.%' OR kind='harness.prepare'")
            for row in connection.execute('SELECT command_id,response_json FROM commands').fetchall():
                value = _load(row['response_json'])
                migrated = _view(value)
                converted = row['response_json'] if migrated == value else canonical_json(migrated)
                if converted != row['response_json']:
                    connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                       (converted, row['command_id']))
            # Historical event kinds no longer produced keep their original
            # payload; process/evidence payloads still read by current consumers
            # are converted in stock along with new writes.
            for row in connection.execute('SELECT seq,kind,payload_json FROM events').fetchall():
                value = _load(row['payload_json'])
                converted = _event(row['kind'], value)
                if converted != value:
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                       (canonical_json(converted), row['seq']))
            connection.execute("UPDATE tasks SET queue_reason='awaiting-execution' WHERE queue_reason='awaiting-worker'")
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Foreign keys failed after schema conversion')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BoardError('UPGRADE_INVALID_BOARD', 'SQLite integrity failed after schema conversion')
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
        finally:
            connection.execute('PRAGMA foreign_keys=ON')
