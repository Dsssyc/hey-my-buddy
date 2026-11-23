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
import re
import tempfile
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable, NoReturn

import c_two as cc
from c_two.error import CCError, CoreError

from . import rpc_config
from .service_process import independent_service_popen
from .contracts import CONTROL_NAME, CONTRACT_VERSION, BuddyControl
from ..errors import BoardError
from ..state_root import check_state_root, get_state_dir
from ..install.entrypoints import ENTRY_MODULES

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
PROTOCOL_VERSION = 2
WAIT_TICK_SECONDS = 0.25
SUBSCRIPTION_CLOSE_TIMEOUT_SECONDS = 2.0

#: The two locks a board owner holds from admission until its process exits.
LIFETIME_LOCKS = ("control-daemon.lock", "board-owner.lock")


def owner_held_message() -> str:
    """The one owner-busy explanation shared by every lifetime-lock refusal."""
    return "A board owner still holds this state directory's lifetime locks."

#: Retained top-level CLI names only. ``await`` is implemented locally over the
#: separate internal operation registry below. A service operation never becomes
#: callable from the CLI merely because ``await`` or a role client uses it.
METHOD_MAP: dict[str, str] = {
    "health": "health",
    "harnesses": "capabilities",
    "harness-set": "harness_set",
    "quota-redetect": "quota_redetect",
    "runtime": "runtime_info",
    "storage-plan": "storage_plan",
    "storage-apply": "storage_apply",
    "relocate": "project_relocate",
    "worker-sessions": "worker_sessions",
    # -- Host lifecycle -------------------------------------------------
    "create": "workflow_create",
    "amend": "workflow_amend",
    "submit": "workflow_submit",
    "get": "workflow_get",
    "continue": "workflow_continue",
    "cancel": "workflow_cancel",
    "accept": "workflow_accept",
    "conclude": "workflow_conclude",
    "inquire": "inquiry_observe",
    "console": "console",
    # -- User operations ------------------------------------------------
    "stop": "service_control",
    "restart": "service_control",
    # -- Credential-scoped role commands --------------------------------
    "suggest": "workflow_suggest",
    "list": "task_list",
}

#: Private calls used by await, installer, console, and credential-scoped role
#: clients. These names are absent from the CLI registry and from Host help.
INTERNAL_METHOD_MAP: dict[str, str] = {
    "ping": "ping",
    "capabilities": "capabilities",
    "accounts": "accounts",
    "workflow_get": "workflow_get",
    "task_get": "task_get",
    "task_result": "task_result",
    "task_observe": "task_observe",
    "events_read": "events_read",
    "artifact_list": "artifact_list",
    "message_get": "message_get",
    "message_list": "message_list",
    "macro_task_list": "macro_task_list",
    "macro_task_timeline": "macro_task_timeline",
    "task_cancel": "task_cancel",
    "selection_get": "selection_get",
    "selection_list": "selection_list",
    "model_catalog_refresh": "model_catalog_refresh",
    "buddy_list": "buddy_list",
    "user_policy_publish": "user_policy_publish",
}

UNWRAP_TASK = frozenset({"task_get"})
DIRECT_TASK_VIEW = frozenset({"task_observe"})
READ_ONLY_OPERATIONS = frozenset({"task_observe"})


class ServiceError(BoardError):
    """A board failure raised on the client side of the boundary."""


def local_rpc_error(error: CoreError | CCError) -> ServiceError:
    """Keep C-Two's local I/O cause visible at the Host boundary."""
    details = {}
    cause = error.__cause__
    for diagnostic in (cause, error) if isinstance(cause, CoreError) else (error,):
        details.update(diagnostic.details)
        details.update({key: value for key in ('category', 'transport_kind', 'transport_phase',
                       'io_kind', 'raw_os_error', 'io_operation', 'endpoint_unverified_reason',
                       'local_protocol_version', 'peer_protocol_version')
                       if (value := getattr(diagnostic, key, None)) is not None})
    kind = details.get('io_kind')
    if kind == 'permission_denied':
        code = 'LAUNCH_ACCESS_DENIED'
    elif kind in ('not_found', 'connection_refused') and details.get('transport_phase') != 'dispatch_uncertain':
        code = 'SERVICE_UNAVAILABLE'
    elif isinstance(error, CCError):
        # ErrorCode members are PascalCase (ResourceClosed, ClientCallingResource);
        # the Host boundary keeps the C_TWO_<UPPER_SNAKE> spelling.
        code = 'C_TWO_' + re.sub(r'(?<!^)(?=[A-Z])', '_', error.code.name).upper()
    else:
        code = 'C_TWO_' + error.category.upper()
    return ServiceError(code, str(error), **details)


