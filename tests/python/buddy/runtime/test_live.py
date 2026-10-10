"""Focused 5-B2 tests: real identities, holders, C-Two hops and owner commits."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import c_two as cc

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.live import (
    InquiryPayload, LiveCapabilities, LiveReply, LiveRequest, LiveSnapshot,
    MAX_LIVE_FRAME_BYTES,
)
from hey_my_buddy.buddy.runtime import live
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from hey_my_buddy.protocol.contracts import HarnessRunLive, WorkerRuntimeLive
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.run_identity import RunIdentity
from hey_my_buddy.protocol.worker_live import WorkerLiveActor, WorkerLiveAttach, WorkerLiveDetach


def identity(**changes):
    values = dict(task_id="task-live", attempt_id="attempt-live", generation=1,
                  invocation_id="invocation-live", turn_id="turn-live", input_sha256="a" * 64)
    values.update(changes)
    return RunIdentity(**values)


def claim(run=None):
    run = run or identity()
    return {"task": {"taskId": run.task_id},
            "attempt": {"taskId": run.task_id, "attemptId": run.attempt_id,
                        "generation": run.generation}}


def request(run=None, request_id="request-1", question_id="question-1", question="What changed?"):
    return LiveRequest(identity=run or identity(), request_id=request_id, kind="inquiry",
                       payload=InquiryPayload(question_id=question_id, question=question))


def frame(attachment, model=ctl.LiveWireRequest, **changes):
    values = dict(identity=attachment.identity, instance_id=attachment.instance_id,
                  token=attachment.live_token)
    if model is ctl.LiveWireRequest:
        values.update(request_id="request-1", kind="inquiry", payload=request().payload, timeout_ms=1000,
                      deadline_monotonic=time.monotonic() + 1.0)
    elif model is ctl.LiveWireObserve:
        values.update(limit=10)
    values.update(changes)
    return canonical_json(model(**values).to_payload())


def reply(raw):
    return LiveReply.from_payload(decode_strict_json(raw))


def snapshot(raw):
    return LiveSnapshot.from_payload(decode_strict_json(raw))


class FakeC2:
    def __init__(self, events):
        self.events = events
        self.address = "ipc://actual-worker-address"

    def __getattr__(self, name):
        return getattr(cc, name)

    def register(self, contract, implementation, **options):
        self.events.append(("register", contract, options["name"]))

    def server_address(self):
        self.events.append(("address",))
        return self.address

    def unregister(self, name):
        self.events.append(("unregister", name))

    def shutdown(self):
        self.events.append(("shutdown",))


class FakeBoard:
    def __init__(self):
        self.state_dir = Path(os.environ["BUDDY_STATE_DIR"]).resolve()
        self.attachments = []
        self.hook = lambda attachment: None
        self.detach_hook = lambda withdrawal: None
        self.detach_attempts = []
        self.detaches = []
        self.registry = {}

    def live_attach(self, attachment):
        self.hook(attachment)
        validated = WorkerLiveAttach.from_payload(attachment.to_payload())
        self.attachments.append(validated)
        self.registry[validated.identity] = validated
        return {"attached": True}

    def live_detach(self, withdrawal):
        validated = WorkerLiveDetach.from_payload(withdrawal.to_payload())
        self.detach_attempts.append(validated)
        self.detach_hook(validated)
        self.detaches.append(validated)
        current = self.registry.get(validated.identity)
        if current is not None and current.instance_id == validated.instance_id \
                and all(getattr(current, field) == getattr(validated, field)
                        for field in WorkerLiveActor.model_fields):
            self.registry.pop(validated.identity)
        return {"detached": True}


class RecordingChannel(ctl.CTwoLiveChannel):
    """Concrete bounded backend with local methods replaced for focused gates."""

    def __init__(self, run=None):
        super().__init__(run or identity(), HarnessRunLive, name="Ada", address="ipc://controller-only",
                         instance_id="b" * 64, token="c" * 64, state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
        self.calls = []
        self.closes = []
        self.check = lambda: None
        self.request_hook = lambda: LiveReply(status="queued", observed=True, state="queued")

    def _request(self, value, *, timeout_ms, deadline_monotonic):
        self.check()
        self.deadline = deadline_monotonic
        self.calls.append(("request", value, timeout_ms))
        return self.request_hook()

    def observe(self, **arguments):
        self.check()
        self.calls.append(("observe", arguments))
        return LiveSnapshot(observed=False, reason="fixture-no-source")

    def capabilities(self):
        self.check()
        self.calls.append(("capabilities",))
        return LiveCapabilities(inquiry_delivery="cooperative-checkpoint")

    def close(self, *, reason):
        self.check()
        self.closes.append(reason)
        super().close(reason=reason)


class WorkerLiveUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict(os.environ, {"BUDDY_STATE_DIR": self.temp.name}))
        self.events = []
        self.c2 = FakeC2(self.events)
        self.enterContext(patch.object(live, "cc", self.c2))
        self.enterContext(patch.object(live.rpc_config, "configure_server",
                                      lambda state: self.events.append(("server-profile", state))))
        self.enterContext(patch.object(live.rpc_config, "configure_client",
                                      lambda state: self.events.append(("client-profile", state))))
        self.board = FakeBoard()
        self.channel = RecordingChannel()
        self.handle = SimpleNamespace(role_run_identity=identity(), pid=123)
        self.resolutions = []

        def resolve(handle):
            self.resolutions.append(handle)
            return "bound", self.channel

        self.runtime = live.WorkerLiveRuntime(self.board, "worker-1", "process-1", resolve, state_dir=self.board.state_dir)
        self.addCleanup(self.runtime.stop)

    def bound(self):
        self.assertTrue(self.runtime.bind(claim(), self.handle, "nonce-1"))
        return self.board.attachments[-1]

    def test_private_controller_reaches_start_with_parent_state(self):
        path = Path(__file__).parent / "fixtures" / "live_runtime_peer.py"
        spec = importlib.util.spec_from_file_location("live_runtime_peer", path)
        peer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(peer)
        command = {"op": "start", "identity": identity().to_payload()}

        def before_bind(endpoint):
            self.assertEqual(endpoint._state_dir, Path(self.temp.name))
            raise BoardError("FIXTURE_START_REACHED", "before native registration")

        with patch.object(sys, "stdin", io.StringIO(canonical_json(command) + "\n")), \
                patch.object(ctl.CTwoLiveEndpoint, "start", autospec=True, side_effect=before_bind) as start, \
                patch.object(ctl.CTwoLiveEndpoint, "stop") as stop:
            failure = None
            try:
                peer.controller()
            except Exception as error:
                failure = error
        self.assertIsInstance(failure, BoardError, "private controller must reach start with the parent state Path")
        self.assertEqual(failure.code, "FIXTURE_START_REACHED")
        start.assert_called_once()
        stop.assert_called_once()

    def unlocked(self):
        acquired = self.runtime._lock.acquire(blocking=False)
        self.assertTrue(acquired, "map lock held across external call")
        if acquired:
            self.runtime._lock.release()

    def test_b2_01_profiles_actual_address_single_resource_and_stop(self):
        self.board.hook = lambda attachment: self.events.append(("attach",))
        attachment = self.bound()
        descriptor = self.runtime.start()
        self.assertIs(descriptor, self.runtime.start())
        self.assertEqual(attachment.address, self.c2.address)
        self.assertEqual([e[0] for e in self.events],
                         ["server-profile", "client-profile", "register", "address", "attach"])
        self.assertEqual(self.events[0][1], self.board.state_dir)
        self.assertEqual(self.events[1][1], self.board.state_dir)
        self.assertIs(self.events[2][1], WorkerRuntimeLive)
        self.runtime.stop()
        self.runtime.stop()
        self.assertEqual([e[0] for e in self.events[-2:]], ["unregister", "shutdown"])
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))

    def test_worker_supplied_root_wins_over_an_injected_client_root(self):
        owner = Path(self.temp.name).resolve()
        runtime = live.WorkerLiveRuntime(SimpleNamespace(state_dir="<FOREIGN_STATE>"),
            "worker-owned", "process-owned", lambda handle: ("bound", self.channel), state_dir=owner)
        self.assertEqual(self.events[-2:], [("server-profile", Path(owner)), ("client-profile", Path(owner))])
        runtime.stop()

    def test_b2_02_actual_actor_identity_and_independent_worker_token(self):
        attachment = self.bound()
        self.assertEqual(attachment.identity, self.handle.role_run_identity)
        self.assertEqual((attachment.worker_id, attachment.worker_instance, attachment.attempt_id,
                          attachment.generation, attachment.nonce),
                         ("worker-1", "process-1", "attempt-live", 1, "nonce-1"))
        self.assertEqual(set(attachment.to_payload()),
                         {"workerId", "workerInstance", "attemptId", "generation", "nonce", "identity",
                          "address", "name", "instanceId", "liveToken"})
        self.assertFalse(attachment.live_token == self.channel._token)
        self.assertFalse(attachment.instance_id == self.channel._instance_id)
        self.assertFalse(attachment.address == self.channel._address)
        self.assertNotIn(attachment.live_token, repr(attachment))
        self.assertNotIn("token", self.runtime.start().to_payload())

    def test_b2_03_command_pending_and_wrong_identity_do_not_create_bindings(self):
        self.assertFalse(self.runtime.bind(claim(), SimpleNamespace(pid=123), "nonce-1"))
        self.assertFalse(self.resolutions)
        self.assertEqual(len(self.events), 2)
        self.runtime._resolve_channel = lambda handle: ("unavailable", None)
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
        wrong = RecordingChannel(identity(invocation_id="foreign"))
        self.runtime._resolve_channel = lambda handle: ("bound", wrong)
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
        self.assertFalse(self.board.attachments)
        self.runtime._resolve_channel = lambda handle: ("bound", self.channel)
        self.bound()
        self.assertFalse(self.runtime.bind(claim(identity(generation=2)), self.handle, "nonce-1"))

    def test_b2_04_bind_retry_and_refresh_cache_channel_and_require_same_holder_actor(self):
        self.board.hook = lambda attachment: (_ for _ in ()).throw(RuntimeError("board unavailable"))
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
        self.board.hook = lambda attachment: None
        attachment = self.bound()
        self.assertTrue(self.runtime.refresh(claim(), self.handle, "nonce-1"))
        self.assertEqual(len(self.resolutions), 1)
        self.assertTrue(self.board.attachments[-1] == attachment)
        other_handle = SimpleNamespace(role_run_identity=identity(), pid=self.handle.pid)
        self.assertFalse(self.runtime.refresh(claim(), other_handle, "nonce-1"))
        self.assertFalse(self.runtime.bind(claim(), other_handle, "nonce-1"))
        self.assertFalse(self.runtime.refresh(claim(), self.handle, "other-nonce"))
        self.assertEqual(len(self.board.attachments), 2)

    def test_b2_05_request_observe_and_capabilities_forward_only_known_methods(self):
        attachment = self.bound()
        result = reply(self.runtime.request(frame(attachment)))
        self.assertEqual(result.status, "queued")
        self.assertEqual(self.channel.calls[0][1].identity, identity())
        self.assertEqual(self.channel.calls[0][2], 1000)
        result = snapshot(self.runtime.observe(frame(attachment, ctl.LiveWireObserve,
                                                    after_seq=3, fields=("observation",))))
        self.assertFalse(result.observed)
        self.assertEqual(result.reason, "fixture-no-source")
        self.assertEqual(self.channel.calls[1][1],
                         {"after_seq": 3, "limit": 10, "fields": ("observation",),
                          "inquiry_id": None, "timeout_ms": 1500})
        result = decode_strict_json(self.runtime.capabilities(frame(attachment, ctl.LiveWireQuery)))
        self.assertEqual(result, {"inquiryDelivery": "cooperative-checkpoint"})
        self.assertFalse(hasattr(self.runtime, "dispatch"))
        self.assertFalse(hasattr(self.runtime, "consume_request"))
        point = snapshot(self.runtime.observe(frame(attachment, ctl.LiveWireObserve,
                                                   inquiry_id="question-1", limit=None)))
        self.assertFalse(point.observed)

    def test_worker_forwarding_preserves_original_deadline_and_refuses_missing_or_expired(self):
        attachment = self.bound()
        deadline = time.monotonic() + 1.0
        self.assertEqual(reply(self.runtime.request(frame(attachment, deadline_monotonic=deadline))).status, "queued")
        self.assertEqual(self.channel.deadline, deadline)
        self.channel.calls.clear()
        payload = json.loads(frame(attachment))
        del payload["deadlineMonotonic"]
        self.assertEqual(reply(self.runtime.request(canonical_json(payload))).reason_code, "frame-invalid")
        self.assertEqual(reply(self.runtime.request(frame(attachment, deadline_monotonic=0.0))).reason_code,
                         "request-window-expired")
        self.assertEqual(self.channel.calls, [], "missing/expired request reached controller")

    def test_b2_06_all_six_identity_components_tokens_instances_refuse_before_peer(self):
        attachment = self.bound()
        changes = {"task_id": "other-task", "attempt_id": "other-attempt", "generation": 2,
                   "invocation_id": "other-invocation", "turn_id": "other-turn", "input_sha256": "d" * 64}
        for field, value in changes.items():
            with self.subTest(field=field):
                result = reply(self.runtime.request(frame(attachment, identity=identity(**{field: value}))))
                self.assertEqual(result.status, "unavailable")
        for name, value in (("token", "d" * 64), ("instance_id", "e" * 64)):
            with self.subTest(field=name):
                self.assertEqual(reply(self.runtime.request(frame(attachment, **{name: value}))).status,
                                 "unavailable")
                self.assertFalse(snapshot(self.runtime.observe(
                    frame(attachment, ctl.LiveWireObserve, **{name: value}))).observed)
                with self.assertRaises(BoardError):
                    self.runtime.capabilities(frame(attachment, ctl.LiveWireQuery, **{name: value}))
        self.assertFalse(self.channel.calls)

    def test_b2_07_shared_strict_codec_rejects_bad_and_oversize_frames(self):
        attachment = self.bound()
        valid = frame(attachment)
        malformed = ['{', '{"token":"x","token":"y"}',
                     '{"identity":{"generation":NaN}}',
                     '{"identity":{"generation":1e999}}',
                     ' ' * (MAX_LIVE_FRAME_BYTES + 1), None]
        extra = decode_strict_json(valid)
        extra["dispatch"] = "cancel"
        malformed.append(canonical_json(extra))
        for raw in malformed:
            with self.subTest(kind=type(raw).__name__):
                self.assertEqual(reply(self.runtime.request(raw)).status, "unavailable")
                self.assertFalse(snapshot(self.runtime.observe(raw)).observed)
                with self.assertRaises(BoardError):
                    self.runtime.capabilities(raw)
        self.assertFalse(self.channel.calls)

    def test_b2_08_source_handle_and_channel_identity_are_real_gates(self):
        attachment = self.bound()
        original = self.handle.role_run_identity
        self.handle.role_run_identity = identity(invocation_id="changed-handle")
        self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
        self.handle.role_run_identity = original
        self.channel._identity = identity(invocation_id="changed-channel")
        self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
        self.assertFalse(self.channel.calls)

    def test_b2_09_unbind_is_local_and_old_binding_is_rejected(self):
        attachment = self.bound()
        self.assertFalse(self.runtime.unbind(claim(identity(generation=2))))
        self.assertTrue(self.runtime.unbind(claim()))
        self.assertFalse(self.runtime.unbind(claim()))
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])
        self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
        self.assertFalse(self.runtime.refresh(claim(), self.handle, "nonce-1"))
        self.assertFalse(self.channel.calls)
        self.assertEqual(len(self.board.attachments), 1)
        self.assertEqual(len(self.board.detaches), 1)
        self.assertFalse(self.board.registry)

    def test_b2_10_resolver_board_peer_and_close_all_run_outside_map_lock(self):
        self.runtime._resolve_channel = lambda handle: (self.unlocked() or "bound", self.channel)
        self.board.hook = lambda attachment: self.unlocked()
        self.board.detach_hook = lambda withdrawal: self.unlocked()
        self.channel.check = self.unlocked
        attachment = self.bound()
        self.runtime.refresh(claim(), self.handle, "nonce-1")
        self.runtime.request(frame(attachment))
        self.runtime.observe(frame(attachment, ctl.LiveWireObserve))
        self.runtime.capabilities(frame(attachment, ctl.LiveWireQuery))
        self.runtime.unbind(claim())
        self.assertEqual(len(self.board.detaches), 1)

    def test_b2_11_blocked_resolver_cannot_resurrect_unbound_handle(self):
        entered, release = threading.Event(), threading.Event()

        def resolve(handle):
            entered.set()
            release.wait(3)
            return "bound", self.channel

        self.runtime._resolve_channel = resolve
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.runtime.unbind(claim()))
            finally:
                release.set()
            self.assertFalse(pending.result(2))
        self.assertFalse(self.board.attachments)
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])

    def test_b2_12_board_failure_does_not_hold_map_or_adopt_another_handle(self):
        entered, release = threading.Event(), threading.Event()

        def hook(attachment):
            if attachment.identity == identity():
                entered.set()
                release.wait(3)

        other = identity(task_id="task-other", attempt_id="attempt-other")
        other_channel = RecordingChannel(other)
        other_handle = SimpleNamespace(role_run_identity=other)
        self.runtime._resolve_channel = lambda handle: (
            "bound", self.channel if handle is self.handle else other_channel)
        self.board.hook = hook
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                second = pool.submit(self.runtime.bind, claim(other), other_handle, "nonce-other")
                self.assertTrue(second.result(1))
                self.assertTrue(self.runtime.unbind(claim()))
            finally:
                release.set()
            self.assertFalse(first.result(2))
        self.assertFalse(self.runtime.refresh(claim(), self.handle, "nonce-1"))
        self.assertNotIn(identity(), self.board.registry)

    def test_b2_13_source_failure_and_private_exceptions_are_only_unavailable(self):
        attachment = self.bound()
        secret = self.channel._token
        self.channel.request_hook = lambda: (_ for _ in ()).throw(RuntimeError(secret))
        result = self.runtime.request(frame(attachment))
        self.assertEqual(reply(result).status, "unavailable")
        self.assertNotIn(secret, result)
        self.assertNotIn("shutdown", result.lower())
        self.channel.check = lambda: (_ for _ in ()).throw(RuntimeError(secret))
        result = self.runtime.observe(frame(attachment, ctl.LiveWireObserve))
        self.assertFalse(snapshot(result).observed)
        self.assertNotIn(secret, result)
        with self.assertRaises(BoardError) as error:
            self.runtime.capabilities(frame(attachment, ctl.LiveWireQuery))
        self.assertNotIn(secret, str(error.exception))
        self.channel.check = lambda: None

    def test_b2_14_failed_start_releases_only_own_registration(self):
        self.c2.address = None
        with self.assertRaises(BoardError):
            self.runtime.start()
        self.assertEqual([e[0] for e in self.events[-2:]], ["unregister", "shutdown"])

    def test_b2_21_rebinding_changes_service_token_and_old_binding_cannot_replay(self):
        old = self.bound()
        self.assertTrue(self.runtime.unbind(claim()))
        self.channel = RecordingChannel()
        new = self.bound()
        self.assertFalse(old.live_token == new.live_token)
        self.assertEqual(old.instance_id, new.instance_id)
        self.assertEqual(reply(self.runtime.request(frame(old))).status, "unavailable")
        self.assertFalse(self.channel.calls)
        self.assertEqual(reply(self.runtime.request(frame(new))).status, "queued")

    def test_b2_22_stop_during_resolver_closes_late_channel_without_attaching(self):
        entered, release = threading.Event(), threading.Event()

        def resolve(handle):
            entered.set()
            release.wait(3)
            return "bound", self.channel

        self.runtime._resolve_channel = resolve
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                self.runtime.stop()
            finally:
                release.set()
            self.assertFalse(pending.result(2))
        self.assertFalse(self.board.attachments)
        self.assertEqual(self.channel.closes, ["binding-unavailable"])

    def test_b2_23_failed_register_shuts_own_session_and_does_not_keep_channel(self):
        def failed_register(*args, **options):
            self.events.append(("register-failed",))
            raise RuntimeError("fixture IPC initialization refused")

        self.c2.register = failed_register
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
        self.assertEqual([e[0] for e in self.events],
                         ["server-profile", "client-profile", "register-failed", "shutdown"])
        self.assertEqual(self.channel.closes, ["binding-unavailable"])
        self.assertFalse(self.runtime._resolving)
        self.assertFalse(self.board.attachments)

    def test_b2_24_named_detach_transmits_exact_retained_actor_identity_instance(self):
        calls = []

        def call(operation, payload):
            self.unlocked()
            calls.append((operation, payload))
            return {"attached": True} if operation == "worker_live_attach" else {"detached": True}

        self.runtime._client = BoardClient(call=call, autostart=False)
        self.assertTrue(self.runtime.bind(claim(), self.handle, "nonce-1"))
        attachment = WorkerLiveAttach.from_payload(calls[0][1])
        # Removal uses held facts even if the handle later loses its identity.
        self.handle.role_run_identity = None
        self.assertTrue(self.runtime.unbind(claim()))
        self.assertEqual([operation for operation, _ in calls],
                         ["worker_live_attach", "worker_live_detach"])
        expected = {field: value for field, value in attachment.to_payload().items()
                    if field not in {"address", "name", "liveToken"}}
        self.assertEqual(calls[1][1], expected)
        self.assertEqual(WorkerLiveDetach.from_payload(calls[1][1]).identity, identity())
        self.assertFalse(self.runtime._retiring)
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])

    def test_b2_25_failed_detach_revokes_locally_fences_rebind_and_can_retry(self):
        attachment = self.bound()

        def fail(withdrawal):
            self.unlocked()
            raise RuntimeError("board offline")

        self.board.detach_hook = fail
        self.assertTrue(self.runtime.unbind(claim()))
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])
        self.assertFalse(self.runtime._bindings)
        self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
        self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
        self.assertEqual(len(self.resolutions), 1)
        self.assertIn(identity(), self.board.registry)
        self.assertEqual(len(self.board.detach_attempts), 1)
        self.board.detach_hook = lambda withdrawal: self.unlocked()
        self.assertTrue(self.runtime.unbind(claim()))
        self.assertFalse(self.board.registry)
        self.assertFalse(self.runtime._retiring)
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])
        self.channel = RecordingChannel()
        replacement = self.bound()
        self.assertNotEqual(replacement.live_token, attachment.live_token)
        self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")

    def test_b2_26_stop_detaches_each_holder_and_retries_failure_after_socket_shutdown(self):
        self.bound()
        other = identity(task_id="task-other", attempt_id="attempt-other")
        other_handle = SimpleNamespace(role_run_identity=other)
        other_channel = RecordingChannel(other)
        self.runtime._resolve_channel = lambda handle: ("bound", other_channel)
        self.assertTrue(self.runtime.bind(claim(other), other_handle, "other-nonce"))

        def hook(withdrawal):
            self.unlocked()
            self.assertEqual(self.events[-1][0], "shutdown")
            acquired = self.runtime._lifecycle.acquire(blocking=False)
            self.assertTrue(acquired)
            if acquired:
                self.runtime._lifecycle.release()
            if withdrawal.identity == identity():
                raise RuntimeError("one detach unavailable")

        self.board.detach_hook = hook
        self.runtime.stop()
        self.assertFalse(self.runtime._bindings)
        self.assertEqual(set(self.board.registry), {identity()})
        self.assertEqual(len(self.board.detach_attempts), 2)
        self.assertEqual(self.channel.closes, ["worker-live-stopped"])
        self.assertEqual(other_channel.closes, ["worker-live-stopped"])
        self.board.detach_hook = lambda withdrawal: self.unlocked()
        self.runtime.stop()
        self.assertFalse(self.board.registry)
        self.assertFalse(self.runtime._retiring)
        self.assertEqual(len(self.board.detach_attempts), 3)
        self.assertEqual(sum(event[0] == "shutdown" for event in self.events), 1)

    def test_b2_27_unbind_during_attach_compensates_late_commit_and_fences_same_key(self):
        entered, release = threading.Event(), threading.Event()

        def hook(attachment):
            self.unlocked()
            entered.set()
            release.wait(3)

        self.board.hook = hook
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.runtime.unbind(claim()))
                self.assertEqual(len(self.board.detaches), 1)
                self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
                self.assertFalse(self.runtime.refresh(claim(), self.handle, "nonce-1"))
            finally:
                release.set()
            self.assertFalse(pending.result(2))
        self.assertEqual(len(self.board.attachments), 1)
        self.assertEqual(len(self.board.detaches), 2)
        self.assertFalse(self.board.registry)
        self.assertFalse(self.runtime._retiring)
        self.assertEqual(self.channel.closes, ["binding-withdrawn"])
        self.board.hook = lambda attachment: None
        self.channel = RecordingChannel()
        self.bound()

    def test_b2_28_inflight_detach_repeats_after_late_attach_return(self):
        attach_entered, attach_release = threading.Event(), threading.Event()
        detach_entered, detach_release = threading.Event(), threading.Event()

        def attach_hook(attachment):
            attach_entered.set()
            attach_release.wait(3)

        def detach_hook(withdrawal):
            self.unlocked()
            if len(self.board.detach_attempts) == 1:
                detach_entered.set()
                detach_release.wait(3)

        self.board.hook = attach_hook
        self.board.detach_hook = detach_hook
        with ThreadPoolExecutor(max_workers=2) as pool:
            attach = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(attach_entered.wait(1))
                detach = pool.submit(self.runtime.unbind, claim())
                self.assertTrue(detach_entered.wait(1))
                attach_release.set()
                self.assertFalse(attach.result(2))
                self.assertFalse(self.runtime.bind(claim(), self.handle, "nonce-1"))
            finally:
                attach_release.set()
                detach_release.set()
            self.assertTrue(detach.result(2))
        self.assertEqual(len(self.board.detaches), 2)
        self.assertFalse(self.board.registry)
        self.assertFalse(self.runtime._retiring)

    def test_b2_29_refresh_inflight_is_serialized_and_unbind_cleans_late_refresh(self):
        attachment = self.bound()
        entered, release = threading.Event(), threading.Event()

        def hook(value):
            entered.set()
            release.wait(3)

        self.board.hook = hook
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.runtime.refresh, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.runtime.refresh(claim(), self.handle, "nonce-1"))
                self.assertTrue(self.runtime.unbind(claim()))
                self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
            finally:
                release.set()
            self.assertFalse(pending.result(2))
        self.assertEqual(len(self.board.attachments), 2)
        self.assertEqual(len(self.board.detaches), 2)
        self.assertFalse(self.board.registry)

    def test_b2_30_failed_attach_reply_after_commit_is_compensated_on_stop(self):
        entered, release = threading.Event(), threading.Event()
        original = self.board.live_attach

        def lost_reply(attachment):
            original(attachment)
            entered.set()
            release.wait(3)
            # The actual fake registry commit occurred before the reply failed.
            raise RuntimeError("lost attach response")

        self.board.live_attach = lost_reply
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.runtime.bind, claim(), self.handle, "nonce-1")
            try:
                self.assertTrue(entered.wait(1))
                self.assertIn(identity(), self.board.registry)
                self.runtime.stop()
                self.assertFalse(self.board.registry)
            finally:
                release.set()
            self.assertFalse(pending.result(2))
        self.assertFalse(self.runtime._retiring)
        self.assertEqual(len(self.board.detaches), 2)
        self.assertEqual(self.channel.closes, ["worker-live-stopped"])

    def test_b2_31_blocked_detach_does_not_block_another_holder(self):
        self.bound()
        entered, release = threading.Event(), threading.Event()

        def hook(withdrawal):
            if withdrawal.identity == identity():
                entered.set()
                release.wait(3)

        other = identity(task_id="task-other", attempt_id="attempt-other")
        other_handle = SimpleNamespace(role_run_identity=other)
        other_channel = RecordingChannel(other)
        self.runtime._resolve_channel = lambda handle: ("bound", other_channel)
        self.board.detach_hook = hook
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.runtime.unbind, claim())
            try:
                self.assertTrue(entered.wait(1))
                second = pool.submit(self.runtime.bind, claim(other), other_handle, "other-nonce")
                self.assertTrue(second.result(1))
                attachment = self.board.attachments[-1]
                self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "queued")
                self.assertTrue(self.runtime.unbind(claim(other)))
            finally:
                release.set()
            self.assertTrue(pending.result(2))
        self.assertFalse(self.board.registry)

    def test_b2_32_repeated_bind_unbind_does_not_accumulate_service_registrations(self):
        for _ in range(65):
            self.channel = RecordingChannel()
            attachment = self.bound()
            self.assertTrue(self.runtime.unbind(claim()))
            self.assertFalse(self.board.registry)
            self.assertFalse(self.runtime._retiring)
            self.assertEqual(reply(self.runtime.request(frame(attachment))).status, "unavailable")
        self.assertEqual(len(self.board.detaches), 65)


SANITIZED = ("BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
             "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE",
             "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
PEER = Path(__file__).resolve().parent / "fixtures/live_runtime_peer.py"


def materials():
    # Retain every new directory for Host cleanup; no TemporaryDirectory teardown.
    root = os.environ.get("BUDDY_LIVE_TEST_MATERIALS",
                          os.environ.get("BUDDY_CHECKS_TMPDIR", tempfile.gettempdir()))
    return Path(tempfile.mkdtemp(prefix="case-", dir=root))


class Peer:
    def __init__(self):
        self.root = materials()
        env = {key: value for key, value in os.environ.items() if key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
               and not key.startswith(("BUDDY_", "ANTHROPIC_", "C2_"))}
        self.state = Path(os.environ["BUDDY_STATE_DIR"])
        from hey_my_buddy.protocol import rpc_config
        rpc_config.configure_client(self.state)
        env.update(BUDDY_STATE_DIR=str(self.state), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                   TMPDIR=str(self.root))
        env.update(C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="", PYTHONDONTWRITEBYTECODE="1")
        self.stderr = (self.root / "peer-stderr.log").open("x")
        self.process = subprocess.Popen([sys.executable, "-c",
            "import os,runpy,sys; module=runpy.run_path(sys.argv[1]); "
            "module['FakeBoardClient'].state_dir=os.environ['BUDDY_STATE_DIR']; module['worker']()",
            str(PEER)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr, text=True, env=env)
        self.controllers = []
        try:
            self.descriptor = ctl.LiveEndpointDescriptor.from_payload(self.call(
                op="start", workerId="worker-test", workerInstance="worker-process-test")["descriptor"])
        except Exception:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            finally:
                self.process.stdout.close()
                self.stderr.close()
            raise

    def call(self, **command):
        self.process.stdin.write(canonical_json(command) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("private Worker peer closed its pipe; inspect retained stderr")
        value = decode_strict_json(line)
        if not value.get("ok"):
            raise RuntimeError("private Worker peer command failed")
        return value

    def bind(self, label="one", run=None, **options):
        run = run or identity()
        descriptor = ctl.LiveEndpointDescriptor.from_payload(self.call(
            op="spawn", label=label, identity=run.to_payload(),
            journal=str(self.root / (label + "-journal.jsonl")), **options)["descriptor"])
        self.controllers.append(descriptor)
        bound = self.call(op="bind", label=label, claim=claim(run), nonce="nonce-test")
        if not bound["bound"]:
            raise RuntimeError("private Worker bind failed")
        return WorkerLiveAttach.from_payload(bound["attachment"])

    def controller(self, label="one", **command):
        return self.call(op="controller", label=label, command=command)

    def channel(self, attachment, **changes):
        values = dict(name=attachment.name, address=attachment.address,
                      instance_id=attachment.instance_id, token=attachment.live_token)
        values.update(changes)
        return ctl.CTwoLiveChannel(attachment.identity, WorkerRuntimeLive, state_dir=self.state, **values)

    def stop(self):
        try:
            if self.process.poll() is None:
                result = self.call(op="stop")
                self.process.wait(timeout=10)
                if result["registrationCount"] != 0:
                    raise RuntimeError("private Worker left live registrations after stop")
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
            self.stderr.close()


@contextmanager
def peer():
    process = Peer()
    try:
        yield process
    finally:
        process.stop()


def endpoint_status(descriptor):
    return cc.inspect_endpoint(descriptor.address)["status"]


@unittest.skipUnless(os.name == "posix", "native local endpoint maintenance is POSIX")
class WorkerLiveRealPeerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = materials() / "state"
        cls.environment = patch.dict(os.environ, {"BUDDY_STATE_DIR": str(cls.state)})
        cls.environment.start()

    @classmethod
    def tearDownClass(cls):
        cc.shutdown()
        cls.environment.stop()

    def test_b2_15_real_owner_journal_commit_precedes_queued_and_observe_carries_facts(self):
        with peer() as process:
            attachment = process.bind(paused=True)
            channel = process.channel(attachment)
            self.assertEqual(channel.capabilities().inquiry_delivery, "cooperative-checkpoint")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(channel.request, request(), timeout_ms=4000)
                deadline = time.monotonic() + 2
                while not process.controller(op="consumed")["consumed"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(process.controller(op="consumed")["consumed"])
                self.assertFalse(pending.done())
                self.assertEqual(len(process.controller(op="journal")["records"]), 0)
                before = channel.observe(limit=10, fields=("inquiries",), timeout_ms=1000)
                self.assertEqual(len(before.inquiries), 0)
                process.controller(op="commit")
                self.assertEqual(pending.result(3).status, "queued")
            records = process.controller(op="journal")["records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(RunIdentity.from_payload(records[0]["identity"]), attachment.identity)
            process.controller(op="publish", snapshot={"observed": True, "observation": {
                "ready": True, "agentStatus": "running", "sessionId": "fixture-session"}})
            observed = channel.observe(limit=10, timeout_ms=1000)
            self.assertTrue(observed.observed)
            self.assertEqual(observed.observation.session_id, "fixture-session")
            self.assertEqual(observed.journal.entries, 1)
            self.assertEqual(observed.inquiries[0].status, "queued")
            point = channel.observe(inquiry_id="question-1", timeout_ms=1000)
            self.assertEqual(point.inquiries[0].question_id, "question-1")

    def test_b2_16_real_controller_owns_replay_and_conflict_not_worker(self):
        with peer() as process:
            attachment = process.bind()
            channel = process.channel(attachment)
            self.assertEqual(channel.request(request(), timeout_ms=2000).status, "queued")
            self.assertEqual(channel.request(request(), timeout_ms=1000).status, "queued")
            conflict = channel.request(request(question="Changed text"), timeout_ms=1000)
            self.assertEqual(conflict.status, "unavailable")
            self.assertEqual(conflict.reason_code, "request-payload-conflict")
            self.assertEqual(len(process.controller(op="journal")["records"]), 1)

    def test_b2_17_real_same_name_different_addresses_and_normal_endpoint_disappearance(self):
        with peer() as first, peer() as second:
            one, two = first.bind(), second.bind()
            self.assertEqual(one.name, two.name)
            self.assertNotEqual(one.address, two.address)
            self.assertEqual(second.channel(two, token=one.live_token).request(
                request(), timeout_ms=1000).status, "unavailable")
            self.assertEqual(second.channel(two, instance_id=one.instance_id).request(
                request(), timeout_ms=1000).status, "unavailable")
            self.assertEqual(first.channel(one).request(request(), timeout_ms=2000).status, "queued")
            self.assertEqual(len(second.controller(op="journal")["records"]), 0)
            self.assertEqual(second.channel(two).request(request(), timeout_ms=2000).status, "queued")
            descriptors = [first.descriptor, second.descriptor, *first.controllers, *second.controllers]
            self.assertTrue(all(endpoint_status(d) == "present" for d in descriptors))
        self.assertTrue(all(endpoint_status(d) == "absent" for d in descriptors))
        self.assertEqual(first.process.returncode, 0)
        self.assertEqual(second.process.returncode, 0)

    def test_b2_18_real_full_identity_token_instance_refresh_and_unbind(self):
        with peer() as process:
            attachment = process.bind()
            for field, value in {"task_id": "other-task", "attempt_id": "other-attempt", "generation": 2,
                                 "invocation_id": "other-invocation", "turn_id": "other-turn",
                                 "input_sha256": "f" * 64}.items():
                altered = identity(**{field: value})
                channel = ctl.CTwoLiveChannel(
                    altered, WorkerRuntimeLive, name=attachment.name, address=attachment.address,
                    instance_id=attachment.instance_id, token=attachment.live_token, state_dir=self.state)
                self.assertEqual(channel.request(request(altered), timeout_ms=1000).status, "unavailable")
            for changes in ({"token": "d" * 64}, {"instance_id": "e" * 64}):
                channel = process.channel(attachment, **changes)
                self.assertEqual(channel.request(request(), timeout_ms=1000).status, "unavailable")
                self.assertFalse(channel.observe(limit=10, timeout_ms=1000).observed)
                with self.assertRaises(BoardError):
                    channel.capabilities()
            refreshed = process.call(op="refresh", label="one", claim=claim(), nonce="nonce-test")
            self.assertTrue(refreshed["bound"])
            self.assertTrue(WorkerLiveAttach.from_payload(refreshed["attachment"]) == attachment)
            detached = process.call(op="unbind", claim=claim())
            self.assertTrue(detached["unbound"])
            self.assertEqual(detached["registrationCount"], 0)
            self.assertEqual(process.channel(attachment).request(request(), timeout_ms=1000).status, "unavailable")
            held = process.call(op="held", label="one")
            self.assertTrue(held["held"])
            self.assertIsNone(held["returnCode"])

    def test_b2_19_real_stalled_source_bound_and_other_holder_remains_usable(self):
        with peer() as process:
            slow = process.bind(slow=True)
            other = identity(task_id="task-other", attempt_id="attempt-other")
            healthy = process.bind(label="two", run=other)
            with ThreadPoolExecutor(max_workers=2) as pool:
                started = time.monotonic()
                pending = pool.submit(process.channel(slow).request, request(), timeout_ms=250)
                result = pool.submit(process.channel(healthy).request, request(other), timeout_ms=1500)
                self.assertEqual(result.result(2).status, "queued")
                self.assertEqual(pending.result(1).status, "unavailable")
                self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(process.call(op="unbind", claim=claim())["unbound"])
            self.assertTrue(process.call(op="held", label="one")["held"])

    def test_b2_20_real_unreachable_controller_is_unavailable_not_shutdown_evidence(self):
        with peer() as process:
            attachment = process.bind()
            controller_descriptor = process.controllers[0]
            process.controller(op="stop")
            self.assertEqual(endpoint_status(controller_descriptor), "absent")
            channel = process.channel(attachment)
            result = channel.request(request(), timeout_ms=500)
            self.assertEqual(result.status, "unavailable")
            self.assertNotIn("shutdown", canonical_json(result.to_payload()).lower())
            observed = channel.observe(limit=10, timeout_ms=500)
            self.assertFalse(observed.observed)
            held = process.call(op="held", label="one")
            self.assertTrue(held["held"])
            self.assertEqual(held["returnCode"], 0)


if __name__ == "__main__":
    unittest.main()
