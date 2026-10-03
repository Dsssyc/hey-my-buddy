"""Native token usage, quota and last-assistant-message normalization.

The two native record fixtures are desensitized minimal copies of the real
records this machine produces: a DSH session rollout (``assistant/message`` with
``data.usage`` and ``turn/end``) and the Codex App Server v2 token-usage and
rate-limit payloads. The tests pin the one rule that matters for the board: DSH
reports its input count excluding cache read/write tokens and Codex includes the
cached input, so each is unified exactly once and a replayed record never
accumulates twice.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.protocol import usage

FIXTURES = Path(__file__).resolve().parents[1] / "buddy" / "harnesses"
DSH_NATIVE = json.loads((FIXTURES / "dsh/fixtures/native-usage-dsh.json").read_text())
CODEX_NATIVE = json.loads((FIXTURES / "codex/fixtures/native-usage-codex.json").read_text())


def dsh_usage_record() -> dict:
    """The DSH native counters summed exactly as the native session reports them."""
    events = [event["data"]["usage"] for event in DSH_NATIVE["events"] if event["type"] == "assistant/message"]
    summed = {key: sum(event[key] for event in events) for key in events[0]}
    return {"source": "dsh/session-assistant-usage", "inputTokens": summed["inputTokens"],
            "outputTokens": summed["outputTokens"], "cacheReadTokens": summed["cacheReadTokens"],
            "totalTokens": summed["totalTokens"], "inputBasis": "excludes-cached",
            "nativeRecords": len(events), "completeness": "complete"}


def codex_usage_record() -> dict:
    """The Codex native counters after the attempt delta, as the runner builds them."""
    notifications = CODEX_NATIVE["tokenUsageNotifications"]
    baseline = {key: value - notifications[0]["params"]["tokenUsage"]["last"][key]
                for key, value in notifications[0]["params"]["tokenUsage"]["total"].items()}
    total = notifications[-1]["params"]["tokenUsage"]["total"]
    return {"source": "codex/app-server-thread-token-usage", "inputBasis": "includes-cached",
            "inputTokens": total["inputTokens"] - baseline["inputTokens"],
            "cachedInputTokens": total["cachedInputTokens"] - baseline["cachedInputTokens"],
            "outputTokens": total["outputTokens"] - baseline["outputTokens"],
            "reasoningOutputTokens": total["reasoningOutputTokens"] - baseline["reasoningOutputTokens"],
            "nativeRecords": CODEX_NATIVE["expected"]["records"], "completeness": "complete"}


class TokenUsageTests(unittest.TestCase):
    def test_dsh_native_counters_add_cache_exactly_once(self):
        normalized = usage.normalize_token_usage(dsh_usage_record())
        expected = DSH_NATIVE["expected"]
        self.assertEqual(normalized["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(normalized["cachedInputTokens"], expected["derivedCachedInputTokens"])
        self.assertEqual(normalized["outputTokens"], expected["outputTokens"])
        self.assertEqual(normalized["inputBasis"], usage.INPUT_BASIS_INCLUDES_CACHED)
        self.assertEqual(normalized["scope"], "attempt")
        self.assertEqual(normalized["nativeRecords"], 2)
        self.assertEqual(normalized["source"], "dsh/session-assistant-usage")

    def test_codex_native_counters_never_add_cache_again(self):
        normalized = usage.normalize_token_usage(codex_usage_record())
        expected = CODEX_NATIVE["expected"]
        self.assertEqual(normalized["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(normalized["inputTokens"], expected["inputTokens"])
        self.assertEqual(normalized["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(normalized["outputTokens"], expected["outputTokens"])
        self.assertEqual(normalized["reasoningOutputTokens"], expected["reasoningOutputTokens"])

    def test_normalization_is_idempotent_and_never_accumulates_on_replay(self):
        for record in (dsh_usage_record(), codex_usage_record()):
            with self.subTest(source=record["source"]):
                once = usage.normalize_token_usage(record)
                twice = usage.normalize_token_usage(copy.deepcopy(once))
                self.assertEqual(once, twice)

    def test_a_native_dsh_record_without_input_basis_is_still_unified_once(self):
        # A raw DSH record carries cache counters but no explicit basis; reading it
        # as canonical would silently undercount the prompt side.
        raw = {"inputTokens": 10357, "cacheReadTokens": 768, "outputTokens": 208, "totalTokens": 11333}
        normalized = usage.normalize_token_usage(raw)
        self.assertEqual(normalized["inputTokens"], 10357 + 768)
        self.assertEqual(normalized["cachedInputTokens"], 768)

    def test_an_absent_counter_stays_unknown_and_is_never_zero(self):
        normalized = usage.normalize_token_usage({"inputTokens": 100, "outputTokens": 5, "source": "fixture"})
        self.assertIsNone(normalized["cachedInputTokens"])
        self.assertEqual(normalized["completeness"], "unknown")
        self.assertEqual(normalized["nativeRecords"], 1)

    def test_output_only_data_is_kept_with_an_unknown_input(self):
        normalized = usage.normalize_token_usage({"outputTokens": 5, "source": "fixture"})
        self.assertIsNone(normalized["inputTokens"])
        self.assertEqual(normalized["outputTokens"], 5)

    def test_a_session_cumulative_record_is_refused(self):
        self.assertIsNone(usage.normalize_token_usage(
            {"inputTokens": 100, "outputTokens": 5, "scope": "session"}))
        self.assertIsNone(usage.normalize_token_usage({"inputTokens": 100, "outputTokens": 5, "scope": "task"}))

    def test_no_usable_counter_returns_none(self):
        for value in (None, {}, "usage", {"inputTokens": None, "outputTokens": None},
                      {"inputTokens": True, "outputTokens": 1}, {"inputTokens": -1, "outputTokens": 1},
                      {"inputTokens": 1.5, "outputTokens": 1}, {"inputTokens": 1, "outputTokens": 1,
                                                                "completeness": "certain"},
                      {"inputTokens": 1, "outputTokens": 1, "inputBasis": "unknown"}):
            with self.subTest(value=value):
                self.assertIsNone(usage.normalize_token_usage(value))

    def test_inconsistent_cache_larger_than_input_is_refused(self):
        self.assertIsNone(usage.normalize_token_usage(
            {"inputTokens": 10, "cachedInputTokens": 11, "outputTokens": 1, "inputBasis": "includes-cached"}))

    def test_partial_completeness_is_preserved(self):
        normalized = usage.normalize_token_usage({"inputTokens": 10, "cachedInputTokens": 2, "outputTokens": 1,
                                                  "source": "fixture", "completeness": "partial"})
        self.assertEqual(normalized["completeness"], "partial")


class QuotaTests(unittest.TestCase):
    def test_a_native_rate_limit_response_becomes_windows_with_iso_resets(self):
        response = CODEX_NATIVE["rateLimitsRead"]
        observed_at = 1791046000
        candidate = {"source": "codex/app-server-rate-limits", "observedAt": observed_at,
                     "provider": "openai", "nativeAccountId": response["accountId"],
                     "ordinaryUsageAllowed": response["ordinaryUsageAllowed"],
                     "limitId": response["rateLimits"]["limitId"], "planType": response["rateLimits"]["planType"],
                     "windows": [
                         {"name": "primary", "usedPercent": response["rateLimits"]["primary"]["usedPercent"],
                          "resetsAt": response["rateLimits"]["primary"]["resetsAt"],
                          "windowDurationMins": response["rateLimits"]["primary"]["windowDurationMins"]},
                         {"name": "secondary", "usedPercent": response["rateLimits"]["secondary"]["usedPercent"],
                          "resetsAt": response["rateLimits"]["secondary"]["resetsAt"],
                          "windowDurationMins": response["rateLimits"]["secondary"]["windowDurationMins"]},
                     ]}
        normalized = usage.normalize_quota(candidate)
        self.assertEqual(normalized["observedAt"], "2026-10-03T16:46:40Z")
        self.assertEqual(normalized["scope"]["provider"], "openai")
        self.assertEqual(normalized["scope"]["nativeAccountId"], "account-fixture-0001")
        self.assertTrue(normalized["ordinaryUsageAllowed"])
        self.assertEqual([window["name"] for window in normalized["windows"]], ["primary", "secondary"])
        self.assertEqual(normalized["windows"][0]["usedPercent"], 42.5)
        self.assertEqual(normalized["windows"][1]["windowDurationMins"], 10080)
        self.assertTrue(normalized["windows"][1]["resetsAt"].startswith("2026-10-03T17:01:16"))

    def test_a_window_without_a_percentage_is_dropped_and_never_zero(self):
        self.assertIsNone(usage.normalize_quota({
            "source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
            "windows": [{"name": "primary"}, {"name": "secondary", "usedPercent": None}]}))
        kept = usage.normalize_quota({
            "source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
            "windows": [{"name": "primary"}, {"name": "secondary", "usedPercent": 12}]})
        self.assertEqual([window["name"] for window in kept["windows"]], ["secondary"])
        self.assertEqual(kept["windows"][0]["usedPercent"], 12)

    def test_a_missing_or_zero_reset_time_stays_unknown(self):
        normalized = usage.normalize_quota({
            "source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
            "windows": [{"name": "primary", "usedPercent": 10, "resetsAt": 0},
                        {"name": "secondary", "usedPercent": 20}]})
        self.assertNotIn("resetsAt", normalized["windows"][0])
        self.assertNotIn("resetsAt", normalized["windows"][1])

    def test_a_quota_observation_requires_its_own_time_and_source(self):
        self.assertIsNone(usage.normalize_quota({"source": "fixture",
                                                 "windows": [{"name": "primary", "usedPercent": 1}]}))
        no_source = usage.normalize_quota({"observedAt": "2026-01-01T00:00:00Z",
                                           "windows": [{"name": "primary", "usedPercent": 1}]})
        self.assertEqual(no_source["source"], "unknown")

    def test_an_over_long_window_list_is_refused_instead_of_trimmed(self):
        windows = [{"name": f"w{index}", "usedPercent": 1} for index in range(usage.MAX_WINDOWS + 1)]
        self.assertIsNone(usage.normalize_quota({"source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
                                                 "windows": windows}))

    def test_a_sparse_update_that_proves_nothing_returns_none(self):
        self.assertIsNone(usage.normalize_quota({
            "source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
            "windows": [{"name": "primary", "usedPercent": None}], "ordinaryUsageAllowed": None}))

    def test_a_reached_type_or_permission_alone_is_a_fact(self):
        reached = usage.normalize_quota({"source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
                                         "reachedType": "rate_limit_reached", "windows": []})
        self.assertEqual(reached["reachedType"], "rate_limit_reached")
        self.assertEqual(reached["windows"], [])

    def test_free_text_identifier_values_never_enter_the_normalized_quota(self):
        leaked = "Bearer sk-ant-api03-SECRET-FRAGMENT"
        normalized = usage.normalize_quota({
            "source": leaked, "observedAt": "2026-01-01T00:00:00Z", "provider": "openai sandbox",
            "nativeAccountId": "account\n800-1", "limitId": leaked, "planType": "pro plan",
            "reachedType": "rate limit reached",
            "windows": [{"name": "primary window", "usedPercent": 42.5, "limitId": leaked},
                        {"name": "primary", "usedPercent": 42.5, "limitId": "codex"}]})
        # A non-identifier value is omitted (or "unknown" for source), never kept.
        self.assertEqual(normalized["source"], "unknown")
        self.assertEqual(normalized["scope"], {"provider": None, "nativeAccountId": None,
                                               "limitId": None, "planType": None})
        self.assertNotIn("reachedType", normalized)
        self.assertEqual([window["name"] for window in normalized["windows"]], ["primary"])
        self.assertEqual(normalized["windows"][0]["limitId"], "codex")
        self.assertNotIn("SECRET-FRAGMENT", json.dumps(normalized))

    def test_normal_identifier_values_are_kept_unchanged(self):
        normalized = usage.normalize_quota({
            "source": "codex/app-server-rate-limits", "observedAt": "2026-01-01T00:00:00Z",
            "provider": "openai", "nativeAccountId": "account-fixture-0001", "limitId": "codex",
            "planType": "pro", "reachedType": "rate_limit_reached",
            "windows": [{"name": "five_hour", "usedPercent": 10, "limitId": "codex:primary"}]})
        self.assertEqual(normalized["source"], "codex/app-server-rate-limits")
        self.assertEqual(normalized["scope"], {"provider": "openai", "nativeAccountId": "account-fixture-0001",
                                               "limitId": "codex", "planType": "pro"})
        self.assertEqual(normalized["reachedType"], "rate_limit_reached")
        self.assertEqual(normalized["windows"][0]["limitId"], "codex:primary")

    def test_no_quota_fact_at_all_returns_none(self):
        for value in (None, {}, "quota", {"source": "fixture"}, {"observedAt": "2026-01-01T00:00:00Z"}):
            with self.subTest(value=value):
                self.assertIsNone(usage.normalize_quota(value))


class QuotaFailureTests(unittest.TestCase):
    def test_native_codes_keep_their_structured_identity(self):
        for native, expected in (("QUOTA", "quota-exceeded"), ("usageLimitExceeded", "quota-exceeded"),
                                 ("sessionBudgetExceeded", "quota-exceeded"),
                                 ("workspace_owner_usage_limit_reached", "quota-exceeded"),
                                 ("RATE_LIMIT", "rate-limited"), ("rateLimitExceeded", "rate-limited")):
            with self.subTest(native=native):
                failure = usage.normalize_quota_failure({"nativeCode": native, "source": "fixture",
                                                         "observedAt": 1791046876})
                self.assertEqual(failure["code"], expected)
                self.assertEqual(failure["nativeCode"], native)
                self.assertEqual(failure["observedAt"], "2026-10-03T17:01:16Z")

    def test_an_unrelated_native_error_is_not_a_quota_failure(self):
        failure = usage.normalize_quota_failure({"nativeCode": "contextWindowExceeded", "source": "fixture"})
        self.assertEqual(failure["code"], "unknown")

    def test_a_failure_without_a_native_code_returns_none(self):
        for value in (None, {}, {"code": "quota-exceeded"}, {"nativeCode": ""}, {"nativeCode": 5}):
            with self.subTest(value=value):
                self.assertIsNone(usage.normalize_quota_failure(value))

    def test_free_text_failure_values_are_refused_or_unknown(self):
        # A free-text native code is not a structured fact and is refused; a
        # free-text source is replaced with "unknown". Neither is ever copied.
        for bad in ("Bearer sk-ant-api03-SECRET-FRAGMENT", "usage limit exceeded!", "quota\nexceeded"):
            with self.subTest(bad=bad):
                self.assertIsNone(usage.normalize_quota_failure({"nativeCode": bad, "source": "fixture"}))
        failure = usage.normalize_quota_failure({"nativeCode": "usage_limit_reached",
                                                 "source": "Bearer sk-ant-api03-SECRET-FRAGMENT"})
        self.assertEqual(failure["nativeCode"], "usage_limit_reached")
        self.assertEqual(failure["source"], "unknown")
        self.assertNotIn("SECRET-FRAGMENT", json.dumps(failure))


class LastAssistantMessageTests(unittest.TestCase):
    def message(self, text: str, **overrides) -> dict:
        raw = text.encode()
        value = {"text": text, "sourceBytes": len(raw),
                 "sha256": hashlib.sha256(raw).hexdigest(), "truncated": False}
        value.update(overrides)
        return value

    def test_a_codex_checkpoint_message_keeps_its_native_identity(self):
        # The Codex checkpoint spells the native identity ``itemId``.
        normalized = usage.normalize_last_assistant_message(
            self.message("Fixture final answer.\n", itemId="final-1", phase="final_answer"),
            source="codex/app-server-root-assistant-message")
        self.assertEqual(normalized["text"], "Fixture final answer.\n")
        self.assertEqual(normalized["sourceId"], "final-1")
        self.assertEqual(normalized["phase"], "final_answer")
        self.assertEqual(normalized["source"], "codex/app-server-root-assistant-message")
        self.assertFalse(normalized["truncated"])
        self.assertEqual(normalized["sha256"], hashlib.sha256(b"Fixture final answer.\n").hexdigest())

    def test_a_dsh_native_message_without_metadata_is_bounded_not_faked(self):
        normalized = usage.normalize_last_assistant_message({"text": "Done.\n", "sourceId": "assistant-fixture-2"},
                                                            source="dsh/session-root-assistant-message")
        self.assertEqual(normalized["sourceBytes"], len("Done.\n".encode()))
        self.assertFalse(normalized["truncated"])

    def test_a_long_message_is_head_truncated_with_full_text_evidence(self):
        text = "x" * (usage.MAX_ASSISTANT_MESSAGE_BYTES + 500)
        normalized = usage.normalize_last_assistant_message(self.message(text), source="fixture")
        self.assertTrue(normalized["truncated"])
        self.assertEqual(len(json.dumps(normalized["text"]).encode()), usage.MAX_ASSISTANT_MESSAGE_BYTES)
        self.assertEqual(normalized["sourceBytes"], len(text.encode()))
        self.assertEqual(normalized["sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_text_that_contradicts_its_own_evidence_is_refused(self):
        raw = b"Fixture final answer.\n"
        self.assertIsNone(usage.normalize_last_assistant_message(
            {"text": "different", "sourceBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
             "truncated": False}))
        self.assertIsNone(usage.normalize_last_assistant_message(
            {"text": "Fixture final answer.\n", "sourceBytes": 3, "sha256": hashlib.sha256(raw).hexdigest(),
             "truncated": True}))

    def test_non_text_or_blank_output_is_never_a_message(self):
        # The normalizer bounds one already-selected native text. Selecting a root
        # assistant text (never a tool result) is the adapter's job.
        for value in (None, {}, {"text": ""}, {"text": "   "}, {"text": 5}, {"items": ["tool"]},
                      {"text": "ok", "sourceBytes": "many"}, {"text": "ok", "sha256": "zz"}):
            with self.subTest(value=value):
                self.assertIsNone(usage.normalize_last_assistant_message(value, source="fixture"))

    def test_a_truncated_native_message_stays_truncated_after_normalization(self):
        text = "y" * 100
        normalized = usage.normalize_last_assistant_message(
            {"text": text, "sourceBytes": 200, "sha256": hashlib.sha256((text * 2).encode()).hexdigest(),
             "truncated": True}, source="fixture")
        self.assertTrue(normalized["truncated"])
        self.assertEqual(normalized["sourceBytes"], 200)


class SidecarBoundaryTests(unittest.TestCase):
    """The DSH observer's document is private, bounded and attempt-bound."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-usage-sidecar-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, document: dict, name: str = "native-usage.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(document))
        return path

    def document(self, **overrides) -> dict:
        value = {"version": 1, "taskId": "task", "attemptId": "attempt", "generation": 1,
                 "updatedAt": "2026-01-01T00:00:00Z",
                 "nativeUsage": {"source": "dsh/session-assistant-usage"}}
        value.update(overrides)
        return value

    def read(self, path: Path) -> dict | None:
        return usage.read_sidecar(path, task_id="task", attempt_id="attempt", generation=1)

    def test_a_bound_document_is_read_verbatim(self):
        path = self.write({**self.document(), "nativeUsage": {"tokenUsage": {"inputTokens": 1, "outputTokens": 1}}})
        document = self.read(path)
        self.assertEqual(document["nativeUsage"]["tokenUsage"], {"inputTokens": 1, "outputTokens": 1})

    def test_a_foreign_or_stale_binding_is_refused(self):
        for overrides in ({"taskId": "other"}, {"attemptId": "other"}, {"generation": 2},
                          {"version": 2}, {"nativeUsage": {}, "extra": True}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(self.read(self.write(self.document(**overrides))))

    def test_malformed_oversized_and_missing_documents_are_refused(self):
        broken = self.root / "broken.json"
        broken.write_text("{not json")
        self.assertIsNone(self.read(broken))
        self.assertIsNone(self.read(self.root / "missing.json"))
        oversized = self.root / "oversized.json"
        oversized.write_text(json.dumps({**self.document(), "pad": "x" * usage.MAX_SIDECAR_BYTES}))
        self.assertIsNone(self.read(oversized))

    def test_a_symlinked_document_is_refused(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("this platform cannot refuse a symlinked sidecar")
        target = self.write(self.document(), name="real.json")
        link = self.root / "link.json"
        link.symlink_to(target)
        self.assertIsNone(self.read(link))


if __name__ == "__main__":
    unittest.main()
