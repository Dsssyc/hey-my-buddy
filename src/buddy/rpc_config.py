"""Buddy's private C-Two IPC profile, applied through the public Python setup API.

``configure_server()`` and ``configure_client()`` must run in each process before its
first ``c_two.register()`` or ``c_two.connect()``. They call the released C-Two
``set_server``/``set_client`` overrides and nothing else, so the tuning stays inside
the Buddy process: it is not written to the environment, not exported to a coding
child, and not applied to any other C-Two project on the machine.

Measured facts behind the profile (installed ``c-two==0.5.1``):

- ``pool_enabled=False`` is **not** an off switch. A server started that way still
  maps a shared-memory region (measured: one 256 MiB ``SM=SHM`` segment, the default
  ``pool_segment_size``), because the pooled client and server initialize the buddy
  pool regardless. Buddy therefore never offers a fake "off" mode and keeps the pool
  enabled while bounding it.
- The pool is bounded to two 16 MiB segments instead of the default four 256 MiB
  segments. The legal C-Two DTO here is a JSON string of at most 8 MiB
  (``buddy.transport.MAX_MESSAGE_BYTES``); its pickle envelope adds tens of bytes, so
  one 16 MiB segment holds a maximum legal request and the second segment keeps
  concurrent transfers from serializing on a single allocation.
- Reassembly is configured separately and explicitly: a 16 MiB reassembly segment,
  at most two of them, and a 16 MiB reassembled-payload ceiling. Payloads switch to
  chunked transfer at 0.9 x segment size, so a maximum legal 8 MiB message never
  chunks; the reassembly bound only covers a larger, over-limit transfer that would
  otherwise fall back to the C-Two defaults (64 MiB x 4).
- ``max_execution_workers`` is the server's callback capacity. The C-Two default of
  10 is below Buddy's default wait admission (``BUDDY_WAIT_CAPACITY=32``); with the
  default, thirty-two admitted waits delayed the next ordinary control call by 7.4 s
  in a measured run. The profile raises it to the C-Two maximum of 64 so waits and
  control operations (renew, result, cancel, health) do not starve each other.

``report()`` publishes only these whitelisted overrides and the bounds they imply.
A configured capacity is a ceiling: mapped shared memory and resident RSS are
different, separately measured quantities and are never derived from it here.
"""
from __future__ import annotations

import threading

import c_two as cc

#: Identifier of the applied profile, echoed in every report so a running process
#: can be compared against the source it claims to run.
PROFILE_ID = "buddy-lightweight-1"
MAX_WAIT_CAPACITY = 48

_MIB = 1024 * 1024

#: Pool and reassembly overrides accepted by both ``set_server`` and ``set_client``.
POOL_OVERRIDES: dict[str, object] = {
    "pool_enabled": True,
    "pool_segment_size": 16 * _MIB,
    "max_pool_segments": 2,
    "reassembly_segment_size": 16 * _MIB,
    "reassembly_max_segments": 2,
    "max_reassembly_bytes": 16 * _MIB,
    "max_total_chunks": 256,
}

#: Server-only capacity overrides. ``max_execution_workers`` is capped at 64 by the
#: released resolver; ``max_payload_size`` must not be smaller than a pool segment.
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
    segment = int(overrides["pool_segment_size"])
    pool_segments = int(overrides["max_pool_segments"])
    reassembly_segment = int(overrides["reassembly_segment_size"])
    reassembly_segments = int(overrides["reassembly_max_segments"])
    return {
        "poolSegmentBytes": segment,
        "maxPoolSegments": pool_segments,
        "poolCapacityBytes": segment * pool_segments,
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


def _apply(role: str) -> dict:
    with _LOCK:
        if role not in _configured:
            overrides = _role_overrides(role)
            if role == "server":
                cc.set_server(ipc_overrides=overrides)
            else:
                cc.set_client(ipc_overrides=overrides)
            _reports[role] = _build_report(role)
            _configured.add(role)
        return _reports[role]


def configure_server() -> dict:
    """Apply the server profile before the first ``register``. Idempotent per process."""
    if "server" in _configured:
        return _reports["server"]
    return _apply("server")


def configure_client() -> dict:
    """Apply the client profile before the first ``connect``. Idempotent per process."""
    if "client" in _configured:
        return _reports["client"]
    return _apply("client")


def configured_roles() -> tuple[str, ...]:
    """The roles this process has already applied."""
    with _LOCK:
        return tuple(sorted(_configured))
