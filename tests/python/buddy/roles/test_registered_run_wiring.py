"""Registered role runs over real Python mock app-servers, without model calls."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import Adapter, ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, adapter, run_seam
from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request, decode_run_result, encode_run_result
from hey_my_buddy.buddy.roles.controller import FastPreparation, start_router_preparation, worker_executor
from hey_my_buddy.buddy.roles import run_controller, run_execution, structured_call, turn_io
from hey_my_buddy.errors import BoardError
from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from buddy.harnesses.zcode.test_zcode_tool_evidence import FakeAppServerTests, SCHEMA
from buddy.harnesses.test_run_contract import full_request


class RegisteredDescriptionTests(unittest.TestCase):
    def test_the_description_has_no_legacy_execution_entries(self):
        description = adapter("zcode")
        self.assertNotIsInstance(description, Adapter)
        for entry in ("prepare", "start", "collect", "cancel", "start_no_tool_structured", "start_read_only_structured"):
            self.assertFalse(hasattr(description, entry), entry)
        self.assertIsNone(importlib.util.find_spec("hey_my_buddy.buddy.harnesses.zcode.runner"))
        module = run_seam("zcode")
        self.assertIs(worker_executor("zcode").module, module)
        for operation in ("run", "run_discovery", "check_preparation", "prepare_services",
                          "validate_turn_provenance", "session_facts", "native_evidence"):
            self.assertTrue(callable(getattr(module, operation)))


class NativeEvidenceProjectionTests(unittest.TestCase):
    def project(self, *, policy=None, completion=None, native_identity=None, event_count=None, capture=True):
        fields = full_request(Path("/private/tmp")).to_payload()
        fields.update(toolScope="none", sessionServices=[], captureEvidence=capture)
        request = decode_run_request(fields)
        result = rc.RunResult(
            identity=request.identity, harness="zcode", end=rc.RunEnd(status="ok"),
            native_identity=native_identity, native_event_count=event_count,
            value=rc.RunValue( schema_status="unknown", raw='{"answer":"value"}'),
            completion_evidence=completion,
            effective_policy=rc.EffectivePolicy(tools=policy))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("native projection spawned")):
            return run_execution._fast_result(result, request, {"stopReason": None, "elapsedMs": 1})

    def test_native_evidence_preserves_settings_eof_and_the_consumed_native_identity(self):
        projected = self.project(
            policy=rc.PolicyFact(
                                 requested={"toolAllowlist": ["Read"], "titleGenerationEnabled": True}),
            completion=rc.CompletionEvidence( stream_end=False),
            native_identity=rc.NativeIdentity(session_id="actual-session",
                                              turn_id="actual-turn", ),
            event_count=3)
        self.assertEqual(projected["nativeEvidence"], {"eventCount": 3, "toolAllowlist": ["Read"],
                                                     "titleGenerationEnabled": True, "streamEof": False})
        self.assertEqual(projected["nativeIdentity"], {"sessionId": "actual-session", "turnId": "actual-turn"})

    def test_missing_native_settings_or_completion_remain_unknown(self):
        for policy, completion, title in (
            (None, None, None),
            (rc.PolicyFact(),
             rc.CompletionEvidence(), None),
            (rc.PolicyFact( requested={"titleGenerationEnabled": True}), None, True),
        ):
            with self.subTest(policy=policy):
                projected = self.project(policy=policy, completion=completion)
                self.assertEqual(projected["nativeEvidence"], {"eventCount": None, "toolAllowlist": None,
                                                             "titleGenerationEnabled": title, "streamEof": None})
                self.assertNotIn("nativeIdentity", projected)

    def test_capture_evidence_selects_whether_the_native_projection_is_published(self):
        with mock.patch.object(run_seam("zcode"), "native_evidence", side_effect=AssertionError("not requested")):
            projected = self.project(capture=False)
        self.assertNotIn("nativeEvidence", projected)

    def test_codex_policy_survives_without_capture_and_keeps_the_old_receipt_level(self):
        fields = full_request(Path("/private/tmp")).to_payload()
        fields.update(harness="codex", toolScope="none", sessionServices=[], captureEvidence=False)
        request = decode_run_request(fields)
        policy = {"configuration": "private-no-tool", "tools": []}
        module = SimpleNamespace(native_evidence=lambda _result: {"nativePolicy": policy})
        with mock.patch.dict(RUN_SEAMS, {"codex": module}):
            for status in ("ok", "error"):
                result = rc.RunResult(identity=request.identity, harness="codex", end=rc.RunEnd(status=status))
                payload = run_execution._fast_result(result, request, {"stopReason": None, "elapsedMs": 1})
                self.assertEqual(payload["nativePolicy"], policy)
                self.assertNotIn("nativeEvidence", payload)
                if status == "error":
                    self.assertIs(payload["zeroToolVerified"], False)
            fields["toolScope"] = "read"
            review = run_execution._review_result(result, decode_run_request(fields),
                                                   {"stopReason": None, "elapsedMs": 1})
            self.assertEqual(review["nativePolicy"], policy)

    def test_codex_worker_receipt_keeps_identity_and_the_bounded_validation_reason(self):
        request = full_request(Path("/private/tmp"))
        result = rc.RunResult(
            identity=request.identity, harness="codex", end=rc.RunEnd(status="ok"),
            native_identity=rc.NativeIdentity(session_id="thread-1", turn_id="turn-1"),
            value=rc.RunValue( schema_status="unknown", parsed={"outcome": {
                "disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
                "artifacts": [], "request": {}}}))
        payload = run_execution._worker_facts(result)
        self.assertEqual(payload["nativeIdentity"], {"sessionId": "thread-1", "turnId": "turn-1"})
        self.assertEqual(payload["outcomeValidationError"], "a completed outcome requires request: null")


class FastRegisteredRunTests(FakeAppServerTests):
    def test_preparation_retains_the_run_and_refuses_a_changed_registration(self):
        context = ExecutionContext("task", "attempt", 1,
            {"provider": "fixture-api", "model": "fixture-model", "effort": "low"},
            self.root / "attempt", {}, self.environment)
        request = NoToolStructuredRequest(str(self.cwd), "Choose", SCHEMA, 3)
        preparation = FastPreparation("zcode",  request, context, self.cwd)
        self.assertIs(preparation.run_module, run_seam("zcode"))
        with mock.patch.dict(RUN_SEAMS, {"zcode": object()}), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("spawn")) as spawn:
            with self.assertRaises(BoardError) as caught:
                start_router_preparation(preparation)
            self.assertEqual(caught.exception.code, "ROLE_RUN_UNREGISTERED")
            spawn.assert_not_called()

    def invoke(self, case="ok", *, capture_evidence=False):
        context = ExecutionContext("task", "attempt", 1,
            {"provider": "fixture-api", "model": "fixture-model", "effort": "low",
             "cwd": str(self.cwd), "timeoutSeconds": 3}, self.root / "attempt", {},
            {**self.environment, "BUDDY_ZCODE_TEST_CASE": case})
        request = NoToolStructuredRequest(str(self.cwd), "Choose a profile", SCHEMA, 3,
                                          capture_evidence=capture_evidence)
        handle = start_router_preparation(FastPreparation("zcode",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(11))
        return context, handle, structured_call.collect(handle)

    def test_fast_consumes_public_frames_and_corrects_on_the_registered_process(self):
        context, handle, outcome = self.invoke("correct", capture_evidence=True)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        request = decode_run_request(Path(handle.role_run_control["requestFile"]).read_bytes())
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        self.assertEqual(result.identity, request.identity)
        self.assertEqual(result.identity, handle.role_run_identity)
        self.assertEqual(request.tool_scope, "none")
        self.assertEqual(request.session_services, ())
        self.assertIsNone(request.identity.turn_id)
        # The internal request frame carries no account reference at all; the
        # frozen account selection stays an outer environment choice.
        self.assertNotIn("frozenAccount", request.to_payload())
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(outcome.result["rawAnswer"], result.value.raw)
        self.assertEqual(outcome.result["nativeEvidence"], {"eventCount": result.native_event_count,
                                                         "toolAllowlist": [], "titleGenerationEnabled": False,
                                                         "streamEof": True})
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(handle.process.args[2], "hey_my_buddy.buddy.roles.run_controller")
        self.assertFalse((context.directory / "no-tool-control.json").exists())

    def test_a_foreign_result_cannot_borrow_the_owned_run_identity(self):
        _, handle, outcome = self.invoke()
        self.assertEqual(outcome.status, "ok")
        path = Path(handle.log_paths["stdout"])
        fields = decode_run_result(path.read_bytes()).to_payload()
        fields["identity"]["invocationId"] = "foreign-invocation"
        path.write_text(encode_run_result(decode_run_result(fields)))
        refused = structured_call.collect(handle)
        self.assertEqual(refused.status, "failed")
        self.assertEqual(refused.result["code"], "invalid-native-result")
        self.assertFalse(refused.shutdown_confirmed)

    def test_both_native_and_controller_groups_are_required_for_fast_stop(self):
        _, handle, outcome = self.invoke()
        self.assertTrue(outcome.shutdown_confirmed)
        path = Path(handle.log_paths["stdout"])
        raw = path.read_bytes()
        with mock.patch.object(handle, "shutdown_confirmed", return_value=False):
            refused = structured_call.collect(handle)
            self.assertFalse(refused.shutdown_confirmed)
            self.assertEqual(refused.status, "failed")
        fields = decode_run_result(raw).to_payload()
        fields["stopEvidence"]["native"]["groupState"] = "unknown"
        path.write_text(encode_run_result(decode_run_result(fields)))
        refused = structured_call.collect(handle)
        self.assertFalse(refused.shutdown_confirmed)
        self.assertEqual(refused.status, "failed")

    def test_discovery_has_no_prompt_run_or_service_binding(self):
        root = self.root / "discovery"
        root.mkdir(mode=0o700)
        module = run_seam("zcode")
        with mock.patch.dict(os.environ, {**self.environment, "BUDDY_ZCODE_TEST_CASE": "discovery"}, clear=True), \
                mock.patch.object(module, "run", side_effect=AssertionError("model run")), \
                mock.patch.object(module, "prepare_services", side_effect=AssertionError("session service")):
            raw, code = run_controller.execute({"operation": "discover", "harness": "zcode",
                "cwd": str(root), "privateRoot": str(root), "nativeRoot": str(root / "native"),
                "timeoutSeconds": 3}, threading.Event())
        result = json.loads(raw)
        self.assertEqual(code, 0)
        self.assertFalse(result["modelStarted"])
        self.assertTrue(result["processState"]["shutdownConfirmed"])
        self.assertEqual(result["catalog"]["providers"][0]["models"][0]["id"], "fixture-model")
        self.assertFalse((root / "role-run-request.json").exists())
        self.assertFalse((root / "finish-bridge.json").exists())


class WorkerRegisteredRunTests(ZcodeFixtureCase):
    def assert_failed_attention_facts(self, outcome, original, *, shutdown):
        self.assertEqual(outcome.status, "failed")
        self.assertIs(outcome.shutdown_confirmed, shutdown)
        self.assertIs(outcome.result["processState"]["shutdownConfirmed"], True)
        self.assertIn("nativeAttention", outcome.result)
        self.assertIn("inquiry", outcome.result)
        self.assertEqual(outcome.result["nativeAttention"]["requests"], 3)
        self.assertEqual(outcome.result["nativeAttention"], original.result["nativeAttention"])
        self.assertEqual(outcome.result["inquiry"], original.result["inquiry"])
        self.assertIs(outcome.result["attentionRequired"], True)
        self.assertNotIn("turn", outcome.result)
        self.assertNotIn("workspaceSeal", outcome.result)

    def test_worker_consumes_frames_without_publishing_private_service_credentials(self):
        context = self.context()
        context.agent_credential = "synthetic-attempt-secret"
        context.runtime["account"] = {"adapter": "zcode", "source": "native", "revision": 3, "credentialRevision": 2}
        handle, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        request_bytes = Path(handle.role_run_control["requestFile"]).read_bytes()
        request = decode_run_request(request_bytes)
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        self.assertEqual(request.identity, result.identity)
        self.assertEqual(request.tool_scope, "write")
        # The runtime holds the account; the request frame carries none of it.
        self.assertNotIn("frozenAccount", request.to_payload())
        self.assertEqual(request.identity.input_sha256, turn_io.input_hash(context.turn_input))
        self.assertEqual(outcome.result["turn"]["outcome"], result.value.parsed.value)
        self.assertEqual(outcome.result["turn"]["provenance"]["nativeTurnId"], result.native_identity.turn_id)
        self.assertEqual(handle.process.args[2], "hey_my_buddy.buddy.roles.run_controller")
        self.assertNotIn(b"synthetic-attempt-secret", request_bytes)
        self.assertNotIn("synthetic-attempt-secret", json.dumps(outcome.to_report()))
        service_names = request.session_services[0].tool_names
        self.assertEqual(len(service_names), 3)
        for name in service_names:
            self.assertIn(name, request.input_text)

    def test_a_changed_provenance_reference_fails_without_losing_real_stop(self):
        context = self.context("usage-ok")
        fixture = Path(__file__).parents[1] / "harnesses/zcode/fixtures/mock_zcode_usage.py"
        fixture.chmod(0o755)
        context.environment["BUDDY_ZCODE_CLI"] = str(fixture)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        self.assertIsNotNone(result.usage)
        self.assertIsNotNone(result.last_assistant_message)
        ref = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
        value = json.loads(Path(ref.location).read_text())
        value["nativeTurnId"] = "foreign-turn"
        Path(ref.location).write_text(json.dumps(value))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["code"], "invalid-role-result")
        self.assertEqual(outcome.result["tokenUsage"], result.usage.value)
        self.assertEqual(outcome.result["lastAssistantMessage"], {
            **result.last_assistant_message.value, "source": "zcode/session-root-assistant-message"})
        self.assertNotIn("turn", outcome.result)

    def test_an_unconfirmed_controller_keeps_valid_inquiry_and_attention(self):
        context = self.context("attention")
        handle, original = self.execute(context)
        self.assertEqual(original.status, "ok", original.to_report())
        self.assertEqual(original.result["nativeAttention"]["requests"], 3)
        with mock.patch.object(handle, "shutdown_confirmed", return_value=False), \
                mock.patch.object(turn_io, "seal_workspace", side_effect=AssertionError("unconfirmed seal")):
            refused = self.adapter.collect(handle, context)
        self.assert_failed_attention_facts(refused, original, shutdown=False)

    def test_a_changed_provenance_keeps_valid_inquiry_and_attention(self):
        context = self.context("attention")
        handle, original = self.execute(context)
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        reference = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
        path = Path(reference.location)
        path.write_bytes(path.read_bytes() + b" ")
        with mock.patch.object(turn_io, "seal_workspace", side_effect=AssertionError("unverified seal")):
            refused = self.adapter.collect(handle, context)
        self.assertEqual(refused.result["code"], "invalid-role-result")
        self.assert_failed_attention_facts(refused, original, shutdown=True)

    def test_each_bad_report_keeps_the_other_verified_report(self):
        for index, (kind, failed_key, kept_key) in enumerate((
            ("inquiry-report", "inquiry", "nativeAttention"),
            ("attention-report", "nativeAttention", "inquiry"),
        ), 1):
            with self.subTest(kind=kind):
                context = self.context("attention", index=index)
                handle, original = self.execute(context)
                result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
                reference = next(ref for ref in result.evidence_refs if ref.kind == kind)
                path = Path(reference.location)
                path.write_bytes(path.read_bytes() + b" ")
                with mock.patch.object(turn_io, "seal_workspace", side_effect=AssertionError("unverified seal")):
                    refused = self.adapter.collect(handle, context)
                self.assertEqual(refused.status, "failed")
                self.assertIs(refused.shutdown_confirmed, True)
                self.assertEqual(refused.result["code"], "invalid-role-result")
                self.assertIn(kept_key, refused.result)
                self.assertEqual(refused.result[kept_key], original.result[kept_key])
                self.assertNotIn(failed_key, refused.result)
                self.assertIs(refused.result["attentionRequired"], True if kind == "inquiry-report" else None)
                self.assertNotIn("turn", refused.result)
                self.assertNotIn("workspaceSeal", refused.result)



class DshPublishedReceiptTests(unittest.TestCase):
    def test_an_unvalidated_dsh_turn_does_not_publish_a_captured_session(self):
        from buddy.harnesses.dsh.test_dsh_role_wiring import DshRoleCase
        fixture = DshRoleCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context()
        # The real driver completes its native root, but the role rejects the
        # turn binding during import. Raw root identity is not a captured turn.
        with mock.patch("hey_my_buddy.buddy.roles.turn_io.read_turn", return_value=(None, "untrusted turn")):
            _handle, outcome = fixture.execute(context)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertIsNotNone(outcome.result.get("sessionId"))
        self.assertIsNone(outcome.result["nativeSession"]["sessionId"])
        self.assertFalse(outcome.result["nativeSession"]["captured"])
        self.assertEqual(outcome.result["nativeSession"]["sessionIdSource"], "none")
        self.assertTrue(outcome.result["nativeActivity"]["sidecarWritten"])

if __name__ == "__main__":
    unittest.main()
