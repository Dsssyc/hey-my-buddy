"""Guarded storage inventory and confirmed reclamation; no process termination."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from ...paths import is_absolute_path
from ... import locking
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

import psutil

from ... import private_dirs, private_files
from ...private_dirs import directory_mode
from ...protocol import schemas
from ..store.db import utc_now
from . import host_conclusions
from ...errors import BoardError
from ...install.runtime import DEFAULT_RUNTIME_ROOT, INSTALL_LOCK, is_ready, installation_lock, runtime_root

GRACE_SECONDS = 3 * 86400
_RUNTIME_ID = r'[0-9a-f]{32}'
_BUILD_DIRECTORY = re.compile(r'\.(' + _RUNTIME_ID + r')\.build-.+')


def tree_info(path: Path, *, excluded: frozenset[Path] = frozenset()) -> tuple[int, str, bool]:
    """Size and mutation fingerprint without descending into linked entries."""
    size = 0
    digest = hashlib.sha256()
    safe = not private_dirs.linked(path)
    if not safe:
        return 0, '', False
    if not path.exists():
        return 0, '', safe
    paths = []
    def collect(item: Path):
        paths.append(item)
        if private_dirs.linked(item) or not item.is_dir():
            return
        with os.scandir(item) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            collect(child)
    collect(path)
    for item in sorted(paths):
        stat = item.lstat()
        if item not in excluded and not private_dirs.linked(item) and item.is_file():
            size += stat.st_size
        digest.update(f'{item.relative_to(path)}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_mode}\n'.encode())
    return size, digest.hexdigest(), safe


_INSPECTION_ERRORS = (psutil.Error, OSError, AttributeError, NotImplementedError)


def _require_running(process: psutil.Process) -> None:
    # is_running compares creation identities as well as PIDs. These are local
    # observations, never substitutes for the execution's durable stop identity.
    if not process.is_running():
        raise psutil.NoSuchProcess(process.pid)


def _processes() -> tuple[list[psutil.Process], bool]:
    """A fresh PID snapshot; a lost or unreadable entry makes it incomplete."""
    try:
        pids = psutil.pids()
        if pids is None:
            raise AttributeError('process list unavailable')
    except _INSPECTION_ERRORS:
        return [], False
    processes = []
    known = True
    # process_iter silently skips disappearing processes and caches identities.
    # Explicit construction lets this boundary retain those races as unknown.
    for pid in pids:
        try:
            process = psutil.Process(pid)
            if process.create_time() is None:
                raise AttributeError('process creation time unavailable')
            processes.append(process)
        except _INSPECTION_ERRORS:
            known = False
    return processes, known


def observe_process_argv() -> tuple[list[tuple[psutil.Process, list[str]]], bool]:
    """Shared read-only argv observation, preserving native argument boundaries."""
    processes, known = _processes()
    rows = []
    for process in processes:
        try:
            argv = process.cmdline()
            if argv is None:
                raise AttributeError('process command line unavailable')
            _require_running(process)
            rows.append((process, argv))
        except _INSPECTION_ERRORS:
            known = False
    return rows, known


def _open_file_paths(process: psutil.Process) -> set[str]:
    opened = process.open_files()
    if opened is None:
        raise AttributeError('process files unavailable')
    paths = {item.path for item in opened}
    if any(not isinstance(path, str) for path in paths):
        raise AttributeError('process file path unavailable')
    return paths


def observe_open_paths(*, proc_fds: bool = False) -> tuple[dict[int, set[str]], str | None, bool]:
    """Path references, observation failure, and channel availability.

    psutil supplies cwd and Linux/Windows mappings. POSIX descriptor references
    still need lsof (or the checks runner's existing Linux fd channel):
    open_files omits directories and Linux deleted-but-open files. macOS has no
    psutil memory_maps, so lsof also retains mappings there. No class of reference
    is observed through both channels. Windows open_files is positive evidence
    only: the API can silently miss handles, even after a successful call.
    """
    processes, known = _processes()
    problems = [] if known else ['psutil process enumeration incomplete']
    paths: dict[int, set[str]] = {}
    selected = []
    for process in processes:
        try:
            if not psutil.WINDOWS:
                uids = process.uids()
                uid = uids.effective if psutil.LINUX else uids.real
                if uid != os.getuid():
                    continue  # Match the existing lsof user scope.
            selected.append(process)
            observed = paths.setdefault(process.pid, set())
            cwd = process.cwd()
            if cwd is None:
                raise AttributeError('process cwd unavailable')
            observed.add(cwd)
            if psutil.LINUX or psutil.WINDOWS:
                mappings = process.memory_maps()
                if mappings is None:
                    raise AttributeError('process mappings unavailable')
                observed.update(item.path for item in mappings)
            if psutil.WINDOWS:
                observed.update(_open_file_paths(process))
            missing = {path for path in observed if not isinstance(path, str)}
            if missing:
                observed.difference_update(missing)
                raise AttributeError('process path unavailable')
        except _INSPECTION_ERRORS as error:
            problems.append(f'psutil path observation failed: {type(error).__name__}')
    available = True
    if psutil.WINDOWS:
        problems.append('Windows open-file observation is incomplete: psutil can omit handles')
    else:
        binary = shutil.which('lsof')
        if binary is not None:
            # Linux memory_maps includes program text and deleted mappings.
            excluded = '^cwd,^mem,^DEL,^txt' if psutil.LINUX else '^cwd'
            try:
                result = subprocess.run(
                    [binary, '-nP', '-a', '-u', str(os.getuid()),
                     '-p', ','.join(str(process.pid) for process in selected),
                     '-d', excluded, '-Fpn'],
                    capture_output=True, text=True, encoding=locale.getencoding(), timeout=120,
                    check=False)
            except (OSError, subprocess.SubprocessError) as error:
                problems.append(f'lsof failed: {type(error).__name__}')
            else:
                if result.returncode or result.stderr.strip():
                    problems.append(f'lsof observation incomplete (exit {result.returncode})')
                current_pid = None
                selected_pids = {process.pid for process in selected}
                for line in result.stdout.splitlines():
                    if line.startswith('p'):
                        current_pid = int(line[1:]) if line[1:].isdigit() else None
                        if current_pid not in selected_pids:
                            problems.append('lsof process identity was not observed')
                    elif line.startswith('n') and current_pid is not None:
                        paths.setdefault(current_pid, set()).add(line[1:])
        elif proc_fds and psutil.LINUX:
            # psutil.open_files filters out deleted/non-regular fd targets. Keep
            # only the existing fd observation here, with psutil supplying PIDs,
            # cwd and mappings; failures can no longer disappear into a continue.
            for process in selected:
                try:
                    for handle in (Path('/proc') / str(process.pid) / 'fd').iterdir():
                        paths[process.pid].add(os.readlink(handle))
                except OSError as error:
                    problems.append(f'/proc fd observation failed: {type(error).__name__}')
        else:
            available = False
    for process in selected:
        try:
            _require_running(process)
        except _INSPECTION_ERRORS as error:
            problems.append(f'psutil process identity changed: {type(error).__name__}')
    normalized = {pid: {path.removesuffix(' (deleted)') for path in values if isinstance(path, str)}
                  for pid, values in paths.items()}
    return normalized, '; '.join(dict.fromkeys(problems)) or None, available


def _service_process_modules() -> dict[str, str]:
    """Exact ``-m`` modules for the current runtime's services."""
    from ...install.entrypoints import ENTRY_MODULES, SERVICE_ROLES
    return {modules[role]: role for modules in ENTRY_MODULES.values() for role in SERVICE_ROLES}


def process_inventory(state: Path) -> tuple[list[dict], list[str], bool]:
    """Only observations. Commands and unrelated process details never escape."""
    rows, known = observe_process_argv()
    services = _service_process_modules()
    orphans, commands = [], []
    for process, argv in rows:
        pid = process.pid
        command = ' '.join(argv)
        commands.append(command)
        kind = next((services[argv[index + 1]] for index, token in enumerate(argv[:-1])
                     if token == '-m' and argv[index + 1] in services), None)
        if kind is None:
            continue
        observed_state = None
        if kind == 'execution':
            startup = next((argv[index + 1] for index, token in enumerate(argv[:-1])
                            if token == '--startup'), None)
            if startup:
                path = Path(startup)
                observed_state = next((str(parent.parent) for parent in path.parents
                                       if parent.name == 'harnesses'), None)
        # Daemon argv can omit its state root. Control identity provides one exact
        # positive match, but no missing PID is interpreted as stopped.
        try:
            current = json.loads(private_files.read_text(state / 'control.json'))
            if pid == current.get('pid'):
                observed_state = str(state)
        except (OSError, ValueError, BoardError):
            pass
        if observed_state is None:
            try:
                roots = {str(Path(path).parent) for path in _open_file_paths(process)
                         if Path(path).name in {'board-owner.lock', 'control-daemon.lock'}}
                _require_running(process)
                if len(roots) == 1:
                    observed_state = roots.pop()
            except _INSPECTION_ERRORS:
                known = False
        runtime = next((str(parent) for parent in Path(argv[0]).parents
                        if parent.parent.name == 'runtime'), None)
        if observed_state is not None:
            observed_state = str(Path(observed_state).resolve())
        if observed_state != str(state):
            orphans.append({'pid': pid, 'kind': kind, 'stateDir': observed_state,
                            'runtimeDir': runtime})
    return orphans, commands, known


def runtime_usage(directory: Path, commands: list[str], known: bool) -> list[str]:
    if not known or os.name == 'nt':
        return ['process-inspection-unavailable']
    if any(str(directory) in command for command in commands):
        return ['runtime-in-use']
    opened, error, available = observe_open_paths()
    if error or not available:
        return ['process-inspection-unavailable']
    if any(path == str(directory) or path.startswith(str(directory) + os.sep)
           for paths in opened.values() for path in paths):
        return ['runtime-in-use']
    return []


@contextmanager
def _runtime_removal_locks(path: Path, *, create: bool):
    lock_path = path.parent / INSTALL_LOCK
    if not create and not lock_path.exists() and not lock_path.is_symlink():
        yield
        return
    with installation_lock(lock_path, blocking=False, create=create):
        yield


def _runtime_lock_reasons(path: Path) -> list[str]:
    try:
        with _runtime_removal_locks(path, create=False):
            return []
    except BlockingIOError:
        return ['runtime-installation-active']
    except (BoardError, OSError):
        return ['runtime-lock-unproven']


def _runtime_leftover_recent(path: Path, now: float) -> bool:
    if now - path.lstat().st_mtime < GRACE_SECONDS:
        return True
    if path.is_dir():
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if private_dirs.linked(child):
                    if now - child.lstat().st_mtime < GRACE_SECONDS:
                        return True
                elif _runtime_leftover_recent(child, now):
                    return True
    return False


def _zcode_reasons(store, connection, run, task, now: float) -> list[str]:
    if run is None or task is None:
        return ['owner-unproven']
    # Cancelled runs can currently continue. Preserve them rather than changing
    # existing Host recovery semantics merely to make reclaimable bytes bigger.
    shutdown = store.workflow.shutdown_summary(connection, run['run_id'])
    reasons = []
    if not all(shutdown.get(k) is True for k in ('selfConfirmed', 'descendantsConfirmed')):
        reasons.append('shutdown-unconfirmed')
    if run['state'] == 'accepted' and task['accepted_at']:
        finished_at = task['accepted_at']
    elif run['state'] == 'concluded':
        finished_at = host_conclusions.current(connection, run)['created_at']
    else:
        return ['continuation-supported', *reasons]
    finished = datetime.fromisoformat(finished_at).timestamp()
    if now - finished < GRACE_SECONDS:
        reasons.append('grace-period')
    if connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued')", (run['run_id'],)).fetchone():
        reasons.append('pending-continuations')
    if connection.execute("SELECT 1 FROM workflow_requests WHERE run_id=? AND state='open'", (run['run_id'],)).fetchone():
        reasons.append('open-requests')
    return reasons


def _workspace_reference_rows(connection):
    """Physical references in retained facts, without filtering their state.

    Integration targets need not be a run's execution workspace: verifying a
    target ref can leave its checkout unchanged. Cleanup plans likewise retain
    their exact path and identity even after reservations have been released.
    Include durable inputs, results and receipts that can retain other physical
    locations.
    """
    for table in (
        'tasks', 'attempts', 'artifacts', 'macro_tasks', 'resource_claims', 'messages',
        'events', 'commands', 'decision_requests', 'harness_health',
        'workflow_runs', 'workflow_children', 'workspace_reservations',
        'workflow_continuations', 'workflow_turns', 'workflow_artifacts',
        'workflow_requests', 'workflow_scope_versions', 'workflow_workspace_conflicts',
        'workflow_integrations', 'workspace_cleanup_plans', 'workflow_host_conclusions'):
        for row in connection.execute('SELECT * FROM ' + table):
            yield table, row


def allocation_references(connection, manifest: dict, request_id: str | None = None) -> list[str]:
    """An unregistered allocation has no retained board reference of any state."""
    return _allocation_reference_reasons(_workspace_reference_rows(connection), manifest, request_id)


def submission_references(connection, manifest: dict, request_id: str, directory: Path) -> list[str]:
    """Prove the new logical allocation is unregistered, including retained uses.

    Existing workspaces borrow their checkout, so its other readers do not own
    this request's records or refs. A worktree's checkout identity belongs to
    its allocation and protects references independently of path spelling.
    """
    return _allocation_reference_sets(_workspace_reference_rows(connection), [(manifest, request_id)],
                                      directories=[str(directory)])[0]


def _allocation_reference_reasons(rows, manifest, request_id):
    return _allocation_reference_sets(rows, [(manifest, request_id)])[0]


def _allocation_reference_sets(rows, allocations, *, directories=None):
    """One streaming pass, one parse per structured field, bounded by a row.

    Exact tokens and normalized ancestor paths match every candidate together.
    No state filter is safe: historical receipts and malformed structured facts
    can still retain a location. Only the reason sets survive each input row.
    """
    if not allocations:
        return []
    reasons = [set() for _ in allocations]
    tokens, paths = {}, {}
    for index, (manifest, request_id) in enumerate(allocations):
        identities = [manifest['workspaceId'], request_id, manifest.get('manifestSha256')]
        if manifest['kind'] == 'worktree':
            identities.append(manifest['checkoutId'])
        for token in identities:
            if isinstance(token, str):
                tokens.setdefault(token, set()).add(index)
        directory = directories[index] if directories is not None else str(Path(manifest['checkoutRoot']).parent)
        paths.setdefault(directory, set()).add(index)

    def matches(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from matches(item)
        elif isinstance(value, list):
            for item in value:
                yield from matches(item)
        elif isinstance(value, str):
            yield from tokens.get(value, ())
            if is_absolute_path(value):
                for path in {value, os.path.normpath(value)}:
                    while True:
                        yield from paths.get(path, ())
                        parent = os.path.dirname(path)
                        if parent == path:
                            break
                        path = parent

    for table, row in rows:
        referenced = set()
        unproven = False
        for field in row.keys():
            value = row[field]
            if isinstance(value, str) and (field.endswith('_json') or field in ('exclusive_resources', 'required_capabilities', 'log_paths')):
                try:
                    value = json.loads(value)
                except ValueError:
                    unproven = True
            referenced.update(matches(value))
        for index in referenced:
            reasons[index].add(table + '-reference')
        if unproven:
            for item in reasons:
                item.add(table + '-reference-unproven')
    return [sorted(item) for item in reasons]


def _orphan_workspace(store, directory: Path, *, git_proof: bool = True) -> dict:
    """Only the original allocation record can prove an orphan's exact checkout."""
    from . import workspace
    state = store.directory.resolve()
    path = directory / 'checkout'
    result = {'path': str(path), 'orphan': True, 'runId': None, 'reasons': [],
              'manifest': None, 'allocationFacts': None}
    try:
        manifest = _read_storage_json(directory / 'manifest.json')
        request = _read_storage_json(directory / 'request.json')
        pinned = _read_storage_json(directory / 'input.json')
        workspace._validate_manifest(manifest)
        if (not isinstance(request.get('requestId'), str) or not request['requestId']
                or not isinstance(request.get('intent'), dict) or not isinstance(pinned.get('snapshot'), dict)):
            raise BoardError('STORAGE_UNSAFE', 'Original allocation records have invalid fields')
        expected_id = 'ws-' + workspace._sha(workspace._json([str(state), request['requestId']]))[:32]
        if (directory.parent != state / 'workspaces' or directory.name != expected_id
                or manifest['workspaceId'] != directory.name or manifest['kind'] != 'worktree'
                or manifest['checkoutRoot'] != str(path) or str(path.resolve()) != str(path)
                or type(request.get('version')) is not int or request['version'] not in (1, 2, 3)
                or request['version'] != manifest['version']
                or request.get('intent', {}).get('kind') != 'worktree'
                or pinned.get('inputCommit') != manifest['inputCommit']
                or pinned.get('inputTree') != manifest['inputTree']
                or pinned.get('snapshot', {}).get('stagedCommit') != manifest['snapshot']['stagedCommit']):
            raise BoardError('STORAGE_UNSAFE', 'Original allocation records do not prove this checkout')
        result['manifest'] = manifest
        result['allocationRequestId'] = request['requestId']
        refs = {}
        if git_proof:
            proof = workspace.cleanup_inspect(state, manifest)
            result['reasons'].extend(proof['reasons'])
            result['proof'] = proof
            repository = Path(manifest['snapshot']['repositoryPath'])
            for suffix, commit in (('input', manifest['inputCommit']),
                                   ('staged', manifest['snapshot']['stagedCommit']),
                                   ('retained/input', manifest['inputCommit']),
                                   ('retained/staged', manifest['snapshot']['stagedCommit'])):
                ref = 'refs/hey-my-buddy/workspaces/' + directory.name + '/' + suffix
                refs[ref] = workspace._line(repository, 'show-ref', '--verify', '--hash', ref)
                if (suffix in ('input', 'staged') and manifest['snapshot'][suffix + 'Ref'] != ref
                        or workspace._git(repository, 'symbolic-ref', '-q', ref, allowed=(0, 1)).strip()
                        or refs[ref] != commit):
                    result['reasons'].append('fixed-reference-changed')
            if workspace._git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'):
                result['reasons'].append('unsealed-changes')
        identities = {}
        for name, item in (('allocation', directory), ('checkout', path), ('lock', directory / '.lock')):
            identity = item.lstat()
            if not (stat.S_ISREG(identity.st_mode) if name == 'lock' else stat.S_ISDIR(identity.st_mode)):
                raise BoardError('STORAGE_UNSAFE', 'Original allocation physical identity is unproven')
            identities[name] = {'device': identity.st_dev, 'inode': identity.st_ino, 'mode': identity.st_mode}
        result['allocationFacts'] = {'identities': identities, 'refs': refs,
                                    'manifestSha256': manifest['manifestSha256'],
                                    'requestSha256': workspace._sha(workspace._json(request)),
                                    'inputSha256': workspace._sha(workspace._json(pinned))}
    except (BoardError, OSError, ValueError, TypeError, KeyError) as error:
        result['reasons'].append('allocation-unproven')
        if isinstance(error, BoardError):
            result['reasons'].append(error.code)
            result['proofError'] = error.payload()
    result['reasons'] = sorted(set(result['reasons']))
    return result


def _remove_orphan_workspace(store, candidate: dict) -> None:
    from . import workspace
    directory = Path(candidate['path']).parent
    # This is the same lock and order as submission: allocation, then board.
    # Keep the DB writer fence through removal so admission cannot win between
    # checking retained facts and deleting a checkout.
    with workspace._lock(directory):
        # Git and allocation-record proof precede the writer transaction. The
        # allocation lock still fences same-request preparation/admission.
        fresh = _orphan_workspace(store, directory)
        with store.db.write() as connection:
            if not fresh['reasons']:
                fresh['reasons'].extend(allocation_references(
                    connection, fresh['manifest'], fresh['allocationRequestId']))
            if (fresh['reasons'] or fresh['allocationFacts'] != candidate.get('allocationFacts')
                    or fresh['manifest'] != candidate.get('manifest')
                    or tree_info(Path(candidate['path']))[1:] != (candidate['fingerprint'], True)):
                raise BoardError('STORAGE_CHANGED', 'Orphan allocation changed or acquired a board reference',
                                 reasons=fresh['reasons'])
            # Revalidate small physical records after the historical scan too;
            # moving Git inspection out of the transaction must not widen the
            # deletion authority to changed request/input records or inodes.
            for name, item in (('allocation', directory), ('checkout', Path(candidate['path'])),
                               ('lock', directory / '.lock')):
                identity = item.lstat()
                facts = {'device': identity.st_dev, 'inode': identity.st_ino, 'mode': identity.st_mode}
                if facts != fresh['allocationFacts']['identities'][name]:
                    raise BoardError('STORAGE_CHANGED', 'Orphan physical identity changed')
            for name, key in (('request.json', 'requestSha256'), ('input.json', 'inputSha256')):
                record = _read_storage_json(directory / name)
                if workspace._sha(workspace._json(record)) != fresh['allocationFacts'][key]:
                    raise BoardError('STORAGE_CHANGED', 'Orphan allocation record changed')
            if _read_storage_json(directory / 'manifest.json') != fresh['manifest']:
                raise BoardError('STORAGE_CHANGED', 'Orphan allocation manifest changed')
            workspace.cleanup_remove(store.directory, fresh['manifest'])


def inspect(store, *, git_proof: bool = True) -> dict:
    from . import workspace
    state = store.directory.resolve()
    rows = []
    orphaned, commands, known = process_inventory(state)
    now = time.time()
    def add(category, path, reasons, *, excluded=frozenset(), **metadata):
        size, fingerprint, safe = tree_info(path, excluded=excluded)
        if not safe:
            reasons = [*reasons, 'linked-path']
        if not path.exists() and not private_dirs.linked(path):
            return
        row = {'id': hashlib.sha256(str(path).encode()).hexdigest()[:24], 'category': category,
               'path': str(path), 'bytes': size, 'eligible': not reasons, 'reasons': sorted(set(reasons)),
               'fingerprint': fingerprint, **metadata}
        rows.append(row)
        return row
    # Snapshot board facts first. Filesystem walks and Git inspection must never
    # hold an authoritative database transaction open.
    native_owners = {}
    workspace_inputs = []
    with store.db.read() as connection:
        for run in connection.execute('SELECT * FROM workflow_runs'):
            task = connection.execute('SELECT * FROM tasks WHERE task_id=?', (run['run_id'],)).fetchone()
            reasons = _zcode_reasons(store, connection, run, task, now)
            micro_task_ids = [run['run_id'], *(routed['task_id'] for routed in
                                             store.workflow._routing_tasks(connection, run['run_id']))]
            for micro_task_id in micro_task_ids:
                digest = hashlib.sha256(micro_task_id.encode()).hexdigest()
                native_owners[digest] = (run['run_id'], reasons)
            manifest = json.loads(run['workspace_manifest_json'] or '{}')
            if not manifest:
                continue
            retained = store.workflow._allocation_provenance(connection, run)
            reasons, _, _ = store.workflow._cleanup_reasons(connection, run, task, manifest)
            sealed = store.workflow._latest_handoff(connection, run, manifest) if not reasons else None
            workspace_inputs.append((run['run_id'], run['revision'], manifest, retained, reasons, sealed))
    home_root = state / 'harnesses/zcode'
    if home_root.exists():
        for path in sorted(home_root.iterdir()):
            if path.name == 'micro-tasks':
                continue
            run_id, reasons = native_owners.get(path.name, (None, ['owner-unproven']))
            add('zcode', path, reasons, runId=run_id)
    harnesses = state / 'harnesses'
    if harnesses.exists():
        for adapter in sorted(private_dirs.ADAPTERS):
            adapter_root = harnesses / adapter
            if not adapter_root.exists():
                continue
            micro_tasks = adapter_root / 'micro-tasks'
            if micro_tasks.exists():
                for path in sorted(micro_tasks.iterdir()):
                    run_id, reasons = native_owners.get(path.name, (None, ['owner-unproven']))
                    add('harnesses', path, reasons, runId=run_id, adapter=adapter)
            if adapter != 'zcode':
                for path in sorted(adapter_root.iterdir()):
                    if path.name != 'micro-tasks':
                        add('harnesses', path, ['legacy-layout-unmigrated'], adapter=adapter)
    sessions = state / 'sessions'
    if sessions.exists():
        for category in ('macro-tasks', 'micro-tasks'):
            groups = sessions / category
            if groups.exists():
                for group in sorted(groups.iterdir()):
                    add('session-records', group, ['macro-task-deletion'], groupId=(
                        group.name if category == 'macro-tasks' else 'run:' + group.name))
    seen = set()
    # Board knowledge is independent of whether physical ownership is proved.
    # Rejected/borrowed allocations must not fall through to the orphan path.
    recognized = set()
    for _, _, manifest, retained, _, _ in workspace_inputs:
        for recorded in (manifest, *retained):
            root = recorded.get('checkoutRoot')
            if isinstance(root, str):
                recognized.add(Path(os.path.normpath(root)))
            identifier = recorded.get('workspaceId')
            if recorded.get('kind') == 'worktree' and workspace._workspace_identifier(identifier):
                recognized.add(state / 'workspaces' / identifier / 'checkout')
    for run_id, revision, manifest, retained, reasons, sealed in workspace_inputs:
        try:
            allocation = workspace.resolve_allocation(state, manifest, retained)
            if allocation is None:
                continue  # Host-owned/unrelated checkout: never measure or delete.
            path = Path(allocation['path'])
            if path in seen:
                continue
            seen.add(path)
            if not workspace._path_present(path):
                # The checkout may have been reclaimed while its allocation
                # record and sealed outputs remain. Keep those bytes visible.
                add('workspaces', path.parent, [*reasons, 'checkout-missing'],
                    runId=run_id, revision=revision)
                continue
            if not reasons and git_proof:
                inspected = workspace.cleanup_inspect(state, manifest, sealed=sealed, retained=retained)
                reasons.extend(inspected['reasons'])
            add('workspaces', path, reasons, runId=run_id, revision=revision)
        except BoardError:
            # Invalid ownership never licenses a filesystem guess.
            continue
    workspace_root = state / 'workspaces'
    orphan_inputs = []
    if workspace_root.is_dir():
        for directory in sorted(workspace_root.iterdir()):
            path = directory / 'checkout'
            if path in seen or path in recognized:
                continue
            if not path.exists():
                add('workspaces', directory, ['owner-unproven', 'checkout-missing'],
                    orphan=True, runId=None)
                continue
            orphan_inputs.append(_orphan_workspace(store, directory, git_proof=git_proof))
    pending = [item for item in orphan_inputs if not item['reasons']]
    if pending:
        with store.db.read() as connection:
            references = _allocation_reference_sets(
                _workspace_reference_rows(connection),
                [(item['manifest'], item['allocationRequestId']) for item in pending])
        for item, reasons in zip(pending, references):
            item['reasons'].extend(reasons)
    for orphan in orphan_inputs:
        reasons = orphan.pop('reasons')
        path = Path(orphan.pop('path'))
        orphan.pop('allocationRequestId', None)
        add('workspaces', path, reasons, **orphan)
    runtimes = runtime_root().absolute()
    from ...home import default_state_dir
    foreign_default_root = (runtimes.resolve() == Path(DEFAULT_RUNTIME_ROOT).resolve()
                            and state != default_state_dir().resolve())
    try:
        keep = json.loads(private_files.read_text(state / 'runtime-retention.json'))
        retained_ids = {keep['current'], keep['previous']}
    except (OSError, KeyError, ValueError, BoardError):
        retained_ids = None
    if private_dirs.linked(runtimes):
        add('runtimes', runtimes, ['linked-path'])
    elif runtimes.exists() and not runtimes.is_dir():
        add('runtimes', runtimes, ['runtime-format-unsupported'])
    elif runtimes.exists():
        for path in sorted(runtimes.iterdir()):
            if path.name == INSTALL_LOCK:
                continue  # Root-lifetime coordination file, never a reclamation candidate.
            if private_dirs.linked(path):
                add('runtimes', path, ['linked-path'])
                continue
            try:
                path_stat = path.lstat()
            except OSError:
                add('runtimes', path, ['runtime-identity-unproven'])
                continue
            build = _BUILD_DIRECTORY.fullmatch(path.name)
            incomplete = (stat.S_ISDIR(path_stat.st_mode) and
                          re.fullmatch(_RUNTIME_ID, path.name) is not None and
                          not (path / 'READY.json').exists() and not (path / 'READY.json').is_symlink())
            leftover = (stat.S_ISDIR(path_stat.st_mode) and
                        (build is not None or path.name.startswith('.build-') or incomplete))
            if leftover:
                identity = build[1] if build else path.name
                reasons = (['retention-history-unproven'] if retained_ids is None else
                           ['retained-runtime'] if identity in retained_ids else [])
                if foreign_default_root:
                    reasons.append('runtime-owned-by-default-state')
                if _runtime_leftover_recent(path, now):
                    reasons.append('retention-grace')
                reasons.extend(_runtime_lock_reasons(path))
                if stat.S_ISDIR(path_stat.st_mode) and not reasons:
                    reasons.extend(runtime_usage(path, commands, known))
                add('runtimes', path, reasons)
                continue
            if not stat.S_ISDIR(path_stat.st_mode):
                add('runtimes', path, ['runtime-format-unsupported'])
                continue
            ready = path / 'READY.json'
            if private_dirs.linked(ready):
                add('runtimes', path, ['runtime-format-unsupported'])
                continue
            try:
                ready_stat = ready.lstat()
            except OSError:
                add('runtimes', path, ['runtime-identity-unproven'])
                continue
            if not stat.S_ISREG(ready_stat.st_mode):
                add('runtimes', path, ['runtime-format-unsupported'])
                continue
            reasons = (['retention-history-unproven'] if retained_ids is None else
                       ['retained-runtime'] if path.name in retained_ids else [])
            if foreign_default_root:
                reasons.append('runtime-owned-by-default-state')
            try:
                current_ready = is_ready(path)
            except (BoardError, OSError, ValueError, TypeError, UnicodeError):
                current_ready = False
            if not current_ready:
                reasons.append('runtime-identity-unproven')
            if not reasons:
                reasons.extend(runtime_usage(path, commands, known))
                reasons.extend(_runtime_lock_reasons(path))
            add('runtimes', path, reasons)
    add('backup', state / 'backups', ['current-backup'])
    for path in sorted(state.iterdir()):
        if path.name in {'workspaces','harnesses','sessions','backups','archives'} or path == runtimes:
            continue
        add('durable', path, ['durable-record'])
    if harnesses.exists():
        for path in sorted(harnesses.iterdir()):
            if path.name not in private_dirs.ADAPTERS:
                add('durable', path, ['durable-record'])
                continue
    categories = []
    for key in ('harnesses', 'session-records', 'zcode', 'workspaces', 'runtimes', 'backup', 'durable'):
        members = [r for r in rows if r['category'] == key]
        total_bytes = tree_info(state / 'workspaces')[0] if key == 'workspaces' else sum(r['bytes'] for r in members)
        categories.append({'id': key, 'bytes': total_bytes,
                           'reclaimableBytes': sum(r['bytes'] for r in members if r['eligible']),
                           'count': len(members), 'eligibleCount': sum(r['eligible'] for r in members),
                           'reasons': sorted({reason for r in members for reason in r['reasons']})})
    return {'createdAt': utc_now(), 'categories': categories, 'candidates': rows, 'orphanProcesses': orphaned}


def _read_storage_json(path: Path, *, missing_ok: bool = False) -> dict | None:
    try:
        fd = private_dirs.open_regular_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except BoardError as error:
        raise BoardError('STORAGE_UNSAFE', 'Storage record is unsafe', path=str(path)) from error
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        try:
            value = json.load(stream)
        except ValueError as error:
            raise BoardError('STORAGE_UNSAFE', 'Storage record is invalid JSON', path=str(path)) from error
    if not isinstance(value, dict):
        raise BoardError('STORAGE_UNSAFE', 'Storage record must be an object', path=str(path))
    return value


def _atomic_storage_json(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    try:
        private_files.publish_bytes(path, raw)
    except BoardError as error:
        raise BoardError('STORAGE_UNSAFE', 'Storage record is unsafe', path=str(path)) from error


def _validate_pending(result: dict, planned: dict, state: Path) -> None:
    pending = result.get('pending')
    if pending is None:
        return
    if result.get('complete') or not isinstance(pending, dict):
        raise BoardError('STORAGE_UNSAFE', 'Storage recovery journal is invalid')
    candidates = planned['candidates']
    matches = [row for row in candidates if isinstance(row, dict) and row.get('id') == pending.get('id')]
    if len(matches) != 1:
        raise BoardError('STORAGE_UNSAFE', 'Pending removal is absent from its original plan')
    candidate = matches[0]
    category = candidate.get('category')
    if (candidate.get('eligible') is not True or category not in ('harnesses', 'zcode', 'runtimes') or
            any(pending.get(key) != candidate.get(key) for key in ('id', 'path', 'fingerprint', 'bytes')) or
            not isinstance(pending.get('path'), str) or not is_absolute_path(pending['path']) or
            pending['id'] != hashlib.sha256(pending['path'].encode()).hexdigest()[:24]):
        raise BoardError('STORAGE_UNSAFE', 'Pending removal does not match its eligible plan candidate')
    original = Path(pending['path'])
    if '..' in original.parts:
        raise BoardError('STORAGE_UNSAFE', 'Pending removal path is not a live candidate')
    root = Path(state).absolute()
    if category == 'runtimes':
        allowed_parent = runtime_root().absolute()
        allowed = original.parent == allowed_parent
    elif category == 'zcode':
        allowed = original.parent == root / 'harnesses' / 'zcode'
    else:
        allowed = any(original.parent == root / 'harnesses' / adapter / 'micro-tasks'
                      for adapter in private_dirs.ADAPTERS)
    tomb = original.with_name('.reclaim-' + planned['planId'] + '-' + original.name)
    if (not allowed or pending.get('tomb') != str(tomb) or
            type(pending.get('device')) is not int or type(pending.get('inode')) is not int):
        raise BoardError('STORAGE_UNSAFE', 'Pending removal path is outside its fixed storage boundary')


@contextmanager
def locked(store):
    path = store.directory / 'storage'
    path.mkdir(mode=directory_mode(), exist_ok=True)
    if not path.is_dir():
        raise BoardError('STORAGE_UNSAFE', 'Storage plan root must be a directory', path=str(path))
    lock_path = path / '.lock'
    try:
        fd = private_dirs.open_regular_fd(lock_path, os.O_CREAT | os.O_RDWR)
    except BoardError as error:
        raise BoardError('STORAGE_UNSAFE', 'Storage lock is unsafe', path=str(lock_path)) from error
    try:
        locking.lock(fd, blocking=False)
        yield path
    except BlockingIOError as error:
        raise BoardError('STORAGE_BUSY', 'A storage operation is already running') from error
    finally:
        os.close(fd)


def plan(store, params: dict) -> dict:
    schemas.reject_unknown(params, set(), 'storage.plan')
    with locked(store) as directory:
        result = inspect(store)
        result.update(planId='stg-' + uuid.uuid4().hex, expiresAt=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        _atomic_storage_json(directory / (result['planId'] + '.json'), result)
        return result


def _changed_workspace_reasons(store, candidate: dict) -> list[str]:
    """Diagnose a retained checkout without authorizing its changed storage plan."""
    from . import workspace
    with store.db.read() as connection:
        run = store.workflow._run_row(connection, candidate['runId'])
        manifest = json.loads(run['workspace_manifest_json'])
        retained = store.workflow._allocation_provenance(connection, run)
        sealed = store.workflow._latest_handoff(connection, run, manifest)
    proof = workspace.cleanup_inspect(store.directory, manifest, sealed=sealed, retained=retained,
                                      allowed=store.workflow._board_adopted_paths(run['run_id'], manifest))
    return proof['reasons'] or ['candidate-changed']


def apply(store, params: dict) -> dict:
    schemas.reject_unknown(params, {'planId', 'commandId', 'confirm'}, 'storage.apply')
    plan_id = schemas.required_string(params, 'planId', max_length=64, pattern=re.compile(r'^stg-[0-9a-f]{32}$'))
    command = schemas.required_string(params, 'commandId', max_length=128)
    if params.get('confirm') is not True:
        raise BoardError('CONFIRMATION_REQUIRED', 'Confirm the exact storage plan before applying it')
    with locked(store) as directory:
        receipt = directory / ('receipt-' + hashlib.sha256(command.encode()).hexdigest() + '.json')
        saved = _read_storage_json(receipt, missing_ok=True)
        if saved is not None:
            if saved.get('planId') != plan_id:
                raise BoardError('CONFLICT', 'This commandId belongs to another storage plan')
        try:
            path = directory / (plan_id + '.json')
            planned = _read_storage_json(path)
        except (OSError, ValueError):
            raise BoardError('NOT_FOUND', 'Storage plan is unavailable')
        if planned.get('planId') != plan_id or not isinstance(planned.get('candidates'), list):
            raise BoardError('STORAGE_UNSAFE', 'Storage plan identity is invalid')
        if saved is not None:
            _validate_pending(saved, planned, store.directory)
            if saved.get('complete'):
                return saved
        if saved is None and datetime.fromisoformat(planned['expiresAt']).timestamp() < time.time():
            raise BoardError('PLAN_EXPIRED', 'Storage plan expired; inspect again')
        result = {'planId': plan_id, 'removedBytes': 0, 'removed': [], 'skipped': [], 'complete': False}
        if saved is not None:
            result = saved
        if result.get('pending'):
            pending = result['pending']
            runtime_pending = next(row for row in planned['candidates']
                                   if row['id'] == pending['id'])['category'] == 'runtimes'
            with ExitStack() as removal:
                if runtime_pending:
                    original, tomb = Path(pending['path']), Path(pending['tomb'])
                    removal.enter_context(_runtime_removal_locks(original, create=True))
                    remaining = original if original.exists() else tomb
                    if remaining.is_dir():
                        _, commands, known = process_inventory(store.directory.resolve())
                        if runtime_usage(remaining, commands, known):
                            raise BoardError('STORAGE_CHANGED', 'Runtime usage changed')
                _finish_pending(result, receipt)
        # The plan proved Git state before confirmation. The removal paths prove
        # it again under their own deletion fences, after this board inventory.
        current = {row['id']: row for row in inspect(store, git_proof=False)['candidates']}
        done = {row['id'] for row in result['removed'] + result['skipped']}
        for candidate in planned['candidates']:
            if candidate['id'] in done:
                continue
            if not candidate['eligible']:
                if candidate['category'] == 'runtimes':
                    result['skipped'].append({'id': candidate['id'], 'path': candidate['path'],
                                              'reasons': candidate['reasons']})
                    _save_receipt(receipt, result)
                continue
            fresh = current.get(candidate['id'])
            try:
                if (fresh is None or not fresh['eligible'] or
                        any(fresh.get(key) != candidate.get(key) for key in ('category', 'path', 'fingerprint'))):
                    reasons = fresh['reasons'] if fresh and not fresh['eligible'] else ['candidate-changed']
                    if (fresh is not None and fresh['eligible'] and fresh['category'] == 'workspaces'
                            and not fresh.get('orphan')
                            and all(fresh.get(key) == candidate.get(key) for key in ('category', 'path'))):
                        reasons = _changed_workspace_reasons(store, fresh)
                    result['skipped'].append({'id': candidate['id'], 'path': candidate['path'], 'reasons': reasons})
                    continue
                if candidate['category'] == 'workspaces':
                    if fresh.get('orphan') is True:
                        _remove_orphan_workspace(store, candidate)
                        result['removed'].append({'id': fresh['id'], 'path': fresh['path'], 'bytes': fresh['bytes']})
                        result['removedBytes'] += fresh['bytes']
                        _save_receipt(receipt, result)
                        continue
                    authority = {'sessionId': 'storage-maintenance'}
                    request = {'runId': fresh['runId'], 'commandId': command + ':' + fresh['id'], 'expectedRevision': fresh['revision']}
                    planned_workspace = store.workflow.cleanup_plan(request, console_authority=authority)
                    wp = planned_workspace['plan']
                    store.workflow.cleanup_apply({**request, 'commandId': request['commandId'] + ':apply', 'expectedRevision': planned_workspace['targetRevision'], 'planId': wp['planId'], 'confirmPath': fresh['path']}, console_authority=authority)
                elif candidate['category'] in ('harnesses', 'zcode', 'runtimes'):
                    # Recheck immediately before atomic removal from the live name.
                    original = Path(fresh['path'])
                    with ExitStack() as removal:
                        if candidate['category'] == 'runtimes':
                            removal.enter_context(_runtime_removal_locks(original, create=True))
                            if original.is_dir():
                                _, commands, known = process_inventory(store.directory.resolve())
                                if runtime_usage(original, commands, known):
                                    raise BoardError('STORAGE_CHANGED', 'Runtime usage changed')
                        if tree_info(original)[1:] != (fresh['fingerprint'], True):
                            raise BoardError('STORAGE_CHANGED', 'Candidate changed')
                        tomb = original.with_name('.reclaim-' + plan_id + '-' + original.name)
                        identity = original.lstat()
                        result['pending'] = {'id':fresh['id'], 'path':str(original), 'tomb':str(tomb),
                                             'bytes':fresh['bytes'], 'fingerprint':fresh['fingerprint'],
                                             'device':identity.st_dev, 'inode':identity.st_ino}
                        _save_receipt(receipt, result)
                        _finish_pending(result, receipt)
                    continue
                else:
                    raise BoardError('STORAGE_PROTECTED', 'Durable storage cannot be reclaimed')
                result['removed'].append({'id': fresh['id'], 'path': fresh['path'], 'bytes': fresh['bytes']})
                result['removedBytes'] += fresh['bytes']
            except (BoardError, OSError) as error:
                if result.get('pending'):
                    raise BoardError('STORAGE_INCOMPLETE', 'Removal is incomplete; retry the same commandId and planId', planId=plan_id) from error
                if isinstance(error, BoardError):
                    reasons = (error.details.get('reasons') or [error.code]) if error.code == 'NOT_READY' else [error.code]
                else:
                    reasons = ['filesystem-error']
                result['skipped'].append({'id': candidate['id'], 'path': candidate['path'], 'reasons': reasons})
            _save_receipt(receipt, result)
        result['complete'] = True
        _save_receipt(receipt, result)
        return result


def _finish_pending(result: dict, receipt: Path) -> None:
    """Resume only a previously journaled exact tomb; never count it as skipped."""
    pending = result['pending']
    original, tomb = Path(pending['path']), Path(pending['tomb'])
    original_exists = original.exists()
    tomb_exists = tomb.exists()
    if original_exists:
        identity = original.lstat()
        if (tomb_exists or
                (identity.st_dev != pending['device'] or identity.st_ino != pending['inode']) or
                tree_info(original)[1:] != (pending['fingerprint'], True)):
            raise BoardError('STORAGE_CHANGED', 'Pending removal no longer matches its candidate')
        os.rename(original, tomb)
        private_files.sync_directory(original.parent)
        tomb_exists = True
    if tomb_exists:
        identity = tomb.lstat()
        if identity.st_dev != pending['device'] or identity.st_ino != pending['inode']:
            raise BoardError('STORAGE_CHANGED', 'Pending tomb no longer matches its candidate')
        if tomb.is_dir():
            private_dirs.remove_tree(tomb)
        else:
            tomb.unlink()
        private_files.sync_directory(tomb.parent)
    result['removed'].append({k:pending[k] for k in ('id','path','bytes')})
    result['removedBytes'] += pending['bytes']
    del result['pending']
    _save_receipt(receipt, result)


def cleanup_accepted_workspace(store, run_id: str) -> dict:
    """Daemon-triggered cleanup after acceptance, using the normal guarded plan."""
    with store.db.read() as connection:
        run = store.workflow._run_row(connection, run_id)
        if run['state'] != 'accepted':
            return {'removed':False, 'reason':'not-accepted'}
        revision = run['revision']
    authority = {'sessionId':'accepted-workspace-maintenance'}
    command = 'accepted-cleanup-' + uuid.uuid4().hex
    params = {'runId':run_id, 'expectedRevision':revision, 'commandId':command}
    planned = store.workflow.cleanup_plan(params, console_authority=authority)
    if not planned['plan']['eligible']:
        return {'removed':False, 'plan':planned['plan']}
    return store.workflow.cleanup_apply({**params, 'commandId':command + '-apply',
        'expectedRevision':planned['targetRevision'], 'planId':planned['plan']['planId'],
        'confirmPath':planned['plan']['path']}, console_authority=authority)


def prune_old_runtimes(store) -> dict:
    """Upgrade-authorized retention; other storage categories remain untouched."""
    planned = plan(store, {})
    for candidate in planned['candidates']:
        if candidate['category'] != 'runtimes':
            candidate['eligible'] = False
    _atomic_storage_json(store.directory / 'storage' / (planned['planId'] + '.json'), planned)
    return apply(store, {'planId':planned['planId'], 'commandId':'upgrade-' + planned['planId'], 'confirm':True})


def _save_receipt(path: Path, result: dict) -> None:
    _atomic_storage_json(path, result)
