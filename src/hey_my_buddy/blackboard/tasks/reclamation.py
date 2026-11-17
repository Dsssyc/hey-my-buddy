"""Service reclamation from terminal tasks and physical allocation ownership."""
from __future__ import annotations

from contextlib import ExitStack, closing
from datetime import datetime, timedelta
import hashlib
import threading
from pathlib import Path

from ... import private_dirs
from ...errors import BoardError
from . import allocation_revocations, host_conclusions, workspace
from .workspace_bindings import MANIFEST_OWNERS_CTE, RUN_BINDINGS_CTE

NATIVE_RETENTION = timedelta(days=3)


def workspace_candidates(store, task_ids=None) -> list[str]:
    """Cheap durable selection; existing submissions never need provenance work."""
    query = (
        f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}),"
        f" run_bindings AS ({RUN_BINDINGS_CTE})"
        " SELECT r.run_id FROM workflow_runs r"
        " JOIN run_bindings b ON b.run_id=r.run_id"
        " JOIN manifest_owners o ON o.workspace_id=b.workspace_id"
        " AND o.manifest_sha256=b.manifest_sha256"
        " JOIN workflow_artifacts initial ON initial.run_id=r.run_id AND initial.kind='input'"
        " JOIN manifest_owners allocation ON allocation.workspace_id=initial.workspace_id"
        " AND allocation.manifest_sha256=initial.input_manifest_sha256"
        " WHERE r.state IN ('accepted','concluded') AND allocation.workspace_kind='worktree'"
        " AND NOT EXISTS (SELECT 1 FROM workspace_cleanup_plans p WHERE p.run_id=r.run_id"
        " AND p.path=o.checkout_root AND p.state='applied')"
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
        f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}),"
        f" run_bindings AS ({RUN_BINDINGS_CTE})"
        " SELECT p.* FROM workflow_children c JOIN workflow_runs p ON p.run_id=c.parent_run_id"
        " JOIN run_bindings pb ON pb.run_id=p.run_id"
        " JOIN manifest_owners po ON po.workspace_id=pb.workspace_id"
        " AND po.manifest_sha256=pb.manifest_sha256"
        " JOIN run_bindings rb ON rb.run_id=c.child_task_id"
        " JOIN manifest_owners ro ON ro.workspace_id=rb.workspace_id"
        " AND ro.manifest_sha256=rb.manifest_sha256"
        " WHERE c.child_task_id=? AND po.checkout_root=ro.checkout_root",
        (run['run_id'],),
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


def allocation_record(store, directory):
    """Read the immutable request before deciding whether to acquire its lock."""
    result = {'directory': directory, 'manifest': None, 'requestId': None,
              'owner': None, 'tasks': set(), 'reasons': [], 'revoked': False, 'missing': False}
    try:
        if private_dirs.linked(directory) or not workspace._workspace_identifier(directory.name):
            raise ValueError('Unproven allocation directory')
        request = workspace._record(directory / 'request.json')
        if request is None:
            with store.db.read() as connection:
                revoked = connection.execute('SELECT request_id FROM allocation_revocations WHERE allocation_id=?',
                                             (directory.name,)).fetchone()
            if revoked is None:
                raise ValueError('Unproven request')
            request = {'requestId': revoked['request_id']}
            result['revoked'] = True
            result['missing'] = not workspace._path_present(directory)
            if not result['missing']:
                try:
                    entries = {entry.name for entry in directory.iterdir()}
                except FileNotFoundError:
                    result['missing'] = True
                else:
                    if entries - {'.lock'}:
                        raise ValueError('Revoked allocation has additional evidence')
        elif not isinstance(request, dict):
            raise ValueError('Unproven request')
        elif workspace.allocation_lock_directory(store.directory, directory.name) != directory:
            raise ValueError('Unproven allocation lock')
        request_id = request.get('requestId')
        if not isinstance(request_id, str) or not request_id or '\0' in request_id:
            raise ValueError('Unproven request identity')
        expected = 'ws-' + workspace._sha(workspace._json([str(store.directory.resolve()), request_id]))[:32]
        if directory.name != expected:
            raise ValueError('Request does not own this directory')
        result['requestId'] = request_id
    except (BoardError, ValueError, TypeError, KeyError):
        result['reasons'] = ['allocation-unproven']
    return result


def _allocation_owner(store, item):
    with store.db.read() as connection:
        run = _request_run(connection, item['requestId'])
    if run is not None:
        item['tasks'].add(run['task_id'])
        if run['state'] not in ('accepted', 'concluded') or workspace._path_present(item['directory'] / 'manifest.json'):
            item['owner'] = run['task_id']
            item['reasons'] = ['request-task-retained']


def allocation_records(store, directories, *, stopping=lambda: False, create_revoked_lock=False):
    """Skip owned allocations without a lock; lock unowned candidates one by one.

    Admission also holds the allocation lock, so a candidate's owner is re-read
    after acquisition. Removal finishes before the iterator releases its lock.
    """
    for directory in directories:
        if stopping():
            break
        item = allocation_record(store, directory)
        if not item['reasons']:
            _allocation_owner(store, item)
        if item['reasons'] or item['missing']:
            yield item
            continue
        if item['revoked'] and not create_revoked_lock and not workspace._path_present(directory / '.lock'):
            # Inventory grants no deletion authority and never creates a lock.
            # Reclamation creates it and rechecks the record before removal.
            yield item
            continue
        with ExitStack() as ownership:
            try:
                ownership.enter_context(workspace._lock(directory, create=create_revoked_lock and item['revoked']))
            except BoardError as error:
                if error.code not in ('WORKSPACE_BUSY', 'WORKSPACE_UNSAFE', 'PRIVATE_PATH_UNSAFE'):
                    raise
                item['reasons'] = ['WORKSPACE_BUSY' if error.code == 'WORKSPACE_BUSY' else 'allocation-unproven']
            else:
                item.update(allocation_record(store, directory))
                _allocation_owner(store, item)
                if item['owner'] is None:
                    try:
                        item['manifest'] = workspace._record(directory / 'manifest.json')
                        if (item['manifest'] is not None and (not isinstance(item['manifest'], dict)
                                or item['manifest'].get('workspaceId') != directory.name)):
                            item['reasons'] = ['allocation-unproven']
                    except BoardError as error:
                        if error.code not in ('WORKSPACE_CONFLICT', 'PRIVATE_PATH_UNSAFE'):
                            raise
                        item['reasons'] = ['allocation-unproven']
            if item['reasons']:
                ownership.close()
            yield item


