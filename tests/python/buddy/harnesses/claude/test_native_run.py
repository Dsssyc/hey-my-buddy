"""The unified claude run seam itself: a real native connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.claude.native_run.run`
directly — a real spawned native CLI (the private stream-json fixtures), a
real frozen :class:`RunRequest`, the role observer contract and one factual
:class:`RunResult`. Nothing here wraps the old controller entry: the request
goes through the same initialize, user-message boundary, send/wait/drain
settlement and conservative stop the micro task turn and the read-only call
always shared, and the legacy adapter path is exercised by its own suites.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from temporary import TemporaryDirectory
import unittest
import uuid
from pathlib import Path
from unittest import mock
from buddy.harnesses.fixture_selection import select_command, select_fixture

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.blackboard.routing.router import answer_schema
from hey_my_buddy.buddy.harnesses.in_turn_inquiry import prepare_inquiry_services
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge, _owner_response
from hey_my_buddy.buddy.harnesses.claude.native_run import run, run_discovery
from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE,
    PrivateStatePaths,
    RunBudget,
    RunBuddy,
    RunContinuation,
    RunFeedback,
    RunIdentity,
    RunRequest,
    SessionService,
)
from hey_my_buddy.buddy.roles.worker_services import OUTCOME_SCHEMA as WORKER_SCHEMA
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
from hey_my_buddy.protocol.activity import is_newer, normalize_activity

FIXTURE = Path(__file__).parent / "fixtures/fake_claude.py"
STREAM_FIXTURE = Path(__file__).parent / "fixtures/mock_claude.py"
USAGE_FIXTURE = Path(__file__).parent / "fixtures/fake_claude_usage.py"
BUDDY = {"provider": "anthropic", "model": "claude-opus-5-5[1m]", "effort": "high"}
READ_SCHEMA = answer_schema(["legal"])


def flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


class NativeRunCase(unittest.TestCase):
    """The shared fake-CLI harness for direct run-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = TemporaryDirectory(prefix="hey-my-buddy-claude-seam-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cwd = self.base / "checkout"
        self.cwd.mkdir()
        self.state_path = self.base / "fixture.json"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("HMB_", "ANTHROPIC_", "CLAUDE_"))
                       and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update(HMB_CONSOLE_PORT="0", HMB_DEV_SOURCE="1",
                           HMB_CLAUDE_FIXTURE_STATE=str(self.state_path),
                           HMB_STATE_DIR=str(self.base / "state"),
                           HMB_RUNTIME_ROOT=str(self.base / "runtime"))
        select_fixture(environment, self.base, "claude", FIXTURE)
        self.patcher = mock.patch.dict(os.environ, environment, clear=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.case = "ok"

    def fixture_case(self, case: str):
        os.environ["HMB_CLAUDE_FIXTURE_CASE"] = case
        self.case = case

    def use_fixture(self, fixture: Path):
        select_fixture(os.environ, self.base, "claude", fixture)

    def request(self, *, scope: str = "write", schema=None, timeout: int = 10,
                continuation=None) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="task-1", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                                 generation=1, invocation_id=uuid.uuid4().hex, turn_id="turn-1"),
            harness="claude",
            session_record_path=str(self.base / f"session-{uuid.uuid4().hex}.jsonl"),
            buddy=RunBuddy(**BUDDY),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(
                state_dir=str(self.base / "state"),
                invocation_root=str(self.base / f"invocation-{uuid.uuid4().hex[:8]}"),
                native_root=str(self.base / f"native-{uuid.uuid4().hex[:8]}")),
            input_text="fixture micro task prompt", permission=scope,
            output_schema=schema if schema is not None else WORKER_SCHEMA,
            budget=RunBudget(timeout_seconds=timeout), continuation=continuation)

    def execute(self, request: RunRequest, *, observer=None):
        return run(request, observer=observer or (lambda _facts: FEEDBACK_CONTINUE),
                   services=None, cancelled=lambda: False)

    def fixture_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class NativeInquiryTests(NativeRunCase):
    def test_child_lifecycle_does_not_change_the_root_inquiry_window(self):
        request = self.request()
        bridge = mock.Mock()
        collector = native_run._TurnCollector(request, native_run.ActivityPublisher(None),
                                               lambda _facts: FEEDBACK_CONTINUE, bridge=bridge)
        collector.evidence = native_run.TurnEvidence("root-session", request.cwd)
        collector.observe({"type": "system", "subtype": "init", "session_id": "root-session",
                           "cwd": request.cwd, "model": request.buddy.model})
        collector.observe({"type": "result", "parent_tool_use_id": "child-call", "session_id": "child-session"})
        collector.observe({"type": "system", "subtype": "init", "parent_tool_use_id": "child-call",
                           "session_id": "child-session"})
        bridge.activate.assert_called_once_with("root-session")
        bridge.deactivate.assert_not_called()

    def execute_inquiry(self, case: str, *, expected_status="ok"):
        self.fixture_case(case)
        request = self.request()
        identity = {"taskId": request.identity.task_id, "attemptId": request.identity.attempt_id,
                    "generation": request.identity.generation, "turnId": request.identity.turn_id}
        inquiry = prepare_inquiry_services(
            invocation_root=Path(request.private_state.invocation_root), identity=identity,
            input_sha256=hashlib.sha256(request.input_text.encode()).hexdigest(),
            attention_path=self.base / "attention.json",
            inquiry={"resultsPath": str(self.base / f"inquiry-{request.identity.attempt_id}.jsonl")})
        original_activate = InquiryBridge.activate

        def activate(bridge, session_id):
            self.bridge = bridge
            original_activate(bridge, session_id)
            queued = bridge._ask({"inquiryId": "q1", "question": "What is 6 times 7?"}, _owner_response)
            self.assertTrue(queued["ok"], queued)

        with mock.patch.object(InquiryBridge, "activate", activate):
            result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=native_run.RunServices(inquiry=inquiry), cancelled=lambda: False)
        report_ref = next(ref for ref in result.evidence_refs if ref.kind == "inquiry-report")
        if result.end.status != expected_status:
            stderr = next((ref for ref in result.evidence_refs if ref.kind == "native-stderr"), None)
            self.fail(f"{result.end.reason_code}: {result.end.message}\n"
                      + (Path(stderr.location).read_text(encoding="utf-8") if stderr else ""))
        return result, json.loads(Path(report_ref.location).read_text(encoding="utf-8"))

    def test_root_signed_answer_binds_to_running_turn(self):
        result, report = self.execute_inquiry("inquiry-answer")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(report["answered"], 1)
        self.assertEqual(report["unansweredDelivered"], 0)
        self.assertIn("Host inquiry q1", self.fixture_state()["inquiryInput"])
        self.assertIsNotNone(flag_value(self.fixture_state()["argv"], "--mcp-config"))

    def test_foreign_call_and_child_result_do_not_answer(self):
        for case in ("inquiry-wrong-call", "inquiry-child"):
            with self.subTest(case=case):
                result, report = self.execute_inquiry(case)
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertEqual(report["answered"], 0)
                self.assertEqual(report["unansweredDelivered"], 1)

    def test_assistant_text_and_unanswered_delivery_do_not_answer(self):
        result, report = self.execute_inquiry("inquiry-text-only")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(report["answered"], 0)
        self.assertEqual(report["unansweredDelivered"], 1)
        from hey_my_buddy.buddy.roles.run_execution import worker_delivery
        outcome, provenance = worker_delivery(result)
        self.assertEqual(outcome["disposition"], "attention")
        self.assertIsNone(native_run.validate_turn_provenance({
            "sessionId": result.native_identity.session_id, "provenance": provenance,
            "outcome": outcome, "resumeMode": "initial", "previousSessionId": None}))

    def test_closed_bridge_refuses_question_after_end(self):
        result, report = self.execute_inquiry("inquiry-unanswered")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(report["unansweredDelivered"], 1)
        late = self.bridge._ask({"inquiryId": "q2", "question": "Too late?"}, _owner_response)
        self.assertEqual(late["error"], "agent-not-running")

    def test_ordinary_tool_error_keeps_turn_alive_and_retains_native_error(self):
        for case, answered in (("inquiry-tool-error", 0), ("inquiry-tool-error-retry", 1)):
            with self.subTest(case=case):
                result, report = self.execute_inquiry(case)
                self.assertTrue(self.fixture_state()["continuedAfterInquiryError"])
                self.assertEqual(report["answered"], answered)
                self.assertEqual(report["unansweredDelivered"], 1 - answered)
                reference = next(ref for ref in result.evidence_refs if ref.kind == "inquiry-tool-failures")
                failures = json.loads(Path(reference.location).read_text(encoding="utf-8"))
                self.assertEqual(failures["records"][0]["error"], "fixture inquiry transport error: retry permitted")
                self.assertEqual(failures["records"][0]["toolCallId"], "toolu_inquiry_1")
                self.assertEqual(failures["dropped"], 0)

    def test_signed_refusal_continues_but_forged_refusal_fails(self):
        result, report = self.execute_inquiry("inquiry-signed-refusal")
        self.assertTrue(self.fixture_state()["continuedAfterInquiryError"])
        self.assertEqual(report["answered"], 0)
        result, _report = self.execute_inquiry("inquiry-forged-refusal", expected_status="error")
        self.assertEqual(result.end.reason_code, "invalid-tool-refusal")

    def test_late_input_cannot_run_an_unadmitted_followup_turn(self):
        with mock.patch.object(native_run, "_native_request_response", wraps=native_run._native_request_response) as response:
            result, report = self.execute_inquiry("inquiry-late-input")
        response.assert_not_called()
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(report["answered"], 0)
        self.assertEqual(report["unansweredDelivered"], 1)
        self.assertEqual(self.bridge.entries["q1"]["state"], "unavailable")
        self.assertNotIn("reason", self.bridge.entries["q1"])
        self.assertFalse((self.cwd / "unadmitted-followup-write.txt").exists())
        self.assertFalse(self.fixture_state().get("unadmittedFollowupRan", False))
        self.assertTrue(self.fixture_state()["interrupted"])
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        reference = next(ref for ref in result.evidence_refs if ref.kind == "native-observations")
        observations = json.loads(Path(reference.location).read_text(encoding="utf-8"))
        self.assertTrue(observations["stoppedAtRootResult"])
        self.assertTrue(observations["unadmittedFollowupInit"])
        self.assertEqual(observations["unadmittedFollowupMessages"], 2)
        self.assertEqual(observations["postRootControlRequests"], 1)
        self.assertEqual(result.value.parsed.value["outcome"]["summary"], "fixture work completed")

    def test_answered_inquiry_does_not_hide_unrelated_duplicate_init(self):
        result, report = self.execute_inquiry("inquiry-answered-duplicate-init", expected_status="error")
        self.assertEqual(report["answered"], 1)
        self.assertEqual(result.end.reason_code, "invalid-protocol")

    def test_slow_interrupt_ack_and_eof_cleanup_keep_successful_inquiry_turn(self):
        result, report = self.execute_inquiry("inquiry-slow-interrupt")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(report["unansweredDelivered"], 1)
        self.assertTrue(self.fixture_state()["interrupted"])
        self.assertTrue(self.fixture_state()["cleanupCompleted"])

    def test_failure_evidence_is_bounded(self):
        collector = native_run._TurnCollector(self.request(), native_run.ActivityPublisher(None),
                                               lambda _facts: FEEDBACK_CONTINUE)
        for number in range(12):
            collector._retain_inquiry_failure(str(number), "tool", "错" * 2000)
        self.assertEqual(len(collector.inquiry_tool_failures), 8)
        self.assertEqual(collector.inquiry_tool_failures_dropped, 4)
        self.assertLessEqual(len(collector.inquiry_tool_failures[0]["error"].encode()), 4000)


