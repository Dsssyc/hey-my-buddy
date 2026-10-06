"""Codex fast Router uses a private native catalog and verifies the full stream.

Every case runs through the registered role seam — the same
``start_router_preparation`` entry the Router's DecisionAdapter uses — so the
no-tool parameters, the policy layers readback and the EOF drain are asserted
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
from hey_my_buddy.buddy.harnesses.codex.config import prepare_no_tool_home
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.buddy.roles.controller import FastPreparation, start_router_preparation
from hey_my_buddy.buddy.roles.structured_call import collect, correction_code


FIXTURE = Path(__file__).parent / "fixtures" / "no_tool_codex.py"
SOURCE = Path(__file__).resolve().parents[5] / "src"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["profileId"],
          "properties": {"profileId": {"type": "string", "enum": ["legal"]}}}


class NoToolCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-no-tool-codex-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
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
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)

    def run_case(self, case="ok", timeout=3):
        """One fast call through the registered role controller; returns the outcome and its directory."""
        directory = self.root / ("run-" + case)
        directory.mkdir(mode=0o700)
        environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        environment.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                           PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                           BUDDY_STATE_DIR=str(self.root / 'state'), BUDDY_RUNTIME_ROOT=str(self.runtime),
                           BUDDY_DEV_SOURCE="1", BUDDY_CODEX_CLI=str(FIXTURE),
                           BUDDY_CODEX_FIXTURE_CASE=case, BUDDY_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        context = ExecutionContext("router-task", "router-attempt", 7,
                                   {"provider": "openai", "model": "fixture-model", "effort": "low",
                                    "cwd": str(self.cwd), "timeoutSeconds": timeout}, directory, {}, environment)
        request = NoToolStructuredRequest(str(self.cwd), "Pick a profile", SCHEMA, timeout_seconds=timeout)
        handle = start_router_preparation(FastPreparation("codex",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(timeout + 9), "the fast role controller did not exit")
        return collect(handle), handle.role_run_control, directory

    def test_capability_and_empty_native_tool_configuration(self):
        self.assertTrue(CodexAdapter().no_tool_structured)
        outcome, control, directory = self.run_case()
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
        self.assertEqual(json.loads(outcome.result["rawAnswer"]), {"profileId": "legal"})
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"adapter": "codex", "taskId": "router-task",
                                               "attemptId": "router-attempt", "generation": 7})
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "turn-1"}])
        self.assertTrue(evidence["streamComplete"])
        self.assertEqual((evidence["events"], evidence["toolCalls"], evidence["unsettledToolCalls"],
                          evidence["truncated"]), ([], 0, 0, False))
        trace = json.loads((directory / "trace.json").read_text())
        self.assertEqual(trace["thread"]["environments"], [])
        self.assertEqual(trace["thread"]["dynamicTools"], [])
        self.assertEqual(trace["thread"]["selectedCapabilityRoots"], [])
        self.assertEqual(trace["turns"][0]["environments"], [])
        self.assertNotIn("Pick a profile", trace["thread"]["baseInstructions"])
        private = json.loads((Path(trace["home"]) / "no-tool-models.json").read_text())["models"][0]
        self.assertEqual(private["shell_type"], "disabled")
        self.assertIsNone(private["apply_patch_tool_type"])
        self.assertEqual(private["experimental_supported_tools"], [])
        self.assertEqual(private["tool_mode"], "direct")
        self.assertIsNone(private["multi_agent_version"])
        self.assertFalse((Path(control["nativeRoot"]) / "codex-home/auth.json").is_symlink())
        self.assertTrue((self.home / "auth.json").is_file())

    def test_adapter_entrypoint_and_collector(self):
        directory = self.root / "adapter"
        environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        environment.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                           PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                           BUDDY_RUNTIME_ROOT=str(self.runtime),
                           BUDDY_DEV_SOURCE="1", BUDDY_CODEX_CLI=str(FIXTURE),
                           BUDDY_STATE_DIR=str(self.root / "state"),
                           BUDDY_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        context = ExecutionContext("task", "attempt", 1,
                                   {"provider": "openai", "model": "fixture-model", "effort": "low",
                                    "cwd": str(self.cwd), "timeoutSeconds": 3}, directory, {}, environment)
        request = NoToolStructuredRequest(str(self.cwd), "Pick a profile", SCHEMA, 3)
        handle = start_router_preparation(FastPreparation("codex",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(8))
        outcome = collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["toolEvidence"]["binding"],
                         {"adapter": "codex", "taskId": "task", "attemptId": "attempt", "generation": 1})

    def test_native_metadata_notifications_do_not_hide_tools_or_break_the_turn(self):
        outcome, _control, _directory = self.run_case('native-metadata')
        self.assertEqual(outcome.status, 'ok', outcome.result)
        self.assertTrue(outcome.result['zeroToolVerified'])

    def test_compatible_cache_from_another_client_version_and_private_instructions(self):
        cache = self.home / 'models_cache.json'
        data = json.loads(cache.read_text())
        data['client_version'] = '0.158.0'
        cache.write_text(json.dumps(data))
        outcome, control, _directory = self.run_case()
        self.assertEqual(outcome.status, 'ok', outcome.result)
        import tomllib
        config = tomllib.loads((Path(control['nativeRoot']) / 'codex-home/config.toml').read_text())
        self.assertEqual(config['project_doc_max_bytes'], 0)
        self.assertFalse(config['skills']['include_instructions'])
        self.assertFalse(config['skills']['bundled']['enabled'])

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

    def test_unknown_partial_and_eof_rejected(self):
        for case in ("unknown", "truncated", "eof"):
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

    def test_policy_and_deadline(self):
        for case in ("config-mismatch", "wrong-model"):
            with self.subTest(case=case):
                outcome, _control, _directory = self.run_case(case)
                self.assertEqual(outcome.status, 'failed', outcome.result)
                self.assertEqual(outcome.result["code"], "no-tool-policy-unverified")
                self.assertIs(outcome.result["zeroToolVerified"], False)
        outcome, control, _directory = self.run_case("hang", timeout=1)
        self.assertEqual(outcome.status, 'failed', outcome.result)
        self.assertEqual(outcome.result["code"], "deadline")
        self.assertIs(outcome.result["zeroToolVerified"], False)
        self.assertIs(outcome.result["nativeInterruptRequested"], True)
        self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])
        self.assertFalse((Path(control["nativeRoot"]) / "codex-home/auth.json").is_symlink())

    def test_missing_or_mismatched_native_metadata_refused(self):
        cache = self.home / 'models_cache.json'
        cache.unlink()
        with self.assertRaises(CodexProtocolError) as caught:
            prepare_no_tool_home(self.root / "private", {"CODEX_HOME": str(self.home)},
                                 {"model": "fixture-model", "effort": "low"})
        self.assertEqual(caught.exception.code, "no-tool-policy-unverified")
        outcome, _control, _directory = self.run_case('missing-metadata')
        self.assertEqual(outcome.status, 'failed', outcome.result)
        self.assertIs(outcome.result['modelStarted'], False)
        self.assertTrue(outcome.result['processState']['shutdownConfirmed'])


if __name__ == "__main__":
    unittest.main()
