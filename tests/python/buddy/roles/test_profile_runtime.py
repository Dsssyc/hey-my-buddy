"""Profile corrections use fresh web sessions with independent model start waits."""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.protocol import tool_evidence


class ProfileRuntimeTests(unittest.TestCase):
    def _control(self, root: Path):
        return {
            "operation": "profile", "harness": "codex", "privateRoot": str(root / "invocation"),
            "nativeRoot": str(root / "native"), "stateDir": str(root),
            "sessionRecordPath": None, "cwd": str(root / "cwd"),
            "timeoutSeconds": None, "taskId": "task", "attemptId": "attempt", "generation": 1,
            "spec": {"provider": "openai", "model": "gpt", "effort": "low"},
            "profileRequest": {"prompt": "Profile this model", "outputSchema": {"type": "object"}},
        }

    def _run(self, control, responses, *, outcomes=None, silent_round=None):
        from hey_my_buddy.buddy.runtime import execution as execution_module
        seen = []
        clock = [0.0]
        self.round_starts = []
        publications = []
        handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
            task_id=control["taskId"], attempt_id=control["attemptId"],
            generation=control["generation"], invocation_id="profile-fixture"), grace_seconds=0)

        wait_path = Path(control["stateDir"]) / "execution-budget.json"
        handle.model_response_wait = execution_module.ModelResponseWait(wait_path,
            {"taskId": "task", "attemptId": "attempt", "generation": 1}, 60)

        @contextmanager
        def live(_request, _services, _handle):
            yield None

        def run(_module, request, *, observer, services, cancelled):
            seen.append(request)
            self.assertEqual(request.permission, "web")
            self.assertIsNone(request.continuation)
            raw, calls = responses[len(seen) - 1]
            self.round_starts.append((wait_path.exists(), handle.model_responded))
            silent = len(seen) == silent_round
            if silent:
                clock[0] += 60
                self.assertTrue(handle.model_response_wait.expired(handle))
                handle.cancelled.set()
            else:
                self.assertEqual(observer({"modelResponded": True, "toolCalls": calls}).action, "continue")
                self.assertFalse(wait_path.exists())
                clock[0] += 10_000
                self.assertFalse(handle.model_response_wait.expired(handle))
            identity = {"sessionId": f"session-{len(seen)}"}
            evidence = tool_evidence.ToolEventEvidence({"harness": "codex", "taskId": "task",
                "attemptId": "attempt", "generation": 1}, state_dir=Path(control["stateDir"]))
            for index in range(calls):
                evidence.observe({"nativeIdentity": identity, "callId": f"call-{index}",
                                  "toolName": "web_search_call", "category": "fetch", "phase": "start", "outcome": "unknown"})
                evidence.observe({"nativeIdentity": identity, "callId": f"call-{index}",
                                  "toolName": "web_search_call", "category": "fetch", "phase": "end",
                                  "outcome": outcomes[len(seen) - 1][index] if outcomes else "unknown"})
            result = rc.RunResult(
                identity=request.identity, harness=request.harness,
                end=rc.RunEnd(status="cancelled" if silent else "ok", native_exit_code=0),
                model_responded=not silent, native_request_sent=True,
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
            with mock.patch.object(execution_module, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                 mock.patch.object(inline_run, "_inline_live", live), \
                 mock.patch.object(inline_run, "run_harness", side_effect=run), \
                 mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", return_value=None), \
                 mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input", return_value=SimpleNamespace(blocked=False)), \
                 mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                handle.model_response_wait.begin()
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
        self.assertEqual([request.budget.timeout_seconds for request in seen], [None, None, None])
        self.assertEqual(self.round_starts, [(True, False)] * 3)
        self.assertIn('bad one', seen[1].input_text)
        self.assertIn('sourceDate', seen[1].input_text)
        self.assertIn('bad two', seen[2].input_text)
        self.assertEqual([item[1] for item in publications], [0, 1, 2])
        self.assertEqual(verdict['toolCalls'], 2)
        self.assertEqual(verdict['correctionCount'], 2)
        self.assertTrue(verdict['profileFeedback']['accepted'])
        self.assertTrue(verdict['modelResponded'])
        self.assertTrue(verdict['nativeRequestSent'])
        self.assertEqual(verdict['tokenUsage']['inputTokens'], 36)
        self.assertEqual(verdict['tokenUsage']['cachedInputTokens'], 6)
        self.assertEqual(verdict['tokenUsage']['outputTokens'], 9)

    def test_correction_rounds_preserve_and_sum_each_call_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            _seen, _publications, verdict, code = self._run(
                self._control(Path(directory), tool_calls=5),
                [('first', 2), ('second', 1), ('final', 1)],
                outcomes=[['success', 'failure'], ['unknown'], ['failure']])
            package = verdict['toolEvidence']
            self.assertEqual(package['version'], 3)
            self.assertEqual(package['toolSummary'], [
                {'toolName': 'web_search_call', 'calls': 4, 'failed': 2, 'unknown': 1}])
            self.assertEqual([row['outcome'] for row in tool_evidence.read_tool_calls(package, state_dir=Path(directory))],
                             ['success', 'failure', 'unknown', 'failure'])
            self.assertIsNone(tool_evidence.judge_tool_evidence(package, 'web', state_dir=Path(directory)))
        self.assertEqual(code, 0)

    def test_rejected_answer_can_start_another_session_after_many_tool_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code = self._run(self._control(Path(directory)),
                [('bad one', 65), ('good', 65)])
        self.assertEqual(len(seen), 2)
        self.assertEqual(code, 0)
        self.assertEqual(verdict['toolCalls'], 130)
        self.assertIsNone(verdict['stopReason'])
        self.assertEqual(self.round_starts, [(True, False), (True, False)])

    def test_a_correction_waits_again_and_can_time_out_after_a_responding_round(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code = self._run(self._control(Path(directory)),
                [('bad one', 1), ('silent', 0)], silent_round=2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(code, 1)
        self.assertEqual(len(publications), 1)
        self.assertEqual(self.round_starts, [(True, False), (True, False)])
