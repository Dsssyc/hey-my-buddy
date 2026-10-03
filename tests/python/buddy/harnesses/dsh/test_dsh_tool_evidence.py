"""The DSH fast branch's unified tool evidence: facts collected, never judged.

Every outcome carries a ``toolEvidence`` package built by the shared collector
from the native stream facts the no-tool bridge reports. These tests pin the
fact projection: real call identities, unknown and missing-ID facts retained as
observed, truncation and broken streams reported, and zero fabricated call
counts — while the blackboard's judge stays the only allowance authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.protocol.tool_evidence import MAX_TOOL_EVENTS, PACKAGE_FIELDS, TOOLS_FORBIDDEN, judge_tool_evidence


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}
PROMPT = "Choose a profile"
FIXTURE = Path(__file__).parent / "fixtures" / "mock_dsh.py"


class DshToolEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-dsh-tool-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = 0
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.fake = self.root / "fake-dsh"
        shutil.copyfile(FIXTURE, self.fake)
        self.fake.chmod(0o755)
        self.dsh_home = self.root / "dsh-home"
        profile = self.dsh_home / "profiles" / "headless"
        profile.mkdir(parents=True)
        (profile / "package.json").write_text("{}")
        self.record = self.root / "harness-record.json"
        self.record.write_text(json.dumps({"dsh": {"status": "ready", "command": [str(self.fake)]}}))
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                            and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[5] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment.update(BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1", BUDDY_HARNESS_RECORD_FILE=str(self.record),
                                DSH_HOME=str(self.dsh_home))

    def execute(self, case):
        self.index += 1
        (self.root / "case").write_text(case)
        context = ExecutionContext("task", f"attempt-{self.index}", self.index,
            {"provider": "deepseek-official", "model": "deepseek-flash", "effort": "max", "cwd": str(self.cwd),
             "timeoutSeconds": 20}, self.root / f"attempt-{self.index}", {}, self.environment)
        request = NoToolStructuredRequest(str(self.cwd), PROMPT, SCHEMA, timeout_seconds=20)
        handle = DshAdapter().start_no_tool_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(28))
        outcome = DshAdapter().collect(handle, context)
        if outcome.status == "failed" and outcome.result.get("code") == "invalid-native-result" and \
                case not in ("truncated", "stream-broken", "ok-with-events", "ok-truncated"):
            self.fail(f"controller stderr: {Path(handle.log_paths['stderr']).read_text()}")
        package = outcome.result.get("toolEvidence")
        self.assertIsInstance(package, dict, outcome.to_report())
        self.assertEqual(set(package), PACKAGE_FIELDS)
        self.assertEqual(package["version"], 1)
        self.assertEqual(package["binding"], {"adapter": "dsh", "taskId": "task",
                                              "attemptId": f"attempt-{self.index}", "generation": self.index})
        self.assertNotIn(PROMPT, json.dumps(package))
        return outcome, package

    def test_zero_tool_flow_carries_a_complete_evidence_package(self):
        outcome, package = self.execute("ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        root = outcome.result["nativeIdentity"]
        self.assertEqual(package["nativeIdentity"], [root])
        self.assertEqual(package["events"], [])
        self.assertTrue(package["streamComplete"])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], package["truncated"]), (0, 0, False))
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertIsNone(judge_tool_evidence(package, "fast", False))

    def test_correction_accumulates_roots_without_resetting_counts(self):
        outcome, package = self.execute("correct")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["correctionCount"], 1)
        roots = package["nativeIdentity"]
        self.assertEqual(len(roots), 2)
        self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(package["events"], [])
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(judge_tool_evidence(package, "fast", False))

    def test_real_and_unknown_tool_facts_are_projected(self):
        outcome, package = self.execute("tool-ids")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        # The retired violation branch invented a call count no chunk carried.
        self.assertNotIn("usage", outcome.result)
        identity = package["nativeIdentity"][0]
        self.assertEqual(package["events"], [
            {"nativeIdentity": identity, "callId": "delta-call-1", "toolName": "shell",
             "category": "other", "phase": "start"},
            {"nativeIdentity": identity, "callId": "block-call-1", "toolName": "shell",
             "category": "other", "phase": "start"},
            {"nativeIdentity": identity, "callId": "block-call-1", "toolName": "tool-result",
             "category": "other", "phase": "end"}])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 1))
        self.assertTrue(package["streamComplete"])
        self.assertEqual(judge_tool_evidence(package, "fast", False), TOOLS_FORBIDDEN)
        outcome, package = self.execute("tool-unknown")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        self.assertEqual([(event["toolName"], event["category"]) for event in package["events"]],
                         [("mcp-server__lookup", "other"), ("tool-call", "other")])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 2))

    def test_missing_identity_fact_is_retained_incomplete(self):
        outcome, package = self.execute("tool-missing-id")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        event = package["events"][0]
        self.assertIsNone(event["callId"])
        self.assertEqual((event["toolName"], event["category"], event["phase"]),
                         ("tool-call", "other", "start"))
        # An unidentifiable fact never counts as a call and never completes the stream.
        self.assertEqual(package["toolCalls"], 0)
        self.assertFalse(package["streamComplete"])

    def test_truncation_is_reported_and_closes_the_stream(self):
        outcome, package = self.execute("events-truncated")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        self.assertTrue(outcome.result["nativeToolEventsTruncated"])
        self.assertEqual(len(package["events"]), MAX_TOOL_EVENTS)
        self.assertEqual(package["toolCalls"], MAX_TOOL_EVENTS)
        self.assertFalse(package["streamComplete"])

    def test_stream_break_keeps_partial_facts_incomplete(self):
        outcome, package = self.execute("stream-broken")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "invalid-native-result")
        self.assertEqual(package["events"], [
            {"nativeIdentity": package["nativeIdentity"][0], "callId": "lost-call-1",
             "toolName": "shell", "category": "other", "phase": "start"}])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 1))
        self.assertFalse(package["streamComplete"])

    def test_ok_receipts_carrying_tool_facts_are_rejected(self):
        for case in ("ok-with-events", "ok-truncated"):
            with self.subTest(case=case):
                outcome, package = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], "invalid-native-result")
                self.assertNotIn("zeroToolVerified", outcome.result)
                if case == "ok-with-events":
                    self.assertEqual(package["toolCalls"], 1)
                else:
                    self.assertFalse(package["streamComplete"])

    def test_native_finish_failure_keeps_bounded_failure_facts(self):
        outcome, package = self.execute("native-turn-failed")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "native-turn-failed")
        self.assertEqual(outcome.result["nativeFailure"],
                         {"finishKind": "error", "code": "provider_stream_failed", "status": 503})
        self.assertNotIn("zeroToolVerified", outcome.result)
        self.assertEqual(package["events"], [])
        # The native stream closed itself with its failure finish; the missing
        # answer is the controller's business, not an evidence gap.
        self.assertTrue(package["streamComplete"])


if __name__ == "__main__":
    unittest.main()
