"""Read a page from an attempt's append-only SQLite ACP record."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from urllib.parse import parse_qs

from .. import private_dirs
from ..blackboard.tasks import session_content as tasks
from ..buddy.harnesses.registry import session_omitted, session_projector
from ..errors import BoardError
from ..json_codec import decode_strict_json
from ..protocol.reasons import reason
from ..protocol.session_content import SessionNotification


def parse_query(query: str) -> dict:
    params = {}
    for name, values in parse_qs(query, keep_blank_values=True).items():
        if name not in ('before', 'after', 'limit') or len(values) != 1:
            raise BoardError('INVALID_ARGUMENT', 'Unknown or duplicate session parameter')
        value = values[0]
        if re.fullmatch(r'[0-9]+', value) is None:
            raise BoardError('INVALID_ARGUMENT', 'Session parameters must be decimal integers')
        try:
            params[name] = int(value)
        except ValueError:
            raise BoardError('INVALID_ARGUMENT', 'Session parameter is too large') from None
        if params[name] > 2**63 - 1:
            raise BoardError('INVALID_ARGUMENT', 'Session parameter is too large')
    if 'before' in params and 'after' in params:
        raise BoardError('INVALID_ARGUMENT', 'before and after are mutually exclusive')
    if not 1 <= params.get('limit', 100) <= 256:
        raise BoardError('INVALID_ARGUMENT', 'limit must be between 1 and 256')
    return params


def _page(connection: sqlite3.Connection, params: dict) -> dict:
    if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise ValueError('Invalid session format')
    columns = connection.execute('PRAGMA table_info(events)').fetchall()
    if [(column[1], column[2], column[3], column[5]) for column in columns] != [
        ('seq', 'INTEGER', 0, 1), ('notification', 'TEXT', 1, 0)
    ]:
        raise ValueError('Invalid session table')
    limit = params.get('limit', 100)
    if 'after' in params:
        position = params['after']
        rows = connection.execute('SELECT seq, notification FROM events WHERE seq>? ORDER BY seq LIMIT ?',
                                  (position, limit)).fetchall()
    else:
        position = params.get('before')
        if position is None:
            position = connection.execute('SELECT COALESCE(MAX(seq), 0) FROM events').fetchone()[0]
        rows = list(reversed(connection.execute(
            'SELECT seq, notification FROM events WHERE seq<=? ORDER BY seq DESC LIMIT ?',
            (position, limit)).fetchall()))
    events = [SessionNotification.model_validate(decode_strict_json(row[1])).model_dump(
        mode='json', by_alias=True, exclude_none=True) for row in rows]
    before = rows[0][0] - 1 if rows else position
    after = rows[-1][0] if rows else position
    earlier = connection.execute('SELECT 1 FROM events WHERE seq<=? LIMIT 1', (before,)).fetchone() is not None
    return {'events': events, 'before': before, 'after': after, 'hasEarlier': earlier}


def read(store, task_id: str, attempt_id: str, params: dict) -> dict:
    owner = tasks.identity(store, task_id, attempt_id)
    harness = owner['harness']
    if session_projector(harness) is None:
        raise BoardError('SESSION_NOT_INTEGRATED', 'Session projection is not integrated for this harness',
                         reason=reason('session.not-integrated'))
    path = private_dirs.session_record_path(store.directory, owner['group_id'], attempt_id)
    opened = False
    try:
        fd = private_dirs.open_regular_fd(path, os.O_RDONLY)
        opened = True
        os.close(fd)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute('BEGIN')
            page = _page(connection, params)
        # A reader connection can survive unlink. Check the fixed name as well.
        path.stat()
    except FileNotFoundError:
        if opened or tasks.deleting(store, owner['group_id']):
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted')) from None
        raise BoardError('SESSION_NOT_RECORDED', 'This attempt has no session record',
                         reason=reason('session.not-recorded')) from None
    except sqlite3.OperationalError:
        if not path.exists():
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted')) from None
        raise BoardError('SESSION_UNREADABLE', 'The session record could not be read',
                         reason=reason('session.unreadable')) from None
    except (ValueError, RecursionError, sqlite3.DatabaseError):
        raise BoardError('SESSION_INVALID_RECORD', 'The session record is invalid',
                         reason=reason('session.invalid-record')) from None
    except BoardError as error:
        if error.code == 'INVALID_ARGUMENT':
            raise
        raise BoardError('SESSION_UNREADABLE', 'The session record could not be read',
                         reason=reason('session.unreadable')) from None
    except OSError:
        raise BoardError('SESSION_UNREADABLE', 'The session record could not be read',
                         reason=reason('session.unreadable')) from None
    result = json.loads(owner['result_json']) if owner['result_json'] else {}
    return {'state': 'ended' if owner['execution_state'] == 'finished' else 'running',
            'stopped': result.get('sessionRecordFailure'),
            'omitted': list(session_omitted(harness)), **page}
