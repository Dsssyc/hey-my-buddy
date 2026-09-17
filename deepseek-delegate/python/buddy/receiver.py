"""Detached completion inbox: its lifetime is independent of MCP clients."""
from __future__ import annotations

import asyncio
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from uuid import UUID

import c_two as cc

from .contracts import CompletionInbox, NotificationReceiverControl
from .native import CompletionReceiver
from .transport import INBOX_NAME, ServiceError, call_service, encode_message, get_state_dir


def _directory(state_dir=None):
    return get_state_dir(state_dir) / "notifications"


def _endpoint(directory):
    try:
        path = directory / "receiver.json"
        if path.stat().st_mode & 0o077:
            return None
        endpoint = json.loads(path.read_text())
        if not isinstance(endpoint, dict) or not isinstance(endpoint.get("address"), str) or not endpoint["address"].startswith("ipc://") or not isinstance(endpoint.get("token"), str) or not endpoint["token"]:
            return None
        return endpoint
    except (OSError, ValueError):
        return None


def _request(endpoint, method, payload):
    try:
        with cc.connect(NotificationReceiverControl, name="buddy-notification-control", address=endpoint["address"]) as inbox:
            raw = getattr(inbox, method)(payload)
        if not isinstance(raw, str) or len(raw.encode()) > 16384:
            raise ValueError()
        reply = json.loads(raw)
        if not isinstance(reply, dict):
            raise ValueError()
        if "error" in reply:
            raise ServiceError(reply["error"]["code"], reply["error"]["message"])
        return reply
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("RECEIVER_UNAVAILABLE", "Completion receiver request failed") from exc


def _healthy(directory):
    endpoint = _endpoint(directory)
    if endpoint:
        try:
            if _request(endpoint, "ping", endpoint["token"]).get("status") == "ready":
                return endpoint
        except ServiceError:
            pass
    return None


def _lock_until(fd, deadline):
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ServiceError("RECEIVER_START_TIMEOUT", "Completion receiver startup lock timed out")
            time.sleep(0.05)


def _ensure_receiver(state_dir=None):
    directory = _directory(state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    deadline = time.monotonic() + 20
    lock_fd = os.open(directory / "receiver-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_until(lock_fd, deadline)
        endpoint = _healthy(directory)
        if endpoint:
            return endpoint
        env = dict(os.environ)
        env.update(BUDDY_STATE_DIR=str(directory.parent), C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="")
        source_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log_fd = os.open(directory / "receiver.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            child = subprocess.Popen([sys.executable, "-m", "buddy.receiver"], env=env, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd, start_new_session=True, close_fds=True)
            threading.Thread(target=child.wait, name="buddy-receiver-reaper", daemon=True).start()
        finally:
            os.close(log_fd)
        while time.monotonic() < deadline:
            endpoint = _healthy(directory)
            if endpoint:
                return endpoint
            if child.poll() is not None:
                raise ServiceError("RECEIVER_START_FAILED", "Completion receiver failed to start; inspect receiver.log")
            time.sleep(0.05)
        raise ServiceError("RECEIVER_START_TIMEOUT", "Completion receiver did not become ready")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def register_notification(request_id, thread_id, state_dir=None):
    pipe = os.environ.get("CODEX_APP_TOOLS_PIPE_PATH")
    if not isinstance(pipe, str) or not pipe or not os.path.isabs(pipe):
        raise ServiceError("NATIVE_UNAVAILABLE", "The App did not supply its native tool pipe")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ServiceError("INVALID_ARGUMENT", "Invalid notification request ID")
    try:
        thread_id = str(UUID(thread_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ServiceError("INVALID_ARGUMENT", "Invalid caller thread ID") from exc
    endpoint = _ensure_receiver(state_dir)
    return _request(endpoint, "bind", encode_message({"token": endpoint["token"], "requestId": request_id, "threadId": thread_id, "pipePath": pipe}))


def stop_receiver(state_dir=None):
    endpoint = _healthy(_directory(state_dir))
    return _request(endpoint, "stop", endpoint["token"]) if endpoint else {"status": "stopped", "alreadyStopped": True}


class ReceiverInbox:
    def __init__(self, receiver, token, address, loop, stopping):
        self.receiver, self.token, self.address = receiver, token, address
        self.loop, self.stopping = loop, stopping
        self.lock = threading.Lock()

    def _authorize(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            raise ServiceError("UNAUTHORIZED", "Invalid receiver token")

    def _reply(self, operation):
        try:
            return encode_message(operation())
        except ServiceError as exc:
            return encode_message({"error": {"code": exc.code, "message": str(exc)}})
        except Exception:
            return encode_message({"error": {"code": "INVALID_ARGUMENT", "message": "Invalid receiver request"}})

    def ping(self, token):
        def action():
            self._authorize(token)
            return {"status": "ready"}
        return self._reply(action)

    def stop(self, token):
        def action():
            self._authorize(token)
            self.loop.call_soon_threadsafe(self.stopping.set)
            return {"status": "stopping"}
        return self._reply(action)

    def bind(self, binding_json):
        def action():
            if not isinstance(binding_json, str) or len(binding_json.encode()) > 16384:
                raise ValueError()
            data = json.loads(binding_json)
            self._authorize(data.get("token"))
            if set(data) != {"token", "requestId", "threadId", "pipePath"}:
                raise ValueError()
            request_id, pipe = data["requestId"], data["pipePath"]
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128 or not isinstance(pipe, str) or not os.path.isabs(pipe) or "\0" in pipe:
                raise ValueError()
            thread_id = str(UUID(data["threadId"]))
            with self.lock:
                token = self.receiver.bind(request_id, thread_id, pipe_path=pipe)
            return {"address": self.address, "token": token, "threadId": thread_id}
        return self._reply(action)

    def submit(self, event_json):
        # Per-run binding tokens authorize delivery; the endpoint token authorizes management.
        return self.receiver.submit(event_json)


async def _run(directory, token):
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopping.set)
    receiver = CompletionReceiver(directory, loop, lambda method, params: call_service(method, params, directory.parent))
    inbox = ReceiverInbox(receiver, token, "", loop, stopping)
    cc.register(CompletionInbox, receiver, name=INBOX_NAME, concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
    cc.register(NotificationReceiverControl, inbox, name="buddy-notification-control", concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
    cc.serve(blocking=False)
    address = cc.server_address()
    inbox.address = address
    path = directory / "receiver.json"
    temporary = directory / (".receiver-" + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"address": address, "token": token, "pid": os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        await stopping.wait()
    finally:
        cc.shutdown()
        await asyncio.sleep(0)  # Drain delivery callbacks queued before transport stopped.
        pending = list(receiver.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # A task cancelled before its coroutine first runs cannot persist its own outcome.
        with sqlite3.connect(receiver.database) as db:
            db.execute("UPDATE events SET state='unknown',detail='Receiver stopped before delivery was confirmed' WHERE state IN ('received','dispatching')")
        # Never resubmit uncertain delivery during shutdown or a later startup.
        if (_endpoint(directory) or {}).get("token") == token:
            path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def main():
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    fd = os.open(directory / "receiver-daemon.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        asyncio.run(_run(directory, secrets.token_urlsafe(32)))
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
