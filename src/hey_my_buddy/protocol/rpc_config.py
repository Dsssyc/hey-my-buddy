"""Private C-Two 0.7.4 endpoint and IPC profile.

Apply before the first local register/connect. Unix roles use <state>/ipc,
independent of inherited C2_IPC_ROOT; Windows retains the native Named Pipe domain.
The buddy pool is disabled. Temporary shared-memory transfers remain possible:
configuration and C-Two memory accounting are not process RSS.
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
PROFILE_ID = "buddy-lightweight-1"
MAX_WAIT_CAPACITY = 48

_MIB = 1024 * 1024

#: Non-pool and reassembly overrides accepted by both ``set_server`` and ``set_client``.
POOL_OVERRIDES: dict[str, object] = {
    "pool_enabled": False,
    "reassembly_segment_size": 16 * _MIB,
    "reassembly_max_segments": 2,
    "max_reassembly_bytes": 16 * _MIB,
    "max_total_chunks": 256,
}

#: Server-only capacity overrides. ``max_execution_workers`` is capped at 64 by the
#: released resolver. Message and frame ceilings remain independent of the pool.
SERVER_OVERRIDES: dict[str, object] = {
    **POOL_OVERRIDES,
    "max_execution_workers": 64,
    "max_pending_requests": 256,
    "max_payload_size": 32 * _MIB,
    "max_frame_size": 16 * _MIB,
}

#: The override keys this module is allowed to publish, by role.
WHITELISTED_KEYS: dict[str, tuple[str, ...]] = {
    "server": tuple(SERVER_OVERRIDES),
    "client": tuple(POOL_OVERRIDES),
}

_LOCK = threading.Lock()
_configured: set[str] = set()
_reports: dict[str, dict] = {}


def _role_overrides(role: str) -> dict[str, object]:
    if role == "server":
        return dict(SERVER_OVERRIDES)
    if role == "client":
        return dict(POOL_OVERRIDES)
    raise ValueError(f"unknown rpc role {role!r}")


def _capacity(overrides: dict[str, object]) -> dict[str, int]:
    reassembly_segment = int(overrides["reassembly_segment_size"])
    reassembly_segments = int(overrides["reassembly_max_segments"])
    return {
        "reassemblySegmentBytes": reassembly_segment,
        "maxReassemblySegments": reassembly_segments,
        "reassemblyCapacityBytes": reassembly_segment * reassembly_segments,
        "maxReassemblyBytes": int(overrides["max_reassembly_bytes"]),
    }


def _build_report(role: str) -> dict:
    overrides = _role_overrides(role)
    return {
        "profile": PROFILE_ID,
        "role": role,
        "overrides": overrides,
        "capacity": _capacity(overrides),
        "sharedMemoryDisabled": False,
        "note": (
            "Configured capacities are upper bounds, not resident memory; mapped shared memory and process RSS "
            "are separate measurements."
        ),
    }


def report(role: str = "server") -> dict:
    """The applied profile as whitelisted overrides plus the bounds they imply.

    Configured capacity is *not* resident memory: no mapped or RSS number is claimed
    or inferred here. The returned mapping is cached and must be treated as read-only.
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
        cc.set_local_endpoint()
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
        cc.set_local_endpoint(root=str(root))
    except (ValueError, RuntimeError) as error:
        raise BoardError("PRIVATE_PATH_UNSAFE", str(error), path=str(root)) from error
    return root


def _apply(role: str, state_dir: Path, *, create: bool) -> dict:
    with _LOCK:
        configure_local_endpoint(state_dir, create=create)
        # The public scope is absent before the role's first I/O and after complete
        # shutdown. Reapply then; never ask C-Two to retune an active role.
        scope = "server" if role == "server" else "runtime_outgoing"
        active = cc.memory_stats()[scope] is not None
        if active and role not in _configured:
            raise BoardError("RPC_CONFIG_TOO_LATE", "Private RPC profile must precede local I/O", role=role)
        if not active:
            overrides = _role_overrides(role)
            if role == "server":
                cc.set_server(ipc_overrides=overrides)
            else:
                cc.set_client(ipc_overrides=overrides)
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
