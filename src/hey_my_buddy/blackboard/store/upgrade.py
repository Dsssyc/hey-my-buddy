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
    'tasks', 'events', 'workflow_runs', 'workflow_scope_versions', 'workflow_workspace_conflicts',
    'workflow_integrations', 'workspace_cleanup_plans', 'workflow_host_conclusions',
)
_ACTOR_TABLES = frozenset(_REBUILT) - {'tasks', 'workflow_runs', 'events'}


_BARE_ACTORS = {
    'cli': ('host', 'cli'), 'service': ('service', 'service'),
    'service-stop': ('service', 'stop'), 'bridge': ('bridge', 'bridge'),
    'live-bridge': ('bridge', 'live-bridge'), 'bridge-journal': ('bridge', 'bridge-journal'),
}


def _actor(value: str, run_id: str | None, generation: int | None,
           inherited: dict[str, str], known_hosts: set[str]) -> tuple[str, str]:
    """One conversion for stored rows, event authors and product receipt actors."""
    if not isinstance(value, str):
        raise BoardError('UPGRADE_INVALID_ACTOR', 'Malformed schema-20 actor', runId=run_id)
    lead_id = inherited.get(run_id)
    if lead_id is not None and value in ('lead:' + lead_id, 'host:lead:' + lead_id) and generation == 1:
        return 'lead', lead_id
    if value in _BARE_ACTORS:
        return _BARE_ACTORS[value]
    if value.startswith('host:') and value[5:]:
        return 'host', value[5:]
    if ':' in value:
        role, identifier = value.split(':', 1)
        if role in {'lead', 'console', 'service', 'workflow', 'bridge'} and identifier:
            return role, identifier
    if value in known_hosts:
        return 'host', value
    raise BoardError('UPGRADE_INVALID_ACTOR', f'Unknown schema-20 actor {value!r}', runId=run_id)


def _generation(connection: sqlite3.Connection, run_id: str | None, *, seq: int | None = None,
                created_at: str | None = None, explicit: int | None = None) -> int | None:
    if run_id is None:
        return None
    if explicit is not None:
        if not isinstance(explicit, int) or explicit < 1:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Invalid owner generation', runId=run_id)
        return explicit
    if seq is not None:
        count = connection.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.takeover' AND seq<?",
                                   (run_id, seq)).fetchone()[0]
        return 1 + count
    takeovers = list(connection.execute("SELECT created_at FROM events WHERE task_id=? AND kind='workflow.takeover'",
                                        (run_id,)))
    if not takeovers:
        return 1
    if created_at is None or any(row[0] == created_at for row in takeovers):
        raise BoardError('UPGRADE_AMBIGUOUS_GENERATION', 'Cannot establish actor owner generation', runId=run_id)
    return 1 + sum(row[0] < created_at for row in takeovers)


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


def _normalize_actor(container: dict, run_id: str | None, generation: int | None,
                     inherited: dict[str, str], known_hosts: set[str]) -> bool:
    actor = container.get('actor')
    if isinstance(actor, str):
        role, identifier = _actor(actor, run_id, generation, inherited, known_hosts)
        container['actor'] = {'role': role, 'id': identifier}
        return True
    if actor is not None:
        raise BoardError('UPGRADE_INVALID_ACTOR', 'Malformed schema-20 actor', runId=run_id)
    return False


def _host_id(value: str | None, run_id: str, generation: int,
             inherited: dict[str, str]) -> str | None:
    lead_id = inherited.get(run_id)
    return None if lead_id is not None and generation == 1 and value == 'lead:' + lead_id else value


def _owner_at_generation(connection: sqlite3.Connection, run_id: str, generation: int,
                         inherited: dict[str, str]) -> dict[str, str]:
    run = connection.execute('SELECT host_id,owner_generation FROM workflow_runs WHERE run_id=?',
                             (run_id,)).fetchone()
    if run is None or generation > run['owner_generation']:
        raise BoardError('UPGRADE_AMBIGUOUS_GENERATION', 'Receipt has no matching owner generation', runId=run_id)
    host_id = run['host_id']
    if generation < run['owner_generation']:
        takeover = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.takeover' "
                                      "ORDER BY seq", (run_id,)).fetchall()
        values = [json.loads(event[0]) for event in takeover]
        if generation == 1:
            match = next((value for value in values if value.get('previousOwnerGeneration') == 1), None)
            host_id = match.get('previousHostId') if match is not None else None
        else:
            match = next((value for value in values if value.get('ownerGeneration') == generation), None)
            host_id = match.get('newHostId') if match is not None else None
        if match is None:
            raise BoardError('UPGRADE_AMBIGUOUS_GENERATION', 'Takeover history cannot identify receipt owner',
                             runId=run_id)
    if host_id is None:
        lead_id = inherited.get(run_id)
        if lead_id is None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Unowned workflow run has no Lead relationship', runId=run_id)
        return {'role': 'lead', 'id': lead_id}
    return {'role': 'host', 'id': host_id}


