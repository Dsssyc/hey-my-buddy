"""Attempt-bound live material between the execution process and its Worker."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import c_two as cc

from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, LiveEndpointDescriptor
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, live_binding
from hey_my_buddy.buddy.harnesses.run_contract import HARNESS_NAMES, RunIdentity
from hey_my_buddy.buddy.roles import live as role_live
from hey_my_buddy.buddy.roles.turn_io import private_json
from hey_my_buddy.protocol import rpc_config


def identity(invocation="invocation-live"):
    return RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                       invocation_id=invocation, turn_id="turn")


class LiveMaterialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hmb-execution-live-",
                                               dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.state = self.directory / "state"
        rpc_config.configure_client(self.state)
        self.addCleanup(cc.shutdown)
        self.context = cc.local_endpoint_context()
        self.credential = SimpleNamespace(address="ipc://fixture", context=self.context)
        self.enterContext(mock.patch.object(cc.EndpointCredential, "from_json", return_value=self.credential))
        self.material = self.directory / "execution-live.json"
        self.descriptor = LiveEndpointDescriptor(address="ipc://fixture", name="Alex",
            instance_id="b" * 64, host_pid=123, endpoint_credential="opaque-native-credential")

    def publish(self, *, run=None, harness="zcode", descriptor=None, token="a" * 64):
        private_json(self.material, {"identity": (run or identity()).to_payload(),
            "harness": harness, "descriptor": (descriptor or self.descriptor).to_payload(), "token": token})

    def handle(self, *, expected=None, harness="zcode", pid=123, publish=True):
        if publish:
            self.publish()
        process = mock.Mock(pid=pid)
        process.poll.return_value = -9
        return SimpleNamespace(pid=pid, process=process,
            execution_live_file=self.material, role_run_identity=expected or identity("execution"),
            role_run_control={"harness": harness}, shutdown_confirmed=mock.Mock(return_value=True))

    def unavailable(self, handle):
        self.assertEqual(role_live.handle_live_binding(handle, state_dir=self.state),
                         (role_live.LIVE_UNAVAILABLE, None))

    def test_registry_exposes_only_registered_harness_live_channels(self):
        for name in HARNESS_NAMES:
            self.assertIs(live_binding(name), CTwoLiveChannel)
        for name in ("command", "external", "not-a-harness"):
            self.assertIsNone(live_binding(name))

    def test_worker_adopts_the_execution_invocation_after_attempt_and_pid_checks(self):
        handle = self.handle()
        state, channel = role_live.handle_live_binding(handle, state_dir=self.state)
        self.assertEqual(state, role_live.LIVE_BOUND)
        self.assertEqual(channel.identity, identity())
        self.assertEqual(handle.role_run_identity, identity())
        self.assertIs(role_live.handle_live_binding(handle, state_dir=self.state)[1], channel)
        self.assertNotIn("a" * 64, self.descriptor.to_payload().__str__())

    def test_summary_turn_replaces_the_endpoint_and_closes_the_previous_channel(self):
        handle = self.handle()
        _, first = role_live.handle_live_binding(handle, state_dir=self.state)
        changed = self.descriptor.model_copy(update={"instance_id": "c" * 64})
        self.publish(run=identity("summary"), descriptor=changed, token="d" * 64)
        with mock.patch.object(first, "close") as close:
            state, second = role_live.handle_live_binding(handle, state_dir=self.state)
        self.assertEqual(state, role_live.LIVE_BOUND)
        self.assertEqual(second.identity, identity("summary"))
        self.assertIsNot(second, first)
        close.assert_called_once_with(reason="role-endpoint-replaced")

    def test_foreign_attempt_turn_harness_pid_or_domain_never_binds(self):
        for field, value in (("task_id", "other"), ("attempt_id", "other"),
                             ("generation", 2), ("turn_id", "other")):
            with self.subTest(field=field):
                self.unavailable(self.handle(expected=identity().model_copy(update={field: value})))
        self.unavailable(self.handle(harness="codex"))
        self.unavailable(self.handle(pid=124))
        self.credential.address = "ipc://other"
        self.unavailable(self.handle())
        self.credential.address = "ipc://fixture"
        self.credential.context = SimpleNamespace(platform=self.context.platform,
            namespace_id="foreign-state", root="<OTHER_PRIVATE_ROOT>")
        self.unavailable(self.handle())

    def test_missing_malformed_linked_or_oversized_material_never_binds(self):
        handle = self.handle(publish=False)
        self.unavailable(handle)
        self.material.write_text("{not json")
        self.unavailable(handle)
        self.publish()
        linked = self.directory / "linked-live.json"
        linked.symlink_to(self.material)
        handle.execution_live_file = linked
        self.unavailable(handle)
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "live-fifo"
            os.mkfifo(fifo)
            handle.execution_live_file = fifo
            self.unavailable(handle)
        handle.execution_live_file = self.material
        self.material.write_bytes(b" " * 32769)
        self.unavailable(handle)

    def test_reaping_requires_the_reaped_owner_and_group_proof(self):
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "reaped", "reason": None}) as reap:
            handle = self.handle()
            handle.shutdown_confirmed.return_value = False
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.handle()
            handle.process.poll.return_value = None
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            handle = self.handle()
            handle.process.pid = 124
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_not_called()

    def test_reaping_revalidates_material_and_never_retries_a_refusal(self):
        handle = self.handle()
        role_live.handle_live_binding(handle, state_dir=self.state)
        changed = self.descriptor.model_copy(update={"address": "ipc://foreign"})
        self.publish(descriptor=changed)
        self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
        handle = self.handle()
        with mock.patch.object(cc, "reap_endpoint", return_value={"status": "busy", "reason": "alive"}) as reap:
            first = role_live.release_live_binding(handle)
            self.assertEqual((first.outcome, first.reason), ("busy", "alive"))
            self.assertIs(role_live.release_live_binding(handle), first)
            reap.assert_called_once()

    def test_exceptional_reap_is_not_retried(self):
        handle = self.handle()
        with mock.patch.object(cc, "reap_endpoint", side_effect=OSError("native IO failure")) as reap:
            with self.assertRaises(OSError):
                role_live.release_live_binding(handle)
            self.assertEqual(role_live.release_live_binding(handle).outcome, "unverified")
            reap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
