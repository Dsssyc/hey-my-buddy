"""The one pending, offline schema-19 to schema-20 board conversion."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from ...json_codec import canonical_json, decode_strict_json
from ...private_dirs import open_regular_fd
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


def _run(row: dict) -> dict:
    spec, _, _ = _spec(json.loads(row['micro_task_json']))
    row['micro_task_json'] = canonical_json(spec)
    row['micro_task_fingerprint'] = schemas.spec_fingerprint(spec)
    row['delegation_authority'] = 0
    return row


def _attempt(row: dict) -> dict:
    receipt = json.loads(row['result_json']) if row['result_json'] is not None else None
    old_stop = isinstance(receipt, dict) and receipt.get('terminationReason') == 'user-cancel'
    row['stop_initiator'] = 'unknown' if row['cancel_requested_at'] or old_stop else None
    row['grace_deadline'] = None
    if receipt is not None:
        converted = _receipt_object(receipt, service_receipt=True, adapter=row['adapter'])
        row['result_json'] = canonical_json(converted)
    return row


def _termination(value: dict, *, adapter: str | None = None) -> dict:
    reason = value.get('terminationReason')
    if reason == 'user-cancel':
        value['terminationReason'] = 'unknown-stop' if value.get('shutdownConfirmed') is True else None
        value['stopInitiator'] = 'unknown'
    elif reason == 'deadline' and adapter is not None and adapter != 'decision':
        # A historical Worker deadline was a task time limit, not a Router budget.
        value['terminationReason'] = None
    value.setdefault('stopInitiator', None)
    return value


def _receipt_object(value, member: str | None = None, *, service_receipt: bool = False,
                    adapter: str | None = None):
    """Update durable service task projections; leave native outcomes alone."""
    if isinstance(value, list):
        return [_receipt_object(item, service_receipt=service_receipt, adapter=adapter) for item in value]
    if not isinstance(value, dict):
        return value
    # Native model outcomes and original inputs are evidence. The result immediately
    # under an attempt view is a service receipt; its own result remains native.
    attempt_view = ('attemptId' in value and 'adapter' in value
                    and any(key in value for key in ('generation', 'executionState', 'cancelRequestedAt', 'result')))
    value = {key: (_receipt_object(item, key, service_receipt=True, adapter=value.get('adapter'))
                   if key == 'result' and attempt_view else
                   item if key in ('result', 'outcome', 'spec', 'input', 'context', 'provenance', 'report')
                   else _receipt_object(item, key)) for key, item in value.items()}
    task_view = ('taskId' in value or 'runId' in value) and 'adapter' in value and not attempt_view
    if task_view or service_receipt or 'terminationReason' in value:
        _termination(value, adapter=value.get('adapter', adapter))
    if attempt_view:
        receipt = value.get('result')
        value['stopInitiator'] = ('unknown' if value.get('cancelRequestedAt')
                                  or isinstance(receipt, dict) and receipt.get('stopInitiator') == 'unknown'
                                  else None)
        value['graceDeadline'] = None
    if task_view:
        # A task result may carry resultMeta without a selected attempt view.
        selected = value.get('selectedAttempt')
        meta = value.get('resultMeta')
        value['stopInitiator'] = ('unknown' if isinstance(selected, dict) and selected.get('stopInitiator') == 'unknown'
                                  or isinstance(meta, dict) and meta.get('stopInitiator') == 'unknown'
                                  or value.get('stopInitiator') == 'unknown' else None)
        value['graceDeadline'] = None
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


def _native_bindings(state: Path) -> list[tuple[Path, dict]]:
    """The base saved Codex and DSH bindings only for confirmed completed turns."""
    bindings = []
    for harness in ('codex', 'dsh'):
        for path in (state / 'harnesses' / harness / 'micro-tasks').glob('*/native/*.json'):
            if re.fullmatch(r'[0-9a-f]{64}\.json', path.name) is None:
                continue
            with os.fdopen(open_regular_fd(path, os.O_RDONLY), 'rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('Native binding exceeds its upgrade boundary')
            binding = decode_strict_json(raw)
            fields = {'taskId', 'cwd', 'buddy'} | ({'threadId', 'lastTurnId', 'lastAttemptId', 'lastInputSha256'}
                                                 if harness == 'codex' else {'sessionId'})
            session_field = 'threadId' if harness == 'codex' else 'sessionId'
            if (not isinstance(binding, dict) or set(binding) != fields
                    or not isinstance(binding['taskId'], str) or not isinstance(binding[session_field], str)
                    or path.name != hashlib.sha256(binding[session_field].encode()).hexdigest() + '.json'
                    or path.parents[1].name != hashlib.sha256(binding['taskId'].encode()).hexdigest()):
                raise ValueError('Native binding differs from the schema-19 base form')
            bindings.append((path, binding))
    return bindings


def _write_native_binding(path: Path, binding: dict) -> None:
    from ..service.daemon import atomic_json
    atomic_json(path, binding)


def migrate(state: Path) -> None:
    """Convert a stopped schema-19 board after a verified backup was published.

    The caller holds the daemon and board-owner locks. SQLite performs the
    complete replacement in one transaction; an exception leaves schema 19.
    """
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3', isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        replaced_bindings = []
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise ValueError('Expected the schema-19 upgrade base')
            _rebuild(connection, 'tasks', _task)
            _rebuild(connection, 'workflow_turns', lambda row: _turn(connection, row))
            _rebuild(connection, 'attempts', _attempt)
            _rebuild(connection, 'workflow_runs', _run)
            for row in connection.execute('SELECT decision_id,requested_json FROM decision_requests').fetchall():
                request = json.loads(row['requested_json'])
                old = request.pop('timeoutSeconds', None)
                if old is not None:
                    request['routerBudgetSeconds'] = old
                connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                   (canonical_json(request), row['decision_id']))
            for row in connection.execute("SELECT command_id,response_json FROM commands").fetchall():
                response = _receipt_object(json.loads(row['response_json']))
                connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                   (canonical_json(response), row['command_id']))
            for row in connection.execute("SELECT seq,payload_json FROM events WHERE payload_json LIKE '%terminationReason%'").fetchall():
                payload = _receipt_object(json.loads(row['payload_json']))
                connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                   (canonical_json(payload), row['seq']))
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
            for path, binding in _native_bindings(Path(state)):
                replaced_bindings.append((path, binding))
                _write_native_binding(path, {**binding, 'lastTurnStatus': 'completed'})
            connection.execute('COMMIT')
        except BaseException:
            try:
                for path, binding in reversed(replaced_bindings):
                    _write_native_binding(path, binding)
            finally:
                connection.execute('ROLLBACK')
            raise