def is_transient_ipc_error(error: BoardError, *, pre_dispatch_only: bool = False) -> bool:
    """Recognize an unavailable service, withdrawn route, capacity refusal or transient IPC failure."""
    details = error.details
    if pre_dispatch_only and details.get("transport_phase") == "dispatch_uncertain":
        return False
    if error.code in ("SERVICE_UNAVAILABLE", "C_TWO_RESOURCE_CLOSED", "C_TWO_RESOURCE_REMOVED",
                      "C_TWO_RESOURCE_UNAVAILABLE", "C_TWO_RESOURCE_NOT_FOUND",
                      "C_TWO_CALL_CAPACITY_EXCEEDED"):
        return True
    phases = ("pre_dispatch",) if pre_dispatch_only else ("pre_dispatch", "dispatch_uncertain")
    return (error.code in ("C_TWO_CLIENT_CALLING_RESOURCE", "C_TWO_TRANSPORT")
            and details.get("category") == "transport"
            and details.get("transport_kind") == "ipc"
            and details.get("io_kind") not in ("other", "address_in_use", "permission_denied")
            and details.get("transport_phase") in phases)


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
    if operation in {'capabilities', 'buddy_list', 'model_catalog_refresh', 'workflow_submit'}:
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
def _connection(endpoint: dict, directory: Path, *, timeout: float | None = None):
    # Validate the paths and select the private domain before opening a connection.
    rpc_config.configure_client(directory, create=False)
    connect_kwargs: dict[str, Any] = {"name": CONTROL_NAME, "address": endpoint["address"]}
    if timeout is not None:
        connect_kwargs["timeout"] = max(0.05, min(timeout, rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS))
    with ExitStack() as stack:
        try:
            service = stack.enter_context(cc.connect(BuddyControl, **connect_kwargs))
        except (CoreError, CCError) as exc:
            raise local_rpc_error(exc) from exc
        yield service


def _request(endpoint: dict, operation: str, params: dict, *, state_dir: Path) -> dict:
    with _connection(endpoint, state_dir) as service:
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


