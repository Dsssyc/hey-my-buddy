"""Offline DSH controller tests with the shared no-tool protocol fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.dsh.runner import composed_safe


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}

FIXTURE = Path(__file__).parent / "fixtures" / "mock_dsh.py"


class DshNoToolTests(unittest.TestCase):
    def test_folded_native_config_dump_is_checked(self):
        plugin = Path("/private/adapter.mjs")
        dump = ("- id: headless-runner\n  disabled: true\n"
                "- id: buddy-no-tool-structured\n  name: >-\n"
                "    file:///private/adapter.mjs\n")
        self.assertTrue(composed_safe(dump, plugin))
        self.assertFalse(composed_safe(dump.replace("disabled: true", "disabled: false"), plugin))
        self.assertFalse(composed_safe(dump + "- id: headless-runner\n  disabled: false\n", plugin))
        reordered = ("- name: '@deepseek-ai/dsh-headless'\n  config:\n    task: !!js ctx.startup.task\n"
                     "  disabled: true\n  id: headless-runner\n"
                     "- config:\n    requestFile: /private/request.json\n  id: buddy-no-tool-structured\n"
                     "  name: >-\n    file:///private/adapter.mjs\n")
        self.assertTrue(composed_safe(reordered, plugin))
        self.assertFalse(composed_safe(reordered.replace('  disabled: true', '  disabled: !!js true'), plugin))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-no-tool-dsh-")
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
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment.update(BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1", BUDDY_HARNESS_RECORD_FILE=str(self.record),
                                DSH_HOME=str(self.dsh_home))

    def execute(self, case="ok", *, timeout=3, cancel=False, capture=False):
        self.index += 1
        (self.root / "case").write_text(case)
        context = ExecutionContext("task", f"attempt-{self.index}", self.index,
            {"provider": "deepseek-official", "model": "deepseek-flash", "effort": "max", "cwd": str(self.cwd),
             "timeoutSeconds": timeout}, self.root / f"attempt-{self.index}", {},
            self.environment)
        request = NoToolStructuredRequest(str(self.cwd), "Choose a profile", SCHEMA,
                                          timeout_seconds=timeout, capture_evidence=capture)
        handle = DshAdapter().start_no_tool_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        if cancel:
            time.sleep(0.2)
            handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(timeout + 8))
        outcome = DshAdapter().collect(handle, context)
        if outcome.result.get("code") == "invalid-native-result" and case not in ("truncated", "ok-with-events", "ok-truncated"):
            self.fail(f"controller stderr: {Path(handle.log_paths['stderr']).read_text()}")
        return outcome

    def test_success_and_one_correction(self):
        for case, count in (("ok", 0), ("correct", 1)):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                self.assertTrue(outcome.result["zeroToolVerified"])
                self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
                self.assertEqual(outcome.result["correctionCount"], count)
                self.assertEqual(len(outcome.result["toolEvidence"]["nativeIdentity"]), count + 1)
                control = json.loads((self.root / f"attempt-{self.index}" / 'no-tool-control.json').read_text())
                self.assertTrue((Path(control['directory']) / "dsh-home" /
                                 "profiles" / "headless" / "package.json").is_file())
                evidence = Path(control["evidenceRoot"])
                self.assertTrue((evidence / "call-1/request.json").is_file())
                self.assertTrue((evidence / f"call-{count + 1}/result.json").is_file())
                self.assertFalse((self.dsh_home / "profiles" / "headless" / "cordis.yml").exists())

    def test_enum_is_not_corrected(self):
        outcome = self.execute("enum")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(outcome.result["answerValid"])
        self.assertEqual(outcome.result["correctionCount"], 0)

    def test_capture_contains_bounded_native_facts(self):
        outcome = self.execute(capture=True)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["nativeEvidence"],
                         {"chunkCount": 3, "toolSchemaCount": 0, "profileRunnerDisabled": True})

    def test_unsafe_profile_tool_and_truncated_result_fail(self):
        for case, code in (("unsafe", "no-tool-profile-unsafe"), ("tool", "no-tool-violation"),
                           ("truncated", "invalid-native-result")):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], code)
                self.assertNotIn("zeroToolVerified", outcome.result)
        # The retired violation branch invented a call that no chunk carried.
        outcome = self.execute("tool")
        self.assertNotIn("usage", outcome.result)
        self.assertEqual(outcome.result["toolEvidence"]["events"], [])

    def test_deadline_and_cancel(self):
        late = self.execute("timeout", timeout=1)
        self.assertEqual(late.result["code"], "deadline")
        self.assertTrue(late.shutdown_confirmed)
        cancelled = self.execute("timeout", timeout=10, cancel=True)
        self.assertEqual(cancelled.status, "cancelled", cancelled.to_report())
        self.assertTrue(cancelled.shutdown_confirmed)


if __name__ == "__main__":
    unittest.main()