class Batch4FactsTests(NativeRunCase):
    def observations(self, result):
        ref = next(ref for ref in result.evidence_refs if ref.kind == "native-observations")
        return json.loads(Path(ref.location).read_bytes())

    def test_unread_catalog_continues_discovery_as_unknown(self):
        from hey_my_buddy.blackboard.catalog.catalog import canonical_payload
        self.fixture_case("invalid-catalog")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native", timeout_seconds=10,
                                cancelled=lambda: False)
        self.assertEqual(catalog["discoveries"][0]["status"], "unknown")
        self.assertEqual(canonical_payload(catalog)["discoveries"][0]["status"], "unknown")
        self.assertEqual(catalog["providers"][0]["models"], [])
        self.assertEqual(catalog["warnings"][0]["reason"],
                         {"code": "catalog.model-list-unreadable", "params": {"shape": "NoneType"}})
        self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_unread_catalog_continues_the_turn_without_checked_buddy(self):
        self.fixture_case("invalid-catalog")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertTrue(result.model_started)
        self.assertIsNone(result.buddy.checked.model)
        self.assertIsNone(result.buddy.checked.effort)
        facts = self.observations(result)
        self.assertEqual(facts["catalogStatus"], "unknown")
        self.assertEqual(facts["buddyStatus"], "unknown")
        self.assertEqual(facts["catalogWarnings"][0]["reason"]["code"], "catalog.model-list-unreadable")
        self.assertEqual(result.native_evidence.unconfirmed, ("model-catalog",))
        self.assertIsNone(result.native_evidence.unknown_events)

    def test_readable_empty_catalog_refuses_before_the_user_message(self):
        self.fixture_case("empty-catalog")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-buddy")
        self.assertIsNone(result.model_started)
        self.assertIsNone(result.buddy.checked.model)
        self.assertIsNone(result.buddy.checked.effort)
        self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_unknown_initialize_reply_continues_with_correlated_result_facts(self):
        self.fixture_case("unknown-initialize")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        facts = self.observations(result)
        self.assertEqual(facts["catalogStatus"], "unknown")
        self.assertEqual(facts["protocol"]["status"], "incomplete")
        self.assertEqual(facts["protocol"]["unreadResponses"], 1)
        self.assertEqual(facts["protocol"]["samples"][0]["requestId"], "hey-my-buddy-1")
        self.assertEqual(result.native_evidence.unknown_events.to_payload(),
                         {"countsByType": {"control-response-unread": 1}, "total": 1})
        self.assertEqual(result.native_evidence.unconfirmed, ("model-catalog",))
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native", timeout_seconds=10,
                                cancelled=lambda: False)
        self.assertEqual(catalog["discoveries"][0]["status"], "unknown")
        self.assertTrue(any(warning.get("diagnostic", {}).get("requestId") == "hey-my-buddy-1"
                            and "responseShape" in warning["diagnostic"]
                            for warning in catalog["warnings"]))
        self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_untyped_event_continues_and_reaches_role_and_retained_facts(self):
        self.fixture_case("missing-event-type")
        callbacks = []
        def observe(facts):
            callbacks.append(facts)
            return FEEDBACK_CONTINUE
        result = self.execute(self.request(), observer=observe)
        self.assertEqual(result.end.status, "ok", result.end.message)
        facts = self.observations(result)["protocol"]
        self.assertEqual(facts["unknownEvents"], 1)
        self.assertEqual(facts["status"], "incomplete")
        self.assertEqual(facts["samples"][0]["kind"], "event-type-unread")
        self.assertNotIn("credential-fragment", json.dumps(facts))
        expected = {"countsByType": {"event-type-unread": 1}, "total": 1}
        self.assertEqual(result.native_evidence.unknown_events.to_payload(), expected)
        self.assertEqual(result.native_evidence.unconfirmed, ())
        self.assertTrue(any(item["unknownEvents"] == expected for item in callbacks))
        self.assertTrue(all("protocolObservations" not in item for item in callbacks))

    def test_shared_native_facts_reach_all_three_role_results_without_samples(self):
        from hey_my_buddy.buddy.harnesses.run_contract import RunResult
        from hey_my_buddy.buddy.roles import run_execution
        self.fixture_case("unknown-initialize-with-untyped-event")
        for scope in ("write", "read"):
            with self.subTest(scope=scope):
                request = self.request(scope=scope, schema=READ_SCHEMA).model_copy(
                    update={"capture_evidence": False})
                result = RunResult.from_payload(self.execute(request).to_payload())
                self.assertEqual(result.end.status, "ok", result.end.message)
                verdict = {"stopReason": None, "elapsedMs": 1}
                payloads = (run_execution._fast_result(result, request, verdict),
                            run_execution._review_result(result, request, verdict),
                            run_execution._worker_facts(result))
                expected = {"unknownEvents": {"countsByType": {
                    "event-type-unread": 1, "control-response-unread": 1}, "total": 2},
                    "unconfirmed": ["model-catalog"]}
                for payload in payloads:
                    self.assertEqual(payload["nativeEvidence"], expected)
                    self.assertNotIn("unknownEvents", payload)
                    self.assertNotIn("unconfirmed", payload)
                    self.assertNotIn("samples", json.dumps(payload))
                    self.assertNotIn("credential-fragment", json.dumps(payload))

    def test_large_catalog_is_retained_after_confirmed_discovery_stop(self):
        self.fixture_case("oversized-catalog")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native", timeout_seconds=10,
                                cancelled=lambda: False)
        models = catalog["providers"][0]["models"]
        self.assertEqual(len(models), 201)
        self.assertEqual(models[-1]["id"], "model-200")
        self.assertEqual(models[-1]["efforts"], ["high"])

    def test_permission_requests_keep_all_denial_correlations(self):
        from hey_my_buddy.protocol.activity import ActivityPublisher
        collector = native_run._TurnCollector(self.request(), ActivityPublisher(None),
                                             lambda _facts: FEEDBACK_CONTINUE)
        connection = mock.Mock()
        connection.observations.return_value = {"unknownEvents": 0, "unreadResponses": 0}
        collector.install(connection)
        for index in range(160):
            collector.on_request({"type": "control_request", "request_id": str(index),
                                  "request": {"subtype": "can_use_tool", "tool_name": "Bash"}})
        self.assertEqual(connection.send.call_count, 160)
        self.assertEqual(collector.facts(settled=False, raw=None)["deniedInteractions"], 160)
        self.assertEqual(collector.denied[-1]["requestId"], "159")

    def test_initialization_frame_bound_still_refuses(self):
        from hey_my_buddy.protocol.activity import ActivityPublisher
        collector = native_run._TurnCollector(self.request(), ActivityPublisher(None),
                                             lambda _facts: FEEDBACK_CONTINUE)
        connection = mock.Mock()
        connection.observations.return_value = {"unknownEvents": 0, "unreadResponses": 0}
        collector.install(connection)
        for _ in range(128):
            collector.buffer({"type": "future"})
        with self.assertRaises(native_run.ClaudeProtocolError) as frames:
            collector.buffer({"type": "future"})
        self.assertEqual(frames.exception.code, "invalid-protocol")
    def test_unknown_interrupt_reply_does_not_confirm_acknowledgement(self):
        connection = mock.Mock()
        connection.call.return_value = None
        self.assertFalse(native_run._interrupt(connection))

    def test_interrupt_restores_deadline_and_original_cancellation_even_on_error(self):
        import threading
        import time
        for error in (None, native_run.ClaudeProtocolError("invalid-protocol", "fixture refusal")):
            with self.subTest(error=error):
                cancelled = threading.Event()
                deadline = time.monotonic() + 20
                connection = mock.Mock(deadline=deadline, cancelled=cancelled)

                def acknowledge(_request):
                    self.assertIsNot(connection.cancelled, cancelled)
                    self.assertLess(connection.deadline, deadline)
                    cancelled.set()  # Cancellation arriving during this control call.
                    if error is not None:
                        raise error
                    return {}

                connection.call.side_effect = acknowledge
                if error is None:
                    self.assertTrue(native_run._interrupt(connection, strict=True))
                else:
                    with self.assertRaises(native_run.ClaudeProtocolError):
                        native_run._interrupt(connection, strict=True)
                self.assertEqual(connection.deadline, deadline)
                self.assertIs(connection.cancelled, cancelled)
                self.assertTrue(connection.cancelled.is_set())

    def test_terminal_checks_separate_success_errors_and_unread_shapes(self):
        from hey_my_buddy.buddy.harnesses.claude.protocol import TurnEvidence
        for subtype, is_error, success in (("future", False, False), ("success", None, False),
                                           (None, False, False), (42, False, False),
                                           ("success", 0, False), ("success", False, True)):
            evidence = TurnEvidence("session", str(self.cwd))
            evidence.init_observed = True
            evidence.result = {"session_id": "session", "subtype": subtype, "is_error": is_error}
            with self.subTest(subtype=subtype, is_error=is_error):
                self.assertIs(native_run._check_terminal(evidence, "session"), success)

    def test_explicit_native_errors_remain_failures(self):
        for case in ("failed", "error-subtype-without-flag", "error-flag-with-unknown-subtype"):
            with self.subTest(case=case):
                self.fixture_case(case)
                result = self.execute(self.request())
                self.assertEqual(result.end.status, "error")
                self.assertEqual(result.end.reason_code, "native-turn-failed")
                self.assertIsNone(result.completion_evidence)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_unread_terminal_ends_unknown_without_completed_delivery(self):
        from hey_my_buddy.buddy.roles import run_execution
        for case in ("bad-subtype", "missing-is-error", "unknown-initialize-with-unread-terminal"):
            for scope in ("read", "write"):
                with self.subTest(case=case, scope=scope):
                    self.fixture_case(case)
                    callbacks = []
                    def observe(facts):
                        callbacks.append(facts)
                        return FEEDBACK_CONTINUE
                    request = self.request(scope=scope, schema=READ_SCHEMA)
                    result = self.execute(request, observer=observe)
                    self.assertEqual(result.end.status, "unknown")
                    self.assertEqual(result.end.reason_code, "native-terminal-unread")
                    self.assertIsNone(result.end.message)
                    self.assertIsNone(result.native_failure)
                    self.assertIsNone(result.completion_evidence)
                    self.assertNotIn("native-turn-facts", [ref.kind for ref in result.evidence_refs])
                    self.assertTrue(result.tool_evidence.value["streamComplete"])
                    self.assertEqual(result.stop_evidence.native.group_state, "gone")
                    self.assertTrue(result.model_started)
                    self.assertFalse(any(facts["settled"] for facts in callbacks))
                    self.assertIsNotNone(result.last_assistant_message)
                    verdict = {"stopReason": None, "elapsedMs": 1}
                    for payload in (run_execution._fast_result(result, request, verdict),
                                    run_execution._review_result(result, request, verdict)):
                        self.assertEqual(payload["status"], "unknown")
                        self.assertEqual(payload["code"], "native-terminal-unread")
                        self.assertNotIn("rawAnswer", payload)
                    payload = run_execution._worker_facts(result)
                    if case == "unknown-initialize-with-unread-terminal":
                        expected = {"unknownEvents": {"countsByType": {
                            "event-type-unread": 1, "control-response-unread": 1}, "total": 2},
                            "unconfirmed": ["model-catalog"]}
                        for projected in (payload,
                                          run_execution._fast_result(result, request, verdict),
                                          run_execution._review_result(result, request, verdict)):
                            self.assertEqual(projected["nativeEvidence"], expected)

    def test_unread_terminal_still_requires_confirmed_native_stop(self):
        self.fixture_case("bad-subtype")
        stop_native = native_run._stop_native
        def unconfirmed(spawn, deadline):
            _, exit_code = stop_native(spawn, deadline)
            return False, exit_code
        with mock.patch.object(native_run, "_stop_native", side_effect=unconfirmed):
            result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertIsNone(result.completion_evidence)

    def test_imported_turn_provenance_still_requires_explicit_success(self):
        result = self.execute(self.request())
        ref = next(ref for ref in result.evidence_refs if ref.kind == "native-turn-facts")
        facts = json.loads(Path(ref.location).read_bytes())
        provenance = {**facts, "turnEnd": "completed", "structuredOutputValidated": True}
        record = {"sessionId": result.native_identity.session_id, "provenance": provenance,
                  "resumeMode": "initial", "previousSessionId": None}
        self.assertIsNone(native_run.validate_turn_provenance(record))
        self.assertIsNone(native_run.validate_turn_provenance({**record, "provenance": {
            **provenance, "permissionDenials": 160, "modelUsage": [f"model-{i}" for i in range(40)]}}))
        for change in ({"resultSubtype": "future"}, {"resultIsError": None}, {"resultIsError": True},
                       {"permissionDenials": -1}, {"modelUsage": [""]}):
            with self.subTest(change=change):
                self.assertIsNotNone(native_run.validate_turn_provenance(
                    {**record, "provenance": {**provenance, **change}}))

        attention = {**record, "outcome": {"disposition": "attention"}, "provenance": {
            **provenance, "controllerAttention": True, "structuredOutputValidated": False,
            "permissionDenials": 0, "deniedControlRequestIds": [""]}}
        self.assertIsNotNone(native_run.validate_turn_provenance(attention))
        attention["provenance"]["deniedControlRequestIds"] = ["request-" + "x" * 300] * 160
        self.assertIsNone(native_run.validate_turn_provenance(attention))


