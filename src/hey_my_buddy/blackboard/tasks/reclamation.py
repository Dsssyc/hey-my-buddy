"""Service reclamation from terminal tasks and physical allocation ownership."""
from __future__ import annotations

from contextlib import ExitStack, closing
from datetime import datetime, timedelta
import hashlib
import threading
from pathlib import Path

from ... import private_dirs
from ...errors import BoardError
from . import host_conclusions, workspace

NATIVE_RETENTION = timedelta(days=3)


def workspace_candidates(store, task_ids=None) -> list[str]:
    """Cheap durable selection; existing submissions never need provenance work."""
    query = (
        "SELECT r.run_id FROM workflow_runs r WHERE r.state IN ('accepted','concluded')"
        " AND json_extract(r.execution_workspace_json,'$.kind')='worktree'"
        " AND NOT EXISTS (SELECT 1 FROM workspace_cleanup_plans p WHERE p.run_id=r.run_id"
        " AND p.checkout_id=json_extract(r.workspace_manifest_json,'$.checkoutId') AND p.state='applied')"
    )
    with store.db.read() as connection:
        if task_ids is None:
            return [row['run_id'] for row in connection.execute(query)]
        return [task_id for task_id in task_ids if connection.execute(query + ' AND r.run_id=?', (task_id,)).fetchone()]


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


def native_roots(store) -> list[Path]:
    """Only roots still on disk can require native reclamation or inspection."""
    roots = []
    for harness in private_dirs.ADAPTERS:
        parent = store.directory / 'harnesses' / harness / 'micro-tasks'
        if parent.is_dir():
            roots.extend(parent.iterdir())
    return roots


def native_task_ids(store) -> dict[str, str]:
    with store.db.read() as connection:
        return {hashlib.sha256(row['task_id'].encode()).hexdigest(): row['task_id']
                for row in connection.execute('SELECT task_id FROM tasks')}


def native_owner(connection, task_id):
    run = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (task_id,)).fetchone()
    if run is not None:
        return run
    return connection.execute(
        "SELECT r.* FROM router_dispatches d JOIN workflow_routes w ON w.decision_id=d.decision_id"
        " JOIN workflow_runs r ON r.run_id=w.run_id WHERE d.task_id=?", (task_id,),
    ).fetchone()


def native_reasons(store, connection, run, now) -> list[str]:
    if run is None:
        return ['owner-unproven']
    finished = _finished_at(connection, run)
    reasons = (['continuation-supported'] if finished is None else
               ['grace-period'] if now - datetime.fromisoformat(finished) < NATIVE_RETENTION else [])
    shutdown = store.workflow.shutdown_summary(connection, run['run_id'])
    if not (shutdown['selfConfirmed'] and shutdown['descendantsConfirmed']):
        reasons.append('shutdown-unconfirmed')
    return reasons


def reclaim_native(store, *, task_ids=None, stopping=lambda: False) -> None:
    from .storage import locked
    roots = native_roots(store)
    if not roots:
        return
    task_ids = native_task_ids(store) if task_ids is None else task_ids
    owned = {}
    with store.db.read() as connection:
        for path in roots:
            task_id = task_ids.get(path.name)
            run = native_owner(connection, task_id) if task_id is not None else None
            if run is not None:
                owned.setdefault(run['run_id'], []).append(path)
    failure = None
    for run_id, paths in owned.items():
        if stopping():
            break
        try:
            # The same lock spans macro deletion's physical and row phases.
            # Re-read terminal/stop facts inside it, once for this native object.
            with locked(store):
                with store.db.read() as connection:
                    run = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
                    finished = _finished_at(connection, run) if run is not None else None
                    if finished is None or datetime.fromisoformat(store.now()) - datetime.fromisoformat(finished) < NATIVE_RETENTION:
                        continue
                    shutdown = store.workflow.shutdown_summary(connection, run_id)
                    if not (shutdown['selfConfirmed'] and shutdown['descendantsConfirmed']):
                        continue
                for path in paths:
                    private_dirs.remove_tree(path)
        except (BoardError, OSError) as error:
            if isinstance(error, BoardError) and error.code == 'STORAGE_BUSY':
                continue
            if failure is None:
                failure = error
    if failure is not None:
        raise failure


