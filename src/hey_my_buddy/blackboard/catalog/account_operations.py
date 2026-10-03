"""Authenticated private-account operations, with durable nonsecret reservations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from . import accounts
from ...buddy.harnesses.account_native import CodexAccountProcess, key_value, write_codex_key
from ...buddy.harnesses.base import ProcessHandle
from ..store.db import canonical_json, utc_now
from ...errors import BoardError
from ...private_dirs import account_root, remove_tree


@dataclass
class Operation:
    adapter: str
    account: dict
    guard: object
    mutation: dict
    expires_at: str
    owner: object = None
    state: str = 'pending'
    settled: bool = False
    inflight: bool = True
    cancel_requested: bool = False
    native_identity: tuple = ()

    @property
    def id(self):
        return 'login-' + self.mutation['operationId']

    def view(self):
        return {'loginId': self.id, 'state': self.state, 'expiresAt': self.expires_at}


class AccountOperations:
    """Daemon-owned account children; model Workers cannot invoke this owner."""

    def __init__(self, service):
        self.service = service
        self.board = service.store
        self.settings = service.account_settings
        self.lock = threading.RLock()
        self.operations = {}
        self.closed = False

    def _reserve(self, params, capability):
        adapter = params.get('adapter')
        revision = params.get('expectedRevision')
        if type(revision) is not int or revision < 0:
            raise BoardError('INVALID_ARGUMENT', 'expectedRevision must be a nonnegative integer')
        with self.board.db.read() as db:
            account = accounts.selection(db, adapter)
            health = self.service.harnesses.get(adapter)
        if account['source'] != 'worker':
            raise BoardError('ACCOUNT_NATIVE_READ_ONLY', 'Manage shared native login in the native tool')
        if account['revision'] != revision:
            raise BoardError('REVISION_CONFLICT', 'Account selection changed; reread before saving')
        if not accounts.capabilities(adapter, health=health).get(capability):
            raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'This native account operation has no approved evidence')
        if not health.get('command'):
            raise BoardError('HARNESS_UNAVAILABLE', 'The selected native executable is unavailable')
        guard = self.settings.credential_change(adapter, 'worker', expected_credential_revision=account['credentialRevision'],
            expected_selection_revision=revision)
        from ...buddy.harnesses.runtime_selection import bound
        with bound([health]):
            mutation = guard.__enter__()
        expiry = datetime.fromtimestamp(time.time() + 600, timezone.utc).isoformat().replace('+00:00', 'Z')
        operation = Operation(adapter, account, guard, mutation, expiry)
        operation.native_identity = (tuple(health.get('command') or []), health.get('version'))
        with self.lock:
            if self.closed:
                guard.__exit__(None, None, None)
                raise BoardError('SERVICE_UNAVAILABLE', 'The account owner is closing')
            self.operations[operation.id] = operation
        with self.board.db.write() as db:
            key = 'account-mutation:' + canonical_json([adapter, 'worker'])
            pending = accounts._read(db, key, None)
            if pending and pending['operationId'] == mutation['operationId']:
                accounts._write(db, key, {**pending, 'loginId': operation.id, 'expiresAt': expiry, 'kind': capability})
        return operation, list(health['command'])

    def _publish(self, operation, facts):
        with self.board.db.write() as db:
            current = accounts.selection(db, operation.adapter)
            if (current['source'] != 'worker'
                    or current['credentialRevision'] != operation.account['credentialRevision'] + 1):
                return
            row = db.execute('SELECT record_json FROM harness_health WHERE adapter=?', (operation.adapter,)).fetchone()
            record = json.loads(row[0]) if row else {}
            if (tuple(record.get('command') or []), record.get('version')) != operation.native_identity:
                return
            record['account'] = accounts.identity(current)
            record['billingByProvider'] = facts.get('billingByProvider') or {}
            status = 'ready' if facts.get('status') == 'ready' else 'login-required' if facts.get('status') == 'logged-out' else 'unknown'
            db.execute('UPDATE harness_health SET status=?,record_json=?,checked_at=?,revision=revision+1 WHERE adapter=?',
                (status, canonical_json(record), facts.get('checkedAt') or utc_now(), operation.adapter))
            self.board._append_event(db, 'account.operation_completed', payload={'adapter': operation.adapter,
                'account': accounts.identity(current), 'status': facts.get('status') or 'unknown', 'loginId': operation.id})
            head = self.board._head_of(db)
        self.board._notify(head)

    def _settle(self, operation, facts=None):
        with self.lock:
            if operation.settled or operation.inflight:
                return
            owner = operation.owner
            handle = owner.handle if isinstance(owner, CodexAccountProcess) else owner
            stopped = owner is None or isinstance(handle, ProcessHandle) and handle.shutdown_confirmed()
            if not stopped:
                if operation.state != 'unconfirmed':
                    operation.state = 'unconfirmed'
                    error = BoardError('ACCOUNT_STOP_UNCONFIRMED', 'The native account process stop is unconfirmed')
                    operation.guard.__exit__(BoardError, error, None)
                return
            if operation.state == 'unconfirmed':
                self.settings.credential_change_stopped(operation.adapter, 'worker',
                    operation_id=operation.mutation['operationId'], shutdown_confirmed=True)
                operation.state = 'failed'
            else:
                operation.guard.__exit__(None, None, None)
            operation.settled = True
            operation.owner = None
            finished = [key for key, item in self.operations.items() if item.settled and key != operation.id]
            for key in finished[:-31]:
                self.operations.pop(key, None)
        self._publish(operation, facts or {'status': 'unknown'})

    def _account(self, adapter):
        with self.board.db.read() as db:
            return accounts.view(db, adapter)

    def login(self, params):
        mode = params.get('mode')
        if mode not in ('oauth', 'api-key'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be oauth or api-key')
        secret = params.pop('apiKey', None)
        if mode == 'api-key':
            secret = key_value(secret)
        elif secret is not None:
            raise BoardError('INVALID_ARGUMENT', 'OAuth login accepts no key')
        operation, command = self._reserve(params, 'oauth' if mode == 'oauth' else 'apiKey')
        home = account_root(self.board.directory, operation.adapter)
        try:
            if mode == 'oauth':
                owner = CodexAccountProcess(command, home, dict(self.service.harnesses.environment()),
                    on_spawn=lambda owner: setattr(operation, 'owner', owner))
                owner.initialize()
                ticket = owner.start_login()
                operation.inflight = False
                thread = threading.Thread(target=self._wait_login, args=(operation,), daemon=True, name='worker-account-login')
                thread.start()
                return {'account': self._account(operation.adapter), 'login': {**ticket, 'loginId': operation.id}}
            if operation.adapter == 'codex':
                write_codex_key(command, home, dict(self.service.harnesses.environment()), secret,
                    on_spawn=lambda handle: setattr(operation, 'owner', handle))
                owner = CodexAccountProcess(command, home, dict(self.service.harnesses.environment()),
                    seconds=15, on_spawn=lambda owner: setattr(operation, 'owner', owner))
                owner.initialize()
                try:
                    facts = owner.read()
                finally:
                    owner.stop()
            elif operation.adapter == 'claude':
                from .account_keystore import identity, open_store
                open_store().put(*identity(self.board.directory, 'claude'), secret)
                from ...protocol.billing import fact
                facts = {'status': 'ready', 'accountType': 'apiKey', 'checkedAt': utc_now(),
                    'billingByProvider': {'anthropic': fact('metered', 'claude/worker-system-key', utc_now())}}
            else:
                raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'This key path is unverified')
            operation.state = 'completed'
            operation.inflight = False
            self._settle(operation, facts)
            if not operation.settled:
                raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'The native account process stop is unconfirmed', loginId=operation.id)
            return {'account': self._account(operation.adapter)}
        except Exception as error:
            self._stop_owner(operation)
            operation.state = 'failed'
            operation.inflight = False
            self._settle(operation)
            code = error.code if isinstance(error, BoardError) else 'ACCOUNT_LOGIN_FAILED'
            if not operation.settled:
                code = 'ACCOUNT_STOP_UNCONFIRMED'
            raise BoardError(code, 'The private account operation did not complete', loginId=operation.id) from None
        finally:
            secret = None

    def _wait_login(self, operation):
        owner = operation.owner
        if owner is None:
            return
        facts = owner.wait()
        operation.state = owner.state
        self._settle(operation, facts)

    def _stop_owner(self, operation):
        owner = operation.owner
        if isinstance(owner, CodexAccountProcess):
            return owner.stop()
        if isinstance(owner, ProcessHandle):
            owner.terminate(grace_seconds=0.5)
            return owner.shutdown_confirmed()
        return True

    def status(self, params):
        with self.lock:
            operation = self.operations.get(params.get('loginId'))
        if operation is None or operation.adapter != params.get('adapter'):
            raise BoardError('ACCOUNT_OPERATION_UNAVAILABLE', 'This login owner is unavailable; retain unconfirmed credentials')
        if operation.state == 'unconfirmed':
            self._settle(operation)
        return {'login': operation.view(), 'account': self._account(operation.adapter)}

    def cancel(self, params):
        with self.lock:
            operation = self.operations.get(params.get('loginId'))
        if operation is None or operation.adapter != params.get('adapter'):
            raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'This native owner is unavailable; no stop is inferred')
        if not operation.settled:
            operation.cancel_requested = True
            if isinstance(operation.owner, CodexAccountProcess):
                operation.state = operation.owner.cancel()['state']
            else:
                self._stop_owner(operation)
                if not operation.inflight:
                    operation.state = 'cancelled'
            self._settle(operation)
        return {'login': operation.view(), 'account': self._account(operation.adapter)}

    def logout(self, params, *, remove=False):
        operation, command = self._reserve(params, 'remove' if remove else 'logout')
        home = account_root(self.board.directory, operation.adapter)
        try:
            if operation.adapter == 'codex':
                owner = CodexAccountProcess(command, home, dict(self.service.harnesses.environment()), seconds=15,
                    on_spawn=lambda owner: setattr(operation, 'owner', owner))
                owner.initialize()
                try:
                    facts = owner.logout()
                finally:
                    owner.stop()
            elif operation.adapter == 'claude':
                from .account_keystore import identity, open_store
                open_store().delete(*identity(self.board.directory, 'claude'))
                facts = {'status': 'logged-out', 'checkedAt': utc_now()}
            else:
                raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'This logout path is unverified')
            if not self._stop_owner(operation):
                raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'The native account stop is unconfirmed')
            if remove:
                remove_tree(home)
            operation.state = 'completed'
            operation.inflight = False
            self._settle(operation, facts)
            return {'account': self._account(operation.adapter)}
        except Exception as error:
            self._stop_owner(operation)
            operation.state = 'failed'
            operation.inflight = False
            self._settle(operation)
            code = error.code if isinstance(error, BoardError) else 'ACCOUNT_LOGOUT_FAILED'
            raise BoardError(code, 'The private account operation did not complete', loginId=operation.id) from None

    def close(self):
        with self.lock:
            self.closed = True
            pending = [operation for operation in self.operations.values() if not operation.settled]
        for operation in pending:
            self.cancel({'adapter': operation.adapter, 'loginId': operation.id})
        deadline = time.monotonic() + 20
        while any(operation.inflight for operation in pending) and time.monotonic() < deadline:
            time.sleep(0.05)