class WorkerCarrierTests(NativeRunCase):
    def test_one_write_run_delivers_the_native_schema_value_and_stop_facts(self):
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.end.reason_code)
        # The schema verdict is the role's check; the driver reports the
        # delivered value with its own raw text and parsed form.
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")
        self.assertEqual(result.value.raw, json.dumps(result.value.parsed.value,
                                                     separators=(",", ":"), ensure_ascii=False))
        completion = result.completion_evidence
        self.assertTrue(completion.stream_end)
        self.assertTrue(result.model_started)
        # The confirmed root is the session the CLI was actually launched with.
        self.assertEqual(result.native_identity.session_id,
                         flag_value(self.fixture_state()["argv"], "--session-id"))
        self.assertNotIn("--resume", self.fixture_state()["argv"])
        self.assertEqual(result.tool_evidence.value["nativeIdentity"],
                         [{"sessionId": result.native_identity.session_id}])
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)

    def test_the_catalog_check_confirms_only_what_native_readback_proved(self):
        result = self.execute(self.request())
        checked = result.buddy.checked
        # Provider comes from initialize, while the directory confirms the
        # requested model and effort; no per-check ledger travels beside it.
        self.assertEqual(checked.provider.value, "anthropic")
        self.assertEqual(checked.model.value, BUDDY["model"])
        self.assertEqual(checked.effort.value, BUDDY["effort"])

    def test_a_model_outside_the_readable_catalog_is_refused_before_the_user_message(self):
        self.fixture_case("ok")
        request = self.request()
        broken = request.model_copy(update={"buddy": RunBuddy(
            provider="anthropic", model="claude-not-in-catalog", effort="high")})
        result = self.execute(broken)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-buddy")
        self.assertIsNone(result.model_started)
        checked = result.buddy.checked
        self.assertIsNone(checked.provider)
        self.assertIsNone(checked.model)
        self.assertIsNone(checked.effort)
        self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_an_effort_outside_the_readable_catalog_is_refused_before_the_user_message(self):
        request = self.request().model_copy(update={"buddy": RunBuddy(
            provider="anthropic", model=BUDDY["model"], effort="default")})
        result = self.execute(request)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-buddy")
        self.assertIsNone(result.model_started)
        self.assertEqual(self.fixture_state()["userTurns"], 0)
        self.assertIsNone(result.buddy.checked.model)
        self.assertIsNone(result.buddy.checked.effort)

    def test_long_native_tool_name_returns_the_result_and_stop_evidence(self):
        self.fixture_case("long-tool-name")
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNotNone(result.value)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        activity = [call.args[0] for call in endpoint.publish_activity.call_args_list
                    if call.args[0].get("toolName")]
        self.assertTrue(activity)
        self.assertEqual(activity[0]["toolName"], ("mcp__" + "s" * 60 + "__tool")[:64])

    def test_the_activity_fact_and_observed_evidence_survive_the_run(self):
        result = self.execute(self.request())
        activity = result.activity.value
        self.assertEqual(activity["phase"], "finishing")
        self.assertEqual(activity["counts"]["modelTurns"], 1)
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        # The observed model set is a bounded observation, never an identity.
        self.assertEqual(observations["observedModels"],
                         ["claude-haiku-4-5-20251001", "claude-opus-5-5[1m]"])
        self.assertEqual(observations["totalCostUsd"], 0.01)
        self.assertEqual(observations["sessionModel"], BUDDY["model"])
        turn_facts = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "native-turn-facts")).read_bytes())
        self.assertEqual(turn_facts["structuredOutputSource"], "json-schema")
        self.assertTrue(turn_facts["initObserved"])
        self.assertTrue(turn_facts["backgroundSettled"])
        self.assertIn("native-stderr", {ref.kind for ref in result.evidence_refs})

    def test_usage_comes_from_the_native_records_only(self):
        self.use_fixture(USAGE_FIXTURE)
        self.fixture_case("usage-ok")
        request = self.request(timeout=20).model_copy(update={"buddy": RunBuddy(
            provider="anthropic", model="claude-fixture-5", effort="high")})
        result = self.execute(request)
        self.assertEqual(result.end.status, "ok", result.end.message)
        usage = result.usage.value
        self.assertEqual(usage["source"], "claude/stream-json-result-usage")
        self.assertEqual(usage["completeness"], "complete")
        # The native prompt count excludes cached input; the canonical attempt
        # total adds the cache back instead of undercounting it.
        self.assertEqual(usage["inputTokens"], 88000)
        self.assertEqual(usage["cachedInputTokens"], 73000)
        self.assertEqual(usage["outputTokens"], 350)
        self.assertEqual(usage["reasoningOutputTokens"], 25)
        self.assertEqual(result.last_assistant_message.value["sourceId"], "msg-fixture-0002")
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertEqual(observations["observedModels"], ["claude-fixture-5"])
        self.assertNotIn("quota", observations)
        self.assertNotIn("rateLimitObservations", observations)

    def test_a_denied_native_permission_is_a_reported_fact_not_an_outcome(self):
        self.fixture_case("permission")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        # The refused interaction's own full record — native request and tool
        # identities included — travels in the retained evidence reference.
        records = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                       if ref.kind == "denied-interactions")).read_bytes())["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["toolName"], records[0]["requestId"]),
                         ("WebFetch", "perm-1"))
        # The outcome rewrite is the role's assembly; the delivered value keeps
        # its own completed fact for the role to judge.
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")

    def test_background_work_must_settle_before_the_result_is_usable(self):
        self.fixture_case("bg-running")
        unsettled = self.execute(self.request())
        self.assertEqual(unsettled.end.status, "error")
        self.assertEqual(unsettled.end.reason_code, "background-work-unsettled")
        self.assertIsNone(unsettled.completion_evidence)
        self.fixture_case("bg-settled")
        settled = self.execute(self.request())
        self.assertEqual(settled.end.status, "ok", settled.end.message)
        self.assertTrue(settled.completion_evidence.stream_end)

    def test_a_completed_task_notification_does_not_block_the_turn(self):
        # A foreground task reported by the real task_started/task_notification
        # pair has settled; the turn must not end as background-work-unsettled.
        self.fixture_case("bg-notification")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.end.reason_code)
        self.assertTrue(result.completion_evidence.stream_end)


