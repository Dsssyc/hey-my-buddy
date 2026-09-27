"""Guarded storage inventory and confirmed reclamation; no process termination."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from . import schemas
from .db import utc_now
from .errors import BoardError
from .runtime import DEFAULT_RUNTIME_ROOT, is_ready

GRACE_SECONDS = 3 * 86400
LABELS = {'zcode':'ZCode 私有主目录', 'workspaces':'受管检出', 'runtimes':'运行时', 'backup':'备份', 'durable':'看板与记录'}


def tree_info(path: Path) -> tuple[int, str, bool]:
    """Size and mutation fingerprint, never traversing symlinks."""
    size = 0
    digest = hashlib.sha256()
    safe = not path.is_symlink()
    if not path.exists():
        return 0, '', safe
    paths = [path]
    if path.is_dir() and safe:
        for parent, dirs, files in os.walk(path, followlinks=False):
            paths.extend(Path(parent) / name for name in dirs + files)
    for item in sorted(paths):
        stat = item.lstat()
        if item.is_file() and not item.is_symlink():
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
    if not known:
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


def _zcode_reasons(store, connection, run, task, now: float) -> list[str]:
    if run is None or task is None:
        return ['owner-unproven']
    # Cancelled runs can currently continue. Preserve them rather than changing
    # existing Host recovery semantics merely to make reclaimable bytes bigger.
    if run['state'] != 'accepted' or task['acceptance_verdict'] != 'accepted':
        return ['continuation-supported']
    shutdown = store.workflow.shutdown_summary(connection, run['run_id'])
    reasons = []
    if not all(shutdown.get(k) is True for k in ('selfConfirmed', 'descendantsConfirmed')):
        reasons.append('shutdown-unconfirmed')
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
        size, fingerprint, safe = tree_info(path)
        if not safe:
            reasons = [*reasons, 'linked-path']
        if not path.exists():
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
            native_owners[hashlib.sha256(run['run_id'].encode()).hexdigest()] = (
                run['run_id'], _zcode_reasons(store, connection, run, task, now))
            manifest = json.loads(run['workspace_manifest_json'] or '{}')
            if not manifest:
                continue
            retained = store.workflow._allocation_provenance(connection, run)
            reasons, _, _ = store.workflow._cleanup_reasons(connection, run, task, manifest)
            sealed = store.workflow._latest_handoff(connection, run, manifest) if not reasons else None
            workspace_inputs.append((run['run_id'], run['revision'], manifest, retained, reasons, sealed))
    home_root = state / 'harnesses/zcode'
    if any(p.is_symlink() for p in (state / 'harnesses', home_root)):
        add('zcode', state / 'harnesses' if (state / 'harnesses').is_symlink() else home_root, ['linked-path'])
    elif home_root.exists():
        for path in sorted(home_root.iterdir()):
            run_id, reasons = native_owners.get(path.name, (None, ['owner-unproven']))
            add('zcode', path, reasons, runId=run_id)
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
    try:
        keep = json.loads((state / 'runtime-retention.json').read_text())
        retained_ids = {keep['current'], keep['previous']}
    except (OSError, KeyError, ValueError):
        retained_ids = None
    if any(p.is_symlink() for p in (runtime_root, *runtime_root.parents)):
        add('runtimes', runtime_root, ['linked-path'])
    elif runtime_root.exists():
        for path in sorted(runtime_root.iterdir()):
            if not path.is_dir() or not (path / 'READY.json').exists():
                continue
            reasons = (['retention-history-unproven'] if retained_ids is None else
                       ['retained-runtime'] if path.name in retained_ids else runtime_usage(path, commands, known))
            if any(p.is_symlink() for p in (runtime_root, *runtime_root.parents)):
                reasons.append('linked-path')
            if not is_ready(path):
                reasons.append('runtime-identity-unproven')
            add('runtimes', path, reasons)
    add('backup', state / 'backups', ['current-backup'])
    for path in sorted(state.iterdir()):
        if path.name in {'workspaces','harnesses','backups'} or path == runtime_root:
            continue
        add('durable', path, ['durable-record'])
    harnesses = state / 'harnesses'
    if harnesses.exists() and not harnesses.is_symlink():
        for path in sorted(harnesses.iterdir()):
            if path.name != 'zcode':
                add('durable', path, ['durable-record'])
    categories = []
    for key, label in LABELS.items():
        members = [r for r in rows if r['category'] == key]
        total_bytes = tree_info(state / 'workspaces')[0] if key == 'workspaces' else sum(r['bytes'] for r in members)
        categories.append({'id': key, 'label': label, 'bytes': total_bytes,
                           'reclaimableBytes': sum(r['bytes'] for r in members if r['eligible']),
                           'count': len(members), 'eligibleCount': sum(r['eligible'] for r in members),
                           'reasons': sorted({reason for r in members for reason in r['reasons']})})
    return {'createdAt': utc_now(), 'categories': categories, 'candidates': rows, 'orphanProcesses': orphaned}


@contextmanager
def locked(store):
    path = store.directory / 'storage'
    if path.is_symlink():
        raise BoardError('STORAGE_UNSAFE', 'Storage plan root cannot be linked')
    path.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path
    except BlockingIOError as error:
        raise BoardError('STORAGE_BUSY', 'A storage operation is already running') from error
    finally:
        os.close(fd)


def plan(store, params: dict) -> dict:
    from .daemon import atomic_json
    schemas.reject_unknown(params, set(), 'storage.plan')
    with locked(store) as directory:
        result = inspect(store)
        result.update(planId='stg-' + uuid.uuid4().hex, expiresAt=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        atomic_json(directory / (result['planId'] + '.json'), result)
        return result


def apply(store, params: dict) -> dict:
    from .daemon import atomic_json
    schemas.reject_unknown(params, {'planId', 'commandId', 'confirm'}, 'storage.apply')
    plan_id = schemas.required_string(params, 'planId', max_length=64, pattern=re.compile(r'^stg-[0-9a-f]{32}$'))
    command = schemas.required_string(params, 'commandId', max_length=128)
    if params.get('confirm') is not True:
        raise BoardError('CONFIRMATION_REQUIRED', 'Confirm the exact storage plan before applying it')
    with locked(store) as directory:
        receipt = directory / ('receipt-' + hashlib.sha256(command.encode()).hexdigest() + '.json')
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if saved['planId'] != plan_id:
                raise BoardError('CONFLICT', 'This commandId belongs to another storage plan')
            if saved.get('complete'):
                return saved
        try:
            path = directory / (plan_id + '.json')
            if path.is_symlink():
                raise ValueError()
            planned = json.loads(path.read_text())
        except (OSError, ValueError):
            raise BoardError('NOT_FOUND', 'Storage plan is unavailable')
        if not receipt.exists() and datetime.fromisoformat(planned['expiresAt']).timestamp() < time.time():
            raise BoardError('PLAN_EXPIRED', 'Storage plan expired; inspect again')
        result = {'planId': plan_id, 'removedBytes': 0, 'removed': [], 'skipped': [], 'complete': False}
        if receipt.exists():
            result = saved
        if result.get('pending'):
            _finish_pending(result, receipt)
        current = {row['id']: row for row in inspect(store)['candidates']}
        done = {row['id'] for row in result['removed'] + result['skipped']}
        for candidate in planned['candidates']:
            if not candidate['eligible'] or candidate['id'] in done:
                continue
            fresh = current.get(candidate['id'])
            if fresh is None or not fresh['eligible'] or fresh['fingerprint'] != candidate['fingerprint']:
                result['skipped'].append({'id': candidate['id'], 'path': candidate['path'], 'reasons': fresh['reasons'] if fresh and not fresh['eligible'] else ['candidate-changed']})
                continue
            try:
                if candidate['category'] == 'workspaces':
                    authority = {'sessionId': 'storage-maintenance'}
                    request = {'runId': fresh['runId'], 'commandId': command + ':' + fresh['id'], 'expectedRevision': fresh['revision']}
                    planned_workspace = store.workflow.cleanup_plan(request, console_authority=authority)
                    wp = planned_workspace['plan']
                    store.workflow.cleanup_apply({**request, 'commandId': request['commandId'] + ':apply', 'expectedRevision': planned_workspace['targetRevision'], 'planId': wp['planId'], 'confirmPath': fresh['path']}, console_authority=authority)
                elif candidate['category'] in ('zcode', 'runtimes'):
                    # Recheck immediately before atomic removal from the live name.
                    original = Path(fresh['path'])
                    if tree_info(original)[1:] != (fresh['fingerprint'], True):
                        raise BoardError('STORAGE_CHANGED', 'Candidate changed')
                    if candidate['category'] == 'runtimes':
                        _, commands, known = process_inventory(store.directory.resolve())
                        if runtime_usage(original, commands, known):
                            raise BoardError('STORAGE_CHANGED', 'Runtime usage changed')
                    tomb = original.with_name('.reclaim-' + plan_id + '-' + original.name)
                    result['pending'] = {'id':fresh['id'], 'path':str(original), 'tomb':str(tomb), 'bytes':fresh['bytes'], 'fingerprint':fresh['fingerprint']}
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
    from .daemon import atomic_json
    pending = result['pending']
    original, tomb = Path(pending['path']), Path(pending['tomb'])
    if any(p.is_symlink() for p in (original, tomb, *original.parents)):
        raise BoardError('STORAGE_UNSAFE', 'Removal path identity changed')
    if original.exists():
        if tomb.exists() or tree_info(original)[1] != pending['fingerprint']:
            raise BoardError('STORAGE_CHANGED', 'Pending removal no longer matches its candidate')
        os.rename(original, tomb)
        from .backup import sync_dir
        sync_dir(original.parent)
    if tomb.exists():
        shutil.rmtree(tomb)
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
    from .daemon import atomic_json
    planned = plan(store, {})
    for candidate in planned['candidates']:
        if candidate['category'] != 'runtimes':
            candidate['eligible'] = False
    atomic_json(store.directory / 'storage' / (planned['planId'] + '.json'), planned)
    return apply(store, {'planId':planned['planId'], 'commandId':'upgrade-' + planned['planId'], 'confirm':True})


def _save_receipt(path: Path, result: dict) -> None:
    from .daemon import atomic_json
    from .backup import sync_dir
    atomic_json(path, result)
    sync_dir(path.parent)
