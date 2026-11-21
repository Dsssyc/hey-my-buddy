"""Read one fixed-length snapshot of the attempt's append-only ACP record."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

from .. import private_dirs
from ..blackboard.tasks import session_content as tasks
from ..blackboard.tasks.workflow import WorkflowCoordinator
from ..buddy.harnesses.registry import session_coverage, session_projector
from ..errors import BoardError
from ..protocol.reasons import reason
from ..protocol.session_reader import SessionRecordDeleted, empty_raw_page, read_raw_page


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


def read(store, task_id: str, attempt_id: str, params: dict) -> dict:
    owner = tasks.identity(store, task_id, attempt_id)
    if owner['session_group_id'] is None:
        raise BoardError('SESSION_NOT_RECORDED', 'This attempt has no session record',
                         reason=reason('session.not-recorded'))
    harness = owner['harness']
    if session_projector(harness) is None:
        raise BoardError('SESSION_NOT_INTEGRATED', 'Session projection is not integrated for this harness',
                         reason=reason('session.not-integrated'))
    stored = json.loads(owner['result_json']) if owner['result_json'] else {}
    stopped = WorkflowCoordinator.result_payload(stored).get('sessionRecordFailure')
    response = {'state': 'ended' if owner['execution_state'] == 'finished' else 'running',
                'stopped': stopped, 'coverage': {kind: declaration.to_payload() for kind, declaration in session_coverage(harness).items()}}
    path = private_dirs.session_record_path(store.directory, owner['session_group_id'], attempt_id)
    try:
        page = read_raw_page(path, params)
    except SessionRecordDeleted:
        raise BoardError('SESSION_DELETED', 'The session record was deleted',
                         reason=reason('session.deleted')) from None
    except FileNotFoundError:
        if tasks.deleting(store, task_id, attempt_id):
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted')) from None
        if stopped is not None:
            return {**response, **empty_raw_page(params)}
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
    if tasks.deleting(store, task_id, attempt_id):
        raise BoardError('SESSION_DELETED', 'The session record was deleted',
                         reason=reason('session.deleted'))
    return {**response, **page}
