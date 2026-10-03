"""Bounded, model-free Codex account and rate-limit readback."""
from __future__ import annotations

import subprocess
import threading
import time

from ..base import ProcessHandle
from .protocol import Connection, quota_candidate_from_response
from ...runtime.windows_process import owned_popen
from ....protocol.billing import codex_account
from ....blackboard.store.db import utc_now
from ..discovery import native_environment
from ....protocol.usage import normalize_quota


def read(command: list[str], environment: dict) -> tuple[dict | None, dict | None]:
    """Read two account RPCs; never start a thread, turn or model request."""
    if not command:
        return None, None
    child = owned_popen([*command, "app-server", "--listen", "stdio://"],
                        env=native_environment(environment, command=command),
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
        observed_at = utc_now()
        billing = codex_account(account, observed_at)
        try:
            response = connection.call("account/rateLimits/read", {})
            quota = normalize_quota(quota_candidate_from_response(response, observed_at=utc_now()))
        except Exception:
            quota = None
        return billing, quota
    finally:
        handle.terminate(grace_seconds=0.25)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()
        if not handle.shutdown_confirmed():
            from ....errors import BoardError
            raise BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'The account read process stop could not be confirmed')
