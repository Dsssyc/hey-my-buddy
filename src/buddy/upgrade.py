"""Idle-only package launcher cutover with a verified backup and rollback."""
from __future__ import annotations
from contextlib import ExitStack, contextmanager, closing
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import sqlite3
import subprocess
import time

from . import backup, runtime, schemas
from .contracts import CONTRACT_VERSION
from .db import SCHEMA_VERSION, utc_now
from .errors import BoardError
from .transport import get_state_dir


def write_journal(path: Path, value: dict) -> None:
    from .daemon import atomic_json
    atomic_json(path, value)
    backup.sync_dir(path.parent)


def clear_journal(path: Path) -> None:
    path.unlink()
    backup.sync_dir(path.parent)


@contextmanager
def file_lock(path: Path, *, timeout: float = 0):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BoardError('UPGRADE_BUSY', 'An owner still holds the required lock', path=str(path))
                time.sleep(.1)
        yield fd
    finally:
        os.close(fd)


def idle_snapshot(state: Path) -> dict:
    with closing(sqlite3.connect((state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if schema is None or str(schema[0]) != str(SCHEMA_VERSION):
            raise BoardError('UNSUPPORTED_SCHEMA', 'Upgrade accepts schema 12 only; no conversion was performed')
        active = connection.execute("SELECT task_id,state FROM tasks WHERE state IN ('queued','running','cancelling','reconciliation-needed')").fetchall()
        unresolved = connection.execute("SELECT attempt_id FROM attempts WHERE execution_state!='finished' OR shutdown_confirmed!=1").fetchall()
        if active or unresolved:
            raise BoardError('UPGRADE_NOT_IDLE', 'Upgrade requires idle work and confirmed shutdown; no task was cancelled',
                             active=[{'runId': row[0], 'state': row[1]} for row in active[:20]], unresolvedAttempts=[r[0] for r in unresolved[:20]])
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise BoardError('UPGRADE_INVALID_BOARD', 'Board integrity or foreign key checks failed')
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        fingerprints = {}
        for name in tables:
            if name in {'workers', 'events'}:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), default=lambda value: value.hex()) for row in connection.execute('SELECT * FROM ' + quoted))
            fingerprints[name] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()
        return {'tables': {name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"','""') + '"').fetchone()[0] for name in tables}, 'fingerprints': fingerprints}


def _environment(state: Path, target: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in {
        'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'BUDDY_DEV_SOURCE', 'BUDDY_RUNTIME',
        'BUDDY_RUNTIME_IDENTITY', 'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID', 'BUDDY_AGENT_CREDENTIAL', 'BUDDY_AGENT_CREDENTIAL_FILE'}}
    env.update(BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME=str(target), BUDDY_RUNTIME_IDENTITY='runtime:' + target.name,
               BUDDY_PYTHON=str(target / 'venv/bin/python'))
    return env


def command(state: Path, target: Path, method: str, params: dict | None = None) -> dict:
    operations = {'health':'health', 'runtime':'runtime_info', 'restart':'service_control', 'backup':'backup'}
    payload = dict(params or {})
    if method == 'restart':
        payload['action'] = 'restart'
    # No auto-start while holding the startup fence, including a stale endpoint.
    script = "import json,sys; from pathlib import Path; from buddy.client import BoardClient; print(json.dumps(BoardClient(Path(sys.argv[1]),autostart=False).call(sys.argv[2],json.loads(sys.argv[3]))))"
    result = subprocess.run([str(target / 'venv/bin/python'), '-c', script, str(state), operations[method], json.dumps(payload)],
                            env=_environment(state, target), cwd=target, capture_output=True, text=True, timeout=45)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise BoardError('UPGRADE_CLIENT_FAILED', 'Runtime client did not return a valid envelope', method=method)
    if result.returncode or 'error' in data:
        error = data.get('error', {})
        raise BoardError(error.get('code', 'UPGRADE_CLIENT_FAILED'), error.get('message', 'Runtime command failed'))
    return data


def probe(state: Path, target: Path) -> dict | None:
    try:
        health = command(state, target, 'health')
        if health.get('runtimeContentId') == target.name and health.get('runtimeStable'):
            return health
    except (BoardError, subprocess.SubprocessError, OSError):
        pass
    return None


def retire_workers(state: Path, *, timeout: float = 35) -> None:
    from .worker.worker import RETIRE_REQUEST_NAME
    # Exact private worker directories, including explicitly started supervisors;
    # retirement is cooperative and cannot interrupt a running child.
    if (state / 'workers').is_symlink():
        raise BoardError('UPGRADE_UNSAFE', 'Worker root cannot be linked')
    locks = sorted((state / 'workers').glob('*/supervisor.lock'))
    for lock in locks:
        if lock.parent.is_symlink() or lock.is_symlink():
            raise BoardError('UPGRADE_UNSAFE', 'Worker identity is linked')
        write_journal(lock.parent / RETIRE_REQUEST_NAME, {'requestedAt': utc_now(), 'reason': 'idle upgrade'})
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        for lock in locks:
            stack.enter_context(file_lock(lock, timeout=max(0, deadline - time.monotonic())))


def detach(state: Path, target: Path) -> None:
    try:
        command(state, target, 'restart', {'drainSeconds': 0, 'reason': 'coordinated idle upgrade'})
    except (BoardError, subprocess.SubprocessError):
        # A lost restart reply is not a stop receipt. The ownership locks below
        # must still be acquired before inspecting or restoring any state.
        pass
    with file_lock(state / 'control-daemon.lock', timeout=30), file_lock(state / 'board-owner.lock', timeout=30):
        # Reject work admitted in the small window before an old-version daemon
        # detached. The caller then restores that runtime without touching work.
        idle_snapshot(state)
        retire_workers(state)


def start(state: Path, target: Path) -> dict:
    log = os.open(state / 'upgrade-start.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        child = subprocess.Popen([str(target / 'venv/bin/python'), '-m', 'buddy.daemon'], cwd=target,
                                 env=_environment(state, target), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    finally:
        os.close(log)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise BoardError('UPGRADE_START_FAILED', 'Replacement daemon exited; see private upgrade-start.log')
        try:
            endpoint = json.loads((state / 'control.json').read_text())
            if endpoint.get('pid') == child.pid:
                return command(state, target, 'health')
        except (OSError, ValueError, BoardError):
            pass
        time.sleep(.2)
    # SIGTERM runs the daemon's task-cancelling stop handler. Even a rollback
    # startup may be preserving work admitted before a legacy detach, so an
    # upgrade must never use that signal as a timeout cleanup shortcut.
    raise BoardError('UPGRADE_SHUTDOWN_UNCONFIRMED', 'Replacement startup is unconfirmed; journal retained, no process was signalled')


def restore(state: Path, current: Path) -> None:
    state = state.resolve()
    current = current.resolve()
    backup.verify(current)
    snapshot = state / '.restore.sqlite3'
    with gzip.open(current / 'board.sqlite3.gz', 'rb') as source, snapshot.open('wb') as destination:
        os.chmod(snapshot, 0o600)
        shutil.copyfileobj(source, destination)
        destination.flush();os.fsync(destination.fileno())
    for suffix in ('-wal', '-shm'):
        (state / ('board.sqlite3' + suffix)).unlink(missing_ok=True)
    os.replace(snapshot, state / 'board.sqlite3')
    for source in backup._regular_files(current / 'state'):
        destination = state / source.relative_to(current / 'state')
        if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents if parent != state.parent):
            raise BoardError('UPGRADE_UNSAFE', 'Restore destination cannot be linked')
        temporary = destination.with_name('.restore-' + destination.name)
        temporary.unlink(missing_ok=True)
        backup._private_copy(source, temporary)
        os.replace(temporary, destination)
    backup.sync_dir(state)


def verify_started(state: Path, target: Path, health: dict, before: dict) -> dict:
    if health.get('runtimeContentId') != target.name or not health.get('runtimeStable') or health.get('schemaVersion') != SCHEMA_VERSION:
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Replacement runtime identity or schema did not match')
    actual = idle_snapshot(state)
    changed = [name for name, value in before['fingerprints'].items() if actual['fingerprints'].get(name) != value]
    if changed:
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Retained database values changed', tables=changed)
    inspected = command(state, target, 'runtime')
    if inspected.get('leaks'):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Runtime still depends on replaceable source paths')
    return {'runtimeContentId': target.name, 'schemaVersion': SCHEMA_VERSION, 'retainedDataFingerprints': True, 'sourceLeaks': []}


def upgrade(params: dict) -> dict:
    from .store import BoardStore
    schemas.reject_unknown(params, set(), 'upgrade')
    if os.environ.get('BUDDY_AGENT_CREDENTIAL') or os.environ.get('BUDDY_AGENT_CREDENTIAL_FILE'):
        raise BoardError('UNAUTHORIZED', 'A Worker cannot upgrade its owning service')
    state = get_state_dir().resolve()
    root = Path(os.environ.get('BUDDY_RUNTIME_ROOT') or runtime.DEFAULT_RUNTIME_ROOT).resolve()
    if (state / 'upgrade.json').exists():
        with file_lock(state / 'upgrade.lock'), file_lock(state / 'control-start.lock'):
            return recover(state, root)
    if not (state / 'control.json').exists():
        raise BoardError('UPGRADE_NOT_RUNNING', 'Start the installed service before upgrading')
    root = Path(os.environ.get('BUDDY_RUNTIME_ROOT') or runtime.DEFAULT_RUNTIME_ROOT).resolve()
    endpoint = json.loads((state / 'control.json').read_text())
    identity = endpoint.get('runtimeIdentity', '')
    if not isinstance(identity, str) or not identity.startswith('runtime:'):
        raise BoardError('UPGRADE_NO_ROLLBACK_RUNTIME', 'Upgrade needs a proven previous stable runtime')
    previous = root / identity.removeprefix('runtime:')
    if not runtime.is_ready(previous):
        raise BoardError('UPGRADE_NO_ROLLBACK_RUNTIME', 'Previous stable runtime is unavailable')
    health_before = probe(state, previous)
    if health_before is None or health_before.get('serviceId') != endpoint.get('serviceId') or health_before.get('pid') != endpoint.get('pid'):
        raise BoardError('UPGRADE_IDENTITY_CHANGED', 'The live installed service does not match its recorded runtime identity')
    before = idle_snapshot(state)
    # Dependency installation cannot disturb the old daemon; materialize before
    # taking the short ownership fence and preserve both generations on failure.
    materialized = runtime.materialize()
    target = Path(materialized['runtimeDir'])
    if previous == target:
        return {'upgraded': False, 'reason': 'already-current', 'runtimeContentId': target.name}
    marker = state / 'upgrade.json'
    with file_lock(state / 'upgrade.lock'), file_lock(state / 'control-start.lock'):
        if marker.exists():
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'An interrupted upgrade journal exists; recover it before another upgrade')
        attached = probe(state, previous)
        if attached is None or attached.get('serviceId') != endpoint.get('serviceId'):
            raise BoardError('UPGRADE_IDENTITY_CHANGED', 'The installed service changed during runtime preparation')
        before = idle_snapshot(state)
        backup_token = secrets.token_urlsafe(32)
        journal = {'startedAt': utc_now(), 'previous': str(previous), 'target': str(target), 'phase': 'preflight', 'before':before, 'backupTokenHash':hashlib.sha256(backup_token.encode()).hexdigest()}
        write_journal(marker, journal)
        backed_up = None
        active = previous
        detached = False
        try:
            # 0.15.1 has no backup RPC or admission fence. After its idle detach,
            # this exclusive maintenance owner executes the SAME service backup
            # implementation before the new runtime ever opens the board.
            if health_before.get('contractVersion') == CONTRACT_VERSION:
                backed_up = command(state, previous, 'backup', {'upgradeToken':backup_token})
                journal.update(phase='backed-up', backup=backed_up)
                write_journal(marker, journal)
            detach(state, previous)
            detached = True
            with file_lock(state / 'control-daemon.lock'), file_lock(state / 'board-owner.lock'):
                before = idle_snapshot(state)
                if backed_up is None:
                    backed_up = backup.create(BoardStore(state), runtime_identity={'identity': identity}, plugin_commit=runtime.read_ready(previous).get('sourceCommit'), contract_version=endpoint['contractVersion'])
                journal.update(phase='backed-up', backup=backed_up)
                write_journal(marker, journal)
            active = target
            health = start(state, target)
            evidence = verify_started(state, target, health, before)
            write_journal(state / 'runtime-retention.json', {'current':target.name, 'previous':previous.name})
            from .storage import prune_old_runtimes
            try:
                pruning = prune_old_runtimes(BoardStore(state))
            except Exception as cleanup_error:
                pruning = {'complete':False, 'error':getattr(cleanup_error, 'code', type(cleanup_error).__name__), 'note':'Upgrade verified; cleanup can be retried independently'}
            journal.update(phase='verified', verification=evidence, runtimePruning=pruning)
            write_journal(state / 'upgrade-last.json', journal)
            clear_journal(marker)
            return {'upgraded':True, 'backup':backed_up, 'verification':evidence, 'previousRuntime':previous.name,
                    'rollback':None, 'runtimePruning':pruning}
        except Exception as failure:
            try:
                if active == previous and not detached:
                    surviving = probe(state, previous)
                    if surviving is not None:
                        clear_journal(marker)
                        return {'upgraded':False, 'error':{'code':'UPGRADE_NOT_SWITCHED','message':'Upgrade could not detach; the previous service remains available'}, 'failure':getattr(failure,'code',type(failure).__name__)}
                endpoint_now = json.loads((state / 'control.json').read_text()) if (state / 'control.json').exists() else None
                if detached and endpoint_now and endpoint_now.get('runtimeIdentity') == 'runtime:' + active.name:
                    detach(state, active)
                if backed_up is not None:
                    with file_lock(state / 'control-daemon.lock', timeout=30), file_lock(state / 'board-owner.lock', timeout=30):
                        restore(state, Path(backed_up['path']))
                # A race admitted work before the old daemon detached: preserve it
                # and restart the previous runtime without restoring an older DB.
                recovered = start(state, previous)
                evidence = verify_started(state, previous, recovered, before) if backed_up else {'runtimeContentId':previous.name, 'workPreserved':True}
                journal.update(phase='rolled-back', rollback=evidence, failure=getattr(failure, 'code', type(failure).__name__))
                write_journal(state / 'upgrade-last.json', journal)
                clear_journal(marker)
                return {'upgraded':False, 'error':{'code':'UPGRADE_ROLLED_BACK','message':'Upgrade failed; the previous runtime and backup were restored'},
                        'backup':backed_up, 'rollback':evidence, 'failure':journal['failure']}
            except Exception as recovery:
                journal.update(phase='recovery-required', failure=getattr(failure,'code',type(failure).__name__), recoveryFailure=getattr(recovery,'code',type(recovery).__name__))
                write_journal(marker, journal)
                raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Automatic rollback could not prove recovery; journal and backup retained', journal=str(marker)) from recovery


def recover(state: Path, root: Path) -> dict:
    """Resume an interrupted journal under both launcher fences; never cancel work."""
    marker = state / 'upgrade.json'
    journal = json.loads(marker.read_text())
    previous, target = Path(journal['previous']), Path(journal['target'])
    for path in (previous, target):
        if path.parent.resolve() != root or path.is_symlink() or not runtime.is_ready(path):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal runtime identity cannot be verified')
    target_health = probe(state, target)
    previous_health = probe(state, previous)
    if journal.get('phase') == 'verified' and target_health is not None:
        evidence = verify_started(state, target, target_health, journal['before'])
        write_journal(state / 'upgrade-last.json', journal)
        clear_journal(marker)
        return {'upgraded':True, 'recoveredJournal':True, 'verification':evidence}
    if journal.get('phase') == 'preflight':
        if target_health is not None:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Unexpected target owner during preflight recovery')
        if previous_health is None:
            with file_lock(state / 'control-daemon.lock', timeout=30), file_lock(state / 'board-owner.lock', timeout=30):
                pass
            # No backup/cutover happened: preserve live workers and any work that
            # raced the old-version preflight rather than attempting to retire it.
            previous_health = start(state, previous)
        clear_journal(marker)
        return {'upgraded':False, 'recoveredJournal':True, 'rollback':{'runtimeContentId':previous.name,'workPreserved':True}}
    if target_health is not None:
        detach(state, target)
    elif previous_health is not None:
        detach(state, previous)
    with file_lock(state / 'control-daemon.lock', timeout=30), file_lock(state / 'board-owner.lock', timeout=30):
        retire_workers(state)
        saved = journal.get('backup')
        if saved is not None:
            current = state / 'backups/current'
            if Path(saved['path']).resolve() != current.resolve():
                raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal backup path is not the single current backup')
            restore(state, current)
        before = idle_snapshot(state)
    health = start(state, previous)
    evidence = verify_started(state, previous, health, before)
    journal.update(phase='rolled-back', rollback=evidence)
    write_journal(state / 'upgrade-last.json', journal)
    clear_journal(marker)
    return {'upgraded':False, 'recoveredJournal':True, 'backup':journal.get('backup'), 'rollback':evidence}
