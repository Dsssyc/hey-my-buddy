"""Read one fixed-length snapshot of the attempt's append-only ACP record."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs

from .. import private_dirs
from ..blackboard.tasks import session_content as tasks
from ..blackboard.tasks.workflow import WorkflowCoordinator
from ..buddy.harnesses.registry import session_omitted, session_projector
from ..errors import BoardError
from ..json_codec import decode_strict_json
from ..protocol.reasons import reason
from ..protocol.session_content import SESSION_FORMAT, SessionFormat, SessionNotification


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
    if 'before' in params and 'after' in params:
        raise BoardError('INVALID_ARGUMENT', 'before and after are mutually exclusive')
    if not 1 <= params.get('limit', 100) <= 256:
        raise BoardError('INVALID_ARGUMENT', 'limit must be between 1 and 256')
    return params


def _last_newline(stream, end: int, floor: int) -> int:
    """Find a newline backwards without reading the whole history."""
    while end > floor:
        start = max(floor, end - 65536)
        stream.seek(start)
        block = stream.read(end - start)
        index = block.rfind(b'\n')
        if index >= 0:
            return start + index
        end = start
    return floor - 1


def _empty_page(params: dict) -> dict:
    cursor = params.get('before', params.get('after', 0))
    return {'events': [], 'before': cursor, 'after': cursor, 'hasEarlier': False}


def _page(stream, params: dict) -> dict:
    size = os.fstat(stream.fileno()).st_size
    header = stream.readline(size)
    if not header.endswith(b'\n'):
        return _empty_page(params)
    if SessionFormat.model_validate(decode_strict_json(header)) != SESSION_FORMAT:
        raise ValueError('Invalid session format')
    start = len(header)
    cursor = params.get('before', params.get('after'))
    if cursor == 0:
        if 'before' in params:
            return _empty_page(params)
        cursor = start
    if cursor is not None:
        if cursor < start or cursor > size:
            raise BoardError('INVALID_ARGUMENT', 'Session offset is outside the event area')
        stream.seek(cursor - 1)
        if stream.read(1) != b'\n':
            raise BoardError('INVALID_ARGUMENT', 'Session offset is not a line boundary')
    end = _last_newline(stream, size, start) + 1
    limit = params.get('limit', 100)
    if 'after' in params:
        first = cursor
        stream.seek(first)
        lines = []
        for _ in range(limit):
            if stream.tell() >= end:
                break
            lines.append(stream.readline(end - stream.tell()))
        last = stream.tell()
    else:
        last = cursor if cursor is not None else end
        first = last
        for _ in range(limit):
            if first == start:
                break
            first = _last_newline(stream, first - 1, start) + 1
        stream.seek(first)
        lines = stream.read(last - first).split(b'\n')[:-1]
    events = [SessionNotification.model_validate(decode_strict_json(line)).model_dump(
        mode='json', by_alias=True, exclude_none=True) for line in lines]
    return {'events': events, 'before': first, 'after': last, 'hasEarlier': first > start}


def read(store, task_id: str, attempt_id: str, params: dict) -> dict:
    owner = tasks.identity(store, task_id, attempt_id)
    if owner['group_id'] is None:
        raise BoardError('SESSION_NOT_RECORDED', 'This attempt has no session record',
                         reason=reason('session.not-recorded'))
    harness = owner['harness']
    if session_projector(harness) is None:
        raise BoardError('SESSION_NOT_INTEGRATED', 'Session projection is not integrated for this harness',
                         reason=reason('session.not-integrated'))
    stored = json.loads(owner['result_json']) if owner['result_json'] else {}
    stopped = WorkflowCoordinator.result_payload(stored).get('sessionRecordFailure')
    response = {'state': 'ended' if owner['execution_state'] == 'finished' else 'running',
                'stopped': stopped, 'omitted': list(session_omitted(harness))}
    path = private_dirs.session_record_path(store.directory, owner['group_id'], attempt_id)
    opened = False
    try:
        fd = private_dirs.open_regular_fd(path, os.O_RDONLY)
        opened = True
        with os.fdopen(fd, 'rb') as stream:
            page = _page(stream, params)
            # An open POSIX descriptor survives unlink. Check the fixed name as
            # well so deletion during the read cannot return retained bytes.
            path.stat()
    except FileNotFoundError:
        if opened or tasks.deleting(store, owner['group_id']):
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted')) from None
        if stopped is not None:
            return {**response, **_empty_page(params)}
        raise BoardError('SESSION_NOT_RECORDED', 'This attempt has no session record',
                         reason=reason('session.not-recorded')) from None
    except (ValueError, RecursionError):
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
    return {**response, **page}
