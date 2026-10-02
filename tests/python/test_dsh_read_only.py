"""Offline DSH read-only controller tests with the shared review protocol fixture.

The review entry drives the packaged Node bridge through the private headless
profile: a free dump-config preflight before any model runs, one restricted
native Agent turn per answer round with a real call id, at most one format
correction sharing the absolute deadline and the accumulated tool budget, and
facts-only tool evidence. Every scenario here is simulated; no model runs and
native verification stays a separately authorized boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from buddy.adapters import dsh_read_only, read_only
from buddy.adapters.base import ExecutionContext, NoToolStructuredRequest, ReadOnlyStructuredRequest
from buddy.adapters.dsh import DshAdapter
from buddy.adapters.dsh_read_only import read_only_composed
from buddy.tool_evidence import TOOL_EVIDENCE_UNVERIFIED, judge_tool_evidence


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}
PROMPT = "Choose a profile"
SPEC = {"provider": "deepseek-official", "model": "deepseek-flash", "effort": "max"}
FIXTURE = Path(__file__).parent / "fixtures" / "mock_dsh_read_only.py"
NO_TOOL_FIXTURE = Path(__file__).parent / "fixtures" / "mock_dsh.py"

SAFE_DUMP = ("- id: headless-runner\n  disabled: true\n"
             "- id: session-title-llm\n  disabled: true\n"
             "- id: session-telemetry-otel\n  disabled: true\n"
             "- id: workflow\n  disabled: true\n"
             "- id: agent\n- id: agent-default-model\n- id: agent-loop\n- id: tools\n- id: session\n"
             "- id: tool-fs\n- id: tool-fs-search\n"
             "- id: buddy-read-only-structured\n  name: >-\n    file:///private/bridge.mjs\n")


class DshReadOnlyUnitTests(unittest.TestCase):
    def test_composed_profile_dump_is_checked_before_the_model(self):
        plugin = Path("/private/bridge.mjs")
        self.assertTrue(read_only_composed(SAFE_DUMP, plugin))
        folded = SAFE_DUMP.replace("file:///private/bridge.mjs", str(plugin))
        self.assertTrue(read_only_composed(folded, plugin))
        for broken in (
            SAFE_DUMP.replace("  disabled: true\n- id: session-title-llm", "- id: session-title-llm"),
            SAFE_DUMP.replace("- id: headless-runner\n  disabled: true", "- id: headless-runner"),
            SAFE_DUMP.replace("- id: workflow\n  disabled: true", "- id: workflow"),
            SAFE_DUMP.replace("- id: tool-fs\n", ""),
            SAFE_DUMP.replace("- id: agent\n", "- id: fake-agent\n"),
            SAFE_DUMP.replace("- id: buddy-read-only-structured", "- id: buddy-other-bridge"),
            SAFE_DUMP + "- id: buddy-read-only-structured\n  name: file:///private/other.mjs\n",
            SAFE_DUMP + "- id: buddy-read-only-structured\n  name: file:///private/bridge.mjs\n",
            "- id: buddy-read-only-structured\n  name: file:///private/bridge.mjs\n",
            SAFE_DUMP.replace("- id: workflow\n  disabled: true", "- id: workflow\n  disabled: !!js true"),
            "- [broken\n", "- just a scalar\n",
        ):
            with self.subTest(dump=broken.splitlines()[0][:40]):
                self.assertFalse(read_only_composed(broken, plugin))

    def test_free_check_declares_restricted_review_without_a_sandbox(self):
        result = DshAdapter().local_read_only_check()
        self.assertTrue(result["eligible"])
        self.assertFalse(result["systemSandbox"])
        self.assertTrue(result["sameAttemptContinuation"])
        self.assertIsNone(result["reasonCode"])
        self.assertEqual(DshAdapter.read_only_tool_categories, ("read", "search"))

    def test_free_check_fails_closed_without_the_packaged_bridge(self):
        from buddy.errors import BoardError
        cases = (("missing", patch("buddy.adapters.dsh_read_only.bridge_plugin",
                                   return_value=Path("/nonexistent/bridge.mjs"))),
                 ("undeclared", patch("buddy.adapters.dsh_read_only.bridge_plugin",
                                      side_effect=BoardError("RUNTIME_MANIFEST_MISSING", "no manifest"))))
        for label, broken in cases:
            with broken, self.subTest(mode=label):
                result = DshAdapter().local_read_only_check()
                self.assertFalse(result["eligible"])
                self.assertFalse(result["systemSandbox"])
                self.assertTrue(result["sameAttemptContinuation"])
                self.assertEqual(result["reasonCode"], "readonly-resource-missing")


class DshReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-dsh-read-only-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = 0
        self.cwd = self.root / "frozen"
        self.cwd.mkdir(mode=0o700)
        (self.cwd / "input.txt").write_text("frozen review copy\n")
        home = self.root / "home"
        profile = home / ".dsh" / "profiles" / "headless"
        profile.mkdir(parents=True)
        (profile / "package.json").write_text("{}")
        self.fake = self.root / "fake-dsh-readonly"
        shutil.copyfile(FIXTURE, self.fake)
        self.fake.chmod(0o755)
        self.record = self.root / "harness-record.json"
        self.write_record(str(self.fake))
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                            and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment.update(HOME=str(home), BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_DEV_SOURCE="1",
                                BUDDY_HARNESS_RECORD_FILE=str(self.record))

    def write_record(self, command):
        self.record.write_text(json.dumps({"dsh": {"status": "ready", "command": [command]}}))

    def execute(self, case, *, tool_calls=8, timeout=20, cancel=False, capture=False):
        self.index += 1
        (self.root / "case").write_text(case)
        context = ExecutionContext("task", f"attempt-{self.index}", self.index,
            {**SPEC, "cwd": str(self.cwd), "timeoutSeconds": timeout},
            self.root / f"attempt-{self.index}", {}, self.environment)
        request = ReadOnlyStructuredRequest(str(self.cwd), PROMPT, SCHEMA,
                                            {"timeoutSeconds": timeout, "toolCalls": tool_calls},
                                            capture_evidence=capture)
        handle = DshAdapter().start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        if cancel:
            time.sleep(0.3)
            handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(timeout + 10))
        outcome = read_only.collect(handle)
        if outcome.status == "failed" and outcome.result.get("code") not in (
                "invalid-native-result", "stream-incomplete", "native-turn-failed", "stop-unknown",
                "configuration-unavailable", "read-only-tools-unavailable", "deadline",
                "readonly-budget-exhausted", "answer-too-large",
                "read-only-profile-unsafe", "read-only-profile-unverified"):
            self.fail(f"controller stderr: {Path(handle.log_paths['stderr']).read_text()}")
        return outcome

    def request_of(self, call):
        return json.loads((self.root / f"attempt-{self.index}" / f"call-{call}" / "request.json").read_text())

    def test_control_file_carries_only_the_python_identity_and_request(self):
        outcome = self.execute("ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        control = json.loads((self.root / f"attempt-{self.index}" / "readonly-control.json").read_text())
        self.assertEqual(control["taskId"], "task")
        self.assertEqual(control["attemptId"], f"attempt-{self.index}")
        self.assertEqual(control["generation"], self.index)
        self.assertEqual(control["cwd"], str(self.cwd))
        self.assertEqual(control["spec"], SPEC)
        self.assertEqual(control["readOnlyRequest"]["budget"], {"timeoutSeconds": 20, "toolCalls": 8})
        request = self.request_of(1)
        self.assertEqual(set(request), {"callId", "cwd", "spec", "prompt", "outputSchema", "budget"})
        self.assertNotIn("taskId", json.dumps(request))
        self.assertNotIn("attemptId", json.dumps(request))
        self.assertTrue((self.root / f"attempt-{self.index}" / "dsh-home" / "profiles" / "headless" /
                         "package.json").is_file())

    def test_clean_turn_reports_facts_and_stays_publishable(self):
        outcome = self.execute("ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["correctionCount"], 0)
        self.assertEqual(result["usage"]["toolCalls"], 0)
        self.assertIsNone(result["observed"])
        package = result["toolEvidence"]
        self.assertEqual(package["binding"], {"adapter": "dsh", "taskId": "task",
                                              "attemptId": f"attempt-{self.index}", "generation": self.index})
        self.assertEqual(package["nativeIdentity"], [result["nativeIdentity"]])
        self.assertEqual(package["events"], [])
        self.assertTrue(package["streamComplete"])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], package["truncated"]), (0, 0, False))
        self.assertIsNone(judge_tool_evidence(package, "review", False))
        self.assertNotIn(PROMPT, json.dumps(package))

    def test_incomplete_native_dto_is_never_a_zero_tool_receipt(self):
        for case in ('events-missing', 'truncated-missing', 'model-not-started'):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, 'failed')
                self.assertEqual(outcome.result['code'], 'invalid-native-result')
                self.assertEqual(outcome.result['usage']['toolCalls'], 0)

    def test_native_tool_budget_failure_remains_a_budget_failure(self):
        outcome = self.execute('tool-budget-exhausted')
        self.assertEqual(outcome.status, 'failed')
        self.assertEqual(outcome.result['code'], 'readonly-budget-exhausted')
        self.assertTrue(outcome.shutdown_confirmed)

    def test_legal_native_read_and_search_loop_is_not_a_correction(self):
        outcome = self.execute("tools-ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["correctionCount"], 0)
        self.assertEqual({key: result['usage'][key] for key in ('inputTokens', 'outputTokens', 'toolCalls')},
                         {"inputTokens": 30, "outputTokens": 9, "toolCalls": 2})
        self.assertIsNone(result['usage']['bytesRead'])
        self.assertGreaterEqual(result['usage']['elapsedMs'], 0)
        package = result["toolEvidence"]
        self.assertEqual([(event["toolName"], event["category"], event["phase"]) for event in package["events"]],
                         [("read", "read", "start"), ("read", "read", "end"),
                          ("grep", "search", "start"), ("grep", "search", "end")])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 0))
        self.assertEqual(package["nativeIdentity"], [result["nativeIdentity"]])
        self.assertIsNone(judge_tool_evidence(package, "review", False))

    def test_one_correction_accumulates_roots_budget_and_facts(self):
        outcome = self.execute("correct-tools", tool_calls=8)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["correctionCount"], 1)
        second = self.request_of(2)
        # The correction round carries only the remaining tools and the
        # remaining wall time of the one absolute deadline.
        self.assertEqual(second["budget"]["toolCalls"], 6)
        self.assertLessEqual(1, second["budget"]["timeoutSeconds"])
        self.assertLessEqual(second["budget"]["timeoutSeconds"], 20)
        self.assertIn("Format correction: answer-invalid-json", second["prompt"])
        self.assertEqual(self.request_of(1)["budget"]["toolCalls"], 8)
        package = result["toolEvidence"]
        self.assertEqual(len(package["nativeIdentity"]), 2)
        self.assertNotEqual(package["nativeIdentity"][0], package["nativeIdentity"][1])
        self.assertEqual(package["toolCalls"], 3)
        self.assertEqual({key: result['usage'][key] for key in ('inputTokens', 'outputTokens', 'toolCalls')},
                         {"inputTokens": 61, "outputTokens": 18, "toolCalls": 3})
        self.assertIsNone(result['usage']['bytesRead'])
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(judge_tool_evidence(package, "review", False))

    def test_correction_may_spend_zero_remaining_tool_calls(self):
        outcome = self.execute("correct-zero", tool_calls=2)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["correctionCount"], 1)
        self.assertEqual(self.request_of(2)["budget"]["toolCalls"], 0)
        package = result["toolEvidence"]
        self.assertEqual(package["toolCalls"], 2)
        self.assertEqual(len(package["events"]), 4)
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(judge_tool_evidence(package, "review", False))

    def test_out_of_bounds_candidate_is_never_corrected(self):
        outcome = self.execute("enum")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertFalse(result["answerValid"])
        self.assertEqual(result["correctionCount"], 0)
        self.assertFalse((self.root / f"attempt-{self.index}" / "call-2").exists())

    def test_missing_identity_fact_is_retained_incomplete(self):
        outcome = self.execute("missing-id")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "stream-incomplete")
        package = outcome.result["toolEvidence"]
        event = package["events"][0]
        self.assertIsNone(event["callId"])
        self.assertEqual((event["toolName"], event["category"], event["phase"]), ("read", "read", "start"))
        self.assertEqual(package["toolCalls"], 0)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(judge_tool_evidence(package, "review", False), TOOL_EVIDENCE_UNVERIFIED)

    def test_native_failures_keep_bounded_facts(self):
        outcome = self.execute("native-turn-failed")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "native-turn-failed")
        self.assertEqual(outcome.result["nativeTurnEnd"], "error")
        self.assertNotIn("rawAnswer", outcome.result)
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"], [])
        self.assertEqual(package["toolCalls"], 0)
        for case, code, started in (("provider-missing", "configuration-unavailable", False),
                                    ("tools-unavailable", "read-only-tools-unavailable", False)):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], code)
                self.assertFalse(outcome.result["modelStarted"])
                self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])

    def test_only_fixed_failure_stages_leave_the_private_native_receipt(self):
        for stage in ('provider-registration', 'agent-create', 'native-turn', 'invalid', 'private-detail'):
            with self.subTest(stage=stage):
                outcome = self.execute('provider-stage-' + stage)
                self.assertEqual(outcome.result['code'], 'configuration-unavailable')
                self.assertFalse(outcome.result['modelStarted'])
                self.assertTrue(outcome.shutdown_confirmed)
                if stage in ('provider-registration', 'agent-create', 'native-turn'):
                    self.assertEqual(outcome.result['failureStage'], stage)
                else:
                    self.assertNotIn('failureStage', outcome.result)
                    self.assertNotIn('private', str(outcome.result))

    def test_failure_diagnostics_keep_only_class_and_integer_bridge_positions(self):
        for case in ('valid', 'boolean', 'fraction', 'negative', 'extra'):
            with self.subTest(case=case):
                outcome = self.execute('failure-diagnostics-' + case)
                self.assertFalse(outcome.result['modelStarted'])
                self.assertTrue(outcome.shutdown_confirmed)
                if case == 'valid':
                    self.assertEqual(outcome.result['failureKind'], 'TypeError')
                    self.assertEqual(outcome.result['failureSite'], {'line': 400, 'column': 5})
                else:
                    self.assertNotIn('failureKind', outcome.result)
                    self.assertNotIn('failureSite', outcome.result)
                    self.assertNotIn('private detail', str(outcome.result))

    def test_untrusted_receipts_fail_before_publishing_an_answer(self):
        for case, calls in (("usage-mismatch", 1), ("resolved-provider", 0), ("resolved-model", 0),
                            ("resolved-effort", 0), ("identity-invalid", 0)):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertEqual(outcome.result["code"], "invalid-native-result")
                self.assertNotIn("rawAnswer", outcome.result)
                self.assertEqual(outcome.result['usage']['toolCalls'],
                                 1 if case == 'usage-mismatch' else 0)
                self.assertEqual(outcome.result['usage']['toolCalls'], outcome.result['toolEvidence']['toolCalls'])
                package = outcome.result["toolEvidence"]
                # The tool-event stream itself was real and complete; the answer
                # envelope around it is what failed the readback.
                self.assertEqual(package["toolCalls"], calls)
                self.assertTrue(package["streamComplete"])

    def test_node_stop_unknown_is_reported_and_the_group_confirmed_stopped(self):
        outcome = self.execute("stop-unknown")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "stop-unknown")
        self.assertFalse(outcome.shutdown_confirmed)
        self.assertFalse(outcome.result["processState"]["shutdownConfirmed"])
        self.assertNotIn("rawAnswer", outcome.result)

    def test_deadline_and_cancel_stop_the_owned_group(self):
        late = self.execute("timeout", timeout=1)
        self.assertEqual(late.status, "failed")
        self.assertEqual(late.result["code"], "deadline")
        self.assertTrue(late.shutdown_confirmed)
        cancelled = self.execute("timeout", timeout=10, cancel=True)
        self.assertEqual(cancelled.status, "cancelled", cancelled.to_report())
        self.assertTrue(cancelled.shutdown_confirmed)
        self.assertEqual(cancelled.result["toolEvidence"]["events"], [])

    def test_dump_config_preflight_fails_closed_before_any_model(self):
        for case in ("dump-bridge", "dump-title", "dump-telemetry", "dump-workflow",
                     "dump-stack", "unsafe"):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertEqual(outcome.result["code"], "read-only-profile-unsafe")
                self.assertFalse(outcome.result["modelStarted"])
                self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])
                self.assertEqual(outcome.result["toolEvidence"]["events"], [])
                self.assertFalse((self.root / f"attempt-{self.index}" / "call-1" / "native").exists())

    def test_missing_bridge_fails_closed_before_any_native_call(self):
        directory = self.root / "controller-direct"
        directory.mkdir(mode=0o700)
        control = {"directory": str(directory), "cwd": str(self.cwd), "timeoutSeconds": 5,
                   "taskId": "task", "attemptId": "attempt-direct", "generation": 1,
                   "spec": SPEC,
                   "readOnlyRequest": {"prompt": PROMPT, "outputSchema": SCHEMA,
                                       "budget": {"timeoutSeconds": 5, "toolCalls": 8},
                                       "captureEvidence": False}}
        with patch("buddy.adapters.dsh_read_only.bridge_plugin", return_value=self.root / "missing.mjs"):
            result, code = dsh_read_only.run(control, threading.Event())
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "adapter-unavailable")
        self.assertFalse(result["modelStarted"])
        self.assertTrue(result["processState"]["shutdownConfirmed"])
        self.assertEqual(result["toolEvidence"]["binding"],
                         {"adapter": "dsh", "taskId": "task", "attemptId": "attempt-direct", "generation": 1})
        self.assertEqual(result["toolEvidence"]["events"], [])
        self.assertFalse((directory / "dsh-home").exists())

    def test_runner_main_dispatches_one_branch_per_control_kind(self):
        from buddy.adapters import dsh_runner
        review_control = self.root / "review-control.json"
        review_control.write_text(json.dumps({"directory": str(self.root), "readOnlyRequest": {}}))
        no_tool_control = self.root / "no-tool-control.json"
        no_tool_control.write_text(json.dumps({"directory": str(self.root), "noToolRequest": {}}))
        with patch.object(dsh_read_only, "run", return_value=({"status": "ok"}, 0)) as review, \
             patch.object(dsh_runner, "run", return_value=({"status": "ok"}, 0)) as legacy:
            for path, expected in ((review_control, review), (no_tool_control, legacy)):
                with self.subTest(control=path.name):
                    with patch.object(sys, "argv", ["dsh_runner", "--control", str(path)]):
                        self.assertEqual(dsh_runner.main(), 0)
            review.assert_called_once()
            legacy.assert_called_once()

    def test_ordinary_dsh_no_tool_entry_still_runs_through_the_same_adapter(self):
        notool = self.root / "notool"
        notool.mkdir()
        fake = notool / "fake-dsh-no-tool"
        shutil.copyfile(NO_TOOL_FIXTURE, fake)
        fake.chmod(0o755)
        (notool / "case").write_text("ok")
        self.write_record(str(fake))
        empty = self.root / "empty"
        empty.mkdir(mode=0o700)
        self.index += 1
        context = ExecutionContext("task", f"attempt-{self.index}", self.index,
            {**SPEC, "cwd": str(empty), "timeoutSeconds": 20}, self.root / f"attempt-{self.index}", {},
            self.environment)
        request = NoToolStructuredRequest(str(empty), PROMPT, SCHEMA, timeout_seconds=20)
        handle = DshAdapter().start_no_tool_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(28))
        outcome = read_only.collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["toolEvidence"]["binding"]["attemptId"], f"attempt-{self.index}")


if __name__ == "__main__":
    unittest.main()
