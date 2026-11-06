"""Profile corrections use fresh web sessions under one execution budget."""
from __future__ import annotations

import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.protocol import tool_evidence


class ProfileRuntimeTests(unittest.TestCase):
    def _control(self, root: Path, *, tool_calls=3):
        return {
            "operation": "profile", "harness": "codex", "privateRoot": str(root / "invocation"),
            "nativeRoot": str(root / "native"), "stateDir": str(root),
            "sessionRecordPath": None, "cwd": str(root / "cwd"),
            "timeoutSeconds": 120, "taskId": "task", "attemptId": "attempt", "generation": 1,
            "spec": {"provider": "openai", "model": "gpt", "effort": "low"},
            "profileRequest": {"prompt": "Profile this model", "outputSchema": {"type": "object"},
                               "budget": {"preset": "brief", "timeoutSeconds": 120,
                                          "toolCalls": tool_calls}},
        }

    def _run(self, control, responses):
        seen = []
        publications = []
        handle = SimpleNamespace(cancelled=threading.Event(), request=None,
                                 role_run_active_control=None, role_run_active_identity=None,
                                 live_endpoint=None)

        @contextmanager
        def live(_request, _services, _handle):
            yield None

        def run(_module, request, *, observer, services, cancelled):
            seen.append(request)
            self.assertEqual(request.permission, "web")
            self.assertIsNone(request.continuation)
            raw, calls = responses[len(seen) - 1]
            identity = {"sessionId": f"session-{len(seen)}"}
            evidence = tool_evidence.ToolEventEvidence({"harness": "codex", "taskId": "task",
                "attemptId": "attempt", "generation": 1}, state_dir=Path(control["stateDir"]))
            for index in range(calls):
                evidence.observe({"nativeIdentity": identity, "callId": f"call-{index}",
                                  "toolName": "web_search_call", "category": "fetch", "phase": "start"})
                evidence.observe({"nativeIdentity": identity, "callId": f"call-{index}",
                                  "toolName": "web_search_call", "category": "fetch", "phase": "end"})
            result = rc.RunResult(
                identity=request.identity, harness=request.harness,
                end=rc.RunEnd(status="ok", native_exit_code=0),
                model_started=True, native_request_sent=True,
                buddy=rc.ResultBuddy(requested=request.buddy),
                native_identity=rc.NativeIdentity(**{"session_id": identity["sessionId"]}),
                value=rc.RunValue(schema_status="invalid", raw=raw),
                usage=rc.FrozenJson({"version": 1, "inputTokens": 10, "cachedInputTokens": 2,
                                    "outputTokens": 3, "inputBasis": "excludes-cached",
                                    "scope": "attempt", "source": "fixture", "completeness": "complete", "nativeRecords": 1}),
                tool_evidence=rc.FrozenJson(evidence.finish([identity], True)),
                stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
            return result

        def publish(raw, round_index, observations):
            publications.append((raw, round_index, observations))
            return {"accepted": round_index == len(responses) - 1,
                    "errors": [] if round_index == len(responses) - 1 else ["claim 1 needs a sourceDate"]}

        token = run_execution._profile_publisher.set(publish)
        try:
            with mock.patch.object(inline_run, "_inline_live", live), \
                 mock.patch.object(inline_run, "run_harness", side_effect=run), \
                 mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", return_value=None), \
                 mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input", return_value=SimpleNamespace(blocked=False)), \
                 mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                result, request, verdict, code = inline_run._profile_execute(
                    control, handle, SimpleNamespace(prepare_run_services=None),
                                                                               inline_run.time.monotonic())
        finally:
            run_execution._profile_publisher.reset(token)
        return seen, publications, verdict, code

    def test_two_corrections_start_distinct_native_sessions_and_aggregate_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code = self._run(self._control(Path(directory)),
                [('bad one', 1), ('bad two', 1), ('good', 0)])
            self.assertIsNone(tool_evidence.judge_tool_evidence(
                verdict['toolEvidence'], 'web', state_dir=Path(directory)))
            self.assertEqual(len(verdict['nativeIdentity']), 3)
        self.assertEqual(code, 0)
        self.assertEqual(len({request.identity.invocation_id for request in seen}), 3)
        self.assertEqual([request.budget.timeout_seconds for request in seen][0], 120)
        self.assertIn('bad one', seen[1].input_text)
        self.assertIn('sourceDate', seen[1].input_text)
        self.assertIn('bad two', seen[2].input_text)
        self.assertEqual([item[1] for item in publications], [0, 1, 2])
        self.assertEqual(verdict['toolCalls'], 2)
        self.assertEqual(verdict['correctionCount'], 2)
        self.assertTrue(verdict['profileFeedback']['accepted'])
        self.assertTrue(verdict['modelStarted'])
        self.assertTrue(verdict['nativeRequestSent'])
        self.assertEqual(verdict['tokenUsage']['inputTokens'], 36)
        self.assertEqual(verdict['tokenUsage']['cachedInputTokens'], 6)
        self.assertEqual(verdict['tokenUsage']['outputTokens'], 9)

    def test_rejected_answer_cannot_start_another_session_after_tool_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code = self._run(self._control(Path(directory), tool_calls=1),
                [('bad one', 1), ('unused', 0)])
        self.assertEqual(len(seen), 1)
        self.assertEqual(code, 1)
        self.assertEqual(verdict['toolCalls'], 1)
        self.assertEqual(verdict['stopReason'], 'profile-budget-exhausted')
