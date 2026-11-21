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
            "taskId": "task", "attemptId": "attempt", "generation": 1,
            "spec": {"provider": "openai", "model": "gpt", "effort": "low"},
            "profileRequest": {"prompt": "Profile this model", "outputSchema": {"type": "object"}},
        }

    def _run(self, control, responses, *, outcomes=None, silent_round=None,
             stop_preparing_round=None, stop_publish_round=None, expire_first_wait=False,
             handle=None):
        from hey_my_buddy.buddy.runtime import execution as execution_module
        seen = []
        clock = [0.0]
        self.round_starts = []
        publications = []
        preparations = []
        if handle is None:
            handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
                task_id=control["taskId"], attempt_id=control["attemptId"],
                generation=control["generation"], invocation_id="profile-fixture"))

        wait_path = Path(control["stateDir"]) / "execution-budget.json"
        handle.model_response_wait = execution_module.ModelResponseWait(wait_path,
            {"taskId": "task", "attemptId": "attempt", "generation": 1}, 60)

        @contextmanager
        def live(_request, _services, _handle, **_kwargs):
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
            if stop_publish_round is not None and round_index == stop_publish_round:
                # The stop lands between the rounds, before the next one is
                # even prepared; it must end the attempt without a rearm.
                handle.cancelled.set()
            return {"accepted": round_index == len(responses) - 1,
                    "errors": [] if round_index == len(responses) - 1 else ["claim 1 needs a sourceDate"]}

        def selected(_harness, _environment):
            # A stop can arrive while a round is being prepared; landing it
            # there reproduces the pre-launch window before the native call.
            preparations.append(None)
            if stop_preparing_round is not None and len(preparations) == stop_preparing_round:
                handle.cancelled.set()
            return None

        token = run_execution._profile_publisher.set(publish)
        try:
            with mock.patch.object(execution_module, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                 mock.patch.object(inline_run, "_inline_live", live), \
                 mock.patch.object(inline_run, "run_harness", side_effect=run), \
                 mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", side_effect=selected), \
                 mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input", return_value=SimpleNamespace(blocked=False)), \
                 mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                handle.model_response_wait.begin()
                if expire_first_wait:
                    clock[0] += 61
                result, request, verdict, code = inline_run._profile_execute(
                    control, handle, SimpleNamespace(prepare_run_services=None),
                                                                               inline_run.time.monotonic())
        finally:
            run_execution._profile_publisher.reset(token)
        return seen, publications, verdict, code, result

    def test_two_corrections_start_distinct_native_sessions_and_aggregate_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code, _result = self._run(self._control(Path(directory)),
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
            _seen, _publications, verdict, code, _result = self._run(
                self._control(Path(directory)),
                [('first', 2), ('second', 1), ('final', 1)],
                outcomes=[['success', 'failure'], ['unknown'], ['failure']])
            package = verdict['toolEvidence']
            self.assertEqual(package['version'], 4)
            self.assertEqual(package['sessionServiceTools'], [])
            self.assertEqual(package['toolSummary'], [
                {'toolName': 'web_search_call', 'calls': 4, 'failed': 2, 'unknown': 1}])
            calls = list(tool_evidence.read_tool_calls(package, state_dir=Path(directory)))
            self.assertEqual([row['outcome'] for row in calls], ['success', 'failure', 'unknown', 'failure'])
            self.assertTrue(all(row['sessionServiceVerified'] is False for row in calls))
            self.assertIsNone(tool_evidence.judge_tool_evidence(package, 'web', state_dir=Path(directory)))
        self.assertEqual(code, 0)

    def test_rejected_answer_can_start_another_session_after_many_tool_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code, _result = self._run(self._control(Path(directory)),
                [('bad one', 65), ('good', 65)])
        self.assertEqual(len(seen), 2)
        self.assertEqual(code, 0)
        self.assertEqual(verdict['toolCalls'], 130)
        self.assertNotIn('stopReason', verdict)
        self.assertEqual(self.round_starts, [(True, False), (True, False)])

    def test_a_correction_waits_again_and_can_time_out_after_a_responding_round(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code, _result = self._run(self._control(Path(directory)),
                [('bad one', 1), ('silent', 0)], silent_round=2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(code, 1)
        self.assertEqual(len(publications), 1)
        self.assertEqual(self.round_starts, [(True, False), (True, False)])

    def test_a_first_round_stopped_or_timed_out_during_preparation_never_enters_the_harness(self):
        for stop, expire in ((True, False), (False, True)):
            with self.subTest(stopped=stop, expired=expire), tempfile.TemporaryDirectory() as directory:
                seen, publications, verdict, code, result = self._run(
                    self._control(Path(directory)), [('bad one', 1)],
                    stop_preparing_round=1 if stop else None, expire_first_wait=expire)
                self.assertEqual(seen, [], "a native session was started anyway")
                self.assertEqual(publications, [])
                self.assertEqual(code, 1)
                # The stopped attempt itself is the result: no native session
                # was ever entered, so nothing may claim one was.
                self.assertEqual(result.end.status, "cancelled")
                self.assertIs(result.native_request_sent, False)
                self.assertIs(result.model_responded, False)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                self.assertEqual(verdict["correctionCount"], 0)
                self.assertIs(verdict["modelResponded"], False)
                self.assertIs(verdict["nativeRequestSent"], False)
                self.assertIsNone(verdict["profileFeedback"])

    def test_a_correction_round_that_never_launches_counts_no_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, publications, verdict, code, _result = self._run(
                self._control(Path(directory)), [('bad one', 1), ('good', 0)], stop_preparing_round=2)
        # The first round answered and was rejected, but the stop landed while
        # the correction round was only being prepared: it never entered a
        # native session, so it counts no correction — and the aggregated
        # model fact of the attempt keeps the first round's response.
        self.assertEqual(len(seen), 1)
        self.assertEqual([item[1] for item in publications], [0])
        self.assertEqual(verdict["correctionCount"], 0)
        self.assertIs(verdict["modelResponded"], True)
        self.assertIs(verdict["nativeRequestSent"], True)
        self.assertEqual(self.round_starts, [(True, False)])

    def test_a_stop_between_rounds_ends_the_attempt_before_its_next_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = self._control(root)
            wait_path = root / "execution-budget.json"
            handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
                task_id=control["taskId"], attempt_id=control["attemptId"],
                generation=control["generation"], invocation_id="profile-fixture"))
            seen, publications, verdict, code, _result = self._run(
                control, [('bad one', 1), ('good', 0)], stop_publish_round=0, handle=handle)
        # The stop landed between the rounds: the correction round is neither
        # prepared nor re-armed, so the first round's model fact and cleared
        # wait file survive untouched beside its rejected answer.
        self.assertEqual(len(seen), 1)
        self.assertEqual([item[1] for item in publications], [0])
        self.assertEqual(verdict["correctionCount"], 0)
        self.assertIs(verdict["modelResponded"], True)
        self.assertIs(handle.model_responded, True)
        self.assertFalse(wait_path.exists(), "the stopped round re-armed its start wait")

    def test_correction_preparation_time_counts_against_its_response_budget(self):
        from hey_my_buddy.buddy.runtime import execution as execution_module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = self._control(root)
            clock = [0.0]
            seen = []
            preparations = 0
            handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
                task_id=control["taskId"], attempt_id=control["attemptId"],
                generation=control["generation"], invocation_id="profile-fixture"))
            handle.model_response_wait = execution_module.ModelResponseWait(
                root / "execution-budget.json",
                {"taskId": "task", "attemptId": "attempt", "generation": 1}, 1)

            def slow_selected(_harness, _environment):
                nonlocal preparations
                preparations += 1
                if preparations == 2:
                    # 2 seconds spent preparing a correction round exceeds its
                    # 1-second response budget and must prevent launching it.
                    clock[0] += 2.0
                return None

            def run(_module, request, *, observer, services, cancelled):
                seen.append(request)
                self.assertEqual(observer({"modelResponded": True, "toolCalls": 0}).action, "continue")
                return rc.RunResult(identity=request.identity, harness=request.harness,
                    end=rc.RunEnd(status="ok", native_exit_code=0), model_responded=True,
                    native_request_sent=True, buddy=rc.ResultBuddy(requested=request.buddy),
                    value=rc.RunValue(schema_status="invalid", raw="bad one"),
                    stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))

            def publish(_raw, _round_index, _observations):
                return {"accepted": False, "errors": ["claim 1 needs a sourceDate"]}

            @contextmanager
            def live(_request, _services, _handle, **_kwargs):
                yield None

            token = run_execution._profile_publisher.set(publish)
            try:
                with mock.patch.object(execution_module, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                     mock.patch.object(inline_run, "_inline_live", live), \
                     mock.patch.object(inline_run, "run_harness", side_effect=run), \
                     mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", side_effect=slow_selected), \
                     mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input",
                                return_value=SimpleNamespace(blocked=False)), \
                     mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                    handle.model_response_wait.begin()
                    _result, _request, verdict, code = inline_run._profile_execute(
                        control, handle, SimpleNamespace(prepare_run_services=None), 0.0)
            finally:
                run_execution._profile_publisher.reset(token)
        self.assertEqual(len(seen), 1, "correction round launched despite exhausting its budget during preparation")
        self.assertEqual(code, 1)
        self.assertTrue(handle.model_response_wait.timed_out)
        self.assertEqual(verdict["correctionCount"], 0)
        self.assertIs(verdict["modelResponded"], True)

    def test_an_accepted_stop_before_control_attach_prevents_the_first_native_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = self._control(root)
            stop_view = [None]
            handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
                task_id=control["taskId"], attempt_id=control["attemptId"],
                generation=control["generation"], invocation_id="profile-fixture"))
            handle.accepted_stop = lambda: stop_view[0]
            seen = []

            def selected(_harness, _environment):
                # Emulate a console stop arriving on Execution before
                # control.attach(handle) has bound handle.cancelled.
                stop_view[0] = {"stopInitiator": "console"}
                return None

            def run(_module, request, **_kwargs):
                seen.append(request)
                raise AssertionError("native harness must not be entered after an accepted stop")

            @contextmanager
            def live(_request, _services, _handle, **_kwargs):
                yield None

            with mock.patch.object(inline_run, "_inline_live", live), \
                 mock.patch.object(inline_run, "run_harness", side_effect=run), \
                 mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", side_effect=selected), \
                 mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input",
                            return_value=SimpleNamespace(blocked=False)), \
                 mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                result, _request, verdict, code = inline_run._profile_execute(
                    control, handle, SimpleNamespace(prepare_run_services=None), 0.0)
        self.assertEqual(seen, [])
        self.assertEqual(code, 1)
        self.assertEqual(result.end.status, "cancelled")
        self.assertIs(handle.cancel_requested, True)
        self.assertEqual(handle.stop_initiator, "console")
        self.assertEqual(verdict["correctionCount"], 0)
        self.assertIs(verdict["nativeRequestSent"], False)

    def test_correction_rounds_continue_the_attempt_activity_sequence(self):
        from dataclasses import dataclass
        from datetime import datetime, timezone
        from hey_my_buddy.protocol.activity import is_newer

        @dataclass(frozen=True)
        class _Services:
            live: object = None
            inquiry: object = None

        def now():
            return datetime.now(timezone.utc).isoformat()

        for mode in ("live-and-result", "result-only"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                control = self._control(Path(directory))
                responses = [("bad one", 1, 30, "session-1"), ("good", 0, 3, "session-2")]
                seen, initial_live, observed_live, round_results = [], [], [], []
                handle = run_execution.InlineRunHandle(control, rc.RunIdentity(
                    task_id=control["taskId"], attempt_id=control["attemptId"],
                    generation=control["generation"], invocation_id="profile-fixture"))

                def run(_module, request, *, observer, services, cancelled):
                    seen.append(request)
                    # A fresh round's endpoint is never pre-seeded with the
                    # previous round's session receipt; the harness emits its
                    # own local sequence (e.g. Claude/DSH starting back at 3)
                    # and the endpoint/role offsets it strictly above round 1.
                    initial_live.append(services.live.current_activity())
                    raw, calls, local_seq, session_id = responses[len(seen) - 1]
                    self.assertEqual(observer({"modelResponded": True, "toolCalls": calls}).action, "continue")
                    if mode == "live-and-result":
                        payload = {"phase": "streaming-model", "observedAt": now(),
                                   "eventSeq": local_seq, "nativeSessionId": session_id,
                                   "counts": {"modelTurns": 1, "toolCalls": calls}}
                        self.assertTrue(services.live.publish_activity(payload))
                        observed_live.append(services.live.current_activity())
                    result_obj = rc.RunResult(identity=request.identity, harness=request.harness,
                        end=rc.RunEnd(status="ok", native_exit_code=0), model_responded=True,
                        native_request_sent=True, buddy=rc.ResultBuddy(requested=request.buddy),
                        value=rc.RunValue(schema_status="invalid", raw=raw),
                        activity=rc.FrozenJson({"phase": "streaming-model", "observedAt": now(),
                                                "eventSeq": local_seq, "nativeSessionId": session_id,
                                                "counts": {"modelTurns": 1, "toolCalls": calls}}),
                        stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
                    round_results.append(result_obj)
                    return result_obj

                def publish(raw, round_index, observations):
                    return {"accepted": round_index == len(responses) - 1,
                            "errors": [] if round_index == len(responses) - 1 else ["claim 1 needs a sourceDate"]}

                token = run_execution._profile_publisher.set(publish)
                try:
                    with mock.patch.object(inline_run, "run_harness", side_effect=run), \
                         mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", return_value=None), \
                         mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input",
                                    return_value=SimpleNamespace(blocked=False)), \
                         mock.patch("hey_my_buddy.protocol.capabilities.select_input_limit", return_value=None):
                        result, request, verdict, code = inline_run._profile_execute(
                            control, handle, SimpleNamespace(prepare_run_services=lambda *, inquiry=None: _Services()),
                            inline_run.time.monotonic())
                finally:
                    run_execution._profile_publisher.reset(token)
                self.assertEqual(len(seen), 2)
                self.assertEqual(code, 0)
                self.assertEqual(initial_live, [None, None])
                first_activity = round_results[0].activity.value
                # Round 2 had local eventSeq 3 after round 1's 30: offset by
                # 31 gives 34, strictly above 30 for both live and final facts.
                self.assertEqual(result.activity.value["eventSeq"], 34)
                self.assertEqual(result.activity.value["nativeSessionId"], "session-2")
                self.assertTrue(is_newer(result.activity.value, first_activity))
                if mode == "live-and-result":
                    self.assertEqual(observed_live[0]["eventSeq"], 30)
                    self.assertEqual(observed_live[1]["eventSeq"], 34)
                    self.assertEqual(observed_live[1]["nativeSessionId"], "session-2")
                    self.assertTrue(is_newer(observed_live[1], observed_live[0]))