class BoardWaitSession:
    """One read-only BuddyControl connection multiplexing topic wakes and RPC reads.

    A thin receiver thread drains ``board_changed`` frames from Rust's GIL-free
    ``block_on`` iterator into a local :class:`threading.Event`, so the calling
    thread waits with bounded Python signal checks (``Ctrl+C``) and exact local
    deadlines across POSIX and Windows.
    """

    def __init__(self, directory: Path, *, timeout: float | None = None):
        self.directory = directory
        self._connect_timeout = timeout
        self._stack: ExitStack | None = None
        self._endpoint: dict | None = None
        self._peer: Any = None
        self._sub: Any = None
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._error: ServiceError | None = None
        self._closed = False

    def __enter__(self) -> BoardWaitSession:
        try:
            endpoint = _attach_read_only(self.directory)
        except ServiceError as error:
            if error.code == "C_TWO_CALL_CAPACITY_EXCEEDED" or not is_transient_ipc_error(error):
                raise
            endpoint = None
        if endpoint is None:
            _refuse_without_service(self.directory)
        self._endpoint = endpoint
        stack = ExitStack()
        try:
            self._peer = stack.enter_context(_connection(endpoint, self.directory, timeout=self._connect_timeout))
        except ServiceError as error:
            stack.close()
            if error.code == "SERVICE_UNAVAILABLE":
                _refuse_without_service(self.directory)
            raise
        self._stack = stack
        return self

    def call(self, operation: str, params: dict) -> dict:
        assert self._peer is not None and self._endpoint is not None
        try:
            return _invoke(self._peer, self._endpoint, operation, params)
        except ServiceError as error:
            if error.code == "SERVICE_UNAVAILABLE":
                _refuse_without_service(self.directory)
            raise

    def subscribe(self) -> None:
        if self._sub is not None:
            return
        assert self._peer is not None
        try:
            sub = self._peer.board_changed(start="next", on_lag="skip")
        except (CoreError, CCError) as exc:
            error = local_rpc_error(exc)
            if error.code == "SERVICE_UNAVAILABLE":
                _refuse_without_service(self.directory)
            raise error from exc
        self._sub = sub
        self._wake.clear()
        self._error = None

        def _drain() -> None:
            try:
                for _delivery in sub:
                    self._wake.set()
                if not self._closed:
                    self._error = ServiceError("C_TWO_CALL_CANCELLED", "Board change subscription closed")
                    self._wake.set()
            except (CoreError, CCError) as exc:
                if not self._closed:
                    self._error = local_rpc_error(exc)
                    self._wake.set()
            except Exception as exc:  # noqa: BLE001 - surface unexpected transport error to waiter
                if not self._closed:
                    self._error = ServiceError("SERVICE_UNAVAILABLE", str(exc))
                    self._wake.set()

        thread = threading.Thread(target=_drain, name="hey-my-buddy-topic-wait", daemon=True)
        self._thread = thread
        thread.start()

    def prepare_read(self) -> None:
        """Clear pending wakeups *before* an authoritative read so no concurrent change is lost."""
        self._wake.clear()

    def wait_wake(self, timeout: float, *, clock: Callable[[], float] = time.monotonic) -> bool:
        """Block interruptibly until a board_changed delivery, transport error or deadline."""
        deadline = clock() + max(0.0, timeout)
        while True:
            if self._error is not None:
                raise self._error
            remaining = deadline - clock()
            if remaining <= 0:
                return False
            if self._wake.wait(min(WAIT_TICK_SECONDS, remaining)):
                if self._error is not None:
                    raise self._error
                return True

    def close(self) -> None:
        self._closed = True
        sub, self._sub = self._sub, None
        thread, self._thread = self._thread, None
        if sub is not None:
            try:
                sub.cancel()
            except (CoreError, CCError):
                pass
            try:
                sub.close(timeout=SUBSCRIPTION_CLOSE_TIMEOUT_SECONDS)
            except (CoreError, CCError):
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=SUBSCRIPTION_CLOSE_TIMEOUT_SECONDS)
        stack, self._stack = self._stack, None
        self._peer = None
        if stack is not None:
            stack.close()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _open_wait_session(directory: Path, *, timeout: float | None = None) -> BoardWaitSession:
    """Open one read-only BuddyControl session using an already selected state root."""
    return BoardWaitSession(directory, timeout=timeout)


def open_wait_session(directory: str | Path | None = None, *, timeout: float | None = None) -> BoardWaitSession:
    """Open one read-only multiplexed BuddyControl session for blocking waits."""
    return _open_wait_session(get_state_dir(directory), timeout=timeout)


