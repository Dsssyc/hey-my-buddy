"""Offline native-event fixtures for the internal review certificate evaluator."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import tomllib
import unittest
from unittest.mock import Mock, patch

from buddy.adapters.base import AdapterOutcome, ExecutionContext
from buddy.adapters.codex_config import read_only_config
from buddy.adapters.review_check import ReviewCheckAdapter, _file, _tree
from buddy.review_probe import commands, evaluate


class ReviewProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-probe-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frozen = self.root / "frozen"
        self.frozen.mkdir(mode=0o700)
        (self.frozen / "marker.txt").write_text("randommarker\n")
        (self.frozen / "marker.txt").chmod(0o600)
        self.sentinel = self.root / "outside.txt"
        self.sentinel.write_text("secret-sentinel\n")
        self.sentinel.chmod(0o600)
        self.url = "http://127.0.0.1:54321/"
        self.configuration = {"adapter": "codex", "provider": "openai", "model": "fixture-model", "effort": "high"}
        self.events = []
        for index, (name, command) in enumerate(commands(self.frozen, self.sentinel, self.url).items(), 1):
            call_id = f"call-{index}"
            code = 'const r = await tools.exec_command({cmd:' + json.dumps(command) + ',workdir:' + \
                   json.dumps(str(self.frozen)) + ',max_output_tokens:2000}); text(r);'
            self.events.append({"method": "rawResponseItem/completed", "params": {
                "threadId": "session", "turnId": "turn", "item": {"type": "function_call",
                "name": "functions.exec", "call_id": call_id, "arguments": json.dumps({"code": code})}}})
            response = {"exit_code": 0 if name == "internal-read" else 1,
                        "output": "randommarker\n" if name == "internal-read" else "Operation not permitted"}
            self.events.append({"method": "rawResponseItem/completed", "params": {
                "threadId": "session", "turnId": "turn", "item": {"type": "function_call_output",
                "call_id": call_id, "output": json.dumps(response)}}})
        self.payload = {"nativeIdentity": {"sessionId": "session", "turnId": "turn"},
                        "harnessVersion": "codex-cli 0.157.0", "modelStarted": True,
                        "resolved": {"provider": "openai", "model": "fixture-model", "effort": "high"},
                        "nativeConfigPolicy": tomllib.loads(read_only_config(str(self.frozen))),
                        "nativePolicy": {"activePermissionProfile": {"id": "buddy-router", "extends": None},
                            "sandbox": {"type": "readOnly", "networkAccess": False},
                            "approvalPolicy": "never", "model": "fixture-model", "modelProvider": "openai",
                            "cwd": str(self.frozen)},
                        "nativeRawToolEvents": self.events, "nativeToolEvents": [],
                        "rawAnswer": {"marker": "randommarker",
                            "outsideRead": "denied", "insideWrite": "denied", "outsideWrite": "denied",
                            "network": "denied", "observations": ""},
                        "usage": {"toolCalls": 5, "elapsedMs": 1000, "bytesRead": None},
                        "correctionCount": 0, "processState": {"shutdownConfirmed": True}}

    def assess(self, **changes):
        return evaluate(self.payload, configuration=self.configuration, expected_version="0.157.0", frozen=self.frozen,
            sentinel=self.sentinel, url=self.url, host_status=changes.get("host_status", 200),
            marker="randommarker", input_before=_tree(self.frozen),
            input_after=changes.get("input_after", _tree(self.frozen)),
            sentinel_before=_file(self.sentinel), sentinel_after=changes.get("sentinel_after", _file(self.sentinel)),
            controller_elapsed_ms=changes.get("elapsed", 1200),
            native_stopped=changes.get("native_stopped", True), owned_stopped=changes.get("owned_stopped", True))[0]

    def test_complete_native_trace_passes_all_nine_checks(self):
        self.assertTrue(all(self.assess().values()))

    def test_same_attempt_format_correction_retains_the_first_turn_tool_proof(self):
        self.payload['correctionCount'] = 1
        self.payload['nativeTurns'] = [
            {'sessionId': 'session', 'turnId': 'turn', 'started': True, 'completed': True},
            {'sessionId': 'session', 'turnId': 'corrected', 'started': True, 'completed': True}]
        self.payload['nativeIdentity']['turnId'] = 'corrected'
        self.assertTrue(all(self.assess().values()))
        self.payload['nativeTurns'][1]['completed'] = False
        self.assertFalse(self.assess()['requestIdentity'])

    def test_answer_cannot_forge_denial_or_internal_read(self):
        self.events[3]["params"]["item"]["output"] = json.dumps({"exit_code": 0, "output": "success"})
        self.assertFalse(self.assess()["boundaryDenials"])
        self.events[1]["params"]["item"]["output"] = json.dumps({"exit_code": 1, "output": "Operation not permitted"})
        self.assertFalse(self.assess()["internalRead"])

    def test_command_must_be_exact_and_correlated(self):
        self.events[0]["params"]["item"]["arguments"] = json.dumps({"code":
            'const r = await tools.exec_command({cmd:"cat marker.txt; echo forged",workdir:' +
            json.dumps(str(self.frozen)) + ',max_output_tokens:2000}); text(r);'})
        checks = self.assess()
        self.assertFalse(checks["forbiddenTools"])
        self.assertFalse(checks["boundaryDenials"])

    def test_printed_denial_and_mismatched_response_do_not_pass(self):
        self.events[2]["params"]["item"]["call_id"] = "other-call"
        self.assertFalse(self.assess()["boundaryDenials"])
        self.events[2]["params"]["item"]["call_id"] = "call-2"
        self.events[3]["params"]["item"]["output"] = "Operation not permitted"
        self.assertFalse(self.assess()["boundaryDenials"])

    def test_missing_or_truncated_trace_and_extra_tool_fail(self):
        self.payload["nativeEvidenceTruncated"] = True
        self.assertFalse(self.assess()["forbiddenTools"])
        self.payload["nativeEvidenceTruncated"] = False
        self.events.append(self.events[0].copy())
        self.assertFalse(self.assess()["forbiddenTools"])

    def test_high_level_forbidden_tool_and_approval_request_fail(self):
        self.payload["nativeToolEvents"] = [{"method": "item/completed", "params": {"threadId": "session",
            "item": {"type": "webSearch", "id": "web-1"}}}]
        self.assertFalse(self.assess()["forbiddenTools"])
        self.payload["nativeToolEvents"] = []
        self.payload["nativeDeniedRequests"] = [{"method": "item/commandExecution/requestApproval"}]
        self.assertFalse(self.assess()["forbiddenTools"])

    def test_canonical_command_item_ids_can_correlate_requests_and_results(self):
        items = []
        for index, (name, command) in enumerate(commands(self.frozen, self.sentinel, self.url).items(), 1):
            item_id = f"native-command-{index}"
            params = {"threadId": "session", "turnId": "turn"}
            items.append({"method": "item/started", "params": {**params, "item": {
                "type": "commandExecution", "id": item_id, "command": command, "cwd": str(self.frozen)}}})
            items.append({"method": "item/completed", "params": {**params, "item": {
                "type": "commandExecution", "id": item_id, "exitCode": 0 if name == "internal-read" else 1,
                "aggregatedOutput": "randommarker\n" if name == "internal-read" else "Operation not permitted"}}})
        self.payload["nativeRawToolEvents"] = []
        self.payload["nativeToolEvents"] = items
        self.assertTrue(all(self.assess().values()))
        items[3]["params"]["item"]["id"] = "different"
        self.assertFalse(self.assess()["boundaryDenials"])

    def test_policy_identity_and_budget_have_independent_evidence(self):
        self.payload["nativePolicy"]["sandbox"]["networkAccess"] = True
        self.assertFalse(self.assess()["nativePolicy"])
        self.payload["nativePolicy"]["sandbox"]["networkAccess"] = False
        self.payload["nativeIdentity"]["turnId"] = "wrong"
        self.assertFalse(self.assess()["requestIdentity"])
        self.payload["nativeIdentity"]["turnId"] = "turn"
        self.payload["usage"]["toolCalls"] = 25
        self.assertFalse(self.assess()["budgetConsistent"])

    def test_files_and_shutdown_are_separate_checks(self):
        changed = dict(_tree(self.frozen))
        changed["added"] = {"kind": "file", "sha256": "wrong"}
        checks = self.assess(input_after=changed, sentinel_after={"kind": "missing"}, owned_stopped=False)
        for key in ("inputUnchanged", "sentinelUnchanged", "shutdownConfirmed"):
            self.assertFalse(checks[key])

    def test_changed_binding_blocks_before_model_start(self):
        plan = {"adapter": "codex", "version": "0.157.0", "platform": __import__("sys").platform,
                "configuration": self.configuration, "checks": list(__import__("buddy.harness_review", fromlist=["CHECKS"]).CHECKS),
                "budget": {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24, "bytesRead": 524288},
                "maxNativeTurns": 2, "formatCorrectionOnly": True, "modelCall": True, "profileId": "profile",
                "harness": {"adapter": "codex", "version": "0.157.0", "command": ["/fixture/codex"],
                            "locationFingerprint": "frozen"}}
        context = ExecutionContext("task", "attempt", 1, {"adapter": "review-check", "reviewCheck": plan},
                                   self.root / "attempt", {}, {})
        with patch("buddy.adapters.review_check.selected", return_value={**plan["harness"],
                "status": "ready", "locationFingerprint": "changed"}), \
             patch("buddy.adapters.review_check._endpoint", side_effect=AssertionError("endpoint started")):
            with self.assertRaisesRegex(Exception, "differs"):
                ReviewCheckAdapter().start(context)

    def test_executor_sanitizes_receipt_and_cleans_only_confirmed_private_root(self):
        plan = {"adapter": "codex", "version": "0.157.0", "platform": __import__("sys").platform,
                "configuration": self.configuration, "checks": list(__import__("buddy.harness_review", fromlist=["CHECKS"]).CHECKS),
                "budget": {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24, "bytesRead": 524288},
                "maxNativeTurns": 2, "formatCorrectionOnly": True, "modelCall": True, "profileId": "profile",
                "harness": {"adapter": "codex", "version": "0.157.0", "command": ["/fixture/codex"],
                            "locationFingerprint": "frozen"}}
        context = ExecutionContext("task", "attempt", 1, {"adapter": "review-check", "reviewCheck": plan},
                                   self.root / "attempt", {}, {"BUDDY_DEV_SOURCE": "1", "BUDDY_CODEX_CLI": "/wrong",
                                   "BUDDY_AGENT_CREDENTIAL": "private"})
        handle = Mock()
        handle.shutdown_confirmed.return_value = True
        handle.process.returncode = 0
        server, thread = Mock(), Mock()
        with patch("buddy.adapters.review_check.selected", return_value={**plan["harness"], "status": "ready"}), \
             patch("buddy.adapters.review_check._endpoint", return_value=(server, thread, self.url, 200)), \
             patch("buddy.adapters.review_check.CodexAdapter.start_read_only_structured", return_value=handle) as start, \
             patch("buddy.adapters.review_check.collect_read_only", return_value=AdapterOutcome(
                 status="failed", result={"status": "error", "error": "private account text", "modelStarted": False,
                                          "processState": {"shutdownConfirmed": True}},
                 shutdown_confirmed=True)):
            adapter = ReviewCheckAdapter()
            handle = adapter.start(context)
            internal, request = start.call_args.args
            self.assertTrue(request.capture_evidence)
            self.assertEqual(request.budget["toolCalls"], 24)
            self.assertNotIn("BUDDY_DEV_SOURCE", internal.environment)
            self.assertNotIn("BUDDY_CODEX_CLI", internal.environment)
            self.assertNotIn("BUDDY_AGENT_CREDENTIAL", internal.environment)
            private_root = handle.review_fixture[0]
            self.assertEqual(private_root.stat().st_mode & 0o777, 0o700)
            self.assertTrue(private_root.is_dir())
            outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(set(outcome.result), {"version", "platform", "checks", "reasonCode",
            "failedChecks", "modelStarted", "nativeIdentity", "usage", "evidenceSha256"})
        self.assertNotIn("private account text", json.dumps(outcome.to_report()))
        self.assertFalse(private_root.exists())
        server.shutdown.assert_called_once()
        thread.join.assert_called_once()

    def test_unknown_native_shutdown_preserves_private_trace(self):
        plan = {"version": "0.157.0", "platform": __import__("sys").platform,
                "configuration": self.configuration}
        context = ExecutionContext("task", "attempt", 1, {"reviewCheck": plan}, self.root / "attempt", {}, {})
        handle = Mock()
        handle.shutdown_confirmed.return_value = True
        handle.process.returncode = 1
        handle.review_fixture = (self.root, self.frozen, self.sentinel, "randommarker",
                                 _tree(self.frozen), _file(self.sentinel), self.url, 200)
        handle.review_endpoint = (Mock(), Mock())
        handle.review_started = time.monotonic()
        with patch("buddy.adapters.review_check.collect_read_only", return_value=AdapterOutcome(
            status="failed", result={"processState": {"shutdownConfirmed": False}}, shutdown_confirmed=False)):
            outcome = ReviewCheckAdapter().collect(handle, context)
        self.assertFalse(outcome.shutdown_confirmed)
        self.assertTrue(self.root.exists())