def _nested_generation(connection: sqlite3.Connection, run_id: str | None,
                       kind: str, value: dict) -> int | None:
    if run_id is None:
        return None
    if kind == 'scope' and value.get('scopeVersion') == 1:
        return 1
    if kind == 'hostConclusion' and value.get('ownerGeneration') is not None:
        return _generation(connection, run_id, explicit=value['ownerGeneration'])
    timestamp = value.get('updatedAt') if kind in ('scope', 'workspaceConflicts') else value.get('createdAt')
    return _generation(connection, run_id, created_at=timestamp)


def _cancellation_generation(connection: sqlite3.Connection, run_id: str | None,
                             value: dict, inherited: dict[str, str], known_hosts: set[str]) -> int | None:
    if run_id is None:
        return None
    actor = value.get('actor')
    matches = set()
    for event in connection.execute("SELECT seq,payload_json,actor_role,actor_id FROM events WHERE task_id=? "
                                    "AND kind IN ('workflow.cancelled','workflow.helper_cancelled')", (run_id,)):
        payload = json.loads(event['payload_json'])
        reason = payload.get('reason')
        reason = (reason.strip()[:4000] or None) if isinstance(reason, str) else None
        if reason != value.get('reason'):
            continue
        generation = _generation(connection, run_id, seq=event['seq'])
        if _actor(actor, run_id, generation, inherited, known_hosts) == (event['actor_role'], event['actor_id']):
            matches.add(generation)
    if len(matches) != 1:
        raise BoardError('UPGRADE_AMBIGUOUS_GENERATION', 'Cancellation receipt has no unique event generation',
                         runId=run_id)
    return matches.pop()


def _rewrite_receipt_view(connection: sqlite3.Connection, view: dict, inherited: dict[str, str],
                          known_hosts: set[str], fallback_run_id: str | None = None,
                          created_at: str | None = None) -> bool:
    """Update product-owned fields of a saved workflow response, not native content."""
    changed = False
    for nested in view.get('results', ()):
        if isinstance(nested, dict):
            changed |= _rewrite_receipt_view(connection, nested, inherited, known_hosts,
                                             fallback_run_id, created_at)
    run_id = view.get('runId') or fallback_run_id
    lead_id = inherited.get(run_id)
    generation = _generation(connection, run_id, explicit=view.get('ownerGeneration'), created_at=created_at)
    run = (connection.execute('SELECT request_fingerprint FROM workflow_runs WHERE run_id=?',
                              (run_id,)).fetchone() if run_id is not None else None)
    if run is not None:
        receipt_owner = _owner_at_generation(connection, run_id, generation, inherited)
        if lead_id is not None and generation == 1 and view.get('hostId') == 'lead:' + lead_id:
            view['hostId'] = None
            changed = True
        if 'requestFingerprint' in view and view['requestFingerprint'] != run['request_fingerprint']:
            view['requestFingerprint'] = run['request_fingerprint']
            changed = True
        workspace = view.get('executionWorkspace')
        if isinstance(workspace, dict) and isinstance(workspace.get('integrator'), str):
            workspace['integrator'] = receipt_owner
            changed = True
        if receipt_owner['role'] == 'lead' and 'control' in view:
            del view['control']
            changed = True
        task = view.get('task')
        if isinstance(task, dict):
            if 'owner' in task:
                task['owner'] = None
                changed = True
            workflow = task.get('workflow')
            if (lead_id is not None and generation == 1 and isinstance(workflow, dict)
                    and workflow.get('hostId') == 'lead:' + lead_id):
                workflow['hostId'] = None
                changed = True
            lineage = task.get('lineage')
            if (lead_id is not None and generation == 1 and isinstance(lineage, dict)
                    and lineage.get('currentHostId') == 'lead:' + lead_id):
                lineage['currentHostId'] = None
                changed = True
    for key in ('cancellation', 'scope', 'hostConclusion', 'cleanup'):
        value = view.get(key)
        if isinstance(value, dict) and value.get('actor') is not None:
            if not isinstance(value['actor'], str):
                _normalize_actor(value, run_id, None, inherited, known_hosts)
            record_generation = (_cancellation_generation(connection, run_id, value, inherited, known_hosts)
                                 if key == 'cancellation'
                                 else _nested_generation(connection, run_id, key, value))
            changed |= _normalize_actor(value, run_id, record_generation, inherited, known_hosts)
    for key in ('workspaceConflicts', 'integrations'):
        values = view.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict) and value.get('actor') is not None:
                    if not isinstance(value['actor'], str):
                        _normalize_actor(value, run_id, None, inherited, known_hosts)
                    changed |= _normalize_actor(value, run_id,
                                                _nested_generation(connection, run_id, key, value),
                                                inherited, known_hosts)
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


