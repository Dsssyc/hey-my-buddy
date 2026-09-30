"""Guarded storage inventory and confirmed reclamation; no process termination."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from . import locking
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

from . import private_dirs, schemas
from .db import utc_now
from .errors import BoardError
from .runtime import DEFAULT_RUNTIME_ROOT, is_ready

GRACE_SECONDS = 3 * 86400
LABELS = {'harnesses':'Harness 私有会话', 'zcode':'ZCode 旧私有主目录', 'workspaces':'受管检出',
          'runtimes':'运行时', 'backup':'备份', 'durable':'看板与记录'}


def tree_info(path: Path) -> tuple[int, str, bool]:
    """Size and mutation fingerprint without traversing any reparse point."""
    size = 0
    digest = hashlib.sha256()
    safe = private_dirs.linked_component(path) is None
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
        if not private_dirs.linked(item) and item.is_file():
            size += stat.st_size
        digest.update(f'{item.relative_to(path)}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_mode}\n'.encode())
    return size, digest.hexdigest(), safe


def process_inventory(state: Path) -> tuple[list[dict], list[str], bool]:
    """Only observations. Commands and unrelated process details never escape."""
    try:
        listing = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return [], [], False
    orphans, commands = [], []
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        commands.append(command)
        if not re.search(r' -m buddy\.(daemon|worker\.supervisor)(?: |$)', command):
            continue
        kind = 'supervisor' if 'buddy.worker.supervisor' in command else 'daemon'
        match = re.search(r'--state-dir\s+(\S+)', command)
        observed_state = match.group(1) if match else None
        # Daemon argv can omit its state root. Control identity provides one exact
        # positive match, but no missing PID is interpreted as stopped.
        try:
            current = json.loads((state / 'control.json').read_text())
            if int(pid) == current.get('pid'):
                observed_state = str(state)
        except (OSError, ValueError):
            pass
        if observed_state is None:
            try:
                opened = subprocess.run(['lsof','-nP','-p',pid,'-Fn'], capture_output=True, text=True, timeout=5)
                roots = {str(Path(line[1:].removesuffix(' (deleted)')).parent) for line in opened.stdout.splitlines()
                         if line.startswith('n/') and line.removesuffix(' (deleted)').endswith(('/board-owner.lock','/control-daemon.lock'))}
                if len(roots) == 1:
                    observed_state = roots.pop()
            except (OSError, subprocess.SubprocessError):
                pass
        runtime_match = re.search(r'(/\S+/(?:runtime|runtime-root)/[^/ ]+)/', command)
        if observed_state is not None:
            observed_state = str(Path(observed_state).resolve())
        if observed_state != str(state):
            orphans.append({'pid': int(pid), 'kind': kind, 'stateDir': observed_state,
                            'runtimeDir': runtime_match.group(1) if runtime_match else None})
    return orphans, commands, True


def runtime_usage(directory: Path, commands: list[str], known: bool) -> list[str]:
    if not known or os.name == 'nt':
        return ['process-inspection-unavailable']
    if any(str(directory) in command for command in commands):
        return ['runtime-in-use']
    # argv alone misses mapped/open files and working directories. Inspect this
    # user's file table; any incomplete observation retains the runtime.
    try:
        result = subprocess.run(['lsof', '-nP', '-u', str(os.getuid()), '-Fn'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ['process-inspection-unavailable']
    if result.returncode or result.stderr.strip():
        return ['process-inspection-unavailable']
    if any(line.startswith('n' + str(directory) + '/') or line == 'n' + str(directory) for line in result.stdout.splitlines()):
        return ['runtime-in-use']
    return []


def _legacy_runtime_identity(directory: Path, ready: Path) -> bool:
    """Recognize an older READY marker without requiring today's asset layout."""
    if re.fullmatch(r'[0-9a-f]{32}', directory.name) is None:
        return False
    try:
        marker = _read_storage_json(ready)
    except (BoardError, OSError, UnicodeError):
        return False
    interpreter = marker.get('python')
    if not isinstance(interpreter, str):
        return False
    python_path = Path(interpreter)
    if not python_path.is_absolute() or '..' in python_path.parts or not python_path.is_relative_to(directory):
        return False
    return (marker.get('state') == 'READY' and marker.get('contentId') == directory.name
            and marker.get('runtimeRoot') == str(directory.parent))


