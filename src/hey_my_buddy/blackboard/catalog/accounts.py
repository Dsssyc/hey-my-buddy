"""Service-owned, secret-free account selection and native capability boundary.

No provider is installed by default. Approved native integrations can supply an
environment provider and use the guarded credential-change boundary; metadata
alone never enables a Worker account or implements login.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Callable
import uuid

from ..store.db import canonical_json
from ...errors import BoardError
from ... import private_dirs

ADAPTERS = ('dsh', 'zcode', 'codex', 'claude')
CAPABILITIES = ('workerAccount', 'oauth', 'apiKey', 'logout', 'remove')


def _adapter(adapter):
    if adapter not in ADAPTERS:
        raise BoardError('UNSUPPORTED_ADAPTER', 'Expected dsh, zcode, codex or claude')
    return adapter


def _source(source):
    if source not in ('native', 'worker'):
        raise BoardError('INVALID_ARGUMENT', 'source must be native or worker')
    return source


def identity(account):
    source = _source(account.get('source'))
    revision = account.get('credentialRevision')
    if type(revision) is not int or revision < 0:
        raise BoardError('INVALID_ARGUMENT', 'credentialRevision must be a nonnegative integer')
    return {'source': source, 'credentialRevision': revision}


def key(account):
    """An opaque local bucket, containing no real account identifier."""
    item = identity(account)
    return canonical_json([_adapter(account['adapter']), item['source'], item['credentialRevision']])


def _read(connection, name, default):
    row = connection.execute('SELECT value FROM meta WHERE key=?', (name,)).fetchone()
    return json.loads(row[0]) if row else default


def _write(connection, name, value):
    connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (name, canonical_json(value)))


def selection(connection, adapter):
    adapter = _adapter(adapter)
    setting = _read(connection, 'account-selection:' + adapter, {'source': 'native', 'revision': 0})
    credential = _read(connection, 'account-credential:' + canonical_json([adapter, setting['source']]), 0)
    return {'adapter': adapter, **setting, 'credentialRevision': credential}


def binding_key(connection, adapter):
    """The selected account binding with its selection epoch.

    The selection revision separates an A→B→A round trip from the original A:
    both hold the same source and credential revision, but the returned
    selection is a fresh epoch whose read facts and windows were cleared at each
    switch. Ordinary harness-health observation revisions never enter this key,
    so they cannot reopen a bounded re-read window on their own.
    """
    selected = selection(connection, adapter)
    return canonical_json([selected['source'], selected['credentialRevision'], selected['revision']])


def attempt_account(connection, attempt):
    adapter = attempt['model_adapter'] or attempt['adapter']
    if adapter not in ADAPTERS:
        return None
    # Historical attempts belong only to the initial native credential bucket.
    return _read(connection, 'attempt-account:' + attempt['attempt_id'],
                 {'adapter': adapter, 'source': 'native', 'revision': 0, 'credentialRevision': 0})


def freeze(connection, attempt_id, adapter):
    if adapter not in ADAPTERS:
        return None
    account = selection(connection, adapter)
    assert_credentials_current(connection, account)
    _write(connection, 'attempt-account:' + attempt_id, account)
    return account


def assert_credentials_current(connection, account):
    pending = connection.execute('SELECT 1 FROM meta WHERE key=?',
                                 ('account-mutation:' + canonical_json([account['adapter'], account['source']]),)).fetchone()
    if pending:
        raise BoardError('ACCOUNT_IN_USE', 'Credentials have an active or shutdown-uncertain mutation')
    revision = _read(connection, 'account-credential:' + canonical_json([account['adapter'], account['source']]), 0)
    if revision != account['credentialRevision']:
        raise BoardError('ACCOUNT_BINDING_CHANGED', 'The frozen credentials changed before native start')


@contextmanager
def native_operation(database, account, purpose):
    """Retain non-attempt credential users until the owner proves native stop.

    The service registers before spawn, outside the native call's lifetime. A
    restart or unknown shutdown leaves a durable reservation, never a timeout
    assumption that credentials are safe to replace.
    """
    operation_id = 'account-operation:' + uuid.uuid4().hex
    evidence = {'operationId': operation_id, 'shutdownConfirmed': False}
    with database.write() as connection:
        assert_credentials_current(connection, account)
        _write(connection, operation_id, {'adapter': account['adapter'], **identity(account),
               'purpose': purpose, 'status': 'active'})
    try:
        yield evidence
    finally:
        with database.write() as connection:
            if evidence['shutdownConfirmed'] is True:
                connection.execute('DELETE FROM meta WHERE key=?', (operation_id,))
            else:
                _write(connection, operation_id, {'adapter': account['adapter'], **identity(account),
                       'purpose': purpose, 'status': 'uncertain'})


@dataclass(frozen=True)
class WorkerAccountProvider:
    """Host-installed native evidence boundary; never populated from RPC input."""
    environment: Callable | None = None
    capabilities: dict = field(default_factory=dict)
    versions: tuple[str, ...] = ()


_providers: dict[str, WorkerAccountProvider] = {}


@contextmanager
def using_provider(adapter, provider):
    """Scoped trusted integration/fixture injection, with no persisted permission."""
    adapter = _adapter(adapter)
    if not isinstance(provider, WorkerAccountProvider):
        raise TypeError('Expected WorkerAccountProvider')
    previous = _providers.get(adapter)
    _providers[adapter] = provider
    try:
        yield
    finally:
        if previous is None:
            _providers.pop(adapter, None)
        else:
            _providers[adapter] = previous


def _provider(adapter):
    provider = _providers.get(adapter)
    if provider is None:
        from ...buddy.harnesses.account_integrations import providers
        provider = providers().get(adapter)
    return provider


def capabilities(adapter, *, health=None):
    provider = _provider(adapter)
    if provider and provider.versions:
        if health is None:
            from ...buddy.harnesses.runtime_selection import selected
            health = selected(adapter)
        if not health or health.get('version') not in provider.versions:
            provider = None
    return {name: bool(provider and provider.capabilities.get(name) is True
                       and provider.environment is not None) for name in CAPABILITIES}


def execution_environment(state, account, environment, *, purpose='execution'):
    """Resolve the frozen source without reading credentials or native fallback."""
    adapter = _adapter(account['adapter'])
    item = identity(account)
    if item['source'] == 'native':
        result = dict(environment)
        result.pop('BUDDY_ACCOUNT_SELECTION', None)
        return result
    provider = _provider(adapter)
    if not capabilities(adapter)['workerAccount']:
        raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'Worker account isolation has no approved native integration')
    secure_account_root(Path(state), adapter)
    return dict(provider.environment(Path(state), dict(account), dict(environment), purpose))


def secure_account_root(state, adapter):
    """Create/secure ordinary entries only; never inspect credential contents."""
    root = private_dirs.account_root(state, _adapter(adapter))
    # All account-tree ancestors owned by this service are private as well.
    for directory in (root.parents[2], root.parents[1], root.parent, root):
        private_dirs.ensure_private_dir(directory)

    def secure(directory):
        private_dirs.ensure_private_dir(directory)
        for child in directory.iterdir():
            if private_dirs.linked(child):
                raise BoardError('PRIVATE_PATH_UNSAFE', 'Account trees cannot contain links or reparse points')
            if child.is_dir():
                secure(child)
            else:
                fd = private_dirs.open_regular_fd(child, os.O_RDONLY)
                try:
                    if os.fstat(fd).st_nlink != 1:
                        raise BoardError('PRIVATE_PATH_UNSAFE', 'Account files cannot be shared by hard links')
                    if os.name != 'nt':
                        os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
    secure(root)
    return root


def view(connection, adapter, *, account=None):
    selected = account or selection(connection, adapter)
    item = identity(selected)
    row = connection.execute('SELECT status,record_json,checked_at FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    record = json.loads(row['record_json']) if row else {}
    caps = capabilities(adapter, health=record)
    if item['source'] == 'native':
        caps = {name: caps[name] if name == 'workerAccount' else False for name in CAPABILITIES}
    observed_account = identity(record['account']) if isinstance(record.get('account'), dict) else {'source': 'native', 'credentialRevision': 0}
    matches = observed_account == item
    checked = row['checked_at'] if row and matches else None
    pending = _read(connection, 'account-mutation:' + canonical_json([adapter, selected['source']]), None)
    if item['source'] == 'worker' and not caps['workerAccount']:
        status, reason, guidance = 'unsupported', 'ACCOUNT_CAPABILITY_UNVERIFIED', 'Worker account isolation requires approved native evidence and an installed integration.'
    elif pending:
        status, reason, guidance = 'unknown', 'ACCOUNT_CREDENTIAL_CHANGE_PENDING', 'Retain the account credentials until the native owner confirms completion and shutdown.'
        checked = None
    elif row and matches and row['status'] == 'login-required':
        status, reason, guidance = 'logged-out', 'ACCOUNT_LOGIN_REQUIRED', 'Sign in using the selected harness native account flow.'
    elif row and matches and row['status'] == 'ready':
        status, reason, guidance = 'ready', None, None
    else:
        status, reason, guidance = 'unknown', 'ACCOUNT_NOT_CHECKED', 'Refresh the selected harness to obtain a native observation.'
    billing = record.get('billingByProvider') if matches else None
    kinds = {fact.get('kind') for fact in billing.values() if isinstance(fact, dict)} if isinstance(billing, dict) else set()
    account_type = next(iter(kinds)) if len(kinds) == 1 and kinds <= {'subscription', 'metered'} else None
    result = {**selected, 'status': status, 'accountType': account_type, 'checkedAt': checked,
              'capabilities': caps, 'reasonCode': reason, 'guidance': guidance}
    if pending and pending.get('loginId') and pending.get('expiresAt'):
        result['pendingLogin'] = {'loginId': pending['loginId'], 'expiresAt': pending['expiresAt'],
            'state': 'unconfirmed' if pending['status'] == 'uncertain' else 'pending', 'kind': pending.get('kind')}
    return result


def _invalidate(connection, adapter, account):
    row = connection.execute('SELECT record_json FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    record = json.loads(row[0]) if row else {}
    for name in ('billingByProvider', 'quotaCheckedAt', 'account'):
        record.pop(name, None)
    record['account'] = identity(account)
    connection.execute("UPDATE harness_health SET status='unknown',record_json=?,checked_at=NULL,expires_at=NULL,scan_after=NULL,revision=revision+1 WHERE adapter=?",
                       (canonical_json(record), adapter))
    connection.execute("UPDATE catalog_current SET discovery_id=NULL,status='unknown',reason='ACCOUNT_BINDING_CHANGED' WHERE adapter=?", (adapter,))
    connection.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='ACCOUNT_BINDING_CHANGED' WHERE adapter=?",
                       (adapter,))
    # ADR-027: disappearance windows and confirmed-read facts belong to the
    # account whose readings produced them; a new binding never inherits them.
    from .catalog import clear_confirmed_read
    clear_confirmed_read(connection, adapter)


class Accounts:
    def __init__(self, board):
        self.board = board

    def all(self):
        with self.board.db.read() as connection:
            return [view(connection, adapter) for adapter in ADAPTERS]

    def native_operation_stopped(self, operation_id, *, shutdown_confirmed):
        """Trusted owner's later actual-stop evidence; no public mutation route."""
        if shutdown_confirmed is not True:
            raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'Actual native shutdown evidence is required')
        if not isinstance(operation_id, str) or not operation_id.startswith('account-operation:'):
            raise BoardError('INVALID_ARGUMENT', 'Expected the owned native account operation identity')
        with self.board.db.write() as connection:
            row = connection.execute('SELECT value FROM meta WHERE key=?', (operation_id,)).fetchone()
            if row is None:
                raise BoardError('NOT_FOUND', 'The native account operation is not retained')
            if json.loads(row[0])['status'] != 'uncertain':
                raise BoardError('ACCOUNT_IN_USE', 'The native account operation has not yielded ownership')
            connection.execute('DELETE FROM meta WHERE key=?', (operation_id,))

    def set(self, adapter, source, expected_revision):
        adapter, source = _adapter(adapter), _source(source)
        if type(expected_revision) is not int or expected_revision < 0:
            raise BoardError('INVALID_ARGUMENT', 'expectedRevision must be a nonnegative integer')
        with self.board.db.write() as connection:
            old = selection(connection, adapter)
            if old['revision'] != expected_revision:
                raise BoardError('REVISION_CONFLICT', 'Account selection changed; reread before saving')
            if source == 'worker':
                secure_account_root(self.board.directory, adapter)
            if old['source'] != source:
                _write(connection, 'account-selection:' + adapter, {'source': source, 'revision': old['revision'] + 1})
                current = selection(connection, adapter)
                _invalidate(connection, adapter, current)
                self.board._append_event(connection, 'account.source_changed', payload=current)
            result = view(connection, adapter)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return result

    @contextmanager
    def credential_change(self, adapter, source, *, expected_credential_revision, expected_selection_revision=None):
        """Reserve credentials atomically, without holding SQLite during native I/O.

        Native-source use records an external credential change; it grants no
        authority to mutate the user's native login. Worker mutations require a
        separately installed native integration. No secret travels in this API.
        """
        adapter, source = _adapter(adapter), _source(source)
        if type(expected_credential_revision) is not int or expected_credential_revision < 0:
            raise BoardError('INVALID_ARGUMENT', 'expected credential revision must be nonnegative')
        if source == 'worker' and not capabilities(adapter)['workerAccount']:
            raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'Worker credential changes require an approved native integration')
        mutation_key = 'account-mutation:' + canonical_json([adapter, source])
        operation_id = uuid.uuid4().hex
        with self.board.db.write() as connection:
            if expected_selection_revision is not None:
                selected = selection(connection, adapter)
                if selected['source'] != source or selected['revision'] != expected_selection_revision:
                    raise BoardError('REVISION_CONFLICT', 'Account selection changed before credential admission')
            name = 'account-credential:' + canonical_json([adapter, source])
            revision = _read(connection, name, 0)
            if revision != expected_credential_revision:
                raise BoardError('REVISION_CONFLICT', 'The selected credentials changed; reread before saving')
            if connection.execute('SELECT 1 FROM meta WHERE key=?', (mutation_key,)).fetchone():
                raise BoardError('ACCOUNT_IN_USE', 'An account credential mutation is still active or uncertain')
            for attempt in connection.execute("SELECT * FROM attempts WHERE execution_state IN ('starting','executing','finalizing','uncertain') OR shutdown_confirmed=0 AND result_json IS NOT NULL"):
                account = attempt_account(connection, attempt)
                if account and account['adapter'] == adapter and account['source'] == source:
                    raise BoardError('ACCOUNT_IN_USE', 'Credentials are retained by an active or shutdown-uncertain attempt')
            for row in connection.execute("SELECT value FROM meta WHERE key LIKE 'account-operation:%'"):
                operation = json.loads(row[0])
                if operation['adapter'] == adapter and operation['source'] == source:
                    raise BoardError('ACCOUNT_IN_USE', 'Credentials are retained by an active or shutdown-uncertain native operation')
            if source == 'worker':
                secure_account_root(self.board.directory, adapter)
            _write(connection, mutation_key, {'operationId': operation_id, 'status': 'active', 'credentialRevision': revision})
        try:
            yield {'operationId': operation_id}
            if source == 'worker':
                secure_account_root(self.board.directory, adapter)
        except BaseException:
            with self.board.db.write() as connection:
                pending = _read(connection, mutation_key, {})
                _write(connection, mutation_key, {**pending, 'operationId': operation_id, 'status': 'uncertain', 'credentialRevision': revision})
            raise
        else:
            self.credential_change_stopped(adapter, source, operation_id=operation_id, shutdown_confirmed=True)

    def credential_change_stopped(self, adapter, source, *, operation_id, shutdown_confirmed):
        """Trusted native owner completion/recovery after actual stop evidence."""
        adapter, source = _adapter(adapter), _source(source)
        if shutdown_confirmed is not True:
            raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'Actual native shutdown evidence is required')
        mutation_key = 'account-mutation:' + canonical_json([adapter, source])
        with self.board.db.write() as connection:
            pending = _read(connection, mutation_key, None)
            if pending is None or pending['operationId'] != operation_id:
                raise BoardError('CONFLICT', 'The native owner does not match the retained credential mutation')
            revision = pending['credentialRevision']
            name = 'account-credential:' + canonical_json([adapter, source])
            _write(connection, name, revision + 1)
            connection.execute('DELETE FROM meta WHERE key=?', (mutation_key,))
            current = selection(connection, adapter)
            if current['source'] == source:
                _invalidate(connection, adapter, current)
            self.board._append_event(connection, 'account.credentials_changed', payload={'adapter': adapter, 'source': source, 'credentialRevision': revision + 1})
            head = self.board._head_of(connection)
        self.board._notify(head)
