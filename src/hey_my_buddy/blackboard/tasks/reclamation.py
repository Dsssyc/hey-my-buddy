"""Service reclamation from terminal tasks and physical allocation ownership."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import threading
import json
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


def reclaim_allocation(store, directory: Path) -> str | None:
    """Remove only an unreferenced allocation; an owner return permits cheap reuse."""
    from .macro_task_deletion_workspace import _delete_refs, _record_proven, _repository_skip
    from .storage import allocation_references, locked

    with locked(store), ExitStack() as ownership:
        # Malformed or unreadable allocation material proves no ownership.
        # Storage inspection names those retained objects; no service fault.
        try:
            if private_dirs.linked(directory) or workspace.allocation_lock_directory(store.directory, directory.name) != directory:
                return None
            ownership.enter_context(workspace._lock(directory, create=False))
            request = workspace._record(directory / 'request.json')
            if not isinstance(request, dict):
                return None
            request_id = request.get('requestId')
            if not isinstance(request_id, str) or not request_id or '\0' in request_id:
                return None
            expected = 'ws-' + workspace._sha(workspace._json([str(store.directory), request_id]))[:32]
            if directory.name != expected:
                return None
            manifest = workspace._record(directory / 'manifest.json')
            if manifest is not None:
                workspace._validate_manifest(manifest)
                if manifest['workspaceId'] != directory.name or not _record_proven(store.directory, manifest):
                    return None
        except (BoardError, OSError, ValueError, TypeError, KeyError) as error:
            if isinstance(error, BoardError) and error.code == 'WORKSPACE_BUSY':
                raise
            return None
        with store.db.read() as connection:
            run = _request_run(connection, request_id)
            if run is not None and (manifest is not None or run['state'] not in ('accepted', 'concluded')):
                return run['task_id']
        if manifest is None:
            if {item.name for item in directory.iterdir()} <= {'.lock', 'request.json'}:
                private_dirs.remove_tree(directory)
            return None
        # Independent integrations can retain an otherwise unregistered
        # checkout. Hold the writer fence from this shared proof through
        # deletion, so a new retained reference cannot cross that boundary.
        with store.db.write() as connection:
            if allocation_references(connection, manifest, request_id):
                return None
            checkout = directory / 'checkout'
            try:
                if _repository_skip(manifest, 'allocation', str(directory)) is not None:
                    return None
                refs = _allocation_refs(directory, manifest)
                proof = workspace.deletion_inspect(store.directory, manifest) if workspace._path_present(checkout) else None
                if proof is not None:
                    if proof['reasons']:
                        return None
                    from .checkout_processes import require_idle_checkout
                    require_idle_checkout(checkout)
            except (BoardError, OSError, ValueError, TypeError, KeyError):
                return None
            if proof is not None:
                workspace.cleanup_remove(store.directory, manifest, proof=proof)
            if _delete_refs(Path(manifest['snapshot']['repositoryPath']), directory.name, refs):
                return None
            private_dirs.remove_tree(directory)
    return None


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
                    self._allocations.update(self._owned_allocations.pop(task_id, ()))
            workspaces = tuple(self._workspaces)
            task_ids = self._task_ids
        return workspaces, task_ids

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
            self._entries = current
            self._allocation_stamp = stamp

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
        for directory in tuple(self._allocations):
            if stopping():
                return
            try:
                owner = reclaim_allocation(store, directory)
                if owner is not None:
                    self._owned_allocations.setdefault(owner, set()).add(directory)
                    self._allocations.discard(directory)
                elif not workspace._path_present(directory):
                    self._allocations.discard(directory)
                    self._entries.pop(directory, None)
            except (BoardError, OSError) as error:
                if isinstance(error, BoardError) and error.code in ('WORKSPACE_BUSY', 'STORAGE_BUSY'):
                    continue
                if failure is None:
                    failure = error
        if failure is None:
            store.clear_service_issue('allocation-reclamation')
        else:
            store.record_service_issue('allocation-reclamation', failure)


def reclaim(store, *, stopping=lambda: False) -> None:
    """One fresh pass for callers without the daemon's incremental inventory."""
    Reclamation(store).run(stopping=stopping)
