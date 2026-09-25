"""C-Two client, endpoint trust rules and single-daemon bootstrap.

The client speaks only named board operations. There is no ``dispatch(method, JSON)``
facade and no Python-to-Node engine relay: each CLI method maps to one validated
C-Two operation here.
"""
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

from . import rpc_config
from .contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from .errors import BoardError

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
PROTOCOL_VERSION = 2

#: CLI method -> (resource, C-Two operation). The CLI names are the only public
#: surface; the C-Two operation names never change here. ``await`` is a CLI-level
#: read-only wait implemented on top of these in ``blocking.py`` and never appears
#: here. The single governed goal lifecycle keeps the bare names; the advanced
#: ``execution-*`` names reach the ordinary task records that the command/external
#: adapters and the internal decision infrastructure own.
METHOD_MAP: dict[str, tuple[str, str]] = {
    "ping": ("control", "ping"),
    "health": ("control", "health"),
    "capabilities": ("control", "capabilities"),
    "adapters": ("control", "capabilities"),
    "runtime": ("control", "runtime_info"),
    # -- single governed goal lifecycle ------------------------------------
    "submit": ("control", "workflow_submit"),
    "get": ("control", "workflow_get"),
    "decide": ("control", "workflow_decide"),
    "continue": ("control", "workflow_continue"),
    "takeover": ("control", "workflow_takeover"),
    "cancel": ("control", "workflow_cancel"),
    "acknowledge": ("control", "workflow_acknowledge"),
    "scope-amend": ("control", "workflow_scope_amend"),
    "workspace-resolve": ("control", "workflow_workspace_resolve"),
    "integration-record": ("control", "workflow_integration_record"),
    "workspace-cleanup-plan": ("control", "workspace_cleanup_plan"),
    "workspace-cleanup-apply": ("control", "workspace_cleanup_apply"),
    "suggest": ("control", "workflow_suggest"),
    # -- execution records for command/external/decision infrastructure -----
    "execution-submit": ("control", "task_submit"),
    "execution-cancel": ("control", "task_cancel"),
    "execution-retry": ("control", "task_retry"),
    "execution-acknowledge": ("control", "task_acknowledge"),
    # -- execution observation stays distinct -------------------------------
    "status": ("control", "task_get"),
    "list": ("control", "task_list"),
    "result": ("control", "task_result"),
    "wait": ("wait", "task_wait"),
    "watch": ("wait", "events_wait"),
    "events": ("control", "events_read"),
    "inquire": ("control", "inquiry_observe"),
    "workers": ("control", "worker_list"),
    "worker-register": ("control", "worker_register"),
    "worker-claim": ("control", "worker_claim"),
    "worker-reconcile": ("control", "worker_reconcile"),
    "worker-renew": ("control", "worker_renew"),
    "worker-progress": ("control", "worker_progress"),
    "worker-result": ("control", "worker_result"),
    "worker-release": ("control", "worker_release"),
    "message": ("control", "message_post"),
    "messages": ("control", "message_list"),
    "message-get": ("control", "message_get"),
    "message-update": ("control", "message_update"),
    "artifacts": ("control", "artifact_list"),
    "console": ("control", "console"),
    "console-snapshot": ("control", "console_snapshot"),
    "evaluation-write-begin": ("control", "evaluation_write_begin"),
    "evaluation-write-renew": ("control", "evaluation_write_renew"),
    "user-policy-publish": ("control", "user_policy_publish"),
    "assessment-publish": ("control", "assessment_publish"),
    "evaluation-write-abort": ("control", "evaluation_write_abort"),
    "evaluation-reader-begin": ("control", "evaluation_reader_begin"),
    "evaluation-reader-release": ("control", "evaluation_reader_release"),
    "evaluation-evidence-record": ("control", "evaluation_evidence_record"),
    "evaluation-prepare": ("control", "evaluation_prepare"),
    "evaluation-history": ("control", "evaluation_history"),
    "selection-request": ("control", "selection_request"),
    "selection-get": ("control", "selection_get"),
    "selection-list": ("control", "selection_list"),
    "model-catalog-refresh": ("control", "model_catalog_refresh"),
    "model-profiles": ("control", "model_profiles"),
    "stop": ("control", "service_control"),
    "restart": ("control", "service_control"),
    "wait-capacity": ("wait", "wait_capacity"),
}

