"""Bounded, model-free Codex account billing readback."""
from __future__ import annotations

import subprocess
import threading
import time

from ..base import ProcessHandle
from .protocol import Connection
from ....protocol.owned_process import owned_popen
from ....protocol.billing import codex_account, fact
from ....blackboard.store.db import utc_now
from ..discovery import native_environment
from ....errors import BoardError


def read(command: list[str], environment: dict, *, entry: str | None = None) -> dict | None:
    """Read native billing and provider metadata without requesting a model turn."""
    if not command:
        return None
    child = owned_popen([*command, "app-server", "--listen", "stdio://"],
                        env=native_environment(environment, command=command, entry=entry),
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        start_new_session=True, close_fds=True)
    handle = ProcessHandle(child, own_group=True, log_paths={})
    try:
        connection = Connection(child, time.monotonic() + 8, threading.Event())
        connection.on_request = lambda message: connection.send({"id": message["id"], "error": {
            "code": -32601, "message": "Account readback does not grant interactive access"}})
        connection.call("initialize", {"clientInfo": {"name": "hey_my_buddy", "title": "Hey My Buddy", "version": "0.9.0"}})
        connection.send({"method": "initialized", "params": {}})
        account = connection.call("account/read", {"refreshToken": False}).get("account")
        configured = connection.call("config/read", {"includeLayers": False}).get("config") or {}
        from .config import native_provider
        provider = native_provider(configured)
        observed_at = utc_now()
        billing = (codex_account(account, observed_at) if provider == "openai"
                   else fact("unknown", "codex/account-read", observed_at))
        billing["provider"] = provider
        return billing
    finally:
        handle.terminate(grace_seconds=0.25)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()
        if not handle.shutdown_confirmed():
            raise BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'The account read process stop could not be confirmed')