class ReadCarrierTests(NativeRunCase):
    def argv_and_settings(self, result):
        argv = self.fixture_state()["argv"]
        settings = json.loads(Path(flag_value(argv, "--settings")).read_bytes())
        return argv, settings

    def test_one_review_read_takes_the_shared_read_posture_and_reports_it(self):
        result = self.execute(self.request(scope="read", schema=READ_SCHEMA))
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.parsed.value["buddyId"], "legal")
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["tools"], ["Glob", "Grep", "LS", "Read", "Skill"])
        self.assertEqual(policy.requested.value["permissionMode"], "default")
        self.assertEqual(policy.requested.value["disallowedTools"],
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])
        self.assertEqual(policy.requested.value["sandboxNetworkAllowedDomains"], ["*"])
        argv, settings = self.argv_and_settings(result)
        self.assertEqual(settings["sandbox"]["network"],
                         {"strictAllowlist": False, "allowedDomains": ["*"], "allowLocalBinding": True})
        self.assertEqual(flag_value(argv, "--disallowedTools").split(","),
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])

    def test_an_ordinary_worker_read_uses_the_same_open_network_posture(self):
        result = self.execute(self.request(scope="read"))
        self.assertEqual(result.end.status, "ok", result.end.message)
        policy = result.effective_policy.tools.requested.value
        self.assertEqual(policy["disallowedTools"],
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])
        self.assertEqual(policy["sandboxNetworkAllowedDomains"], ["*"])
        _argv, settings = self.argv_and_settings(result)
        self.assertEqual(settings["sandbox"]["network"]["allowedDomains"], ["*"])

    def test_the_unified_postures_match_the_native_permission_mapping(self):
        # Both read callers use the same native posture. Each run's argv is captured from
        # the fixture state immediately, before the next run overwrites it.
        from hey_my_buddy.buddy.harnesses.claude.config import (
            execution_args,
            permission_sandbox_settings,
        )

        def normalized(argv):
            return [flag_value(argv, flag) if flag in argv else None
                    for flag in ("--tools", "--permission-mode", "--disallowedTools")]

        session = "00000000-0000-4000-8000-000000000000"
        postures = (
            ("worker-write", {}, execution_args(session_id=session, model="m", effort="high",
                                                settings_path="/s.json", read_only=False,
                                                output_schema=WORKER_SCHEMA),
             permission_sandbox_settings("write")),
            ("worker-read", {"scope": "read"},
             execution_args(session_id=session, model="m", effort="high",
                            settings_path="/s.json", read_only=True, output_schema=WORKER_SCHEMA),
             permission_sandbox_settings("read")),
            ("review", {"scope": "read", "schema": READ_SCHEMA},
             execution_args(session_id=session, model="m", effort="high",
                            settings_path="/s.json", read_only=True, output_schema=READ_SCHEMA),
             permission_sandbox_settings("read")),
        )
        for name, request_values, legacy_argv, legacy_settings in postures:
            with self.subTest(posture=name):
                result = self.execute(self.request(**request_values))
                self.assertEqual(result.end.status, "ok", result.end.message)
                argv, settings = self.argv_and_settings(result)
                self.assertEqual(normalized(argv), normalized(legacy_argv))
                self.assertEqual(settings, legacy_settings)

    def test_a_write_run_keeps_the_registry_allowlist_and_its_subagent_tools(self):
        result = self.execute(self.request(scope="write"))
        self.assertEqual(result.end.status, "ok", result.end.message)
        policy = result.effective_policy.tools.requested.value
        self.assertIn("Agent", policy["tools"])
        self.assertNotIn("mcp__*", policy["disallowedTools"])
        _argv, settings = self.argv_and_settings(result)
        self.assertTrue(settings["sandbox"]["network"]["allowedDomains"])

    def test_the_shared_send_wait_drain_serves_both_carriers(self):
        # The same fixture run under both scopes settles through the same
        # primitive: both report the observed session, the explicit success
        # criteria and a stream complete only at the observed end.
        for scope in ("write", "read"):
            with self.subTest(scope=scope):
                result = self.execute(self.request(scope=scope))
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertTrue(result.completion_evidence.stream_end)
                # The observed native event count is a real count of this run.
                self.assertGreaterEqual(result.native_event_count, 1)


