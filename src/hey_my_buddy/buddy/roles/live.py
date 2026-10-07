"""Live access through the controller held by this Worker process."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...protocol.contracts import HarnessRunLive
from ..harnesses.c_two_live import (
    C_TWO_IPC_DIRECTORY, CTwoLiveChannel, ConfirmedProcessGone,
    LiveEndpointDescriptor, cleanup_abandoned_socket,
)
from ..harnesses.run_contract import MAX_RUN_REQUEST_BYTES, RunIdentity, decode_run_request
from .turn_io import guard_private_path

LIVE_BOUND = "bound"
LIVE_UNAVAILABLE = "unavailable"
LIVE_UNEXTRACTED = "unextracted"


def _private_regular_bytes(path: Path, maximum: int) -> bytes:
    # Reuse the retired sidecar reader's nonblocking, no-link, regular-file
    # barrier for the two actual readiness materials the holder still reads.
    path = guard_private_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise BoardError("INVALID_ARGUMENT", "Live readiness material is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise BoardError("INVALID_ARGUMENT", "Live readiness material exceeds its frame bound")
        return raw
    finally:
        os.close(descriptor)


def _ready_descriptor(handle) -> LiveEndpointDescriptor | None:
    control = getattr(handle, "role_run_control", None)
    expected = getattr(handle, "role_run_identity", None)
    if not isinstance(control, dict) or not isinstance(expected, RunIdentity):
        return None
    material = control.get("live")
    if not isinstance(material, dict):
        return None
    try:
        request = decode_run_request(_private_regular_bytes(Path(control["requestFile"]), MAX_RUN_REQUEST_BYTES))
        if request.identity != expected or request.harness != control["harness"]:
            return None
        descriptor = LiveEndpointDescriptor.from_payload(decode_strict_json(
            _private_regular_bytes(Path(material["readyFile"]), 16384)))
    except (OSError, ValueError, BoardError, RecursionError, KeyError, TypeError):
        return None
    if descriptor.instance_id != material.get("instanceId") or descriptor.host_pid != handle.pid:
        return None
    fact = descriptor.socket
    if fact is not None:
        if not descriptor.address.startswith("ipc://"):
            return None
        server_id = descriptor.address[len("ipc://"):]
        if not server_id or "/" in server_id or "\\" in server_id:
            return None
        if fact.address != descriptor.address or fact.path != str(Path(C_TWO_IPC_DIRECTORY) / (server_id + ".sock")):
            return None
    return descriptor


def handle_live_binding(handle):
    """Retry readiness against the held identity; never adopt a PID or file's identity."""
    control = getattr(handle, "role_run_control", None)
    expected = getattr(handle, "role_run_identity", None)
    if not isinstance(control, dict) or not isinstance(expected, RunIdentity):
        return LIVE_UNEXTRACTED, None
    from ..harnesses.registry import live_binding
    binding = live_binding(control.get("harness"))
    if binding is None:
        return LIVE_UNEXTRACTED, None
    descriptor = _ready_descriptor(handle)
    if descriptor is None:
        return LIVE_UNAVAILABLE, None
    cached = getattr(handle, "role_live_channel", None)
    if cached is None:
        cached = binding(expected, HarnessRunLive, name=descriptor.name, address=descriptor.address,
                         instance_id=descriptor.instance_id, token=control["live"]["token"])
        handle.role_live_channel = cached
        handle.role_live_descriptor = descriptor
    return LIVE_BOUND, cached


def release_live_binding(handle):
    """Release local access and clean only this reaped controller's exact endpoint."""
    cached = getattr(handle, "role_live_channel", None)
    if cached is not None:
        cached.close(reason="owned-controller-ended")
    descriptor = getattr(handle, "role_live_descriptor", None) or _ready_descriptor(handle)
    if descriptor is None:
        return None
    # ProcessHandle owns this Popen. poll() reaps its leader; its separate group
    # confirmation is necessary even when the leader has an exit code.
    exit_code = handle.process.poll()
    evidence = ConfirmedProcessGone(pid=handle.pid, exit_code=exit_code,
                                    group_gone=handle.shutdown_confirmed() is True)
    return cleanup_abandoned_socket(descriptor, evidence)
