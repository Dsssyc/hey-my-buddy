"""Shared native facts and role verdicts using synthetic run results only."""
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.buddy.roles import inline_run, router, run_execution, structured_call, turn_io
from hey_my_buddy.buddy.roles.run_observers import FastCorrection
from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
from buddy.harnesses.test_run_contract import full_request, full_result


class NativeEvidenceRoleTests(unittest.TestCase):
    def request(self, harness="zcode"):
        fields = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        fields.update(harness=harness, permission="none", sessionServices=[], captureEvidence=False)
        return rc.RunRequest.from_payload(fields)

    def result(self, harness="zcode", *, evidence=None, status="ok"):
        return full_result().model_copy(update={
            "harness": harness, "native_evidence": evidence, "provider_rejection": None,
            "end": rc.RunEnd(status=status, reason_code="native-end-unread" if status == "unknown" else None),
            "value": rc.RunValue(schema_status="valid", raw='{"answer":"value"}')})

    def project(self, result):
        request = self.request(result.harness)
        # Codex projects its old native facts at the top level; the other
        # fast carriers use a nested projection when capture is requested.
        module = SimpleNamespace(native_evidence=lambda _result: {"eventCount": 3})
        with mock.patch.dict(RUN_SEAMS, {result.harness: module}):
            return [run_execution._fast_result(result, request, {"stopReason": None, "elapsedMs": 1}),
                    run_execution._review_result(result, request, {"stopReason": None, "elapsedMs": 1}),
                    run_execution._worker_facts(result)]

    def test_confirmed_readback_reaches_all_role_receipts_without_filling_unknowns(self):
        requested = rc.RunBuddy(provider="requested-provider", model="requested-model", effort="off")
        checked = rc.CheckedBuddy(provider=rc.CheckedValue(value="native-provider"),
                                  model=rc.CheckedValue(value="native-model"),
                                  effort=rc.CheckedValue(value="high"))
        expected = {"provider": "native-provider", "model": "native-model", "effort": "high"}
        for harness in ("codex", "claude", "dsh", "zcode"):
            result = self.result(harness).model_copy(update={"buddy": rc.ResultBuddy(requested=requested, checked=checked)})
            for payload in self.project(result):
                with self.subTest(harness=harness):
                    self.assertEqual(payload["resolved"], expected)
                    self.assertEqual(payload["requested"], requested.to_payload())
                    self.assertNotIn("observed", payload)
            for missing in ("provider", "model", "effort"):
                partial = checked.model_copy(update={missing: None})
                result = result.model_copy(update={"buddy": rc.ResultBuddy(requested=requested, checked=partial)})
                for payload in self.project(result):
                    with self.subTest(harness=harness, missing=missing):
                        self.assertIsNone(payload["resolved"])
                        self.assertNotIn("observed", payload)

    def test_claude_without_a_projection_does_not_emit_empty_native_evidence(self):
        request = self.request("claude").model_copy(update={"capture_evidence": True})
        with mock.patch.dict(RUN_SEAMS, {"claude": SimpleNamespace(native_evidence=None)}):
            payload = run_execution._fast_result(self.result("claude"), request,
                                                  {"stopReason": None, "elapsedMs": 1})
            self.assertNotIn("nativeEvidence", payload)
            result = self.result("claude", evidence=rc.NativeEvidence(unconfirmed=["model-catalog"]))
            payload = run_execution._fast_result(result, request, {"stopReason": None, "elapsedMs": 1})
            self.assertEqual(payload["nativeEvidence"], {"unconfirmed": ["model-catalog"]})

    def test_publication_does_not_replace_the_collectors_single_stop_gate(self):
        request = self.request().model_copy(update={"permission": "write"})
        result = self.result().model_copy(update={
            "identity": request.identity,
            "stop_evidence": rc.StopEvidence(native=rc.StopLayer(group_state="unknown"))})
        turn = {"sessionId": "native-session", "outcome": {"disposition": "completed"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.json").write_text("{}", encoding="utf-8")
            control = {"operation": "worker", "harness": "zcode", "timeoutSeconds": None,
                       "inputFile": str(root / "input.json"), "nativeRoot": directory}
            handle = run_execution.InlineRunHandle(control, request.identity, grace_seconds=0)
            with mock.patch.object(run_execution, "worker_request", return_value=(request, None, None, None)), \
                 mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)), \
                 mock.patch.object(inline_run, "run_harness", return_value=result), \
                 mock.patch.object(run_execution, "_worker_result", return_value=turn) as publish:
                handle.result, handle.request, handle.verdict, handle.exit_code = inline_run.execute(control, handle)
            publish.assert_called_once()
            self.assertIs(handle.turn_record, turn)
            self.assertEqual(handle.exit_code, 0)
            handle.done.set()
            context = SimpleNamespace(turn_input={"resumeMode": "initial"}, effective_workspace={},
                                      turn_output_file=lambda: root / "turn-output.json")
            module = SimpleNamespace(session_facts=lambda _root, _session: {})
            executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
            with mock.patch.object(turn_io, "seal_workspace") as seal, \
                 mock.patch.object(run_execution, "_artifacts") as artifacts:
                outcome = executor.collect(handle, context)
            self.assertEqual(outcome.status, "failed")
            self.assertFalse(outcome.shutdown_confirmed)
            self.assertNotIn("turn", outcome.result)
            seal.assert_not_called()
            artifacts.assert_not_called()

    def test_shared_native_facts_have_one_location_in_all_three_results(self):
        evidence = rc.NativeEvidence(unknown_events={"countsByType": {"future": 2}, "total": 2},
                                     unconfirmed=["tool-scope", "buddy-readback"])
        expected = evidence.to_payload()
        for harness in ("codex", "claude", "zcode", "dsh"):
            for payload in self.project(self.result(harness, evidence=evidence)):
                with self.subTest(harness=harness, status=payload["status"]):
                    self.assertEqual(payload["nativeEvidence"], expected)
                    self.assertNotIn("unknownEvents", payload)
                    self.assertNotIn("unconfirmed", payload)

    def test_absent_and_empty_facts_are_omitted_from_every_result(self):
        for evidence in (None, rc.NativeEvidence(), rc.NativeEvidence(
                unknown_events={"countsByType": {}, "total": 0})):
            for harness in ("codex", "claude", "zcode", "dsh"):
                for payload in self.project(self.result(harness, evidence=evidence)):
                    self.assertNotIn("nativeEvidence", payload)

    def test_fast_native_projection_merges_with_the_shared_facts(self):
        evidence = rc.NativeEvidence(unconfirmed=["tool-scope"])
        request = self.request().model_copy(update={"capture_evidence": True})
        module = SimpleNamespace(native_evidence=lambda _result: {"eventCount": 3})
        with mock.patch.dict(RUN_SEAMS, {"zcode": module}):
            payload = run_execution._fast_result(self.result(evidence=evidence), request,
                                                  {"stopReason": None, "elapsedMs": 1})
        self.assertEqual(payload["nativeEvidence"], {"unconfirmed": ["tool-scope"], "eventCount": 3})

    def test_worker_permission_merges_with_the_shared_facts(self):
        result = self.result(evidence=rc.NativeEvidence(unconfirmed=["tool-scope"]))
        request = self.request().model_copy(update={"permission": "write"})
        payload = run_execution._worker_facts(result, request)
        self.assertEqual(payload["nativeEvidence"], {"unconfirmed": ["tool-scope"],
                                                    "permission": {"level": "write",
                                                                   "enforcement": PERMISSION_LEVELS["zcode"]["write"]}})

    def test_unknown_worker_end_keeps_facts_and_partial_artifacts_without_delivery(self):
        result = self.result(status="unknown", evidence=rc.NativeEvidence(unconfirmed=["thread-receipt"]))
        module = SimpleNamespace(session_facts=lambda _root, _id: {"present": True},
                                 validate_turn_provenance=mock.Mock())
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with tempfile.TemporaryDirectory() as directory:
            context = ExecutionContext("task", "attempt", 1, {}, Path(directory), {}, {}, session_record_path=None)
            context.turn = {"input": {"resumeMode": "initial"}}
            context.effective_workspace = {"partial": True}
            control = {"nativeRoot": directory, "harness": "zcode", "sessionRecordPath": None}
            handle = SimpleNamespace(role_run_control=control, role_run_identity=result.identity,
                                     role_run_original_identity=result.identity, result=result,
                                     role_run_active_control=control, role_run_active_identity=result.identity,
                                     cancel_requested=False, turn_record=None, error=None,
                                     exit_code=0, shutdown_confirmed=lambda: True)
            partial = [{"kind": "partial-workspace", "location": directory}]
            with mock.patch.object(run_execution, "_read_request", return_value=self.request()), \
                    mock.patch.object(run_execution, "_artifacts", return_value=partial), \
                    mock.patch.object(run_execution, "worker_delivery") as delivery, \
                    mock.patch.object(turn_io, "seal_workspace") as seal:
                outcome = executor.collect(handle, context)
            delivery.assert_not_called()
            seal.assert_not_called()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "native-end-unread")
        self.assertEqual(outcome.result["tokenUsage"], result.usage.value)
        self.assertEqual(outcome.result["lastAssistantMessage"]["text"], "done")
        self.assertTrue(outcome.result["nativeSession"]["present"])
        self.assertEqual(outcome.result["workspaceManifest"], {"partial": True})
        self.assertEqual(outcome.artifacts, partial)

    def router_collect(self, payload, mode="fast"):
        context = SimpleNamespace(decision_input={"buddy": {}, "budget": {},
                                                 "routingMode": mode, "buddies": [{"buddyId": "chosen"}]})
        handle = SimpleNamespace(role_run_control={"operation": mode},
                                 exit_code=0, shutdown_confirmed=lambda: True)
        project = "read_review_result" if mode == "review" else "read_fast_result"
        with mock.patch("hey_my_buddy.buddy.roles.run_execution." + project, return_value=payload), \
                mock.patch.object(router.router, "routing_facts", return_value={}):
            return router.DecisionAdapter().collect(handle, context)

    def test_unknown_router_end_never_adopts_an_answer_and_keeps_native_facts(self):
        evidence = rc.NativeEvidence(unknown_events={"countsByType": {"future": 1}, "total": 1})
        for mode, payload in zip(("fast", "review"), self.project(self.result(status="unknown", evidence=evidence))):
            self.assertNotIn("rawAnswer", payload)
            with mock.patch.object(router.router, "validate_answer") as validate:
                outcome = self.router_collect(payload, mode)
            validate.assert_not_called()
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(outcome.result["code"], "native-end-unread")
            self.assertNotIn("decision", outcome.result)
            self.assertEqual(outcome.result["nativeEvidence"],
                             {"unknownEvents": evidence.unknown_events.to_payload()})

    def test_unknown_fast_events_continue_to_an_adopted_router_answer(self):
        correction = FastCorrection({"type": "object"}, "Choose")
        facts = {"unknownEvents": {"countsByType": {"future": 1}, "total": 1}}
        self.assertEqual(correction.observer(facts).action, "continue")
        self.assertEqual(correction.observer(facts | {"settled": True, "rawAnswer": "{}"}).action, "continue")
        self.assertIsNone(correction.stop_reason)
        evidence = rc.NativeEvidence(unknown_events=facts["unknownEvents"])
        payload = self.project(self.result(evidence=evidence))[0]
        payload["rawAnswer"] = {"buddyId": "chosen", "note": "fits", "evidence": []}
        outcome = self.router_collect(payload)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.result["decision"]["buddyId"], "chosen")
        self.assertEqual(outcome.result["nativeEvidence"]["unknownEvents"], facts["unknownEvents"])

    def test_unknown_events_use_the_ordinary_format_correction_rule(self):
        correction = FastCorrection({"type": "object"}, "Choose")
        facts = {"unknownEvents": {"total": 1}, "settled": True, "rawAnswer": "{"}
        self.assertEqual(correction.observer(facts).action, "correct")
        self.assertEqual(correction.observer(facts).action, "continue")
        self.assertEqual(correction.correction_count, 1)
        self.assertIsNone(correction.stop_reason)

    def test_tool_facts_markers_and_denied_interactions_still_stop_immediately(self):
        for field in ("toolCalls", "toolMarkerFrames", "deniedInteractions"):
            correction = FastCorrection({"type": "object"}, "Choose")
            self.assertEqual(correction.observer({field: 1, "unknownEvents": {"total": 1}}).action, "stop")
            self.assertEqual(correction.stop_reason, "no-tool-violation")
