"""Authoritative identity for the console's attempt display record."""
from __future__ import annotations

from ...errors import BoardError
from ...protocol.reasons import reason
from .macro_tasks import MEMBERS_SQL


def identity(store, task_id: str, attempt_id: str) -> dict:
    with store.db.read() as connection:
        row = connection.execute(f'''
            SELECT a.execution_state, a.result_json,
                   COALESCE(a.model_harness, a.adapter) AS harness,
                   member.group_id
              FROM attempts a JOIN tasks t ON t.task_id=a.task_id
              JOIN ({MEMBERS_SQL}) member ON member.task_id=t.task_id
             WHERE t.task_id=? AND a.attempt_id=?
        ''', (task_id, attempt_id)).fetchone()
        if row is None:
            raise BoardError('NOT_FOUND', 'Unknown task attempt')
        result = dict(row)
        if connection.execute('SELECT 1 FROM macro_task_deletions WHERE target_id=?',
                              (result['group_id'],)).fetchone() is not None:
            raise BoardError('SESSION_DELETED', 'The session record was deleted',
                             reason=reason('session.deleted'))
    return result


def deleting(store, group_id: str) -> bool:
    with store.db.read() as connection:
        return connection.execute('SELECT 1 FROM macro_task_deletions WHERE target_id=?',
                                  (group_id,)).fetchone() is not None
