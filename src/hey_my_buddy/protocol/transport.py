"""C-Two client, endpoint trust rules and single-daemon bootstrap.

The client speaks only named board operations. There is no ``dispatch(method, JSON)``
facade and no Python-to-Node engine relay: each CLI method maps to one validated
C-Two operation here.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from .. import locking, private_dirs
import json
import os
from pathlib import Path
import socket
import tempfile
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, NoReturn

import c_two as cc
from c_two.error import CCError, CoreError

from . import rpc_config
from .service_process import independent_service_popen
from .contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from ..errors import BoardError
from ..state_root import check_state_root, get_state_dir
from ..install.entrypoints import ENTRY_MODULES

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
PROTOCOL_VERSION = 2

#: The two locks a board owner holds from admission until its process exits.
LIFETIME_LOCKS = ("control-daemon.lock", "board-owner.lock")


def owner_held_message() -> str:
    """The one owner-busy explanation shared by every lifetime-lock refusal."""
    return "A board owner still holds this state directory's lifetime locks."

#: CLI method -> (resource, C-Two operation). The CLI names are the only public
#: surface; the C-Two operation names never change here. ``await`` is a CLI-level
#: read-only wait implemented on top of these in ``blocking.py`` and never appears
#: here. The single micro task lifecycle keeps the bare names; the advanced
#: ``execution-*`` names manage ordinary task records created by the workflow
#: and internal decision infrastructure.
METHOD_MAP: dict[str, tuple[str, str]] = {
    "accounts": ("control", "accounts"),
    "ping": ("control", "ping"),
    "health": ("control", "health"),
    "harnesses": ("control", "capabilities"),
    "harness-set": ("control", "harness_set"),
    "quota-redetect": ("control", "quota_redetect"),
    "runtime": ("control", "runtime_info"),
    "storage-plan": ("control", "storage_plan"),
    "storage-apply": ("control", "storage_apply"),
    "worker-sessions": ("control", "worker_sessions"),
    # -- single micro task lifecycle ------------------------------------
    "submit": ("control", "workflow_submit"),
    "get": ("control", "workflow_get"),
    "decide": ("control", "workflow_decide"),
    "continue": ("control", "workflow_continue"),
    "takeover": ("control", "workflow_takeover"),
    "cancel": ("control", "workflow_cancel"),
    "accept": ("control", "workflow_accept"),
    "conclude": ("control", "workflow_conclude"),
    "reclaim": ("control", "workflow_reclaim"),
    "scope-amend": ("control", "workflow_scope_amend"),
    "workspace-resolve": ("control", "workflow_workspace_resolve"),
    "suggest": ("control", "workflow_suggest"),
    # -- work macro tasks: read-only browsing of grouped micro tasks ----------
    "macro-task-list": ("control", "macro_task_list"),
    "macro-task-timeline": ("control", "macro_task_timeline"),
    # -- execution records for command/decision infrastructure -----
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
    "messages": ("control", "message_list"),
    "message-get": ("control", "message_get"),
    "artifacts": ("control", "artifact_list"),
    "console": ("control", "console"),
    "console-snapshot": ("control", "console_snapshot"),
    "user-policy-publish": ("control", "user_policy_publish"),
    "selection-request": ("control", "selection_request"),
    "selection-get": ("control", "selection_get"),
    "selection-list": ("control", "selection_list"),
    "model-catalog-refresh": ("control", "model_catalog_refresh"),
    "buddy-list": ("control", "buddy_list"),
    "stop": ("control", "service_control"),
    "restart": ("control", "service_control"),
    "wait-capacity": ("wait", "wait_capacity"),
}

#: Methods whose response body wraps the execution task view under ``task``.
UNWRAP_TASK = frozenset({"execution-cancel", "execution-retry", "execution-acknowledge", "status"})
#: Methods whose response body *is* the task view (the wait route returns it directly).
DIRECT_TASK_VIEW = frozenset({"wait"})


class ServiceError(BoardError):
    """A board failure raised on the client side of the boundary."""


def local_rpc_error(error: CoreError | CCError) -> ServiceError:
    """Keep C-Two's local I/O cause visible at the Host boundary."""
    details = dict(getattr(error, 'details', None) or {})
    details.update({key: value for key in ('category', 'transport_kind', 'transport_phase',
               'io_kind', 'raw_os_error', 'io_operation')
               if (value := getattr(error, key, None)) is not None})
    kind = details.get('io_kind')
    if kind == 'permission_denied':
        code = 'LAUNCH_ACCESS_DENIED'
    elif kind in ('not_found', 'connection_refused'):
        code = 'SERVICE_UNAVAILABLE'
    elif isinstance(error, CCError):
        code = 'C_TWO_' + error.code.name.removeprefix('ERROR_')
    else:
        code = 'C_TWO_' + error.category.upper()
    return ServiceError(code, str(error), **details)