def _request_run(connection, request_id):
    task = connection.execute(
        "SELECT t.task_id,r.state FROM tasks t LEFT JOIN workflow_runs r ON r.run_id=t.task_id WHERE t.request_id=?",
        (request_id,),
    ).fetchone()
    if task is not None:
        return task
    run_id, separator, continuation_id = request_id.rpartition(':')
    if not separator:
        return None
    return connection.execute(
        "SELECT t.task_id,r.state FROM workflow_continuations c JOIN workflow_runs r ON r.run_id=c.run_id"
        " JOIN tasks t ON t.task_id=r.run_id WHERE c.continuation_id=? AND c.run_id=?",
        (continuation_id, run_id),
    ).fetchone()


def _allocation_refs(directory, manifest):
    from .macro_task_deletion_workspace import _artifact_refs, _fixed_refs
    expected = _fixed_refs(manifest)
    outputs = directory / 'outputs'
    if not outputs.exists() or private_dirs.linked(outputs):
        return expected
    names = ('output.json', 'pending.json', 'abandoned.json', 'abandoned-pending.json',
             'resolve-restore.json', 'resolve-restore-pending.json', 'resolve-adopt.json', 'resolve-adopt-pending.json')
    for output in outputs.iterdir():
        if private_dirs.linked(output) or not output.is_dir():
            continue
        for name in names:
            artifact = workspace._record(output / name)
            if artifact is None:
                continue
            if not isinstance(artifact, dict):
                raise BoardError('WORKSPACE_CONFLICT', 'An allocation artifact is not a record')
            for ref, oid in _artifact_refs(artifact, {manifest['workspaceId']: manifest}).items():
                if ref in expected and expected[ref] != oid:
                    raise BoardError('WORKSPACE_CONFLICT', 'Allocation records disagree on a fixed reference')
                expected[ref] = oid
    return expected


def allocation_record(store, directory):
    """Read untrusted allocation material once; no Git or database transaction."""
    from .macro_task_deletion_workspace import _record_proven
    result = {'directory': directory, 'manifest': None, 'requestId': None,
              'owner': None, 'tasks': set(), 'reasons': []}
    try:
        request = workspace._record(directory / 'request.json')
        if not isinstance(request, dict):
            raise ValueError('Unproven request')
        request_id = request.get('requestId')
        if not isinstance(request_id, str) or not request_id or '\0' in request_id:
            raise ValueError('Unproven request identity')
        expected = 'ws-' + workspace._sha(workspace._json([str(store.directory), request_id]))[:32]
        if directory.name != expected:
            raise ValueError('Request does not own this directory')
        manifest = workspace._record(directory / 'manifest.json')
        if manifest is not None:
            workspace._validate_manifest(manifest)
            if manifest['workspaceId'] != directory.name or not _record_proven(store.directory, manifest):
                raise ValueError('Unproven allocation records')
        result.update(manifest=manifest, requestId=request_id)
    except (BoardError, ValueError, TypeError, KeyError):
        result['reasons'] = ['allocation-unproven']
    return result


def allocation_records(store, directories, *, stopping=lambda: False):
    """Inspect one allocation at a time, holding only its own allocation lock.

    A manifest's retained uses are checked in a read snapshot under that lock.
    Shells need no retained-reference scan. The caller closes the iterator before
    leaving, so interruption releases the current lock as well.
    """
    from .storage import _allocation_reference_sets, _workspace_reference_rows
    for directory in directories:
        if stopping():
            break
        with ExitStack() as ownership:
            try:
                if private_dirs.linked(directory) or workspace.allocation_lock_directory(store.directory, directory.name) != directory:
                    raise BoardError('WORKSPACE_UNSAFE', 'Unproven allocation directory or lock')
                ownership.enter_context(workspace._lock(directory, create=False))
            except BoardError as error:
                if error.code not in ('WORKSPACE_BUSY', 'WORKSPACE_UNSAFE', 'PRIVATE_PATH_UNSAFE'):
                    raise
                item = {'directory': directory, 'manifest': None, 'requestId': None,
                        'owner': None, 'tasks': set(), 'reasons': [
                            'WORKSPACE_BUSY' if error.code == 'WORKSPACE_BUSY' else 'allocation-unproven']}
            else:
                item = allocation_record(store, directory)
                if not item['reasons']:
                    with store.db.read() as connection:
                        run = _request_run(connection, item['requestId'])
                        if run is not None and (item['manifest'] is not None or run['state'] not in ('accepted', 'concluded')):
                            item['owner'] = run['task_id']
                            item['tasks'].add(run['task_id'])
                            item['reasons'] = ['request-task-retained']
                        elif item['manifest'] is not None:
                            def referenced(index, table, row):
                                item['tasks'].update(value for key in row.keys()
                                                     if (key.endswith('task_id') or key.endswith('run_id'))
                                                     and isinstance(value := row[key], str))
                            item['reasons'] = _allocation_reference_sets(_workspace_reference_rows(connection),
                                [(item['manifest'], item['requestId'])],
                                directories=[str(directory)], on_reference=referenced)[0]
            if item['reasons']:
                ownership.close()
            yield item


