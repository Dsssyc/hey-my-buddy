"""The role-side live seam over the harness registry (ADR-025 step 2-C2).

The seam is the only way a generic consumer — the blackboard's inquiry or the
Worker runtime's activity forwarding — reaches a harness live channel: through
the registry's registered binding, bound to the stored public run request's
complete identity. Nothing here imports a specific harness module; the
registration table alone decides which harness answers.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import c_two as cc
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, LiveEndpointDescriptor
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, live_binding, run_seam
from hey_my_buddy.buddy.harnesses.run_contract import (
    FrozenJson,
    HARNESS_NAMES,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunIdentity,
    RunRequest,
    decode_run_request,
    encode_run_request,
)
from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.roles import live as role_live
from hey_my_buddy.buddy.roles.turn_io import input_hash
from hey_my_buddy.errors import BoardError


def identity(*, invocation_id="invocation-live") -> RunIdentity:
    return RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                       invocation_id=invocation_id, turn_id="turn")


def request(*, harness="zcode", invocation_id="invocation-live") -> RunRequest:
    return RunRequest(
        identity=identity(invocation_id=invocation_id), harness=harness,
        configuration=RunConfiguration(provider="fixture-zcode", model="fixture-glm", effort="low"),
        cwd="/private/tmp", private_state=PrivateStatePaths(invocation_root="/private/tmp/invocation",
                                                            native_root="/private/tmp/invocation/native"),
        input_text="the governed turn input", tool_scope="write",
        output_schema=FrozenJson({"type": "object"}),
        budget=RunBudget(timeout_seconds=600))


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-live-holder-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory / "state")}))
        from hey_my_buddy.protocol import rpc_config
        rpc_config.configure_client(self.directory / "state")
        self.addCleanup(cc.shutdown)
        self.context = cc.local_endpoint_context()
        self.credential = SimpleNamespace(address="ipc://fixture", context=self.context)
        self.enterContext(mock.patch.object(cc.EndpointCredential, "from_json",
                                           return_value=self.credential))
        self.request_file = self.directory / "role-run-request.json"
        self.ready_file = self.directory / "live-ready.json"

    def handle(self, *, held=None, control=None):
        self.request_file.write_text(encode_run_request(request()))
        descriptor = LiveEndpointDescriptor(address="ipc://fixture", name="Alex", instance_id="b"*64,
                                            host_pid=123, endpoint_credential="opaque-native-credential")
        self.ready_file.write_text(json.dumps(descriptor.to_payload()))
        return SimpleNamespace(pid=123, role_run_control=control if control is not None else {
            "operation": "worker", "harness": "zcode", "requestFile": str(self.request_file),
            "live": {"readyFile": str(self.ready_file), "instanceId": "b"*64, "token": "a"*64}},
            role_run_identity=held if held is not None else identity())

    def unavailable(self, handle):
        self.assertEqual(role_live.handle_live_binding(handle), (role_live.LIVE_UNAVAILABLE, None))


class LiveBindingRegistryTests(Fixture, unittest.TestCase):
    def test_a_ready_handle_cannot_borrow_the_ambient_sdk_state_when_its_owner_is_missing(self):
        handle = self.handle()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cc, "connect") as connect, \
                mock.patch.object(cc, "local_endpoint_context") as context:
            with self.assertRaises(BoardError) as raised:
                role_live.handle_live_binding(handle)
            self.assertEqual(raised.exception.code, "PRIVATE_STATE_REQUIRED")
            connect.assert_not_called()
            context.assert_not_called()

    def test_only_registered_modules_carry_a_live_binding(self):
        for name in HARNESS_NAMES:
            self.assertIs(live_binding(name), CTwoLiveChannel)
        for name in ("command", "external", "not-a-harness"):
            self.assertIsNone(live_binding(name))

    def test_a_channel_binds_the_requests_own_complete_identity(self):
        state, channel = role_live.handle_live_binding(self.handle())
        self.assertEqual(state, role_live.LIVE_BOUND)
        self.assertIsInstance(channel, CTwoLiveChannel)
        self.assertEqual(channel.identity, identity())

    def test_a_foreign_harness_request_is_refused_and_an_unregistered_one_answers_none(self):
        handle = self.handle()
        handle.role_run_control["harness"] = "codex"
        self.unavailable(handle)
        with mock.patch.dict(RUN_SEAMS, {"codex": None}):
            self.assertEqual(role_live.handle_live_binding(handle), (role_live.LIVE_UNEXTRACTED, None))


class StoredRequestTests(Fixture, unittest.TestCase):
    def test_a_whole_stored_relationship_verifies_its_complete_identity(self):
        handle = self.handle()
        _, channel = role_live.handle_live_binding(handle)
        self.assertEqual(channel.identity, handle.role_run_identity)
        self.assertIs(role_live.handle_live_binding(handle)[1], channel)

    def test_any_identity_gap_refuses_the_whole_request(self):
        handle = self.handle()
        handle.role_run_control["live"]["instanceId"] = "c"*64
        self.unavailable(handle)
        handle = self.handle()
        handle.pid = 124
        self.unavailable(handle)
        handle = self.handle()
        self.ready_file.write_text("{not json")
        self.unavailable(handle)
        handle = self.handle()
        handle.role_run_control["live"]["readyFile"] = str(self.directory / "absent")
        self.unavailable(handle)

    def test_one_request_identity_component_mismatch_refuses_alone(self):
        for key, value in {"task_id": "other", "attempt_id": "other", "generation": 2,
                           "turn_id": "other", "invocation_id": "other", "input_sha256": "f"*64}.items():
            with self.subTest(component=key):
                handle = self.handle()
                original = request()
                altered = original.model_copy(update={"identity": original.identity.model_copy(update={key: value})})
                self.request_file.write_text(encode_run_request(altered))
                self.unavailable(handle)


class HandleBindingTests(Fixture, unittest.TestCase):
    def test_the_owned_request_binds_the_channel(self):
        handle = self.handle()
        state, channel = role_live.handle_live_binding(handle)
        self.assertEqual(state, role_live.LIVE_BOUND)
        self.assertEqual(channel.identity, identity())
        self.assertNotIn("a"*64, self.ready_file.read_text())
        self.assertNotIn("a"*64, encode_run_request(request()))

    def test_the_binding_states_are_the_three_the_consumer_acts_on(self):
        self.assertEqual(role_live.handle_live_binding(SimpleNamespace()), (role_live.LIVE_UNEXTRACTED, None))
        handle = self.handle()
        handle.role_run_control.pop("live")
        self.unavailable(handle)
        handle = self.handle()
        self.assertEqual(role_live.handle_live_binding(handle)[0], role_live.LIVE_BOUND)

    def test_a_missing_unreadable_or_foreign_request_never_binds(self):
        handle = self.handle()
        handle.role_run_control["requestFile"] = str(self.directory / "absent")
        self.unavailable(handle)
        handle = self.handle()
        self.request_file.write_text("{not json")
        self.unavailable(handle)
        handle = self.handle(held=identity(invocation_id="foreign"))
        self.unavailable(handle)
        handle = self.handle()
        handle.role_run_control["harness"] = "codex"
        self.unavailable(handle)

    def test_each_held_identity_component_and_harness_must_match_the_request(self):
        for key, value in {"task_id": "other", "attempt_id": "other", "generation": 2,
                           "turn_id": "other", "invocation_id": "other", "input_sha256": "f"*64}.items():
            with self.subTest(component=key):
                self.unavailable(self.handle(held=identity().model_copy(update={key: value})))
        handle = self.handle()
        self.request_file.write_text(encode_run_request(request(harness="codex")))
        self.unavailable(handle)

    def stopped(self):
        handle = self.handle()
        handle.process = mock.Mock(pid=123)
        handle.process.poll.return_value = -9
        handle.shutdown_confirmed = mock.Mock(return_value=True)
        return handle

    def test_credential_address_and_domain_mismatch_never_reap(self):
        handle = self.stopped()
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "reaped", "reason": None}) as reap:
            self.credential.address = "ipc://foreign"
            self.unavailable(handle)
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            self.credential.address = "ipc://fixture"
            self.credential.context = SimpleNamespace(platform=self.context.platform,
                namespace_id="foreign-state", root="<OTHER_PRIVATE_ROOT>")
            self.unavailable(handle)
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_not_called()

    def test_unknown_controller_group_never_reaps_endpoint(self):
        handle = self.stopped()
        handle.shutdown_confirmed.return_value = False
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "reaped", "reason": None}) as reap:
            result = role_live.release_live_binding(handle)
        self.assertEqual((result.outcome, result.reason), ("unverified", "vanishing-not-confirmed"))
        reap.assert_not_called()

    def test_unreaped_or_wrong_popen_pid_never_reaps_endpoint(self):
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "reaped", "reason": None}) as reap:
            handle = self.stopped()
            handle.process.poll.return_value = None
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.stopped()
            handle.process.pid = 124
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_not_called()

    def test_release_revalidates_every_identity_instance_pid_and_cached_address(self):
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "reaped", "reason": None}) as reap:
            for field, value in {"task_id": "other", "attempt_id": "other", "generation": 2,
                                 "turn_id": "other", "invocation_id": "other", "input_sha256": "f"*64}.items():
                with self.subTest(component=field):
                    handle = self.stopped()
                    role_live.handle_live_binding(handle)
                    handle.role_run_identity = identity().model_copy(update={field: value})
                    self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.stopped()
            handle.role_run_control["live"]["instanceId"] = "c"*64
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.stopped()
            handle.pid = 124
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.stopped()
            role_live.handle_live_binding(handle)
            handle.role_live_descriptor = handle.role_live_descriptor.model_copy(update={"address": "ipc://other"})
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_not_called()

    def test_exceptional_reap_is_not_retried(self):
        handle = self.stopped()
        with mock.patch.object(cc, "reap_endpoint", side_effect=OSError("native IO failure")) as reap:
            with self.assertRaises(OSError):
                role_live.release_live_binding(handle)
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_called_once()

    def test_a_native_refusal_is_reported_and_never_retried(self):
        handle = self.stopped()
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "busy", "reason": "alive"}) as reap:
            first = role_live.release_live_binding(handle)
            self.assertEqual((first.outcome, first.reason), ("busy", "alive"))
            self.assertIs(role_live.release_live_binding(handle), first)
            reap.assert_called_once()


class ReadyFileBarrierTests(Fixture, unittest.TestCase):
    def test_live_binding_rejects_symlinks_fifos_and_oversized_readiness(self):
        handle = self.handle()
        path = self.directory / "symlink"
        path.symlink_to(self.ready_file)
        handle.role_run_control["live"]["readyFile"] = str(path)
        self.unavailable(handle)
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "fifo"
            os.mkfifo(fifo)
            handle.role_run_control["live"]["readyFile"] = str(fifo)
            self.unavailable(handle)
        handle = self.handle()
        self.ready_file.write_bytes(b" " * 16385)
        self.unavailable(handle)