#: Methods whose response body wraps the execution task view under ``task``.
UNWRAP_TASK = frozenset({"execution-submit", "execution-cancel", "execution-retry", "execution-acknowledge", "status"})
#: Methods whose response body *is* the task view (the wait route returns it directly).
DIRECT_TASK_VIEW = frozenset({"wait"})


class ServiceError(BoardError):
    """A board failure raised on the client side of the boundary."""


def get_state_dir(state_dir: str | Path | None = None) -> Path:
    return Path(
        state_dir or os.environ.get("BUDDY_STATE_DIR") or Path.home() / ".local/share/hey-my-buddy/state"
    ).expanduser().resolve()


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
        return (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077
        )
    except OSError:
        return False


def _trusted_ancestors(directory: Path) -> bool:
    """True when no ancestor above the state directory can be swapped by another user."""
    try:
        current = directory.parent
        while True:
            parent = os.lstat(current)
            if stat.S_ISLNK(parent.st_mode):
                if parent.st_uid not in (os.geteuid(), 0):
                    return False
            elif (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid not in (os.geteuid(), 0)
                or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX)
            ):
                return False
            if current.parent == current:
                return True
            current = current.parent
    except OSError:
        return False


def _trusted_directory(directory: Path) -> bool:
    return _private_directory(directory) and _trusted_ancestors(directory)