def allocation_proof(store, item):
    """Ownership and process proof outside every SQLite transaction."""
    from .macro_task_deletion_workspace import _repository_skip
    directory, manifest = item['directory'], item['manifest']
    if item['reasons']:
        return
    if manifest is None:
        if {entry.name for entry in directory.iterdir()} - {'.lock', 'request.json'}:
            item['reasons'] = ['allocation-unproven']
        return
    skipped = _repository_skip(manifest, 'allocation', str(directory))
    if skipped is not None:
        item['reasons'] = [skipped['reason']['code']]
        return
    item['refs'] = _allocation_refs(directory, manifest)
    checkout = directory / 'checkout'
    proof = workspace.deletion_inspect(store.directory, manifest) if workspace._path_present(checkout) else None
    item['proof'] = proof
    if proof is not None:
        item['reasons'].extend(proof['reasons'])
        if not item['reasons']:
            from .checkout_processes import require_idle_checkout
            try:
                require_idle_checkout(checkout)
            except BoardError as error:
                cause = error.details.get('reason', {})
                if (error.code != 'NOT_READY' or cause.get('code') != 'checkout.process-blockers'
                        or cause.get('params', {}).get('unreadableProcesses')):
                    raise
                item['reasons'].extend(error.details['reasons'])


def reclaim_allocation(store, item):
    """Remove a proven object while its original allocation lock is held."""
    from .macro_task_deletion_workspace import _delete_refs
    from .storage import locked
    with locked(store):
        allocation_proof(store, item)
        if item['reasons']:
            return
        directory, manifest = item['directory'], item['manifest']
        if manifest is not None:
            if item['proof'] is not None:
                workspace.cleanup_remove(store.directory, manifest, proof=item['proof'])
            skipped = _delete_refs(Path(manifest['snapshot']['repositoryPath']), directory.name, item['refs'])
            if skipped:
                item['reasons'] = sorted({entry['reason']['code'] for entry in skipped})
                return
        private_dirs.remove_tree(directory)

def allocation_inventory(store, directories):
    """List only allocations automatic reclamation cannot presently prove."""
    from .macro_task_deletion_workspace import _refs
    result = []
    with closing(allocation_records(store, directories)) as records:
        for item in records:
            if item['owner'] is not None:
                continue
            allocation_proof(store, item)
            manifest = item['manifest']
            if not item['reasons'] and manifest is not None:
                repository = Path(manifest['snapshot']['repositoryPath'])
                for ref, oid in _refs(repository, item['directory'].name).items():
                    if (ref not in item['refs'] or workspace._line(repository, 'symbolic-ref', '--quiet', ref, allowed=(0, 1))):
                        item['reasons'].append('macro-task-deletion.ref-unproven')
                    elif oid != item['refs'][ref]:
                        item['reasons'].append('macro-task-deletion.ref-changed')
            if item['reasons']:
                checkout = item['directory'] / 'checkout'
                item['path'] = checkout if workspace._path_present(checkout) else item['directory']
                if not workspace._path_present(checkout):
                    item['reasons'].append('checkout-missing')
                result.append(item)
    return result


