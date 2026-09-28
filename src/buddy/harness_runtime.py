"""Carry a service-selected native command through adapter/controller boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

from .errors import BoardError

_records = ContextVar('harness_records', default=None)
RECORD_FILE = 'BUDDY_HARNESS_RECORD_FILE'


@contextmanager
def bound(records):
    token = _records.set({record['adapter']: record for record in records})
    try:
        yield
    finally:
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
        from .harness_discovery import discover
        record = discover(adapter, environment=env)
    if record.get('status') != 'ready' or not record.get('command'):
        raise BoardError(record.get('reasonCode') or 'HARNESS_UNAVAILABLE',
                         record.get('remedy') or 'Run buddy adapters with refresh:true', adapter=adapter)
    return list(record['command'])


def native_environment(environment, *, adapter):
    from .harness_discovery import native_environment as clean
    return clean(environment, command=command_for(adapter, environment))


def controller_environment(directory):
    """Only trusted Python controllers receive the private command selection."""
    env = dict(os.environ)
    records = _records.get()
    if records is not None:
        from .adapters.turn_io import private_json
        path = Path(directory) / 'harness-selection.json'
        private_json(path, records)
        env[RECORD_FILE] = str(path)
    return env