class RequestGateTests(NativeRunCase):
    def test_session_services_refuse_and_reconstruction_never_resumes_natively(self):
        request = self.request()
        with self.assertRaises(BoardError):
            self.execute(request.model_copy(update={"session_services": (
                SessionService(tool_names=["mcp__x__y"]),)}))
        # Native resume is checked once by WorkerRunExecutor.prepare; the
        # role-boundary regression lives in test_claude.ClaudeAdapterTests.
        # The reconstruction mode stays legal and never resumes natively.
        self.fixture_case("ok")
        reconstructed = self.execute(self.request(
            continuation=RunContinuation(mode="reconstructed-new-session",
                                         previous_session_id=str(uuid.uuid4()))))
        self.assertEqual(reconstructed.end.status, "ok", reconstructed.end.message)
        self.assertFalse(reconstructed.continuation.resumable)
        self.assertNotIn("--resume", self.fixture_state()["argv"])

    def test_the_module_declares_the_request_controls_it_consumes(self):
        self.assertEqual(native_run.supported_request_controls,
                         ())

    def test_the_module_carries_the_registered_role_surface(self):
        # 3-B2 delivers the wiring: the one native body also exposes every
        # operation the shared role executor consumes, and the legacy adapter
        # entries are gone — registration is the only switch left, and it is
        # the Host's line in the registry.
        for operation in ("run", "run_discovery", "check_preparation", "session_facts",
                          "validate_turn_provenance", "prepare_run_services"):
            self.assertTrue(callable(getattr(native_run, operation, None)), operation)
        from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
        description = ClaudeAdapter()
        self.assertFalse(any(hasattr(description, entry) for entry in
                             ("prepare", "start", "collect", "cancel",
                              "start_read_only_structured", "start_no_tool_structured")))
        self.assertEqual(description.validate_turn_provenance({"provenance": {}}),
                         "the Claude turn lacks native completion and initialization evidence")