def _zcode_reasons(store, connection, run, task, now: float) -> list[str]:
    if run is None or task is None:
        return ['owner-unproven']
    # Cancelled runs can currently continue. Preserve them rather than changing
    # existing Host recovery semantics merely to make reclaimable bytes bigger.
    shutdown = store.workflow.shutdown_summary(connection, run['run_id'])
    reasons = []
    if not all(shutdown.get(k) is True for k in ('selfConfirmed', 'descendantsConfirmed')):
        reasons.append('shutdown-unconfirmed')
    if run['state'] != 'accepted' or task['acceptance_verdict'] != 'accepted':
        return ['continuation-supported', *reasons]
    try:
        accepted = datetime.fromisoformat(task['accepted_at'].replace('Z', '+00:00')).timestamp()
        if now - accepted < GRACE_SECONDS:
            reasons.append('grace-period')
    except (ValueError, TypeError, AttributeError):
        reasons.append('acceptance-time-unproven')
    if connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued')", (run['run_id'],)).fetchone():
        reasons.append('pending-continuations')
    if connection.execute("SELECT 1 FROM workflow_requests WHERE run_id=? AND state='open'", (run['run_id'],)).fetchone():
        reasons.append('open-requests')
    return reasons


def inspect(store) -> dict:
    from . import workspace
    state = store.directory.resolve()
    rows = []
    orphaned, commands, known = process_inventory(state)
    now = time.time()
    def add(category, path, reasons, **metadata):
        linked = private_dirs.linked_component(path)
        if linked is not None:
            size, fingerprint, safe = 0, '', False
        else:
            size, fingerprint, safe = tree_info(path)
        if not safe:
            reasons = [*reasons, 'linked-path']
        if linked is None and not path.exists():
            return
        rows.append({'id': hashlib.sha256(str(path).encode()).hexdigest()[:24], 'category': category,
                     'path': str(path), 'bytes': size, 'eligible': not reasons, 'reasons': sorted(set(reasons)),
                     'fingerprint': fingerprint, **metadata})
    # Snapshot board facts first. Filesystem walks and Git inspection must never
    # hold an authoritative database transaction open.
    native_owners = {}
    workspace_inputs = []
    with store.db.read() as connection:
        runs = connection.execute('SELECT * FROM workflow_runs').fetchall()
        for run in runs:
            task = connection.execute('SELECT * FROM tasks WHERE task_id=?', (run['run_id'],)).fetchone()
            reasons = _zcode_reasons(store, connection, run, task, now)
            native_owners[hashlib.sha256(run['run_id'].encode()).hexdigest()] = (run['run_id'], reasons)
            for routed in store.workflow._routing_tasks(connection, run['run_id']):
                native_owners[hashlib.sha256(routed['task_id'].encode()).hexdigest()] = (run['run_id'], reasons)
            manifest = json.loads(run['workspace_manifest_json'] or '{}')
            if not manifest:
                continue
            retained = store.workflow._allocation_provenance(connection, run)
            reasons, _, _ = store.workflow._cleanup_reasons(connection, run, task, manifest)
            sealed = store.workflow._latest_handoff(connection, run, manifest) if not reasons else None
            workspace_inputs.append((run['run_id'], run['revision'], manifest, retained, reasons, sealed))
    home_root = state / 'harnesses/zcode'
    if any(private_dirs.linked(p) for p in (state / 'harnesses', home_root)):
        add('zcode', state / 'harnesses' if private_dirs.linked(state / 'harnesses') else home_root, ['linked-path'])
    elif home_root.exists():
        for path in sorted(home_root.iterdir()):
            if path.name in ('goals', 'accounts'):
                continue
            run_id, reasons = native_owners.get(path.name, (None, ['owner-unproven']))
            add('zcode', path, reasons, runId=run_id)
    harnesses = state / 'harnesses'
    if harnesses.exists() and not private_dirs.linked(harnesses):
        for adapter in sorted(private_dirs.ADAPTERS):
            adapter_root = harnesses / adapter
            if private_dirs.linked(adapter_root):
                add('harnesses', adapter_root, ['linked-path'], adapter=adapter)
                continue
            if not adapter_root.exists():
                continue
            goals = adapter_root / 'goals'
            if private_dirs.linked(goals):
                add('harnesses', goals, ['linked-path'], adapter=adapter)
                continue
            if goals.exists():
                for path in sorted(goals.iterdir()):
                    run_id, reasons = native_owners.get(path.name, (None, ['owner-unproven']))
                    add('harnesses', path, reasons, runId=run_id, adapter=adapter)
            if adapter != 'zcode':
                for path in sorted(adapter_root.iterdir()):
                    if path.name not in ('goals', 'accounts'):
                        add('harnesses', path, ['legacy-layout-unmigrated'], adapter=adapter)
    seen = set()
    for run_id, revision, manifest, retained, reasons, sealed in workspace_inputs:
        try:
            allocation = workspace.resolve_allocation(state, manifest, retained)
            if allocation is None:
                continue  # Host-owned/unrelated checkout: never measure or delete.
            path = Path(allocation['path'])
            if path in seen or not path.exists():
                continue
            seen.add(path)
            if not reasons:
                inspected = workspace.cleanup_inspect(state, manifest, sealed=sealed, retained=retained)
                reasons.extend(inspected['reasons'])
            add('workspaces', path, reasons, runId=run_id, revision=revision)
        except BoardError:
            # Invalid ownership never licenses a filesystem guess.
            continue
    runtime_root = Path(os.environ.get('BUDDY_RUNTIME_ROOT') or DEFAULT_RUNTIME_ROOT).expanduser().absolute()
    from .home import default_state_dir
    foreign_default_root = (runtime_root.resolve() == Path(DEFAULT_RUNTIME_ROOT).resolve()
                            and state != default_state_dir().resolve())
    try:
        keep = json.loads((state / 'runtime-retention.json').read_text())
        retained_ids = {keep['current'], keep['previous']}
    except (OSError, KeyError, ValueError):
        retained_ids = None
    if private_dirs.linked_component(runtime_root) is not None:
        add('runtimes', runtime_root, ['linked-path'])
    elif runtime_root.exists() and not runtime_root.is_dir():
        add('runtimes', runtime_root, ['runtime-format-unsupported'])
    elif runtime_root.exists():
        for path in sorted(runtime_root.iterdir()):
            if private_dirs.linked_component(path) is not None:
                add('runtimes', path, ['linked-path'])
                continue
            try:
                path_stat = path.lstat()
            except OSError:
                add('runtimes', path, ['runtime-identity-unproven'])
                continue
            if not stat.S_ISDIR(path_stat.st_mode):
                add('runtimes', path, ['runtime-format-unsupported'])
                continue
            ready = path / 'READY.json'
            if private_dirs.linked_component(ready) is not None:
                add('runtimes', path, ['linked-path'])
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
            if not current_ready and not _legacy_runtime_identity(path, ready):
                reasons.append('runtime-identity-unproven')
            if not reasons:
                reasons.extend(runtime_usage(path, commands, known))
            add('runtimes', path, reasons)
    add('backup', state / 'backups', ['current-backup'])
    for path in sorted(state.iterdir()):
        if path.name in {'workspaces','harnesses','backups'} or path == runtime_root:
            continue
        add('durable', path, ['durable-record'])
    if harnesses.exists() and not private_dirs.linked(harnesses):
        for path in sorted(harnesses.iterdir()):
            if path.name not in private_dirs.ADAPTERS:
                add('durable', path, ['durable-record'])
                continue
            accounts = path / 'accounts'
            if accounts.exists() or private_dirs.linked(accounts):
                add('durable', accounts, ['user-account-protected'])
    categories = []
    for key, label in LABELS.items():
        members = [r for r in rows if r['category'] == key]
        total_bytes = tree_info(state / 'workspaces')[0] if key == 'workspaces' else sum(r['bytes'] for r in members)
        categories.append({'id': key, 'label': label, 'bytes': total_bytes,
                           'reclaimableBytes': sum(r['bytes'] for r in members if r['eligible']),
                           'count': len(members), 'eligibleCount': sum(r['eligible'] for r in members),
                           'reasons': sorted({reason for r in members for reason in r['reasons']})})
    return {'createdAt': utc_now(), 'categories': categories, 'candidates': rows, 'orphanProcesses': orphaned}


