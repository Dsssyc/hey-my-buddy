"""Settled macro-task deletion: durable fence, owned content, atomic row removal.

The caller supplies its BoardStore; no public transport operation is registered
here. Database rows remain the ownership evidence until physical work completes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import re
import uuid

from ... import private_dirs
from ...errors import BoardError
from ...home import control_file_path, submission_file_path
from ...protocol import schemas
from ...protocol.reasons import reason
from . import micro_task, storage, worker_sessions
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


def _collect(connection, target):
    kind, identifier = _target(target)
    if kind == 'macro-task':
        if connection.execute('SELECT 1 FROM macro_tasks WHERE macro_task_id=?', (identifier,)).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown macro task', macroTaskId=target)
        seed = 'SELECT run_id FROM workflow_runs WHERE macro_task_id=?'
        seed_id = identifier
    else:
        root = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (identifier[4:],)).fetchone()
        if (root is None or root['macro_task_id'] is not None or connection.execute(
                'SELECT 1 FROM workflow_children WHERE child_task_id=?',(identifier[4:],)).fetchone() is not None
                or connection.execute('SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=?',
                                      (identifier[4:],)).fetchone() is not None):
            raise BoardError('NOT_FOUND', 'Unknown run group', macroTaskId=target)
        seed, seed_id = 'SELECT run_id FROM workflow_runs WHERE run_id=?', identifier[4:]
    runs = {row[0] for row in connection.execute(f'''
        WITH RECURSIVE members(run_id) AS (
            {seed}
            UNION SELECT c.child_task_id FROM workflow_children c JOIN members m ON c.parent_run_id=m.run_id
            UNION SELECT l.owned_run_id FROM workflow_lead_tasks l JOIN members m ON l.lead_run_id=m.run_id
        ) SELECT run_id FROM members''', (seed_id,))}
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
    artifact_rows = _rows(connection, 'workflow_artifacts', 'run_id', runs)
    artifacts = [json.loads(row['manifest_json']) for row in artifact_rows]
    manifests = []
    def add(document):
        if document is None:
            return
        assert isinstance(document, dict), 'Workspace evidence is not an object'
        candidate = document.get('executionWorkspace', document)
        if (candidate.get('kind') in ('worktree','existing')
                and 'workspaceId' in candidate and 'snapshot' in candidate):
            manifests.append(candidate)
    for row in _rows(connection, 'workflow_children', 'parent_run_id', runs):
        if row['workspace_manifest_json']:
            add(json.loads(row['workspace_manifest_json']))
    for row in run_rows:
        if row['workspace_manifest_json']:
            add(json.loads(row['workspace_manifest_json']))
    for row in turns:
        add(json.loads(row['input_json']))
    for row in _rows(connection, 'workflow_continuations', 'run_id', runs):
        if row['workspace_manifest_json']:
            add(json.loads(row['workspace_manifest_json']))
    for document in artifacts:
        add(document)
    request_ids = {row['request_id'] for row in _rows(connection, 'decision_requests', 'decision_id', decisions)}
    return Scope(runs, tasks, {r['attempt_id'] for r in attempt_rows}, decisions,
                 {r['turn_id'] for r in turns}, request_ids, run_rows, task_rows,
                 attempt_rows, manifests, artifacts)


def _blockers(store, connection, scope):
    runs = {row['run_id']: row for row in scope.run_rows}
    tasks = {row['task_id']:row for row in scope.task_rows}
    reasons = {}
    def block(task_id, code, attempt_ids=()):
        task = tasks[task_id]
        run = runs.get(task_id)
        key = (task_id, code)
        reasons[key] = {'runId': task_id if run else None, 'taskId': task_id,
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
    workspace_ids = {m['workspaceId'] for m in scope.manifests}
    from . import workspace
    allocations = [workspace.resolve_allocation(store.directory,m,scope.manifests)
                   for m in scope.manifests if m.get('kind') == 'worktree']
    checkouts = {(a['repositoryId'],a['checkoutId']) for a in allocations if a is not None}
    holders = {}

    def retain(kind, owner):
        if owner not in scope.tasks:
            holders.setdefault(kind,set()).add(owner)

    for table,column,owner,nested in (
            ('workflow_runs','workspace_manifest_json','run_id',False),
            ('workflow_artifacts','manifest_json','run_id',False),
            ('workflow_continuations','workspace_manifest_json','run_id',False),
            ('workflow_children','workspace_manifest_json','child_task_id',False),
            ('workflow_turns','input_json','run_id',True)):
        valid = f'CASE WHEN json_valid({column}) THEN {column} END'
        prefix = '$.executionWorkspace' if nested else '$'
        wid = f"json_extract({valid},'{prefix}.workspaceId')"
        rid = f"json_extract({valid},'{prefix}.repositoryId')"
        cid = f"json_extract({valid},'{prefix}.checkoutId')"
        predicates = [f'{wid} IN (SELECT value FROM json_each(?))']
        args = [_ids(workspace_ids)]
        for repository_id,checkout_id in sorted(checkouts):
            predicates.append(f'({rid}=? AND {cid}=?)')
            args.extend((repository_id,checkout_id))
        query = f'SELECT {owner} AS owner FROM {table} WHERE (' + ' OR '.join(predicates) + ')'
        for row in connection.execute(query,args):
            retain('workspace',row['owner'])
    for row in connection.execute(
            "SELECT holder_task_id,workspace_id,repository_id,checkout_id FROM workspace_reservations WHERE state='held'"):
        if row['workspace_id'] in workspace_ids or (row['repository_id'],row['checkout_id']) in checkouts:
            retain('checkout',row['holder_task_id'])
    session_ids = {row['sessionId'] for row in scope.sessions}
    for row in worker_sessions.session_references(connection,session_ids):
        retain('native-session',row['taskId'])
    return [{'objectKind':kind,'holderRunIds':sorted(ids)} for kind,ids in sorted(holders.items())]


def _unavailable_harnesses(connection, scope):
    """A session in a harness user store needs that harness for its deletion."""
    involved = {}
    for session in scope.sessions:
        involved.setdefault(session['harness'], set()).add(session['taskId'])
    unavailable = []
    for harness in sorted(involved):
        health = read_health(connection, harness)
        if not health['available']:
            unavailable.append({'harness': harness, 'reason': health['reason'],
                                'microTaskIds': sorted(involved[harness])})
    return unavailable


def _busy(connection, scope):
    return any(row['state'] == 'applying' for row in _rows(connection, 'workspace_cleanup_plans', 'run_id', scope.runs))


def _summary(row, *, duplicate):
    return {'deletionId':row['deletion_id'], 'macroTaskId':row['target_id'], 'state':row['state'],
            'deletedAt':row['deleted_at'], 'microTaskCount':row['micro_task_count'], 'duplicate':duplicate}


def preview(store, target: str) -> dict:
    """Return confirmation counts and at most fifty blockers in one snapshot."""
    with store.db.read() as connection:
        deletion = _record(connection, target)
        if deletion is not None and deletion['state'] == 'deleted':
            return {'macroTaskId':target, 'microTaskCount':deletion['micro_task_count'],
                    'unconcludedFailureCount':0, 'blockerTotal':0, 'blockers':[],
                    'backupRetention':'until-backup-rotation', 'deletionState':'deleted'}
        scope = _collect(connection, target)
        if _busy(connection, scope):
            raise BoardError('MACRO_TASK_BUSY', 'A workspace cleanup is applying', macroTaskId=target)
        scope.sessions = worker_sessions.deletable_sessions(store,scope.tasks,connection=connection)
        shared = _storage_in_use(store,connection,scope)
        if shared:
            raise BoardError('MACRO_TASK_STORAGE_IN_USE', 'Storage is retained by another micro task', macroTaskId=target, objects=shared)
        unavailable = _unavailable_harnesses(connection, scope)
        if unavailable:
            raise BoardError('MACRO_TASK_HARNESS_UNAVAILABLE', 'A harness with stored sessions is unavailable',
                             macroTaskId=target, harnesses=unavailable)
        blockers = _blockers(store, connection, scope)
        concluded = {r['run_id'] for r in _rows(connection, 'workflow_host_conclusions', 'run_id', scope.runs)}
        tasks = {r['task_id']:r for r in scope.task_rows}
        failures = sum(r['state'] in ('failed','cancelled') and not tasks[r['run_id']]['accepted_at']
                       and r['run_id'] not in concluded for r in scope.run_rows)
        return {'macroTaskId':target, 'microTaskCount':len(scope.runs), 'unconcludedFailureCount':failures,
                'blockerTotal':len(blockers), 'blockers':blockers[:50],
                'backupRetention':'until-backup-rotation', 'deletionState':deletion['state'] if deletion else None}


def _delete_sessions(store, scope):
    from ...buddy.harnesses.registry import adapter
    grouped = {}
    for session in scope.sessions:
        grouped.setdefault(session['harness'], set()).add(session['sessionId'])
    for harness, ids in grouped.items():
        adapter(harness).delete_sessions(sorted(ids))
    return []


def _delete_workspaces(store, scope):
    from .macro_task_deletion_workspace import delete_workspaces
    return delete_workspaces(store, scope.manifests, scope.artifacts)


def _remove_owned_tree(path):
    if (private_dirs.linked_component(path) is not None
            or path.exists() and not path.is_dir()):
        return [{'objectKind':'private-root', 'path':str(path), 'reason':reason('macro-task-deletion.owner-unproven')}]
    private_dirs.remove_tree(path)
    return []


def _delete_private_roots(store, scope):
    skipped = []
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
            for attempt in scope.attempt_rows:
                if attempt['task_id'] == task_id:
                    inquiry = root / 'attempts' / attempt['attempt_id'] / 'inquiry.json'
                    if not private_dirs.linked(root) and not any(private_dirs.linked(p) for p in inquiry.parents):
                        private_dirs.cleanup_inquiry_fallback(inquiry, attempt['attempt_id'])
            skipped.extend(_remove_owned_tree(root))
            skipped.extend(_remove_owned_tree(store.directory / 'harnesses' / harness / hashlib.sha256(task_id.encode()).hexdigest()))
        skipped.extend(_remove_owned_tree(store.directory / 'attempts' / task_id))
    return skipped


def _delete_cli_files(store, scope):
    skipped = []

    def safe_file(path):
        return private_dirs.linked_component(path) is None and (not path.exists() or path.is_file())

    def remove(path):
        if not safe_file(path):
            skipped.append({'objectKind':'cli-file','path':str(path),'reason':reason('macro-task-deletion.owner-unproven')})
        else:
            path.unlink(missing_ok=True)

    def temporary_matches(path, **binding):
        if not safe_file(path):
            return False
        try:
            document = json.loads(path.read_text())
        except (ValueError,UnicodeError):
            return False
        return isinstance(document,dict) and all(document.get(key) == value for key,value in binding.items())

    controls = store.directory / 'controls'
    if controls.is_dir() and private_dirs.linked_component(controls) is None:
        files = list(controls.iterdir())
        for run_id in scope.runs:
            pattern = re.compile(re.escape(run_id) + r'\.g([1-9][0-9]*)\.json\Z')
            temporary = re.compile(r'\.' + re.escape(run_id) + r'\.g([1-9][0-9]*)\.json\..+\.tmp\Z')
            for path in files:
                match = pattern.fullmatch(path.name)
                if match:
                    remove(control_file_path(store.directory,run_id,int(match[1])))
                else:
                    match = temporary.fullmatch(path.name)
                    if match and temporary_matches(path,runId=run_id,ownerGeneration=int(match[1])):
                        remove(path)
    submissions = store.directory / 'submissions'
    for task in scope.task_rows:
        path = submission_file_path(store.directory,task['request_id'])
        remove(path)
        if submissions.is_dir() and private_dirs.linked_component(submissions) is None:
            for temporary in submissions.glob('.' + path.stem + '.*.tmp'):
                if temporary_matches(temporary,requestId=task['request_id']):
                    remove(temporary)
    return skipped


def _delete_rows(store, connection, scope, deletion):
    r,t,a,d,u = scope.runs,scope.tasks,scope.attempts,scope.decisions,scope.turns
    bindings = {'runId':r, 'targetRunId':r,
                'requestId':{x['request_id'] for x in _rows(connection,'workflow_requests','run_id',r)},
                'continuationId':{x['continuation_id'] for x in _rows(connection,'workflow_continuations','run_id',r)},
                'planId':{x['plan_id'] for x in _rows(connection,'workspace_cleanup_plans','run_id',r)},
                'artifactId':{x['artifact_id'] for x in _rows(connection,'workflow_artifacts','run_id',r)},
                'integrationId':{x['integration_id'] for x in _rows(connection,'workflow_integrations','run_id',r)},
                'conclusionId':{x['conclusion_id'] for x in _rows(connection,'workflow_host_conclusions','run_id',r)},
                'conflictId':{x['conflict_id'] for x in _rows(connection,'workflow_workspace_conflicts','run_id',r)}}
    bindings['macroTaskId'] = {deletion['target_id']}
    commands = set()
    for table,column in (('workflow_requests','decision_command_id'),('workflow_children','decision_command_id'),
                         ('workflow_continuations','command_id'),('workflow_scope_versions','command_id'),
                         ('workflow_workspace_conflicts','command_id'),('workflow_integrations','command_id'),
                         ('workspace_cleanup_plans','command_id'),('workflow_host_conclusions','command_id')):
        owner = 'parent_run_id' if table == 'workflow_children' else 'run_id'
        commands.update(x[column] for x in _rows(connection,table,owner,r) if x[column])
    clauses,args = [],[]
    for column,values in (('task_id',t),('attempt_id',a),('command_id',commands)):
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
    commands.update(row[0] for row in connection.execute(
        'SELECT command_id FROM commands WHERE deletion_id IS NULL AND (' + ' OR '.join(clauses) + ')',args))
    clause,args = _in('command_id',commands)
    connection.execute(f"UPDATE commands SET task_id=NULL,attempt_id=NULL,response_json='{{}}',deletion_id=? WHERE {clause}",
                       [deletion['deletion_id'],*args])
    clause,args = _in('retry_consumed_by',d)
    connection.execute(f'UPDATE quota_current SET retry_consumed_by=NULL WHERE {clause}',args)
    def remove(table, *conditions):
        clauses,args = [],[]
        for column,values in conditions:
            clause,params = _in(column,values)
            clauses.append(clause);args.extend(params)
        connection.execute(f'DELETE FROM {table} WHERE ' + ' OR '.join(clauses),args)
    # Facts refer to events and attempts; preserve their dependency order.
    outcome_events = {x['event_seq'] for x in connection.execute(
        'SELECT event_seq FROM router_outcomes WHERE task_id IN (SELECT value FROM json_each(?))'
        ' OR attempt_id IN (SELECT value FROM json_each(?)) OR (plane=\'work\' AND request_id IN (SELECT value FROM json_each(?)))',
        (_ids(t),_ids(a),_ids(scope.requests)))}
    remove('router_outcomes',('event_seq',outcome_events))
    for table,conditions in [
        ('workflow_host_conclusions',[('run_id',r),('attempt_id',a)]),
        ('workflow_lead_tasks',[('lead_run_id',r),('owned_run_id',r),('lead_turn_id',u)]),
        ('workflow_fixes',[('run_id',r),('fixed_run_id',r)]),
        ('workflow_requests',[('run_id',r)]),('workflow_continuations',[('run_id',r)]),
        ('workflow_suggestions',[('run_id',r),('attempt_id',a)]),('workflow_scope_versions',[('run_id',r)]),
        ('workflow_workspace_conflicts',[('run_id',r)]),('workspace_cleanup_plans',[('run_id',r)]),
        ('workspace_reservations',[('holder_task_id',t),('parent_run_id',r)]),
        ('agent_credentials',[('run_id',r),('task_id',t),('attempt_id',a)]),
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
                       " OR (kind LIKE 'router.%' AND json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.requestId') IN (SELECT value FROM json_each(?)))",
                       (_ids(t),_ids(a),_ids(outcome_events),_ids(d),_ids(scope.requests)))
    remove('attempts',('attempt_id',a))
    remove('workflow_runs',('run_id',r))
    remove('tasks',('task_id',t))
    if deletion['target_kind'] == 'macro-task':
        connection.execute('DELETE FROM macro_tasks WHERE macro_task_id=?',(deletion['target_id'],))
    connection.execute("UPDATE macro_task_deletions SET state='deleted',deleted_at=? WHERE deletion_id=?",
                       (store.now(),deletion['deletion_id']))
    return store._append_event(connection,'macro_task.deleted',payload={'macroTaskId':deletion['target_id'],
                                                                      'microTaskCount':deletion['micro_task_count']})


def delete(store, target: str, micro_task_count: int) -> dict:
    """Fence a confirmed complete set, delete owned content, then remove its rows."""
    if isinstance(micro_task_count,bool) or not isinstance(micro_task_count,int) or micro_task_count < 0:
        raise BoardError('INVALID_ARGUMENT','microTaskCount must be a nonnegative integer')
    with storage.locked(store):
        # Both marking and cleanup admission take the allocation lock before
        # opening their short write transaction. No restore can cross this fence.
        with store.db.read() as connection:
            previous = _record(connection,target)
            if previous is not None and previous['state'] == 'deleted':
                return _summary(previous,duplicate=True)
            initial_scope = _collect(connection,target)
        from . import workspace
        with ExitStack() as locks:
            for identifier in sorted({m['workspaceId'] for m in initial_scope.manifests}):
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
                if deletion is not None and deletion['state'] == 'deleted':
                    return _summary(deletion,duplicate=True)
                scope = _collect(connection,target)
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
                    shared = _storage_in_use(store,connection,scope)
                    if shared:
                        raise BoardError('MACRO_TASK_STORAGE_IN_USE','Storage is retained by another micro task',macroTaskId=target,objects=shared)
                    unavailable = _unavailable_harnesses(connection,scope)
                    if unavailable:
                        raise BoardError('MACRO_TASK_HARNESS_UNAVAILABLE','A harness with stored sessions is unavailable',
                                         macroTaskId=target,harnesses=unavailable)
                    kind,identifier = _target(target)
                    connection.execute('INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id,state,started_at,micro_task_count)'
                                       " VALUES(?,?,?,'deleting',?,?)",('del-'+uuid.uuid4().hex,kind,identifier,store.now(),len(scope.runs)))
                    deletion = _record(connection,target)
        skipped = []
        for phase,operation in (('native-session',_delete_sessions),('workspace',_delete_workspaces),
                                ('private-root',_delete_private_roots),('cli-file',_delete_cli_files)):
            try:
                skipped.extend(operation(store,scope))
            except (BoardError,OSError) as error:
                return {**_summary(deletion,duplicate=duplicate),
                        'reason':reason('macro-task-deletion.physical-failed',
                                        errorCode=error.code if isinstance(error,BoardError) else 'IO_ERROR'),
                        'objectKind':phase}
        with store.db.write() as connection:
            current = _record(connection,target)
            assert current['state'] == 'deleting'
            current_scope = _collect(connection,target)
            assert (current_scope.runs,current_scope.tasks,current_scope.attempts,current_scope.decisions) == (
                    scope.runs,scope.tasks,scope.attempts,scope.decisions), 'Deletion scope grew after its fence'
            head = _delete_rows(store,connection,scope,current)
            result = _summary(_record(connection,target),duplicate=duplicate)
        store._notify(head)
        if skipped:
            result['skipped'] = skipped
        return result