class SettlementGuardTests(NativeRunCase):
    def test_model_output_before_the_user_message_is_rejected_with_its_facts_kept(self):
        self.use_fixture(STREAM_FIXTURE)
        # The premature frame is written before the initialize response, so
        # the boundary rejection follows from pipe order, not thread timing.
        self.fixture_case("early-before-response")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-started-early")
        # The premature tool fact was projected before the boundary rejection.
        package = result.tool_evidence.value
        self.assertEqual(list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))[0]["categories"], ["read"])
        self.assertFalse(package["streamComplete"])

    def test_the_native_identity_is_published_only_after_native_confirmation(self):
        # An unconfirmed handshake leaves the preallocated id out of the
        # native identity; the identity the stream did report stays in the
        # fact packages, never masquerading as this run's root.
        self.fixture_case("invalid-json")
        broken = self.execute(self.request(timeout=20))
        self.assertEqual(broken.end.status, "error")
        self.assertIsNone(broken.native_identity)
        # No native root was ever confirmed, and the package lists none.
        self.assertEqual(broken.tool_evidence.value["nativeIdentity"], [])
        self.assertTrue(broken.model_started, "the input send itself is still a fact")
        self.fixture_case("init-wrong-session")
        foreign = self.execute(self.request())
        self.assertEqual(foreign.end.status, "error")
        self.assertEqual(foreign.end.reason_code, "wrong-native-session")
        self.assertIsNone(foreign.native_identity)
        # The foreign identity the native stream actually reported.
        self.assertEqual(foreign.tool_evidence.value["nativeIdentity"],
                         [{"sessionId": "not-the-allocation"}])
        self.assertIsNone(foreign.continuation)

    def test_a_native_failure_keeps_the_observed_stream_end(self):
        # The transport's own EOF is a fact of the stream: an explicit native
        # failure does not erase it, and the business verdict travels in the
        # end status, the group disappearance in the stop evidence.
        self.fixture_case("failed")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        self.assertIsNone(result.completion_evidence)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)

    def test_wrong_session_and_duplicate_results_are_rejected(self):
        for case, reason in (("wrong-session", "wrong-native-session"),
                             ("duplicate-result", "invalid-protocol"),
                             ("no-result", "transport-error")):
            with self.subTest(case=case):
                self.fixture_case(case)
                result = self.execute(self.request())
                self.assertEqual(result.end.status, "error", result.end.message)
                self.assertEqual(result.end.reason_code, reason)

    def test_an_oversized_delivered_value_drops_alone_and_keeps_every_confirmed_fact(self):
        # A 600 KiB structured delivery — under the native 8 MiB frame bound,
        # over the run contract's value bound — must cost the value alone:
        # the confirmed stop, the checked buddy, the usage and the
        # completion evidence all still reach the caller.
        self.fixture_case("large-value")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.value)
        self.assertIsNotNone(result.completion_evidence)
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)
        self.assertEqual(result.buddy.checked.model.value, BUDDY["model"])
        self.assertIsNotNone(result.last_assistant_message)

    def test_a_quota_rejection_is_infrastructure_not_a_model_result(self):
        self.fixture_case("quota-rejected")
        result = self.execute(self.request(timeout=20))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "harness-error")
        self.assertEqual(result.end.message, "the Claude provider quota rejected this execution")
        failure = result.native_failure.value
        self.assertEqual(failure["nativeCode"], "five_hour")
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["source"], "claude/stream-json-rate-limit-event")
        self.assertEqual(failure["rejectionPhase"], "before-model")
        self.assertIsNone(result.value)
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertTrue(self.fixture_state()["interrupted"])
        # The observations package retains only the interrupt pair; the failure
        # travels in the common native-failure package.
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertNotIn("quotaFailure", observations)
        self.assertIs(observations["nativeInterruptRequested"], True)
        self.assertIs(observations["nativeInterruptAcknowledged"], True)
        self.fixture_case("ok")
        clean = self.execute(self.request())
        observations = json.loads(Path(next(ref.location for ref in clean.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertNotIn("quotaFailure", observations)
        self.assertNotIn("nativeInterruptRequested", observations)
        self.assertNotIn("nativeInterruptAcknowledged", observations)

    def test_overload_uses_the_common_failure_package_and_activity_phase(self):
        from hey_my_buddy.buddy.roles.run_execution import _worker_facts
        for case, phase in (("overload-before", "before-model"), ("overload-during", "during-model")):
            self.fixture_case(case)
            for scope, schema in (("write", WORKER_SCHEMA), ("read", READ_SCHEMA)):
                with self.subTest(case=case, scope=scope):
                    result = self.execute(self.request(scope=scope, schema=schema, timeout=20))
                    self.assertEqual(result.end.reason_code, "harness-error")
                    failure = _worker_facts(result)["quotaFailure"]
                    self.assertEqual(failure["code"], "model-overloaded")
                    self.assertEqual(failure["nativeCode"], "overloaded")
                    self.assertEqual(failure["rejectionPhase"], phase)

    def test_quota_failure_uses_the_common_package_on_worker_and_router_carriers(self):
        self.fixture_case("quota-rejected")
        for scope, schema in (("write", WORKER_SCHEMA), ("read", READ_SCHEMA)):
            with self.subTest(scope=scope):
                result = self.execute(self.request(scope=scope, schema=schema, timeout=20))
                self.assertEqual(result.end.reason_code, "harness-error")
                self.assertEqual(result.native_failure.value["rejectionPhase"], "before-model")
                self.assertEqual(result.native_failure.value["nativeCode"], "five_hour")

    def test_result_quota_rejection_survives_a_corrupt_trailing_frame_as_unknown_phase(self):
        self.fixture_case("result-quota-bad-tail")
        result = self.execute(self.request(timeout=20))
        self.assertEqual(result.end.reason_code, "invalid-protocol")
        self.assertEqual(result.native_failure.value["code"], "quota-exceeded")
        self.assertEqual(result.native_failure.value["source"], "claude/stream-json-result")
        self.assertEqual(result.native_failure.value["rejectionPhase"], "unknown")

    def test_cancellation_interrupts_the_native_run(self):
        self.fixture_case("hang")
        result = run(self.request(timeout=20), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: True)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "user-cancel")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertTrue(self.fixture_state()["interrupted"])
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        # The real reply of the interrupted peer, not the later group stop.
        self.assertIs(observations["nativeInterruptAcknowledged"], True)

    def test_an_unconfirmed_native_group_stop_reports_unknown_not_gone(self):
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        self.fixture_case("ok")
        observed: list[ProcessHandle] = []
        observe = ProcessHandle.shutdown_confirmed

        def unconfirmed(handle, settle_seconds: float = 2.0) -> bool:
            if "--version" in handle.process.args:
                return observe(handle, settle_seconds=settle_seconds)
            observed.append(handle)
            return False

        with mock.patch.object(ProcessHandle, "shutdown_confirmed", unconfirmed):
            result = self.execute(self.request())
        native = result.stop_evidence.native
        self.assertEqual(native.group_state, "unknown")
        self.assertIsNotNone(result.end.native_exit_code)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        # The EOF the drain already observed and the group's disappearance are
        # separate facts: an unconfirmed stop never back-infers the stream end.
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        # No orphan: the halt really terminated the fake CLI's group.
        handle = observed[0]
        self.assertIsNotNone(handle.process.poll())
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.5))

    def test_a_late_unrepresentable_package_keeps_every_other_observed_fact(self):
        self.fixture_case("ok")
        with mock.patch("hey_my_buddy.buddy.harnesses.claude.protocol.TurnEvidence.token_usage",
                        return_value={"inputTokens": "not-a-number"}):
            result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")
        # No completion-tool receipt exists on this carrier; the native
        # schema's own stream-end fact is what completion evidence carries.
        self.assertTrue(result.completion_evidence.stream_end)


class ObserverContractTests(NativeRunCase):
    def stopping_observer(self):
        seen: list[dict] = []

        def observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        return observer, seen

    def test_a_tool_fact_stops_the_run_through_the_observer_feedback(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("clean")
        observer, seen = self.stopping_observer()
        result = self.execute(self.request(), observer=observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertTrue(list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))))
        # The cumulative counts reached the role before the stop; unknown
        # events stay the honest absence this harness never classified.
        self.assertTrue(any(facts["toolCalls"] > 0 for facts in seen))
        self.assertTrue(all(facts["unknownEvents"] is None for facts in seen))

    def test_a_denied_interaction_reaches_the_role_before_further_waiting(self):
        self.fixture_case("permission")
        observer, seen = self.stopping_observer()

        def denied_watcher(facts):
            seen.append(dict(facts))
            if int(facts.get("deniedInteractions") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.request(), observer=denied_watcher)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(any(facts["deniedInteractions"] > 0 for facts in seen))
        # The peer received its deny answer before the stop took effect.
        self.assertEqual(self.fixture_state()["permissionReply"]["behavior"], "deny")

    def test_a_correction_is_refused_this_harness_takes_none(self):
        self.fixture_case("ok")

        def correcting(_facts):
            return RunFeedback(action="correct", input_text="again")
        with self.assertRaises(BoardError) as caught:
            self.execute(self.request(), observer=correcting)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.fixture_state()["userTurns"], 1)


class CatalogAccountTests(unittest.TestCase):
    def account_status(self, initialize):
        return native_run._catalog({"models": [], **initialize}, "fixture")["discoveries"][0]["accountStatus"]

    def test_subscription_account_without_token_source_is_confirmed(self):
        account = {"apiProvider": "firstParty", "email": "subscriber@example.invalid",
                   "organization": "fixture-organization", "subscriptionType": "max"}
        self.assertEqual(self.account_status({"account": account}), "confirmed")
        self.assertEqual(self.account_status({"account": {**account, "tokenSource": None}}), "confirmed")

    def test_nonempty_account_facts_do_not_select_a_provider_or_login_method(self):
        for field, value in (("email", "subscriber@example.invalid"), ("organization", "fixture-organization"),
                             ("subscriptionType", "max"), ("tokenSource", "subscription"),
                             ("tokenSource", "none"), ("apiKeySource", "ANTHROPIC_API_KEY"),
                             ("futureAccountField", {"identity": "fixture"})):
            for provider in (None, ""):
                with self.subTest(field=field, value=value, provider=provider):
                    self.assertEqual(self.account_status({"account": {"apiProvider": provider, field: value}}),
                                     "confirmed")

    def test_provider_only_accounts_are_confirmed(self):
        for provider in ("firstParty", "bedrock", "vertex", "future-provider"):
            with self.subTest(provider=provider):
                self.assertEqual(self.account_status({"account": {"apiProvider": provider}}), "confirmed")

    def test_absent_malformed_and_empty_account_facts_are_unknown(self):
        self.assertEqual(self.account_status({}), "unknown")
        for account in (None, "account", [], ["account"], True, 1, {}, {"apiProvider": ""},
                        {"apiProvider": None},
                        {"apiProvider": "", "email": "", "tokenSource": None,
                         "futureAccountField": {}, "anotherField": [], "flag": False, "count": 0}):
            with self.subTest(account=account):
                self.assertEqual(self.account_status({"account": account}), "unknown")


