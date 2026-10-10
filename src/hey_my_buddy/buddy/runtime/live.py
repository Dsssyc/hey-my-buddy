"""One holding Worker's live resource over its actual in-memory handles.

The Host supplies the role resolver and owns the Worker lifecycle hooks. This
module neither creates role channels nor adopts processes. Controller admission,
owner settlement and replay stay behind the cached, bounded C-Two channel.
"""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import c_two as cc

from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol import rpc_config
from ...protocol.contracts import WorkerRuntimeLive
from ...protocol.inquiry import DEFAULT_TRANSPORT_TIMEOUT_MS
from ...protocol.internal_models import check_text, fail
from ...protocol.run_identity import RunIdentity
from ...protocol.worker_live import WorkerLiveActor, WorkerLiveAttach, WorkerLiveDetach
from ..harnesses.c_two_live import (
    CTwoLiveChannel,
    LiveEndpointDescriptor,
    LiveWireObserve,
    LiveWireQuery,
    LiveWireRequest,
    authenticate_live_frame,
    decode_live_wire_frame,
    random_person_name,
)
from ..harnesses.live import LiveChannel, LiveReply, LiveRequest, LiveSnapshot


@dataclass(repr=False)
class _Binding:
    handle: object
    channel: LiveChannel
    attachment: WorkerLiveAttach
    attaching: bool = False
    detaching: bool = False


