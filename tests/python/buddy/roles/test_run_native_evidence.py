"""Shared native facts and role verdicts using synthetic run results only."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.buddy.roles import router, run_execution, structured_call, turn_io
from hey_my_buddy.buddy.roles.run_observers import FastCorrection
from buddy.harnesses.test_run_contract import full_request, full_result


class NativeEvidenceRoleTests(unittest.TestCase):
    def request(self, harness="zcode"):
        fields = full_request(Path("/private/tmp")).to_payload()
        fields.update(harness=harness, toolScope="none", sessionServices=[], captureEvidence=False)
        return rc.decode_run_request(fields)

    def result(self, harness="zcode", *, evidence=None, status="ok"):
        return full_result().model_copy(update={
            "harness": harness, "native_evidence": evidence, "native_failure": None,
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
        declaration = {"source": "harness-permission-declaration", "binding": result.identity.to_payload(),
                       "permission": {"level": "write", "toolScope": "write", "enforcement": "convention"}}
        with mock.patch.object(run_execution, "_evidence", side_effect=lambda _r, kind:
                               declaration if kind == "worker-permission" else None):
            payload = run_execution._worker_facts(result)
        self.assertEqual(payload["nativeEvidence"], {"unconfirmed": ["tool-scope"],
                                                    "workerPermission": declaration})

    def test_unknown_worker_end_keeps_facts_and_partial_artifacts_without_delivery(self):
        result = self.result(status="unknown", evidence=rc.NativeEvidence(unconfirmed=["thread-receipt"]))
        module = SimpleNamespace(session_facts=lambda _root, _id: {"present": True},
                                 validate_turn_provenance=mock.Mock())
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with tempfile.TemporaryDirectory() as directory:
            context = ExecutionContext("task", "attempt", 1, {}, Path(directory), {}, {})
            context.turn = {"input": {"resumeMode": "initial"}}
            context.effective_workspace = {"partial": True}
            handle = SimpleNamespace(role_run_control={"nativeRoot": directory}, cancel_requested=False,
                                     log_paths={})
            collection = SimpleNamespace(payload=result, exit_code=0, stop_confirmed=True)
            partial = [{"kind": "partial-workspace", "location": directory}]
            with mock.patch.object(run_execution, "collect_controller", return_value=collection), \
                    mock.patch.object(run_execution, "_read_request", return_value=self.request()), \
                    mock.patch.object(run_execution, "_artifacts", return_value=partial), \
                    mock.patch.object(run_execution, "worker_delivery") as delivery, \
                    mock.patch.object(turn_io, "read_turn", return_value=(None, None)), \
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
        collection = SimpleNamespace(payload=payload, stop_confirmed=True)
        context = SimpleNamespace(decision_input={"buddy": {}, "budget": {},
                                                 "routingMode": mode, "buddies": [{"buddyId": "chosen"}]})
        handle = SimpleNamespace(router_started=time.monotonic(), role_run_control={"operation": mode},
                                 process=SimpleNamespace(returncode=0))
        with mock.patch.object(structured_call, "collect_controller", return_value=collection), \
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
        payload["rawAnswer"] = {"buddyId": "chosen", "reason": "fits", "evidence": []}
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