class DiscoveryTests(NativeRunCase):
    def test_non_first_party_handshake_can_be_selected_and_executed(self):
        from hey_my_buddy.blackboard.catalog.catalog import CatalogView, _check_recorded, discovery_payload

        self.fixture_case("third-party")
        os.environ["ANTHROPIC_BASE_URL"] = "https://shell-only.example.invalid"
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "third-party-discovery",
                                native_root=self.base / "third-party-native", timeout_seconds=25,
                                cancelled=lambda: False)
        view = CatalogView(discovery_payload(catalog))
        selected = {"harness": "claude", **BUDDY, "provider": "bedrock"}
        self.assertEqual(_check_recorded(selected, view, pending_efforts=None), selected)
        buddies = view.proposed_buddies()
        self.assertTrue(buddies)
        self.assertTrue(all(p["provider"] == "bedrock" and p["buddyId"].startswith("claude:bedrock:")
                            for p in buddies))
        self.assertEqual(catalog["discoveries"][0]["accountStatus"], "confirmed")
        self.assertEqual(self.fixture_state()["userTurns"], 0)
        for flag in ("--restricted", "--setting-sources", "--strict-mcp-config", "--mcp-config", "--safe-mode"):
            self.assertNotIn(flag, self.fixture_state()["argv"])
        request = self.request().model_copy(update={"buddy": RunBuddy(**{k: selected[k] for k in ("provider", "model", "effort")})})
        result = self.execute(request)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.buddy.checked.provider.value, "bedrock")
        state = self.fixture_state()
        self.assertIn("--no-session-persistence", state["argv"])
        self.assertEqual(state["seenAnthropicEnv"], [])
        settings = json.loads(Path(flag_value(state["argv"], "--settings")).read_text(encoding="utf-8"))
        self.assertEqual(settings, native_run.permission_sandbox_settings("write"))

    def test_unknown_account_does_not_require_a_login_proof_process(self):
        for case in ("empty-account", "invalid-account", "null-account", "missing-account"):
            with self.subTest(case=case):
                self.fixture_case(case)
                catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / f"discovery-{case}",
                                        native_root=self.base / f"native-{case}", timeout_seconds=25,
                                        cancelled=lambda: False)
                self.assertEqual(catalog["discoveries"][0]["accountStatus"], "unknown")
                self.assertEqual(catalog["providers"][0]["provider"], "unknown")
                self.assertNotIn("authStatusRuns", self.fixture_state())
                result = self.execute(self.request())
                self.assertEqual(result.end.status, "ok", result.end.message)

    def test_account_facts_are_confirmed_in_one_initialize_without_a_user_message(self):
        for case in ("ok", "null-token", "token-source", "api-key-source", "provider-only"):
            with self.subTest(case=case):
                self.fixture_case(case)
                previous_runs = self.fixture_state()["runs"] if self.state_path.exists() else 0
                catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / f"discovery-{case}",
                                        native_root=self.base / f"native-{case}", timeout_seconds=25,
                                        cancelled=lambda: False)
                self.assertEqual(catalog["discoveries"][0]["accountStatus"], "confirmed")
                state = self.fixture_state()
                self.assertTrue(state["initialize"])
                self.assertEqual(state["userTurns"], 0)
                self.assertEqual(state["runs"], previous_runs + 1)
                self.assertNotIn("authStatusRuns", state)

    def test_early_fixture_is_ordered_before_initialize_acknowledgement(self):
        self.fixture_case("early-result")
        result = self.execute(self.request())
        self.assertEqual(result.end.reason_code, "native-turn-started-early")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_discovery_initializes_only_and_never_sends_a_user_message(self):
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discover-invocation",
                                native_root=self.base / "discover-native", timeout_seconds=25,
                                cancelled=lambda: False)
        models = [model["id"] for model in catalog["providers"][0]["models"]]
        self.assertIn(BUDDY["model"], models)
        # The default alias never becomes a catalog identity.
        self.assertTrue(all(model != "default" for model in models))
        state = self.fixture_state()
        self.assertFalse(state["userTurns"])
        self.assertTrue(state["initialize"])
        self.assertIn("--no-session-persistence", state["argv"])
        self.assertTrue((self.base / "discover-invocation" / "native.stderr.log").is_file())

    def test_discovery_failures_carry_the_actual_native_stop_fact(self):
        # A pre-spawn refusal never started anything: the known never-started
        # fact, not a guess from the error code — and no native child exists.
        with mock.patch.object(native_run, "launch", side_effect=native_run.ClaudeUnavailable("missing CLI")):
            with self.assertRaises(native_run.ClaudeUnavailable) as caught:
                run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-pre",
                              native_root=self.base / "ds-pre-n", timeout_seconds=25, cancelled=lambda: False)
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
        self.assertFalse(self.state_path.exists())
        # A settled refusal after a confirmed stop carries the true fact, and
        # the outer layer may recycle its directory on it.
        self.fixture_case("initialize-error")
        with self.assertRaises(native_run.ClaudeProtocolError) as caught:
            run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-ok",
                          native_root=self.base / "ds-ok-n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "native-rpc-error")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)

    def test_discovery_metadata_error_with_an_unknown_stop_reports_false(self):
        # An unread catalog continues, but an unobserved native stop
        # must stay false: the outer layer keeps the directory for inspection
        # instead of trusting the error to mean the group is gone.
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        self.fixture_case("invalid-catalog")
        observe = ProcessHandle.shutdown_confirmed
        def unknown_native(handle, settle_seconds=2.0):
            return observe(handle, settle_seconds=settle_seconds) if "--version" in handle.process.args else False
        with mock.patch.object(ProcessHandle, "shutdown_confirmed", unknown_native):
            with self.assertRaises(native_run.ClaudeProtocolError) as caught:
                run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-unk",
                              native_root=self.base / "ds-unk-n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "native-shutdown-failed")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
        # The unknown catalog cannot substitute for actual stop evidence.

    def test_discovery_refuses_an_unusable_cli(self):
        select_command(os.environ, self.base, "claude", [str(self.base / "missing-cli")])
        with self.assertRaises(native_run.ClaudeProtocolError) as caught:
            run_discovery(cwd=str(self.cwd), invocation_root=self.base / "d3",
                          native_root=self.base / "d3n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "harness-unavailable")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)


class RoleSurfaceTests(NativeRunCase):
    """The operations the shared role executor consumes beyond the run itself."""

    def test_check_preparation_refuses_before_any_spawn(self):
        from hey_my_buddy.buddy.harnesses.claude.config import ClaudeUnavailable
        native_run.check_preparation({"provider": "bedrock"},
                                     {**os.environ, "ANTHROPIC_BASE_URL": "https://gateway.example"})
        with mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1", "HMB_CLAUDE_CLI": str(self.base / "nope")}):
            with mock.patch.object(native_run, "cli_command", side_effect=ClaudeUnavailable("CLI missing")):
                with self.assertRaises(BoardError) as caught:
                    native_run.check_preparation({"provider": "anthropic"}, dict(os.environ))
        self.assertEqual(caught.exception.code, "HARNESS_UNAVAILABLE")
        self.assertFalse(self.state_path.exists(), "no native child may start from a preparation check")
        native_run.check_preparation({"provider": "anthropic"}, dict(os.environ))

    def test_the_optional_live_service_binding_consumes_the_endpoint(self):
        binding = native_run.prepare_run_services()
        self.assertEqual(binding, native_run.RunServices())
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(endpoint.publish_activity.call_args.args[0], result.activity.value)

    def test_session_facts_report_disabled_persistence(self):
        facts = native_run.session_facts(self.base / "native", "session-x")
        self.assertEqual(facts["harness"], "claude")
        self.assertEqual((facts["sessionId"], facts["captured"]), ("session-x", True))
        self.assertEqual(facts["storageScope"], "none")
        self.assertEqual(facts["nativeAppVisibility"], "none")
        self.assertNotIn("bindingPresent", facts)
        empty = native_run.session_facts(self.base / "native", None)
        self.assertFalse(empty["captured"])

    def test_native_events_publish_the_identity_bound_shared_endpoint_activity(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("clean")
        request = self.request()
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["claude"])
        with mock.patch.object(endpoint, "publish_activity", wraps=endpoint.publish_activity) as publish:
            result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        payloads = [call.args[0] for call in publish.call_args_list]
        self.assertEqual([payload["phase"] for payload in payloads],
                         ["waiting-model", "starting", "tool-running", "streaming-model", "finishing"])
        self.assertEqual(payloads[-1]["counts"], {"modelTurns": 1, "toolCalls": 1})
        self.assertTrue(all(normalize_activity(payload) == payload for payload in payloads))
        self.assertTrue(all(is_newer(new, old) for old, new in zip(payloads, payloads[1:])))
        snapshot = json.loads(endpoint.observe_local(fields=("activity",), limit=10))
        self.assertEqual(snapshot["activity"], result.activity.value)
        self.assertFalse(Path(request.private_state.invocation_root, "activity.json").exists())

    def test_refused_activity_publication_keeps_native_final_activity(self):
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = False
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertEqual(result.activity.value["counts"]["modelTurns"], 1)
        self.assertGreater(endpoint.publish_activity.call_count, 0)
        publisher = native_run.ActivityPublisher(endpoint.publish_activity)
        self.assertFalse(publisher.publish(result.activity.value))
        self.assertIsNone(publisher.current(), "refusal is never a transmitted receipt")

    def test_unknown_native_tools_keep_actual_callback_counts_and_final_facts(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("unknown")
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 2, "toolCalls": 2})
        receipts = [call.args[0] for call in endpoint.publish_activity.call_args_list]
        self.assertEqual(receipts[-1], result.activity.value)
        self.assertEqual([receipt["phase"] for receipt in receipts],
                         ["waiting-model", "starting", "tool-running", "finishing"])

    def test_native_failure_preserves_the_actual_callback_activity(self):
        self.fixture_case("failed")
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertEqual(endpoint.publish_activity.call_args.args[0], result.activity.value)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")


