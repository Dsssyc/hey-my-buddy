"""Private C-Two endpoint and IPC profile.

Apply before the first local register/connect. Unix roles use <state>/ipc,
independent of inherited C2_IPC_ROOT; Windows retains the native Named Pipe domain.
Each role gets its own (memory, file-spill, outstanding-calls) budget, which C-Two
allocates lazily at the role's first I/O and freezes there: a configured budget is
a ceiling C-Two accounts for, never a resident-memory claim.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import threading

from .. import private_dirs
from ..errors import BoardError

import c_two as cc

#: Identifier of the applied profile, echoed in every report so a running process
#: can be compared against the source it claims to run.
PROFILE_ID = "hey-my-buddy-lightweight-2"
#: Shared by the launcher readiness wait and every native RPC server startup.
SERVICE_STARTUP_TIMEOUT_SECONDS = 20

_MIB = 1024 * 1024

#: In-memory transfer budget each role may account against before spilling.
MEMORY_BUDGET_BYTES = 64 * _MIB
#: File-spill budget for transfers that outgrow the in-memory budget.
FILE_SPILL_BUDGET_BYTES = 64 * _MIB
#: Calls one role may keep outstanding; the pool itself stays lazily allocated.
MAX_OUTSTANDING_CALLS = 256
#: Server-only payload ceiling, verified against 8-32 MiB transfers.
MAX_PAYLOAD_BYTES = 32 * _MIB
#: Server-only execution workers, the released maximum.
MAX_EXECUTION_WORKERS = 64

#: Role-domain budget keywords accepted by ``set_client``.
CLIENT_BUDGETS: dict[str, int] = {
    "memory_budget_bytes": MEMORY_BUDGET_BYTES,
    "file_spill_budget_bytes": FILE_SPILL_BUDGET_BYTES,
    "max_outstanding_calls": MAX_OUTSTANDING_CALLS,
}

#: Budget keywords accepted by ``set_server``, adding its payload and worker ceilings.
SERVER_BUDGETS: dict[str, int] = {
    **CLIENT_BUDGETS,
    "max_payload_size": MAX_PAYLOAD_BYTES,
    "max_execution_workers": MAX_EXECUTION_WORKERS,
}

#: The budget keys this module is allowed to publish, by role.
WHITELISTED_KEYS: dict[str, tuple[str, ...]] = {
    "server": tuple(SERVER_BUDGETS),
    "client": tuple(CLIENT_BUDGETS),
}

_LOCK = threading.Lock()
_configured: set[str] = set()
_reports: dict[str, dict] = {}


def _role_budgets(role: str) -> dict[str, int]:
    if role == "server":
        return dict(SERVER_BUDGETS)
    if role == "client":
        return dict(CLIENT_BUDGETS)
    raise ValueError(f"unknown rpc role {role!r}")


def _build_report(role: str) -> dict:
    return {
        "profile": PROFILE_ID,
        "role": role,
        "budgets": _role_budgets(role),
    }


def report(role: str = "server") -> dict:
    """The applied profile as its whitelisted role budgets.

    A budget is a ceiling C-Two enforces when it lazily allocates the role's pool:
    no resident or mapped memory number is claimed or inferred here. The returned
    mapping is cached and must be treated as read-only.
    """
    if role not in ("server", "client"):
        raise ValueError(f"unknown rpc role {role!r}")
    if role in _reports:
        return _reports[role]
    with _LOCK:
        return _reports.setdefault(role, _build_report(role))


def _validate_path(path: Path, *, boundary: Path | None = None) -> bool:
    """Keep structural path guards; C-Two owns endpoint access checks."""
    if '..' in path.parts:
        raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path contains a linked or unsafe component", path=str(path))
    # The chosen state root may have user-owned linked ancestors. Inspect only
    # entries in the private tree, before following any of those entries.
    first = boundary if boundary is not None else path
    if private_dirs.linked(first):
        raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path contains a linked or unsafe component", path=str(first))
    current = first
    for part in path.relative_to(first).parts:
        current /= part
        if private_dirs.linked(current):
            raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path contains a linked or unsafe component", path=str(current))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path is not a directory", path=str(path))
    return True


def configure_local_endpoint(state_dir: Path, *, create: bool = True) -> Path | None:
    """Select explicit state/ipc; native C-Two validates access on first I/O.

    ``create=False`` never creates or chmods directories. Completed native shutdown
    allows a new selection. Windows retains its native Named Pipe domain.
    """
    state = state_dir
    if os.name == "nt":
        # Windows has a user-scoped Named Pipe domain, never a Unix root.
        # Core rejects even an inherited root override before selecting it.
        os.environ.pop("C2_IPC_ROOT", None)
        cc.endpoint.set_local_endpoint()
        return None
    root = state / "ipc"
    for directory in (state, root):
        if not _validate_path(directory, boundary=state):
            if not create:
                raise BoardError("PRIVATE_PATH_UNSAFE", "Private IPC directory is missing", path=str(directory))
            try:
                directory.mkdir(mode=0o700, parents=True)
            except FileExistsError:
                pass
        _validate_path(directory, boundary=state)
    # Core owns both the active-domain fence and endpoint access validation.
    try:
        cc.endpoint.set_local_endpoint(root=str(root))
    except (ValueError, RuntimeError) as error:
        raise BoardError("PRIVATE_PATH_UNSAFE", str(error), path=str(root)) from error
    return root


def _apply(role: str, state_dir: Path, *, create: bool) -> dict:
    with _LOCK:
        configure_local_endpoint(state_dir, create=create)
        # C-Two freezes a role's budgets at its first I/O and unfreezes only after
        # complete shutdown. Reapply then; never ask C-Two to retune an active role.
        stats = cc.observe.memory_stats()[role]
        if stats["frozen"] and role not in _configured:
            raise BoardError("RPC_CONFIG_TOO_LATE", "Private RPC profile must precede local I/O", role=role)
        if not stats["frozen"]:
            budgets = _role_budgets(role)
            if role == "server":
                cc.set_server(startup_timeout=SERVICE_STARTUP_TIMEOUT_SECONDS, **budgets)
            else:
                cc.set_client(**budgets)
        _reports.setdefault(role, _build_report(role))
        _configured.add(role)
        return _reports[role]


def configure_server(state_dir: Path, *, create: bool = True) -> dict:
    """Apply the server profile and private domain before the first register."""
    return _apply("server", state_dir, create=create)


def configure_client(state_dir: Path, *, create: bool = True) -> dict:
    """Apply the client profile and private domain before the first connect."""
    return _apply("client", state_dir, create=create)


def configured_roles() -> tuple[str, ...]:
    """The roles this process has applied."""
    with _LOCK:
        return tuple(sorted(_configured))
