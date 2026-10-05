"""The unified zcode run seam itself: a real native connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.zcode.native_run.run`
directly — a real spawned app-server (the fake fixture), a real frozen
:class:`RunRequest`, the role's real observation rules and service binding,
and a :class:`RunResult` read back as facts. Nothing here wraps a prepared
dict; the request goes through session create/configure/subscribe/send and
the result comes from the same native stream the legacy controller observed.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from hey_my_buddy.buddy.harnesses.run_contract import (
    NetworkPolicy,
    RunFeedback,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
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
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.run_observers import FastCorrection, worker_observer
from hey_my_buddy.buddy.roles.structured_call import no_tool_prompt
from hey_my_buddy.buddy.roles.turn_io import input_hash, validate_outcome
from hey_my_buddy.errors import BoardError

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from buddy.harnesses.zcode.test_zcode_tool_evidence import FakeAppServerTests, SCHEMA


class NativeRunCase(FakeAppServerTests):
    """The shared fake-app-server harness for direct fast-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-seam-",
                                                 dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        os.environ.update(self.environment)

    def fast_request(self, prompt: str, *, timeout: int = 5) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="task", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                                 generation=1, invocation_id=uuid.uuid4().hex),
            harness="zcode",
            configuration=RunConfiguration(provider="fixture-api", model="fixture-model", effort="low"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(invocation_root=str(self.base / f"invocation-{uuid.uuid4().hex[:8]}"),
                                            native_root=str(self.base / f"native-{uuid.uuid4().hex[:8]}")),
            input_text=prompt, tool_scope="none", network=NetworkPolicy(requested=False),
            output_schema=SCHEMA,
            budget=RunBudget(timeout_seconds=timeout, max_output_bytes=512 * 1024))


