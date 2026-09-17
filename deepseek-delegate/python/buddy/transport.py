"""C-Two client and single-daemon bootstrap. No DSH lifecycle is owned here."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

import c_two as cc

from .contracts import BuddyControl

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CONTROL_NAME = "buddy-control"
INBOX_NAME = "buddy-completion-inbox"


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def get_state_dir(state_dir: str | Path | None = None) -> Path:
    return Path(state_dir or os.environ.get("BUDDY_STATE_DIR") or Path.home() / ".local/share/hey-my-buddy").expanduser().resolve()


def encode_message(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ServiceError("INVALID_ARGUMENT", "Request must be JSON serializable") from exc
    if len(text.encode()) > MAX_MESSAGE_BYTES:
        raise ServiceError("MESSAGE_TOO_LARGE", "Request exceeds 8 MiB")
    return text


def _endpoint(directory: Path) -> dict | None:
    try:
        value = json.loads((directory / "control.json").read_text())
        if not isinstance(value, dict) or not isinstance(value.get("address"), str) or not value["address"].startswith("ipc://") or not isinstance(value.get("token"), str) or not value["token"]:
            return None
        return value
    except (OSError, ValueError):
        return None


def _request(endpoint: dict, method: str, params: dict) -> dict:
    request = encode_message({"token": endpoint["token"], "method": method, "params": params})
    try:
        with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as service:
            raw = service.dispatch(request)
    except Exception as exc:
        raise ServiceError("SERVICE_UNAVAILABLE", "C-Two control request failed") from exc
    if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE_BYTES:
        raise ServiceError("INVALID_RESPONSE", "Invalid or oversized service response")
    try:
        reply = json.loads(raw)
        if "error" in reply:
            raise ServiceError(reply["error"]["code"], reply["error"]["message"])
        result = reply["result"]
        if not isinstance(result, dict):
            raise ValueError("Expected result object")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError("INVALID_RESPONSE", "Malformed service response") from exc


def _healthy(directory: Path) -> dict | None:
    endpoint = _endpoint(directory)
    if endpoint:
        try:
            _request(endpoint, "health", {})
            return endpoint
        except ServiceError:
            pass
    return None


def ensure_service(state_dir: str | Path | None = None) -> dict:
    directory = get_state_dir(state_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    endpoint = _healthy(directory)
    if endpoint:
        return endpoint
    lock_fd = os.open(directory / "control-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        endpoint = _healthy(directory)
        if endpoint:
            return endpoint
        env = dict(os.environ)
        env.update(BUDDY_STATE_DIR=str(directory), C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="")
        source_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log_fd = os.open(directory / "control.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            child = subprocess.Popen([sys.executable, "-m", "buddy.daemon"], env=env, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd, start_new_session=True, close_fds=True)
            # Keep/reap the detached child while this client lives. Client exit
            # still leaves the independently owned daemon running.
            threading.Thread(target=child.wait, name="buddy-daemon-reaper", daemon=True).start()
        finally:
            os.close(log_fd)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            endpoint = _healthy(directory)
            if endpoint:
                return endpoint
            if child.poll() not in (None, 0):
                raise ServiceError("SERVICE_START_FAILED", "Buddy daemon failed to start; inspect control.log")
            time.sleep(0.05)
        raise ServiceError("SERVICE_START_TIMEOUT", "Buddy daemon did not become ready; inspect control.log")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def call_service(method: str, params: dict | None = None, state_dir: str | Path | None = None) -> dict:
    if not isinstance(method, str) or not method or (params is not None and not isinstance(params, dict)):
        raise ServiceError("INVALID_ARGUMENT", "method must be a string and params an object")
    # Validate before spawning a daemon. Never replay a failed business RPC:
    # its execution may already have reached the service.
    encode_message({"method": method, "params": params or {}})
    if method == "stop":
        endpoint = _healthy(get_state_dir(state_dir))
        if endpoint is None:
            return {"status": "stopped", "alreadyStopped": True}
    else:
        endpoint = ensure_service(state_dir)
    return _request(endpoint, method, params or {})
