"""Idle-only package launcher cutover with a verified backup and rollback."""
from __future__ import annotations
from contextlib import ExitStack, contextmanager, closing
from . import locking
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
import uuid

from . import backup, runtime, schemas
from .contracts import CONTRACT_VERSION
from .db import PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION, utc_now
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
                locking.lock(fd, blocking=False)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BoardError('UPGRADE_BUSY', 'An owner still holds the required lock', path=str(path))
                time.sleep(.1)
        yield fd
    finally:
        os.close(fd)


def idle_snapshot(state: Path, *, event_head: int | None = None) -> dict:
    with closing(sqlite3.connect((state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if schema is None or str(schema[0]) not in (str(SCHEMA_VERSION), str(PREVIOUS_SCHEMA_VERSION)):
            raise BoardError('UNSUPPORTED_SCHEMA', f'Upgrade accepts schema {PREVIOUS_SCHEMA_VERSION} or {SCHEMA_VERSION} only; no conversion was performed')
        active = connection.execute("SELECT task_id,state FROM tasks WHERE state IN ('queued','running','cancelling','reconciliation-needed')").fetchall()
        unresolved = connection.execute("SELECT attempt_id FROM attempts WHERE execution_state!='finished' OR shutdown_confirmed!=1").fetchall()
        if active or unresolved:
            raise BoardError('UPGRADE_NOT_IDLE', 'Upgrade requires idle work and confirmed shutdown; no task was cancelled',
                             active=[{'runId': row[0], 'state': row[1]} for row in active[:20]], unresolvedAttempts=[r[0] for r in unresolved[:20]])
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise BoardError('UPGRADE_INVALID_BOARD', 'Board integrity or foreign key checks failed')
        return {**backup.database_snapshot(connection, event_head=event_head), 'schema': int(schema[0])}


def _environment(state: Path, target: Path) -> dict:
    from .launcher import is_model_endpoint
    excluded = {
        'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'BUDDY_DEV_SOURCE', 'BUDDY_RUNTIME',
        'BUDDY_RUNTIME_IDENTITY', 'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID', 'BUDDY_AGENT_CREDENTIAL', 'BUDDY_AGENT_CREDENTIAL_FILE'}
    env = {key: value for key, value in os.environ.items() if key not in excluded and not is_model_endpoint(key)}
    marker = state / 'upgrade.json'
    if marker.exists():
        preserved = json.loads(marker.read_text()).get('environment', {})
        for key in ('BUDDY_MAX_CONCURRENT', 'BUDDY_WAIT_CAPACITY'):
            if key in preserved:
                env[key] = str(preserved[key])
    env.update(BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME=str(target), BUDDY_RUNTIME_IDENTITY='runtime:' + target.name,
               BUDDY_PYTHON=str(runtime.runtime_python(target)))
    return env


def command(state: Path, target: Path, method: str, params: dict | None = None) -> dict:
    operations = {'health':'health', 'runtime':'runtime_info', 'restart':'service_control', 'backup':'backup'}
    payload = dict(params or {})
    if method == 'restart':
        payload['action'] = 'restart'
    # No auto-start while holding the startup fence, including a stale endpoint.
    script = "import json,sys; from pathlib import Path; from buddy.client import BoardClient; print(json.dumps(BoardClient(Path(sys.argv[1]),autostart=False).call(sys.argv[2],json.loads(sys.argv[3]))))"
    result = subprocess.run([str(runtime.runtime_python(target)), '-c', script, str(state), operations[method], json.dumps(payload)],
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
        child = subprocess.Popen([str(runtime.runtime_python(target)), '-m', 'buddy.daemon'], cwd=target,
                                 env=_environment(state, target), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    finally:
        os.close(log)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if child.poll() is not None:
            from .launcher import read_private
            failure = read_private(state / 'startup-error.json') or {}
            if failure.get('pid') == child.pid and isinstance(failure.get('code'), str):
                raise BoardError(failure['code'], str(failure.get('message') or 'Replacement daemon failed'))
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
    backup.recover(state)
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
    if health.get('runtimeContentId') != target.name or not health.get('runtimeStable') or health.get('schemaVersion') != before.get('schema', SCHEMA_VERSION):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Replacement runtime identity or schema did not match')
    settings = before.get('runtimeSettings', {})
    if any(health.get(key) != value for key, value in settings.items()):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Configured runtime capacity changed')
    actual = idle_snapshot(state, event_head=before.get('eventHead'))
    if set(actual['tables']) != set(before['tables']):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Database table inventory changed')
    changed = [name for name, value in before['fingerprints'].items() if actual['fingerprints'].get(name) != value]
    if changed:
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Retained database values changed', tables=changed)
    inspected = command(state, target, 'runtime')
    if inspected.get('leaks'):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Runtime still depends on replaceable source paths')
    return {'runtimeContentId': target.name, 'schemaVersion': before.get('schema', SCHEMA_VERSION), 'retainedDataFingerprints': True, 'sourceLeaks': []}


def migrate_board(state: Path, before: dict) -> tuple[dict, dict]:
    """Migrate the idle, backed-up board in place; prove untouched tables kept their values."""
    from . import migrations
    board = state / 'board.sqlite3'
    def meta_rows():
        with closing(sqlite3.connect(board.as_uri() + '?mode=ro', uri=True)) as connection:
            return sorted(row for row in connection.execute('SELECT key, value FROM meta') if row[0] != 'schema_version')
    meta_before = meta_rows()
    try:
        with closing(sqlite3.connect(board, isolation_level=None, timeout=10)) as connection:
            summary = migrations.migrate_13_to_14(connection)
    except (sqlite3.Error, ValueError) as error:
        raise BoardError('UPGRADE_MIGRATION_FAILED', f'Board migration failed: {error}') from None
    expected = idle_snapshot(state, event_head=before.get('eventHead'))
    changed = [name for name, value in before['fingerprints'].items()
               if name not in migrations.MIGRATED_TABLES and expected['fingerprints'].get(name) != value]
    if changed or meta_rows() != meta_before or expected.get('schema') != SCHEMA_VERSION:
        raise BoardError('UPGRADE_MIGRATION_FAILED', 'Migration changed data outside its declared tables', tables=changed)
    expected['runtimeSettings'] = before.get('runtimeSettings', {})
    return summary, expected


def _skill_paths(journal: dict) -> tuple[Path, Path, Path] | None:
    if not journal.get('skillTarget'):
        return None
    from .skill_install import agent_skills_home, claude_skills_home, SKILL
    import re
    try:
        target, staged, previous = (Path(journal[name]) for name in ('skillTarget', 'skillStage', 'skillPrevious'))
        expected = agent_skills_home().resolve() / SKILL
        suffix = staged.name.removeprefix('.buddy-upgrade-stage-')
        if (not all(path.is_absolute() and not path.is_symlink() for path in (target, staged, previous))
                or target.resolve() != expected or staged.parent.resolve() != expected.parent
                or previous.parent.resolve() != expected.parent
                or re.fullmatch(r'[0-9a-f]{32}', suffix) is None
                or staged.name != '.buddy-upgrade-stage-' + suffix
                or previous.name != '.buddy-upgrade-previous-' + suffix):
            raise ValueError('unsafe skill paths')
        if journal.get('claudeLink') and Path(journal['claudeLink']).absolute() != claude_skills_home() / SKILL:
            raise ValueError('unsafe Claude link')
        return target, staged, previous
    except (KeyError, TypeError, ValueError):
        raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal skill paths do not match this installation') from None


def _restore_skill(journal: dict) -> None:
    paths = _skill_paths(journal)
    if paths is None:
        return
    target, staged, previous = paths
    if previous.exists():
        if target.exists():
            shutil.rmtree(target)
        previous.rename(target)
    elif journal.get('skillHadPrevious') is False and target.exists() and not staged.exists():
        shutil.rmtree(target)
    if staged.exists():
        shutil.rmtree(staged)
    link = journal.get('claudeLink')
    if journal.get('claudeLinkWasMissing') and link and Path(link).is_symlink() and Path(os.path.realpath(link)) == target:
        Path(link).unlink()


def _publish_skill(journal: dict) -> None:
    paths = _skill_paths(journal)
    if paths is None:
        return
    target, staged, previous = paths
    if target.exists():
        target.rename(previous)
    staged.rename(target)
    from .skill_install import _link_claude
    linked = _link_claude(target)
    if linked['status'] == 'linked':
        journal['createdClaudeLink'] = linked['path']


def _verify_skill_generation(journal: dict, target: Path, health: dict) -> None:
    paths = _skill_paths(journal)
    if paths is None:
        return
    from .skill_install import _marker
    skill = paths[1] if paths[1].exists() else paths[0]
    marker = _marker(skill)
    ready = runtime.read_ready(target)
    if (marker is None or marker.get('contract') != health.get('contractVersion')
            or marker.get('sourceCommit') != ready.get('sourceCommit')):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Skill, launcher and runtime generation do not match')


def _finish_skill(journal: dict) -> None:
    paths = _skill_paths(journal)
    if paths is None:
        return
    _, staged, previous = paths
    for path in (previous, staged):
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def upgrade(params: dict, *, skill_source: Path | None = None, skill_target: Path | None = None) -> dict:
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
    preserved = {'BUDDY_MAX_CONCURRENT':str(health_before['maxConcurrent']), 'BUDDY_WAIT_CAPACITY':str(health_before['waitCapacity'])}
    runtime_settings = {'maxConcurrent':health_before['maxConcurrent'], 'waitCapacity':health_before['waitCapacity']}
    before = idle_snapshot(state)
    before['runtimeSettings'] = runtime_settings
    # Dependency installation cannot disturb the old daemon; materialize before
    # taking the short ownership fence and preserve both generations on failure.
    materialized = runtime.materialize(root=skill_source / 'package') if skill_source is not None else runtime.materialize()
    target = Path(materialized['runtimeDir'])
    if previous == target and skill_source is None:
        return {'upgraded': False, 'reason': 'already-current', 'runtimeContentId': target.name}
    marker = state / 'upgrade.json'
    with file_lock(state / 'upgrade.lock'), file_lock(state / 'control-start.lock'):
        if marker.exists():
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'An interrupted upgrade journal exists; recover it before another upgrade')
        attached = probe(state, previous)
        if attached is None or attached.get('serviceId') != endpoint.get('serviceId'):
            raise BoardError('UPGRADE_IDENTITY_CHANGED', 'The installed service changed during runtime preparation')
        before = idle_snapshot(state)
        before['runtimeSettings'] = runtime_settings
        skill_stage = None
        skill_previous = None
        if skill_source is not None:
            if skill_target is None or skill_target.is_symlink():
                raise BoardError('SKILL_TARGET_CONFLICT', 'Upgrade skill target is missing or linked')
            from .skill_install import _marker, _is_buddy_skill
            if skill_target.exists() and not (_marker(skill_target) or _is_buddy_skill(skill_target)):
                raise BoardError('SKILL_TARGET_CONFLICT', 'Upgrade skill target is not a buddy skill')
            suffix = uuid.uuid4().hex
            skill_stage = skill_target.with_name(f'.buddy-upgrade-stage-{suffix}')
            skill_previous = skill_target.with_name(f'.buddy-upgrade-previous-{suffix}')
            shutil.copytree(skill_source, skill_stage, symlinks=True)
            from .skill_install import write_runtime_hint
            write_runtime_hint(skill_stage, target)
        backup_token = secrets.token_urlsafe(32)
        journal = {'startedAt': utc_now(), 'previous': str(previous), 'target': str(target), 'phase': 'preflight', 'before':before, 'environment':preserved, 'backupTokenHash':hashlib.sha256(backup_token.encode()).hexdigest()}
        if skill_stage is not None:
            from .skill_install import claude_skills_home, SKILL
            claude_link = claude_skills_home() / SKILL
            journal.update(skillTarget=str(skill_target), skillStage=str(skill_stage), skillPrevious=str(skill_previous),
                           skillHadPrevious=skill_target.exists(), claudeLink=str(claude_link),
                           claudeLinkWasMissing=not claude_link.exists() and not claude_link.is_symlink())
        write_journal(marker, journal)
        backed_up = None
        active = previous
        detached = False
        try:
            # A request may have entered the previous daemon before its entry
            # fence became visible. Recheck after fencing, and take the backup
            # only after detach proves there are no remaining database writers.
            before = idle_snapshot(state)
            before['runtimeSettings'] = runtime_settings
            journal['before'] = before
            write_journal(marker, journal)
            # The exclusive maintenance owner uses the same backup implementation
            # after all previous daemon writers have stopped.
            detach(state, previous)
            detached = True
            with file_lock(state / 'control-daemon.lock'), file_lock(state / 'board-owner.lock'):
                before = idle_snapshot(state)
                before['runtimeSettings'] = runtime_settings
                journal['before'] = before
                if backed_up is None:
                    backed_up = backup.create(BoardStore(state), runtime_identity={'identity': identity}, plugin_commit=runtime.read_ready(previous).get('sourceCommit'), contract_version=endpoint['contractVersion'])
                stored = backup.verify(Path(backed_up['path']))
                if stored.get('databaseSnapshot') is not None:
                    before = {**stored['databaseSnapshot'], 'schema': stored.get('schema'), 'runtimeSettings':runtime_settings}
                    journal['before'] = before
                journal.update(phase='backed-up', backup=backed_up)
                write_journal(marker, journal)
                expected = before
                if before.get('schema') == PREVIOUS_SCHEMA_VERSION:
                    migration, expected = migrate_board(state, before)
                    journal.update(phase='migrated', migration=migration, expected=expected)
                    write_journal(marker, journal)
            active = target
            health = start(state, target)
            evidence = verify_started(state, target, health, expected)
            _verify_skill_generation(journal, target, health)
            journal.update(phase='skill-switching', expected=expected)
            write_journal(marker, journal)
            _publish_skill(journal)
            write_journal(marker, journal)
            from .launcher import write_active_runtime
            write_active_runtime(state, target, preserved)
            journal.update(phase='verified', verification=evidence)
            write_journal(marker, journal)
            write_journal(state / 'upgrade-last.json', journal)
            clear_journal(marker)
            _finish_skill(journal)
            try:
                from .storage import prune_old_runtimes
                write_journal(state / 'runtime-retention.json', {'current':target.name, 'previous':previous.name})
                pruning = prune_old_runtimes(BoardStore(state))
            except Exception as cleanup_error:
                pruning = {'complete':False, 'error':getattr(cleanup_error, 'code', type(cleanup_error).__name__), 'note':'Upgrade verified; cleanup can be retried independently'}
            return {'upgraded':True, 'backup':backed_up, 'verification':evidence, 'previousRuntime':previous.name,
                    'rollback':None, 'runtimePruning':pruning}
        except Exception as failure:
            try:
                _restore_skill(journal)
                if active == previous and not detached:
                    surviving = probe(state, previous)
                    if surviving is not None:
                        from .launcher import write_active_runtime
                        write_active_runtime(state, previous, preserved)
                        clear_journal(marker)
                        error = failure.payload() if isinstance(failure, BoardError) and failure.code == 'UPGRADE_NOT_IDLE' else {'code':'UPGRADE_NOT_SWITCHED','message':'Upgrade could not detach; the previous service remains available'}
                        return {'upgraded':False, 'error':error, 'failure':getattr(failure,'code',type(failure).__name__)}
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
                from .launcher import write_active_runtime
                write_active_runtime(state, previous, preserved)
                journal.update(phase='rolled-back', rollback=evidence, failure=getattr(failure, 'code', type(failure).__name__))
                write_journal(state / 'upgrade-last.json', journal)
                clear_journal(marker)
                error = failure.payload() if backed_up is None and isinstance(failure, BoardError) and failure.code == 'UPGRADE_NOT_IDLE' else {'code':'UPGRADE_ROLLED_BACK','message':'Upgrade failed; the previous runtime and backup were restored'}
                return {'upgraded':False, 'error':error,
                        'backup':backed_up, 'rollback':evidence, 'failure':journal['failure']}
            except Exception as recovery:
                journal.update(phase='recovery-required', failure=getattr(failure,'code',type(failure).__name__), recoveryFailure=getattr(recovery,'code',type(recovery).__name__))
                write_journal(marker, journal)
                raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Automatic rollback could not prove recovery; journal and backup retained', journal=str(marker)) from recovery


def recover(state: Path, root: Path) -> dict:
    """Resume an interrupted journal under both launcher fences; never cancel work."""
    marker = state / 'upgrade.json'
    journal = json.loads(marker.read_text())
    _skill_paths(journal)  # Validate destructive recovery paths before touching a service.
    from .launcher import write_active_runtime
    previous, target = Path(journal['previous']), Path(journal['target'])
    for path in (previous, target):
        if path.parent.resolve() != root or path.is_symlink() or not runtime.is_ready(path):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal runtime identity cannot be verified')
    target_health = probe(state, target)
    previous_health = probe(state, previous)
    if journal.get('phase') == 'verified' and target_health is not None:
        evidence = verify_started(state, target, target_health, journal.get('expected', journal['before']))
        _verify_skill_generation(journal, target, target_health)
        write_active_runtime(state, target, journal.get('environment'))
        write_journal(state / 'upgrade-last.json', journal)
        _finish_skill(journal)
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
        write_active_runtime(state, previous, journal.get('environment'))
        _restore_skill(journal)
        clear_journal(marker)
        return {'upgraded':False, 'recoveredJournal':True, 'rollback':{'runtimeContentId':previous.name,'workPreserved':True}}
    if target_health is not None or previous_health is not None:
        idle_snapshot(state)
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
    write_active_runtime(state, previous, journal.get('environment'))
    _restore_skill(journal)
    journal.update(phase='rolled-back', rollback=evidence)
    write_journal(state / 'upgrade-last.json', journal)
    clear_journal(marker)
    return {'upgraded':False, 'recoveredJournal':True, 'backup':journal.get('backup'), 'rollback':evidence}
