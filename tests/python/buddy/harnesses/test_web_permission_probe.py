"""Offline verdict checks for the deliberately paid native permission probes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.protocol.tool_evidence import ToolEventEvidence

PROBE_PATH = Path(__file__).resolve().parents[4] / "tests/probes/web_permission_native.py"
SPEC = importlib.util.spec_from_file_location("web_permission_native_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class WebPermissionProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="web-verdict-", dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"

    def result(self, harness, answer, calls=(), *, complete=True):
        marker = uuid.uuid4().hex
        identity = rc.RunIdentity(task_id="probe", attempt_id=marker, generation=1, invocation_id=marker)
        evidence = ToolEventEvidence({"harness": harness, "taskId": "probe", "attemptId": marker,
                                      "generation": 1}, state_dir=self.state)
        native = {"sessionId": "session"}
        for index, (name, category) in enumerate(calls):
            for phase in ("start", "end"):
                evidence.observe({"nativeIdentity": native, "callId": f"call-{index}",
                                  "toolName": name, "category": category, "phase": phase})
        return rc.RunResult(identity=identity, harness=harness, end=rc.RunEnd(status="ok"),
            value=rc.RunValue(schema_status="unknown", raw=json.dumps(answer)),
            tool_evidence=evidence.finish([native], complete),
            effective_policy=rc.EffectivePolicy(tools=rc.PolicyFact(requested={"web": True})))

    def evaluate(self, harness, answer, calls=(), *, tools=None, complete=True):
        init = [] if tools is None else [{"tools": tools, "mcp_servers": []}]
        return probe.evaluate(harness, self.result(harness, answer, calls, complete=complete),
                              self.state, init, None, "secret-canary")["checks"]

    def answer(self):
        return {"read": "No native local-file read tool is visible.",
                "command": "MISSING: no native command execution tool is visible.",
                "mcp": "No visible user MCP tools or servers.", "visibleMcpServers": "none",
                "visibleTools": "WebSearch, WebFetch",
                "search": "WebSearch returned https://example.com/.",
                "fetch": "This domain is for use in documentation examples without needing permission."}

    def test_complete_native_inventory_does_not_require_denial_wording_or_a_model_inventory(self):
        answer = self.answer()
        answer.update(read="Step one checked.", command="Step two checked.", mcp="Step three checked.",
                      visibleTools="Read, Bash, mcp__imagined__tool", visibleMcpServers="imagined")
        checks = self.evaluate("claude", answer, [("WebSearch", "fetch"), ("WebFetch", "fetch")],
                               tools=["StructuredOutput", "WebSearch", "WebFetch"])
        self.assertEqual(set(checks.values()), {"PASS"})

    def test_complete_inventory_does_not_hide_observed_forbidden_calls_or_canary_content(self):
        tools = ["StructuredOutput", "WebSearch", "WebFetch"]
        answer = self.answer()
        checks = self.evaluate("claude", answer, [("Bash", "execute"), ("mcp__data__read", "other")],
                               tools=tools)
        self.assertEqual(checks["noCommand"], "FAIL")
        self.assertEqual(checks["noMcp"], "FAIL")
        answer["read"] = "secret-canary"
        self.assertEqual(self.evaluate("claude", answer, tools=tools)["noLocalRead"], "FAIL")

    def test_missing_tool_wording_and_current_example_page_body_pass_without_native_inventory(self):
        answer = self.answer()
        answer.update(visibleTools="working_directory, web_search, web_fetch",
                      read="MISSING TOOL: no native local-file read tool is visible.",
                      command="No native command-execution/shell tool is visible, so pwd could not be run.")
        checks = self.evaluate("dsh", answer, [("web_search", "other"), ("web_fetch", "other")])
        self.assertEqual(set(checks.values()), {"PASS"})

    def test_fetch_failure_stays_failed_while_missing_tools_and_search_pass(self):
        answer = self.answer()
        answer["fetch"] = ('FAILED: URL hostname "example.com" resolves to a non-public IP address. '
                           'No page body was returned.')
        checks = self.evaluate("dsh", answer, [("web_search", "other"), ("web_fetch", "other")])
        self.assertEqual(checks, {"noLocalRead": "PASS", "noCommand": "PASS", "noMcp": "PASS",
                                  "webSearch": "PASS", "webFetch": "FAIL"})

    def test_web_result_claims_require_calls_and_complete_evidence(self):
        answer = self.answer()
        checks = self.evaluate("dsh", answer)
        self.assertEqual(checks["webSearch"], "FAIL")
        self.assertEqual(checks["webFetch"], "FAIL")
        incomplete = self.evaluate("dsh", answer, [("web_search", "other"), ("web_fetch", "other")],
                                   complete=False)
        self.assertEqual(set(incomplete.values()), {"FAIL"})


if __name__ == "__main__":
    unittest.main()
