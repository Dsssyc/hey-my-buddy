"""Carry a service-selected native command through adapter/controller boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

from ...errors import BoardError

_records = ContextVar('harness_records', default=None)
_environment = ContextVar('harness_environment', default=None)
RECORD_FILE = 'HMB_HARNESS_RECORD_FILE'


def environment_for(environment=None):
    return _environment.get() or (os.environ if environment is None else environment)


@contextmanager
def bound(records, *, environment=None):
    token = _records.set({record['harness']: record for record in records})
    env_token = _environment.set(environment)
    try:
        yield
    finally:
        _environment.reset(env_token)
        _records.reset(token)


def selected(harness, environment=None):
    env = os.environ if environment is None else environment
    records = _records.get()
    if records is None and env.get(RECORD_FILE):
        try:
            records = json.loads(Path(env[RECORD_FILE]).read_text())
        except (OSError, ValueError):
            raise BoardError('HARNESS_RECORD_INVALID', 'The attempt has no readable selected harness record') from None
    return records.get(harness) if records is not None else None


def command_for(harness, environment=None):
    env = os.environ if environment is None else environment
    record = selected(harness, env)
    if record is None:
        from .discovery import discover
        record = discover(harness, environment=env)
    if record.get('status') != 'ready' or not record.get('command'):
        raise BoardError('HARNESS_UNAVAILABLE', 'The selected harness is unavailable',
                         harness=harness, reason=record.get('reason'))
    return list(record['command'])


def native_environment(environment, *, harness):
    from .discovery import native_environment as clean
    environment = environment_for(environment)
    return clean(environment, command=command_for(harness, environment))


def controller_environment(directory, environment=None, *, read_only=False):
    """Only trusted Python controllers receive the private command selection."""
    source = (_environment.get() or os.environ) if environment is None else environment
    from .discovery import native_environment
    env = native_environment(source)
    allowed = ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONUTF8', 'PYTHONIOENCODING',
               'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE',
               'HMB_STATE_DIR', 'HMB_RUNTIME_ROOT', 'HMB_RUNTIME', 'HMB_RUNTIME_IDENTITY',
               'HMB_HARNESS_RECORD_FILE')
    for key in allowed:
        if key in source:
            env[key] = source[key]
    if not read_only:
        for key in ('HMB_AGENT_CREDENTIAL_FILE',):
            if key in source:
                env[key] = source[key]
    if source.get('HMB_DEV_SOURCE') == '1':
        for key in ('HMB_DEV_SOURCE', 'HMB_CLAUDE_CLI', 'HMB_CODEX_CLI', 'HMB_ZCODE_CLI',
                    'HMB_NODE', 'HMB_CODEX_FIXTURE_CASE', 'HMB_CODEX_FIXTURE_STATE',
                    'HMB_CLAUDE_FIXTURE_CASE', 'HMB_CLAUDE_FIXTURE_STATE',
                    'HMB_ZCODE_TEST_CASE'):
            if key in source:
                env[key] = source[key]
    records = _records.get()
    if records is not None:
        from ..roles.turn_io import private_json
        path = Path(directory) / 'harness-selection.json'
        private_json(path, records)
        env[RECORD_FILE] = str(path)
    return env