def _current_owners(connection: sqlite3.Connection, owned: dict[str, str]) -> dict[str, dict[str, str]]:
    runs = {row['run_id']: row for row in connection.execute(
        'SELECT run_id,host_id,owner_generation FROM workflow_runs')}
    parents = dict(connection.execute('SELECT child_task_id,parent_run_id FROM workflow_children'))
    result = {}
    for run_id in runs:
        root = run_id
        visited = set()
        while root in parents:
            if root in visited:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Workflow child relationship has a cycle', runId=run_id)
            visited.add(root)
            root = parents[root]
        controlling = runs.get(root)
        if controlling is None:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Workflow child has no controlling run', runId=run_id)
        host_id = controlling['host_id']
        lead_id = owned.get(root)
        if (lead_id is not None and controlling['owner_generation'] == 1
                and host_id in (None, 'lead:' + lead_id)):
            result[run_id] = {'role': 'lead', 'id': lead_id}
        elif host_id is not None:
            result[run_id] = {'role': 'host', 'id': host_id}
        else:
            raise BoardError('UPGRADE_INVALID_BOARD', 'Controlling run has no owner', runId=run_id)
    return result


def _rebuild(connection: sqlite3.Connection, table: str, sql: str, indexes: list[str],
             owned: dict[str, str], inherited: dict[str, str], converted: set[str],
             known_hosts: set[str], workflow_ids: set[str],
             current_owners: dict[str, dict[str, str]]) -> None:
    rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(sql)
    for row in rows:
        if table == 'tasks':
            if row['task_id'] in workflow_ids:
                row['owner'] = None
        elif table == 'workflow_runs':
            row['request_fingerprint'] = None
            if row['run_id'] in converted:
                row['host_id'] = None
            workspace = json.loads(row['execution_workspace_json'])
            lead_id = inherited.get(row['run_id'])
            if row['host_id'] is None and lead_id is None:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Unowned workflow run has no Lead relationship',
                                 runId=row['run_id'])
            workspace['integrator'] = current_owners[row['run_id']]
            row['execution_workspace_json'] = canonical_json(workspace)
        elif table == 'events':
            payload = json.loads(row['payload_json'])
            actor = payload.pop('actor', None)
            if actor is not None and not isinstance(actor, str):
                raise BoardError('UPGRADE_INVALID_ACTOR', 'Malformed schema-20 event actor', runId=row['task_id'])
            generation = _generation(connection, row['task_id'], seq=row['seq'])
            role, identifier = (_actor(actor, row['task_id'], generation, inherited, known_hosts)
                                if isinstance(actor, str) else (None, None))
            if row['kind'] == 'task.submitted' and isinstance(payload.get('microTask'), dict):
                source = payload['microTask'].get('sourceHostId')
                if isinstance(source, str):
                    source_role, source_id = _actor('host:' + source, row['task_id'], generation,
                                                    inherited, known_hosts)
                    payload['microTask']['sourceHostId'] = None if source_role == 'lead' else source_id
                    if role is None:
                        role, identifier = source_role, source_id
            if row['kind'] == 'workflow.helper_admitted' and isinstance(payload.get('sourceHostId'), str):
                payload['sourceHostId'] = _host_id(payload['sourceHostId'], row['task_id'], generation, inherited)
            if row['kind'] == 'workflow.takeover':
                previous = payload.get('previousOwnerGeneration')
                if previous is None:
                    raise BoardError('UPGRADE_AMBIGUOUS_GENERATION', 'Takeover omits previous generation',
                                     runId=row['task_id'])
                payload['previousHostId'] = _host_id(payload.get('previousHostId'), row['task_id'], previous, inherited)
                payload['newHostId'] = _host_id(payload.get('newHostId'), row['task_id'], previous + 1, inherited)
            _remove_legacy_evidence(payload)
            row['payload_json'] = canonical_json(payload)
            row['actor_role'], row['actor_id'] = role, identifier
        elif table in _ACTOR_TABLES:
            actor = row.pop('actor')
            generation = _generation(connection, row.get('run_id'),
                                     explicit=(row.get('owner_generation') or
                                               (1 if table == 'workflow_scope_versions' and row['scope_version'] == 1 else None)),
                                     created_at=(row.get('updated_at') if table == 'workflow_workspace_conflicts'
                                                 else row.get('created_at')))
            role, identifier = (_actor(actor, row.get('run_id'), generation, inherited, known_hosts)
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
            known_hosts = {row[0] for row in connection.execute('SELECT host_id FROM workflow_runs WHERE host_id IS NOT NULL')}
            workflow_ids = {row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')}
            current_owners = _current_owners(connection, owned)
            for event in connection.execute("SELECT payload_json FROM events WHERE kind IN "
                                            "('task.submitted','workflow.helper_admitted','workflow.takeover')"):
                payload = json.loads(event[0])
                for value in (payload.get('microTask', {}).get('sourceHostId'), payload.get('sourceHostId'),
                              payload.get('previousHostId'), payload.get('newHostId')):
                    if isinstance(value, str):
                        known_hosts.add(value)
            for table in _REBUILT:
                _rebuild(connection, table, tables[table], indexes[table], owned, inherited,
                         converted, known_hosts, workflow_ids, current_owners)
            for row in connection.execute('SELECT child_task_id,workspace_intent_json FROM workflow_children'):
                workspace = json.loads(row['workspace_intent_json'])
                run = connection.execute('SELECT host_id FROM workflow_runs WHERE run_id=?',
                                         (row['child_task_id'],)).fetchone()
                if run is None:
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Helper has no workflow run', runId=row['child_task_id'])
                workspace['integrator'] = current_owners[row['child_task_id']]
                connection.execute('UPDATE workflow_children SET workspace_intent_json=? WHERE child_task_id=?',
                                   (canonical_json(workspace), row['child_task_id']))
            connection.execute('DROP TABLE attempt_legacy_starts')
            for row in connection.execute("SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL"):
                payload = json.loads(row['result_json'])
                if isinstance(payload, dict) and _remove_legacy_attempt_result(payload):
                    connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?',
                                       (canonical_json(payload), row['attempt_id']))
            for row in connection.execute("SELECT request_id,run_id,decision_json,decided_at FROM workflow_requests WHERE decision_json IS NOT NULL"):
                decision = json.loads(row['decision_json'])
                generation = _generation(connection, row['run_id'], created_at=row['decided_at'])
                changed = False
                if isinstance(decision, dict):
                    changed |= _normalize_actor(decision, row['run_id'], generation, inherited, known_hosts)
                    if isinstance(decision.get('resolution'), dict):
                        changed |= _normalize_actor(decision['resolution'], row['run_id'], generation,
                                                    inherited, known_hosts)
                if changed:
                    connection.execute('UPDATE workflow_requests SET decision_json=? WHERE request_id=?',
                                       (canonical_json(decision), row['request_id']))
            for row in connection.execute('SELECT command_id,kind,task_id,response_json,created_at FROM commands'):
                response = json.loads(row['response_json'])
                if not isinstance(response, dict):
                    continue
                changed = (_rewrite_receipt_view(connection, response, inherited, known_hosts,
                                                row['task_id'], row['created_at'])
                           if row['kind'].startswith(('workflow.', 'macro_task.')) else False)
                if row['kind'] in ('task.cancel', 'task.acknowledge'):
                    task = response.get('task')
                    cancellation = task.get('cancellation') if isinstance(task, dict) else None
                    task_id = (task.get('runId') or task.get('taskId') or row['task_id']) if isinstance(task, dict) else row['task_id']
                    if (isinstance(task, dict) and 'owner' in task and task_id is not None
                            and connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?', (task_id,)).fetchone()):
                        task['owner'] = None
                        changed = True
                    if isinstance(cancellation, dict) and cancellation.get('actor') is not None:
                        if not isinstance(cancellation['actor'], str):
                            _normalize_actor(cancellation, task_id, None, inherited, known_hosts)
                        cancellation_generation = (
                            _cancellation_generation(connection, task_id, cancellation, inherited, known_hosts)
                            if task_id in workflow_ids else
                            _generation(connection, task_id, created_at=row['created_at']))
                        changed |= _normalize_actor(cancellation, task_id,
                                    cancellation_generation,
                                    inherited, known_hosts)
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
