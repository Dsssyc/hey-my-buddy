"""The unified zcode run seam itself: a real native connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.zcode.native_run.run`
directly — a real spawned app-server (the fake fixture), a real frozen
:class:`RunRequest`, the role's real observation rules and service binding,
and a :class:`RunResult` read back as facts. Nothing here wraps a prepared
dict; the request goes through session create/configure/subscribe/send and
the result comes from the same native stream the legacy controller observed.
"""
from __future__ import annotations

from contextlib import nullcontext
import dataclasses
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
import weakref
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.run_contract import (
    RunFeedback,
    PrivateStatePaths,
    RunBudget,
    RunBuddy,
    RunContinuation,
    RunIdentity,
    RunRequest,
    SessionService,
)
from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.harnesses.zcode.native_run import (
    SessionServices,
    prepare_session_service,
    run,
)
from hey_my_buddy.buddy.harnesses.zcode.protocol import COOPERATIVE_INQUIRY_NOTE
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.run_observers import FastCorrection, worker_observer
from hey_my_buddy.buddy.roles.structured_call import no_tool_prompt
from hey_my_buddy.buddy.roles.turn_io import input_hash, validate_outcome
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses import c_two_live as ctl

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from buddy.harnesses.zcode.test_zcode_tool_evidence import FakeAppServerTests, SCHEMA
from buddy.runtime.test_windows_process import FakeAPI, FakeProcess
from hey_my_buddy.buddy.runtime import windows_process


_FIXTURE_BRIDGES = weakref.WeakValueDictionary()


def fixture_inquiry_bridge(credentials=None, **kwargs):
    """Actual owner; retained locator is only a test fixture's lookup key."""
    bridge = native_run.make_inquiry_bridge(**kwargs)
    for locator in ((credentials or {}).get("socketPath"), kwargs["journal_path"]):
        if locator:
            _FIXTURE_BRIDGES[str(locator)] = bridge
    return bridge


def fixture_ask(bridge, frame):
    from hey_my_buddy.buddy.harnesses.inquiry_bridge import _owner_response
    return bridge._ask(frame, _owner_response)


def fixture_live_channel(case, identity, *, credentials, journal_path, activity_path, harness="zcode"):
    """Use the actual owner and shared endpoint; both SDK boundaries are local."""
    from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM
    from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, CTwoLiveEndpoint
    from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
    from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
    endpoint = CTwoLiveEndpoint(identity, EXISTING_CAPABILITIES[harness], TEST_CRM,
                               instance_id="c"*64, token="d"*64, state_dir=Path(os.environ["HMB_STATE_DIR"]))
    address = f"fixture-address-{uuid.uuid4().hex}"
    channel = CTwoLiveChannel(identity, TEST_CRM, name="Test Producer", address=address,
                             instance_id="c"*64, token="d"*64, state_dir=Path(os.environ["HMB_STATE_DIR"]))
    # DSH creates its owner when the native turn starts; its endpoint must not
    # acquire a competing fixture consumer before that owner exists.
    bridge = None
    if harness != "dsh":
        locator = credentials.get("socketPath") or journal_path
        bridge = _FIXTURE_BRIDGES.get(str(locator))
        if bridge is None:
            bridge = InquiryBridge(identity={"taskId": identity.task_id, "attemptId": identity.attempt_id,
                "generation": identity.generation, "turnId": identity.turn_id},
                journal_path=journal_path or str(Path(activity_path).parent / "missing-journal"),
                error_factory=native_run.NativeError, event_metadata=lambda _: (None, None),
                limitation=COOPERATIVE_INQUIRY_NOTE, live=endpoint)
            bridge.start()
        else:
            case.assertEqual((bridge.identity["taskId"], bridge.identity["attemptId"], bridge.identity["generation"],
                              bridge.identity["turnId"]),
                             (identity.task_id, identity.attempt_id, identity.generation, identity.turn_id))
            bridge.live = endpoint
            # These producer cases mount the owner before attaching the test
            # endpoint. Run the actual owner consumer after that attachment.
            if bridge.thread is None:
                bridge.thread = threading.Thread(target=bridge._consume_loop, daemon=True)
                bridge.thread.start()
    def call(operation, request_json):
        if bridge is not None:
            bridge._publish()
        return getattr(endpoint, operation)(request_json)
    peer = types.SimpleNamespace(
        capabilities=lambda text: call("capabilities", text),
        request=lambda text: call("request", text),
        observe=lambda text: call("observe", text))
    if not hasattr(case, "_fixture_live_peers"):
        # Several channels coexist in the journal-correlation cases. One SDK
        # boundary routes their distinct fixture addresses to their held peers.
        case._fixture_live_peers = {}
        def connect(*args, address, timeout, **kwargs):
            case.assertGreaterEqual(timeout, 0)
            return nullcontext(case._fixture_live_peers[address])
        def with_call_options(connection, *, timeout):
            case.assertTrue(any(connection is held for held in case._fixture_live_peers.values()))
            case.assertGreaterEqual(timeout, 0)
            return connection
        case.enterContext(mock.patch.object(ctl.cc, "connect", side_effect=connect))
        case.enterContext(mock.patch.object(ctl.cc, "with_call_options", side_effect=with_call_options))
    case._fixture_live_peers[address] = peer
    def close():
        if bridge is not None:
            bridge.close()
        endpoint.close(reason="fixture-finished")
    case.addCleanup(close)
    channel.fixture_endpoint = endpoint
    return channel


FALLBACK_PREFERENCES = {"memoryEnabled": {"value": False, "source": "fallback"},
                        "nativeSearchEnhancementsEnabled": {"value": True, "source": "fallback"}}


