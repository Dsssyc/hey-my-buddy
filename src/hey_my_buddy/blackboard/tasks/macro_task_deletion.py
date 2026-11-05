"""Settled macro-task deletion: durable fence, owned content, atomic row removal.

The caller supplies its BoardStore; no public transport operation is registered
here. Database rows remain the ownership evidence until physical work completes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import uuid

from ... import private_dirs
from ...errors import BoardError
from ...protocol import schemas
from ...protocol.reasons import reason
from . import micro_task, projects, storage, worker_sessions
from .workspace_bindings import MANIFEST_OWNERS_CTE, RUN_BINDINGS_CTE
from .macro_task_deletion_progress import collect_skipped
from ..service.harness_health import read_health
from ..store.db import canonical_json


_TARGET_PATTERN = re.compile(r'(?:macro-[A-Za-z0-9-]{1,120}|run:[A-Za-z0-9._:-]{1,128})\Z')
_BLOCKER_CODES = {
    'queued': 'macro-task-deletion.queued',
    'routing': 'macro-task-deletion.routing',
    'running': 'macro-task-deletion.running',
    'awaiting-host': 'macro-task-deletion.awaiting-host',
    'review': 'macro-task-deletion.review',
    'stop-unconfirmed': 'macro-task-deletion.stop-unconfirmed',
}


def _request_target(params, fields, operation, console_authority):
    if console_authority is None:
        raise BoardError('UNAUTHORIZED', 'Macro task deletion requires an authenticated console writer')
    schemas.reject_unknown(params, {'macroTaskId'} | fields, operation)
    return schemas.required_string(params, 'macroTaskId', max_length=134, pattern=_TARGET_PATTERN)


def preview_request(store, params: dict, *, console_authority: dict | None = None) -> dict:
    target = _request_target(params, set(), 'macro_task.deletion_preview', console_authority)
    return preview(store, target)


def delete_request(store, params: dict, *, console_authority: dict | None = None) -> dict:
    target = _request_target(params, {'microTaskCount', 'confirm'}, 'macro_task.delete', console_authority)
    if params.get('confirm') is not True:
        raise BoardError('INVALID_ARGUMENT', 'confirm must be true')
    return delete(store, target, params.get('microTaskCount'))


@dataclass
class Scope:
    runs: set[str]
    tasks: set[str]
    attempts: set[str]
    decisions: set[str]
    turns: set[str]
    requests: set[str]
    run_rows: list
    task_rows: list
    attempt_rows: list
    manifests: list[dict]
    artifacts: list[dict]
    sessions: list[dict] = field(default_factory=list)


def _ids(values):
    return canonical_json(sorted(values))


def _in(column, values):
    return f'{column} IN (SELECT value FROM json_each(?))', [_ids(values)]


def _rows(connection, table, column, values):
    clause, args = _in(column, values)
    return connection.execute(f'SELECT * FROM {table} WHERE {clause}', args).fetchall()


def _target(target):
    if target.startswith('run:'):
        return 'micro-task-tree', target
    return 'macro-task', target


def _record(connection, target):
    kind, identifier = _target(target)
    return connection.execute('SELECT * FROM macro_task_deletions WHERE target_kind=? AND target_id=?',
                              (kind, identifier)).fetchone()


def _collect(connection, target, state_dir, *, read_evidence=True):
    from .macro_tasks import MEMBERS_SQL

    kind, identifier = _target(target)
    if kind == 'macro-task' and connection.execute(
            'SELECT 1 FROM macro_tasks WHERE macro_task_id=?', (identifier,)).fetchone() is None:
        raise BoardError('NOT_FOUND', 'Unknown macro task', macroTaskId=target)
    members = connection.execute(
        f'SELECT task_id,micro_task_run_id FROM ({MEMBERS_SQL}) WHERE group_id=?', (target,)).fetchall()
    if not members and kind == 'micro-task-tree':
        raise BoardError('NOT_FOUND', 'Unknown run group', macroTaskId=target)
    runs = {row['micro_task_run_id'] for row in members if row['micro_task_run_id'] is not None}
    run_rows = _rows(connection, 'workflow_runs', 'run_id', runs)
    assert len(run_rows) == len(runs), 'Owned child has no workflow run'
    if kind == 'macro-task':
        assert all(row['macro_task_id'] == target for row in run_rows), 'Inconsistent macro task ownership'
    else:
        assert all(row['macro_task_id'] is None for row in run_rows), 'Inconsistent run group ownership'
    decisions = {row['decision_id'] for row in _rows(connection, 'workflow_routes', 'run_id', runs)}
    tasks = set(runs)
    tasks.update(row['task_id'] for row in _rows(connection, 'decision_requests', 'decision_id', decisions)
                 if row['task_id'] is not None)
    tasks.update(row['task_id'] for row in connection.execute(
        f'SELECT task_id FROM ({micro_task.ROUTING_TASKS_SQL}) WHERE decision_id IN (SELECT value FROM json_each(?))',
        (_ids(decisions),)))
    task_rows = _rows(connection, 'tasks', 'task_id', tasks)
    assert len(task_rows) == len(tasks), 'Owned execution has no task'
    for route in _rows(connection, 'workflow_routes', 'decision_id', decisions):
        assert route['run_id'] in runs, 'Decision belongs to another run'
    attempt_rows = _rows(connection, 'attempts', 'task_id', tasks)
    turns = _rows(connection, 'workflow_turns', 'run_id', runs)
    artifact_rows = _rows(connection, 'workflow_artifacts', 'run_id', runs) if read_evidence else []
    artifacts = [dict(row) for row in artifact_rows]
    owners = connection.execute(
        f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}),'
        ' refs AS ('
        ' SELECT workspace_id,input_manifest_sha256 AS digest FROM workflow_artifacts'
        ' WHERE run_id IN (SELECT value FROM json_each(?)) AND input_manifest_sha256 IS NOT NULL'
        ' UNION SELECT output_workspace_id,output_manifest_sha256 FROM workflow_artifacts'
        ' WHERE run_id IN (SELECT value FROM json_each(?)) AND output_workspace_id IS NOT NULL'
        ' UNION SELECT workspace_id,workspace_manifest_sha256 FROM workflow_turns'
        ' WHERE run_id IN (SELECT value FROM json_each(?))'
        ' UNION SELECT workspace_id,workspace_manifest_sha256 FROM workflow_continuations'
        ' WHERE run_id IN (SELECT value FROM json_each(?)) AND workspace_id IS NOT NULL)'
        ' SELECT DISTINCT o.*,COALESCE(m.project_id,r.standalone_project_id) AS project_id'
        ' FROM manifest_owners o JOIN refs ON refs.workspace_id=o.workspace_id'
        ' AND refs.digest=o.manifest_sha256'
        ' JOIN workflow_runs r ON r.run_id=o.owner_run_id'
        ' LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id'
        ' WHERE o.owner_run_id IN (SELECT value FROM json_each(?))',
        (_ids(runs), _ids(runs), _ids(runs), _ids(runs), _ids(runs))).fetchall() if read_evidence else []
    manifests = [dict(row) for row in owners]
    request_ids = {row['request_id'] for row in _rows(connection, 'decision_requests', 'decision_id', decisions)}
    return Scope(runs, tasks, {r['attempt_id'] for r in attempt_rows}, decisions,
                 {r['turn_id'] for r in turns}, request_ids, run_rows, task_rows,
                 attempt_rows, manifests, artifacts)


def _blockers(store, connection, scope):
    runs = {row['run_id']: row for row in scope.run_rows}
    tasks = {row['task_id']:row for row in scope.task_rows}
    reasons = {}
    def block(task_id, code, attempt_ids=()):
        from .macro_tasks import _run_title
        task = tasks[task_id]
        run = runs.get(task_id)
        title, title_source = _run_title(connection, task_id)
        key = (task_id, code)
        reasons[key] = {'runId': task_id if run else None, 'taskId': task_id,
                        'title': title, 'titleSource': title_source,
                        'state': task['state'], 'runState': run['state'] if run else None,
                        'reason': reason(_BLOCKER_CODES[code]),
                        'attemptIds': sorted(attempt_ids)}
    for task in scope.task_rows:
        identifier = task['task_id']
        run = runs.get(identifier)
        if not store.workflow._stop_proven(connection, task):
            block(identifier, 'stop-unconfirmed',
                  [a['attempt_id'] for a in scope.attempt_rows if a['task_id'] == identifier
                   and (a['execution_state'] != 'finished' or not a['shutdown_confirmed'])])
        if task['state'] in ('queued', 'running', 'cancelling', 'reconciliation-needed'):
            block(identifier, 'queued' if task['state'] == 'queued' else 'running')
        if run and run['state'] not in ('accepted', 'concluded', 'cancelled', 'failed'):
            code = {'delivered': 'review', 'awaiting-host': 'awaiting-host'}.get(run['state'], 'running')
            block(identifier, code)
    for row in _rows(connection, 'workflow_requests', 'run_id', scope.runs):
        if row['state'] == 'open':
            block(row['run_id'], 'awaiting-host')
    for row in _rows(connection, 'workflow_continuations', 'run_id', scope.runs):
        if row['state'] in ('recorded', 'queued'):
            block(row['run_id'], 'queued')
    for row in _rows(connection, 'workflow_routes', 'run_id', scope.runs):
        if row['state'] == 'pending':
            block(row['run_id'], 'routing')
    return sorted(reasons.values(), key=lambda row: (row['taskId'], row['reason']['code']))


def _storage_in_use(store, connection, scope):
    """Match only the allocations, ref namespaces and native sessions we remove."""
    workspace_ids = {m['workspace_id'] for m in scope.manifests}
    checkouts = {m['checkout_root'] for m in scope.manifests if m['workspace_kind'] == 'worktree'
                 and m['checkout_root'] == str(store.directory.resolve() / 'workspaces' /
                                                m['workspace_id'] / 'checkout')}
    holders = {}

    def retain(kind, owner):
        if owner not in scope.tasks:
            holders.setdefault(kind,set()).add(owner)

    predicates = ['refs.workspace_id IN (SELECT value FROM json_each(?))']
    args = [_ids(workspace_ids)]
    for checkout_path in sorted(checkouts):
        descendant = checkout_path + os.sep
        predicates.append('(o.checkout_root=? OR substr(o.checkout_root,1,?)=?)')
        args.extend((checkout_path, len(descendant), descendant))
    query = (
        f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}),'
        f' run_bindings AS ({RUN_BINDINGS_CTE}),'
        ' refs AS ('
        ' SELECT run_id AS holder,workspace_id,manifest_sha256 FROM run_bindings'
        ' UNION ALL SELECT run_id,workspace_id,input_manifest_sha256 FROM workflow_artifacts'
        ' WHERE input_manifest_sha256 IS NOT NULL'
        ' UNION ALL SELECT run_id,output_workspace_id,output_manifest_sha256 FROM workflow_artifacts'
        ' WHERE output_workspace_id IS NOT NULL'
        ' UNION ALL SELECT run_id,workspace_id,workspace_manifest_sha256 FROM workflow_continuations'
        ' WHERE workspace_id IS NOT NULL'
        ' UNION ALL SELECT run_id,workspace_id,workspace_manifest_sha256 FROM workflow_turns)'
        ' SELECT DISTINCT refs.holder FROM refs LEFT JOIN manifest_owners o'
        ' ON o.workspace_id=refs.workspace_id AND o.manifest_sha256=refs.manifest_sha256'
        ' WHERE ' + ' OR '.join(predicates)
    )
    for row in connection.execute(query, args):
        retain('workspace', row['holder'])
    for row in connection.execute(
            "SELECT holder_task_id,workspace_id,checkout_root FROM workspace_reservations WHERE state='held'"):
        if row['workspace_id'] in workspace_ids or row['checkout_root'] in checkouts:
            retain('checkout',row['holder_task_id'])
    session_ids = {row['sessionId'] for row in scope.sessions}
    for row in worker_sessions.session_references(connection,session_ids):
        retain('native-session',row['taskId'])
    return [{'objectKind':kind,'holderRunIds':sorted(ids)} for kind,ids in sorted(holders.items())]


def _project_paths(connection, project_id):
    """Published SQL locations of the same project, including later runs."""
    rows = connection.execute(
        f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})'
        ' SELECT DISTINCT o.repository_path FROM manifest_owners o'
        ' JOIN workflow_runs r ON r.run_id=o.owner_run_id'
        ' LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id'
        ' WHERE COALESCE(m.project_id,r.standalone_project_id)=?', (project_id,)).fetchall()
    return tuple(row['repository_path'] for row in rows)


def _unavailable_harnesses(connection, scope):
    """A session in a harness user store needs that harness for its deletion."""
    involved = {}
    for session in scope.sessions:
        involved.setdefault(session['harness'], set()).add(session['taskId'])
    unavailable = []
    for harness in sorted(involved):
        health = read_health(connection, harness)
        if health['status'] != 'ready':
            unavailable.append({'harness': harness, 'reason': health['reason'],
                                'microTaskIds': sorted(involved[harness])})
    return unavailable


def _require_unused_storage(store, connection, scope, target):
    shared = _storage_in_use(store, connection, scope)
    if shared:
        raise BoardError('MACRO_TASK_STORAGE_IN_USE', 'Storage is retained by another micro task',
                         macroTaskId=target, objects=shared)


def _busy(connection, scope):
    return any(row['state'] == 'applying' for row in _rows(connection, 'workspace_cleanup_plans', 'run_id', scope.runs))


def _summary(row, scope, *, duplicate):
    return {'deletionId':row['deletion_id'], 'macroTaskId':row['target_id'], 'state':'deleting',
            'deletedAt':None, 'microTaskCount':len(scope.runs), 'duplicate':duplicate}


def _scope_summary(store, scope, connection):
    """Project retained identities and current refs, without reading native stores or Git sizes."""
    from .macro_task_deletion_workspace import _expected_refs, _refs, _repository_current

    checkouts = len({m['checkout_root'] for m in scope.manifests
                     if m['workspace_kind'] == 'worktree' and m['checkout_root'] ==
                     str(store.directory.resolve() / 'workspaces' / m['workspace_id'] / 'checkout')})
    manifests = {}
    refs = {}
    refs_known = True
    for manifest in scope.manifests:
        identifier = manifest['workspace_id']
        previous = manifests.setdefault(identifier, manifest)
        if previous != manifest:
            refs_known = False
            continue
    if refs_known:
        try:
            expected = _expected_refs(manifests, scope.artifacts)
        except BoardError as error:
            if error.code != 'WORKSPACE_CONFLICT':
                raise
            refs_known = False
    if refs_known:
        for identifier, manifest in manifests.items():
            candidates = _project_paths(connection, manifest['project_id'])
            repository = _repository_current(manifest, candidates=candidates)
            if repository is None:
                refs_known = False
                break
            actual = _refs(repository, identifier)
            if any(commit is None or expected[identifier].get(ref) != commit for ref, commit in actual.items()):
                refs_known = False
                break
            refs.update(((manifest['project_id'], ref), commit) for ref, commit in actual.items())

    sessions = {(session['harness'], session['sessionId']) for session in scope.sessions}
    # Artifacts bind Git snapshots, whose objects may be shared with the user's
    # repository. A patch or checkout path alone is not a complete owned-byte
    # inventory. Native session receipts likewise contain no byte inventory.
    return {'artifacts': {'count': len(scope.artifacts), 'bytes': None if scope.artifacts else 0},
            'sessions': {'count': len(sessions), 'bytes': None if sessions else 0},
            'checkouts': checkouts, 'gitRefs': len(refs) if refs_known else None}


def preview(store, target: str) -> dict:
    """Return confirmation counts and at most fifty blockers in one snapshot."""
    with store.db.read() as connection:
        deletion = _record(connection, target)
        scope = _collect(connection, target, store.directory)
        if deletion is None and _busy(connection, scope):
            raise BoardError('MACRO_TASK_BUSY', 'A workspace cleanup is applying', macroTaskId=target)
        if deletion is None:
            scope.sessions = worker_sessions.deletable_sessions(store,scope.tasks,connection=connection)
            shared = _storage_in_use(store,connection,scope)
            if shared:
                raise BoardError('MACRO_TASK_STORAGE_IN_USE', 'Storage is retained by another micro task', macroTaskId=target, objects=shared)
            unavailable = _unavailable_harnesses(connection, scope)
            if unavailable:
                raise BoardError('MACRO_TASK_HARNESS_UNAVAILABLE', 'A harness with stored sessions is unavailable',
                                 macroTaskId=target, harnesses=unavailable)
            blockers = _blockers(store, connection, scope)
            summary = _scope_summary(store, scope, connection)
        else:
            blockers = []
            summary = {'artifacts': {'count': len(scope.artifacts), 'bytes': None if scope.artifacts else 0},
                       'sessions': {'count': None, 'bytes': None}, 'checkouts': None, 'gitRefs': None}
        concluded = {r['run_id'] for r in _rows(connection, 'workflow_host_conclusions', 'run_id', scope.runs)}
        tasks = {r['task_id']:r for r in scope.task_rows}
        failures = sum(r['state'] in ('failed','cancelled') and not tasks[r['run_id']]['accepted_at']
                       and r['run_id'] not in concluded for r in scope.run_rows)
        return {'macroTaskId':target, 'microTaskCount':len(scope.runs), 'unconcludedFailureCount':failures,
                'scope': summary,
                'blockerTotal':len(blockers), 'blockers':blockers[:50],
                'backupRetention':'until-backup-rotation', 'deletionState':'deleting' if deletion else None}


def _delete_sessions(store, scope):
    from ...buddy.harnesses.registry import adapter
    grouped = {}
    for session in scope.sessions:
        grouped.setdefault(session['harness'], set()).add(session['sessionId'])
    for harness, ids in grouped.items():
        adapter(harness).delete_sessions(sorted(ids), state_dir=store.directory)
    return []


def _delete_workspaces(store, scope):
    from .macro_task_deletion_workspace import delete_workspaces
    with store.db.read() as connection:
        candidates = {m['workspace_id']: _project_paths(connection, m['project_id'])
                  for m in scope.manifests}
    return delete_workspaces(store, scope.manifests, scope.artifacts,
                             candidate_paths=candidates,
                             owned_runs=scope.runs)


def _remove_owned_tree(path, *, object_kind='private-root'):
    if (private_dirs.linked(path)
            or path.exists() and not path.is_dir()):
        return [{'objectKind':object_kind, 'path':str(path), 'reason':reason('macro-task-deletion.owner-unproven')}]
    private_dirs.remove_tree(path)
    return []


def _delete_private_roots(store, scope):
    with collect_skipped() as skipped:
        adapters = {task_id:set() for task_id in scope.tasks}
        for row in scope.task_rows + scope.attempt_rows:
            if row['adapter'] in private_dirs.ADAPTERS:
                adapters[row['task_id']].add(row['adapter'])
            if 'model_harness' in row.keys() and row['model_harness'] in private_dirs.ADAPTERS:
                adapters[row['task_id']].add(row['model_harness'])
        with store.db.read() as connection:
            for row in _rows(connection, 'router_dispatches', 'task_id', scope.tasks):
                adapters[row['task_id']].add(row['harness'])
            for row in _rows(connection,'workflow_turns','run_id',scope.runs):
                buddy = json.loads(row['input_json']).get('buddy')
                if buddy and buddy['harness'] in private_dirs.ADAPTERS:
                    adapters[row['run_id']].add(buddy['harness'])
            for row in scope.run_rows:
                if row['buddy_json']:
                    adapters[row['run_id']].add(json.loads(row['buddy_json'])['harness'])
        for task_id, harnesses in adapters.items():
            for harness in harnesses:
                root = private_dirs.micro_task_root(store.directory, harness, task_id)
                skipped.extend(_remove_owned_tree(root))
            skipped.extend(_remove_owned_tree(store.directory / 'attempts' / task_id))
        return skipped


def _delete_session_group(store, target):
    return _remove_owned_tree(private_dirs.session_group_root(store.directory, target), object_kind='session-records')


def _delete_rows(store, connection, scope, deletion):
    r,t,a,d,u = scope.runs,scope.tasks,scope.attempts,scope.decisions,scope.turns
    project_ids = {row['standalone_project_id'] for row in scope.run_rows
                   if row['standalone_project_id'] is not None}
    project_ids.update(json.loads(row['execution_workspace_json'])['projectId']
                       for row in scope.run_rows if row['checkout'] == 'none')
    if deletion['target_kind'] == 'macro-task':
        project_ids.add(connection.execute('SELECT project_id FROM macro_tasks WHERE macro_task_id=?',
                                           (deletion['target_id'],)).fetchone()['project_id'])
    bindings = {'runId':r, 'targetRunId':r,
                'requestId':{x['request_id'] for x in _rows(connection,'workflow_requests','run_id',r)},
                'continuationId':{x['continuation_id'] for x in _rows(connection,'workflow_continuations','run_id',r)},
                'artifactId':{x['artifact_id'] for x in _rows(connection,'workflow_artifacts','run_id',r)},
                'integrationId':{x['integration_id'] for x in _rows(connection,'workflow_integrations','run_id',r)},
                'conclusionId':{x['conclusion_id'] for x in _rows(connection,'workflow_host_conclusions','run_id',r)},
                'conflictId':{x['conflict_id'] for x in _rows(connection,'workflow_workspace_conflicts','run_id',r)}}
    bindings['macroTaskId'] = {deletion['target_id']}
    clauses,args = [],[]
    for column,values in (('task_id',t),('attempt_id',a)):
        clause,values_args = _in(column,values)
        clauses.append(clause);args.extend(values_args)
    response_clauses,response_args = [],[]
    for field,values in bindings.items():
        response_clauses.append(
            f"json_extract(CASE WHEN json_valid(response_json) THEN response_json END,'$.{field}')"
            " IN (SELECT value FROM json_each(?))")
        response_args.append(_ids(values))
    clauses.append("((kind LIKE 'workflow.%' OR kind='macro_task.stop') AND (" + ' OR '.join(response_clauses) + '))')
    args.extend(response_args)
    commands = {row[0] for row in connection.execute(
        'SELECT command_id FROM commands WHERE ' + ' OR '.join(clauses),args)}
    clause,args = _in('command_id',commands)
    connection.execute(f'DELETE FROM commands WHERE {clause}',args)
    def remove(table, *conditions):
        clauses,args = [],[]
        for column,values in conditions:
            clause,params = _in(column,values)
            clauses.append(clause);args.extend(params)
        connection.execute(f'DELETE FROM {table} WHERE ' + ' OR '.join(clauses),args)
    # Facts refer to events and attempts; preserve their dependency order.
    outcome_events = {x['event_seq'] for x in connection.execute(
        'SELECT event_seq FROM router_outcomes WHERE task_id IN (SELECT value FROM json_each(?))'
        ' OR attempt_id IN (SELECT value FROM json_each(?)) OR request_id IN (SELECT value FROM json_each(?))',
        (_ids(t),_ids(a),_ids(scope.requests)))}
    remove('router_outcomes',('event_seq',outcome_events))
    for table,conditions in [
        ('micro_task_classifications',[('run_id',r)]),
        ('kind_summary_evidence',[('run_id',r)]),
        ('routing_judgments',[('decision_id',d)]),
        ('workflow_host_conclusions',[('run_id',r),('attempt_id',a)]),
        ('workflow_lead_tasks',[('lead_run_id',r),('owned_run_id',r),('lead_turn_id',u)]),
        ('workflow_fixes',[('run_id',r),('fixed_run_id',r)]),
        ('workflow_requests',[('run_id',r)]),('workflow_continuations',[('run_id',r)]),
        ('workflow_suggestions',[('run_id',r),('attempt_id',a)]),('workflow_scope_versions',[('run_id',r)]),
        ('workflow_workspace_conflicts',[('run_id',r)]),('workspace_cleanup_plans',[('run_id',r)]),
        ('workspace_reservations',[('holder_task_id',t),('parent_run_id',r)]),
        ('agent_credentials',[('run_id',r),('attempt_id',a)]),
        ('workflow_integrations',[('run_id',r)]),('workflow_artifacts',[('run_id',r)]),
        ('workflow_turns',[('run_id',r)]),('workflow_children',[('parent_run_id',r),('child_task_id',t)]),
        ('workflow_routes',[('run_id',r)]),('router_claims',[('task_id',t),('attempt_id',a)]),
        ('router_dispatches',[('decision_id',d),('task_id',t)]),('router_requests',[('decision_id',d)]),
        ('decision_requests',[('decision_id',d),('task_id',t),('attempt_id',a)]),('evaluation_decisions',[('decision_id',d)])]:
        remove(table,*conditions)
    for table in ('messages','artifacts','resource_claims','attempt_activity','attempt_reminders'):
        remove(table,('task_id',t),('attempt_id',a))
    for table in ('attempt_process_identities','attempt_interruptions','attempt_shutdown_evidence','execution_issues'):
        remove(table,('attempt_id',a))
    connection.execute("DELETE FROM events WHERE task_id IN (SELECT value FROM json_each(?))"
                       " OR attempt_id IN (SELECT value FROM json_each(?)) OR seq IN (SELECT value FROM json_each(?))"
                       " OR ((kind LIKE 'decision.%' OR kind LIKE 'router.%') AND json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.decisionId') IN (SELECT value FROM json_each(?)))"
                       " OR (kind LIKE 'router.%' AND json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.requestId') IN (SELECT value FROM json_each(?)))"
                       " OR (kind LIKE 'macro_task.%' AND json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.macroTaskId')=?)",
                       (_ids(t),_ids(a),_ids(outcome_events),_ids(d),_ids(scope.requests),
                        deletion['target_id'] if deletion['target_kind']=='macro-task' else None))
    remove('attempts',('attempt_id',a))
    remove('workflow_runs',('run_id',r))
    remove('tasks',('task_id',t))
    if deletion['target_kind'] == 'macro-task':
        connection.execute('DELETE FROM macro_tasks WHERE macro_task_id=?',(deletion['target_id'],))
    projects.prune_if_unreferenced(connection, project_ids)
    connection.execute('DELETE FROM macro_task_deletions WHERE deletion_id=?',(deletion['deletion_id'],))
    return store._head_of(connection)


def _physical_failure(error, phase):
    os_error = error if isinstance(error, OSError) else error.__cause__
    if not isinstance(os_error, OSError):
        os_error = None
    path = os_error.filename if os_error is not None else None
    if isinstance(error, BoardError):
        path = path if path is not None else error.details.get('path')
        cause = (error.details['reason'] if error.code == 'NOT_READY' and 'reason' in error.details
                 else reason('macro-task-deletion.physical-failed', errorCode=error.code))
        message = error.message
    else:
        cause = reason('macro-task-deletion.physical-failed', errorCode='IO_ERROR')
        message = str(error)
    return {'objectKind': phase, 'path': os.fsdecode(path) if path is not None else None,
            'reason': cause, 'error': {'message': message,
                'errno': os_error.errno if os_error is not None else None,
                'winerror': getattr(os_error, 'winerror', None)}}


def _skipped_failures(skipped, phase):
    return [{'objectKind': phase, 'path': item['path'], 'reason': item['reason'], 'error': None}
            for item in skipped]


def delete(store, target: str, micro_task_count: int) -> dict:
    """Fence a confirmed complete set, delete owned content, then remove its rows."""
    if isinstance(micro_task_count,bool) or not isinstance(micro_task_count,int) or micro_task_count < 0:
        raise BoardError('INVALID_ARGUMENT','microTaskCount must be a nonnegative integer')
    with storage.locked(store):
        # Both marking and cleanup admission take the allocation lock before
        # opening their short write transaction. No restore can cross this fence.
        with store.db.read() as connection:
            initial_scope = _collect(connection,target,store.directory)
        from . import workspace
        with ExitStack() as locks:
            for identifier in sorted({m['workspace_id'] for m in initial_scope.manifests}):
                directory = workspace.allocation_lock_directory(store.directory,identifier)
                if directory is not None:
                    try:
                        locks.enter_context(workspace._lock(directory,create=False))
                    except BoardError as error:
                        if error.code == 'WORKSPACE_BUSY':
                            with store.db.read() as connection:
                                if _busy(connection,initial_scope):
                                    raise BoardError('MACRO_TASK_BUSY','A workspace cleanup is applying',macroTaskId=target) from error
                        raise
            with store.db.write() as connection:
                deletion = _record(connection,target)
                duplicate = deletion is not None
                scope = _collect(connection,target,store.directory)
                scope.sessions = worker_sessions.deletable_sessions(store,scope.tasks,connection=connection)
                if deletion is None:
                    if len(scope.runs) != micro_task_count:
                        raise BoardError('REVISION_CONFLICT','The micro task count changed',microTaskCount=len(scope.runs))
                    if _busy(connection,scope):
                        raise BoardError('MACRO_TASK_BUSY','A workspace cleanup is applying',macroTaskId=target)
                    blockers = _blockers(store,connection,scope)
                    if blockers:
                        raise BoardError('MACRO_TASK_NOT_SETTLED','The macro task is not settled',macroTaskId=target,
                                         blockerTotal=len(blockers),blockers=blockers[:50])
                    _require_unused_storage(store, connection, scope, target)
                    unavailable = _unavailable_harnesses(connection,scope)
                    if unavailable:
                        raise BoardError('MACRO_TASK_HARNESS_UNAVAILABLE','A harness with stored sessions is unavailable',
                                         macroTaskId=target,harnesses=unavailable)
                    kind,identifier = _target(target)
                    connection.execute('INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id)'
                                       ' VALUES(?,?,?)',('del-'+uuid.uuid4().hex,kind,identifier))
                    deletion = _record(connection,target)
                else:
                    _require_unused_storage(store, connection, scope, target)
        for phase,operation in (('native-session',_delete_sessions),('workspace',_delete_workspaces),
                                ('private-root',_delete_private_roots),
                                ('session-records',lambda store, scope: _delete_session_group(store, target))):
            try:
                skipped = operation(store,scope)
            except (BoardError,OSError) as error:
                return {**_summary(deletion,scope,duplicate=duplicate),
                        'failures': [*_skipped_failures(getattr(error, 'deletion_skipped', ()), phase),
                                     _physical_failure(error, phase)]}
            if skipped:
                return {**_summary(deletion,scope,duplicate=duplicate),
                        'failures': _skipped_failures(skipped, phase)}
        with store.db.write() as connection:
            current = _record(connection,target)
            assert current is not None
            # Physical deletion has already removed the files behind current
            # input/record references. Recheck row ownership without reopening
            # those paths, then use the facts proved before the physical phase.
            current_scope = _collect(connection,target,store.directory,read_evidence=False)
            assert (current_scope.runs,current_scope.tasks,current_scope.attempts,current_scope.decisions) == (
                    scope.runs,scope.tasks,scope.attempts,scope.decisions), 'Deletion scope grew after its fence'
            current_scope.manifests = scope.manifests
            current_scope.artifacts = scope.artifacts
            current_scope.sessions = scope.sessions
            _require_unused_storage(store, connection, current_scope, target)
            head = _delete_rows(store,connection,scope,current)
            result = {**_summary(current,scope,duplicate=duplicate), 'state':'deleted', 'deletedAt':store.now()}
        store._notify(head, changed_tasks=frozenset(scope.tasks))
        return result
