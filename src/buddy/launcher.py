"""Package bootstrap: upgrades use new code; ordinary commands follow active runtime.

This module must load before C-Two contracts. Selection never strips an agent's
credential, and an explicit development-source or runtime pin remains explicit.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

LAUNCH_KEYS = {'BUDDY_MAX_CONCURRENT': (1, 32), 'BUDDY_WAIT_CAPACITY': (1, 1024)}


def read_private(path: Path) -> dict | None:
    from buddy.errors import BoardError
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BoardError('LAUNCH_SETTINGS_INVALID', 'Runtime selection metadata must be owner-private')
        data = json.loads(os.read(fd, 65537))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        return data
    finally:
        os.close(fd)


def write_private(path: Path, value: dict) -> None:
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def launch_defaults(state: Path) -> dict[str, str]:
    from buddy.errors import BoardError
    value = read_private(state / 'launch-settings.json') or {}
    settings = value.get('environment', {})
    if not isinstance(settings, dict) or set(settings) - LAUNCH_KEYS.keys():
        raise BoardError('LAUNCH_SETTINGS_INVALID', 'Unknown persisted launch setting')
    result = {}
    for key, raw in settings.items():
        lower, upper = LAUNCH_KEYS[key]
        if not isinstance(raw, str) or not raw.isdecimal() or not lower <= int(raw) <= upper:
            raise BoardError('LAUNCH_SETTINGS_INVALID', 'Invalid persisted launch setting', field=key)
        result[key] = raw
    return result


def write_active_runtime(state: Path, target: Path, settings: dict[str, str] | None = None) -> None:
    write_private(state / 'active-runtime.json', {'format':1, 'runtimeDir':str(target), 'contentId':target.name})
    if settings is not None:
        write_private(state / 'launch-settings.json', {'environment':settings})


def selected_runtime(state: Path) -> Path | None:
    from buddy import runtime
    from buddy.errors import BoardError
    if os.environ.get('BUDDY_RUNTIME'):
        target = Path(os.environ['BUDDY_RUNTIME']).expanduser().resolve()
    elif os.environ.get('BUDDY_DEV_SOURCE') == '1':
        return None
    else:
        pointer = read_private(state / 'active-runtime.json')
        if pointer is not None:
            target = Path(pointer.get('runtimeDir', ''))
            if pointer.get('format') != 1 or pointer.get('contentId') != target.name:
                raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime metadata is inconsistent')
        else:
            # First upgrade from a release without a pointer: follow its recorded
            # stable runtime with that version's client; never invent a facade.
            endpoint = read_private(state / 'control.json') or {}
            identity = endpoint.get('runtimeIdentity', '')
            if not isinstance(identity, str) or not identity.startswith('runtime:'):
                return None
            target = runtime.runtime_root().resolve() / identity.removeprefix('runtime:')
        if (not target.is_absolute() or target.parent.resolve() != runtime.runtime_root().resolve()
                or not re.fullmatch(r'[0-9a-f]{32}', target.name) or target.is_symlink()):
            raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime is outside the configured root')
    if not runtime.is_ready(target):
        raise BoardError('ACTIVE_RUNTIME_UNAVAILABLE', 'Selected runtime is not READY; use the new package upgrade command to recover')
    return target


def runtime_environment(target: Path) -> dict[str, str]:
    # Preserve scoped Worker credentials. Dropping them would turn a Worker into
    # a service-token Host when the selected CLI builds its request.
    env = {k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT'}}
    env.update(BUDDY_RUNTIME=str(target), BUDDY_RUNTIME_IDENTITY='runtime:' + target.name,
               BUDDY_PYTHON=str(target / 'venv/bin/python'))
    return env


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        from buddy.home import default_state_dir
        state = Path(os.environ.get('BUDDY_STATE_DIR') or default_state_dir()).expanduser().resolve()
        if args and args[0] not in {'upgrade','install','paths','--help','-h'}:
            if (state / 'upgrade.json').exists():
                from buddy.errors import BoardError
                raise BoardError('UPGRADE_IN_PROGRESS', 'The launcher is fenced until upgrade verification or recovery finishes')
            target = selected_runtime(state)
            if target is not None:
                python = str(target / 'venv/bin/python')
                os.execve(python, [python, '-P', '-m', 'buddy.cli', *args], runtime_environment(target))
        from buddy.cli import main as cli_main
        return cli_main(args)
    except Exception as error:
        print(json.dumps({'error':{'code':getattr(error,'code','LAUNCH_FAILED'),'message':str(error)}}))
        return 1


if __name__ == '__main__':
    # Absolute script execution makes this package's upgrade bootstrap independent
    # of inherited PYTHONPATH or an older runtime pin.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