class NativeRunCase(FakeAppServerTests):
    """The shared fake-app-server harness for direct fast-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-seam-",
                                                 dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.enterContext(mock.patch.dict(os.environ, self.environment, clear=True))

    def fast_request(self, prompt: str, *, timeout: int = 5) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="task", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                                 generation=1, invocation_id=uuid.uuid4().hex),
            harness="zcode",
            buddy=RunBuddy(provider="fixture-api", model="fixture-model", effort="low"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(invocation_root=str(self.base / f"invocation-{uuid.uuid4().hex[:8]}"),
                                            native_root=str(self.base / f"native-{uuid.uuid4().hex[:8]}")),
            input_text=prompt, tool_scope="none",
            output_schema=SCHEMA,
            budget=RunBudget(timeout_seconds=timeout))


class MockNativeCase(ZcodeFixtureCase):
    """The full mock app-server (MCP finish bridge included) for worker-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-seam-w-",
                                                 dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def worker_request(self, turn_input: dict, *, timeout: int = 10, continuation=None):
        identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
        invocation = self.base / f"worker-invocation-{uuid.uuid4().hex[:8]}"
        mount = prepare_session_service(
            invocation_root=invocation, identity=identity, input_sha256=input_hash(turn_input),
            attention_path=self.base / "attention.json",
            session_tools=worker_services.session_tools(), completion_tool="hey_my_buddy_finish_turn")
        prompt = worker_services.micro_task_prompt("fixture task", turn_input, mount.finish_tool,
                                                 checkpoint_tool=None, answer_tool=None)
        request = RunRequest(
            identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                                 generation=identity["generation"], invocation_id=uuid.uuid4().hex,
                                 turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
            harness="zcode",
            buddy=RunBuddy(provider="fixture-api", model="fixture-model", effort="low"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(invocation_root=str(invocation),
                                            native_root=str(self.base / "worker-native")),
            input_text=prompt, tool_scope="write",
            output_schema=worker_services.OUTCOME_SCHEMA,
            budget=RunBudget(timeout_seconds=timeout),
            continuation=continuation,
            session_services=(SessionService(tool_names=[f"mcp__{mount.server_name}__{name}"
                                                         for name in mount.bare_tools]),))
        services = SessionServices(mount=mount, validate_outcome=validate_outcome, inquiry=None)
        return request, services


class FastSeamTests(NativeRunCase):
    def test_full_scope_without_worker_permission_is_refused_before_native_launch(self):
        request = self.fast_request("fixture prompt").model_copy(update={"tool_scope": "full"})
        with mock.patch.object(native_run, "_run") as native:
            with self.assertRaises(BoardError) as caught:
                run(request, observer=lambda _facts: RunFeedback(action="continue"),
                    services=None, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(caught.exception.details["toolScope"], "full")
        native.assert_not_called()

    def test_a_settled_answer_is_a_final_message_fact_with_role_checked_schema(self):
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        seen = []

        def observer(facts):
            seen.append(dict(facts))
            return correction.observer(facts)

        result = run(self.fast_request(prompt), observer=observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.end.native_exit_code, 0)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.correction_count, 0)
        self.assertEqual(correction.stop_reason, None)
        # The observer's own cumulative statistics carry the unknown-event
        # count; a clean run saw none, so the shared native evidence stays
        # absent instead of an empty shell.
        self.assertTrue(all(facts["unknownEvents"]["total"] == 0 for facts in seen))
        self.assertIsNone(result.native_evidence)
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["toolAllowlist"], [])
        # The sent create parameters stay the requested block, never a native
        # readback projection beside it.
        self.assertNotIn("reported", policy.to_payload())

    def test_one_format_correction_runs_a_second_session_on_the_same_process(self):
        os.environ["HMB_ZCODE_TEST_CASE"] = "correct"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        # Both root sessions are listed as native roots of this one run.
        self.assertEqual(len(result.tool_evidence.value["nativeIdentity"]), 2)
        self.assertEqual(correction.correction_count, 1)

    def test_a_tool_fact_stops_the_run_through_the_observer_feedback(self):
        os.environ["HMB_ZCODE_TEST_CASE"] = "tool"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertTrue(package["events"])

    def test_a_second_correction_feedback_is_executed_and_stays_bounded(self):
        # How often to correct is the role's rule alone; the driver only
        # executes settled feedback under the shared deadline and cancel. A
        # bounded observer that corrects twice proves the second correction
        # really runs — and terminates — instead of looping or being capped.
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        request = self.fast_request(prompt)
        rounds = {"count": 0}

        def observer(facts):
            if facts["settled"] and rounds["count"] < 2:
                rounds["count"] += 1
                return RunFeedback(action="correct", input_text=f"{prompt}\n\ncorrection {rounds['count']}")
            return FastCorrection(SCHEMA, prompt).observer(facts)

        result = run(request, observer=observer, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.correction_count, 2)
        self.assertEqual(rounds["count"], 2)
        # Three fresh root sessions on the one process: the original round and
        # both executed corrections.
        self.assertEqual(len(result.tool_evidence.value["nativeIdentity"]), 3)

    def test_a_write_scope_without_a_service_takes_the_final_message_path(self):
        # The scope only sets the native tool settings; without a bound
        # completion service the run takes the final-message carrier over the
        # session's own default tools, and the effective policy says exactly
        # that. No external review eligibility is implied.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        os.environ["HMB_ZCODE_TEST_CASE"] = "write-final"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        request = self.fast_request(prompt)
        scoped = RunRequest.from_payload(request.to_payload() | {"toolScope": "write"})
        result = run(scoped, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        # The write scope sends no native policy request of its own (the
        # session default tools run); the fact reports exactly that emptiness.
        self.assertIsNotNone(result.effective_policy.tools)
        self.assertIsNone(result.effective_policy.tools.requested)
        self.assertNotIn("reported", result.effective_policy.tools.to_payload())

    def test_a_late_tool_frame_after_settlement_is_the_role_call_not_the_scopes(self):
        # The EOF drain hands a late tool fact to the role observer: a rule that
        # accepts tools (a write scope) keeps the stream complete, and only a
        # rule that stops marks it incomplete — the driver never fails the
        # frame by reading the tool scope itself.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        os.environ["HMB_ZCODE_TEST_CASE"] = "write-post-close"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        request = self.fast_request(prompt)
        scoped = RunRequest.from_payload(request.to_payload() | {"toolScope": "write"})
        result = run(scoped, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.tool_evidence.value["toolCalls"], 1)
        stopping = FastCorrection(SCHEMA, prompt)
        os.environ["HMB_ZCODE_TEST_CASE"] = "post-close-tool"
        stopped = run(self.fast_request(prompt), observer=stopping.observer,
                      services=None, cancelled=lambda: False)
        self.assertEqual(stopped.end.status, "error")
        self.assertEqual(stopped.end.reason_code, "observer-interrupt")

    def test_a_failed_connection_setup_still_stops_the_spawned_child(self):
        # owned_popen succeeded and only the connection setup failed: the
        # spawn helper is the only holder and stops the child itself, and the
        # result reports a child that existed and was confirmed stopped —
        # never a leaked process behind an invalid-native-result.
        from unittest import mock as _mock
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.native_run.NativeConnection",
                         side_effect=OSError("pipe gone")):
            result = run(self.fast_request(prompt), observer=correction.observer,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-native-result")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        # Killing the group proves the process side only; no SDK answered.
        # (The child's own exit code used to ride the stop layer's slimmed
        # fields; the common result no longer carries it, so the surviving
        # facts here are the confirmed group stop and the failure reason.)

    def test_an_unconfirmed_native_group_stop_reports_unknown_not_gone(self):
        # The run module's own stop collection through the real run path: the
        # narrow stop-observation point (ProcessHandle.shutdown_confirmed) is
        # denied confirmation, so the halt still closes stdin, waits and
        # terminates the real fake app-server while the reported group state
        # stays the honest unknown — a factually reaped leader never becomes
        # confirmed-gone evidence, and the settlement is refused without it.
        from unittest import mock as _mock
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        observed: list[ProcessHandle] = []

        def unconfirmed(handle, settle_seconds: float = 2.0) -> bool:
            observed.append(handle)
            return False

        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        with _mock.patch.object(ProcessHandle, "shutdown_confirmed", unconfirmed):
            result = run(self.fast_request(prompt), observer=correction.observer,
                         services=None, cancelled=lambda: False)
        native = result.stop_evidence.native
        self.assertEqual(native.group_state, "unknown")
        self.assertIsNotNone(result.end.native_exit_code)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        # The halt really signalled: the pre-terminate check and the final
        # confirmation were both denied for this run's own one handle.
        self.assertEqual(len(observed), 2)
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "owned-group-signal")
        # No orphan: with the patch gone, the run's own captured handle shows
        # the leader reaped and the whole group confirmed gone by the real
        # observation the halt performed.
        handle = observed[0]
        self.assertIsNotNone(handle.process.poll())
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.5))
        self.assertFalse(handle.group_alive())

    def test_the_peer_receives_the_refusal_before_the_observer_stop_ends_the_run(self):
        # The refusal answer completes first — the peer reads the reply with
        # the same id and records it — and only then does the role's stop take
        # effect on the driver's next boundary, with no settlement behind it.
        os.environ["HMB_ZCODE_TEST_CASE"] = "reverse-request"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        started = time.monotonic()
        result = run(self.fast_request(prompt, timeout=8), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertLess(time.monotonic() - started, 6, "the run waited instead of stopping on the denied fact")
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        marker = json.loads((self.cwd / "refusal-reply.json").read_text())
        self.assertEqual(marker, {"id": "srv-1", "refused": True},
                         "the native peer never received the same-id refusal")
        # The stop requested an interrupt, no signal was actually sent, and no
        # native acknowledgement exists — the basis names the requester.
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "observer-request")

    def test_a_reverse_request_before_the_rpc_reply_stops_after_the_refusal(self):
        os.environ["HMB_ZCODE_TEST_CASE"] = "reverse-before-send-reply"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        started = time.monotonic()
        result = run(self.fast_request(prompt, timeout=8), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertLess(time.monotonic() - started, 6, "the RPC waited for a reply after the refusal")
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        # The refused interaction's own full record travels in the retained
        # evidence reference, read back through its size and digest.
        ref = next(ref for ref in result.evidence_refs if ref.kind == "denied-interactions")
        raw = Path(ref.location).read_bytes()
        self.assertEqual(len(raw), ref.size_bytes)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref.sha256)
        records = json.loads(raw)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["method"], "interaction/requestPermission")
        self.assertEqual(json.loads((self.cwd / "refusal-reply.json").read_text()),
                         {"id": "srv-before-reply", "refused": True})
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "owned-group-signal")

    def test_a_late_foreign_tool_frame_reaches_the_role_before_the_protocol_check(self):
        # A late tool frame from a child session keeps its projected fact and
        # reaches the role observer first: the fast rule stops with its own
        # reason instead of the protocol layer's foreign-session code.
        os.environ["HMB_ZCODE_TEST_CASE"] = "post-close-foreign"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        self.assertFalse(result.tool_evidence.value["streamComplete"])
        self.assertTrue(result.tool_evidence.value["events"])

    def test_a_failed_close_keeps_the_final_message_facts(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        os.environ["HMB_ZCODE_TEST_CASE"] = "close-fail"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        request = self.fast_request(prompt)
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "session-close-unconfirmed")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        # The value belongs to the observed root session's own native turn.
        package = result.tool_evidence.value
        self.assertEqual(result.native_identity.turn_id,
                         next(root["turnId"] for root in package["nativeIdentity"]
                              if root.get("sessionId") == result.native_identity.session_id))

    def test_a_reverse_request_ends_the_fast_run_immediately_with_the_fact_kept(self):
        # The refused interaction is answered and recorded, and the role's stop
        # takes effect on that same pump turn: no settlement, no later unknown
        # event and no timeout wait may be needed to end the run.
        for case in ("reverse-request", "reverse-oauth"):
            with self.subTest(case=case):
                os.environ["HMB_ZCODE_TEST_CASE"] = case
                prompt = no_tool_prompt("Choose a buddy", SCHEMA)
                correction = FastCorrection(SCHEMA, prompt)
                started = time.monotonic()
                result = run(self.fast_request(prompt, timeout=8), observer=correction.observer,
                             services=None, cancelled=lambda: False)
                self.assertLess(time.monotonic() - started, 6,
                                "the run waited instead of stopping on the denied fact")
                self.assertEqual(result.end.status, "cancelled")
                self.assertEqual(result.end.reason_code, "observer-interrupt")
                self.assertEqual(correction.stop_reason, "no-tool-violation")
                ref = next(ref for ref in result.evidence_refs if ref.kind == "denied-interactions")
                raw = Path(ref.location).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), ref.sha256)
                records = json.loads(raw)["records"]
                self.assertEqual(len(records), 1)
                self.assertIn("interaction/", records[0]["method"])

    def test_a_pre_spawn_failure_claims_nothing_that_never_happened(self):
        from unittest import mock as _mock
        from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError as _NativeError
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a buddy", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.native_run._spawn_app_server",
                         side_effect=_NativeError("harness-unavailable", "no native start")):
            result = run(self.fast_request(prompt), observer=correction.observer,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "harness-unavailable")
        # Nothing is claimed that the run never reached: no session opened, no
        # effective policy, no buddy check, no completion, no model,
        # and no native exit code — the holding side's own never-spawned fact.
        self.assertIsNone(result.model_started)
        self.assertIsNone(result.effective_policy.tools)
        checked = result.buddy.checked
        self.assertIsNone(checked.provider)
        self.assertIsNone(checked.model)
        self.assertIsNone(checked.effort)
        self.assertIsNone(result.completion_evidence)
        self.assertIsNone(result.native_event_count)
        self.assertIsNone(result.end.native_exit_code)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_the_role_rule_corrects_only_once_and_never_for_an_enum(self):
        # The at-most-once and enum decisions live in FastCorrection: after one
        # correction a still-invalid settle continues, and an enum violation is
        # never corrected at all.
        schema = SCHEMA
        correction = FastCorrection(schema, "base prompt")
        first = correction.observer({"settled": True, "rawAnswer": "bad JSON",
                                     "toolCalls": 0, "toolMarkerFrames": 0,
                                     "unknownEvents": {"total": 0}, "deniedInteractions": 0})
        self.assertEqual((first.action, correction.correction_count), ("correct", 1))
        second = correction.observer({"settled": True, "rawAnswer": "still bad",
                                      "toolCalls": 0, "toolMarkerFrames": 0,
                                      "unknownEvents": {"total": 0}, "deniedInteractions": 0})
        self.assertEqual(second.action, "continue")
        self.assertEqual(correction.correction_count, 1)
        enum_rule = FastCorrection(schema, "base prompt")
        enum = enum_rule.observer({"settled": True, "rawAnswer": '{"choice":"b"}',
                                   "toolCalls": 0, "toolMarkerFrames": 0,
                                   "unknownEvents": {"total": 0}, "deniedInteractions": 0})
        self.assertEqual(enum.action, "continue")
        self.assertEqual(enum_rule.correction_count, 0)


