"""The one pending, offline schema-19 to schema-20 board conversion."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from ...json_codec import canonical_json
from ...protocol import schemas
from .db import SCHEMA, SCHEMA_VERSION


BASE_SCHEMA_VERSION = 19
DEFAULT_EXPECTED_SECONDS = 1800


def _spec(value: dict) -> tuple[dict, int | None, int | None]:
    value = dict(value)
    old = value.pop('timeoutSeconds')
    if value.get('adapter') == 'decision':
        decision = dict(value['decision'])
        decision['routerBudgetSeconds'] = decision.pop('timeoutSeconds')
        value['decision'] = decision
        value['routerBudgetSeconds'] = old
        return value, None, old
    expected = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    value['expectedSeconds'] = expected
    return value, expected, None


def _fresh_ddl(name: str) -> tuple[str, list[str]]:
    with closing(sqlite3.connect(':memory:')) as fresh:
        fresh.executescript(SCHEMA)
        ddl = fresh.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
        indexes = [row[0] for row in fresh.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name", (name,))]
    return ddl, indexes


def _rebuild(connection: sqlite3.Connection, name: str, transform) -> None:
    ddl, indexes = _fresh_ddl(name)
    staging = name + '_v20'
    connection.execute(re.sub(r'\b' + name + r'\b', staging, ddl, count=1))
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info({staging})')]
    quoted = ','.join('"' + column + '"' for column in columns)
    placeholders = ','.join('?' for _ in columns)
    for row in connection.execute(f'SELECT * FROM "{name}"'):
        data = transform(dict(row))
        connection.execute(f'INSERT INTO "{staging}"({quoted}) VALUES({placeholders})',
                           tuple(data[column] for column in columns))
    connection.execute(f'DROP TABLE "{name}"')
    connection.execute(ddl)
    connection.execute(f'INSERT INTO "{name}"({quoted}) SELECT {quoted} FROM "{staging}"')
    connection.execute(f'DROP TABLE "{staging}"')
    for index in indexes:
        connection.execute(index)


def _task(row: dict) -> dict:
    spec, expected, budget = _spec(json.loads(row['spec_json']))
    canonical, _, _ = _spec(json.loads(row['spec_canonical_json']))
    row['spec_json'] = canonical_json(spec)
    row['spec_canonical_json'] = canonical_json(canonical)
    row['input_fingerprint'] = schemas.spec_fingerprint(canonical)
    row['expected_seconds'] = expected
    row['router_budget_seconds'] = budget
    return row


def _turn(connection: sqlite3.Connection, row: dict) -> dict:
    old = connection.execute('SELECT expected_seconds FROM tasks WHERE task_id=?', (row['run_id'],)).fetchone()
    row['expected_seconds'] = old[0]
    return row


def _receipt_object(value, member: str | None = None):
    """Update durable service task projections; leave native outcomes alone."""
    if isinstance(value, list):
        return [_receipt_object(item) for item in value]
    if not isinstance(value, dict):
        return value
    # Model outcomes and original turn inputs are evidence, never service views.
    value = {key: item if key in ('result', 'outcome', 'spec', 'input', 'context', 'provenance')
             else _receipt_object(item, key) for key, item in value.items()}
    task_view = ('taskId' in value or 'runId' in value) and 'adapter' in value
    if task_view and value['adapter'] != 'decision':
        if value.get('terminationReason') == 'deadline':
            value['terminationReason'] = None
        for member in ('result', 'resultMeta'):
            receipt = value.get(member)
            if isinstance(receipt, dict) and receipt.get('terminationReason') == 'deadline':
                value[member] = {**receipt, 'terminationReason': None}
    if task_view and 'timeoutSeconds' in value:
        old = value.pop('timeoutSeconds')
        if value['adapter'] == 'decision':
            value['routerBudgetSeconds'] = old
        else:
            value['expectedSeconds'] = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    if (task_view and 'spec' in value
            and isinstance(value['spec'], dict) and 'timeoutSeconds' in value['spec']):
        value['spec'], _, _ = _spec(value['spec'])
        if 'inputFingerprint' in value:
            value['inputFingerprint'] = schemas.spec_fingerprint(value['spec'])
    if member == 'microTask' and 'timeoutSeconds' in value:
        old = value.pop('timeoutSeconds')
        value['expectedSeconds'] = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    return value


def migrate(state: Path) -> None:
    """Convert a stopped schema-19 board after a verified backup was published.

    The caller holds the daemon and board-owner locks. SQLite performs the
    complete replacement in one transaction; an exception leaves schema 19.
    """
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3', isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise ValueError('Expected the schema-19 upgrade base')
            _rebuild(connection, 'tasks', _task)
            _rebuild(connection, 'workflow_turns', lambda row: _turn(connection, row))
            # A historical Worker deadline cannot be relabelled as a Router budget.
            for row in connection.execute("SELECT attempt_id,result_json FROM attempts WHERE adapter!='decision' AND result_json IS NOT NULL").fetchall():
                receipt = json.loads(row['result_json'])
                if receipt.get('terminationReason') == 'deadline':
                    receipt['terminationReason'] = None
                    connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?',
                                       (canonical_json(receipt), row['attempt_id']))
            for row in connection.execute('SELECT run_id,micro_task_json FROM workflow_runs').fetchall():
                spec, _, _ = _spec(json.loads(row['micro_task_json']))
                connection.execute('UPDATE workflow_runs SET micro_task_json=?,micro_task_fingerprint=? WHERE run_id=?',
                                   (canonical_json(spec), schemas.spec_fingerprint(spec), row['run_id']))
            for row in connection.execute('SELECT decision_id,requested_json FROM decision_requests').fetchall():
                request = json.loads(row['requested_json'])
                old = request.pop('timeoutSeconds', None)
                if old is not None:
                    request['routerBudgetSeconds'] = old
                connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                   (canonical_json(request), row['decision_id']))
            for row in connection.execute("SELECT command_id,response_json FROM commands WHERE response_json LIKE '%timeoutSeconds%'").fetchall():
                response = _receipt_object(json.loads(row['response_json']))
                connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                   (canonical_json(response), row['command_id']))
            for row in connection.execute(
                "SELECT c.command_id,t.request_id,t.input_fingerprint FROM commands c"
                " JOIN tasks t ON t.task_id=c.task_id WHERE c.kind='task.submit'"
            ).fetchall():
                request = {'requestId': row['request_id'], 'fingerprint': row['input_fingerprint']}
                digest = hashlib.sha256(canonical_json(request).encode()).hexdigest()
                connection.execute('UPDATE commands SET request_hash=? WHERE command_id=?',
                                   (digest, row['command_id']))
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Foreign-key check failed after schema conversion')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Integrity check failed after schema conversion')
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
