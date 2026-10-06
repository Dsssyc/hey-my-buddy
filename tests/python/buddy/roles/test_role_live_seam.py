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
from pathlib import Path
from types import SimpleNamespace

from hey_my_buddy.buddy.harnesses.live import ExistingLiveChannel
from hey_my_buddy.buddy.harnesses.registry import live_binding
from hey_my_buddy.buddy.harnesses.run_contract import (
    FrozenJson,
    NetworkPolicy,
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
        network=NetworkPolicy(requested=False), output_schema=FrozenJson({"type": "object"}),
        budget=RunBudget(timeout_seconds=600, max_output_bytes=1024))


class LiveBindingRegistryTests(unittest.TestCase):
    def test_only_registered_modules_carry_a_live_binding(self):
        self.assertIs(live_binding("zcode"), native_run.bind_live_channel)
        for name in ("dsh", "codex", "claude", "command", "external", "not-a-harness"):
            self.assertIsNone(live_binding(name), name)

    def test_a_channel_binds_the_requests_own_complete_identity(self):
        with tempfile.TemporaryDirectory(prefix="buddy-live-seam-",
                                         dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp")) as directory:
            channel = role_live.build_live_channel("zcode", request(), credentials={},
                                                   activity_dir=directory)
        self.assertIsInstance(channel, ExistingLiveChannel)
        self.assertEqual(channel.identity, request().identity)
        self.assertEqual(channel.capabilities().inquiry_delivery, "cooperative-checkpoint")

    def test_a_foreign_harness_request_is_refused_and_an_unregistered_one_answers_none(self):
        with tempfile.TemporaryDirectory(prefix="buddy-live-seam-x-",
                                         dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp")) as directory:
            with self.assertRaises(BoardError):
                role_live.build_live_channel("codex", request(), credentials={}, activity_dir=directory)
            self.assertIsNone(role_live.build_live_channel("dsh", request(harness="dsh"),
                                                           credentials={}, activity_dir=directory))


class StoredRequestTests(unittest.TestCase):
    """The stored request's complete identity, verified against its own control.

    The fixture writes exactly the relationship the accepted role launch
    writes: the public request, the private control naming the invocation and
    the governed turn input, and that turn input file.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-live-seam-stored-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()

    def write_stored(self, *, invocation="invocation-live", turn_id="turn-live",
                     request_identity=None, control_identity=None) -> Path:
        turn_input = {"taskId": "task", "attemptId": "attempt", "generation": 1,
                      "turnId": turn_id, "resumeMode": "initial", "previousSessionId": None,
                      "context": {}}
        (self.directory / "turn-input.json").write_text(json.dumps(turn_input))
        identity = request_identity or RunIdentity(
            task_id="task", attempt_id="attempt", generation=1, invocation_id=invocation,
            turn_id=turn_id, input_sha256=input_hash(turn_input))
        request = RunRequest(
            identity=identity, harness="zcode",
            configuration=RunConfiguration(provider="fixture-zcode", model="fixture-glm", effort="low"),
            cwd="/private/tmp", private_state=PrivateStatePaths(
                invocation_root=str(self.directory), native_root=str(self.directory / "native")),
            input_text="the governed turn input", tool_scope="write",
            network=NetworkPolicy(requested=False), output_schema=FrozenJson({"type": "object"}),
            budget=RunBudget(timeout_seconds=600, max_output_bytes=1024))
        (self.directory / "role-run-request.json").write_text(encode_run_request(request))
        control = {"operation": "worker", "harness": "zcode", "invocationId": invocation,
                   "inputFile": str(self.directory / "turn-input.json")}
        if control_identity is not None:
            control["invocationId"] = control_identity
        (self.directory / "role-run-control.json").write_text(json.dumps(control))
        return self.directory

    def test_a_whole_stored_relationship_verifies_its_complete_identity(self):
        stored = self.write_stored()
        request = role_live.stored_run_request(stored)
        self.assertIsNotNone(request)
        self.assertEqual(request.identity.invocation_id, "invocation-live")
        self.assertEqual(request.identity.turn_id, "turn-live")
        self.assertEqual(request.identity.input_sha256, decode_run_request(
            (stored / "role-run-request.json").read_bytes()).identity.input_sha256)

    def test_any_identity_gap_refuses_the_whole_request(self):
        mismatched_invocation = self.write_stored(control_identity="another-invocation")
        self.assertIsNone(role_live.stored_run_request(mismatched_invocation))
        foreign_turn = self.write_stored()
        # The stored request carries one turn's digest; the governed input now
        # names another turn, so the stored pair does not verify.
        turn_input = json.loads((foreign_turn / "turn-input.json").read_text())
        turn_input["turnId"] = "other-turn"
        (foreign_turn / "turn-input.json").write_text(json.dumps(turn_input))
        self.assertIsNone(role_live.stored_run_request(foreign_turn))
        incomplete = self.write_stored()
        (incomplete / "role-run-control.json").unlink()
        self.assertIsNone(role_live.stored_run_request(incomplete))
        corrupt = self.write_stored()
        (corrupt / "role-run-request.json").write_text("{not json")
        self.assertIsNone(role_live.stored_run_request(corrupt))
        self.assertIsNone(role_live.stored_run_request(self.directory / "nowhere"))


class HandleBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-live-seam-handle-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.request_file = self.directory / "role-run-request.json"

    def handle(self, *, held=None, control=None):
        request_file = self.request_file
        request_file.write_text(encode_run_request(request()))
        return SimpleNamespace(
            role_run_control=control if control is not None else {
                "operation": "worker", "harness": "zcode", "requestFile": str(request_file),
                "directory": str(self.directory),
                "inquiry": {"socketPath": str(self.directory / "inquiry.sock"),
                            "resultsPath": str(self.directory / "inquiry.results.jsonl"),
                            "token": "a" * 64},
            },
            role_run_identity=held if held is not None else identity())

    def test_the_owned_request_binds_the_channel(self):
        channel = role_live.handle_live_channel(self.handle())
        self.assertIsInstance(channel, ExistingLiveChannel)
        self.assertEqual(channel.identity, identity())
        # No token ever travels in the binding's public materials.
        self.assertNotIn("a" * 64, json.dumps(encode_run_request(request())))

    def test_the_binding_states_are_the_three_the_consumer_acts_on(self):
        unextracted = SimpleNamespace(
            role_run_control={"operation": "worker", "harness": "dsh"},
            role_run_identity=identity())
        self.assertEqual(role_live.handle_live_binding(unextracted),
                         (role_live.LIVE_UNEXTRACTED, None))
        bound_handle = self.handle()
        state, channel = role_live.handle_live_binding(bound_handle)
        self.assertEqual(state, role_live.LIVE_BOUND)
        self.assertIsInstance(channel, ExistingLiveChannel)
        foreign = self.handle(held=identity(invocation_id="another-invocation"))
        self.assertEqual(role_live.handle_live_binding(foreign),
                         (role_live.LIVE_UNAVAILABLE, None))
        absent = self.handle()
        absent.role_run_control["requestFile"] = str(self.directory / "absent.json")
        self.assertEqual(role_live.handle_live_binding(absent),
                         (role_live.LIVE_UNAVAILABLE, None))

    def test_a_missing_unreadable_or_foreign_request_never_binds(self):
        self.assertIsNone(role_live.handle_live_channel(SimpleNamespace(role_run_control=None,
                                                                        role_run_identity=None)))
        absent = self.handle()
        absent.role_run_control["requestFile"] = str(self.directory / "absent.json")
        self.assertIsNone(role_live.handle_live_channel(absent))
        foreign = self.handle(held=identity(invocation_id="another-invocation"))
        self.assertIsNone(role_live.handle_live_channel(foreign))
        changed = self.handle(control={"operation": "worker", "harness": "codex",
                                       "requestFile": str(self.request_file),
                                       "directory": str(self.directory)})
        self.assertIsNone(role_live.handle_live_channel(changed))
        name_only = self.handle()
        name_only.role_run_control = {"operation": "worker", "harness": "zcode"}
        self.assertIsNone(role_live.handle_live_channel(name_only))


if __name__ == "__main__":
    unittest.main()