def evidence_call(result):
    """The verified delivery call's own identity, from the retained provenance."""
    provenance = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                      if ref.kind == "turn-provenance")).read_bytes())
    return provenance["toolCallId"]


class WorkerSeamTests(MockNativeCase):
    def test_worker_levels_keep_the_micro_task_carrier_and_record_the_sent_mode(self):
        from hey_my_buddy.buddy.harnesses.registry import worker_permission
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        original_open = native_run._open_root
        for level in ("read", "write", "full"):
            with self.subTest(level=level):
                request, services = self.worker_request(self.turn_input())
                permission = worker_permission("zcode", level)
                request = request.model_copy(update={"tool_scope": permission.tool_scope,
                                                     "worker_permission": permission})
                with mock.patch.object(native_run, "_open_root", wraps=original_open) as opened:
                    result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                native_mode = "plan" if level == "read" else "yolo"
                self.assertEqual(opened.call_args.kwargs["create_params"],
                                 {"mode": native_mode, "mcpServers": list(services.mount.mcp_servers)})
                self.assertEqual(result.effective_policy.tools.requested.value, {"mode": native_mode})
                self.assertEqual(result.value.schema_status, "valid")
                self.assertEqual(result.value.parsed.value["summary"], "fixture work completed")
                self.assertEqual(permission.enforcement, "convention" if level == "write" else "enforced")

    def resume_with_levels(self, first_level, second_level, *, case="ok"):
        from hey_my_buddy.buddy.harnesses.registry import worker_permission
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        request, services = self.worker_request(self.turn_input())
        permission = worker_permission("zcode", first_level)
        request = request.model_copy(update={"tool_scope": permission.tool_scope, "worker_permission": permission})
        first = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(first.end.status, "ok", first.end.message)
        session = first.native_identity.session_id
        request, services = self.worker_request(self.turn_input(previous=session),
            continuation=RunContinuation(mode="native-session", previous_session_id=session))
        permission = worker_permission("zcode", second_level)
        request = request.model_copy(update={"tool_scope": permission.tool_scope, "worker_permission": permission})
        os.environ["HMB_ZCODE_TEST_CASE"] = case
        calls, mode_readbacks = [], []
        original = native_run.NativeConnection.call

        def call(connection, method, params):
            calls.append((method, params))
            result = original(connection, method, params)
            if method in ("session/resume", "session/setMode"):
                mode_readbacks.append((method, result["settings"]["mode"]))
            return result

        with mock.patch.object(native_run.NativeConnection, "call", call):
            second = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        return first, second, calls, mode_readbacks

    def test_usage_fixture_resumes_with_native_mode_readback_and_only_new_usage(self):
        from hey_my_buddy.protocol.usage import normalize_token_usage
        fixture = Path(__file__).parent / "fixtures/mock_zcode_usage.py"
        expected = json.loads(fixture.with_name("native-usage-zcode.json").read_text())["expectedMessages"]
        with mock.patch.dict(self.environment, {"HMB_ZCODE_CLI": str(fixture)}):
            for first_level, second_level, before, after in (
                    ("read", "write", "build", "yolo"),
                    ("write", "read", "yolo", "yolo"),
                    ("read", "read", "build", "build")):
                with self.subTest(first=first_level, second=second_level), mock.patch.object(
                        self, "base", self.base / f"usage-{first_level}-{second_level}"):
                    first, second, calls, readbacks = self.resume_with_levels(
                        first_level, second_level, case="resume-ok")
                    self.assertEqual(second.end.status, "ok", second.end.message)
                    self.assertEqual(second.native_identity.session_id, first.native_identity.session_id)
                    mode = "plan" if second_level == "read" else "yolo"
                    self.assertIn(("session/setMode", {"sessionId": first.native_identity.session_id,
                                                       "mode": mode}), calls)
                    self.assertEqual(readbacks, [("session/resume", {"current": before}),
                                                ("session/setMode", {"current": after})])
                    token_usage = normalize_token_usage(second.usage.value)
                    self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
                    self.assertEqual(token_usage["nativeRecords"], expected["records"])
                    self.assertEqual(token_usage["completeness"], "complete")

    def check_resumed_level(self, first_level, second_level, native_mode):
        first, second, calls, readbacks = self.resume_with_levels(first_level, second_level,
            case="mode-plan-required" if second_level == "read" else "ok")
        self.assertEqual(second.end.status, "ok", second.end.message)
        self.assertEqual(second.native_identity.session_id, first.native_identity.session_id)
        self.assertEqual([params for method, params in calls if method == "session/setMode"],
                         [{"sessionId": first.native_identity.session_id, "mode": native_mode}])
        methods = [method for method, _ in calls]
        self.assertLess(methods.index("session/resume"), methods.index("session/setMode"))
        self.assertLess(methods.index("session/setMode"), methods.index("session/setModel"))
        self.assertLess(methods.index("session/setMode"), methods.index("session/send"))
        self.assertEqual(second.effective_policy.tools.requested.value, {"mode": native_mode})
        self.assertEqual(second.value.schema_status, "valid")
        underlying = "build" if first_level == "read" else "yolo"
        self.assertEqual(readbacks, [("session/resume", {"current": underlying}),
                                   ("session/setMode", {"current": underlying if native_mode == "plan" else "yolo"})])
        if native_mode == "plan":
            self.assertEqual(dict(second.native_evidence.unknown_events.counts_by_type)
                             ["permission-mode-readback-unconfirmed"], 1)

    def test_read_to_write_resume_sets_yolo_on_the_same_session(self):
        self.check_resumed_level("read", "write", "yolo")

    def test_write_to_read_resume_sets_plan_on_the_same_session(self):
        self.check_resumed_level("write", "read", "plan")

    def test_read_to_read_resume_reapplies_plan_despite_build_readback(self):
        self.check_resumed_level("read", "read", "plan")

    def test_resume_mode_mismatch_fails_before_model_input_and_retains_requested_mode(self):
        _first, second, calls, _readbacks = self.resume_with_levels("read", "write", case="mode-mismatch")
        self.assertEqual(second.end.reason_code, "configuration-mismatch")
        self.assertEqual(second.effective_policy.tools.requested.value, {"mode": "yolo"})
        self.assertNotIn("session/send", [method for method, _ in calls])
        self.assertEqual(second.stop_evidence.native.group_state, "gone")

    def test_unrecognized_resume_mode_is_a_fact_and_the_turn_can_complete(self):
        _first, second, _calls, _readbacks = self.resume_with_levels("read", "write", case="mode-unrecognized")
        self.assertEqual(second.end.status, "ok", second.end.message)
        self.assertEqual(dict(second.native_evidence.unknown_events.counts_by_type)["permission-mode-readback-unconfirmed"], 1)
        self.assertEqual(second.effective_policy.tools.requested.value, {"mode": "yolo"})

    def test_resume_mode_readback_from_another_session_is_refused(self):
        _first, second, calls, _readbacks = self.resume_with_levels("write", "read", case="mode-foreign-session")
        self.assertEqual(second.end.reason_code, "wrong-native-session")
        self.assertNotIn("session/send", [method for method, _ in calls])

    def turn_input(self, *, previous=None, mode=None):
        return {"version": 1, "taskId": "micro-task-1", "attemptId": f"attempt-{uuid.uuid4().hex[:8]}",
                "generation": 1, "turnId": "turn-1",
                "resumeMode": mode or ("native-session" if previous else "initial"),
                "previousSessionId": previous, "context": {}, "executionWorkspace": {}}

    def test_native_events_reach_the_activity_callback_with_original_counts_and_order(self):
        from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
        from hey_my_buddy.protocol.activity import is_newer
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "task-tool"
        request, services = self.worker_request(self.turn_input())
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["zcode"], TEST_CRM, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        services = dataclasses.replace(services, live=endpoint)
        seen = []
        original = endpoint.publish_activity

        def publish(payload):
            seen.append(dict(payload))
            return original(payload)

        with mock.patch.object(endpoint, "publish_activity", side_effect=publish):
            result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        phases = [payload["phase"] for payload in seen]
        self.assertEqual(phases[0], "waiting-model")
        self.assertIn("streaming-model", phases)
        self.assertIn("tool-running", phases)
        self.assertEqual(phases[-1], "finishing")
        self.assertTrue(all(is_newer(new, old) for old, new in zip(seen, seen[1:])))
        self.assertEqual(seen[-1]["counts"], {"modelTurns": 1, "toolCalls": 2})
        self.assertEqual(result.activity.value["counts"], seen[-1]["counts"])
        self.assertEqual(result.tool_evidence.value["toolCalls"], 1,
                         "native activity still counts the verified delivery call")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        self.assertFalse(any(self.base.rglob("activity.json")))

    def test_no_live_endpoint_keeps_the_native_activity_final_fact(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "task-tool"
        request, services = self.worker_request(self.turn_input())
        self.assertIsNone(services.live)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 1, "toolCalls": 2})
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertFalse(any(self.base.rglob("activity.json")))

    def test_a_refused_activity_callback_keeps_transport_unknown_and_native_facts(self):
        from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "task-tool"
        request, services = self.worker_request(self.turn_input())
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["zcode"], TEST_CRM, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        services = dataclasses.replace(services, live=endpoint)
        with mock.patch.object(endpoint, "publish_activity", return_value=False) as publish:
            result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertGreater(publish.call_count, 0)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 1, "toolCalls": 2})
        self.assertIsNone(endpoint._activity, "a refusal never proves a live publication")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_one_unrepresentable_package_keeps_every_other_observed_fact(self):
        # The usage package alone is unrepresentable; the run's identity,
        # value, receipt, buddy and stop facts all stay, and the end
        # is not rewritten — the fallback never blankets knowns into unknowns.
        from unittest import mock as _mock
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.protocol.ZcodeAttemptUsage.raw_usage",
                         return_value={"inputTokens": "not-a-number"}):
            result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        # The verified finish receipt is the provenance's own fact, read back
        # through the retained reference.
        provenance = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "turn-provenance")).read_bytes())
        self.assertTrue(provenance["receiptVerified"])
        self.assertEqual(result.buddy.checked.model.value, "fixture-model")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_a_post_configure_failure_keeps_every_reached_stage_fact(self):
        import os as _os
        _os.environ.update(self.environment)
        _os.environ["HMB_ZCODE_TEST_CASE"] = "wrong-input"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["zcode"], TEST_CRM, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        services = dataclasses.replace(services, live=endpoint)
        with mock.patch.object(endpoint, "publish_activity", wraps=endpoint.publish_activity) as publish:
            result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "wrong-native-turn")
        # Configure ran and the readback confirmed; the session opened; the
        # input was sent. Each reached stage keeps its fact in the failure: the
        # readback values, the session-create fact and the model start.
        self.assertEqual(result.buddy.checked.model.value, "fixture-model")
        self.assertIsNotNone(result.effective_policy.tools)
        self.assertTrue(result.model_started)
        self.assertIsNone(result.completion_evidence)
        self.assertIsNotNone(result.end.message)

        self.assertGreater(publish.call_count, 0)
        self.assertEqual(publish.call_args_list[0].args[0]["phase"], "waiting-model")
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 0, "toolCalls": 0},
                         "the invalid turn never supplies a verified native activity count")
        self.assertEqual(result.activity.value["phase"], "waiting-model")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_a_micro_task_turn_settles_with_a_verified_completion_tool_value(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.schema_status, "valid")
        self.assertEqual(result.value.parsed.value["summary"], "fixture work completed")
        self.assertTrue(result.continuation.resumable)
        # The mock exports no message-boundary token records, so usage stays an
        # honest absent fact here; the adapter-path suites cover the real shape.
        self.assertIsNone(result.usage)
        self.assertEqual(result.buddy.checked.model.value, "fixture-model")
        # The micro task turn's evidence parts are retained for the role's record;
        # the verified finish receipt and its call identity travel in the
        # provenance, read back through the size- and digest-checked reference.
        kinds = {ref.kind for ref in result.evidence_refs}
        self.assertIn("turn-provenance", kinds)
        ref = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
        raw = Path(ref.location).read_bytes()
        self.assertEqual(len(raw), ref.size_bytes)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref.sha256)
        provenance = json.loads(raw)
        self.assertEqual(provenance["settlement"], "session-closed")
        self.assertTrue(provenance["receiptVerified"])
        self.assertIsNotNone(provenance["receiptId"])
        self.assertIsNotNone(provenance["toolCallId"])
        self.assertEqual(result.value.parsed.value["summary"], "fixture work completed")
        # The mechanical binding published the bridge configuration the MCP read.
        bridge = json.loads(Path(services.mount.bridge_config_path).read_bytes())
        self.assertEqual(bridge["identity"]["attemptId"], turn_input["attemptId"])

    def test_native_resume_reuses_the_bound_session_through_the_request_continuation(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        first_input = self.turn_input()
        request, services = self.worker_request(first_input)
        first = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(first.end.status, "ok")
        session = first.native_identity.session_id
        second_input = self.turn_input(previous=session)
        request, services = self.worker_request(
            second_input,
            continuation=RunContinuation(mode="native-session", previous_session_id=session))
        second = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(second.end.status, "ok")
        self.assertEqual(second.native_identity.session_id, session)
        self.assertTrue(second.continuation.resumable)

    def test_a_failed_close_keeps_the_observed_completion_tool_facts(self):
        # The value was already taken through the verified completion-tool
        # receipt; the unacknowledged close fails the run but never rewrites
        # the mechanism or erases the outcome, call and receipt facts.
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "close-failed"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "session-close-unconfirmed")
        self.assertEqual(result.value.schema_status, "valid")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        self.assertIsNotNone(result.native_identity.turn_id)
        # No role record is publishable from a failed close.
        self.assertEqual({ref.kind for ref in result.evidence_refs},
                         {"inquiry-report", "native-stderr", "denied-interactions", "attention-report", "native-runtime-preferences"} & {ref.kind for ref in result.evidence_refs})

    def test_the_request_refuses_mismatched_service_descriptions(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        broken = request.model_copy(update={"session_services": (
            SessionService(tool_names=["mcp__other__hey_my_buddy_finish_turn"]),)})
        with self.assertRaises(BoardError):
            run(broken, observer=worker_observer, services=services, cancelled=lambda: False)

    def test_foreign_same_name_finish_calls_stay_facts_only_verified_delivery_leaves(self):
        # The Host's refusal-argument scenario through the real run: a child
        # relay and a foreign session both call a finish-named tool before the
        # root's own verified refusal and final receipt. The foreign calls are
        # not this run's delivery evidence — they stay task-tool facts; only
        # the root calls whose results the root-turn evidence actually
        # verified (the signed refusal and the accepted receipt) leave the
        # package.
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "refusal-argument"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        package = result.tool_evidence.value
        call_ids = [event["callId"] for event in package["events"]]
        self.assertIn("child-fin", call_ids, "the child relay's same-name call was silently dropped")
        self.assertIn("foreign-fin", call_ids, "the foreign session's same-name call was silently dropped")
        self.assertNotIn("call-finish-first", call_ids,
                         "the root's verified refusal call is delivery evidence, not a task tool")
        self.assertNotIn("root-call", call_ids,
                         "the root's verified receipt call is delivery evidence, not a task tool")
        self.assertGreaterEqual(package["toolCalls"], 2)
        self.assertTrue(package["streamComplete"])
        # The completion facts remain their own evidence beside the package: the
        # signed receipt the root's final call actually received is read back
        # from the retained provenance.
        provenance = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "turn-provenance")).read_bytes())
        self.assertTrue(provenance["receiptVerified"])
        self.assertEqual(provenance["toolCallId"], "call-finish-final")

    def test_every_carrier_reports_task_tool_facts_with_delivery_kept_separate(self):
        # A completion-tool run projects its task tools like any other run:
        # the Bash call is a fact, the mounted finish call is not a task tool
        # (its evidence is the verified receipt), the root identity comes from
        # the verified turn, and the native activity projection keeps its own original
        # counts — a separate fact from this package.
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "task-tool"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["callId"] for event in package["events"]], ["call-task", "call-task"])
        self.assertEqual([event["phase"] for event in package["events"]], ["start", "end"])
        self.assertEqual(package["nativeIdentity"], [{"sessionId": result.native_identity.session_id,
                                                      "turnId": result.native_identity.turn_id}])
        # The delivery call is evidenced by the receipt, never by the package;
        # the verified receipt is the provenance's own fact.
        self.assertNotIn(evidence_call(result), [event["callId"] for event in package["events"]])
        provenance = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "turn-provenance")).read_bytes())
        self.assertTrue(provenance["receiptVerified"])
        self.assertEqual(provenance["toolCallId"], evidence_call(result))

    def test_a_request_schema_differring_from_the_mounted_contract_is_refused(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        # The completion mechanism serves exactly the role session-tool
        # contract; a different requested schema is refused before any native
        # work, so a verified receipt can never stand in for another schema.
        payload = request.to_payload()
        payload["outputSchema"] = {"type": "object", "additionalProperties": False,
                                   "required": ["answer"], "properties": {"answer": {"type": "string"}}}
        foreign = RunRequest.from_payload(payload)
        with self.assertRaises(BoardError) as caught:
            run(foreign, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn("mounted completion contract", caught.exception.message)

    def test_a_read_scope_reports_unrestricted_honestly(self):
        os.environ.update(self.environment)
        os.environ["HMB_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        scoped = request.model_copy(update={"tool_scope": "read"})
        result = run(scoped, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        # The read scope sends no native policy request (the session default
        # tools run); the fact reports exactly that, with no look-alike
        # "unrestricted" verdict of its own.
        self.assertIsNotNone(result.effective_policy.tools)
        self.assertIsNone(result.effective_policy.tools.requested)


class NoToolReverseRequestTests(unittest.TestCase):
    """A refused native interaction is a fact for the role, not a pump failure."""

    def test_a_no_tool_reverse_request_is_answered_recorded_and_survives_pump(self):
        import subprocess
        import sys
        import threading
        import time
        from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeConnection
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def reap():
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()

        self.addCleanup(reap)
        recorded = []
        connection = NativeConnection(process, time.monotonic() + 10, threading.Event(), no_tools=True, runtime_preferences=FALLBACK_PREFERENCES)
        connection.attention = recorded.append
        connection.messages.put({"id": 7, "method": "interaction/requestPermission",
                                 "params": {"sessionId": "s", "kind": "shell"}})
        try:
            connection.pump()
        except Exception as error:  # noqa: BLE001 - the pump must not raise here
            self.fail(f"a no-tool reverse request raised out of the pump: {error!r}")
        self.assertEqual(recorded[0]["method"], "interaction/requestPermission")
        self.assertEqual(recorded[0]["outcome"], "refused-with-jsonrpc-error")

    def test_provider_headers_are_an_unsupported_host_capability_not_a_provider_ban(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        def reap():
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
        self.addCleanup(reap)
        connection = native_run.NativeConnection(process, time.monotonic() + 10, threading.Event(), runtime_preferences=FALLBACK_PREFERENCES)
        for no_tools in (False, True):
            with self.subTest(no_tools=no_tools):
                recorded = []
                connection.no_tools = no_tools
                connection.attention = recorded.append
                connection.send = mock.Mock()
                connection.messages.put({"id": 7, "method": "interaction/requestProviderRuntimeHeaders",
                                         "params": {"sessionId": "s", "providerId": "fixture-account"}})
                connection.pump()
                self.assertEqual(recorded[0]["method"], "interaction/requestProviderRuntimeHeaders")
                self.assertEqual(recorded[0]["outcome"], "refused-with-jsonrpc-error")
                self.assertEqual(connection.send.call_args.args[0]["error"]["code"], -32601)


class ProducerContractTests(NativeRunCase):
    def test_inquiry_factory_passes_the_controller_endpoint_without_socket_credentials(self):
        endpoint = object()
        with mock.patch.object(native_run, "InquiryBridge") as constructor:
            bridge = native_run.make_inquiry_bridge(
                identity={"taskId": "task", "attemptId": "attempt", "generation": 1},
                journal_path="<private-journal>", live=endpoint)
        self.assertIs(bridge, constructor.return_value)
        self.assertIs(constructor.call_args.kwargs["live"], endpoint)
        self.assertEqual(constructor.call_args.args, ())
        self.assertNotIn("credentials", constructor.call_args.kwargs)

    def test_activity_publisher_uses_real_normalization_order_and_throttle(self):
        from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
        from hey_my_buddy.protocol.activity import ActivityPublisher
        request = self.fast_request("fixture")
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["zcode"], TEST_CRM, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        now = [0.0]
        callback = mock.Mock(wraps=endpoint.publish_activity)
        publisher = ActivityPublisher(callback, clock=lambda: now[0])
        first = {"phase": "streaming-model", "eventSeq": 1, "counts": {"modelTurns": 1}}
        self.assertTrue(publisher.publish(first))
        self.assertFalse(publisher.publish(first))
        self.assertFalse(publisher.publish({**first, "eventSeq": 0}))
        self.assertFalse(publisher.publish({**first, "eventSeq": 2}))
        self.assertTrue(publisher.publish({"phase": "tool-running", "eventSeq": 3,
                                           "counts": {"modelTurns": 1, "toolCalls": 1}}))
        now[0] = 2.0
        self.assertTrue(publisher.publish({"phase": "tool-running", "eventSeq": 4,
                                           "counts": {"modelTurns": 1, "toolCalls": 1}}))
        self.assertEqual(callback.call_count, 3)
        with self.assertRaises(BoardError):
            publisher.publish({"phase": "finishing", "eventSeq": 5, "prompt": "private"})
        self.assertEqual(callback.call_count, 3)

    def test_the_shared_endpoint_receives_ask_activity_and_journal(self):
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        from hey_my_buddy.protocol import activity as activity_protocol
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        journal = temp / "inquiry.results.jsonl"
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = make_inquiry_bridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                       "generation": 1, "turnId": "turn-live"},
                               journal_path=str(journal))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=str(journal),
                                    activity_path=temp / "activity.json")
        self.assertIsInstance(channel, CTwoLiveChannel)
        self.assertEqual(channel.capabilities().inquiry_delivery, "cooperative-checkpoint")
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        reply = channel.request(LiveRequest(identity=identity, request_id="q-1", kind="inquiry",
                                            payload=InquiryPayload(question_id="i-1",
                                                                   question="is the binding real?")),
                                timeout_ms=1000)
        self.assertEqual(reply.status, "queued")
        # Activity uses the shared normalizer/throttle and actual endpoint
        # callback; no activity file is a live transport source.
        publisher = activity_protocol.ActivityPublisher(channel.fixture_endpoint.publish_activity)
        self.assertTrue(publisher.publish({"phase": "streaming-model", "eventSeq": 3,
                                           "counts": {"modelTurns": 1, "toolCalls": 0}}))
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=1000)
        self.assertEqual(snapshot.activity.value["eventSeq"], 3)
        self.assertEqual(snapshot.inquiries[0].question_id, "i-1")
        self.assertEqual(snapshot.inquiries[0].status, "queued")
        # The durable journal is the same bound record the bridge wrote.
        records = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        self.assertEqual(records[0]["inquiryId"], "i-1")
        self.assertEqual(records[0]["state"], "queued")

    def test_the_answered_journal_chain_yields_the_verified_answer_text(self):
        # A real bridge journal — ask through the socket, deliver and answer
        # through the controller-authority methods that write the very records
        # — replays through the channel with the verified answer text, not a
        # null where the structured answer object stood.
        import hashlib as _hashlib
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-a-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        journal = temp / "inquiry.results.jsonl"
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge_identity = {"taskId": "task", "attemptId": "attempt-live",
                           "generation": 1, "turnId": "turn-live"}
        bridge = make_inquiry_bridge(credentials, identity=bridge_identity, journal_path=str(journal))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        asked = fixture_ask(bridge, {"version": 1, "id": "r-1", "token": credentials["token"],
                               "method": "ask", "inquiryId": "i-1", "question": "what is the answer?"})
        self.assertTrue(asked["ok"], asked)
        digest = _hashlib.sha256("what is the answer?".encode()).hexdigest()
        bridge.deliver_inquiries({"inquiries": [{"inquiryId": "i-1", "questionSha256": digest}]},
                                 "call-checkpoint")
        bridge.record_answer({"inquiryId": "i-1", "questionSha256": digest,
                              "answer": "the verified answer"}, "call-answer")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=str(journal),
                                    activity_path=temp / "activity.json")
        reply = channel.request(LiveRequest(identity=identity, request_id="q-1", kind="inquiry",
                                            payload=InquiryPayload(question_id="i-1",
                                                                   question="what is the answer?")),
                                timeout_ms=1000)
        self.assertEqual(reply.status, "answered")
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=1000)
        self.assertEqual(snapshot.inquiries[0].status, "answered")
        self.assertEqual(snapshot.inquiries[0].answer, "the verified answer")
        # The journal file itself keeps the structured answer with its source
        # metadata — the channel only derives the text from it.
        raw = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        answered = next(record for record in raw if record["state"] == "answered")
        self.assertEqual(answered["answer"]["text"], "the verified answer")
        self.assertEqual(answered["answer"]["toolCallId"], "call-answer")

    def test_foreign_activity_and_journal_records_read_as_nothing(self):
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-f-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        journal = temp / "inquiry.results.jsonl"
        # The journal's identity gate refuses foreign records before they
        # become endpoint publications. Activity uses the strict callback.
        # Identity is carried by the endpoint envelope; a payload that tries
        # to smuggle a foreign binding is rejected by the public normalizer.
        foreign = {"version": 1, "taskId": "task", "attemptId": "other", "generation": 1,
                   "turnId": "turn-live", "inquiryId": "i-foreign", "state": "queued",
                   "question": "not this attempt", "questionSha256": "b" * 64, "askedAt": "now"}
        torn = json.dumps({"version": 2, "inquiryId": "i-old", "state": "queued"})
        journal.write_text(json.dumps(foreign) + "\n" + torn + "\n")
        channel = bind_live_channel(identity, credentials={"socketPath": "", "token": "a" * 64},
                                    journal_path=str(journal), activity_path=temp / "activity.json")
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=100)
        self.assertIsNone(snapshot.activity)
        self.assertEqual(snapshot.inquiries, ())
        with self.assertRaises(BoardError):
            channel.fixture_endpoint.publish_activity({"phase": "streaming-model", "eventSeq": 9,
                                                       "attemptId": "other"})

    def test_journal_states_follow_the_direct_reader_semantics(self):
        """Empty is available, over-limit and unreadable keep their own reasons."""
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-j-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        journal = temp / "inquiry.results.jsonl"
        channel = bind_live_channel(identity, credentials={"socketPath": "", "token": "a" * 64},
                                    journal_path=str(journal), activity_path=temp / "activity.json")

        def fact():
            return channel.observe(after_seq=None, limit=8, timeout_ms=200,
                                   fields=("inquiries",)).journal

        # Nothing written yet: the direct reader's missing reason.
        self.assertEqual((fact().available, fact().reason, fact().entries),
                         (False, "journal-not-written", 0))
        # An empty existing file is a real, readable journal: available, zero.
        journal.write_bytes(b"")
        self.assertEqual((fact().available, fact().reason, fact().entries), (True, None, 0))
        # Actually over the byte cap keeps its own reason, not the empty one.
        journal.write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertEqual((fact().available, fact().reason), (False, "journal-exceeds-limit"))
        # Unreadable is its own fact, never reported as missing: the state check
        # passes and the locked open is what fails.
        journal.write_bytes(b"")
        journal.chmod(0)
        self.addCleanup(journal.chmod, 0o644)
        try:
            self.assertEqual((fact().available, fact().reason), (False, "journal-unreadable"))
        except AssertionError:
            raise
        finally:
            journal.chmod(0o644)

    def test_the_latest_legal_record_clears_an_older_rejection(self):
        # The Host's superseded-foreign-record case: the same question's first
        # line is foreign, its second line fully bound with an owned answer.
        # The final effective record decides: the answer imports, the stale
        # rejection is gone, and the reader's count covers the question once.
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-sup-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        journal = temp / "inquiry.results.jsonl"
        foreign = {"version": 1, "taskId": "other-task", "attemptId": "other-attempt",
                   "generation": 0, "turnId": "other-turn", "inquiryId": "q-last",
                   "state": "answered", "answer": {"text": "foreign"}}
        valid = {"version": 1, "taskId": "task", "attemptId": "attempt-live", "generation": 1,
                 "turnId": "turn-live", "inquiryId": "q-last", "state": "answered",
                 "answer": {"text": "owned answer", "bytes": 12,
                            "via": "tool:hey_my_buddy_answer_inquiry"}}
        journal.write_text(json.dumps(foreign) + "\n" + json.dumps(valid) + "\n")
        channel = bind_live_channel(identity, credentials={"socketPath": "", "token": "a" * 64},
                                    journal_path=str(journal), activity_path=temp / "activity.json")
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=200, fields=("inquiries",))
        self.assertEqual([(entry.question_id, entry.status, entry.answer) for entry in snapshot.inquiries],
                         [("q-last", "answered", "owned answer")])
        self.assertEqual((snapshot.journal.available, snapshot.journal.entries), (True, 1))
        self.assertEqual(snapshot.journal.rejections, ())

    def test_the_latest_foreign_record_rejects_once_per_question(self):
        # The mirror case plus dedup: a legal record followed by foreign ones
        # leaves exactly one rejection for that id — never an accumulation of
        # every historical refusal — and no bound record is projected for it.
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-rej-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        journal = temp / "inquiry.results.jsonl"
        valid = {"version": 1, "taskId": "task", "attemptId": "attempt-live", "generation": 1,
                 "turnId": "turn-live", "inquiryId": "q-last", "state": "queued"}
        foreign = {"version": 1, "taskId": "other-task", "attemptId": "other-attempt",
                   "generation": 0, "turnId": "other-turn", "inquiryId": "q-last",
                   "state": "answered", "answer": {"text": "foreign"}}
        journal.write_text("".join(json.dumps(record) + "\n" for record in
                                   [valid, foreign, foreign, foreign]))
        channel = bind_live_channel(identity, credentials={"socketPath": "", "token": "a" * 64},
                                    journal_path=str(journal), activity_path=temp / "activity.json")
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=200, fields=("inquiries",))
        self.assertEqual(snapshot.inquiries, (), "the final foreign record refuses the id")
        self.assertEqual((snapshot.journal.entries, len(snapshot.journal.rejections)), (1, 1))
        self.assertEqual(snapshot.journal.rejections[0].question_id, "q-last")
        self.assertEqual(snapshot.journal.rejections[0].reason,
                         "the journal record belongs to another task")

    def test_the_page_budget_counts_the_metadata_it_returns(self):
        # The Host's frame-bound case: 16 bound answers of 3500 bytes beside 16
        # foreign records produced a 66,713-byte first page that failed to
        # encode. The paging budget now measures the journal fact together with
        # the entries, so every returned page encodes within the 64 KiB bound
        # and the full answer set stays reachable through the pagination. The
        # Measure the actual canonical wire frame returned by the C-Two pager.
        from hey_my_buddy.json_codec import canonical_json
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        from hey_my_buddy.buddy.harnesses.zcode.protocol import COOPERATIVE_INQUIRY_NOTE
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-frame-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                               invocation_id="invocation", turn_id="turn")
        journal = temp / "journal.jsonl"
        rows = []
        for index in range(16):
            rows.append({"version": 1, "taskId": "task", "attemptId": "attempt", "generation": 1,
                         "turnId": "turn", "inquiryId": f"q{index}", "state": "answered",
                         "answer": {"text": "x" * 3500, "bytes": 3500,
                                    "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": f"call-{index}",
                                    "at": "2026-10-06T00:00:00Z", "truncated": False},
                         "limitation": COOPERATIVE_INQUIRY_NOTE})
        for index in range(16, 32):
            rows.append({"version": 1, "taskId": "other-task", "attemptId": "other-attempt",
                         "generation": 1, "turnId": "other-turn", "inquiryId": f"q{index}",
                         "state": "queued"})
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
        channel = bind_live_channel(identity, credentials={"socketPath": "", "token": "a" * 64},
                                    journal_path=str(journal), activity_path=temp / "activity.json")
        self.addCleanup(channel.close, reason="probe-finished")
        entries, rejections, pages, after = [], 0, 0, None
        while True:
            snapshot = channel.observe(after_seq=after, limit=256, timeout_ms=1500,
                                       fields=("inquiries",))
            encoded = canonical_json(snapshot.to_payload())
            self.assertLessEqual(len(encoded.encode()), 64 * 1024,
                                 f"page {pages} must encode within the frame bound")
            entries.extend(snapshot.inquiries)
            if snapshot.journal is not None:
                rejections = len(snapshot.journal.rejections)
            pages += 1
            if not snapshot.truncated:
                break
            self.assertTrue(snapshot.inquiries, "a truncated page always carries progress")
            after = max(entry.seq for entry in snapshot.inquiries)
            self.assertLess(pages, 40)
        self.assertEqual(pages, 2, "the metadata-bearing pages break earlier than before")
        self.assertEqual({entry.question_id for entry in entries}, {f"q{i}" for i in range(16)})
        self.assertTrue(all(entry.answer == "x" * 3500 for entry in entries))
        self.assertEqual(rejections, 16)

    def test_the_channel_projection_keeps_the_answer_source_and_delivery_facts(self):
        # The answered journal chain replays with its own source fields, and
        # the queued record's delivery fact survives the later records that
        # lack it — the projection never flattens the answer away.
        import hashlib as _hashlib
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-s-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64,
                       "resultsPath": str(temp / "inquiry.results.jsonl")}
        journal = temp / "inquiry.results.jsonl"
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = make_inquiry_bridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                      "generation": 1, "turnId": "turn-live"},
                               journal_path=str(journal))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        question = "what is the answer?"
        digest = _hashlib.sha256(question.encode()).hexdigest()
        self.assertTrue(fixture_ask(bridge, {"version": 1, "id": "r-1", "token": credentials["token"],
                                       "method": "ask", "inquiryId": "i-1", "question": question})["ok"])
        bridge.deliver_inquiries({"inquiries": [{"inquiryId": "i-1", "questionSha256": digest}]},
                                 "call-checkpoint")
        bridge.record_answer({"inquiryId": "i-1", "questionSha256": digest,
                              "answer": "the verified answer"}, "call-answer")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=str(journal),
                                    activity_path=temp / "activity.json")
        snapshot = channel.observe(after_seq=None, limit=8, timeout_ms=1000, fields=("inquiries",))
        entry = snapshot.inquiries[0]
        self.assertEqual(entry.status, "answered")
        self.assertEqual(entry.answer, "the verified answer")
        self.assertEqual(entry.tool_call_id, "call-answer")
        self.assertEqual(entry.via, "tool:hey_my_buddy_answer_inquiry")
        self.assertTrue(entry.at)
        self.assertEqual(entry.bytes, len("the verified answer".encode()))
        self.assertIs(entry.truncated, False)
        # The queued record's delivery dict survives the later records, and so
        # does its limitation — the board's public unavailable reason prefers
        # it, exactly as the direct reader always did.
        self.assertIsNotNone(entry.delivery)
        self.assertEqual(entry.delivery.value["admittedDelivery"], "cooperative-checkpoint")
        self.assertEqual(entry.limitation, COOPERATIVE_INQUIRY_NOTE)
        # The journal fact: available, the reader's own deduplicated count and
        # no rejections for this bound journal.
        self.assertIsNotNone(snapshot.journal)
        self.assertEqual((snapshot.journal.available, snapshot.journal.reason,
                          snapshot.journal.entries, snapshot.journal.rejections),
                         (True, None, 1, ()))

    def test_the_observation_reads_the_real_bridge_view_with_its_own_bounds(self):
        from hey_my_buddy.buddy.harnesses.live import LiveObservation
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-o-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = make_inquiry_bridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                      "generation": 1, "turnId": "turn-live"},
                               journal_path=str(temp / "inquiry.results.jsonl"))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        # The bridge's own truncations are the observation's bounds: an 80-char
        # kind and a 120-char tool name pass, and the model refuses more.
        bridge.note_event({"method": "session/event",
                           "params": {"type": "k" * 80, "payload": {"toolName": "t" * 120}}}, "running")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=None,
                                    activity_path=temp / "activity.json")
        snapshot = channel.observe(after_seq=None, limit=1, timeout_ms=1000, fields=("observation",))
        self.assertIs(snapshot.observed, True)
        observation = snapshot.observation
        self.assertIsInstance(observation, LiveObservation)
        self.assertIs(observation.ready, True)
        self.assertEqual(observation.session_id, "sess-live")
        self.assertEqual(observation.agent_status, "running")
        self.assertEqual(observation.delivery_mode, "cooperative-checkpoint")
        self.assertEqual(observation.reply_tool.value["name"], "hey_my_buddy_answer_inquiry")
        self.assertEqual(observation.journal.value["enabled"], True)
        self.assertEqual(observation.recent_activity[0].kind, "k" * 80)
        self.assertEqual(observation.recent_activity[0].tool_name, "t" * 120)
        self.assertEqual(observation.unavailable,
                         ("nativeReasoning", "toolArguments", "toolOutput", "providerCredentials",
                          "immediateDelivery"))
        # The producing bridge's truncations are upper bounds: a kind one
        # character longer is refused by the strict model, and the read is
        # reported as an unavailable observation, never reshaped into
        # look-alike metadata.
        bridge_snapshot = bridge.snapshot()

        def oversized_snapshot():
            value = dict(bridge_snapshot)
            value["lastEvent"] = {"at": observation.observed_at, "kind": "k" * 81}
            return value

        bridge.snapshot = oversized_snapshot
        broken = channel.observe(after_seq=None, limit=1, timeout_ms=1000, fields=("observation",))
        self.assertIs(broken.observed, False)
        self.assertEqual(broken.reason, "journal-unavailable")

    def test_the_answer_point_query_reads_one_native_answer_only(self):
        import hashlib as _hashlib
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-a2-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = make_inquiry_bridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                      "generation": 1, "turnId": "turn-live"},
                               journal_path=str(temp / "inquiry.results.jsonl"))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=str(temp / "inquiry.results.jsonl"),
                                    activity_path=temp / "activity.json")
        # No such question: the bridge refuses the point query with its own
        # code, carried as the transport fact — never an invented answer.
        empty = channel.observe(inquiry_id="i-none", timeout_ms=1000)
        # The baseline native producer still reports its refusal verbatim;
        # the shared endpoint has no published answer and returns known absence.
        self.assertIs(empty.observed, True)
        self.assertIsNone(empty.reason)
        self.assertIsNone(empty.error)
        self.assertEqual(empty.inquiries, ())
        question = "what is the answer?"
        digest = _hashlib.sha256(question.encode()).hexdigest()
        self.assertTrue(fixture_ask(bridge, {"version": 1, "id": "r-1", "token": credentials["token"],
                                       "method": "ask", "inquiryId": "i-1", "question": question})["ok"])
        bridge.record_answer({"inquiryId": "i-1", "questionSha256": digest,
                              "answer": "the point answer"}, "call-answer")
        # The channel's journal binding is pointed at a stale, older-state
        # journal: the point query must answer from the native answer view,
        # not from the journal projection — it reads neither the sidecar nor
        # the journal, exactly the direct path's single roundtrip.
        stale = temp / "stale.results.jsonl"
        stale.write_text(json.dumps({
            "version": 1, "taskId": "task", "attemptId": "attempt-live", "generation": 1,
            "turnId": "turn-live", "inquiryId": "i-1", "state": "queued", "question": question,
            "questionSha256": digest, "askedAt": "now"}) + "\n")
        stale_bound = bind_live_channel(identity, credentials=credentials, journal_path=str(stale),
                                        activity_path=temp / "activity.json")
        view = stale_bound.observe(inquiry_id="i-1", timeout_ms=1000)
        entry = view.inquiries[0]
        self.assertEqual(entry.status, "answered")
        self.assertEqual(entry.answer, "the point answer")
        self.assertEqual(entry.tool_call_id, "call-answer")
        self.assertEqual(entry.via, "tool:hey_my_buddy_answer_inquiry")
        self.assertEqual(entry.bytes, len("the point answer".encode()))
        self.assertIs(entry.truncated, False)
        self.assertTrue(entry.at)

    def test_a_refused_ask_carries_the_transport_fact_and_the_specific_code(self):
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        make_inquiry_bridge = fixture_inquiry_bridge
        bind_live_channel = lambda *args, **kwargs: fixture_live_channel(self, *args, **kwargs)
        managed = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-live-r-",
                                              dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = make_inquiry_bridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                      "generation": 1, "turnId": "turn-live"},
                               journal_path=str(temp / "inquiry.results.jsonl"))
        bridge.start()
        self.addCleanup(bridge.close)
        channel = bind_live_channel(identity, credentials=credentials, journal_path=None,
                                    activity_path=temp / "activity.json")
        request = LiveRequest(identity=identity, request_id="q-1", kind="inquiry",
                              payload=InquiryPayload(question_id="i-1", question="anybody there?"))
        # The bridge is mounted but the turn is not admitted: the peer refuses
        # with its own code, and the reply keeps both the transport fact and
        # the specific code instead of folding them into one generic refusal.
        refused = channel.request(request, timeout_ms=1000)
        self.assertEqual((refused.status, refused.reason_code, refused.error_code),
                         ("unavailable", "bridge-refused", "not-ready"))
        # Connection loss keeps the C-Two transport classification.
        gone = bind_live_channel(identity, credentials={"socketPath": str(temp / "absent.sock"),
                                                        "token": credentials["token"]},
                                 journal_path=None, activity_path=temp / "activity.json")
        with mock.patch.object(ctl.cc, "connect", side_effect=ConnectionError("fixture peer lost")):
            unreachable = gone.request(request, timeout_ms=1000)
        self.assertEqual((unreachable.status, unreachable.reason_code, unreachable.error_code),
                         ("unavailable", "transport-unreachable", None))
        # A wrong token is a refused ask whose code is the peer's own.
        foreign = bind_live_channel(identity, credentials={"socketPath": credentials["socketPath"],
                                                           "token": "b" * 64},
                                    journal_path=None, activity_path=temp / "activity.json")
        foreign._token = "b" * 64
        unauthorized = foreign.request(request, timeout_ms=1000)
        self.assertEqual((unauthorized.status, unauthorized.reason_code, unauthorized.error_code),
                         ("unavailable", "token-mismatch", None))