class MockNativeCase(ZcodeFixtureCase):
    """The full mock app-server (MCP finish bridge included) for worker-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-seam-w-",
                                                 dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def worker_request(self, turn_input: dict, *, timeout: int = 10, continuation=None):
        identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
        invocation = self.base / f"worker-invocation-{uuid.uuid4().hex[:8]}"
        mount = prepare_session_service(
            invocation_root=invocation, identity=identity, input_sha256=input_hash(turn_input),
            attention_path=self.base / "attention.json",
            session_tools=worker_services.session_tools(), completion_tool="buddy_finish_turn")
        prompt = worker_services.governed_prompt("fixture task", turn_input, mount.finish_tool,
                                                 checkpoint_tool=None, answer_tool=None)
        request = RunRequest(
            identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                                 generation=identity["generation"], invocation_id=uuid.uuid4().hex,
                                 turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
            harness="zcode",
            configuration=RunConfiguration(provider="fixture-api", model="fixture-model", effort="low"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(invocation_root=str(invocation),
                                            native_root=str(self.base / "worker-native")),
            input_text=prompt, tool_scope="write", network=NetworkPolicy(requested=False),
            output_schema=worker_services.OUTCOME_SCHEMA,
            budget=RunBudget(timeout_seconds=timeout, max_output_bytes=512 * 1024),
            continuation=continuation,
            session_services=(SessionService(service_id=mount.server_name, kind="session-tools",
                                             tool_names=[f"mcp__{mount.server_name}__{name}"
                                                         for name in mount.bare_tools],
                                             input_schema=worker_services.OUTCOME_SCHEMA,
                                             delivery_mode="in-turn"),))
        services = SessionServices(mount=mount, validate_outcome=validate_outcome, inquiry=None)
        return request, services


class FastSeamTests(NativeRunCase):
    def test_a_settled_answer_is_a_final_message_fact_with_role_checked_schema(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.mechanism, "final-message")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.correction_count, 0)
        self.assertEqual(correction.stop_reason, None)
        self.assertIsNone(result.unknown_events)
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.stop_evidence.native.exit_code, 0)
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["toolAllowlist"], [])
        self.assertIsNone(policy.reported, "sent parameters are not a native policy readback")
        self.assertEqual(policy.basis, "zcode/session-create-accepted")

    def test_one_format_correction_runs_a_second_session_on_the_same_process(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "correct"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        # Both root sessions are listed as native roots of this one run.
        self.assertEqual(len(result.root_identities), 2)
        self.assertEqual(correction.correction_count, 1)

    def test_a_tool_fact_stops_the_run_through_the_observer_feedback(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "tool"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertIsNone(result.stop_evidence.interrupt.acknowledged)
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertTrue(package["events"])

    def test_a_second_correction_feedback_is_executed_and_stays_bounded(self):
        # How often to correct is the role's rule alone; the driver only
        # executes settled feedback under the shared deadline and cancel. A
        # bounded observer that corrects twice proves the second correction
        # really runs — and terminates — instead of looping or being capped.
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
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
        self.assertEqual(len(result.root_identities), 3)

    def test_a_write_scope_without_a_service_takes_the_final_message_path(self):
        # The scope only sets the native tool settings; without a bound
        # completion service the run takes the final-message carrier over the
        # session's own default tools, and the effective policy says exactly
        # that. No external review eligibility is implied.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "write-final"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        request = self.fast_request(prompt)
        scoped = RunRequest.from_payload(request.to_payload() | {"toolScope": "write"})
        result = run(scoped, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.mechanism, "final-message")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.effective_policy.tools.enforcement, "unrestricted")
        self.assertIsNone(result.effective_policy.tools.reported)

    def test_a_late_tool_frame_after_settlement_is_the_role_call_not_the_scopes(self):
        # The EOF drain hands a late tool fact to the role observer: a rule that
        # accepts tools (a write scope) keeps the stream complete, and only a
        # rule that stops marks it incomplete — the driver never fails the
        # frame by reading the tool scope itself.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "write-post-close"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        request = self.fast_request(prompt)
        scoped = RunRequest.from_payload(request.to_payload() | {"toolScope": "write"})
        result = run(scoped, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.tool_evidence.value["toolCalls"], 1)
        stopping = FastCorrection(SCHEMA, prompt)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "post-close-tool"
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
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.native_run.NativeConnection",
                         side_effect=OSError("pipe gone")):
            result = run(self.fast_request(prompt), observer=correction.observer,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-native-result")
        native = result.stop_evidence.native
        self.assertTrue(native.started)
        self.assertEqual(native.group_state, "gone")
        self.assertEqual(native.observation_basis, "owned-group-stopped-in-spawn")
        self.assertIsNotNone(native.exit_code)
        # Killing the group proves the process side only; no SDK answered.
        self.assertIsNone(result.stop_evidence.interrupt.acknowledged)

    def test_the_peer_receives_the_refusal_before_the_observer_stop_ends_the_run(self):
        # The refusal answer completes first — the peer reads the reply with
        # the same id and records it — and only then does the role's stop take
        # effect on the driver's next boundary, with no settlement behind it.
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "reverse-request"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
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
        self.assertIsNone(result.stop_evidence.interrupt.acknowledged)

    def test_a_reverse_request_before_the_rpc_reply_stops_after_the_refusal(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "reverse-before-send-reply"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        started = time.monotonic()
        result = run(self.fast_request(prompt, timeout=8), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertLess(time.monotonic() - started, 6, "the RPC waited for a reply after the refusal")
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        self.assertEqual(len(result.denied_interactions), 1)
        self.assertEqual(result.denied_interactions[0].method, "interaction/requestPermission")
        self.assertEqual(json.loads((self.cwd / "refusal-reply.json").read_text()),
                         {"id": "srv-before-reply", "refused": True})
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "owned-group-signal")
        self.assertIsNone(result.stop_evidence.interrupt.acknowledged)

    def test_a_late_foreign_tool_frame_reaches_the_role_before_the_protocol_check(self):
        # A late tool frame from a child session keeps its projected fact and
        # reaches the role observer first: the fast rule stops with its own
        # reason instead of the protocol layer's foreign-session code.
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "post-close-foreign"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
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
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "close-fail"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        request = self.fast_request(prompt)
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "session-close-unconfirmed")
        self.assertEqual(result.value.mechanism, "final-message")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.native_identity.turn_id, result.root_identities[0].turn_id)

    def test_a_reverse_request_ends_the_fast_run_immediately_with_the_fact_kept(self):
        # The refused interaction is answered and recorded, and the role's stop
        # takes effect on that same pump turn: no settlement, no later unknown
        # event and no timeout wait may be needed to end the run.
        for case in ("reverse-request", "reverse-oauth"):
            with self.subTest(case=case):
                os.environ["BUDDY_ZCODE_TEST_CASE"] = case
                prompt = no_tool_prompt("Choose a profile", SCHEMA)
                correction = FastCorrection(SCHEMA, prompt)
                started = time.monotonic()
                result = run(self.fast_request(prompt, timeout=8), observer=correction.observer,
                             services=None, cancelled=lambda: False)
                self.assertLess(time.monotonic() - started, 6,
                                "the run waited instead of stopping on the denied fact")
                self.assertEqual(result.end.status, "cancelled")
                self.assertEqual(result.end.reason_code, "observer-interrupt")
                self.assertEqual(correction.stop_reason, "no-tool-violation")
                self.assertEqual(len(result.denied_interactions), 1)
                self.assertIn("interaction/", result.denied_interactions[0].method)

    def test_a_pre_spawn_failure_claims_nothing_that_never_happened(self):
        from unittest import mock as _mock
        from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError as _NativeError
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.native_run._spawn_app_server",
                         side_effect=_NativeError("unsupported-provider", "no native start")):
            result = run(self.fast_request(prompt), observer=correction.observer,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "unsupported-provider")
        # Nothing is claimed that the run never reached: no session opened, no
        # effective policy, no configuration checks, no completion, no model.
        self.assertIsNone(result.model_started)
        self.assertEqual(result.model_start_evidence.basis, "unknown")
        self.assertIsNone(result.effective_policy.tools)
        self.assertEqual(result.configuration.checks, ())
        self.assertIsNone(result.completion_evidence)
        self.assertIsNone(result.native_event_count)
        self.assertEqual(result.stop_evidence.native.observation_basis, "spawn-never-happened")
        self.assertIsNone(result.stop_evidence.native.started)

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
    return result.completion_evidence.call_id


class WorkerSeamTests(MockNativeCase):
    def turn_input(self, *, previous=None, mode=None):
        return {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{uuid.uuid4().hex[:8]}",
                "generation": 1, "turnId": "turn-1",
                "resumeMode": mode or ("native-session" if previous else "initial"),
                "previousSessionId": previous, "context": {}, "executionWorkspace": {}}

    def test_one_unrepresentable_package_keeps_every_other_observed_fact(self):
        # The usage package alone is unrepresentable; the run's identity,
        # value, receipt, configuration and stop facts all stay, and the end
        # is not rewritten — the fallback never blankets knowns into unknowns.
        from unittest import mock as _mock
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        with _mock.patch("hey_my_buddy.buddy.harnesses.zcode.protocol.ZcodeAttemptUsage.raw_usage",
                         return_value={"inputTokens": "not-a-number"}):
            result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertEqual(result.value.mechanism, "completion-tool")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertEqual(result.configuration.checked.model.value, "fixture-model")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_a_post_configure_failure_keeps_every_reached_stage_fact(self):
        import os as _os
        _os.environ.update(self.environment)
        _os.environ["BUDDY_ZCODE_TEST_CASE"] = "wrong-input"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "wrong-native-turn")
        # Configure ran and the readback confirmed; the session opened; the
        # input was sent. Each reached stage keeps its fact in the failure.
        self.assertEqual(result.configuration.checked.model.value, "fixture-model")
        self.assertEqual(result.configuration.checks,
                         ("native-available-catalog", "session-setModel-readback",
                          "thought-level-readback"))
        self.assertIsNotNone(result.effective_policy.tools)
        self.assertTrue(result.model_started)
        self.assertEqual(result.model_start_evidence.basis, "input-sent")
        self.assertIsNone(result.completion_evidence)
        self.assertIsNotNone(result.end.message)

    def test_a_governed_turn_settles_with_a_verified_completion_tool_value(self):
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.value.mechanism, "completion-tool")
        self.assertEqual(result.value.schema_status, "valid")
        self.assertEqual(result.value.parsed.value["summary"], "fixture work completed")
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertEqual(result.completion_evidence.mechanism, "completion-tool")
        self.assertIsNotNone(result.completion_evidence.call_id)
        self.assertTrue(result.continuation.resumable)
        # The mock exports no message-boundary token records, so usage stays an
        # honest absent fact here; the adapter-path suites cover the real shape.
        self.assertIsNone(result.usage)
        self.assertEqual(result.configuration.checked.model.value, "fixture-model")
        # The governed turn's evidence parts are retained for the role's record.
        kinds = {ref.kind for ref in result.evidence_refs}
        self.assertIn("turn-provenance", kinds)
        provenance = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "turn-provenance")).read_bytes())
        self.assertEqual(provenance["settlement"], "session-closed")
        self.assertEqual(result.value.parsed.value["summary"], "fixture work completed")
        # The mechanical binding published the bridge configuration the MCP read.
        bridge = json.loads(Path(services.mount.bridge_config_path).read_bytes())
        self.assertEqual(bridge["identity"]["attemptId"], turn_input["attemptId"])

    def test_native_resume_reuses_the_bound_session_through_the_request_continuation(self):
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
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
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "close-failed"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        result = run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "session-close-unconfirmed")
        self.assertEqual(result.value.mechanism, "completion-tool")
        self.assertEqual(result.value.schema_status, "valid")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        self.assertEqual(result.completion_evidence.call_id, "root-call")
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertIsNotNone(result.completion_evidence.native_identity.turn_id)
        # No role record is publishable from a failed close.
        self.assertEqual({ref.kind for ref in result.evidence_refs},
                         {"inquiry-report", "native-stderr", "denied-interactions", "attention-report"} & {ref.kind for ref in result.evidence_refs})

    def test_the_request_refuses_mismatched_service_descriptions(self):
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        broken = request.model_copy(update={"session_services": (
            SessionService(service_id="other", kind="session-tools",
                           tool_names=["mcp__other__buddy_finish_turn"],
                           delivery_mode="in-turn"),)})
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
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "refusal-argument"
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
        # The completion facts remain their own evidence beside the package.
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertEqual(result.completion_evidence.call_id, "call-finish-final")

    def test_every_carrier_reports_task_tool_facts_with_delivery_kept_separate(self):
        # A completion-tool run projects its task tools like any other run:
        # the Bash call is a fact, the mounted finish call is not a task tool
        # (its evidence is the verified receipt), the root identity comes from
        # the verified turn, and the activity sidecar keeps its own original
        # counts — a separate fact from this package.
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "task-tool"
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
        # The delivery call is evidenced by the receipt, never by the package.
        self.assertNotIn(evidence_call(result), [event["callId"] for event in package["events"]])
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertEqual(result.completion_evidence.call_id, evidence_call(result))

    def test_a_request_schema_differring_from_the_mounted_contract_is_refused(self):
        os.environ.update(self.environment)
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
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
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        turn_input = self.turn_input()
        request, services = self.worker_request(turn_input)
        scoped = request.model_copy(update={"tool_scope": "read"})
        result = run(scoped, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.effective_policy.tools.enforcement, "unrestricted")


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
        connection = NativeConnection(process, time.monotonic() + 10, threading.Event(), no_tools=True)
        connection.attention = recorded.append
        connection.messages.put({"id": 7, "method": "interaction/requestPermission",
                                 "params": {"sessionId": "s", "kind": "shell"}})
        try:
            connection.pump()
        except Exception as error:  # noqa: BLE001 - the pump must not raise here
            self.fail(f"a no-tool reverse request raised out of the pump: {error!r}")
        self.assertEqual(recorded[0]["method"], "interaction/requestPermission")
        self.assertEqual(recorded[0]["outcome"], "refused-with-jsonrpc-error")


class LiveBindingTests(NativeRunCase):
    def test_the_existing_live_channel_binds_ask_activity_and_journal(self):
        from hey_my_buddy.buddy.harnesses.live import ExistingLiveChannel
        from hey_my_buddy.buddy.harnesses.zcode.live_bridge import InquiryBridge, bind_live_channel
        from hey_my_buddy.protocol import activity as activity_protocol
        managed = tempfile.TemporaryDirectory(prefix="buddy-zcode-live-",
                                              dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        journal = temp / "inquiry.results.jsonl"
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge = InquiryBridge(credentials, identity={"taskId": "task", "attemptId": "attempt-live",
                                                       "generation": 1, "turnId": "turn-live"},
                               journal_path=str(journal))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        channel = bind_live_channel(identity, credentials=credentials, journal_path=str(journal),
                                    activity_path=temp / "activity.json")
        self.assertIsInstance(channel, ExistingLiveChannel)
        self.assertEqual(channel.capabilities().inquiry_delivery, "cooperative-checkpoint")
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        reply = channel.request(LiveRequest(identity=identity, request_id="q-1", kind="inquiry",
                                            payload=InquiryPayload(question_id="i-1",
                                                                   question="is the binding real?")),
                                timeout_ms=1000)
        self.assertEqual(reply.status, "queued")
        # The activity fact goes through the real attempt-bound sidecar writer
        # and reads back only for this attempt.
        sidecar = activity_protocol.ActivitySidecar(temp, task_id="task", attempt_id="attempt-live",
                                                    generation=1)
        self.assertTrue(sidecar.publish({"phase": "streaming-model", "eventSeq": 3,
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
        from hey_my_buddy.buddy.harnesses.zcode.live_bridge import InquiryBridge, bind_live_channel
        managed = tempfile.TemporaryDirectory(prefix="buddy-zcode-live-a-",
                                              dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        credentials = {"socketPath": str(temp / "inquiry.sock"), "token": "a" * 64}
        journal = temp / "inquiry.results.jsonl"
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        bridge_identity = {"taskId": "task", "attemptId": "attempt-live",
                           "generation": 1, "turnId": "turn-live"}
        bridge = InquiryBridge(credentials, identity=bridge_identity, journal_path=str(journal))
        bridge.start()
        self.addCleanup(bridge.close)
        bridge.activate("sess-live")
        asked = bridge.handle({"version": 1, "id": "r-1", "token": credentials["token"],
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
        from hey_my_buddy.buddy.harnesses.zcode.live_bridge import bind_live_channel
        managed = tempfile.TemporaryDirectory(prefix="buddy-zcode-live-f-",
                                              dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(managed.cleanup)
        temp = Path(managed.name)
        identity = RunIdentity(task_id="task", attempt_id="attempt-live", generation=1,
                               invocation_id="invocation-live", turn_id="turn-live")
        journal = temp / "inquiry.results.jsonl"
        # A foreign attempt's sidecar and a foreign journal line: neither the
        # sidecar reader nor the journal replay may surface another attempt's
        # record, exactly like the bridge's own bound replay.
        (temp / "activity.json").write_text(json.dumps({"phase": "streaming-model", "eventSeq": 9,
                                                        "taskId": "task", "attemptId": "other",
                                                        "generation": 1,
                                                        "counts": {"modelTurns": 1, "toolCalls": 0}}))
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

    def test_a_foreign_reply_id_and_a_silent_socket_are_refused_and_bounded(self):
        import socket as socket_module
        import threading
        from hey_my_buddy.buddy.harnesses.zcode.live_bridge import bridge_ask
        from hey_my_buddy.errors import BoardError
        listener = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        listener.bind(str(Path(tempfile.mkdtemp(prefix="buddy-zcode-live-s-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))) / "s.sock"))
        listener.listen(2)

        def serve(kind: str) -> None:
            connection, _ = listener.accept()
            if kind == "wrong-id":
                connection.sendall(b'{"version":1,"id":"not-ours","ok":true,"value":{}}\n')
            else:
                time.sleep(2.0)  # accept, read nothing, answer nothing
            connection.close()

        wrong = threading.Thread(target=serve, args=("wrong-id",), daemon=True)
        wrong.start()
        with self.assertRaises(BoardError) as caught:
            bridge_ask({"socketPath": listener.getsockname(), "token": "a" * 64},
                       "i-1", "hello", timeout_ms=1000)
        self.assertIn("foreign id", caught.exception.message)
        wrong.join(timeout=2)

        silent = threading.Thread(target=serve, args=("silent",), daemon=True)
        silent.start()
        started = time.monotonic()
        with self.assertRaises(BoardError) as timed:
            bridge_ask({"socketPath": listener.getsockname(), "token": "a" * 64},
                       "i-2", "hello", timeout_ms=200)
        self.assertIn("transport window", timed.exception.message)
        self.assertLess(time.monotonic() - started, 1.5,
                        "the caller's timeout did not reach the socket")
        silent.join(timeout=2)
        listener.close()


if __name__ == "__main__":
    unittest.main()
