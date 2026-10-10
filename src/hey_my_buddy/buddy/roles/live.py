"""Live access through the controller held by this Worker process."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import c_two as cc

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...protocol import rpc_config
from ...protocol.contracts import HarnessRunLive
from ..harnesses.c_two_live import (
    CTwoLiveChannel, ConfirmedProcessGone, CleanupOutcome,
    LiveEndpointDescriptor, cleanup_owned_endpoint,
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
    if descriptor.endpoint_credential is None:
        if os.name != "nt":
            return None
    else:
        try:
            credential = cc.EndpointCredential.from_json(descriptor.endpoint_credential)
            # Current native context was selected by this holding Worker's
            # explicit state configuration, independently of readiness files.
            trusted = cc.local_endpoint_context()
        except (ValueError, TypeError):
            return None
        if credential.address != descriptor.address:
            return None
        if (credential.context.platform, credential.context.namespace_id, credential.context.root) != \
                (trusted.platform, trusted.namespace_id, trusted.root):
            return None
    return descriptor


def handle_live_binding(handle, *, state_dir: str | Path | None = None):
    """Retry readiness against the held identity; never adopt a PID or file's identity."""
    control = getattr(handle, "role_run_control", None)
    expected = getattr(handle, "role_run_identity", None)
    if not isinstance(control, dict) or not isinstance(expected, RunIdentity):
        return LIVE_UNEXTRACTED, None
    from ..harnesses.registry import live_binding
    binding = live_binding(control.get("harness"))
    if binding is None:
        return LIVE_UNEXTRACTED, None
    state_dir = rpc_config.resolve_state_dir(state_dir)
    descriptor = _ready_descriptor(handle)
    if descriptor is None:
        return LIVE_UNAVAILABLE, None
    cached = getattr(handle, "role_live_channel", None)
    if cached is not None and (cached.identity != expected or
                               getattr(handle, "role_live_descriptor", None) != descriptor):
        return LIVE_UNAVAILABLE, None
    if cached is None:
        cached = binding(expected, HarnessRunLive, name=descriptor.name, address=descriptor.address,
                         instance_id=descriptor.instance_id, token=control["live"]["token"],
                         state_dir=state_dir)
        handle.role_live_channel = cached
        handle.role_live_descriptor = descriptor
    return LIVE_BOUND, cached


def release_live_binding(handle):
    """Release local access and clean only this reaped controller's exact endpoint."""
    cached = getattr(handle, "role_live_channel", None)
    if cached is not None:
        cached.close(reason="owned-controller-ended")
    # Revalidate the whole held request on release as well. Cached access is
    # never a substitute for the attempt, instance, PID and domain bindings.
    descriptor = _ready_descriptor(handle)
    cached_descriptor = getattr(handle, "role_live_descriptor", None)
    if descriptor is None or (cached_descriptor is not None and cached_descriptor != descriptor):
        return CleanupOutcome(outcome="unverified", reason="endpoint-binding-unverified")
    previous = getattr(handle, "role_endpoint_cleanup", None)
    if previous is not None:
        return previous
    # This is the Popen the holder owns. poll reaps the leader, and the
    # separate owned-group observation must positively confirm disappearance.
    exit_code = handle.process.poll()
    if handle.process.pid != handle.pid:
        return CleanupOutcome(outcome="unverified", reason="process-identity-mismatch")
    evidence = ConfirmedProcessGone(pid=handle.pid, exit_code=exit_code,
                                    group_gone=handle.shutdown_confirmed() is True)
    if evidence.exit_code is not None and evidence.group_gone:
        # Fence even an exceptional native invocation against a later retry.
        handle.role_endpoint_cleanup = CleanupOutcome(outcome="unverified", reason="endpoint-reap-unconfirmed")
    result = cleanup_owned_endpoint(descriptor, evidence, context=cc.local_endpoint_context())
    if evidence.exit_code is not None and evidence.group_gone:
        # No retry/sweep for busy, stale-target or any unverifiable native fact.
        handle.role_endpoint_cleanup = result
    return result
