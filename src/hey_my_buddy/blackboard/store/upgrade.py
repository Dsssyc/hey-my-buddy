"""One pending, offline schema-20 to schema-21 board upgrade."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from ...errors import BoardError
from .db import SCHEMA, SCHEMA_VERSION, canonical_json


BASE_SCHEMA_VERSION = 20
_REBUILT = (
    'events', 'workflow_runs', 'workflow_scope_versions', 'workflow_workspace_conflicts',
    'workflow_integrations', 'workspace_cleanup_plans', 'workflow_host_conclusions',
)
_ACTOR_TABLES = frozenset(_REBUILT) - {'workflow_runs', 'events'}


def _actor(value: str, lead_id: str | None = None, *, prior_lead: bool = False) -> tuple[str, str]:
    if lead_id is not None and value == 'lead:' + lead_id:
        return 'lead', lead_id
    if prior_lead and lead_id is not None and value == 'host:lead:' + lead_id:
        return 'lead', lead_id
    if value.startswith('host:'):
        return 'host', value[5:]
    if value == 'service-stop':
        return 'service', 'stop'
    if value == 'service':
        return 'service', 'service'
    if ':' in value:
        role, identifier = value.split(':', 1)
        if role in {'console', 'workflow', 'bridge', 'worker', 'agent', 'service'}:
            return role, identifier
    return 'host', value


def _remove_legacy_evidence(container: dict) -> bool:
    evidence = container.get('interruptionEvidence')
    if isinstance(evidence, dict) and 'legacyStartedAt' in evidence:
        del evidence['legacyStartedAt']
        return True
    return False


def _remove_legacy_attempt_result(envelope: dict) -> bool:
    result = envelope.get('result')
    return (envelope.get('terminationReason') == 'interrupted'
            and isinstance(result, dict) and _remove_legacy_evidence(result))


def _remove_legacy_receipt_result(response: dict) -> bool:
    changed = False
    task = response.get('task')
    if isinstance(task, dict):
        changed |= _remove_legacy_evidence(task)
        selected = task.get('selectedAttempt')
        if isinstance(selected, dict) and isinstance(selected.get('result'), dict):
            changed |= _remove_legacy_attempt_result(selected['result'])
    attempt = response.get('attempt')
    if isinstance(attempt, dict) and isinstance(attempt.get('result'), dict):
        changed |= _remove_legacy_attempt_result(attempt['result'])
    return changed


def _normalize_actor(container: dict, lead_id: str | None, *, prior_lead: bool = False) -> bool:
    actor = container.get('actor')
    if isinstance(actor, str):
        role, identifier = _actor(actor, lead_id, prior_lead=prior_lead)
        container['actor'] = {'role': role, 'id': identifier}
        return True
    return False


def _event_actor(connection: sqlite3.Connection, row: dict, actor: str,
                 inherited: dict[str, str], first_takeover: dict[str, int]) -> tuple[str, str]:
    kind = row['kind']
    if kind.startswith('workflow.'):
        prior_lead = row['task_id'] in inherited and row['seq'] < first_takeover.get(row['task_id'], 2**63)
        return _actor(actor, inherited.get(row['task_id']), prior_lead=prior_lead)
    if kind == 'message.updated':
        return 'bridge', actor
    if kind == 'task.cancelled' and actor == 'service':
        task = connection.execute('SELECT spec_json FROM tasks WHERE task_id=?', (row['task_id'],)).fetchone()
        if task is not None and json.loads(task[0]).get('decision'):
            return 'service', 'service'
    return 'host', actor


def _rewrite_receipt_view(connection: sqlite3.Connection, view: dict, inherited: dict[str, str],
                          converted: set[str], fallback_run_id: str | None = None) -> bool:
    """Update product-owned fields of a saved workflow response, not native content."""
    changed = False
    for nested in view.get('results', ()):
        if isinstance(nested, dict):
            changed |= _rewrite_receipt_view(connection, nested, inherited, converted)
    run_id = view.get('runId') or fallback_run_id
    lead_id = inherited.get(run_id)
    original_generation = view.get('ownerGeneration')
    prior_lead = original_generation == 1
    if lead_id is not None:
        run = connection.execute('SELECT host_id,request_fingerprint,execution_workspace_json FROM workflow_runs WHERE run_id=?',
                                 (run_id,)).fetchone()
        if run is not None:
            if run_id in converted and prior_lead and view.get('hostId') == 'lead:' + lead_id:
                view['hostId'] = None
                changed = True
            if run_id in converted and 'requestFingerprint' in view and view['requestFingerprint'] != run['request_fingerprint']:
                view['requestFingerprint'] = run['request_fingerprint']
                changed = True
            workspace = view.get('executionWorkspace')
            if isinstance(workspace, dict) and workspace.get('integrator') == 'host:lead:' + lead_id:
                workspace['integrator'] = lead_id
                changed = True
            control = view.get('control')
            if run_id in converted and prior_lead and isinstance(control, dict) and control.get('hostId') == 'lead:' + lead_id:
                control['hostId'] = None
                changed = True
            task = view.get('task')
            if isinstance(task, dict):
                if run_id in converted and prior_lead and task.get('owner') == 'host:lead:' + lead_id:
                    task['owner'] = lead_id
                    changed = True
                workflow = task.get('workflow')
                if run_id in converted and prior_lead and isinstance(workflow, dict) and workflow.get('hostId') == 'lead:' + lead_id:
                    workflow['hostId'] = None
                    changed = True
                lineage = task.get('lineage')
                if run_id in converted and prior_lead and isinstance(lineage, dict) and lineage.get('currentHostId') == 'lead:' + lead_id:
                    lineage['currentHostId'] = None
                    changed = True
    for key in ('cancellation', 'scope', 'hostConclusion', 'cleanup'):
        value = view.get(key)
        if isinstance(value, dict):
            changed |= _normalize_actor(value, lead_id, prior_lead=prior_lead)
    for key in ('workspaceConflicts', 'integrations'):
        values = view.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    changed |= _normalize_actor(value, lead_id, prior_lead=prior_lead)
    return changed


def _target_structure() -> tuple[dict[str, str], dict[str, list[str]]]:
    with sqlite3.connect(':memory:') as fresh:
        fresh.executescript(SCHEMA)
        tables = {name: sql for name, sql in fresh.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ("
            + ','.join('?' for _ in _REBUILT) + ')', _REBUILT)}
        indexes = {name: [sql for (sql,) in fresh.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name", (name,))]
                   for name in _REBUILT}
    return tables, indexes


def _rebuild(connection: sqlite3.Connection, table: str, sql: str, indexes: list[str],
             owned: dict[str, str], inherited: dict[str, str], converted: set[str],
             first_takeover: dict[str, int]) -> None:
    rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(sql)
    for row in rows:
        if table == 'workflow_runs':
            if row['run_id'] in converted:
                row['host_id'] = None
            if row['run_id'] in owned:
                workspace = json.loads(row['execution_workspace_json'])
                if workspace.get('integrator') == 'host:lead:' + owned[row['run_id']]:
                    workspace['integrator'] = owned[row['run_id']]
                    row['execution_workspace_json'] = canonical_json(workspace)
                if row['run_id'] in converted:
                    from ...protocol import schemas
                    row['request_fingerprint'] = schemas.workflow_request_fingerprint(
                        json.loads(row['micro_task_json']), workspace, None,
                        presentation={'title': row['title']} if row['title'] else {},
                        buddy_locked=bool(row['buddy_locked']),
                        delegation_authority=bool(row['delegation_authority']))
        elif table == 'events':
            payload = json.loads(row['payload_json'])
            actor = payload.pop('actor', None)
            role, identifier = (_event_actor(connection, row, actor, inherited, first_takeover)
                                if isinstance(actor, str) else (None, None))
            if row['kind'] == 'task.submitted' and isinstance(payload.get('microTask'), dict):
                if row['task_id'] in owned:
                    role, identifier = 'lead', owned[row['task_id']]
                elif isinstance(payload['microTask'].get('sourceHostId'), str):
                    role, identifier = 'host', payload['microTask']['sourceHostId']
            _remove_legacy_evidence(payload)
            row['payload_json'] = canonical_json(payload)
            row['actor_role'], row['actor_id'] = role, identifier
        elif table in _ACTOR_TABLES:
            actor = row.pop('actor')
            prior_lead = (row.get('scope_version') == 1 if table == 'workflow_scope_versions'
                          else row.get('owner_generation') == 1 if table == 'workflow_host_conclusions'
                          else False)
            role, identifier = (_actor(actor, inherited.get(row.get('run_id')), prior_lead=prior_lead)
                                if actor is not None else (None, None))
            row['actor_role'], row['actor_id'] = role, identifier
        columns = list(row)
        connection.execute(
            f'INSERT INTO "{table}" (' + ','.join('"' + key + '"' for key in columns) + ') VALUES ('
            + ','.join('?' for _ in columns) + ')', tuple(row.values()))
    for index in indexes:
        connection.execute(index)


def migrate(state: Path) -> None:
    """Convert a stopped board after its schema-20 backup has been verified."""
    board = Path(state) / 'board.sqlite3'
    tables, indexes = _target_structure()
    with closing(sqlite3.connect(board, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Expected schema 20 for the pending upgrade')
            unresolved = connection.execute(
                "SELECT l.attempt_id FROM attempt_legacy_starts l JOIN attempts a USING(attempt_id)"
                " WHERE a.execution_state!='finished' OR a.shutdown_confirmed!=1 LIMIT 1").fetchone()
            if unresolved:
                raise BoardError('UPGRADE_NOT_IDLE', 'Legacy attempt lacks confirmed shutdown',
                                 attemptId=unresolved[0])
            owned = dict(connection.execute('SELECT owned_run_id,lead_run_id FROM workflow_lead_tasks'))
            inherited = dict(connection.execute(
                "WITH RECURSIVE descendants(run_id,lead_run_id) AS ("
                "SELECT owned_run_id,lead_run_id FROM workflow_lead_tasks UNION "
                "SELECT c.child_task_id,d.lead_run_id FROM workflow_children c "
                "JOIN descendants d ON c.parent_run_id=d.run_id) "
                "SELECT run_id,lead_run_id FROM descendants"))
            converted = {row['run_id'] for row in connection.execute(
                'SELECT run_id,host_id,owner_generation FROM workflow_runs')
                if row['run_id'] in inherited and row['owner_generation'] == 1
                and row['host_id'] == 'lead:' + inherited[row['run_id']]}
            first_takeover = dict(connection.execute(
                "SELECT task_id,MIN(seq) FROM events WHERE kind='workflow.takeover' GROUP BY task_id"))
            for table in _REBUILT:
                _rebuild(connection, table, tables[table], indexes[table], owned, inherited,
                         converted, first_takeover)
            for run_id, lead_id in owned.items():
                if run_id in converted:
                    connection.execute("UPDATE tasks SET owner=? WHERE task_id=? AND owner=?",
                                       (lead_id, run_id, 'host:lead:' + lead_id))
            connection.execute('DROP TABLE attempt_legacy_starts')
            for row in connection.execute("SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL"):
                payload = json.loads(row['result_json'])
                if isinstance(payload, dict) and _remove_legacy_attempt_result(payload):
                    connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?',
                                       (canonical_json(payload), row['attempt_id']))
            for row in connection.execute("SELECT request_id,run_id,decision_json FROM workflow_requests WHERE decision_json IS NOT NULL"):
                decision = json.loads(row['decision_json'])
                if isinstance(decision, dict) and _normalize_actor(decision, inherited.get(row['run_id'])):
                    connection.execute('UPDATE workflow_requests SET decision_json=? WHERE request_id=?',
                                       (canonical_json(decision), row['request_id']))
            for row in connection.execute('SELECT command_id,kind,task_id,response_json FROM commands'):
                response = json.loads(row['response_json'])
                if not isinstance(response, dict):
                    continue
                changed = (_rewrite_receipt_view(connection, response, inherited, converted, row['task_id'])
                           if row['kind'].startswith(('workflow.', 'macro_task.')) else False)
                if row['kind'] in ('task.cancel', 'task.acknowledge'):
                    task = response.get('task')
                    cancellation = task.get('cancellation') if isinstance(task, dict) else None
                    if isinstance(cancellation, dict) and isinstance(cancellation.get('actor'), str):
                        task_id = task.get('runId') or task.get('taskId') or row['task_id']
                        changed |= _normalize_actor(cancellation, inherited.get(task_id))
                changed |= _remove_legacy_receipt_result(response)
                if changed:
                    connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                       (canonical_json(response), row['command_id']))
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Foreign keys failed after schema conversion')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BoardError('UPGRADE_INVALID_BOARD', 'SQLite integrity failed after schema conversion')
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