def _read_endpoint(directory: Path) -> dict | None:
    """Read control.json without following links and without touching permissions."""
    if not _private_directory(directory) or not _trusted_ancestors(directory):
        return None
    fd = -1
    try:
        fd = os.open(directory / "control.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            return None
        value = json.loads(os.read(fd, MAX_MESSAGE_BYTES + 1))
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("address"), str)
            or not value["address"].startswith("ipc://")
            or not isinstance(value.get("token"), str)
            or not value["token"]
        ):
            return None
        return value
    except (OSError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _request(endpoint: dict, operation: str, params: dict, resource: str = "control") -> dict:
    # The token travels with the operation's own parameters and is verified by the
    # resource before any schema validation happens.
    request = encode_message({"token": endpoint["token"], **params})
    contract = BuddyControl if resource == "control" else BuddyWait
    name = CONTROL_NAME if resource == "control" else WAIT_NAME
    # One process may not connect before the private profile is in place; this is the
    # single client chokepoint, and the call is a no-op after the first time.
    rpc_config.configure_client()
    try:
        with cc.connect(contract, name=name, address=endpoint["address"]) as service:
            raw = getattr(service, operation)(request)
    except Exception as exc:
        raise ServiceError("SERVICE_UNAVAILABLE", "The board service did not answer this operation") from exc
    if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE_BYTES:
        raise ServiceError("INVALID_RESPONSE", "Invalid or oversized service response")
    try:
        reply = json.loads(raw)
        if "error" in reply:
            error = reply["error"]
            raise ServiceError(error.get("code", "SERVICE_ERROR"), error.get("message", "operation failed"))
        if not isinstance(reply, dict):
            raise ValueError("Expected a result object")
        return reply
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError("INVALID_RESPONSE", "Malformed service response") from exc


def call_board(operation: str, params: dict | None = None, state_dir: str | Path | None = None, *, resource: str = "control", endpoint: dict | None = None) -> dict:
    """Call one named C-Two board operation through a healthy, trusted endpoint."""
    endpoint = endpoint or ensure_service(state_dir, resource=resource)
    return _request(endpoint, operation, params or {}, resource)


def _healthy(directory: Path) -> dict | None:
    """Return a trusted endpoint only when its service answers, without any write."""
    endpoint = _read_endpoint(directory)
    if endpoint:
        try:
            _request(endpoint, "ping", {})
            return endpoint
        except ServiceError:
            pass
    return None


def _attach_read_only(directory: Path) -> dict | None:
    """Read-only attachment: never creates, chmods, locks, logs or spawns."""
    return _healthy(directory)


def ensure_service(state_dir: str | Path | None = None, *, resource: str = "control") -> dict:
    """Attach to a healthy daemon or cold-start one, preserving read-only attach."""
    directory = get_state_dir(state_dir)
    endpoint = _attach_read_only(directory)
    if endpoint:
        return endpoint
    if resource == "wait":
        # A wait may never cold-start a service: waiting on nothing would be a lie.
        raise ServiceError("SERVICE_UNAVAILABLE", "No board service is running in this state directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    lock_fd = os.open(directory / "control-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        endpoint = _attach_read_only(directory)
        if endpoint:
            return endpoint
        from . import runtime

        # Cold start installs and runs from the content-addressed stable runtime, so
        # the default path never falls back to the replaceable plugin cache. Only an
        # explicit BUDDY_DEV_SOURCE=1 development/test run executes from the checkout,
        # and that reports stable=false.
        target = runtime.launch_target(log_path=directory / "runtime-install.log")
        env = dict(os.environ)
        env.update(BUDDY_STATE_DIR=str(directory), C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="")
        env["BUDDY_RUNTIME_IDENTITY"] = target["identity"]
        if target["pythonPath"]:
            env["PYTHONPATH"] = target["pythonPath"] + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
        else:
            env.pop("PYTHONPATH", None)
        if target["stable"]:
            env["BUDDY_RUNTIME"] = target["runtime"]["runtimeDir"]
        log_fd = os.open(directory / "control.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            child = subprocess.Popen(
                [target["python"], "-m", "buddy.daemon"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                close_fds=True,
            )
            threading.Thread(target=child.wait, name="buddy-daemon-reaper", daemon=True).start()
        finally:
            os.close(log_fd)
        # A first cold start may install a runtime before the daemon exists.
        deadline = time.monotonic() + (300 if target.get("installed") else 20)
        while time.monotonic() < deadline:
            endpoint = _healthy(directory)
            if endpoint:
                return endpoint
            if child.poll() not in (None, 0):
                raise ServiceError("SERVICE_START_FAILED", "The board daemon failed to start; inspect control.log")
            time.sleep(0.05)
        raise ServiceError("SERVICE_START_TIMEOUT", "The board daemon did not become ready; inspect control.log")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def call_service(method: str, params: dict | None = None, state_dir: str | Path | None = None) -> dict:
    """CLI-facing call: maps one method name onto one named C-Two operation."""
    if not isinstance(method, str) or method not in METHOD_MAP:
        raise ServiceError("INVALID_ARGUMENT", f"Unknown method {method!r}")
    if params is not None and not isinstance(params, dict):
        raise ServiceError("INVALID_ARGUMENT", "params must be an object")
    params = dict(params or {})
    resource, operation = METHOD_MAP[method]
    if method in ("stop", "restart"):
        params.setdefault("action", method)
    # Validate locally before any daemon spawn so an invalid request never starts work.
    encode_message({"method": method, "params": params})
    if method in ("stop", "restart"):
        endpoint = _attach_read_only(get_state_dir(state_dir))
        if endpoint is None:
            return {"status": "stopped", "alreadyStopped": True, "stopped": True}
    else:
        endpoint = ensure_service(state_dir, resource=resource)
    reply = _request(endpoint, operation, params, resource=resource)
    if method in UNWRAP_TASK:
        task = reply.get("task")
        if not isinstance(task, dict):
            raise ServiceError("INVALID_RESPONSE", "The service did not return a task")
        return {**task, **{key: reply[key] for key in ("duplicate", "alreadyTerminal", "retry") if key in reply}}
    if method in DIRECT_TASK_VIEW:
        if not isinstance(reply.get("runId"), str):
            raise ServiceError("INVALID_RESPONSE", "The wait route did not return a task view")
        return reply
    return reply


def request_stop(state_dir: str | Path | None = None, *, action: str = "stop", drain_seconds: int = 10) -> dict:
    """Ask the daemon to stop or restart through its own endpoint (never a signal)."""
    return call_service(action, {"drainSeconds": drain_seconds}, state_dir)
