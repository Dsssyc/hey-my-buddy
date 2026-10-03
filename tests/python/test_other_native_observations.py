"""ADR-018 items 22/23 for the Claude Code and ZCode adapters.

The desensitized native records in ``native-usage-claude.json`` and
``native-usage-zcode.json`` drive both protocol-level replay and the two private
harness fixtures (``fake_claude_usage.py`` and ``mock_zcode_usage.py``). Every
test runs against private state/runtime roots; no model, network, installation or
daily-board state is touched.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from hey_my_buddy.protocol import usage
from hey_my_buddy.buddy.harnesses.claude import adapter as claude_module
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ProcessHandle
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.claude.protocol import QuotaRejected, TurnEvidence
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    NativeError,
    ZcodeAttemptUsage,
    optional_native_call,
    quota_native_code,
)

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_NATIVE = json.loads((FIXTURES / "native-usage-claude.json").read_text())
ZCODE_NATIVE = json.loads((FIXTURES / "native-usage-zcode.json").read_text())
FAKE_CLAUDE_USAGE = FIXTURES / "fake_claude_usage.py"
MOCK_ZCODE_USAGE = FIXTURES / "mock_zcode_usage.py"

CLAUDE_SESSION = CLAUDE_NATIVE["sessionId"]
CLAUDE_MODEL = CLAUDE_NATIVE["modelId"]
ZCODE_SESSION = ZCODE_NATIVE["sessionId"]
ZCODE_TURN = ZCODE_NATIVE["turnId"]


def clean_environment(*, drop_prefixes=("BUDDY_", "ANTHROPIC_", "CLAUDE_")):
    return {key: value for key, value in os.environ.items()
            if not key.startswith(drop_prefixes)
            and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}


class ScriptedConnection:
    """A duck-typed native connection returning one scripted answer per call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.deadline = time.monotonic() + 600
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        if not self.answers:
            raise NativeError("native-rpc-error", "no scripted answer")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ClaudeNativeRecordTests(unittest.TestCase):
    """The Claude protocol layer turns the sanitized native frames into raw facts."""

    cwd = "/tmp/buddy-fixture-checkout"

    def evidence(self) -> TurnEvidence:
        evidence = TurnEvidence(CLAUDE_SESSION, self.cwd)
        evidence.observe({"type": "system", "subtype": "init", "session_id": CLAUDE_SESSION,
                          "cwd": self.cwd, "model": CLAUDE_MODEL})
        return evidence

    def feed(self, evidence: TurnEvidence, frames=None) -> None:
        for frame in frames if frames is not None else CLAUDE_NATIVE["assistantFrames"]:
            evidence.observe(dict(frame))

    def test_assistant_usage_deduplicates_and_rejects_foreign_and_subagent_frames(self):
        evidence = self.evidence()
        self.feed(evidence)
        expected = CLAUDE_NATIVE["expected"]
        token_usage = usage.normalize_token_usage(evidence.token_usage())
        self.assertEqual(token_usage["source"], expected["assistantSource"])
        self.assertEqual(token_usage["scope"], "attempt")
        self.assertEqual(token_usage["inputBasis"], "includes-cached")
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "partial")
        # No counter the native records never reported is invented.
        self.assertNotIn("reasoningOutputTokens", token_usage)

    def test_a_foreign_or_subagent_frame_alone_contributes_nothing(self):
        evidence = self.evidence()
        frames = [frame for frame in CLAUDE_NATIVE["assistantFrames"]
                  if frame["message"]["id"] in ("msg-fixture-foreign", "msg-fixture-child")]
        self.feed(evidence, frames)
        self.assertIsNone(evidence.token_usage())
        self.assertIsNone(evidence.last_assistant_message())

    def test_the_result_summary_is_preferred_and_marks_reasoning(self):
        evidence = self.evidence()
        self.feed(evidence)
        result = {"type": "result", "subtype": "success", "is_error": False, "session_id": CLAUDE_SESSION,
                  "usage": CLAUDE_NATIVE["resultUsage"]}
        evidence.observe(result)
        expected = CLAUDE_NATIVE["expected"]
        token_usage = usage.normalize_token_usage(evidence.token_usage())
        self.assertEqual(token_usage["source"], expected["resultSource"])
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["nativeRecords"], 1)
        self.assertEqual(token_usage["completeness"], "complete")
        message = usage.normalize_last_assistant_message(
            evidence.last_assistant_message(), source="claude/stream-json-root-assistant-message")
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["sourceId"], "msg-fixture-0002")
        self.assertEqual(message["source"], "claude/stream-json-root-assistant-message")
        self.assertNotIn("reasoning", message["text"])
        self.assertNotIn("tool_use", json.dumps(message))

    def test_unknown_cache_counters_never_become_an_input_total(self):
        evidence = self.evidence()
        evidence.observe({"type": "assistant", "session_id": CLAUDE_SESSION, "message": {
            "id": "msg-null-cache", "role": "assistant", "content": [{"type": "text", "text": "bounded"}],
            "usage": {"input_tokens": 700, "output_tokens": 30,
                      "cache_read_input_tokens": None, "cache_creation_input_tokens": None}}})
        token_usage = usage.normalize_token_usage(evidence.token_usage())
        self.assertIsNone(token_usage["inputTokens"])
        self.assertIsNone(token_usage["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], 30)
        self.assertEqual(token_usage["completeness"], "partial")

    def test_native_rate_limit_fractions_become_percent_windows(self):
        evidence = self.evidence()
        evidence.observe({"type": "rate_limit_event", "rate_limit_info": CLAUDE_NATIVE["rateLimitWarning"]["rate_limit_info"]})
        # The pre-existing bounded observation shape stays exactly as it was.
        self.assertEqual(evidence.rate_limits["five_hour"],
                         {"status": "allowed_warning", "resetsAt": 1790467200, "utilization": 0.42})
        quota = usage.normalize_quota(evidence.quota_candidate())
        expected = CLAUDE_NATIVE["expectedQuota"]
        self.assertEqual(quota["source"], expected["source"])
        self.assertEqual(quota["scope"]["provider"], expected["provider"])
        self.assertEqual([{key: window[key] for key in ("name", "usedPercent", "resetsAt")} for window in quota["windows"]],
                         expected["windows"])

    def test_a_rejection_keeps_its_reached_window_without_inventing_a_percentage(self):
        evidence = self.evidence()
        with self.assertRaises(QuotaRejected) as caught:
            evidence.observe({"type": "rate_limit_event",
                              "rate_limit_info": CLAUDE_NATIVE["rateLimitRejected"]["rate_limit_info"]})
        self.assertEqual(caught.exception.rate_limit_type, "five_hour")
        self.assertEqual(caught.exception.resets_at, 1790467200)
        quota = usage.normalize_quota(evidence.quota_candidate())
        expected = CLAUDE_NATIVE["expectedRejectedQuota"]
        self.assertEqual(quota["reachedType"], expected["reachedType"])
        self.assertEqual([{key: window[key] for key in ("name", "usedPercent", "resetsAt")} for window in quota["windows"]],
                         expected["windows"])

    def test_a_window_beyond_its_cap_is_not_clamped(self):
        evidence = self.evidence()
        with self.assertRaises(QuotaRejected):
            evidence.observe({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "utilization": 1.5}})
        quota = usage.normalize_quota(evidence.quota_candidate())
        self.assertEqual(quota.get("windows"), [])
        self.assertEqual(quota["reachedType"], "five_hour")


class ClaudeAdapterObservationTests(unittest.TestCase):
    """The whole Claude controller path against the private stream-json fixture."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-claude-native-usage-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        FAKE_CLAUDE_USAGE.chmod(0o755)
        self.state = self.root / "fixture-state.json"
        self.environment = clean_environment()
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CLAUDE_CLI=str(FAKE_CLAUDE_USAGE),
                                BUDDY_CLAUDE_SETTINGS_POLICY="isolated",
                                BUDDY_CLAUDE_FIXTURE_STATE=str(self.state),
                                BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_DEV_SOURCE="1")
        claude_module._reset_metadata_cache()
        self.addCleanup(claude_module._reset_metadata_cache)
        self.adapter = ClaudeAdapter()

    def context(self, case: str, *, index: int = 1, mode: str = "initial", previous=None,
                timeout: int = 10) -> ExecutionContext:
        turn_input = {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": mode, "previousSessionId": previous,
                      "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "Write a fixture result",
                                      "timeoutSeconds": timeout, "provider": "anthropic",
                                      "model": CLAUDE_MODEL, "effort": "low"},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, case: str, **kwargs):
        context = self.context(case, **kwargs)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20), "Claude fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def test_a_completed_turn_publishes_the_execution_summary_and_quota(self):
        outcome = self.execute("usage-ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        expected = CLAUDE_NATIVE["expected"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["source"], expected["resultSource"])
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["scope"], "attempt")
        self.assertEqual(token_usage["completeness"], "complete")
        quota = outcome.result["quota"]
        self.assertEqual([window["name"] for window in quota["windows"]], ["five_hour", "seven_day"])
        self.assertEqual(quota["windows"][0]["usedPercent"], 42)
        self.assertEqual(quota["windows"][1]["usedPercent"], 90)
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["source"], "claude/stream-json-root-assistant-message")
        self.assertFalse(message["truncated"])
        # The pinned Claude-native quotaFailure shape is untouched by this change.
        self.assertNotIn("quotaFailure", outcome.result)

    def test_a_missing_result_usage_falls_back_to_the_bound_assistant_records(self):
        outcome = self.execute("usage-partial")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        expected = CLAUDE_NATIVE["expected"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["source"], expected["assistantSource"])
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "partial")
        # The rolling quota observation is unaffected by the partial usage fallback.
        self.assertEqual(outcome.result["quota"]["source"], CLAUDE_NATIVE["expectedQuota"]["source"])
        self.assertEqual(outcome.result["quota"]["windows"][0]["usedPercent"], 42)

    def test_a_quota_rejection_retains_usage_quota_and_the_last_root_message(self):
        outcome = self.execute("usage-quota")
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "quota-rejected")
        # The retained observation is the one native assistant message before the wall.
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["source"], CLAUDE_NATIVE["expected"]["assistantSource"])
        self.assertEqual(token_usage["inputTokens"], 45000)
        self.assertEqual(token_usage["cachedInputTokens"], 33000)
        self.assertEqual(token_usage["outputTokens"], 200)
        self.assertEqual(token_usage["nativeRecords"], 1)
        self.assertEqual(token_usage["completeness"], "partial")
        quota = outcome.result["quota"]
        self.assertEqual(quota["reachedType"], "five_hour")
        self.assertEqual([window["usedPercent"] for window in quota["windows"]], [50])
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], "Read the fixture file first.")
        self.assertEqual(outcome.result["quotaFailure"],
                         {"rateLimitType": "five_hour", "resetsAt": 1790467200})
        self.assertNotIn("turn", outcome.result)

    def test_no_native_usage_reports_unknown_not_zero(self):
        outcome = self.execute("usage-none")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertIsNone(outcome.result["quota"])
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], "No native usage was reported.")

    def test_an_oversized_assistant_text_is_bounded_with_its_full_provenance(self):
        outcome = self.execute("usage-long-text")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        recorded = json.loads(self.state.read_text())
        message = outcome.result["lastAssistantMessage"]
        self.assertTrue(message["truncated"])
        self.assertEqual(message["sourceBytes"], recorded["longTextBytes"])
        self.assertEqual(message["sha256"], recorded["longTextSha256"])
        self.assertLessEqual(len(message["text"].encode()), usage.MAX_ASSISTANT_MESSAGE_BYTES)
        self.assertEqual(len(json.dumps(message["text"], ensure_ascii=False).encode()), usage.MAX_ASSISTANT_MESSAGE_BYTES)


class ZcodeNativeRecordTests(unittest.TestCase):
    """The ZCode protocol layer binds usage to the admitted root turn only."""

    def usage(self, *, resumed=False) -> ZcodeAttemptUsage:
        return ZcodeAttemptUsage(ZCODE_SESSION, resumed=resumed)

    def delta(self, payload: dict) -> dict:
        return {"method": "v4/telemetry/event", "params": dict(payload)}

    def test_new_root_messages_are_bound_by_the_pre_model_cursor(self):
        attempt = self.usage(resumed=True)
        connection = ScriptedConnection([ZCODE_NATIVE["baselineMessages"], ZCODE_NATIVE["finalMessages"]])
        attempt.capture_baseline(connection)
        attempt.capture_final(connection)
        self.assertEqual(connection.calls[1][1], {"sessionId": ZCODE_SESSION, "limit": 64,
                                                  "afterMessageId": "old-assistant-0001"})
        expected = ZCODE_NATIVE["expectedMessages"]
        token_usage = usage.normalize_token_usage(attempt.raw_usage())
        self.assertEqual(token_usage["source"], expected["source"])
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "complete")
        message = usage.normalize_last_assistant_message(attempt.last_assistant_message,
                                                         source="zcode/session-root-assistant-message")
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["sourceId"], "new-assistant-0002")

    def test_a_replayed_delta_is_deduplicated_and_foreign_events_are_ignored(self):
        attempt = self.usage()
        for payload in ZCODE_NATIVE["usageDeltas"]:
            attempt.observe(self.delta(payload), ZCODE_TURN)
        # A delta before any root turn started can prove nothing either.
        attempt.observe(self.delta(ZCODE_NATIVE["usageDeltas"][0]), None)
        # A record missing a required native counter or an unsupported version is
        # dropped, never completed with zeros.
        malformed = dict(ZCODE_NATIVE["usageDeltas"][1])
        malformed["eventId"] = "evt-fixture-malformed"
        malformed.pop("reasoningTokens")
        attempt.observe(self.delta(malformed), ZCODE_TURN)
        stale = dict(ZCODE_NATIVE["usageDeltas"][1])
        stale["eventId"] = "evt-fixture-stale"
        stale["version"] = 2
        attempt.observe(self.delta(stale), ZCODE_TURN)
        expected = ZCODE_NATIVE["expectedDelta"]
        token_usage = usage.normalize_token_usage(attempt.delta_usage())
        self.assertEqual(token_usage["source"], expected["source"])
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "partial")

    def test_a_contradictory_replay_makes_the_delta_sum_unknown(self):
        attempt = self.usage()
        first = dict(ZCODE_NATIVE["usageDeltas"][0])
        attempt.observe(self.delta(first), ZCODE_TURN)
        changed = dict(first)
        changed["inputTokens"] = first["inputTokens"] + 1
        attempt.observe(self.delta(changed), ZCODE_TURN)
        self.assertIsNone(attempt.delta_usage())

    def test_a_missing_baseline_or_lost_cursor_is_unprovable_not_everything(self):
        missing = self.usage(resumed=True)
        connection = ScriptedConnection([NativeError("timeout", "stalled optional read")])
        missing.capture_baseline(connection)
        missing.capture_final(connection)
        self.assertIsNone(missing.message_usage())
        self.assertTrue(connection.deadline > time.monotonic() + 500)

        lost = self.usage(resumed=True)
        combined = {"messages": [*ZCODE_NATIVE["baselineMessages"]["messages"],
                                 *ZCODE_NATIVE["finalMessages"]["messages"]]}
        connection = ScriptedConnection([ZCODE_NATIVE["baselineMessages"], combined])
        lost.capture_baseline(connection)
        lost.capture_final(connection)
        self.assertIsNone(lost.message_usage())

    def test_a_resumed_session_without_a_cursor_is_refused(self):
        attempt = self.usage(resumed=True)
        connection = ScriptedConnection([{"messages": []}, ZCODE_NATIVE["finalMessages"]])
        attempt.capture_baseline(connection)
        self.assertIsNone(attempt.baseline_last)
        attempt.capture_final(connection)
        self.assertEqual(len(connection.calls), 1, "no cursorless read may run for a resumed session")
        self.assertIsNone(attempt.message_usage())

    def test_child_work_marks_the_root_only_sum_partial(self):
        attempt = self.usage()
        connection = ScriptedConnection([{"messages": []}, ZCODE_NATIVE["finalMessages"]])
        attempt.capture_baseline(connection)
        attempt.observe({"method": "session/event", "params": {
            "type": "tool.updated", "sessionId": ZCODE_SESSION, "turnId": ZCODE_TURN, "seq": 9,
            "payload": {"kind": "scheduled", "childSessionId": "sess-child", "agentId": "agent-1"}}}, ZCODE_TURN)
        attempt.capture_final(connection)
        self.assertEqual(usage.normalize_token_usage(attempt.message_usage())["completeness"], "partial")

    def test_an_unusable_root_message_counter_never_becomes_zero(self):
        attempt = self.usage()
        page = {"messages": [
            {"info": {"messageId": "new-assistant-broken", "sessionId": ZCODE_SESSION, "role": "assistant",
                      "parentMessageId": "new-user-0001", "tokens": {"input": 5, "output": 1, "reasoning": 0,
                                                                     "cache": {"read": 0}}},
             "parts": [{"type": "text", "text": "partial native record", "partId": "p", "messageId":
                        "new-assistant-broken", "sessionId": ZCODE_SESSION}]}]}
        connection = ScriptedConnection([{"messages": []}, page])
        attempt.capture_baseline(connection)
        attempt.capture_final(connection)
        self.assertIsNone(attempt.message_usage())
        # The retained text still survives for a continuation.
        self.assertEqual(attempt.last_assistant_message["text"], "partial native record")

    def test_reasoning_and_tool_parts_are_never_assistant_text(self):
        attempt = self.usage()
        connection = ScriptedConnection([{"messages": []}, ZCODE_NATIVE["finalMessages"]])
        attempt.capture_baseline(connection)
        attempt.capture_final(connection)
        self.assertNotIn("reasoning", attempt.last_assistant_message["text"])
        self.assertNotIn("tool", attempt.last_assistant_message["text"])

    def test_the_optional_read_is_bounded_and_restores_the_main_deadline(self):
        class Stalled:
            def __init__(self):
                self.deadline = time.monotonic() + 600

            def call(self, _method, _params):
                while time.monotonic() < self.deadline:
                    time.sleep(0.02)
                raise NativeError("timeout", "stalled")

        stalled = Stalled()
        original = stalled.deadline
        started = time.monotonic()
        self.assertIsNone(optional_native_call(stalled, "session/messages", {}))
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(stalled.deadline, original)

        passing = ScriptedConnection([{"messages": []}])
        original = passing.deadline
        self.assertEqual(optional_native_call(passing, "session/messages", {}), {"messages": []})
        self.assertEqual(passing.deadline, original)

    def test_quota_attribution_uses_only_structured_native_codes(self):
        failure = ZCODE_NATIVE["quotaFailureTurn"]
        self.assertEqual(quota_native_code(failure["error"]), failure["expectedCode"])
        self.assertEqual(usage.classify_quota_code(failure["expectedCode"]), failure["expectedClass"])
        unrelated = {"errorType": "AiSdkModelAdapterError", "code": "model_context_exceeded",
                     "attribution": {"reason": "context_window", "providerErrorCode": "1308"}}
        self.assertIsNone(quota_native_code(unrelated))
        self.assertIsNone(quota_native_code({}))


class ZcodeAdapterObservationTests(unittest.TestCase):
    """The whole ZCode controller path against the private app-server fixture."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-native-usage-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        self.builtin = self.root / "builtin.json"
        self.personal = self.root / "personal.json"
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {"templateRules": [], "providerRules": []}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [
            {"providerId": "fixture-api", "config": {"access": {"type": "api-key", "apiKey": "fixture-secret-never-public"}}}]}}}))
        MOCK_ZCODE_USAGE.chmod(0o755)
        self.environment = clean_environment()
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_ZCODE_CLI=str(MOCK_ZCODE_USAGE.resolve()),
                                BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_DEV_SOURCE="1",
                                ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.builtin),
                                ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.personal))
        self.adapter = ZcodeAdapter()

    def context(self, case: str, *, index: int = 1, previous=None, mode=None, timeout: int = 12,
                run_id: str = "goal-1") -> ExecutionContext:
        identity = {"version": 1, "taskId": run_id, "attemptId": f"attempt-{index}", "generation": index,
                    "turnId": f"turn-{index}", "resumeMode": mode or ("native-session" if previous else "initial"),
                    "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id=run_id, attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "fixture task", "timeoutSeconds": timeout,
                                      "provider": "fixture-api", "model": "fixture-model", "effort": "low"},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_ZCODE_TEST_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": identity})

    def execute(self, case: str, **kwargs):
        context = self.context(case, **kwargs)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(context.timeout_seconds + 10), "ZCode fixture controller did not exit")
        return context, self.adapter.collect(handle, context)

    def test_a_turn_publishes_the_bound_root_message_tokens(self):
        _, outcome = self.execute("usage-ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        expected = ZCODE_NATIVE["expectedMessages"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["source"], expected["source"])
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "complete")
        self.assertIsNone(outcome.result["quota"])
        self.assertIsNone(outcome.result["quotaFailure"])
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["source"], "zcode/session-root-assistant-message")

    def test_a_resumed_session_counts_only_the_new_turn(self):
        _, first = self.execute("usage-ok")
        self.assertEqual(first.status, "ok", first.to_report())
        session_id = first.result["sessionId"]
        _, second = self.execute("resume-ok", index=2, previous=session_id)
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["sessionId"], session_id)
        expected = ZCODE_NATIVE["expectedMessages"]
        token_usage = second.result["tokenUsage"]
        # The baseline already carried 11,000 input tokens; they must never be
        # re-counted into the second attempt.
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["source"], expected["source"])

    def test_a_delta_only_turn_uses_the_partial_stream_fallback(self):
        _, outcome = self.execute("delta-only")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        expected = ZCODE_NATIVE["expectedDelta"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["source"], expected["source"])
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "partial")
        self.assertIsNone(outcome.result["lastAssistantMessage"])

    def test_a_lost_cursor_is_never_counted_as_this_attempt(self):
        _, outcome = self.execute("cursor-lost")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["tokenUsage"]["source"], ZCODE_NATIVE["expectedDelta"]["source"])

    def test_a_failed_quota_turn_keeps_its_native_code_and_last_message(self):
        _, outcome = self.execute("usage-quota")
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        failure = outcome.result["quotaFailure"]
        self.assertEqual(failure["nativeCode"], ZCODE_NATIVE["quotaFailureTurn"]["expectedCode"])
        self.assertEqual(failure["code"], ZCODE_NATIVE["quotaFailureTurn"]["expectedClass"])
        self.assertEqual(failure["source"], "zcode/session-turn-failed")
        self.assertIsNotNone(outcome.result["tokenUsage"])
        self.assertEqual(outcome.result["lastAssistantMessage"]["text"],
                         ZCODE_NATIVE["expectedMessages"]["lastAssistantText"])
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_a_stalled_optional_read_cannot_change_the_turn(self):
        _, outcome = self.execute("metadata-timeout", timeout=20)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        # The baseline read was bounded away, so the provable source is the stream
        # fallback; the turn itself is untouched.
        self.assertEqual(outcome.result["tokenUsage"]["source"], ZCODE_NATIVE["expectedDelta"]["source"])
        self.assertEqual(outcome.result["tokenUsage"]["completeness"], "partial")

    def test_no_native_usage_reports_unknown_not_zero(self):
        _, outcome = self.execute("usage-empty")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertIsNone(outcome.result["quota"])
        self.assertIsNone(outcome.result["quotaFailure"])
        self.assertIsNone(outcome.result["lastAssistantMessage"])


if __name__ == "__main__":
    unittest.main()
