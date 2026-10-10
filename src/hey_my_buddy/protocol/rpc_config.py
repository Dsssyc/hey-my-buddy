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

from .. import home, private_dirs
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


def _validate_path(path: Path, *, private: bool = False, ipc: bool = False) -> bool:
    """Reject unsafe existing entries; never repair their permissions."""
    if '..' in path.parts or private_dirs.linked_component(path) is not None:
        raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path contains a linked or unsafe component")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (os.geteuid(), 0)
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
            or (private and (info.st_uid != os.geteuid() or info.st_mode & 0o077))
            or (ipc and stat.S_IMODE(info.st_mode) != 0o700)):
        raise BoardError("PRIVATE_PATH_UNSAFE", "IPC path is not an owner-private directory", path=str(path))
    return True


def configure_local_endpoint(state_dir: str | Path | None = None, *, create: bool = True) -> Path | None:
    """Select the private domain before local I/O; native C-Two fences active roots.

    ``create=False`` requires both state and IPC directories to exist securely and
    never creates or chmods them. Completed native shutdown allows a new selection.
    """
    if os.name == "nt":
        cc.set_local_endpoint()
        return None
    state = private_dirs._absolute(Path(state_dir or os.environ.get("BUDDY_STATE_DIR") or home.default_state_dir()).expanduser())
    root = state / "ipc"
    for ancestor in reversed(state.parents):
        _validate_path(ancestor)
    for directory in (state, root):
        exists = _validate_path(directory, private=True, ipc=directory == root)
        if not exists:
            if not create:
                raise BoardError("PRIVATE_PATH_UNSAFE", "Private IPC directory is missing", path=str(directory))
            # linked_component supplies the shared ancestor guard. An entry that
            # races creation must pass the same strict check; never chmod it.
            try:
                directory.mkdir(mode=0o700, parents=True)
            except FileExistsError:
                pass
        _validate_path(directory, private=True, ipc=directory == root)
    # Do not cache the root in Python: Core owns the active-domain fence and the
    # fresh session after a fully completed shutdown.
    cc.set_local_endpoint(root=str(root))
    return root


def _apply(role: str, state_dir: str | Path | None, *, create: bool) -> dict:
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


def configure_server(state_dir: str | Path | None = None, *, create: bool = True) -> dict:
    """Apply the server profile and private domain before the first register."""
    return _apply("server", state_dir, create=create)


def configure_client(state_dir: str | Path | None = None, *, create: bool = True) -> dict:
    """Apply the client profile and private domain before the first connect."""
    return _apply("client", state_dir, create=create)


def configured_roles() -> tuple[str, ...]:
    """The roles this process has applied."""
    with _LOCK:
        return tuple(sorted(_configured))