class StructuredDeliveryTests(NativeRunCase):
    """The CLI's built-in StructuredOutput delivery is completion evidence, not a tool call.

    Every case drives the real run seam with the stream fixture's scripted
    native frames; the identification is the projection's own native facts —
    this run's schema command line, the confirmed root, the exact built-in
    name, the call id and the final structured value it delivered.
    """

    def delivered(self):
        return {"buddyId": "legal", "note": "Read-only stream fixture", "evidence": []}

    def review_request(self, **kwargs):
        return self.request(scope="read", schema=READ_SCHEMA, **kwargs)

    def package_of(self, result):
        self.assertEqual(result.end.status, "ok", result.end.message)
        return result.tool_evidence.value

    def test_a_verified_delivery_is_completion_evidence_and_never_a_tool_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-clean")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The real Read call counts and settles; the delivery and its
        # tool_result pairing are gone from the ordinary tool facts.
        self.assertEqual([(call["categories"], call["phases"]) for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))],
                         [(["read"], ["end", "start"])])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")))
        # The verified delivery is excluded from the tool facts by its call
        # identity and delivered as the run's own value.
        self.assertEqual([call["callId"] for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))], ["toolu_1"])
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.value.parsed.value, self.delivered())

    def test_a_zero_tool_budget_accepts_a_structured_review(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-only")
        seen: list[dict] = []

        def budget_observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.review_request(), observer=budget_observer)
        package = self.package_of(result)
        self.assertEqual((package["toolCalls"], list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))), (0, []))
        # The delivery is the run's own value, never a charged tool call.
        self.assertEqual(result.value.parsed.value, self.delivered())
        # The role's cumulative count never charged the value carrier.
        self.assertTrue(all(facts["toolCalls"] == 0 for facts in seen))
        self.assertTrue(any(facts["settled"] for facts in seen))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")))

    def test_a_zero_budget_unqualified_delivery_stops_the_run_at_settlement(self):
        # The unverified candidate waits for its association mid-run, but the
        # settlement that precedes the role's settled facts releases it: the
        # role's budget stop fires on the same count the package publishes.
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        seen: list[dict] = []

        def budget_observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.review_request(), observer=budget_observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(any(facts["settled"] and facts["toolCalls"] == 1 for facts in seen))
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(len(list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))), 1)
        # The unqualified call is no one's delivery: it stays an ordinary
        # counted tool fact under its own native call id.
        self.assertEqual([call["callId"] for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))], ["toolu_so_1"])

    def shared_review_observer(self, budget: int):
        try:
            from hey_my_buddy.buddy.roles.run_observers import ReviewObserver
        except ImportError:
            self.skipTest("the shared ReviewObserver is not in this baseline; its "
                          "authoritative run is the 3-C validation copy")
        return ReviewObserver(budget, READ_SCHEMA, "review the frozen packet", can_correct=False)

    def test_the_shared_review_observer_stops_an_unqualified_zero_budget_delivery(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        review = self.shared_review_observer(0)
        result = self.execute(self.review_request(), observer=review.observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(review.stop_reason, "readonly-budget-exhausted")
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_the_shared_review_observer_accepts_a_legal_zero_budget_delivery(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-only")
        review = self.shared_review_observer(0)
        result = self.execute(self.review_request(), observer=review.observer)
        package = self.package_of(result)
        self.assertIsNone(review.stop_reason)
        self.assertEqual((package["toolCalls"], list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))), (0, []))
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")))

    def test_a_conflicting_full_input_on_one_call_refuses_the_exemption(self):
        for case in ("structured-conflict-ab", "structured-conflict-ba"):
            with self.subTest(case=case):
                self.use_fixture(STREAM_FIXTURE)
                self.fixture_case(case)
                result = self.execute(self.review_request())
                package = self.package_of(result)
                self.assertEqual(package["toolCalls"], 1)
                self.assertEqual([(call["categories"], call["phases"]) for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))],
                                 [(["other"], ["end", "start"])])
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                                 tool_evidence.TOOLS_FORBIDDEN)

    def test_an_evicted_first_input_still_refuses_a_conflicting_second(self):
        # Nine delivery uses exhaust the input retention bound; the first
        # call's later, different complete input matches the final value, but
        # the conflict its first input recorded survives the eviction: all
        # nine distinct calls stay counted and none is exempted.
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-evict-conflict")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 9)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_an_evicted_input_reproves_the_value_when_the_same_input_returns(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-evict-same")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The consistent repeat re-proves c1's delivery after the bound evicted
        # its first full text; the eight mismatched calls stay counted and the
        # re-proved delivery alone is the run's own value.
        self.assertEqual(package["toolCalls"], 8)
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_duplicate_stream_and_assistant_projections_exclude_one_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-dupes")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))),
                         (0, 0, []))
        # The one verified delivery is excluded from the facts and delivered
        # as the run's own value.
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")))

    def test_a_bare_name_without_the_value_association_stays_an_ordinary_tool_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([call["categories"] for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))], [["other"]])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)
        # The turn still completed; the call simply proves no delivery.
        self.assertEqual([call["callId"] for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))], ["toolu_so_1"])

    def test_a_mounted_same_name_mcp_tool_is_never_exempted(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-mcp")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([(call["callId"], call["categories"], call["phases"])
                          for call in tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state"))],
                         [("toolu_1", ["other"], ["end", "start"])])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_a_subagent_substream_same_name_call_keeps_its_own_facts(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-subagent")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The root's delivery is excluded; the subagent's look-alike call stays
        # a fact under its parent-call identity and fails the root check.
        self.assertEqual(package["toolCalls"], 1)
        subagent_calls = list(tool_evidence.read_tool_calls(package, state_dir=Path(self.base / "state")))
        self.assertEqual(len(subagent_calls), 1)
        self.assertTrue(all("callId" in call["nativeIdentity"] and not call["belongsToRoot"]
                            for call in subagent_calls))
        # The look-alike call settles as a disallowed other call; its foreign
        # identity is kept in the facts on top of that.
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(self.base / "state")),
                         tool_evidence.TOOLS_FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