def allocation_plan(store, item):
    """Use submission revocation's proof, plus retained uses and process facts."""
    from .storage import _allocation_reference_sets, _workspace_reference_rows
    directory, manifest = item['directory'], item['manifest']
    if item['reasons']:
        return
    if manifest is None:
        if {entry.name for entry in directory.iterdir()} - {'.lock', 'request.json'}:
            item['reasons'] = ['allocation-unproven']
        return
    try:
        item['plan'] = workspace.submission_revoke_plan(store.directory, manifest)
    except BoardError as error:
        if error.code not in ('INVALID_WORKSPACE', 'WORKSPACE_MANIFEST_CHANGED', 'WORKSPACE_CONFLICT',
                              'WORKSPACE_CHANGED', 'WORKSPACE_UNSAFE', 'PRIVATE_PATH_UNSAFE'):
            raise
        item['reasons'] = ['allocation-unproven']
        return
    item['reasons'] = list(item['plan']['reasons'])
    with store.db.read() as connection:
        def referenced(index, table, row):
            item['tasks'].update(value for key in row.keys()
                                 if (key.endswith('task_id') or key.endswith('run_id'))
                                 and isinstance(value := row[key], str))
        item['reasons'].extend(_allocation_reference_sets(_workspace_reference_rows(connection),
            [(manifest, item['requestId'])], directories=[str(directory)], on_reference=referenced)[0])
    checkout = directory / 'checkout'
    if not item['reasons'] and workspace._path_present(checkout):
        from .checkout_processes import allocation_created_at, require_idle_checkout
        try:
            require_idle_checkout(checkout, allocated_at=allocation_created_at(directory))
        except BoardError as error:
            cause = error.details.get('reason', {})
            if (error.code != 'NOT_READY' or cause.get('code') != 'checkout.process-blockers'
                    or cause.get('params', {}).get('unreadableProcesses')):
                raise
            item['reasons'].extend(error.details['reasons'])


def allocation_inventory(store, directories):
    """List only allocations automatic reclamation cannot presently prove."""
    result = []
    with closing(allocation_records(store, directories)) as records:
        for item in records:
            if item['owner'] is not None:
                continue
            if item['missing'] or (item['revoked'] and not item['reasons']
                                   and not workspace._path_present(item['directory'] / '.lock')):
                continue
            allocation_plan(store, item)
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
    every run lists the allocation parent again, so new, replaced and deleted
    entries are all found, and an entry is reprocessed only when it is new or
    replaced by a different inode. Every pending deletion still proves its
    current facts at its own boundary.
    """
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._initialized = False
        self._changed = set()
        self._workspaces = set()
        self._task_ids = {}
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
        root = self.store.directory.resolve() / 'workspaces'
        with self.store.db.read() as connection:
            revoked = {root / row['allocation_id'] for row in connection.execute(
                'SELECT allocation_id FROM allocation_revocations')}
        self._allocations.update(revoked)
        if not root.is_dir():
            return
        with os.scandir(root) as entries:
            current = {root / entry.name: entry.inode() for entry in entries
                       if workspace._workspace_identifier(entry.name)}
        self._allocations.update(path for path, inode in current.items() if self._entries.get(path) != inode)
        self._allocations.intersection_update(current.keys() | revoked)
        for directory in self._entries.keys() - current.keys():
            self._blocked_allocations.pop(directory, None)
            self._remember_tasks(directory, set())
        self._entries = current
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
                result = store.workflow._reclaim_checkout(run_id=run_id)
                if result.get('removed'):
                    self._workspaces.discard(run_id)
                if 'repository-not-found' in result.get('reasons', ()):
                    continue
                if result.get('detail') and failure is None:
                    failure = BoardError(result['reasons'][0],
                        f"{result['detail']}; run {run_id}; plan {result.get('planId')}; path {result.get('path')}")
            except (BoardError, OSError) as error:
                if isinstance(error, BoardError) and error.code in ('NOT_READY', 'TASK_DELETING', 'WORKSPACE_BUSY', 'STORAGE_BUSY', 'NOT_FOUND', 'WORKSPACE_REPOSITORY_NOT_FOUND'):
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
            with closing(allocation_records(store, tuple(self._allocations), stopping=stopping,
                                            create_revoked_lock=True)) as records:
                for item in records:
                    if stopping():
                        return
                    directory = item['directory']
                    if item['reasons'] == ['WORKSPACE_BUSY']:
                        continue
                    try:
                        if not item['reasons']:
                            if item['missing']:
                                if not allocation_revocations.forget_removed(store, directory):
                                    continue
                            else:
                                with locked(store):
                                    allocation_plan(store, item)
                                    if not item['reasons']:
                                        allocation_revocations.record(store, directory, item['requestId'])
                                        if item['manifest'] is None:
                                            (directory / 'request.json').unlink(missing_ok=item['revoked'])
                                        else:
                                            workspace.revoke_submission(item['plan'])
                                if not item['reasons']:
                                    allocation_revocations.finish(store, directory)
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
