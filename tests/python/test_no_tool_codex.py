"""Codex fast Router uses a private native catalog and verifies the full stream."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from buddy.adapters.codex import CodexAdapter
from buddy.adapters.base import ExecutionContext, NoToolStructuredRequest
from buddy.adapters.codex_no_tool import _format_correction, prepare_home
from buddy.adapters.codex_protocol import CodexProtocolError
from buddy.adapters.read_only import collect


FIXTURE = Path(__file__).parent / "fixtures" / "no_tool_codex.py"
SOURCE = Path(__file__).resolve().parents[2] / "src"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["profileId"],
          "properties": {"profileId": {"type": "string", "enum": ["legal"]}}}


class NoToolCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-no-tool-codex-")
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
        directory = self.root / ("run-" + case)
        directory.mkdir(mode=0o700)
        control = {"directory": str(directory), "nativeRoot": str(directory / "native"),
                   "cwd": str(self.cwd), "timeoutSeconds": timeout,
                   "taskId": "router-task", "attemptId": "router-attempt", "generation": 7,
                   "spec": {"provider": "openai", "model": "fixture-model", "effort": "low"},
                   "noToolRequest": {"prompt": "Pick a profile", "outputSchema": SCHEMA}}
        control_path = directory / "control.json"
        control_path.write_text(json.dumps(control))
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        env.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                   PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                   BUDDY_STATE_DIR=str(self.root / 'state'), BUDDY_RUNTIME_ROOT=str(self.runtime),
                   BUDDY_DEV_SOURCE="1", BUDDY_CODEX_CLI=str(FIXTURE),
                   BUDDY_CODEX_FIXTURE_CASE=case, BUDDY_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        process = subprocess.run([sys.executable, "-m", "buddy.adapters.codex_runner", "--control", str(control_path)],
                                 cwd=self.cwd, env=env, capture_output=True, text=True, timeout=12)
        return process, json.loads(process.stdout), directory

    def test_capability_and_empty_native_tool_configuration(self):
        self.assertTrue(CodexAdapter().no_tool_structured)
        process, result, directory = self.run_case()
        self.assertEqual(process.returncode, 0, result)
        self.assertTrue(result["zeroToolVerified"])
        self.assertEqual(result["usage"]["toolCalls"], 0)
        self.assertEqual(json.loads(result["rawAnswer"]), {"profileId": "legal"})
        evidence = result["toolEvidence"]
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
        self.assertFalse((directory / "native/codex-home/auth.json").is_symlink())
        self.assertTrue((self.home / "auth.json").is_file())

    def test_adapter_entrypoint_and_collector(self):
        directory = self.root / "adapter"
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
        env.update(HOME=str(self.root), CODEX_HOME=str(self.home),
                   PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get('PYTHONPATH')))),
                   BUDDY_RUNTIME_ROOT=str(self.runtime),
                   BUDDY_DEV_SOURCE="1", BUDDY_CODEX_CLI=str(FIXTURE),
                   BUDDY_STATE_DIR=str(self.root / "state"),
                   BUDDY_CODEX_FIXTURE_STATE=str(directory / "trace.json"))
        context = ExecutionContext("task", "attempt", 1,
                                   {"provider": "openai", "model": "fixture-model", "effort": "low",
                                    "cwd": str(self.cwd), "timeoutSeconds": 3}, directory, {}, env)
        handle = CodexAdapter().start_no_tool_structured(context,
                  NoToolStructuredRequest(str(self.cwd), "Pick a profile", SCHEMA, 3))
        self.assertEqual(handle.wait(6), 0)
        outcome = collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["toolEvidence"]["binding"],
                         {"adapter": "codex", "taskId": "task", "attemptId": "attempt", "generation": 1})

    def test_native_metadata_notifications_do_not_hide_tools_or_break_the_turn(self):
        process, result, _directory = self.run_case('native-metadata')
        self.assertEqual(process.returncode, 0, result)
        self.assertTrue(result['zeroToolVerified'])

    def test_compatible_cache_from_another_client_version_and_private_instructions(self):
        cache = self.home / 'models_cache.json'
        data = json.loads(cache.read_text())
        data['client_version'] = '0.158.0'
        cache.write_text(json.dumps(data))
        process, result, directory = self.run_case()
        self.assertEqual(process.returncode, 0, result)
        import tomllib
        config = tomllib.loads((directory / 'native/codex-home/config.toml').read_text())
        self.assertEqual(config['project_doc_max_bytes'], 0)
        self.assertFalse(config['skills']['include_instructions'])
        self.assertFalse(config['skills']['bundled']['enabled'])

    def test_every_tool_shape_and_late_tool_fails_closed(self):
        for case in ("typed", "collab", "raw", "raw-no-id", "turn-item", "late", "request"):
            with self.subTest(case=case):
                process, result, _ = self.run_case(case)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(result["code"], "no-tool-violation", result)
                self.assertFalse(result["zeroToolVerified"])

    def test_projected_facts_survive_rejections_and_corrections_add_roots(self):
        for case, tool_name in (("typed", "commandExecution"), ("collab", "collabAgentToolCall"),
                                ("raw", "exec_command"), ("late", "custom_tool_call_output")):
            with self.subTest(case=case):
                process, result, _ = self.run_case(case)
                evidence = result["toolEvidence"]
                self.assertFalse(evidence["streamComplete"])
                self.assertTrue(any(event["toolName"] == tool_name for event in evidence["events"]),
                                (case, evidence["events"]))
                self.assertEqual(evidence["binding"]["taskId"], "router-task")
        process, result, _directory = self.run_case("format")
        self.assertEqual(process.returncode, 0, result)
        evidence = result["toolEvidence"]
        self.assertEqual([identity["turnId"] for identity in evidence["nativeIdentity"]], ["turn-1", "turn-2"])
        self.assertEqual(evidence["events"], [])
        self.assertTrue(evidence["streamComplete"])

    def test_unknown_partial_and_eof_rejected(self):
        for case in ("unknown", "truncated", "eof"):
            with self.subTest(case=case):
                process, result, _ = self.run_case(case)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(result["zeroToolVerified"])

    def test_one_format_correction_but_no_out_of_bounds_retry(self):
        process, result, directory = self.run_case("format")
        self.assertEqual(process.returncode, 0, result)
        self.assertEqual(result["correctionCount"], 1)
        self.assertEqual(len(json.loads((directory / "trace.json").read_text())["turns"]), 2)
        process, result, directory = self.run_case("outside")
        self.assertEqual(process.returncode, 0, result)
        self.assertFalse(result["answerValid"])
        self.assertEqual(len(json.loads((directory / "trace.json").read_text())["turns"]), 1)
        bounded = {"type": "object", "additionalProperties": False, "required": ["reason"],
                   "properties": {"reason": {"type": "string", "maxLength": 2}}}
        self.assertEqual(_format_correction('{"reason":"too long"}', bounded), 'answer-shape')

    def test_policy_and_deadline(self):
        for case in ("config-mismatch", "wrong-model"):
            with self.subTest(case=case):
                process, result, _ = self.run_case(case)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(result["code"], "no-tool-policy-unverified")
        process, result, _ = self.run_case("hang", timeout=1)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["code"], "deadline")
        self.assertFalse(result["zeroToolVerified"])
        self.assertTrue(result["processState"]["shutdownConfirmed"])
        self.assertFalse((self.root / "run-hang/native/codex-home/auth.json").is_symlink())

    def test_missing_or_mismatched_native_metadata_refused(self):
        cache = self.home / "models_cache.json"
        cache.unlink()
        with self.assertRaises(CodexProtocolError) as caught:
            prepare_home(self.root / "private", {"CODEX_HOME": str(self.home)},
                         {"model": "fixture-model", "effort": "low"})
        self.assertEqual(caught.exception.code, "no-tool-policy-unverified")
        process, result, _directory = self.run_case('missing-metadata')
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result['modelStarted'])
        self.assertTrue(result['processState']['shutdownConfirmed'])


if __name__ == "__main__":
    unittest.main()
