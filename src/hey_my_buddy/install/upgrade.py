"""Contract-aware runtime cutover, with offline backup only for upgrades."""
from __future__ import annotations
from contextlib import contextmanager, closing, ExitStack
from ..paths import is_absolute_path
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import uuid

from ..blackboard.store import backup
from .. import private_dirs, private_files
from . import runtime
from .launcher import write_active_runtime
from ..protocol.contracts import CONTRACT_VERSION
from ..protocol.service_process import independent_service_popen
from ..blackboard.store.db import SCHEMA_VERSION, utc_now
from ..errors import BoardError

# A private C-Two 0.7.5 restart with one frozen execution released ownership
# in 5.5 seconds. Allow the seven-second observer join plus bounded client close.
OWNER_LOCK_TIMEOUT_SECONDS = 15


def write_journal(path: Path, value: dict) -> None:
    from ..private_files import publish_bytes
    publish_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def clear_journal(path: Path) -> None:
    path.unlink()
    backup._guard(path.parent)
    private_files.sync_directory(path.parent, follow_symlinks=False)


def _read_json(path: Path):
    with os.fdopen(private_dirs.open_regular_fd(path, os.O_RDONLY), 'rb') as stream:
        return json.load(stream)


@contextmanager
def file_lock(path: Path, *, timeout: float = 0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = private_dirs.open_lock_fd(path, blocking=False)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise BoardError('UPGRADE_BUSY', 'An owner still holds the required lock', path=str(path))
            time.sleep(.1)
    try:
        yield fd
    finally:
        os.close(fd)


def _idle_board(connection: sqlite3.Connection) -> int:
    schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if schema is None or schema[0] != str(SCHEMA_VERSION):
        raise BoardError('UNSUPPORTED_SCHEMA', f'Upgrade requires schema {SCHEMA_VERSION}')
    # A micro-task Host boundary has no autonomous next turn. A recorded or
    # queued continuation still has work to prepare/claim, even if the run
    # currently reports a Host boundary. Routing and helper tasks are checked
    # independently below through the complete task inventory.
    active = connection.execute(
        "SELECT t.task_id,t.state FROM tasks t"
        " WHERE t.state IN ('queued','running','cancelling','reconciliation-needed')"
        " AND NOT (t.state='queued' AND EXISTS ("
        " SELECT 1 FROM workflow_runs r WHERE r.run_id=t.task_id AND r.state='awaiting-host'"
        " AND NOT EXISTS (SELECT 1 FROM workflow_continuations c WHERE c.run_id=r.run_id"
        " AND c.state IN ('recorded','queued'))))"
    ).fetchall()
    unresolved = connection.execute("SELECT attempt_id FROM attempts WHERE execution_state!='finished' OR shutdown_confirmed!=1").fetchall()
    if active or unresolved:
        raise BoardError('UPGRADE_NOT_IDLE', 'Upgrade requires idle work and confirmed shutdown; no task was cancelled',
                         active=[{'runId': row[0], 'state': row[1]} for row in active[:20]], unresolvedAttempts=[r[0] for r in unresolved[:20]])
    return int(schema[0])


def ensure_idle(state: Path) -> int:
    with closing(sqlite3.connect((state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        return _idle_board(connection)


def idle_snapshot(state: Path, *, event_head: int | None = None) -> dict:
    with closing(sqlite3.connect((state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        schema = _idle_board(connection)
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise BoardError('UPGRADE_INVALID_BOARD', 'Board integrity or foreign key checks failed')
        snapshot = backup.database_snapshot(connection, event_head=event_head)
        return {**snapshot, 'schema': schema}


def preflight(state: Path, *, contract_changed: bool) -> dict:
    """The contract boundary decides whether the board must stop changing."""
    if contract_changed:
        return {'schema': ensure_idle(state)}
    with closing(sqlite3.connect((state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if schema is None or schema[0] != str(SCHEMA_VERSION):
        raise BoardError('UNSUPPORTED_SCHEMA', 'An unchanged contract requires the current board schema')
    return {'schema': SCHEMA_VERSION}


def _environment(state: Path, target: Path) -> dict:
    from .launcher import service_environment
    # A service started by the upgrade coordinator inherits only the explicit
    # service allowlist; the journal carries forward the operator's launch settings.
    env = service_environment()
    for key in ('HMB_DEV_SOURCE',):
        env.pop(key, None)
    marker = state / 'upgrade.json'
    if marker.exists():
        preserved = _read_json(marker).get('environment', {})
        for key in ('HMB_MAX_CONCURRENT', 'HMB_WAIT_CAPACITY'):
            if key in preserved:
                env[key] = str(preserved[key])
    else:
        from .launcher import launch_defaults
        for key, value in launch_defaults(state).items():
            env.setdefault(key, value)
    env.update(HMB_STATE_DIR=str(state), HMB_RUNTIME_ROOT=str(target.parent),
               HMB_RUNTIME=str(target), HMB_RUNTIME_IDENTITY='runtime:' + target.name)
    return env


def command(state: Path, target: Path, method: str, params: dict | None = None) -> dict:
    operations = {'health':'health', 'restart':'service_control'}
    payload = dict(params or {})
    if method == 'restart':
        payload['action'] = 'restart'
    # No auto-start while holding the startup fence, including a stale endpoint.
    # The client runs from the target's installed package.
    from .launcher import entry_modules
    client = entry_modules(target)['client']
    script = ("import json,sys; sys.stderr.reconfigure(encoding='utf-8'); "
              "from pathlib import Path; from " + client + " import BoardClient; "
              "sys.stdout.buffer.write((json.dumps(BoardClient(Path(sys.argv[1]),autostart=False).call(sys.argv[2],json.loads(sys.argv[3]))) + '\\n').encode('utf-8'))")
    result = subprocess.run([str(runtime.runtime_python(target)), '-c', script, str(state), operations[method], json.dumps(payload)],
                            env=_environment(state, target), cwd=target, capture_output=True, text=True,
                            encoding='utf-8', timeout=45)
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


def detach(state: Path, target: Path, *, require_idle: bool) -> None:
    try:
        command(state, target, 'restart', {'drainSeconds': 0})
    except (BoardError, subprocess.SubprocessError):
        # A lost restart reply is not a stop receipt. The ownership locks below
        # must still be acquired before inspecting or restoring any state.
        pass
    # The old daemon must release both ownership locks before cutover proceeds.
    with file_lock(state / 'control-daemon.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS), \
         file_lock(state / 'board-owner.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS):
        # Reject work admitted in the small window before an old-version daemon
        # detached. The caller then restores that runtime without touching work.
        if require_idle:
            ensure_idle(state)


def start(state: Path, target: Path) -> dict:
    from .launcher import entry_modules
    # Callers hold control-start.lock: the record is cleared before the spawn, so
    # every startup error that appears afterwards belongs to this start alone.
    (state / 'startup-error.json').unlink(missing_ok=True)
    log = private_dirs.open_regular_fd(state / 'upgrade-start.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        # A rollback starts the previous runtime's installed daemon.
        child = independent_service_popen([str(runtime.runtime_python(target)), '-m', entry_modules(target)['daemon']], cwd=target,
                                          env=_environment(state, target), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                          close_fds=True)
    finally:
        os.close(log)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if child.poll() is not None:
            from .launcher import read_private
            failure = read_private(state / 'startup-error.json') or {}
            if isinstance(failure.get('code'), str):
                details = failure.get('details')
                raise BoardError(failure['code'], str(failure.get('message') or 'Replacement daemon failed'),
                                 **(details if isinstance(details, dict) else {}))
            raise BoardError('UPGRADE_START_FAILED', 'Replacement daemon exited; see private upgrade-start.log')
        health = probe(state, target)
        if health is not None:
            return health
        time.sleep(.2)
    # SIGTERM runs the daemon's task-cancelling stop handler. Even a rollback
    # startup may be preserving work admitted before a legacy detach, so an
    # upgrade must never use that signal as a timeout cleanup shortcut.
    raise BoardError('UPGRADE_SHUTDOWN_UNCONFIRMED', 'Replacement startup is unconfirmed; journal retained, no process was signalled')


def _restore_guard(path: Path, *, regular: bool = False) -> None:
    if '..' in path.parts:
        raise BoardError('UPGRADE_UNSAFE', 'Restore path contains a parent traversal', path=str(path))
    if private_dirs.linked(path):
        raise BoardError('UPGRADE_UNSAFE', 'Restore path contains a link or reparse point', path=str(path))
    if regular:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise BoardError('UPGRADE_UNSAFE', 'Restore path is not an ordinary file', path=str(path))


def restore(state: Path, current: Path) -> None:
    # Keep lexical paths until the state and backup entries have been checked.
    state, current = Path(state), Path(current)
    for path in (state, current):
        _restore_guard(path)
    state, current = state.absolute(), current.absolute()
    snapshot = state / '.restore.sqlite3'
    board = state / 'board.sqlite3'
    sidecars = tuple(state / ('board.sqlite3' + suffix) for suffix in ('-wal', '-shm'))
    compressed = current / 'board.sqlite3.gz'
    for path in (snapshot, board, *sidecars):
        _restore_guard(path, regular=True)
    backup.verify(current)
    sources = list(backup._regular_files(current / 'state'))
    destinations = [(source, state / source.relative_to(current / 'state')) for source in sources]
    for source, destination in destinations:
        _restore_guard(source)
        _restore_guard(destination, regular=True)

    # A crashed earlier restore may have left an ordinary partial snapshot.
    # Remove only that ordinary entry; O_EXCL prevents a raced replacement from
    # being opened for truncation.
    try:
        snapshot.lstat()
    except FileNotFoundError:
        pass
    else:
        _restore_guard(snapshot, regular=True)
        snapshot.unlink()
    try:
        with os.fdopen(private_dirs.open_regular_fd(compressed, os.O_RDONLY), 'rb') as compressed_file, \
             gzip.GzipFile(fileobj=compressed_file, mode='rb') as source, \
             os.fdopen(private_dirs.open_regular_fd(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        for sidecar in sidecars:
            _restore_guard(sidecar, regular=True)
            sidecar.unlink(missing_ok=True)
        _restore_guard(snapshot, regular=True)
        _restore_guard(board, regular=True)
        os.replace(snapshot, board)
    finally:
        if not private_dirs.linked(snapshot):
            snapshot.unlink(missing_ok=True)
    for source, destination in destinations:
        _restore_guard(source)
        _restore_guard(destination, regular=True)
        temporary = destination.with_name('.restore-' + destination.name)
        _restore_guard(temporary, regular=True)
        try:
            temporary.unlink(missing_ok=True)
            backup._private_copy(source, temporary)
            _restore_guard(temporary, regular=True)
            _restore_guard(destination, regular=True)
            os.replace(temporary, destination)
        finally:
            if not private_dirs.linked(temporary):
                temporary.unlink(missing_ok=True)
    backup._guard(state)
    private_files.sync_directory(state, follow_symlinks=False)


def verify_started(state: Path, target: Path, health: dict, before: dict, *, contract_changed: bool = True) -> dict:
    if health.get('runtimeContentId') != target.name or not health.get('runtimeStable') or health.get('schemaVersion') != SCHEMA_VERSION:
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Replacement runtime identity or schema did not match',
                         tables=['meta'] if health.get('schemaVersion') != SCHEMA_VERSION else [])
    settings = before.get('runtimeSettings', {})
    if any(health.get(key) != value for key, value in settings.items()):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Configured runtime capacity changed')
    if contract_changed:
        actual = idle_snapshot(state, event_head=before.get('eventHead'))
        expected_tables = set(before['tables'])
        if set(actual['tables']) != expected_tables:
            raise BoardError('UPGRADE_VERIFY_FAILED', 'Database table inventory changed',
                             tables=sorted(set(actual['tables']) ^ expected_tables))
        changed = [name for name, value in before['fingerprints'].items()
                   if actual['fingerprints'].get(name) != value]
        if changed:
            raise BoardError('UPGRADE_VERIFY_FAILED', 'Retained database values changed', tables=changed)
    return {'runtimeContentId': target.name, 'schemaVersion': SCHEMA_VERSION,
            **({'retainedDataFingerprints': True} if contract_changed else {})}


def _failure_reason(error: Exception) -> dict:
    """The same structured cause goes to the returned error and the durable journal."""
    reason = error.payload() if isinstance(error, BoardError) else {'code':type(error).__name__, 'message':str(error)}
    reason.setdefault('details', {}).setdefault('tables', [])
    return reason


def _skill_paths(journal: dict) -> tuple[Path, Path, Path] | None:
    if not journal.get('skillTarget'):
        return None
    from .skill_install import agent_skills_home, claude_skills_home, _is_link, SKILL
    try:
        target, staged, previous = (Path(journal[name]) for name in ('skillTarget', 'skillStage', 'skillPrevious'))
        expected = agent_skills_home().resolve() / SKILL
        suffix = staged.name.removeprefix('.hey-my-buddy-upgrade-stage-')
        if (not all(is_absolute_path(path) and not _is_link(path) for path in (target, staged, previous))
                or target.resolve() != expected or staged.parent.resolve() != expected.parent
                or previous.parent.resolve() != expected.parent
                or re.fullmatch(r'[0-9a-f]{32}', suffix) is None
                or staged.name != '.hey-my-buddy-upgrade-stage-' + suffix
                or previous.name != '.hey-my-buddy-upgrade-previous-' + suffix):
            raise ValueError('unsafe skill paths')
        if journal.get('claudeLink') and Path(journal['claudeLink']).absolute() != claude_skills_home() / SKILL:
            raise ValueError('unsafe Claude link')
        return target, staged, previous
    except (KeyError, TypeError, ValueError):
        raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal skill paths do not match this installation') from None


def _restore_skill(journal: dict, paths: tuple[Path, Path, Path] | None) -> None:
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
    from .skill_install import _is_link, _remove_claude_link
    if journal.get('claudeLinkWasMissing') and link and _is_link(Path(link)) and Path(os.path.realpath(link)) == target:
        _remove_claude_link(Path(link))


def _publish_skill(journal: dict, paths: tuple[Path, Path, Path] | None) -> None:
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


def _verify_skill_generation(paths: tuple[Path, Path, Path] | None, target: Path, health: dict) -> None:
    if paths is None:
        return
    from .skill_install import _marker
    skill = paths[1] if paths[1].exists() else paths[0]
    marker = _marker(skill)
    ready = runtime.read_ready(target)
    if (marker is None or marker.get('contract') != health.get('contractVersion')
            or marker.get('sourceCommit') != ready.get('sourceCommit')):
        raise BoardError('UPGRADE_VERIFY_FAILED', 'Skill, launcher and runtime generation do not match')


def _finish_skill(paths: tuple[Path, Path, Path] | None) -> str | None:
    if paths is None:
        return
    _, staged, previous = paths
    errors = []
    for path in (previous, staged):
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError as error:
            errors.append(f'{path}: {error}')
    return '; '.join(errors) if errors else None


def retain_runtimes(state: Path, target: Path, previous: Path) -> dict:
    from ..blackboard.store.store import BoardStore
    from ..blackboard.tasks.storage import prune_old_runtimes
    try:
        write_journal(state / 'runtime-retention.json', {'current':target.name, 'previous':previous.name})
        store = BoardStore(state)
        try:
            return prune_old_runtimes(store)
        finally:
            store.db.close()
    except (BoardError, OSError) as cleanup_error:
        return {'complete':False, 'error':cleanup_error.code if isinstance(cleanup_error, BoardError) else type(cleanup_error).__name__,
                'message':str(cleanup_error),
                'note':'Upgrade verified; cleanup can be retried independently'}


@contextmanager
def _prepare_runtime(state: Path, *, skill_source: Path | None):
    """Fence the installed service's reclaimer until it stops.

    The storage fence covers both planning and pending-removal recovery. Even a
    freshly READY runtime can be reclaimed before the cutover retains it.
    Normal installation already serializes installers through the skill lock.
    """
    storage = state / 'storage'
    private_dirs.ensure_private_dir(storage)
    with os.fdopen(private_dirs.open_lock_fd(storage / '.lock'), 'rb'):
        installed = (runtime.materialize(root=skill_source / 'package')
                     if skill_source is not None else runtime.materialize())
        yield installed


def upgrade(state: Path, *, skill_source: Path | None = None, skill_target: Path | None = None) -> dict:
    root = runtime.runtime_root().resolve()
    if (state / 'upgrade.json').exists():
        with file_lock(state / 'upgrade.lock'), file_lock(state / 'control-start.lock'):
            return recover(state, root)
    if not (state / 'control.json').exists():
        raise BoardError('UPGRADE_NOT_RUNNING', 'Start the installed service before upgrading')
    endpoint = _read_json(state / 'control.json')
    identity = endpoint.get('runtimeIdentity', '')
    if not isinstance(identity, str) or not identity.startswith('runtime:'):
        raise BoardError('UPGRADE_NO_ROLLBACK_RUNTIME', 'Upgrade needs a proven previous stable runtime')
    previous = root / identity.removeprefix('runtime:')
    if not runtime.is_ready(previous):
        raise BoardError('UPGRADE_NO_ROLLBACK_RUNTIME', 'Previous stable runtime is unavailable')
    health_before = probe(state, previous)
    if health_before is None or health_before.get('serviceId') != endpoint.get('serviceId') or health_before.get('pid') != endpoint.get('pid'):
        raise BoardError('UPGRADE_IDENTITY_CHANGED', 'The live installed service does not match its recorded runtime identity')
    preserved = {'HMB_MAX_CONCURRENT':str(health_before['maxConcurrent']), 'HMB_WAIT_CAPACITY':str(health_before['waitCapacity'])}
    runtime_settings = {'maxConcurrent':health_before['maxConcurrent'], 'waitCapacity':health_before['waitCapacity']}
    contract_version = endpoint['contractVersion']
    contract_changed = contract_version != CONTRACT_VERSION
    before = preflight(state, contract_changed=contract_changed)
    before['runtimeSettings'] = runtime_settings
    with ExitStack() as preparation:
        # Keep the old daemon serving work, excluding its storage reclaimer while
        # preparing the runtime, before taking the short service ownership fence.
        materialized = preparation.enter_context(_prepare_runtime(state, skill_source=skill_source))
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
            skill_paths = None
            skill_stage = None
            skill_previous = None
            if skill_source is not None:
                from .skill_install import _is_link
                if skill_target is None or _is_link(skill_target):
                    raise BoardError('SKILL_TARGET_CONFLICT', 'Upgrade skill target is missing or linked')
                from .skill_install import _marker, _is_product_skill
                if skill_target.exists() and not (_marker(skill_target) or _is_product_skill(skill_target)):
                    raise BoardError('SKILL_TARGET_CONFLICT', 'Upgrade skill target is not a hey-my-buddy skill')
                suffix = uuid.uuid4().hex
                skill_stage = skill_target.with_name(f'.hey-my-buddy-upgrade-stage-{suffix}')
                skill_previous = skill_target.with_name(f'.hey-my-buddy-upgrade-previous-{suffix}')
                shutil.copytree(skill_source, skill_stage, symlinks=True)
                from .skill_install import write_runtime_hint
                write_runtime_hint(skill_stage, target)
                skill_paths = skill_target, skill_stage, skill_previous
            journal = {'startedAt': utc_now(), 'previous': str(previous), 'target': str(target), 'phase': 'preflight',
                       'contractChanged': contract_changed, 'before':before, 'environment':preserved}
            if skill_stage is not None:
                from .skill_install import claude_skills_home, SKILL
                claude_link = claude_skills_home() / SKILL
                journal.update(skillTarget=str(skill_target), skillStage=str(skill_stage), skillPrevious=str(skill_previous),
                               skillHadPrevious=skill_target.exists(), claudeLink=str(claude_link),
                               claudeLinkWasMissing=not claude_link.exists() and not _is_link(claude_link))
            write_journal(marker, journal)
            backed_up = None
            active = previous
            detached = False
            try:
                # A same-contract installation preserves live work.
                detach(state, previous, require_idle=False)
                detached = True
                preparation.close()
                with file_lock(state / 'control-daemon.lock'), file_lock(state / 'board-owner.lock'):
                    if contract_changed:
                        before = idle_snapshot(state)
                        before['runtimeSettings'] = runtime_settings
                        journal['before'] = before
                        backed_up = backup.create(state, runtime_identity={'identity': identity}, plugin_commit=runtime.read_ready(previous).get('sourceCommit'), contract_version=contract_version)
                        journal.update(phase='backed-up', backup=backed_up)
                        write_journal(marker, journal)
                    journal.update(phase='prepared', expected=before)
                    write_journal(marker, journal)
                active = target
                health = start(state, target)
                evidence = verify_started(state, target, health, before, contract_changed=contract_changed)
                _verify_skill_generation(skill_paths, target, health)
                journal.update(phase='skill-switching', expected=before)
                write_journal(marker, journal)
                _publish_skill(journal, skill_paths)
                write_journal(marker, journal)
                write_active_runtime(state, target, preserved)
                journal.update(phase='verified', verification=evidence)
                write_journal(marker, journal)
            except Exception as failure:
                reason = _failure_reason(failure)
                try:
                    _restore_skill(journal, skill_paths)
                    if active == previous and not detached:
                        surviving = probe(state, previous)
                        if surviving is not None:
                            write_active_runtime(state, previous, preserved)
                            journal.update(phase='not-switched', failure=reason)
                            write_journal(state / 'upgrade-last.json', journal)
                            clear_journal(marker)
                            error = failure.payload() if isinstance(failure, BoardError) and failure.code == 'UPGRADE_NOT_IDLE' else {'code':'UPGRADE_NOT_SWITCHED','message':'Upgrade could not detach; the previous service remains available'}
                            error.setdefault('details', {})['reason'] = reason
                            return {'upgraded':False, 'error':error}
                    endpoint_now = _read_json(state / 'control.json') if (state / 'control.json').exists() else None
                    if active == target and detached and endpoint_now and endpoint_now.get('runtimeIdentity') == 'runtime:' + active.name:
                        detach(state, active, require_idle=contract_changed)
                    if backed_up is not None:
                        with file_lock(state / 'control-daemon.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS), \
                             file_lock(state / 'board-owner.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS):
                            restore(state, Path(backed_up['path']))
                    # A race admitted work before the old daemon detached: preserve it
                    # and restart the previous runtime without restoring an older DB.
                    recovered = start(state, previous)
                    evidence = (verify_started(state, previous, recovered, before, contract_changed=contract_changed)
                                if backed_up is not None or not contract_changed else {'runtimeContentId':previous.name, 'workPreserved':True})
                    write_active_runtime(state, previous, preserved)
                    journal.update(phase='rolled-back', rollback=evidence, failure=reason)
                    write_journal(state / 'upgrade-last.json', journal)
                    clear_journal(marker)
                    if backed_up is None and isinstance(failure, BoardError) and failure.code == 'UPGRADE_NOT_IDLE':
                        error = failure.payload()
                    else:
                        restored = 'Installation failed; the previous runtime and backup were restored' if backed_up is not None else 'Installation failed; the previous runtime was restored'
                        error = {'code':'UPGRADE_ROLLED_BACK', 'message':restored}
                    error.setdefault('details', {})['reason'] = reason
                    return {'upgraded':False, 'error':error,
                            **({'backup':backed_up} if backed_up is not None else {}), 'rollback':evidence}
                except Exception as recovery:
                    journal.update(phase='recovery-required', failure=reason, recoveryFailure=_failure_reason(recovery))
                    write_journal(marker, journal)
                    write_journal(state / 'upgrade-last.json', journal)
                    retained = 'journal and backup retained' if backed_up is not None else 'journal retained'
                    raise BoardError('UPGRADE_RECOVERY_REQUIRED', f'Automatic rollback could not prove recovery; {retained}; rerun install to recover', journal=str(marker),
                                     reason=reason, recoveryReason=journal['recoveryFailure']) from recovery
            write_journal(state / 'upgrade-last.json', journal)
            skill_cleanup_error = _finish_skill(skill_paths)
            journal_error = None
            try:
                clear_journal(marker)
            except OSError as error:
                journal_error = str(error)
            pruning = retain_runtimes(state, target, previous)
            return {'upgraded':True, **({'backup':backed_up} if backed_up is not None else {}), 'verification':evidence, 'previousRuntime':previous.name,
                    'rollback':None, 'runtimePruning':pruning,
                    **({'skillCleanupError':skill_cleanup_error} if skill_cleanup_error is not None else {}),
                    **({'journalError':journal_error} if journal_error is not None else {})}


def recover(state: Path, root: Path) -> dict:
    """Resume an interrupted journal under both launcher fences; never cancel work."""
    marker = state / 'upgrade.json'
    journal = _read_json(marker)
    contract_changed = journal['contractChanged']
    skill_paths = _skill_paths(journal)  # Validate destructive recovery paths before touching a service.
    previous, target = Path(journal['previous']), Path(journal['target'])
    for path in (previous, target):
        if path.parent.resolve() != root or path.is_symlink() or not runtime.is_ready(path):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal runtime identity cannot be verified')
    target_health = probe(state, target)
    previous_health = probe(state, previous)
    if journal.get('phase') == 'verified' and target_health is not None:
        evidence = verify_started(state, target, target_health, journal['expected'],
                                  contract_changed=contract_changed)
        _verify_skill_generation(skill_paths, target, target_health)
        write_active_runtime(state, target, journal.get('environment'))
        journal.update(verification=evidence)
        write_journal(state / 'upgrade-last.json', journal)
        skill_cleanup_error = _finish_skill(skill_paths)
        journal_error = None
        try:
            clear_journal(marker)
        except OSError as error:
            journal_error = str(error)
        pruning = retain_runtimes(state, target, previous)
        return {'upgraded':True, 'recoveredJournal':True, 'verification':evidence, 'runtimePruning':pruning,
                **({'backup':journal['backup']} if 'backup' in journal else {}),
                **({'skillCleanupError':skill_cleanup_error} if skill_cleanup_error is not None else {}),
                **({'journalError':journal_error} if journal_error is not None else {})}
    # A changed-contract cutover records its backup before starting the target.
    # Without that record the board still holds the previous service's work.
    if journal.get('phase') == 'preflight' or (contract_changed and 'backup' not in journal):
        if target_health is not None:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Unexpected target owner before a recorded cutover')
        if previous_health is None:
            with file_lock(state / 'control-daemon.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS), \
                 file_lock(state / 'board-owner.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS):
                pass
            # No backup/cutover happened: preserve old workers and any work that
            # raced the old-version preflight rather than attempting to retire it.
            previous_health = start(state, previous)
        if not contract_changed:
            evidence = verify_started(state, previous, previous_health, journal['before'], contract_changed=False)
        else:
            evidence = {'runtimeContentId':previous.name,'workPreserved':True}
        write_active_runtime(state, previous, journal.get('environment'))
        _restore_skill(journal, skill_paths)
        clear_journal(marker)
        return {'upgraded':False, 'recoveredJournal':True, 'rollback':evidence}
    if contract_changed and (target_health is not None or previous_health is not None):
        ensure_idle(state)
    if target_health is not None:
        detach(state, target, require_idle=contract_changed)
    elif previous_health is not None:
        detach(state, previous, require_idle=contract_changed)
    with file_lock(state / 'control-daemon.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS), \
         file_lock(state / 'board-owner.lock', timeout=OWNER_LOCK_TIMEOUT_SECONDS):
        if contract_changed:
            saved = journal.get('backup')
            if saved is not None:
                current = state / 'backups/current'
                if Path(saved['path']) != current:
                    raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Journal backup path does not match this installation')
                restore(state, current)
            before = idle_snapshot(state)
        else:
            before = journal['before']
    health = start(state, previous)
    evidence = verify_started(state, previous, health, before, contract_changed=contract_changed)
    write_active_runtime(state, previous, journal.get('environment'))
    _restore_skill(journal, skill_paths)
    journal.update(phase='rolled-back', rollback=evidence)
    write_journal(state / 'upgrade-last.json', journal)
    clear_journal(marker)
    return {'upgraded':False, 'recoveredJournal':True,
            **({'backup':journal['backup']} if 'backup' in journal else {}), 'rollback':evidence}