def encode_message(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ServiceError("INVALID_ARGUMENT", "Request must be JSON serializable") from exc
    if len(text.encode()) > MAX_MESSAGE_BYTES:
        raise ServiceError("MESSAGE_TOO_LARGE", "Request exceeds 8 MiB")
    return text


def _read_endpoint(directory: Path) -> dict | None:
    """Read control.json without following links and without touching permissions."""
    fd = -1
    try:
        directory = check_state_root(directory)
        fd = private_dirs.open_regular_fd(directory / "control.json", os.O_RDONLY)
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
    except (OSError, ValueError, BoardError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _invoke(service, endpoint: dict, operation: str, params: dict) -> dict:
    # The token travels with the operation's own parameters and is verified by the
    # resource before any schema validation happens.
    payload = {"token": endpoint["token"], **params}
    if operation in {'capabilities', 'buddy_list', 'model_catalog_refresh', 'workflow_submit', 'selection_request', 'console_snapshot'}:
        payload['_harnessPathHint'] = os.environ.get('PATH', '')
    request = encode_message(payload)
    try:
        raw = getattr(service, operation)(request)
    except (CoreError, CCError) as exc:
        raise local_rpc_error(exc) from exc
    if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE_BYTES:
        raise ServiceError("INVALID_RESPONSE", "Invalid or oversized service response")
    try:
        reply = json.loads(raw)
        if "error" in reply:
            error = reply["error"]
            details = error.get("details")
            raise ServiceError(error.get("code", "SERVICE_ERROR"), error.get("message", "operation failed"),
                               **(details if isinstance(details, dict) else {}))
        if not isinstance(reply, dict):
            raise ValueError("Expected a result object")
        return reply
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError("INVALID_RESPONSE", "Malformed service response") from exc


@contextmanager
def _connection(endpoint: dict, resource: str, directory: Path):
    contract = BuddyControl if resource == "control" else BuddyWait
    name = CONTROL_NAME if resource == "control" else WAIT_NAME
    # Validate the paths and select the private domain before opening a connection.
    rpc_config.configure_client(directory, create=False)
    with ExitStack() as stack:
        try:
            service = stack.enter_context(cc.connect(contract, name=name, address=endpoint["address"]))
        except (CoreError, CCError) as exc:
            raise local_rpc_error(exc) from exc
        yield service


def _request(endpoint: dict, operation: str, params: dict, resource: str = "control", *, state_dir: Path) -> dict:
    with _connection(endpoint, resource, state_dir) as service:
        return _invoke(service, endpoint, operation, params)


def _unheld(path: Path) -> bool:
    """True when no owner holds this lock; a missing file cannot be held."""
    try:
        fd = private_dirs.open_regular_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        return True
    except (OSError, BoardError):
        return False
    try:
        locking.lock(fd, shared=True, blocking=False)
        locking.unlock(fd)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def _owner_refusal(directory: Path, *, fenced: bool = True) -> str | None:
    """Observe the owner's lifetime locks once; None only when both prove absence.

    Any other outcome returns the refusal naming what the observation actually
    saw: a start in progress behind a busy start fence, or the owner message for
    a lifetime lock that is held or cannot be read. This is read-only
    observation: existing files are opened without O_CREAT and nothing is
    created, deleted or spawned. A held lock, a busy start fence or an
    unreadable file never proves absence, because a connection failure is not
    stop evidence. ``fenced`` is False only for a caller already holding the
    start lock, which the same process cannot take twice.
    """
    fence = -1
    try:
        if fenced:
            try:
                fence = private_dirs.open_regular_fd(directory / "control-start.lock", os.O_RDONLY)
            except FileNotFoundError:
                pass
            except (OSError, BoardError):
                return owner_held_message()
            else:
                try:
                    locking.lock(fence, shared=True, blocking=False)
                except BlockingIOError:
                    return "A board service start is in progress in this state directory"
        if not all(_unheld(directory / name) for name in LIFETIME_LOCKS):
            return owner_held_message()
        return None
    finally:
        if fence >= 0:
            locking.unlock(fence)
            os.close(fence)


def _refuse_without_service(directory: Path, *, under_start_lock: bool = False) -> NoReturn:
    """Every endpoint-less read-only or wait path refuses through one observation."""
    refusal = _owner_refusal(directory, fenced=not under_start_lock)
    if refusal:
        raise ServiceError("SERVICE_UNAVAILABLE", refusal)
    raise ServiceError("SERVICE_UNAVAILABLE", "No board service is running in this state directory")


@contextmanager
def _event_waiter(directory: Path):
    """Keep the named wait resource attached across bounded caller timeouts."""
    endpoint = _attach_read_only(directory)
    if endpoint is None:
        _refuse_without_service(directory)
    with _connection(endpoint, "wait", directory) as service:
        yield lambda params: _invoke(service, endpoint, "events_wait", params)


def call_board(operation: str, params: dict | None = None, state_dir: str | Path | None = None, *, resource: str = "control", endpoint: dict | None = None) -> dict:
    """Call one named C-Two board operation through a healthy, trusted endpoint."""
    directory = get_state_dir(state_dir)
    return _call_board(operation, params, directory, resource=resource, endpoint=endpoint)


def _call_board(operation: str, params: dict | None, directory: Path, *, resource: str = "control", endpoint: dict | None = None) -> dict:
    endpoint = endpoint or _ensure_service(directory, resource=resource)
    return _request(endpoint, operation, params or {}, resource, state_dir=directory)


def _call_board_read_only(operation: str, params: dict | None, directory: Path, *, resource: str = "control") -> dict:
    """Call through an existing service using the already selected state root."""
    endpoint = _attach_read_only(directory)
    if endpoint is None:
        _refuse_without_service(directory)
    return _request(endpoint, operation, params or {}, resource=resource, state_dir=directory)


def _ping_endpoint(directory: Path, endpoint: dict | None) -> dict | None:
    if endpoint:
        try:
            _request(endpoint, "ping", {}, state_dir=directory)
            return endpoint
        except ServiceError as error:
            if error.code != "SERVICE_UNAVAILABLE":
                raise
    return None


def _healthy(directory: Path) -> dict | None:
    """Read control.json and ping after the state boundary has been checked."""
    return _ping_endpoint(directory, _read_endpoint(directory))


def _attach_read_only(directory: Path) -> dict | None:
    """Read-only attachment: never creates, chmods, locks, logs or spawns."""
    directory = get_state_dir(directory)
    endpoint = _read_endpoint(directory)
    if endpoint is None:
        return None
    if (os.name != "nt" and not (directory / "ipc").exists()
            and not private_dirs.linked(directory / "ipc")):
        return None
    return _ping_endpoint(directory, endpoint)


def _cold_start_preflight(directory: Path) -> None:
    """Prove state writes and local IPC before inheriting this process into a daemon."""
    directory = check_state_root(directory, create=True)
    probe = directory / ('.launch-probe-' + uuid.uuid4().hex[:12])
    socket_directory = None
    socket_directory_fd = None
    operation = 'configure_local_endpoint'
    try:
        rpc_config.configure_local_endpoint(directory)
        operation = 'state_write'
        fd = private_dirs.open_regular_fd(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        if os.name != 'nt':
            # State paths need not fit sockaddr_un; C-Two also uses short private
            # IPC names. Keep the probe independent of a deeply nested checkout.
            operation = 'make_socket_directory'
            socket_directory = Path(tempfile.mkdtemp(prefix='hey-my-buddy-ipc-'))
            socket_directory_fd = os.open(socket_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            address = str(socket_directory / 's')
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connector = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.settimeout(1)
                connector.settimeout(1)
                operation = 'bind'
                listener.bind(address)
                listener.listen(1)
                operation = 'connect'
                connector.connect(address)
                operation = 'accept'
                accepted, _ = listener.accept()
                accepted.close()
            finally:
                connector.close()
                listener.close()
    except OSError as error:
        code = 'LAUNCH_ACCESS_DENIED' if isinstance(error, PermissionError) else 'LAUNCH_IO_ERROR'
        raise ServiceError(code, str(error),
                           raw_os_error=error.errno, io_operation=operation) from error
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if socket_directory_fd is not None:
                try:
                    os.unlink('s', dir_fd=socket_directory_fd)
                except FileNotFoundError:
                    pass
                # A replacement path is never used to clean up the opened directory.
                opened = os.fstat(socket_directory_fd)
                current = os.stat(socket_directory, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                    socket_directory.rmdir()
        except OSError:
            pass
        finally:
            if socket_directory_fd is not None:
                os.close(socket_directory_fd)


def _retire_stale_endpoint(directory: Path) -> None:
    """Called under the start lock; lifetime locks prove absence of a board owner.

    This makes no assertion about execution processes or stored PIDs. Their
    reconciliation and unconfirmed-stop rules remain the new daemon's job.
    """
    if not (directory / 'control.json').exists():
        return
    locks = []
    try:
        for name in LIFETIME_LOCKS:
            fd = private_dirs.open_regular_fd(directory / name, os.O_CREAT | os.O_RDWR, 0o600)
            locks.append(fd)
            try:
                locking.lock(fd, blocking=False)
            except BlockingIOError:
                raise ServiceError('SERVICE_UNAVAILABLE', owner_held_message()) from None
        (directory / 'control.json').unlink(missing_ok=True)
    finally:
        for fd in reversed(locks):
            os.close(fd)


def ensure_service(state_dir: str | Path | None = None, *, resource: str = "control") -> dict:
    """Attach to a healthy daemon or cold-start one, preserving read-only attach."""
    return _ensure_service(get_state_dir(state_dir), resource=resource)


def _ensure_service(directory: Path, *, resource: str = "control") -> dict:
    upgrading = (directory / "upgrade.json").exists()
    if upgrading:
        # An installer holds this existing lock until verification and recovery
        # finish. Do not probe writes or IPC while its journal is present.
        try:
            lock_fd = private_dirs.open_regular_fd(directory / "control-start.lock", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            _refuse_upgrade_cold_start(directory)
            raise
    else:
        endpoint = _attach_read_only(directory)
        if endpoint:
            return endpoint
        if resource == "wait":
            # A wait may never cold-start a service: waiting on nothing would be a lie.
            _refuse_without_service(directory)
        _cold_start_preflight(directory)
        lock_fd = private_dirs.open_regular_fd(directory / "control-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        locking.lock(lock_fd)
        _refuse_upgrade_cold_start(directory)
        endpoint = _attach_read_only(directory)
        if endpoint:
            return endpoint
        if resource == "wait":
            _refuse_without_service(directory, under_start_lock=True)
        if upgrading:
            _cold_start_preflight(directory)
        _retire_stale_endpoint(directory)
        from ..install import runtime

        # Cold start installs and runs from the content-addressed stable runtime, so
        # the default path stays independent of the source tree. Only an
        # explicit HMB_DEV_SOURCE=1 development/test run executes from the checkout,
        # and that reports stable=false.
        target = runtime.launch_target(log_path=directory / "runtime-install.log")
        from ..install.launcher import launch_defaults, service_environment, write_active_runtime
        # The daemon is a service process: it is built from the explicit allowlist,
        # not from the Host session that happened to start it.
        env = {**launch_defaults(directory), **service_environment()}
        env.update(HMB_STATE_DIR=str(directory), C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="")
        env["HMB_RUNTIME_IDENTITY"] = target["identity"]
        if target["pythonPath"]:
            env["PYTHONPATH"] = target["pythonPath"] + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
        else:
            env.pop("PYTHONPATH", None)
        if target["stable"]:
            env["HMB_RUNTIME"] = target["runtime"]["runtimeDir"]
        # The start lock is held: a record cleared here makes every startup error
        # that appears afterwards attributable to this spawn alone.
        (directory / "startup-error.json").unlink(missing_ok=True)
        log_fd = private_dirs.open_regular_fd(directory / "control.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            child = independent_service_popen(
                [target["python"], "-m", ENTRY_MODULES['hey_my_buddy']['daemon']],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
            )
            threading.Thread(target=child.wait, name="hey-my-buddy-daemon-reaper", daemon=True).start()
        finally:
            os.close(log_fd)
        # A first cold start may install a runtime before the daemon exists.
        deadline = time.monotonic() + (300 if target.get("installed") else rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS)
        while time.monotonic() < deadline:
            endpoint = _healthy(directory)
            if endpoint:
                if target["stable"]:
                    write_active_runtime(directory, Path(target["runtime"]["runtimeDir"]))
                return endpoint
            if child.poll() not in (None, 0):
                from ..install.launcher import read_private
                failure = read_private(directory / "startup-error.json") or {}
                if isinstance(failure.get("code"), str) and isinstance(failure.get("message"), str):
                    details = failure.get("details")
                    raise ServiceError(failure["code"], failure["message"], **(details if isinstance(details, dict) else {}))
                raise ServiceError("SERVICE_START_FAILED", "The board daemon failed to start; inspect control.log")
            time.sleep(0.05)
        raise ServiceError("SERVICE_START_TIMEOUT", "The board daemon did not become ready; inspect control.log")
    finally:
        locking.unlock(lock_fd)
        os.close(lock_fd)


def _refuse_upgrade_cold_start(directory: Path) -> None:
    if (directory / "upgrade.json").exists():
        raise ServiceError(
            "UPGRADE_IN_PROGRESS",
            "Installation verification or recovery is in progress; rerun install to recover an interrupted installation, then retry the same command and identity",
        )


def call_service(method: str, params: dict | None = None, state_dir: str | Path | None = None) -> dict:
    """CLI-facing call: maps one method name onto one named C-Two operation."""
    directory = get_state_dir(state_dir)
    return _call_service(method, params, directory)


def _call_service(method: str, params: dict | None, directory: Path) -> dict:
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
        endpoint = _attach_read_only(directory)
        if endpoint is None:
            # A connection failure is not stop evidence: only both lifetime locks
            # proving the owner absent may report the service as stopped.
            refusal = _owner_refusal(directory)
            if refusal:
                raise ServiceError("SERVICE_UNAVAILABLE", refusal)
            return {"status": "stopped", "alreadyStopped": True, "stopped": True}
    else:
        endpoint = _ensure_service(directory, resource=resource)
    reply = _request(endpoint, operation, params, resource=resource, state_dir=directory)
    if method in UNWRAP_TASK:
        task = reply.get("task")
        if not isinstance(task, dict):
            raise ServiceError("INVALID_RESPONSE", "The service did not return a task")
        return {**task, **{key: reply[key] for key in ("duplicate", "alreadyTerminal", "retry", "waitingForHost") if key in reply}}
    if method in DIRECT_TASK_VIEW:
        if not isinstance(reply.get("runId"), str):
            raise ServiceError("INVALID_RESPONSE", "The wait route did not return a task view")
        return reply
    return reply


def request_stop(state_dir: str | Path | None = None, *, action: str = "stop", drain_seconds: int = 10) -> dict:
    """Ask the daemon to stop or restart through its own endpoint (never a signal)."""
    directory = get_state_dir(state_dir)
    return _call_service(action, {"drainSeconds": drain_seconds}, directory)
