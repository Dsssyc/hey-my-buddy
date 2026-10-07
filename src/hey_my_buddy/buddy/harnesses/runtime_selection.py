"""Carry a service-selected native command through adapter/controller boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

from ...errors import BoardError

_records = ContextVar('harness_records', default=None)
_environment = ContextVar('harness_environment', default=None)
RECORD_FILE = 'BUDDY_HARNESS_RECORD_FILE'


def environment_for(environment=None):
    return _environment.get() or (os.environ if environment is None else environment)


@contextmanager
def bound(records, *, environment=None):
    token = _records.set({record['adapter']: record for record in records})
    env_token = _environment.set(environment)
    try:
        yield
    finally:
        _environment.reset(env_token)
        _records.reset(token)


def selected(adapter, environment=None):
    env = os.environ if environment is None else environment
    records = _records.get()
    if records is None and env.get(RECORD_FILE):
        try:
            records = json.loads(Path(env[RECORD_FILE]).read_text())
        except (OSError, ValueError):
            raise BoardError('HARNESS_RECORD_INVALID', 'The attempt has no readable selected harness record') from None
    return records.get(adapter) if records is not None else None


def command_for(adapter, environment=None):
    env = os.environ if environment is None else environment
    record = selected(adapter, env)
    if record is None:
        from .discovery import discover
        record = discover(adapter, environment=env)
    if record.get('status') != 'ready' or not record.get('command'):
        raise BoardError(record.get('reasonCode') or 'HARNESS_UNAVAILABLE',
                         record.get('remedy') or 'Run buddy adapters with refresh:true', adapter=adapter)
    return list(record['command'])


def native_environment(environment, *, adapter):
    from .discovery import native_environment as clean
    environment = environment_for(environment)
    return clean(environment, command=command_for(adapter, environment))


def controller_environment(directory, environment=None, *, read_only=False):
    """Only trusted Python controllers receive the private command selection."""
    source = (_environment.get() or os.environ) if environment is None else environment
    from .discovery import native_environment
    env = native_environment(source)
    allowed = ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONUTF8', 'PYTHONIOENCODING',
               'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE',
               'BUDDY_STATE_DIR', 'BUDDY_RUNTIME_ROOT', 'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY',
               'BUDDY_HARNESS_RECORD_FILE', 'BUDDY_CLAUDE_SETTINGS_POLICY')
    allowed = (*allowed, 'BUDDY_ACCOUNT_SELECTION')
    for key in allowed:
        if key in source:
            env[key] = source[key]
    if not read_only:
        for key in ('BUDDY_AGENT_CREDENTIAL', 'BUDDY_AGENT_CREDENTIAL_FILE', 'BUDDY_TASK_ID', 'BUDDY_ATTEMPT_ID'):
            if key in source:
                env[key] = source[key]
    if source.get('BUDDY_DEV_SOURCE') == '1':
        for key in ('BUDDY_DEV_SOURCE', 'BUDDY_CLAUDE_CLI', 'BUDDY_CODEX_CLI', 'BUDDY_ZCODE_CLI',
                    'BUDDY_NODE', 'BUDDY_CODEX_FIXTURE_CASE', 'BUDDY_CODEX_FIXTURE_STATE',
                    'BUDDY_CLAUDE_FIXTURE_CASE', 'BUDDY_CLAUDE_FIXTURE_STATE', 'BUDDY_CLAUDE_FIXTURE_AUTH_STATUS',
                    'BUDDY_ZCODE_TEST_CASE'):
            if key in source:
                env[key] = source[key]
    records = _records.get()
    if records is not None:
        from ..roles.turn_io import private_json
        path = Path(directory) / 'harness-selection.json'
        private_json(path, records)
        env[RECORD_FILE] = str(path)
    return env
