"""Codex fast Router uses the user's native configuration and verifies the full stream.

Every case runs through the registered role seam — the same
``start_router_preparation`` entry the Router's DecisionAdapter uses — so the
permission parameters, the policy readback and the EOF drain are asserted
on the production path with the offline ``no_tool_codex.py`` App Server.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.roles.controller import FastPreparation, start_router_preparation
from hey_my_buddy.buddy.roles.structured_call import collect, correction_code


FIXTURE = Path(__file__).parent / "fixtures" / "no_tool_codex.py"
SOURCE = Path(__file__).resolve().parents[5] / "src"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["buddyId"],
          "properties": {"buddyId": {"type": "string", "enum": ["legal"]}}}


class NoToolCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-no-tool-codex-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'empty-runtime'
        self.runtime.mkdir(mode=0o700)
        self.home = self.root / "account-home"
        self.home.mkdir(mode=0o700)
        (self.home / "auth.json").write_text('{"test":"fake-auth-only"}')
        (self.home / "models_cache.json").write_text(json.dumps({"client_version": "0.157.0", "models": [{
            "slug": "fixture-model", "display_name": "Fixture", "shell_type": "unified_exec",
            "apply_patch_tool_type": "freeform", "experimental_supported_tools": ["clock"],
            "supported_reasoning_levels": [{"effort": "low"}], "tool_mode": "code_mode_only",
            "multi_agent_version": "v2"}]}))
        (self.home / "config.toml").write_text('model_provider = "openai"\n[features]\napps = true\n\n[mcp_servers.fixture]\ncommand = "fixture-mcp"\n')
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)

    def run_case(self, case="ok", timeout=3):
        """One fast call through the registered role controller; returns the outcome and its directory."""
        directory = self.root / ("run-" + case)
        directory.mkdir(mode=0o700)
        environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        environment.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                           PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                           HMB_STATE_DIR=str(self.root / 'state'), HMB_RUNTIME_ROOT=str(self.runtime),
                           HMB_DEV_SOURCE="1", HMB_CODEX_CLI=str(FIXTURE),
                           HMB_CODEX_FIXTURE_CASE=case, HMB_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        context = ExecutionContext("router-task", "router-attempt", 7,
                                   {"provider": "openai", "model": "fixture-model", "effort": "low",
                                    "cwd": str(self.cwd), "timeoutSeconds": timeout}, directory, {}, environment)
        request = NoToolStructuredRequest(str(self.cwd), "Pick a buddy", SCHEMA, timeout_seconds=timeout)
        handle = start_router_preparation(FastPreparation("codex",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(timeout + 9), "the fast role controller did not exit")
        return collect(handle), handle.role_run_control, directory

    def test_capability_inherits_user_configuration_and_observes_zero_tools(self):
        self.assertTrue(CodexAdapter().no_tool_structured)
        outcome, control, directory = self.run_case()
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
        self.assertEqual(json.loads(outcome.result["rawAnswer"]), {"buddyId": "legal"})
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"harness": "codex", "taskId": "router-task",
                                               "attemptId": "router-attempt", "generation": 7})
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "turn-1"}])
        self.assertTrue(evidence["streamComplete"])
        self.assertEqual((evidence["events"], evidence["toolCalls"], evidence["unsettledToolCalls"],
                          evidence["truncated"]), ([], 0, 0, False))
        trace = json.loads((directory / "trace.json").read_text())
        self.assertEqual(trace["home"], str(self.home))
        self.assertEqual(trace["userHome"], str(self.root))
        self.assertIs(trace["thread"]["ephemeral"], True)
        for name in ("baseInstructions", "environments", "dynamicTools", "selectedCapabilityRoots", "allowProviderModelFallback"):
            self.assertNotIn(name, trace["thread"])
        self.assertNotIn("environments", trace["turns"][0])
        self.assertFalse(list(Path(control["nativeRoot"]).rglob("codex-home")))
        self.assertFalse((self.home / "auth.json").is_symlink())
        self.assertEqual((self.home / "auth.json").read_text(), '{"test":"fake-auth-only"}')
        self.assertEqual(json.loads((self.home / "models_cache.json").read_text())["models"][0]["multi_agent_version"], "v2")

    def test_protocol_variants_preserve_non_tool_answers_and_conservative_tool_evidence(self):
        from buddy.harnesses.codex.fixtures.protocol_items import PROTOCOL_ITEMS, FUTURE_ITEMS
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        for name, carrier, _fields, calls, phases, complete in PROTOCOL_ITEMS + FUTURE_ITEMS:
            with self.subTest(name=name):
                outcome, _control, _directory = self.run_case("protocol-" + name)
                evidence = outcome.result["toolEvidence"]
                self.assertEqual(evidence["toolCalls"], calls)
                # Fast calls interrupt on the first tool marker. A typed result
                # has no end fact yet when its display's started frame arrives.
                fast_phases = (() if name == "functionCallOutput" else phases[:1]) if carrier == "typed" else phases
                self.assertEqual(tuple(event["phase"] for event in evidence["events"]), fast_phases)
                self.assertEqual(judge_tool_evidence(evidence, "fast", False),
                                 "router-tool-evidence-unverified" if not complete or name == "functionCallOutput"
                                 else "router-tools-forbidden" if phases else None)
                if phases and complete:
                    self.assertEqual(outcome.status, "failed", outcome.result)
                    self.assertEqual(outcome.result["code"], "no-tool-violation")
                    self.assertFalse(evidence["streamComplete"])
                    self.assertFalse(outcome.result["zeroToolVerified"])
                elif not phases:
                    self.assertEqual(outcome.status, "ok", outcome.result)
                    self.assertEqual(json.loads(outcome.result["rawAnswer"]), {"buddyId": "legal"})
                    self.assertTrue(evidence["streamComplete"])
                    self.assertTrue(outcome.result["zeroToolVerified"])

    def test_adapter_entrypoint_and_collector(self):
        directory = self.root / "harness"
        environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        environment.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                           PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                           HMB_RUNTIME_ROOT=str(self.runtime),
                           HMB_DEV_SOURCE="1", HMB_CODEX_CLI=str(FIXTURE),
                           HMB_STATE_DIR=str(self.root / "state"),
                           HMB_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        context = ExecutionContext("task", "attempt", 1,
                                   {"provider": "openai", "model": "fixture-model", "effort": "low",
                                    "cwd": str(self.cwd), "timeoutSeconds": 3}, directory, {}, environment)
        request = NoToolStructuredRequest(str(self.cwd), "Pick a buddy", SCHEMA, 3)
        handle = start_router_preparation(FastPreparation("codex",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(8))
        outcome = collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["toolEvidence"]["binding"],
                         {"harness": "codex", "taskId": "task", "attemptId": "attempt", "generation": 1})

    def test_native_metadata_notifications_do_not_hide_tools_or_break_the_turn(self):
        outcome, _control, _directory = self.run_case('native-metadata')
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertTrue(outcome.result['zeroToolVerified'])

    def test_user_cache_and_configuration_are_not_rewritten(self):
        config = self.home / 'config.toml'
        cache = self.home / 'models_cache.json'
        before_config, before_cache = config.read_bytes(), cache.read_bytes()
        outcome, _control, _directory = self.run_case()
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertEqual(config.read_bytes(), before_config)
        self.assertEqual(cache.read_bytes(), before_cache)

    def test_native_usage_is_returned_for_no_tool_turns(self):
        outcome, _control, _directory = self.run_case('usage')
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertEqual(outcome.result['tokenUsage']['inputTokens'], 17)
        self.assertEqual(outcome.result['tokenUsage']['outputTokens'], 5)
        self.assertEqual(outcome.result['tokenUsage']['cachedInputTokens'], 3)
        self.assertEqual(outcome.result['tokenUsage']['reasoningOutputTokens'], 1)
        self.assertEqual(outcome.result['tokenUsage']['completeness'], 'complete')
        self.assertNotIn('totalTokens', outcome.result['tokenUsage'])

    def test_every_tool_shape_and_late_tool_fails_closed(self):
        for case in ("typed", "collab", "raw", "raw-no-id", "turn-item", "late", "request"):
            with self.subTest(case=case):
                outcome, _control, _directory = self.run_case(case)
                self.assertEqual(outcome.status, 'failed', outcome.result)
                self.assertEqual(outcome.result["code"], "no-tool-violation", outcome.result)
                # The failure receipt states its explicit zero-tool fact; it is
                # never silently absent on a refused fast call.
                self.assertIs(outcome.result["zeroToolVerified"], False)

    def test_projected_facts_survive_rejections_and_corrections_add_roots(self):
        for case, tool_name in (("typed", "commandExecution"), ("collab", "collabAgentToolCall"),
                                ("raw", "exec_command"), ("late", "custom_tool_call_output")):
            with self.subTest(case=case):
                outcome, _control, _directory = self.run_case(case)
                evidence = outcome.result["toolEvidence"]
                self.assertFalse(evidence["streamComplete"])
                self.assertTrue(any(event["toolName"] == tool_name for event in evidence["events"]),
                                (case, evidence["events"]))
                self.assertEqual(evidence["binding"]["taskId"], "router-task")
        outcome, _control, _directory = self.run_case("format")
        self.assertEqual(outcome.status, 'ok', outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual([identity["turnId"] for identity in evidence["nativeIdentity"]], ["turn-1", "turn-2"])
        self.assertEqual(evidence["events"], [])
        self.assertTrue(evidence["streamComplete"])

    def test_unknown_event_keeps_a_valid_answer(self):
        outcome, _control, _directory = self.run_case("unknown")
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.result["answerValid"])
        self.assertEqual(outcome.result["nativeEvidence"]["unknownEvents"],
                         {"countsByType": {"account/updated": 1, "futureThing": 1}, "total": 2})
        self.assertNotIn("unknownEvents", outcome.result)

    def test_partial_and_eof_rejected(self):
        for case in ("truncated", "eof"):
            with self.subTest(case=case):
                outcome, _control, _directory = self.run_case(case)
                self.assertEqual(outcome.status, 'failed', outcome.result)
                self.assertIs(outcome.result["zeroToolVerified"], False)

    def test_unconfirmed_interrupt_is_reported_without_a_fabricated_ack(self):
        outcome, _control, _directory = self.run_case("late")
        self.assertEqual(outcome.result["code"], "no-tool-violation", outcome.result)
        # The drain already closed the native input, so the interrupt request
        # is a real fact while its acknowledgement never arrived: the true key
        # for the request is kept and no acknowledgement key is invented.
        self.assertIs(outcome.result["nativeInterruptRequested"], True)
        self.assertNotIn("nativeInterruptAcknowledged", outcome.result)

    def test_one_format_correction_but_no_out_of_bounds_retry(self):
        outcome, _control, directory = self.run_case("format")
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertEqual(outcome.result["correctionCount"], 1)
        self.assertEqual(len(json.loads((directory / "trace.json").read_text())["turns"]), 2)
        outcome, _control, directory = self.run_case("outside")
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertFalse(outcome.result["answerValid"])
        self.assertEqual(len(json.loads((directory / "trace.json").read_text())["turns"]), 1)
        bounded = {"type": "object", "additionalProperties": False, "required": ["reason"],
                   "properties": {"reason": {"type": "string", "maxLength": 2}}}
        self.assertEqual(correction_code('{"reason":"too long"}', bounded), 'answer-shape')

    def test_policy_readback_differences_and_deadline(self):
        outcome, _control, directory = self.run_case("config-mismatch")
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertIs(outcome.result["modelStarted"], True)
        self.assertIs(outcome.result["zeroToolVerified"], True)
        self.assertEqual(len(json.loads((directory / "trace.json").read_text())["turns"]), 1)
        self.assertEqual(outcome.result["policyCheck"]["configReadback"]["status"], "unknown")
        self.assertEqual(outcome.result["nativeConfigPolicy"]["web_search"], "enabled")
        outcome, _control, directory = self.run_case("wrong-model")
        self.assertEqual(outcome.status, 'failed', outcome.result)
        self.assertEqual(outcome.result["code"], "buddy-mismatch")
        self.assertIs(outcome.result["modelStarted"], False)
        self.assertIs(outcome.result["zeroToolVerified"], False)
        self.assertEqual(outcome.result["nativeThreadReceipt"]["model"], "other")
        self.assertNotIn("turns", json.loads((directory / "trace.json").read_text()))
        outcome, control, _directory = self.run_case("hang", timeout=1)
        self.assertEqual(outcome.status, 'failed', outcome.result)
        self.assertEqual(outcome.result["code"], "deadline")
        self.assertIs(outcome.result["zeroToolVerified"], False)
        self.assertIs(outcome.result["nativeInterruptRequested"], True)
        self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])
        self.assertFalse(list(Path(control["nativeRoot"]).rglob("codex-home")))

    def test_absent_user_model_cache_does_not_refuse_a_native_catalog(self):
        (self.home / 'models_cache.json').unlink()
        outcome, _control, _directory = self.run_case()
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertTrue(outcome.result['zeroToolVerified'])


if __name__ == "__main__":
    unittest.main()
