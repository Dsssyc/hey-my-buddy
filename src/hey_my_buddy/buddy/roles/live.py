"""Live access to the endpoint owned by one execution process."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import c_two as cc

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...protocol.contracts import HarnessRunLive
from ..harnesses.c_two_live import ConfirmedProcessGone, CleanupOutcome, LiveEndpointDescriptor, cleanup_owned_endpoint
from ..harnesses.run_contract import RunIdentity
from .turn_io import guard_private_path

LIVE_BOUND = "bound"
LIVE_UNAVAILABLE = "unavailable"
LIVE_UNEXTRACTED = "unextracted"


def _private_regular_bytes(path: Path, maximum: int) -> bytes:
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


def _material(handle):
    path = getattr(handle, "execution_live_file", None)
    if path is None:
        return None
    expected = getattr(handle, "role_run_identity", None)
    control = getattr(handle, "role_run_control", None)
    if not isinstance(expected, RunIdentity) or not isinstance(control, dict):
        return None
    try:
        value = decode_strict_json(_private_regular_bytes(Path(path), 32768))
        if not isinstance(value, dict) or set(value) != {"identity", "harness", "descriptor", "token"}:
            return None
        identity = RunIdentity.from_payload(value["identity"])
        descriptor = LiveEndpointDescriptor.from_payload(value["descriptor"])
        if ((identity.task_id, identity.attempt_id, identity.generation, identity.turn_id) !=
                (expected.task_id, expected.attempt_id, expected.generation, expected.turn_id)
                or value["harness"] != control.get("harness")
                or descriptor.host_pid != handle.pid
                or descriptor.instance_id != value["descriptor"]["instanceId"]):
            return None
        token = value["token"]
        if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
            return None
        if descriptor.endpoint_credential is None:
            if os.name != "nt":
                return None
        else:
            credential = cc.EndpointCredential.from_json(descriptor.endpoint_credential)
            trusted = cc.local_endpoint_context()
            if credential.address != descriptor.address:
                return None
            if (credential.context.platform, credential.context.namespace_id, credential.context.root) != \
                    (trusted.platform, trusted.namespace_id, trusted.root):
                return None
        return identity, descriptor, token
    except (OSError, ValueError, BoardError, RecursionError, KeyError, TypeError):
        return None


def handle_live_binding(handle, *, state_dir: Path):
    """Bind to the attempt's current endpoint under the held execution identity."""
    from ..harnesses.registry import live_binding
    control = getattr(handle, "role_run_control", None)
    if not isinstance(control, dict):
        return LIVE_UNEXTRACTED, None
    binding = live_binding(control.get("harness"))
    if binding is None:
        return LIVE_UNEXTRACTED, None
    material = _material(handle)
    if material is None:
        return LIVE_UNAVAILABLE, None
    identity, descriptor, token = material
    cached = getattr(handle, "role_live_channel", None)
    if cached is not None and (cached.identity != identity or
                               getattr(handle, "role_live_descriptor", None) != descriptor):
        cached.close(reason="role-endpoint-replaced")
        cached = None
    handle.role_run_identity = identity
    if cached is None:
        cached = binding(identity, HarnessRunLive, name=descriptor.name, address=descriptor.address,
                         instance_id=descriptor.instance_id, token=token, state_dir=state_dir)
        handle.role_live_channel = cached
        handle.role_live_descriptor = descriptor
    return LIVE_BOUND, cached


def release_live_binding(handle):
    """Release access and clean the exact endpoint after the execution was reaped."""
    cached = getattr(handle, "role_live_channel", None)
    if cached is not None:
        cached.close(reason="owned-execution-ended")
    from .run_execution import InlineRunHandle
    if isinstance(handle, InlineRunHandle):
        return CleanupOutcome(outcome="not-applicable", reason="endpoint-owned-by-current-process")
    material = _material(handle)
    descriptor = material[1] if material is not None else None
    cached_descriptor = getattr(handle, "role_live_descriptor", None)
    if descriptor is None or (cached_descriptor is not None and cached_descriptor != descriptor):
        return CleanupOutcome(outcome="unverified", reason="endpoint-binding-unverified")
    previous = getattr(handle, "role_endpoint_cleanup", None)
    if previous is not None:
        return previous
    exit_code = handle.process.poll()
    if handle.process.pid != handle.pid:
        return CleanupOutcome(outcome="unverified", reason="process-identity-mismatch")
    evidence = ConfirmedProcessGone(pid=handle.pid, exit_code=exit_code,
                                    group_gone=handle.shutdown_confirmed() is True)
    if evidence.exit_code is not None and evidence.group_gone:
        handle.role_endpoint_cleanup = CleanupOutcome(outcome="unverified", reason="endpoint-reap-unconfirmed")
    result = cleanup_owned_endpoint(descriptor, evidence, context=cc.local_endpoint_context())
    if evidence.exit_code is not None and evidence.group_gone:
        handle.role_endpoint_cleanup = result
    return result