class WorkerLiveRuntime:
    """Start before BoardClient use; bind only handles this Worker really holds.

    Construction configures both C-Two profiles before even a BoardClient
    connect. ``start`` may then be explicit or lazy at the first valid bind.
    The resolver returns the existing role seam's ``(state, channel)`` pair;
    a bound channel must be the already bounded CTwoLiveChannel backend.
    Failed attachments are retryable with the same handle/channel/token.
    ``refresh`` is called by the Host only after a successful renew/reconcile.
    """

    def __init__(self, client, worker_id: str, worker_instance: str,
                 resolve_channel: Callable[[object], tuple[str, LiveChannel | None]]):
        self._worker_id = check_text(worker_id, "workerId", maximum=128)
        self._worker_instance = check_text(worker_instance, "workerInstance", maximum=128)
        if not callable(resolve_channel):
            raise fail("resolve_channel must be callable")
        # BoardClient supplies the explicit state. For an injected client, the
        # Worker has already selected its domain before constructing this runtime.
        # Use that native selection, never a fresh environment-based selection.
        selected = getattr(client, "state_dir", None)
        if selected is None and os.name != "nt":
            root = cc.local_endpoint_context().root
            if root is None or Path(root).name != "ipc":
                raise fail("Worker live runtime requires the owning private state directory")
            selected = Path(root).parent
        self._state_dir = selected
        self._client = client
        self._resolve_channel = resolve_channel
        self._instance_id = secrets.token_hex(32)
        self._name = random_person_name()
        self._descriptor: LiveEndpointDescriptor | None = None
        self._lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._bindings: dict[tuple[str, str, int], _Binding] = {}
        # A withdrawn binding fences same-key reuse until its last remote attach
        # and a subsequent detach have returned. Other holders remain independent.
        self._retiring: dict[tuple[str, str, int], _Binding] = {}
        self._resolving: dict[tuple[str, str, int], object] = {}
        self._stopped = False
        rpc_config.configure_server(self._state_dir)
        rpc_config.configure_client(self._state_dir)

    def start(self) -> LiveEndpointDescriptor:
        """Register this one Worker resource and read back its actual address."""
        with self._lifecycle:
            if self._stopped:
                raise fail("the Worker live runtime is stopped")
            if self._descriptor is None:
                registered = False
                try:
                    cc.register(WorkerRuntimeLive, self, name=self._name,
                                concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
                    registered = True
                    address = cc.server_address()
                    self._descriptor = LiveEndpointDescriptor(
                        address=address, name=self._name, instance_id=self._instance_id,
                        host_pid=os.getpid())
                except Exception:
                    try:
                        if registered:
                            cc.unregister(self._name)
                    finally:
                        cc.shutdown()
                    raise
            return self._descriptor

    def _claim(self, claim, nonce: str) -> tuple[tuple[str, str, int], WorkerLiveActor]:
        attempt = claim["attempt"]
        task_id = claim["task"]["taskId"]
        actor = WorkerLiveActor(worker_id=self._worker_id,
                                worker_instance=self._worker_instance, nonce=nonce,
                                attempt_id=attempt["attemptId"], generation=attempt["generation"])
        if attempt.get("taskId", task_id) != task_id:
            raise fail("claim task and attempt disagree")
        return (task_id, actor.attempt_id, actor.generation), actor

    def bind(self, claim, handle, nonce: str) -> bool:
        """Resolve once, hold the real handle, and publish only the Worker hop."""
        identity = getattr(handle, "role_run_identity", None)
        if not isinstance(identity, RunIdentity):
            return False
        key, actor = self._claim(claim, nonce)
        if key != (identity.task_id, identity.attempt_id, identity.generation):
            return False
        with self._lock:
            if self._stopped:
                return False
            binding = self._bindings.get(key)
            if binding is not None:
                if not self._same(binding, handle, actor, identity):
                    return False
            elif key in self._resolving or key in self._retiring:
                return False
            else:
                reservation = object()
                self._resolving[key] = reservation
        if binding is not None:
            return self._attach(key, binding)
        channel = None
        try:
            state, channel = self._resolve_channel(handle)
            if state != "bound" or not isinstance(channel, CTwoLiveChannel) \
                    or channel.identity != identity:
                return False
            descriptor = self.start()
            attachment = WorkerLiveAttach(
                **actor.model_dump(), identity=identity, address=descriptor.address,
                name=descriptor.name, instance_id=descriptor.instance_id,
                live_token=secrets.token_hex(32))
            binding = _Binding(handle, channel, attachment)
            with self._lock:
                if self._stopped or self._resolving.get(key) is not reservation:
                    accepted = False
                else:
                    self._bindings[key] = binding
                    accepted = True
            if not accepted:
                channel.close(reason="binding-withdrawn")
                return False
            return self._attach(key, binding)
        except Exception:
            # Resolver/readiness/service failure is not process shutdown evidence.
            with self._lock:
                retained = binding is not None and self._bindings.get(key) is binding
            if not retained and isinstance(channel, CTwoLiveChannel) and channel.identity == identity:
                channel.close(reason="binding-unavailable")
            return False
        finally:
            with self._lock:
                if self._resolving.get(key) is reservation:
                    self._resolving.pop(key)

    @staticmethod
    def _same(binding: _Binding, handle, actor: WorkerLiveActor, identity: RunIdentity) -> bool:
        attachment = binding.attachment
        return binding.handle is handle and attachment.identity == identity \
            and all(getattr(attachment, field) == getattr(actor, field)
                    for field in WorkerLiveActor.model_fields)

    def _attach(self, key, binding: _Binding) -> bool:
        with self._lock:
            if self._stopped or self._bindings.get(key) is not binding or binding.attaching:
                return False
            binding.attaching = True
        attached = False
        try:
            self._client.live_attach(binding.attachment)
            attached = True
        except Exception:
            pass
        finally:
            with self._lock:
                binding.attaching = False
                current = not self._stopped and self._bindings.get(key) is binding
            if not current:
                # Even a failed reply may follow a committed attach. Always
                # compensate after a withdrawn in-flight call has returned.
                self._detach(key, binding)
        return attached and current

    def _detach(self, key, binding: _Binding) -> bool:
        attachment = binding.attachment
        withdrawal = WorkerLiveDetach(
            **{field: getattr(attachment, field) for field in WorkerLiveActor.model_fields},
            identity=attachment.identity, instance_id=attachment.instance_id)
        while True:
            with self._lock:
                if self._retiring.get(key) is not binding or binding.detaching:
                    return False
                binding.detaching = True
                attaching_at_start = binding.attaching
            detached = False
            try:
                self._client.live_detach(withdrawal)
                detached = True
            except Exception:
                # Local withdrawal and socket shutdown do not depend on the
                # board being reachable. Keep a same-key fence for an explicit
                # unbind/stop retry instead of assuming remote removal.
                pass
            finally:
                with self._lock:
                    binding.detaching = False
                    repeat = attaching_at_start and not binding.attaching
                    if detached and not attaching_at_start and not binding.attaching:
                        if self._retiring.get(key) is binding:
                            self._retiring.pop(key)
            if not repeat:
                return detached

    def refresh(self, claim, handle, nonce: str) -> bool:
        """Reattach the same binding after the Host's real ownership renewal."""
        identity = getattr(handle, "role_run_identity", None)
        if not isinstance(identity, RunIdentity):
            return False
        key, actor = self._claim(claim, nonce)
        with self._lock:
            binding = self._bindings.get(key)
            if self._stopped or binding is None or not self._same(binding, handle, actor, identity):
                return False
        return self._attach(key, binding)

    def unbind(self, claim) -> bool:
        """Withdraw locally, then best-effort named detach; never a stop receipt."""
        attempt = claim["attempt"]
        if isinstance(attempt["generation"], bool):
            return False
        key = (claim["task"]["taskId"], attempt["attemptId"], attempt["generation"])
        with self._lock:
            pending = self._resolving.pop(key, None)
            binding = self._bindings.pop(key, None)
            if binding is not None:
                self._retiring[key] = binding
            retiring = self._retiring.get(key)
        if binding is not None:
            binding.channel.close(reason="binding-withdrawn")
        if retiring is not None:
            self._detach(key, retiring)
        return retiring is not None or pending is not None

    def stop(self) -> None:
        """Close local channels and shut down this Worker's own C-Two resource."""
        with self._lifecycle:
            with self._lock:
                first_stop = not self._stopped
                self._stopped = True
                bindings = tuple(self._bindings.values())
                self._retiring.update(self._bindings)
                self._bindings.clear()
                self._resolving.clear()
                retiring = tuple(self._retiring.items())
            for binding in bindings:
                binding.channel.close(reason="worker-live-stopped")
            if first_stop and self._descriptor is not None:
                try:
                    cc.unregister(self._name)
                finally:
                    cc.shutdown()
        # Neither map nor lifecycle lock covers the BoardClient call. A failed
        # detach cannot prevent the Worker's own server from shutting down.
        for key, binding in retiring:
            self._detach(key, binding)

    def _select(self, request_json: str, model: type):
        value, refusal = decode_live_wire_frame(request_json)
        if refusal is not None:
            return None, None, refusal
        try:
            candidate = model.from_payload(value)
        except BoardError:
            return None, None, "frame-invalid"
        identity = candidate.identity
        key = (identity.task_id, identity.attempt_id, identity.generation)
        with self._lock:
            binding = self._bindings.get(key) if not self._stopped else None
        if binding is None:
            return None, None, "binding-unavailable"
        attachment = binding.attachment
        frame, refusal = authenticate_live_frame(
            request_json, model, identity=attachment.identity,
            instance_id=self._instance_id, token=attachment.live_token)
        if refusal is not None:
            return None, None, refusal
        # Both facts belong to the handle we actually retained, never a PID lookup.
        if getattr(binding.handle, "role_run_identity", None) != attachment.identity \
                or binding.channel.identity != attachment.identity:
            return None, None, "source-identity-mismatch"
        with self._lock:
            if self._stopped or self._bindings.get(key) is not binding:
                return None, None, "binding-unavailable"
        return binding, frame, None

    def request(self, request_json: str) -> str:
        binding, frame, refusal = self._select(request_json, LiveWireRequest)
        if refusal is not None:
            reply = LiveReply(status="unavailable", reason_code=refusal)
        else:
            request = LiveRequest(identity=frame.identity, request_id=frame.request_id,
                                  kind=frame.kind, payload=frame.payload)
            try:
                reply = binding.channel.request(request, timeout_ms=frame.timeout_ms)
            except Exception:
                reply = LiveReply(status="unavailable", reason_code="source-unavailable")
        return canonical_json(reply.to_payload())

    def observe(self, request_json: str) -> str:
        binding, frame, refusal = self._select(request_json, LiveWireObserve)
        if refusal is None and frame.inquiry_id is None and frame.limit is None:
            refusal = "frame-invalid"
        if refusal is not None:
            snapshot = LiveSnapshot(unavailable=refusal, observed=False, reason=refusal)
        else:
            try:
                snapshot = binding.channel.observe(
                    after_seq=frame.after_seq, limit=frame.limit, fields=frame.fields,
                    inquiry_id=frame.inquiry_id, timeout_ms=DEFAULT_TRANSPORT_TIMEOUT_MS)
            except Exception:
                snapshot = LiveSnapshot(unavailable="source-unavailable", observed=False,
                                        reason="source-unavailable")
        return canonical_json(snapshot.to_payload())

    def capabilities(self, request_json: str) -> str:
        binding, _, refusal = self._select(request_json, LiveWireQuery)
        if refusal is not None:
            raise BoardError("LIVE_UNAVAILABLE", "the Worker live call was refused", reason=refusal)
        try:
            return canonical_json(binding.channel.capabilities().to_payload())
        except Exception:
            # Never expose an exception message that might contain peer secrets.
            raise BoardError("LIVE_UNAVAILABLE", "the live source is unavailable") from None


__all__ = ["WorkerLiveRuntime"]