def wait_events(params: dict, state_dir: str | Path | None = None, *,
                clock: Callable[[], float] = time.monotonic) -> dict:
    """Wait on committed board events over one read-only topic subscription."""
    directory = get_state_dir(state_dir)
    after = params.get("after", 0)
    limit = params.get("limit", 100)
    timeout_ms = params.get("timeoutMs", 30000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 0:
        raise ServiceError("INVALID_ARGUMENT", "timeoutMs must be a nonnegative integer")
    read_params: dict[str, Any] = {"after": after, "limit": limit}
    for key in ("runId", "credential"):
        if key in params and params[key] is not None:
            read_params[key] = params[key]
    deadline = clock() + timeout_ms / 1000.0
    with _open_wait_session(directory, timeout=max(0.05, timeout_ms / 1000.0) if timeout_ms else None) as session:
        page = session.call("events_read", read_params)
        if page["truncated"] or page["events"] or page["head"] > page["cursor"]:
            return {**page, "timedOut": False}
        if timeout_ms == 0:
            return {**page, "timedOut": True}
        session.subscribe()
        while True:
            session.prepare_read()
            try:
                page = session.call("events_read", read_params)
            except ServiceError as error:
                if error.code == "C_TWO_CALL_CAPACITY_EXCEEDED":
                    remaining = deadline - clock()
                    if remaining > 0:
                        session.wait_wake(min(0.05, remaining), clock=clock)
                        continue
                raise
            if page["truncated"] or page["events"] or page["head"] > page["cursor"]:
                return {**page, "timedOut": False}
            remaining = deadline - clock()
            if remaining <= 0:
                return {**page, "timedOut": True}
            session.wait_wake(remaining, clock=clock)


def wait_task(params: dict, state_dir: str | Path | None = None, *,
              clock: Callable[[], float] = time.monotonic) -> dict:
    """Wait on one task's authoritative observation over one read-only topic subscription."""
    from .awaiting import message_kind

    directory = get_state_dir(state_dir)
    run_id = params.get("runId")
    if not isinstance(run_id, str) or not run_id:
        raise ServiceError("INVALID_ARGUMENT", "runId is required")
    timeout_ms = params.get("timeoutMs", 30000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 0:
        raise ServiceError("INVALID_ARGUMENT", "timeoutMs must be a nonnegative integer")
    after_revision = params.get("afterRevision")
    if after_revision is not None and (
        isinstance(after_revision, bool) or not isinstance(after_revision, int) or after_revision < 0
    ):
        raise ServiceError("INVALID_ARGUMENT", "afterRevision must be a nonnegative integer")
    consume_reminders = bool(params.get("consumeReminders", False))
    reminder_wait_id = params.get("reminderWaitId")
    if consume_reminders:
        if not isinstance(reminder_wait_id, str) or not reminder_wait_id:
            raise ServiceError("INVALID_ARGUMENT", "reminderWaitId is required when consumeReminders is true")
    elif reminder_wait_id is not None:
        raise ServiceError("INVALID_ARGUMENT", "reminderWaitId requires consumeReminders")
    observe_params: dict[str, Any] = {"runId": run_id}
    if consume_reminders:
        observe_params["reminderWaitId"] = reminder_wait_id
    if params.get("credential") is not None:
        observe_params["credential"] = params["credential"]

    def _ready(view: dict) -> bool:
        return (
            message_kind(view) is not None
            or (after_revision is not None and view["revision"] > after_revision)
        )

    deadline = clock() + timeout_ms / 1000.0
    with _open_wait_session(directory, timeout=max(0.05, timeout_ms / 1000.0) if timeout_ms else None) as session:
        view = session.call("task_observe", observe_params)
        if _ready(view) or timeout_ms == 0:
            return view
        session.subscribe()
        while True:
            session.prepare_read()
            try:
                view = session.call("task_observe", observe_params)
            except ServiceError as error:
                if error.code == "C_TWO_CALL_CAPACITY_EXCEEDED":
                    remaining = deadline - clock()
                    if remaining > 0:
                        session.wait_wake(min(0.05, remaining), clock=clock)
                        continue
                raise
            if _ready(view):
                return view
            remaining = deadline - clock()
            if remaining <= 0:
                return view
            session.wait_wake(remaining, clock=clock)


def call_board(operation: str, params: dict | None = None, state_dir: str | Path | None = None, *, endpoint: dict | None = None) -> dict:
    """Call one named C-Two board operation through a healthy, trusted endpoint."""
    directory = get_state_dir(state_dir)
    return _call_board(operation, params, directory, endpoint=endpoint)


def _call_board(operation: str, params: dict | None, directory: Path, *, endpoint: dict | None = None) -> dict:
    endpoint = endpoint or _ensure_service(directory)
    return _request(endpoint, operation, params or {}, state_dir=directory)


def _call_board_read_only(operation: str, params: dict | None, directory: Path) -> dict:
    """Call through an existing service using the already selected state root."""
    try:
        endpoint = _attach_read_only(directory)
    except ServiceError as error:
        if error.code == "C_TWO_CALL_CAPACITY_EXCEEDED" or not is_transient_ipc_error(error):
            raise
        endpoint = None
    if endpoint is None:
        _refuse_without_service(directory)
    return _request(endpoint, operation, params or {}, state_dir=directory)


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


def _probe_local_ipc(directory: Path) -> None:
    """Use Core's real binding path without changing the caller's Runtime."""
    runtime = cc.Runtime()
    namespace = Path(tempfile.mkdtemp(prefix='.launch-ipc-', dir=directory))
    namespace_fd = os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    start_error = None
    try:
        try:
            runtime.set_local_endpoint(root=str(namespace))
            runtime.set_server(startup_timeout=rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS)
            runtime.start()
        except (CoreError, CCError) as error:
            start_error = error
        finally:
            outcome = runtime.shutdown(timeout=rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS)
            if outcome['completed'] is not True:
                raise ServiceError('LAUNCH_IO_ERROR', 'Local IPC probe shutdown is unconfirmed; temporary root preserved',
                                   io_operation='shutdown', path=str(namespace), shutdownOutcome=outcome)
            # This one-use namespace belongs to this process. Completed shutdown
            # permits retiring its files even when the receipt retains a start error.
            for name in os.listdir(namespace_fd):
                os.unlink(name, dir_fd=namespace_fd)
            opened = os.fstat(namespace_fd)
            current = namespace.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ServiceError('LAUNCH_IO_ERROR', 'Local IPC probe directory was replaced; path preserved',
                                   io_operation='cleanup', path=str(namespace))
            namespace.rmdir()
        if start_error is not None:
            failure = local_rpc_error(start_error)
            if (any(outcome[key] for key in ('runtime_barrier_error', 'route_close_error',
                                            'ipc_client_close_error', 'relay_errors'))
                    or any(route['close_error'] for route in outcome['route_outcomes'])):
                failure.details['shutdownOutcome'] = outcome
            raise failure from start_error
    finally:
        os.close(namespace_fd)


def _cold_start_preflight(directory: Path) -> None:
    """Refuse sandboxed writes or IPC before installing a runtime or spawning."""
    directory = check_state_root(directory, create=True)
    probe = directory / ('.launch-probe-' + uuid.uuid4().hex[:12])
    operation = 'configure_local_endpoint'
    try:
        rpc_config.configure_local_endpoint(directory)
        operation = 'state_write'
        fd = private_dirs.open_regular_fd(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        if os.name != 'nt':
            operation = 'bind'
            _probe_local_ipc(directory)
    except (CoreError, CCError) as error:
        raise local_rpc_error(error) from error
    except OSError as error:
        code = 'LAUNCH_ACCESS_DENIED' if isinstance(error, PermissionError) else 'LAUNCH_IO_ERROR'
        raise ServiceError(code, str(error), raw_os_error=error.errno, io_operation=operation) from error
    finally:
        probe.unlink(missing_ok=True)


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


def ensure_service(state_dir: str | Path | None = None) -> dict:
    """Attach to a healthy daemon or cold-start one, preserving read-only attach."""
    return _ensure_service(get_state_dir(state_dir))


def _ensure_service(directory: Path) -> dict:
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
        _cold_start_preflight(directory)
        lock_fd = private_dirs.open_regular_fd(directory / "control-start.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        locking.lock(lock_fd)
        _refuse_upgrade_cold_start(directory)
        endpoint = _attach_read_only(directory)
        if endpoint:
            return endpoint
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
    if not isinstance(method, str) or method not in METHOD_MAP and method not in INTERNAL_METHOD_MAP:
        raise ServiceError("INVALID_ARGUMENT", f"Unknown method {method!r}")
    if params is not None and not isinstance(params, dict):
        raise ServiceError("INVALID_ARGUMENT", "params must be an object")
    params = dict(params or {})
    operation = (METHOD_MAP.get(method) or INTERNAL_METHOD_MAP[method])
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
    elif operation in READ_ONLY_OPERATIONS:
        endpoint = _attach_read_only(directory)
        if endpoint is None:
            _refuse_without_service(directory)
    else:
        endpoint = _ensure_service(directory)
    reply = _request(endpoint, operation, params, state_dir=directory)
    if method in UNWRAP_TASK:
        task = reply.get("task")
        if not isinstance(task, dict):
            raise ServiceError("INVALID_RESPONSE", "The service did not return a task")
        return {**task, **{key: reply[key] for key in ("duplicate", "alreadyTerminal") if key in reply}}
    if method in DIRECT_TASK_VIEW:
        if not isinstance(reply.get("runId"), str):
            raise ServiceError("INVALID_RESPONSE", "The wait route did not return a task view")
        return reply
    return reply


def request_stop(state_dir: str | Path | None = None, *, action: str = "stop", drain_seconds: int = 10) -> dict:
    """Ask the daemon to stop or restart through its own endpoint (never a signal)."""
    directory = get_state_dir(state_dir)
    return _call_service(action, {"drainSeconds": drain_seconds}, directory)