class Reclamation:
    """An incremental inventory, never deletion authority or a durable request queue.

    Startup rebuilds from SQLite and the private directory entries. Thereafter
    the store's existing task-change notification invalidates cached ownership;
    new/replaced directory entries are found when the allocation parent changes.
    Every pending deletion still proves its current facts at its own boundary.
    """
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._initialized = False
        self._changed = set()
        self._workspaces = set()
        self._task_ids = {}
        self._allocation_stamp = None
        self._entries = {}
        self._allocations = set()
        self._owned_allocations = {}
        self._dependencies = {}
        self._blocked_allocations = {}

    def changed(self, task_ids):
        with self._lock:
            self._changed.update(task_ids)

    def _refresh(self):
        with self._lock:
            if not self._initialized:
                self._workspaces.update(workspace_candidates(self.store))
                self._task_ids = native_task_ids(self.store)
                self._initialized = True
            changed, self._changed = self._changed, set()
            self._workspaces.update(workspace_candidates(self.store, changed))
            with self.store.db.read() as connection:
                for task_id in changed:
                    digest = hashlib.sha256(task_id.encode()).hexdigest()
                    if connection.execute('SELECT 1 FROM tasks WHERE task_id=?', (task_id,)).fetchone():
                        self._task_ids[digest] = task_id
                    else:
                        self._task_ids.pop(digest, None)
                    affected = self._owned_allocations.pop(task_id, ())
                    self._allocations.update(affected)
                    for directory in affected:
                        self._blocked_allocations.pop(directory, None)
            workspaces = tuple(self._workspaces)
            task_ids = self._task_ids
        return workspaces, task_ids

    @staticmethod
    def _allocation_entry(directory):
        entries = []
        for path in (directory, directory / 'request.json', directory / 'manifest.json',
                     directory / 'input.json', directory / '.lock', directory / 'checkout'):
            try:
                entry = path.lstat()
            except OSError as error:
                entries.append(error.errno)
            else:
                entries.append((entry.st_ino, entry.st_mtime_ns, entry.st_size, entry.st_mode))
        return tuple(entries)

    def _remember_tasks(self, directory, tasks):
        for task_id in self._dependencies.pop(directory, ()):
            paths = self._owned_allocations.get(task_id)
            if paths is not None:
                paths.discard(directory)
                if not paths:
                    del self._owned_allocations[task_id]
        if tasks:
            self._dependencies[directory] = set(tasks)
            for task_id in tasks:
                self._owned_allocations.setdefault(task_id, set()).add(directory)

    def _discover_allocations(self):
        import os
        root = self.store.directory / 'workspaces'
        if not root.is_dir():
            return
        stamp = root.stat().st_mtime_ns
        if stamp != self._allocation_stamp:
            with os.scandir(root) as entries:
                current = {root / entry.name: entry.inode() for entry in entries
                           if workspace._workspace_identifier(entry.name)}
            self._allocations.update(path for path, inode in current.items() if self._entries.get(path) != inode)
            self._allocations.intersection_update(current)
            for directory in self._entries.keys() - current.keys():
                self._blocked_allocations.pop(directory, None)
                self._remember_tasks(directory, set())
            self._entries = current
            self._allocation_stamp = stamp
        for directory, entry in tuple(self._blocked_allocations.items()):
            if self._allocation_entry(directory) != entry:
                self._allocations.add(directory)
                del self._blocked_allocations[directory]

    def run(self, *, stopping=lambda: False):
        from .storage import locked
        store = self.store
        if stopping():
            return
        workspaces, task_ids = self._refresh()
        failure = None
        for run_id in workspaces:
            if stopping():
                return
            try:
                with locked(store):
                    result = store.workflow._reclaim_checkout(run_id=run_id)
                if result.get('removed'):
                    self._workspaces.discard(run_id)
                if result.get('detail'):
                    raise BoardError(result['reasons'][0], result['detail'])
            except (BoardError, OSError) as error:
                if isinstance(error, BoardError) and error.code in ('NOT_READY', 'TASK_DELETING', 'WORKSPACE_BUSY', 'STORAGE_BUSY', 'NOT_FOUND'):
                    if error.code == 'NOT_FOUND':
                        self._workspaces.discard(run_id)
                    continue
                if failure is None:
                    failure = error
        if failure is None:
            store.clear_service_issue('workspace-cleanup')
        else:
            store.record_service_issue('workspace-cleanup', failure)
        if stopping():
            return
        try:
            reclaim_native(store, task_ids=task_ids, stopping=stopping)
        except (BoardError, OSError) as error:
            store.record_service_issue('native-reclamation', error)
        else:
            store.clear_service_issue('native-reclamation')
        if stopping():
            return
        self._discover_allocations()
        failure = None
        try:
            with closing(allocation_records(store, tuple(self._allocations), stopping=stopping)) as records:
                for item in records:
                    if stopping():
                        return
                    directory = item['directory']
                    if item['reasons'] == ['WORKSPACE_BUSY']:
                        continue
                    try:
                        if not item['reasons']:
                            reclaim_allocation(store, item)
                        self._remember_tasks(directory, item['tasks'])
                        if item['reasons']:
                            self._allocations.discard(directory)
                            if item['owner'] is None:
                                self._blocked_allocations[directory] = self._allocation_entry(directory)
                        elif not workspace._path_present(directory):
                            self._allocations.discard(directory)
                            self._entries.pop(directory, None)
                            self._blocked_allocations.pop(directory, None)
                    except Exception as error:
                        if isinstance(error, BoardError) and error.code in ('WORKSPACE_BUSY', 'STORAGE_BUSY'):
                            continue
                        if failure is None:
                            failure = error
        except Exception as error:
            failure = error
        if failure is None:
            store.clear_service_issue('allocation-reclamation')
        else:
            store.record_service_issue('allocation-reclamation', failure)
