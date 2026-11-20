"""Authoritative identity for the console's attempt display record."""
from __future__ import annotations

from ...errors import BoardError
from ...protocol.reasons import reason
from .macro_tasks import group_for_task


def _marked(connection, group_id: str | None) -> bool:
    return group_id is not None and connection.execute(
        'SELECT 1 FROM macro_task_deletions WHERE target_id=?', (group_id,),
    ).fetchone() is not None


def identity(store, task_id: str, attempt_id: str) -> dict:
    with store.db.read() as connection:
        row = connection.execute('''
            SELECT a.execution_state, a.result_json,
                   COALESCE(a.model_harness, a.adapter) AS harness,
                   a.session_group_id
              FROM attempts a
             WHERE a.task_id=? AND a.attempt_id=?
        ''', (task_id, attempt_id)).fetchone()
        if row is None:
            raise BoardError('NOT_FOUND', 'Unknown task attempt')
        group_id = group_for_task(connection, task_id)
        if _marked(connection, group_id):
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted'))
    return {**dict(row), 'group_id': group_id}


def deleting(store, task_id: str, attempt_id: str) -> bool:
    with store.db.read() as connection:
        return (connection.execute('SELECT 1 FROM attempts WHERE task_id=? AND attempt_id=?',
                                   (task_id, attempt_id)).fetchone() is None
                or _marked(connection, group_for_task(connection, task_id)))