def _guard_storage(path: Path) -> None:
    linked = private_dirs.linked_component(path)
    if linked is not None:
        raise BoardError('STORAGE_UNSAFE', 'Storage path contains a link or reparse point', path=str(linked))


def _read_storage_json(path: Path, *, missing_ok: bool = False) -> dict | None:
    _guard_storage(path)
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
    _guard_storage(path)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    _guard_storage(temporary)
    try:
        try:
            fd = private_dirs.open_regular_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except BoardError as error:
            raise BoardError('STORAGE_UNSAFE', 'Storage temporary file is unsafe', path=str(temporary)) from error
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        _guard_storage(path)
        _guard_storage(temporary)
        os.replace(temporary, path)
        _guard_storage(path)
    finally:
        _guard_storage(temporary)
        temporary.unlink(missing_ok=True)


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
            not isinstance(pending.get('path'), str) or not Path(pending['path']).is_absolute() or
            pending['id'] != hashlib.sha256(pending['path'].encode()).hexdigest()[:24]):
        raise BoardError('STORAGE_UNSAFE', 'Pending removal does not match its eligible plan candidate')
    original = Path(pending['path'])
    if '..' in original.parts:
        raise BoardError('STORAGE_UNSAFE', 'Pending removal path is not a live candidate')
    root = Path(state).absolute()
    if category == 'runtimes':
        allowed_parent = Path(os.environ.get('BUDDY_RUNTIME_ROOT') or DEFAULT_RUNTIME_ROOT).expanduser().absolute()
        allowed = original.parent == allowed_parent
    elif category == 'zcode':
        allowed = original.parent == root / 'harnesses' / 'zcode'
    else:
        allowed = any(original.parent == root / 'harnesses' / adapter / 'goals'
                      for adapter in private_dirs.ADAPTERS)
    tomb = original.with_name('.reclaim-' + planned['planId'] + '-' + original.name)
    has_identity = 'device' in pending or 'inode' in pending
    if (not allowed or pending.get('tomb') != str(tomb) or
            has_identity and (not isinstance(pending.get('device'), int) or
                              not isinstance(pending.get('inode'), int))):
        raise BoardError('STORAGE_UNSAFE', 'Pending removal path is outside its fixed storage boundary')


