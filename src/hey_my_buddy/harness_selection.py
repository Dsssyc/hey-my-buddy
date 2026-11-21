"""Bind the selected native command records and environment to an execution context."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

from .errors import BoardError
from .private_files import read_text

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
            records = json.loads(read_text(Path(env[RECORD_FILE])))
        except (OSError, ValueError, BoardError):
            raise BoardError('HARNESS_RECORD_INVALID', 'The attempt has no readable selected harness record') from None
    return records.get(harness) if records is not None else None