if __name__ == "__main__":
    unittest.main()


class DiscoveryStopTests(NativeRunCase):
    """The existing private app-server fixture, with real stop collection."""
    def discover(self):
        os.environ['HMB_ZCODE_TEST_CASE'] = 'discovery'
        return native_run.run_discovery(cwd=str(self.cwd), invocation_root=self.base / 'discover',
                                       native_root=self.base / 'native-discover', timeout_seconds=3,
                                       cancelled=lambda: False)

    def broken_snapshot(self):
        self.fake.write_text(self.fake.read_text().replace("'sessionId':f's-{session}','sessionKind'",
                                                         "'wrongSessionId':f's-{session}','sessionKind'"))

    def discover_with_windows_boundary(self, api, *, process=None):
        """Real owned_popen; only the Win32 API and Popen creation are synthetic."""
        real_popen = subprocess.Popen

        def popen(argv, **kwargs):
            if "app-server" not in argv:
                return real_popen(argv, **kwargs)  # The private version fixture.
            self.assertEqual(kwargs["creationflags"] & windows_process.CREATE_SUSPENDED,
                             windows_process.CREATE_SUSPENDED)
            self.assertFalse(kwargs["start_new_session"])
            if process is None:
                raise OSError("fixture process creation refused")
            return process

        def owned_popen(*args, **kwargs):
            return windows_process.owned_popen(*args, windows_api=api, **kwargs)

        with mock.patch.object(native_run, "owned_popen", owned_popen), \
             mock.patch.object(windows_process.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(native_run, "NativeConnection") as connection:
            try:
                return self.discover()
            finally:
                connection.assert_not_called()

    def discover_with_fd_close_failure(self, error, *, windows_api=None, process=None):
        """Fail only the app-server stderr close after real owned_popen runs.

        Windows uses injected API/Popen handles; POSIX starts the real private
        fixture. Test teardown retains its own actual handle to reap mutants.
        """
        real_popen, real_close = subprocess.Popen, os.close
        app_fd = None
        close_failed = False
        self.fd_close_child = None

        def popen(argv, **kwargs):
            nonlocal app_fd
            if "app-server" not in argv:
                return real_popen(argv, **kwargs)
            app_fd = kwargs["stderr"]
            if windows_api is None:
                return real_popen(argv, **kwargs)
            self.assertEqual(kwargs["creationflags"] & windows_process.CREATE_SUSPENDED,
                             windows_process.CREATE_SUSPENDED)
            self.assertFalse(kwargs["start_new_session"])
            if process is None:
                raise OSError("fixture process creation refused")
            return process

        def owned_popen(*args, **kwargs):
            child = windows_process.owned_popen(*args, windows_api=windows_api, **kwargs)
            self.fd_close_child = child
            owner = native_run.ProcessHandle(child, own_group=True, log_paths={})

            def reap():
                try:
                    native_run.halt_owned_group(child, owner, time.monotonic() + 5)
                finally:
                    child.stdout.close()
            self.addCleanup(reap)
            return child

        def close(fd):
            nonlocal close_failed
            real_close(fd)  # The private descriptor must not leak in a failing test.
            if fd == app_fd and not close_failed:
                close_failed = True
                raise error

        with mock.patch.object(native_run, "owned_popen", owned_popen), \
             mock.patch.object(windows_process.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(native_run.os, "close", side_effect=close), \
             mock.patch.object(native_run, "NativeConnection") as connection:
            try:
                return self.discover()
            finally:
                self.assertTrue(close_failed)
                connection.assert_not_called()

    def fd_close_windows_process(self):
        process = FakeProcess()
        process.stdin, process.stdout = io.BytesIO(), io.BytesIO()
        return process

    def test_fd_close_failure_after_windows_creation_keeps_actual_stop(self):
        process = self.fd_close_windows_process()
        api = FakeAPI(process)
        error = OSError("fixture stderr close failed")
        with self.assertRaises(Exception) as caught:
            self.discover_with_fd_close_failure(error, windows_api=api, process=process)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertIs(caught.exception, error)
        self.assertIs(self.fd_close_child, process)
        self.assertEqual(api.events[:3], ["create", ("assign", 77, 42), ("resume", 42)])
        self.assertIn(("terminate", 77), api.events)
        self.assertEqual(api.events[-1], ("close", 77))
        self.assertTrue(process._buddy_job.empty_confirmed)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_fd_close_failure_after_windows_creation_keeps_unknown_stop(self):
        process = self.fd_close_windows_process()
        api = FakeAPI(process)
        api.fail_at = "active"
        error = OSError("fixture stderr close failed")
        with self.assertRaises(Exception) as caught:
            self.discover_with_fd_close_failure(error, windows_api=api, process=process)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)
        self.assertIs(caught.exception, error)
        self.assertIs(self.fd_close_child, process)
        self.assertIn(("terminate", 77), api.events)
        self.assertIsNotNone(process._buddy_job.handle)
        self.assertFalse(process._buddy_job.empty_confirmed)
        self.assertTrue(process.stdout.closed)
        api.fail_at = None  # Private synthetic teardown can now observe the Job.

    def test_fd_close_failure_before_windows_creation_keeps_no_child_stop(self):
        api = FakeAPI(FakeProcess())
        error = OSError("fixture stderr close failed")
        with self.assertRaises(Exception) as caught:
            self.discover_with_fd_close_failure(error, windows_api=api)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.fd_close_child)
        self.assertEqual(api.events, ["create", ("close", 77)])
        self.assertEqual(caught.exception.__context__.code, "harness-unavailable")

    def test_fd_close_failure_after_windows_ownership_failure_keeps_unknown_stop(self):
        process = self.fd_close_windows_process()
        api = FakeAPI(process)
        api.fail_at = "assign"
        error = OSError("fixture stderr close failed")
        with mock.patch.object(process, "kill", side_effect=OSError("fixture kill refused")):
            with self.assertRaises(Exception) as caught:
                self.discover_with_fd_close_failure(error, windows_api=api, process=process)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.fd_close_child)
        self.assertTrue(caught.exception.__context__.owned_popen_evidence.created)
        self.assertFalse(caught.exception.__context__.owned_popen_evidence.shutdown_confirmed)

    def test_fd_close_failure_with_stop_error_keeps_owned_handle_unknown(self):
        process = self.fd_close_windows_process()
        api = FakeAPI(process)
        close_error = OSError("fixture stderr close failed")
        stop_error = OSError("fixture wait observation failed")
        with mock.patch.object(process, "wait", side_effect=stop_error):
            with self.assertRaises(Exception) as caught:
                self.discover_with_fd_close_failure(close_error, windows_api=api, process=process)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)
        self.assertIs(caught.exception, stop_error)
        self.assertIs(caught.exception.__context__, close_error)
        self.assertIs(self.fd_close_child, process)
        self.assertIsNotNone(process._buddy_job.handle)
        self.assertTrue(process.stdout.closed)

    @unittest.skipIf(os.name == "nt", "POSIX private-process regression")
    def test_fd_close_failure_after_posix_creation_keeps_actual_stop(self):
        error = OSError("fixture stderr close failed")
        with self.assertRaises(Exception) as caught:
            self.discover_with_fd_close_failure(error)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertIs(caught.exception, error)
        self.assertIsNotNone(self.fd_close_child)
        self.assertIsNotNone(self.fd_close_child.poll())
        self.assertTrue(self.fd_close_child.stdout.closed)

    @unittest.skipIf(os.name == "nt", "POSIX private-process regression")
    def test_fd_close_failure_after_posix_creation_keeps_unknown_stop(self):
        error = OSError("fixture stderr close failed")
        real_killpg = os.killpg

        def killpg(pgid, sig):
            if self.fd_close_child is not None and pgid == self.fd_close_child.pid and sig == 0:
                raise PermissionError("fixture group observation refused")
            return real_killpg(pgid, sig)

        with mock.patch.object(os, "killpg", side_effect=killpg):
            with self.assertRaises(Exception) as caught:
                self.discover_with_fd_close_failure(error)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)
        self.assertIs(caught.exception, error)
        self.assertIsNotNone(self.fd_close_child)
        self.assertIsNotNone(self.fd_close_child.poll())
        self.assertTrue(self.fd_close_child.stdout.closed)

    def test_original_non_native_read_error_keeps_confirmed_stop(self):
        self.broken_snapshot()
        with self.assertRaises(KeyError) as caught:
            self.discover()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertEqual(caught.exception.args, ('sessionId',))

    def test_native_read_error_keeps_confirmed_stop(self):
        call = native_run.NativeConnection.call
        def rejected(connection, method, params):
            if method == 'session/create':
                raise native_run.NativeError('invalid-protocol', 'fixture protocol failure')
            return call(connection, method, params)
        with mock.patch.object(native_run.NativeConnection, 'call', rejected):
            with self.assertRaises(native_run.NativeError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertEqual(caught.exception.code, 'invalid-protocol')

    def test_read_error_keeps_unconfirmed_stop(self):
        self.broken_snapshot()
        stop = native_run._stop_native
        def unobserved(spawn, deadline):
            stop(spawn, deadline)  # Actually reap the private fixture.
            return False, True
        with mock.patch.object(native_run, '_stop_native', side_effect=unobserved):
            with self.assertRaises(KeyError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)

    def test_abnormal_exit_keeps_actual_confirmed_stop(self):
        self.fake.write_text(self.fake.read_text().replace(
            "send({'id':call['id'],'result':result})",
            "send({'id':call['id'],'result':result})\n    if method == 'session/close': sys.exit(7)"))
        with self.assertRaises(native_run.NativeError) as caught:
            self.discover()
        self.assertEqual(caught.exception.code, 'native-process-failed')
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_successful_read_with_unconfirmed_stop_is_refused(self):
        stop = native_run._stop_native
        def unobserved(spawn, deadline):
            stop(spawn, deadline)
            return False, True
        with mock.patch.object(native_run, '_stop_native', side_effect=unobserved):
            with self.assertRaises(native_run.NativeError) as caught:
                self.discover()
        self.assertEqual(caught.exception.code, 'native-shutdown-failed')
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)

    def test_preparation_failure_proves_no_child_created(self):
        with mock.patch.object(native_run, 'ensure_private_dir', side_effect=OSError('fixture directory refused')), \
             mock.patch.object(native_run, '_spawn_app_server') as spawn:
            with self.assertRaises(OSError) as caught:
                self.discover()
        spawn.assert_not_called()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_spawn_rejection_proves_no_child_created(self):
        api = FakeAPI(FakeProcess())
        with self.assertRaises(native_run.NativeError) as caught:
            self.discover_with_windows_boundary(api)
        self.assertEqual(api.events, ["create", ("close", 77)])
        self.assertEqual(caught.exception.code, "harness-unavailable")
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_windows_job_creation_failure_proves_no_child_created(self):
        api = FakeAPI(FakeProcess())
        with mock.patch.object(api, "create_job", side_effect=OSError("fixture job creation refused")):
            with self.assertRaises(native_run.NativeError) as caught:
                self.discover_with_windows_boundary(api)
        self.assertEqual(api.events, [])
        self.assertEqual(caught.exception.code, "harness-unavailable")
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_windows_ownership_failure_keeps_observed_cleanup(self):
        for failure in ("assign", "resume"):
            with self.subTest(failure=failure):
                process = FakeProcess()
                api = FakeAPI(process)
                api.fail_at = failure
                with self.assertRaises(native_run.NativeError) as caught:
                    self.discover_with_windows_boundary(api, process=process)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(api.events[-1], ("close", 77))
                if failure == "assign":
                    self.assertEqual(process.kills, 1)
                    self.assertNotIn(("resume", 42), api.events)
                else:
                    self.assertIn(("terminate", 77), api.events)
                    self.assertIn(("active", 77), api.events)
                self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_windows_ownership_failure_before_registration_keeps_unknown_stop(self):
        cases = (("assign", "kill"), ("assign", "wait"), ("assign", "close"),
                 ("resume", "terminate"), ("resume", "wait"), ("resume", "active"),
                 ("resume", "nonempty"), ("resume", "close"))
        for failure, cleanup in cases:
            with self.subTest(failure=failure, cleanup=cleanup):
                process = FakeProcess()
                api = FakeAPI(process)
                api.fail_at = failure
                target = process if cleanup in ("kill", "wait") else api
                method = "active" if cleanup == "nonempty" else cleanup
                options = {"return_value": 1} if cleanup == "nonempty" else {
                    "side_effect": subprocess.TimeoutExpired("fake", 2) if cleanup == "wait" else OSError("fixture cleanup refused")}
                with mock.patch.object(target, method, **options):
                    with self.assertRaises(Exception) as caught:
                        self.discover_with_windows_boundary(api, process=process)
                self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)
                self.assertTrue(caught.exception.owned_popen_evidence.created)
                self.assertFalse(caught.exception.owned_popen_evidence.shutdown_confirmed)
                self.assertIn(("assign", 77, 42), api.events)

    def test_connection_construction_failure_keeps_actual_stop(self):
        with mock.patch.object(native_run, 'NativeConnection', side_effect=ValueError('fixture connection failed')):
            with self.assertRaises(ValueError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_connection_construction_failure_keeps_unconfirmed_stop(self):
        halt = native_run.halt_owned_group
        def unobserved(process, handle, deadline):
            halt(process, handle, deadline)
            return False, True
        with mock.patch.object(native_run, 'NativeConnection', side_effect=ValueError('fixture connection failed')), \
             mock.patch.object(native_run, 'halt_owned_group', side_effect=unobserved):
            with self.assertRaises(ValueError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)

    def test_actual_failed_discovery_crosses_controller_and_role(self):
        from hey_my_buddy.buddy.roles import run_execution
        self.broken_snapshot()
        os.environ['HMB_ZCODE_TEST_CASE'] = 'discovery'
        with self.assertRaises(BoardError) as caught:
            run_execution.discover_models('zcode')
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)
        self.assertEqual(caught.exception.code, 'HARNESS_UNAVAILABLE')

    def test_unknown_stop_failure_never_uses_pre_spawn_evidence(self):
        stop = native_run._stop_native
        def stopped_without_receipt(spawn, deadline):
            stop(spawn, deadline)
            raise ValueError('fixture stop observation failed')
        with mock.patch.object(native_run, '_stop_native', side_effect=stopped_without_receipt):
            with self.assertRaises(ValueError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), False)

    def test_unknown_connection_stop_keeps_owned_child_uncertain(self):
        halt = native_run.halt_owned_group
        def stopped_without_receipt(process, handle, deadline):
            halt(process, handle, deadline)
            raise ValueError('fixture stop observation failed')
        with mock.patch.object(native_run, 'NativeConnection', side_effect=ValueError('fixture connection failed')), \
             mock.patch.object(native_run, 'halt_owned_group', side_effect=stopped_without_receipt):
            with self.assertRaises(ValueError) as caught:
                self.discover()
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), False)
