"""Owned, bounded native account operations; only private homes are admitted.

Native output, OAuth links and secrets remain in memory. Public facts are an
explicit whitelist. The service supplies the frozen account mutation fence.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .adapters.base import ProcessHandle
from .adapters.codex_protocol import Connection
from .adapters.windows_process import owned_popen
from .billing import codex_account
from .db import utc_now
from .errors import BoardError
from .harness_discovery import native_environment
from .private_dirs import ensure_private_dir, linked, linked_component


def key_value(value) -> str:
    try:
        size = len(value.encode()) if isinstance(value, str) else 0
    except UnicodeError:
        raise BoardError('ACCOUNT_KEY_INVALID', 'Enter one nonempty bounded API key') from None
    if (not isinstance(value, str) or not value.strip() or size > 16384
            or any(character in value for character in ('\0', '\r', '\n'))):
        raise BoardError('ACCOUNT_KEY_INVALID', 'Enter one nonempty bounded API key')
    return value


def harden_home(home: Path) -> None:
    """Inspect entries without reading credentials or following links."""
    ensure_private_dir(home)
    from .adapters.codex_home import _pinned_home
    def descend(directory):
        os.fchmod(directory, 0o700)
        for name in os.listdir(directory):
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                    or getattr(metadata, 'st_file_attributes', 0) & 0x400):
                raise BoardError('PRIVATE_PATH_UNSAFE', 'A Worker account contains a linked or special entry')
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    descend(child)
                finally:
                    os.close(child)
            else:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    actual = os.fstat(child)
                    if not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
                        raise BoardError('PRIVATE_PATH_UNSAFE', 'A Worker credential is not one ordinary file')
                    os.fchmod(child, 0o600)
                finally:
                    os.close(child)
    with _pinned_home(home) as directory:
        descend(directory)


def environment(home: Path, values: dict, command: list[str]) -> dict:
    ensure_private_dir(home)
    return native_environment({**values, 'CODEX_HOME': str(home)}, command=command)


def _end(child, handle) -> bool:
    try:
        if child.stdin and not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            handle.terminate(grace_seconds=0.5)
        return handle.shutdown_confirmed()
    finally:
        if child.stdout and not child.stdout.closed:
            child.stdout.close()


def _cleanup_native_helpers(home: Path) -> None:
    """Codex creates executable links below tmp/arg0; caller proved native stop."""
    from .private_dirs import remove_tree
    remove_tree(home / 'tmp' / 'arg0')


def write_codex_key(command: list[str], home: Path, values: dict, key: str, *, on_spawn) -> None:
    key = key_value(key)
    child = owned_popen([*command, '-c', 'cli_auth_credentials_store="file"', 'login', '--with-api-key'],
                        env=environment(home, values, command), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    handle = ProcessHandle(child, own_group=True, log_paths={})
    on_spawn(handle)
    code = None
    try:
        child.communicate(input=(key + '\n').encode(), timeout=15)
        code = child.returncode
    except Exception:
        pass
    finally:
        key = None
        stopped = _end(child, handle)
    if not stopped:
        raise BoardError('ACCOUNT_STOP_UNCONFIRMED', 'The native account process stop is unconfirmed')
    if code != 0:
        raise BoardError('ACCOUNT_LOGIN_FAILED', 'The native key operation failed')
    _cleanup_native_helpers(home)
    harden_home(home)


def _public_account(account) -> dict:
    kind = account.get('type') if isinstance(account, dict) else None
    kind = kind if kind in ('chatgpt', 'apiKey') else None
    return {'status': 'ready' if kind else 'logged-out', 'accountType': kind,
            'checkedAt': utc_now(), 'billingByProvider': {'openai': codex_account(account, utc_now())}}


class CodexAccountProcess:
    """The caller retains this owner until real process-group stop is confirmed."""

    def __init__(self, command: list[str], home: Path, values: dict, *, seconds=600, on_spawn=None):
        self.home = ensure_private_dir(home)
        self.lock = threading.RLock()
        self.id = 'login-' + secrets.token_hex(16)
        self.state = 'pending'
        self.reason = None
        self.native_id = None
        self.expires_at = time.time() + seconds
        self.child = owned_popen([*command, '-c', 'cli_auth_credentials_store="file"', 'app-server', '--listen', 'stdio://'],
                                env=environment(home, values, command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        self.handle = ProcessHandle(self.child, own_group=True, log_paths={})
        if on_spawn is not None:
            on_spawn(self)
        self.connection = Connection(self.child, time.monotonic() + 15, threading.Event())
        self.connection.on_request = lambda message: self.connection.send({'id': message['id'], 'error': {
            'code': -32601, 'message': 'Account operations grant no interactive access'}})
        self.connection.on_notification = self._notification

    def initialize(self):
        try:
            self.connection.call('initialize', {'clientInfo': {'name': 'hey_my_buddy_accounts', 'version': '0.26.0'}})
            self.connection.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.state = 'failed' if self.stop() else 'unconfirmed'
            raise BoardError('ACCOUNT_LOGIN_FAILED' if self.state == 'failed' else 'ACCOUNT_STOP_UNCONFIRMED',
                             'The native account connection could not be initialized') from None

    def _notification(self, message):
        if (message.get('method') != 'account/login/completed'
                or (message.get('params') or {}).get('loginId') != self.native_id):
            return
        params = message.get('params') or {}
        self.state = 'completed' if params.get('success') is True else 'failed'
        text = str(params.get('error') or '').lower()
        self.reason = None if self.state == 'completed' else (
            'ACCOUNT_LOGIN_TIMEOUT' if any(word in text for word in ('timeout', 'timed out', 'expired')) else 'ACCOUNT_LOGIN_FAILED')

    def view(self) -> dict:
        from datetime import datetime, timezone
        return {'loginId': self.id, 'state': self.state,
                'expiresAt': datetime.fromtimestamp(self.expires_at, timezone.utc).isoformat().replace('+00:00', 'Z'),
                'reasonCode': self.reason}

    def start_login(self) -> dict:
        with self.lock:
            response = self.connection.call('account/login/start', {'type': 'chatgpt'})
            native_id, url = response.get('loginId'), response.get('authUrl')
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (not isinstance(native_id, str) or not native_id or len(native_id) > 1024
                    or not parsed or parsed.scheme != 'https' or parsed.hostname != 'auth.openai.com'
                    or parsed.username or parsed.password or len(url) > 16384):
                raise BoardError('ACCOUNT_LOGIN_FAILED', 'The native login response was invalid')
            self.native_id = native_id
            self.connection.deadline = time.monotonic() + max(1, self.expires_at - time.time())
            return {**self.view(), 'authUrl': url}

    def wait(self) -> dict:
        try:
            while self.state == 'pending':
                with self.lock:
                    self.connection.pump()
                # Let a waiting cancel acquire the connection lock before pumping again.
                time.sleep(0.01)
            if self.state == 'completed':
                with self.lock:
                    facts = self.read()
                return facts
            return {'status': 'logged-out', 'accountType': None, 'checkedAt': utc_now()}
        except Exception:
            if self.state == 'pending':
                self.state, self.reason = 'failed', 'ACCOUNT_LOGIN_TIMEOUT' if time.time() >= self.expires_at else 'ACCOUNT_LOGIN_FAILED'
            return {'status': 'unknown', 'accountType': None, 'checkedAt': utc_now()}
        finally:
            if not self.stop():
                self.state, self.reason = 'unconfirmed', 'ACCOUNT_STOP_UNCONFIRMED'

    def read(self) -> dict:
        return _public_account(self.connection.call('account/read', {'refreshToken': False}).get('account'))

    def logout(self) -> dict:
        with self.lock:
            self.connection.call('account/logout', {})
            facts = self.read()
            if facts['status'] != 'logged-out':
                raise BoardError('ACCOUNT_LOGOUT_FAILED', 'The native private account remains logged in')
            return facts

    def cancel(self) -> dict:
        with self.lock:
            if self.state == 'pending' and self.native_id:
                try:
                    self.connection.deadline = time.monotonic() + 5
                    response = self.connection.call('account/login/cancel', {'loginId': self.native_id})
                    self.state = 'cancelled' if response.get('status') in ('canceled', 'cancelled') else 'failed'
                except Exception:
                    self.state = 'failed'
            if not self.stop():
                self.state, self.reason = 'unconfirmed', 'ACCOUNT_STOP_UNCONFIRMED'
            return self.view()

    def stop(self) -> bool:
        with self.lock:
            stopped = _end(self.child, self.handle)
            if stopped:
                _cleanup_native_helpers(self.home)
                harden_home(self.home)
            return stopped
