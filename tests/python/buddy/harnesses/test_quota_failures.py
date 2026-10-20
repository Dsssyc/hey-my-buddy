"""Native quota classification at the run seams, without native processes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE, PrivateStatePaths, RunBudget, RunBuddy, RunIdentity, RunRequest,
)
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    NativeError, decode_native_failure, quota_failure,
)
from hey_my_buddy.buddy.roles.run_execution import _worker_facts


PROVIDER_RATE_LIMIT_FAILURE = {
    "code": "1310",
    "summary": "provider rate_limited code 1310 status 429 providerCode 1310 phase stream not-retryable",
    "attribution": {"providerErrorCode": "1310", "reason": "rate_limited", "retryable": False, "statusCode": 429},
}

FAILURE_FIELDS = {"code", "nativeCode", "source", "observedAt", "resetsAt", "rejectionPhase"}


class ZcodeQuotaProtocolTests(unittest.TestCase):
    def test_nonretryable_provider_rate_limit_is_exhaustion_with_its_own_code(self):
        failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
        # The specific provider code survives even when the enclosing code differs.
        failure["code"] = "model_rate_limited"
        self.assertEqual(quota_failure(failure), {"code": "quota-exceeded", "nativeCode": "1310"})
        failure["summary"] = "temporarily available"
        self.assertEqual(quota_failure(failure)["code"], "quota-exceeded")

    def test_retryable_must_be_exactly_false_and_summary_cannot_supply_it(self):
        for value in (True, None, 0, "false", [], {}):
            with self.subTest(retryable=value):
                failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
                failure["attribution"]["retryable"] = value
                self.assertEqual(quota_failure(failure), {"code": "rate-limited", "nativeCode": "1310"})
        failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
        del failure["attribution"]["retryable"]
        self.assertEqual(quota_failure(failure)["code"], "rate-limited")

    def test_rate_limit_without_provider_code_keeps_exported_code_or_reason(self):
        failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
        del failure["attribution"]["providerErrorCode"]
        self.assertEqual(quota_failure(failure)["nativeCode"], "1310")
        del failure["code"]
        self.assertEqual(quota_failure(failure), {"code": "quota-exceeded", "nativeCode": "rate_limited"})

    def test_reason_must_be_rate_limited_and_unrelated_failures_are_not_quota(self):
        for reason in ("context_window", "transport", None, "RATE_LIMITED"):
            with self.subTest(reason=reason):
                failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
                failure["attribution"]["reason"] = reason
                self.assertIsNone(quota_failure(failure))
        self.assertIsNone(quota_failure({"summary": PROVIDER_RATE_LIMIT_FAILURE["summary"]}))

    def test_native_decoder_uses_attribution_or_error_retryability(self):
        raw = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
        decoded = decode_native_failure({"error": raw})
        self.assertEqual(decoded["code"], "1310")
        self.assertEqual(decoded["attribution"]["providerErrorCode"], "1310")
        self.assertEqual(quota_failure(decoded), {"code": "quota-exceeded", "nativeCode": "1310"})
        for attribution_retryable in (None, "false", 0):
            with self.subTest(attribution_retryable=attribution_retryable):
                raw["attribution"]["retryable"] = attribution_retryable
                raw["retryable"] = False
                decoded = decode_native_failure({"error": raw})
                self.assertIs(decoded["attribution"]["retryable"], False)
                self.assertEqual(quota_failure(decoded)["code"], "quota-exceeded")
        del raw["attribution"]["retryable"]
        self.assertEqual(quota_failure(decode_native_failure({"error": raw}))["code"], "quota-exceeded")
        raw["attribution"]["retryable"] = True
        self.assertEqual(quota_failure(decode_native_failure({"error": raw}))["code"], "rate-limited")
        raw["attribution"]["retryable"] = False
        raw["retryable"] = True
        self.assertEqual(quota_failure(decode_native_failure({"error": raw}))["code"], "quota-exceeded")


class NativeQuotaFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-quota-run-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def request(self, harness):
        return RunRequest(identity=RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                               invocation_id="invocation"),
                          harness=harness, buddy=RunBuddy(provider="fixture-api", model="fixture-model", effort="low"),
                          cwd=str(self.root), input_text="fixture", permission="none",
                          output_schema={"type": "object"}, budget=RunBudget(timeout_seconds=5),
                          private_state=PrivateStatePaths(invocation_root=str(self.root / "run"),
                                                          native_root=str(self.root / "native")),
                          session_record_path=str(self.root / "session.jsonl"))

    def zcode_result(self, failure):
        from hey_my_buddy.buddy.harnesses.zcode import native_run
        spawn = SimpleNamespace(connection=Mock(), version="fixture-version")
        error = NativeError("native-turn-failed", "fixture turn failed", failure=failure)
        with patch.object(native_run, "_spawn_app_server", return_value=spawn), \
             patch.object(native_run, "_final_message_rounds", side_effect=error), \
             patch.object(native_run, "_stop_collection", return_value=True):
            return native_run.run(self.request("zcode"), observer=lambda _: FEEDBACK_CONTINUE,
                                  services=None, cancelled=lambda: False)

    def test_zcode_publishes_nonretryable_rate_limit_as_exhaustion(self):
        for retryability_on_error in (False, True):
            with self.subTest(retryability_on_error=retryability_on_error):
                raw = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
                if retryability_on_error:
                    del raw["attribution"]["retryable"]
                    raw["retryable"] = False
                result = self.zcode_result(decode_native_failure({"error": raw}))
                self.assertEqual(result.end.status, "error")
                failure = _worker_facts(result)["quotaFailure"]
                self.assertEqual(failure["code"], "quota-exceeded")
                self.assertEqual(failure["nativeCode"], "1310")
                self.assertIsNone(failure["resetsAt"])
                self.assertEqual(set(failure), FAILURE_FIELDS)
                self.assertEqual(failure["source"], "zcode/session-turn-failed")

    def test_zcode_publishes_retryable_and_unspecified_rate_limits_without_exhaustion(self):
        for retryable in (True, None):
            with self.subTest(retryable=retryable):
                failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
                if retryable is None:
                    del failure["attribution"]["retryable"]
                else:
                    failure["attribution"]["retryable"] = retryable
                # Keep misleading summary text in the actual native run path.
                result = self.zcode_result(failure)
                self.assertEqual(_worker_facts(result)["quotaFailure"]["code"], "rate-limited")
                self.assertEqual(set(_worker_facts(result)["quotaFailure"]), FAILURE_FIELDS)

    def test_zcode_does_not_publish_unrelated_native_failures(self):
        for reason in ("context_window", "transport"):
            failure = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
            failure["attribution"]["reason"] = reason
            self.assertIsNone(self.zcode_result(failure).native_failure)

    def test_codex_publishes_the_code_table_classification(self):
        from hey_my_buddy.buddy.harnesses.codex import native_run
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        for native, expected in (("usageLimitExceeded", "quota-exceeded"), ("rateLimitExceeded", "rate-limited"),
                                 ("contextWindowExceeded", None)):
            with self.subTest(native=native):
                state = native_run._RunState()
                evidence = native_run.TurnEvidence("thread", "turn")
                evidence.completed = {"status": "failed", "error": {"codexErrorInfo": native}}
                evidence.event_seq = 1
                evidence.started = True
                chain = SimpleNamespace(projector=None, session=Mock(), bind=Mock())
                with patch.object(native_run, "_admit_turn", return_value={"turn": {"id": "turn"}}), \
                     patch.object(native_run, "TurnEvidence", return_value=evidence), \
                     patch.object(native_run, "_checkpoint", return_value=None), \
                     patch.object(native_run, "attempt_token_usage", return_value=None):
                    with self.assertRaises(CodexProtocolError):
                        native_run._worker_turn(Mock(), chain, self.request("codex"), state, "thread",
                                                lambda _: FEEDBACK_CONTINUE, Mock())
                native_run._collect_quota_failure(state, stream_truncated=False)
                if expected is None:
                    self.assertIsNone(state.quota_failure)
                else:
                    failure = native_run._quota_package(state.quota_failure).value
                    self.assertEqual(failure["code"], expected)
                    self.assertEqual(failure["nativeCode"], native)
                    self.assertEqual(set(failure), FAILURE_FIELDS)
                    self.assertEqual(failure["rejectionPhase"], "before-model")

    def test_claude_publishes_explicit_native_rejection_as_exhaustion(self):
        from hey_my_buddy.buddy.harnesses.claude import native_run
        from hey_my_buddy.buddy.harnesses.claude.protocol import QuotaRejected
        # The native failure boundary raises the same exported rejection that
        # the stream collector raises; no native child or service is started.
        with patch.object(native_run, "_prepare", side_effect=QuotaRejected("five_hour", None)):
            result = native_run.run(self.request("claude"), observer=lambda _: FEEDBACK_CONTINUE,
                                    services=None, cancelled=lambda: False)
        self.assertEqual(result.native_failure.value["code"], "quota-exceeded")
        self.assertEqual(result.native_failure.value["nativeCode"], "five_hour")
        self.assertEqual(set(_worker_facts(result)["quotaFailure"]), FAILURE_FIELDS)
        self.assertEqual(result.native_failure.value["rejectionPhase"], "unknown")

    def test_dsh_publishes_the_code_table_classification(self):
        from hey_my_buddy.buddy.harnesses.dsh import native_run
        for native, expected in (("QUOTA", "quota-exceeded"), ("RATE_LIMIT", "rate-limited"), ("CONTEXT", None)):
            with self.subTest(native=native):
                with patch.object(native_run, "_quota_package", wraps=native_run._quota_package) as package:
                    result = native_run._build_result(self.request("dsh"), state=native_run._RunState(),
                        tools=None, activity=SimpleNamespace(payload=lambda: None, counts={"modelTurns": 0}),
                        client=None, sessions=[], evidence=None, raw_answer=None, correction_count=0,
                        drained=None, invocation_root=self.root, record={"failure": {"code": native}})
                if expected is None:
                    package.assert_not_called()
                    self.assertIsNone(result.native_failure)
                else:
                    failure = _worker_facts(result)["quotaFailure"]
                    self.assertEqual(failure["code"], expected)
                    self.assertEqual(failure["nativeCode"], native)
                    self.assertEqual(set(failure), FAILURE_FIELDS)
