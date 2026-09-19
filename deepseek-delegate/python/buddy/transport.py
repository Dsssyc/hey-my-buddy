"""C-Two client and single-daemon bootstrap. No DSH lifecycle is owned here."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from typing import Any

import c_two as cc

from .contracts import BuddyControl

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CONTROL_NAME = "buddy-control"


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


def _private_directory(directory: Path) -> bool:
    """True only for a real directory this user owns with no group/other access."""
    try:
        info = os.lstat(directory)
        return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077
    except OSError:
        return False


def _trusted_ancestors(directory: Path) -> bool:
    """True when no ancestor above the state directory can be swapped by another user.

    A group/world-writable ancestor is trusted only when it carries the sticky
    bit. POSIX then allows an entry to be renamed or removed only by its owner,
    the directory's owner or root, so another user cannot replace this user's
    private state directory inside a shared location such as a root-owned /tmp.
    A group/world-writable ancestor without the sticky bit stays untrusted.
    """
    try:
        current = directory.parent
        while True:
            parent = os.lstat(current)
            if stat.S_ISLNK(parent.st_mode):
                if parent.st_uid not in (os.geteuid(), 0):
                    return False
            elif not stat.S_ISDIR(parent.st_mode) or parent.st_uid not in (os.geteuid(), 0) or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
                return False
            if current.parent == current:
                return True
            current = current.parent
    except OSError:
        return False


def _trusted_directory(directory: Path) -> bool:
    """True only for a private directory this user owns on a path others cannot replace.

    The state directory itself must be a real directory owned by this user,
    granting no group or other access, and reached without a symlink. Every
    ancestor above it must be unswappable by another user: either this user's or
    root's, and either closed to group/other writes or sticky, so that other
    users cannot rename this user's entry even in a shared /tmp. That still
    rejects a state directory under a foreign-owned or non-sticky shared parent,
    where another user could rename it and plant an endpoint of their own.
    """
    return _private_directory(directory) and _trusted_ancestors(directory)


def _read_endpoint(directory: Path) -> dict | None:
    """Read control.json without following links and without touching permissions.

    The state directory and endpoint file must both be private and owned by this
    user and sit on a path other users cannot swap. Nothing is created, chmodded
    or locked here; an untrusted or malformed endpoint is reported as absent.
    """
    if not _private_directory(directory) or not _trusted_ancestors(directory):
        return None
    fd = -1
    try:
        fd = os.open(directory / "control.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            return None
        value = json.loads(os.read(fd, MAX_MESSAGE_BYTES + 1))
        if not isinstance(value, dict) or not isinstance(value.get("address"), str) or not value["address"].startswith("ipc://") or not isinstance(value.get("token"), str) or not value["token"]:
            return None
        return value
    except (OSError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


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
    """Return a trusted endpoint only when its service answers, without any write."""
    endpoint = _read_endpoint(directory)
    if endpoint:
        try:
            _request(endpoint, "health", {})
            return endpoint
        except ServiceError:
            pass
    return None


def _attach_read_only(directory: Path) -> dict | None:
    """Return a healthy endpoint only if it can be trusted and used without any write.

    This is the read-only attachment path. It never creates, chmods, locks, logs
    or spawns: an existing private service is used as-is, and anything less than
    a healthy, correctly owned, private endpoint falls through to cold startup.
    """
    return _healthy(directory)


def ensure_service(state_dir: str | Path | None = None) -> dict:
    directory = get_state_dir(state_dir)
    # Fast path: an already healthy private service is attached read-only, with
    # no mkdir/chmod/lock/log/spawn. Anything untrusted falls through to the
    # unchanged cold-start path below, which still fails honestly if the
    # authorized setup writes are not permitted.
    endpoint = _attach_read_only(directory)
    if endpoint:
        return endpoint
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    lock_fd = os.open(directory / "control-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        endpoint = _attach_read_only(directory)
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
            # The freshly created private directory and its daemon's endpoint are
            # validated by exactly the same trust rule as any later attachment:
            # no ancestor exception is needed now that a sticky shared parent is
            # recognized as unswappable by other users.
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
        endpoint = _attach_read_only(get_state_dir(state_dir))
        if endpoint is None:
            return {"status": "stopped", "alreadyStopped": True}
    else:
        endpoint = ensure_service(state_dir)
    return _request(endpoint, method, params or {})
