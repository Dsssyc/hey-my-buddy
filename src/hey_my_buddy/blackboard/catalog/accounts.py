"""Read-only native login and billing status from harness health."""
from __future__ import annotations

import json

from ...protocol.harness_names import HARNESS_NAMES


def view(connection, harness):
    row = connection.execute('SELECT status,record_json,checked_at FROM harness_health WHERE harness=?', (harness,)).fetchone()
    record = json.loads(row['record_json']) if row else {}
    if row and row['status'] == 'login-required':
        status, reason, guidance = 'logged-out', 'ACCOUNT_LOGIN_REQUIRED', 'Sign in using the harness native account flow.'
    elif row and row['status'] == 'ready':
        status, reason, guidance = 'ready', None, None
    else:
        status, reason, guidance = 'unknown', 'ACCOUNT_NOT_CHECKED', 'Refresh the harness to obtain a native observation.'
    billing = record.get('billingByProvider')
    kinds = {fact.get('kind') for fact in billing.values() if isinstance(fact, dict)} if isinstance(billing, dict) else set()
    account_type = next(iter(kinds)) if len(kinds) == 1 and kinds <= {'subscription', 'metered'} else None
    return {'harness': harness, 'status': status, 'accountType': account_type,
            'checkedAt': row['checked_at'] if row else None, 'reasonCode': reason, 'guidance': guidance}


class Accounts:
    def __init__(self, board):
        self.board = board

    def all(self):
        with self.board.db.read() as connection:
            return [view(connection, harness) for harness in HARNESS_NAMES]