@contextmanager
def locked(store):
    path = store.directory / 'storage'
    _guard_storage(path)
    path.mkdir(mode=0o700, exist_ok=True)
    _guard_storage(path)
    if not path.is_dir():
        raise BoardError('STORAGE_UNSAFE', 'Storage plan root must be a directory', path=str(path))
    lock_path = path / '.lock'
    _guard_storage(lock_path)
    try:
        fd = private_dirs.open_regular_fd(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
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
            _finish_pending(result, receipt)
        current = {row['id']: row for row in inspect(store)['candidates']}
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
            if (fresh is None or not fresh['eligible'] or
                    any(fresh.get(key) != candidate.get(key) for key in ('category', 'path', 'fingerprint'))):
                result['skipped'].append({'id': candidate['id'], 'path': candidate['path'], 'reasons': fresh['reasons'] if fresh and not fresh['eligible'] else ['candidate-changed']})
                continue
            try:
                if candidate['category'] == 'workspaces':
                    authority = {'sessionId': 'storage-maintenance'}
                    request = {'runId': fresh['runId'], 'commandId': command + ':' + fresh['id'], 'expectedRevision': fresh['revision']}
                    planned_workspace = store.workflow.cleanup_plan(request, console_authority=authority)
                    wp = planned_workspace['plan']
                    store.workflow.cleanup_apply({**request, 'commandId': request['commandId'] + ':apply', 'expectedRevision': planned_workspace['targetRevision'], 'planId': wp['planId'], 'confirmPath': fresh['path']}, console_authority=authority)
                elif candidate['category'] in ('harnesses', 'zcode', 'runtimes'):
                    # Recheck immediately before atomic removal from the live name.
                    original = Path(fresh['path'])
                    if tree_info(original)[1:] != (fresh['fingerprint'], True):
                        raise BoardError('STORAGE_CHANGED', 'Candidate changed')
                    if candidate['category'] == 'runtimes':
                        _, commands, known = process_inventory(store.directory.resolve())
                        if runtime_usage(original, commands, known):
                            raise BoardError('STORAGE_CHANGED', 'Runtime usage changed')
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
                result['skipped'].append({'id': candidate['id'], 'path': candidate['path'], 'reasons': [error.code if isinstance(error, BoardError) else 'filesystem-error']})
            _save_receipt(receipt, result)
        result['complete'] = True
        _save_receipt(receipt, result)
        return result


def _finish_pending(result: dict, receipt: Path) -> None:
    """Resume only a previously journaled exact tomb; never count it as skipped."""
    pending = result['pending']
    original, tomb = Path(pending['path']), Path(pending['tomb'])
    _guard_storage(original)
    _guard_storage(tomb)
    original_exists = original.exists()
    tomb_exists = tomb.exists()
    if original_exists:
        identity = original.lstat()
        if (tomb_exists or ('device' in pending and
                           (identity.st_dev != pending['device'] or identity.st_ino != pending['inode'])) or
                tree_info(original)[1:] != (pending['fingerprint'], True)):
            raise BoardError('STORAGE_CHANGED', 'Pending removal no longer matches its candidate')
        os.rename(original, tomb)
        from .backup import sync_dir
        sync_dir(original.parent)
        tomb_exists = True
    if tomb_exists:
        identity = tomb.lstat()
        if 'device' in pending and (identity.st_dev != pending['device'] or identity.st_ino != pending['inode']):
            raise BoardError('STORAGE_CHANGED', 'Pending tomb no longer matches its candidate')
        private_dirs.remove_tree(tomb)
        from .backup import sync_dir
        sync_dir(tomb.parent)
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
    from .backup import sync_dir
    _atomic_storage_json(path, result)
    sync_dir(path.parent)
