"""Service reclamation from terminal tasks and physical allocation ownership."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from ... import private_dirs
from ...errors import BoardError
from . import host_conclusions, workspace

NATIVE_RETENTION = timedelta(days=3)


def workspace_candidates(store) -> list[str]:
    """Rebuild the work list from durable facts, including after a service restart."""
    result = []
    with store.db.read() as connection:
        for run in connection.execute(
            "SELECT r.* FROM workflow_runs r WHERE r.state IN ('accepted','concluded')"
            " AND NOT EXISTS (SELECT 1 FROM workspace_cleanup_plans p WHERE p.run_id=r.run_id"
            " AND p.checkout_id=json_extract(r.workspace_manifest_json,'$.checkoutId') AND p.state='applied')"
        ):
            manifest = json.loads(run['workspace_manifest_json'])
            provenance = [manifest, *store.workflow._allocation_provenance(connection, run)]
            if any(item.get('kind') == 'worktree' and item.get('checkoutId') == manifest['checkoutId']
                   and item.get('repositoryId') == manifest['repositoryId'] for item in provenance):
                result.append(run['run_id'])
    return result


def _finished_at(connection, run):
    """A failed/cancelled borrower ends with the terminal parent it borrowed from."""
    if run['state'] == 'accepted':
        return connection.execute('SELECT accepted_at FROM tasks WHERE task_id=?', (run['run_id'],)).fetchone()[0]
    if run['state'] == 'concluded':
        return host_conclusions.current(connection, run)['created_at']
    if run['state'] not in ('failed', 'cancelled'):
        return None
    parent = connection.execute(
        "SELECT p.* FROM workflow_children c JOIN workflow_runs p ON p.run_id=c.parent_run_id"
        " WHERE c.child_task_id=?"
        " AND json_extract(p.workspace_manifest_json,'$.checkoutId')=json_extract(?, '$.checkoutId')"
        " AND json_extract(p.workspace_manifest_json,'$.repositoryId')=json_extract(?, '$.repositoryId')",
        (run['run_id'], run['workspace_manifest_json'], run['workspace_manifest_json']),
    ).fetchone()
    return _finished_at(connection, parent) if parent is not None else None


def reclaim_native(store) -> None:
    now = datetime.fromisoformat(store.now())
    with store.db.read() as connection:
        micro_tasks = []
        for run in connection.execute('SELECT * FROM workflow_runs'):
            finished = _finished_at(connection, run)
            if finished is None or now - datetime.fromisoformat(finished) < NATIVE_RETENTION:
                continue
            shutdown = store.workflow.shutdown_summary(connection, run['run_id'])
            if shutdown['selfConfirmed'] and shutdown['descendantsConfirmed']:
                micro_tasks.extend([run['run_id'], *(row['task_id'] for row in
                                                    store.workflow._routing_tasks(connection, run['run_id']))])
    failure = None
    for micro_task in dict.fromkeys(micro_tasks):
        for harness in private_dirs.ADAPTERS:
            try:
                private_dirs.remove_tree(private_dirs.micro_task_root(store.directory, harness, micro_task))
            except (BoardError, OSError) as error:
                if failure is None:
                    failure = error
    if failure is not None:
        raise failure


def _request_run(connection, request_id):
    return connection.execute(
        "SELECT t.task_id,r.state FROM tasks t LEFT JOIN workflow_runs r ON r.run_id=t.task_id"
        " WHERE t.request_id=? OR EXISTS (SELECT 1 FROM workflow_continuations c"
        " WHERE c.run_id=r.run_id AND c.run_id || ':' || c.continuation_id=?)",
        (request_id, request_id),
    ).fetchone()


def reclaim_allocation(store, directory: Path) -> None:
    """Remove an abandoned shell or an allocation whose requesting task is gone."""
    from .macro_task_deletion_workspace import _delete_refs, _fixed_refs, _record_proven, _repository_skip

    if private_dirs.linked(directory) or workspace.allocation_lock_directory(store.directory, directory.name) != directory:
        return
    with workspace._lock(directory, create=False):
        request = workspace._record(directory / 'request.json')
        if request is None:
            return
        request_id = request.get('requestId')
        if not isinstance(request_id, str) or not request_id or '\0' in request_id:
            raise BoardError('WORKSPACE_UNSAFE', 'Allocation request has no stable identity')
        expected = 'ws-' + workspace._sha(workspace._json([str(store.directory), request_id]))[:32]
        if directory.name != expected:
            raise BoardError('WORKSPACE_UNSAFE', 'Allocation request belongs to another directory')
        manifest = workspace._record(directory / 'manifest.json')
        with store.db.read() as connection:
            run = _request_run(connection, request_id)
            if run is not None and (manifest is not None or run['state'] not in ('accepted', 'concluded')):
                return
        if manifest is None:
            if {item.name for item in directory.iterdir()} <= {'.lock', 'request.json'}:
                private_dirs.remove_tree(directory)
            return
        workspace._validate_manifest(manifest)
        if manifest['workspaceId'] != directory.name or not _record_proven(store.directory, manifest):
            raise BoardError('WORKSPACE_UNSAFE', 'Allocation records do not prove ownership')
        repository_skip = _repository_skip(manifest, 'allocation', str(directory))
        if repository_skip is not None:
            return
        checkout = directory / 'checkout'
        if workspace._path_present(checkout):
            proof = workspace.deletion_inspect(store.directory, manifest)
            if proof['reasons']:
                return
            from .checkout_processes import require_idle_checkout
            require_idle_checkout(checkout)
            workspace.cleanup_remove(store.directory, manifest, proof=proof)
        if _delete_refs(Path(manifest['snapshot']['repositoryPath']), directory.name, _fixed_refs(manifest)):
            return
        private_dirs.remove_tree(directory)


def reclaim(store) -> None:
    """One maintenance pass; expected retention never stops other candidates."""
    failed = False
    for run_id in workspace_candidates(store):
        try:
            result = store.workflow._reclaim_checkout(run_id=run_id)
            if result.get('detail'):
                raise BoardError(result['reasons'][0], result['detail'])
        except (BoardError, OSError) as error:
            if isinstance(error, BoardError) and error.code in ('NOT_READY', 'TASK_DELETING', 'WORKSPACE_BUSY'):
                continue
            failed = True
            store.record_service_issue('workspace-cleanup', error)
    if not failed:
        store.clear_service_issue('workspace-cleanup')
    try:
        reclaim_native(store)
    except (BoardError, OSError) as error:
        store.record_service_issue('native-reclamation', error)
    else:
        store.clear_service_issue('native-reclamation')
    from .storage import locked
    try:
        with locked(store):
            root = store.directory / 'workspaces'
            if root.is_dir():
                failure = None
                for directory in sorted(root.iterdir()):
                    try:
                        reclaim_allocation(store, directory)
                    except (BoardError, OSError) as error:
                        if isinstance(error, BoardError) and error.code == 'WORKSPACE_BUSY':
                            continue
                        if failure is None:
                            failure = error
                if failure is None:
                    store.clear_service_issue('allocation-reclamation')
                else:
                    store.record_service_issue('allocation-reclamation', failure)
    except BoardError as error:
        if error.code != 'STORAGE_BUSY':
            raise
