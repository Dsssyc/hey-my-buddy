"""Read-only native login and billing status from harness health."""
from __future__ import annotations

import json

from ...protocol.harness_names import HARNESS_NAMES
from ...protocol.reasons import reason


def view(connection, harness):
    row = connection.execute('SELECT status,record_json,checked_at FROM harness_health WHERE harness=?', (harness,)).fetchone()
    record = json.loads(row['record_json']) if row else {}
    if row and row['status'] == 'login-required':
        status, account_reason = 'logged-out', reason('account.login-required')
    elif row and row['status'] == 'ready':
        status, account_reason = 'ready', None
    else:
        status, account_reason = 'unknown', reason('account.not-checked')
    billing = record.get('billingByProvider')
    kinds = {fact.get('kind') for fact in billing.values() if isinstance(fact, dict)} if isinstance(billing, dict) else set()
    account_type = next(iter(kinds)) if len(kinds) == 1 and kinds <= {'subscription', 'metered'} else None
    return {'harness': harness, 'status': status, 'accountType': account_type,
            'checkedAt': row['checked_at'] if row else None, 'reason': account_reason}


class Accounts:
    def __init__(self, board):
        self.board = board

    def all(self):
        with self.board.db.read() as connection:
            return [view(connection, harness) for harness in HARNESS_NAMES]
