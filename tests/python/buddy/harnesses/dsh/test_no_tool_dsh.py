"""The registered fast seam over the harness's fake ACP agent: no model, no network.

The direct-LLM no-tool controller and its Node plugin are deleted; fast calls run
through the shared role seam (:mod:`hey_my_buddy.buddy.roles.run_execution`) over
the registered native run, so these cases drive the real role controller
subprocess, the real launch wrapper and the real correction rules, with the fake
ACP agent selected through the service's own harness record. The old
``composed_safe`` dump check retired with its carrier: the none scope is
satisfied by the launch patch itself, which the native-run tests pin row by row.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.registry import adapter
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request, decode_run_result, encode_run_result
from hey_my_buddy.buddy.roles.controller import FastPreparation, start_router_preparation
from hey_my_buddy.buddy.roles import structured_call

from buddy.harnesses.dsh.acp.support import FAKE_AGENT

SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}

#: The fake ACP agent's declared configuration options; the run checks the
#: request against the agent's own declared membership and its readback.
SPEC = {"provider": "fake", "model": "m1", "effort": "high"}


class DshNoToolTests(unittest.TestCase):
    """One private root with the fake ACP agent selected through the record.

    The shared adapter-invariants suite consumes this fixture's ``root``, ``cwd``
    and ``environment``; the ``case`` file of the retired direct-LLM mock has no
    reader here, and scenarios select their answers through the record instead.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-no-tool-dsh-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir(mode=0o700)
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.log = self.root / "logs" / "fake-agent.log"
        self.record = self.root / "harness-record.json"
        self.select('{"choice":"a"}')
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                            and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[5] / "src") + os.pathsep + \
            str(Path(__file__).parents[4] / "tests" / "python") + os.pathsep + os.environ.get("PYTHONPATH", "")
        # A run inherits HOME by contract; the role-controller subprocess chain
        # must carry this test's private one, like the in-process cases pin it,
        # so the agent's forced-private-home tripwire holds off the invoking shell.
        (self.root / "home").mkdir(mode=0o700)
        self.environment.update(HOME=str(self.root / "home"),
                                BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1", BUDDY_HARNESS_RECORD_FILE=str(self.record))

    def select(self, *answers: str) -> None:
        """Point the harness record at the fake agent with these final answers."""
        command = [sys.executable, str(FAKE_AGENT), "--log", str(self.log), "--prompt-mode", "final"]
        command += [name for answer in answers for name in ("--final-answer", answer)]
        self.record.write_text(json.dumps({"dsh": {"status": "ready", "command": command}}))


class FastRegisteredSeamTests(DshNoToolTests):
    """Fast calls through the role seam: public frames in, one receipt out."""

    def invoke(self, *, capture=False, timeout=3):
        context = ExecutionContext("task", "attempt-1", 1, {**SPEC, "cwd": str(self.cwd),
                                  "timeoutSeconds": timeout}, self.root / "attempt-1", {},
            self.environment)
        request = NoToolStructuredRequest(str(self.cwd), "Choose a profile", SCHEMA,
                                          timeout_seconds=timeout, capture_evidence=capture)
        handle = start_router_preparation(
            FastPreparation("dsh",  request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(timeout + 10))
        return context, handle, structured_call.collect(handle)

    def test_a_settled_fast_call_reports_the_public_receipt(self):
        context, handle, outcome = self.invoke()
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
        self.assertEqual(outcome.result["correctionCount"], 0)
        self.assertEqual(outcome.result["rawAnswer"], '{"choice":"a"}')
        # The registered seam's control files replace the retired no-tool control:
        # the request and verdict live in the run's own invocation root.
        request = decode_run_request(Path(handle.role_run_control["requestFile"]).read_bytes())
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        self.assertEqual(result.identity, request.identity)
        self.assertEqual(request.tool_scope, "none")
        self.assertEqual(request.session_services, ())
        self.assertFalse((context.directory / "no-tool-control.json").exists())
        self.assertEqual(handle.process.args[2], "hey_my_buddy.buddy.roles.run_controller")

    def test_capture_reports_the_launch_scope_as_native_evidence(self):
        _, _, outcome = self.invoke(capture=True)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        evidence = outcome.result["nativeEvidence"]
        self.assertEqual(evidence["disabledRows"][0], "tool-bash")
        self.assertIn("plan-mode", evidence["disabledRows"])
        self.assertTrue(evidence["streamEof"])
        self.assertGreaterEqual(evidence["eventCount"], 1)

    def test_one_correction_runs_a_second_session_on_the_same_process(self):
        self.select("not json", '{"choice":"a"}')
        _, _, outcome = self.invoke()
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["correctionCount"], 1)
        self.assertEqual(outcome.result["rawAnswer"], '{"choice":"a"}')

    def test_an_enum_violation_is_answered_not_corrected(self):
        self.select('{"choice":"b"}')
        _, _, outcome = self.invoke()
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(outcome.result["answerValid"])
        self.assertEqual(outcome.result["correctionCount"], 0)

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


if __name__ == "__main__":
    unittest.main()
