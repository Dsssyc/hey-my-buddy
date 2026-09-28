"""Claude adapter lifecycle against a private stream-json fixture, with no model calls."""
from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from buddy.adapters import claude as claude_module
from buddy.adapters import turn_io
from buddy.adapters.base import ExecutionContext
from buddy.adapters.claude import ClaudeAdapter
from buddy.adapters.claude_protocol import OUTCOME_SCHEMA, QUOTA_REJECTED_ERROR
from buddy.errors import BoardError

FIXTURE = Path(__file__).parent / "fixtures/fake_claude.py"


def flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


class ClaudeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-claude-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        FIXTURE.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and not key.startswith(("ANTHROPIC_", "CLAUDE_"))
                            and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CLAUDE_CLI=str(FIXTURE), BUDDY_CLAUDE_FIXTURE_STATE=str(self.root / "fixture.json"),
                                BUDDY_CLAUDE_SETTINGS_POLICY="isolated",
                                BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1")
        claude_module._reset_metadata_cache()
        self.addCleanup(claude_module._reset_metadata_cache)
        self.adapter = ClaudeAdapter()

    def context(self, case="ok", *, index=1, previous=None, mode=None, effort="low", timeout=8,
                model="claude-opus-5-5[1m]", access=None):
        turn_input = {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": mode or ("reconstructed-new-session" if previous else "initial"),
                      "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "Write a fixture result", "timeoutSeconds": timeout,
                                      "provider": "anthropic", "model": model, "effort": effort},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, context):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20), "Claude fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def fixture_state(self):
        return json.loads((self.root / "fixture.json").read_text())

    def control(self, index=1):
        return json.loads((self.root / f"attempt-{index}" / "claude-control.json").read_text())

    def test_completed_turn_uses_the_preallocated_session_and_never_resumes(self):
        context = self.context()
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.exit_code, 0)
        turn = outcome.result["turn"]
        control = self.control()
        self.assertEqual(turn["sessionId"], control["sessionId"])
        uuid.UUID(turn["sessionId"], version=4)
        self.assertEqual(turn["provenance"]["nativeSessionId"], turn["sessionId"])
        self.assertTrue(turn["provenance"]["structuredOutputValidated"])
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        # The init readback is the observed session model; argv is not attestation.
        self.assertEqual(turn["provenance"]["sessionModel"], "claude-opus-5-5[1m]")
        self.assertEqual(outcome.result["sessionModel"], "claude-opus-5-5[1m]")
        argv = self.fixture_state()["argv"]
        self.assertEqual(flag_value(argv, "--session-id"), control["sessionId"])
        self.assertNotIn("--resume", argv)
        native_session = outcome.result["nativeSession"]
        self.assertEqual(native_session["storageScope"], "harness-user-store")
        self.assertEqual(native_session["nativeAppVisibility"], "unknown")
        self.assertFalse(native_session["resumable"])

    def test_observed_model_identity_stays_null_with_a_bounded_observed_set(self):
        outcome = self.execute(self.context())
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertIsNone(outcome.result["observed"])
        self.assertEqual(outcome.result["observedModels"], ["claude-haiku-4-5-20251001", "claude-opus-5-5[1m]"])
        self.assertEqual(outcome.result["turn"]["provenance"]["modelUsage"],
                         ["claude-haiku-4-5-20251001", "claude-opus-5-5[1m]"])

    def test_assistance_outcome_is_imported(self):
        outcome = self.execute(self.context("assistance"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")

    def test_completed_result_cannot_also_request_assistance(self):
        outcome = self.execute(self.context("completed-request"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "invalid-result")
        self.assertNotIn("turn", outcome.result)

    def test_wrong_session_identity_is_rejected(self):
        outcome = self.execute(self.context("wrong-session"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "wrong-native-session")
        self.assertNotIn("turn", outcome.result)
        self.assertFalse((self.root / "attempt-1" / "turn-output.json").exists())

    def test_duplicate_or_malformed_native_results_are_rejected(self):
        for index, case in enumerate(("duplicate-result", "invalid-json", "nonfinite", "unknown-response",
                                      "no-result", "bad-structured", "failed", "wrong-cwd", "init-wrong-session",
                                      "bad-subtype", "missing-is-error"), 1):
            with self.subTest(case=case):
                outcome = self.execute(self.context(case, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertNotIn("turn", outcome.result)
                self.assertNotIn("workspaceSeal", outcome.result)

    def test_non_root_subagent_result_does_not_end_the_root_turn(self):
        outcome = self.execute(self.context("non-root-result"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")

    def test_rejected_rate_limit_with_malformed_identity_still_fails_as_quota(self):
        outcome = self.execute(self.context("quota-bad-type"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "quota-rejected")
        self.assertEqual(outcome.result["quotaFailure"], {"rateLimitType": "unknown", "resetsAt": None})

    def test_missing_controller_receipt_is_not_imported(self):
        context = self.context()
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        Path(handle.log_paths["stdout"]).write_text("")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("no complete JSON result", outcome.error)
        self.assertNotIn("turn", outcome.result)

    def test_permission_request_denial_becomes_controller_attention(self):
        outcome = self.execute(self.context("permission"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "attention")
        provenance = turn["provenance"]
        self.assertTrue(provenance["controllerAttention"])
        self.assertFalse(provenance["structuredOutputValidated"])
        self.assertEqual(provenance["deniedToolUseIds"], ["toolu_fixture_1"])
        self.assertEqual(provenance["deniedControlRequestIds"], ["perm-1"])
        self.assertTrue(outcome.result["attentionRequired"])
        state = self.fixture_state()
        self.assertEqual(state["permissionReply"], {"subtype": "success", "behavior": "deny"})
        forged = {**turn, "provenance": {key: value for key, value in provenance.items()
                                         if key != "deniedControlRequestIds"}}
        forged["provenance"]["permissionDenials"] = 0
        self.assertIsNotNone(self.adapter.validate_turn_provenance(forged))
        unbound = {**turn, "outcome": {**turn["outcome"], "disposition": "completed"}}
        self.assertIsNotNone(self.adapter.validate_turn_provenance(unbound))

    def test_result_permission_denials_alone_convert_a_completed_outcome(self):
        outcome = self.execute(self.context("result-denials"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "attention")
        provenance = turn["provenance"]
        self.assertTrue(provenance["controllerAttention"])
        self.assertEqual(provenance["permissionDenials"], 1)
        self.assertIsNone(self.adapter.validate_turn_provenance(turn))

    def test_cancel_interrupts_the_native_child_before_stopping_the_group(self):
        context = self.context("hang", timeout=12)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        time.sleep(0.3)
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(15))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(self.fixture_state()["interrupted"], "the interrupt control request must precede group stop")
        self.assertTrue(outcome.result.get("nativeInterruptAcknowledged"),
                        "the receipt must distinguish a native interrupt reply from forced shutdown")

    def test_deadline_ends_the_native_process_group(self):
        outcome = self.execute(self.context("hang", timeout=1))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "deadline")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_quota_event_while_interrupting_does_not_lose_the_stop_receipt(self):
        context = self.context("interrupt-quota", timeout=12)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=.2) if handle.group_alive() else None)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (self.root / "fixture.json").exists() and self.fixture_state().get("userTurns") == 1:
                break
            time.sleep(.05)
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(15))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)
        self.assertIs(outcome.result.get("nativeInterruptAcknowledged"), False,
                      "a quota frame that interrupts the acknowledgement wait is not an acknowledgement")

    def test_zero_timeout_executes_unlimited_without_an_immediate_deadline(self):
        context = self.context(timeout=0)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertEqual(handle.deadline, math.inf)
        self.assertIsNotNone(handle.wait(20), "Claude fixture controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)

    def test_unsetted_background_work_blocks_a_terminal_result(self):
        unsettled = self.execute(self.context("bg-running"))
        self.assertEqual(unsettled.status, "failed", unsettled.to_report())
        self.assertEqual(unsettled.result["code"], "background-work-unsettled")
        self.assertNotIn("turn", unsettled.result)
        lookalike = self.execute(self.context("bg-not-completed", index=2))
        self.assertEqual(lookalike.status, "failed", lookalike.to_report())
        self.assertEqual(lookalike.result["code"], "background-work-unsettled")
        settled = self.execute(self.context("bg-settled", index=3))
        self.assertEqual(settled.status, "ok", settled.to_report())
        self.assertTrue(settled.result["turn"]["provenance"]["backgroundSettled"])

    def test_model_output_before_the_user_message_is_rejected(self):
        outcome = self.execute(self.context("early-result"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "native-turn-started-early")
        self.assertNotIn("turn", outcome.result)

    def test_quota_rejection_is_sanitized_infrastructure_failure_without_import(self):
        context = self.context("quota-rejected")
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "quota-rejected")
        self.assertEqual(outcome.error, QUOTA_REJECTED_ERROR)
        self.assertIn("ADAPTER_UNAVAILABLE", outcome.error)
        self.assertEqual(outcome.result["quotaFailure"],
                         {"rateLimitType": "five_hour", "resetsAt": "2026-09-26T12:00:00Z"})
        self.assertEqual(set(outcome.result["quotaFailure"]), {"rateLimitType", "resetsAt"})
        self.assertNotIn("turn", outcome.result)
        self.assertNotIn("workspaceSeal", outcome.result)
        self.assertFalse(context.turn_output_file().exists())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(self.fixture_state()["interrupted"], "quota rejection interrupts the native child")

    def test_structured_result_quota_denial_is_classified_without_copying_errors(self):
        for index, case in enumerate(("result-quota", "api-429"), 1):
            with self.subTest(case=case):
                outcome = self.execute(self.context(case, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertEqual(outcome.result["code"], "quota-rejected")
                self.assertEqual(outcome.error, QUOTA_REJECTED_ERROR)
                self.assertEqual(outcome.result["quotaFailure"]["rateLimitType"], "unknown")
                self.assertIsNone(outcome.result["quotaFailure"]["resetsAt"])
                self.assertNotIn("turn", outcome.result)

    def test_quota_warning_records_latest_bounded_observations(self):
        outcome = self.execute(self.context("quota-warning"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        observations = outcome.result["rateLimitObservations"]
        self.assertEqual(set(observations), {"five_hour", "seven_day"})
        self.assertEqual(observations["seven_day"]["status"], "allowed_warning")
        self.assertEqual(observations["seven_day"]["resetsAt"], "2026-09-27T00:00:00Z")
        self.assertEqual(observations["five_hour"]["utilization"], {"fiveHourPctUsed": 12})
        self.assertNotIn("quotaFailure", outcome.result)
        self.assertIn("turn", outcome.result)

    def test_quota_warning_survives_a_later_rejection(self):
        outcome = self.execute(self.context("warning-then-rejected"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.error, QUOTA_REJECTED_ERROR)
        self.assertEqual(outcome.result["rateLimitObservations"]["seven_day"]["utilization"], .9)
        self.assertNotIn("turn", outcome.result)

    def test_discovery_catalog_uses_resolved_ids_without_a_user_turn(self):
        (self.root / "fixture.json").unlink(missing_ok=True)
        with mock.patch.dict("os.environ", self.environment, clear=False):
            catalog = self.adapter.discover_models()
        provider = catalog["providers"][0]
        self.assertEqual(provider["provider"], "anthropic")
        self.assertEqual([model["id"] for model in provider["models"]],
                         ["claude-opus-5-5[1m]", "claude-fable-5-1", "claude-sonnet-5",
                          "claude-haiku-4-5-20251001"])
        efforts = {model["id"]: model["efforts"] for model in provider["models"]}
        self.assertEqual(efforts["claude-opus-5-5[1m]"], ["low", "medium", "high"])
        self.assertEqual(efforts["claude-fable-5-1"], ["low", "high"])
        self.assertEqual(efforts["claude-haiku-4-5-20251001"], ["default"])
        state = self.fixture_state()
        self.assertTrue(state["initialize"])
        self.assertEqual(state["userTurns"], 0, "discovery must never send a user message")
        self.assertNotIn("--session-id", state["argv"])

    def test_discovery_accepts_missing_or_null_token_source_with_verified_auth(self):
        for case in ("null-token", "missing-token"):
            with self.subTest(case=case):
                (self.root / "fixture.json").unlink(missing_ok=True)
                environment = {**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": case,
                               "BUDDY_CLAUDE_FIXTURE_AUTH_STATUS": "ok"}
                with mock.patch.dict("os.environ", environment, clear=False):
                    catalog = self.adapter.discover_models()
                self.assertEqual([model["id"] for model in catalog["providers"][0]["models"]],
                                 ["claude-opus-5-5[1m]", "claude-fable-5-1", "claude-sonnet-5",
                                  "claude-haiku-4-5-20251001"])
                state = self.fixture_state()
                self.assertTrue(state["initialize"])
                self.assertEqual(state["userTurns"], 0, "discovery must never send a user message")
                self.assertEqual(state["authStatusRuns"], 1,
                                 "the missing tokenSource is verified by exactly one bounded auth status readback")

    def test_governed_turn_accepts_null_token_source_with_verified_auth(self):
        context = self.context("null-token")
        context.environment["BUDDY_CLAUDE_FIXTURE_AUTH_STATUS"] = "ok"
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        state = self.fixture_state()
        self.assertEqual(state["userTurns"], 1)
        self.assertEqual(state["authStatusRuns"], 1)

    def test_explicit_none_stays_refused_without_an_auth_status_process(self):
        (self.root / "fixture.json").unlink(missing_ok=True)
        environment = {**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": "no-auth",
                       "BUDDY_CLAUDE_FIXTURE_AUTH_STATUS": "ok"}
        with mock.patch.dict("os.environ", environment, clear=False):
            with self.assertRaises(BoardError) as caught:
                self.adapter.discover_models()
        self.assertIn("tokenSource none", caught.exception.message)
        state = self.fixture_state()
        self.assertEqual(state.get("authStatusRuns", 0), 0,
                         "an explicit none is a decision; no auth status readback may run")
        self.assertEqual(state["userTurns"], 0)

    def test_unverified_auth_status_fails_closed_with_bounded_reasons(self):
        for proof, expected in (("logged-out", "active first-party login"),
                                ("third-party", "not first-party"),
                                ("missing-provider", "not first-party"),
                                ("malformed", "not valid JSON"),
                                ("nonzero", "exited nonzero"),
                                ("timeout", "time bound")):
            with self.subTest(proof=proof):
                (self.root / "fixture.json").unlink(missing_ok=True)
                environment = {**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": "null-token",
                               "BUDDY_CLAUDE_FIXTURE_AUTH_STATUS": proof}
                with mock.patch.dict("os.environ", environment, clear=False):
                    with self.assertRaises(BoardError) as caught:
                        self.adapter.discover_models()
                reason = caught.exception.message
                self.assertIn(expected, reason)
                self.assertLessEqual(len(reason), 200, "the refusal reason stays bounded")
                for leak in ("claude.ai", "subscriptionType", "{not json"):
                    self.assertNotIn(leak, reason)
                self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_available_uses_cached_metadata_and_reports_missing_first_party_auth(self):
        with mock.patch.dict("os.environ", {**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": "ok"}, clear=False):
            self.assertEqual(self.adapter.available(), (True, None))
            with mock.patch.dict("os.environ", {"BUDDY_CLAUDE_FIXTURE_CASE": "no-auth"}, clear=False):
                # Briefly cached: a repeated capability read does not respawn the CLI.
                self.assertEqual(self.adapter.available(), (True, None))
                with self.assertRaises(BoardError):
                    self.adapter.discover_models()
                usable, reason = self.adapter.available()
                self.assertFalse(usable)
                self.assertIn("tokenSource none", reason)
        with mock.patch.dict("os.environ", {"PATH": str(self.root)}, clear=False):
            self.assertFalse(self.adapter.available()[0])
        with mock.patch.dict("os.environ", {**self.environment, "ANTHROPIC_BASE_URL": "https://secret.example.invalid"},
                             clear=False):
            usable, reason = self.adapter.available()
            self.assertFalse(usable)
            self.assertIn("ANTHROPIC_BASE_URL", reason)
            self.assertNotIn("secret.example.invalid", reason)

    def test_unauthenticated_failures_are_cached_not_respawned(self):
        (self.root / "fixture.json").unlink(missing_ok=True)
        with mock.patch.dict("os.environ", {**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": "no-auth"}, clear=False):
            self.assertFalse(self.adapter.available()[0])
            self.assertFalse(self.adapter.available()[0])
            self.assertEqual(self.fixture_state()["runs"], 1,
                             "the unauthenticated failure is cached briefly, not respawned per read")

    def test_concurrent_capability_reads_share_one_metadata_probe(self):
        from concurrent.futures import ThreadPoolExecutor
        entered = threading.Event()
        release = threading.Event()

        def probe():
            entered.set()
            self.assertTrue(release.wait(3))
            raise BoardError("ADAPTER_UNAVAILABLE", "fixture unauthenticated")

        with mock.patch.dict(os.environ, self.environment, clear=True), \
                mock.patch.object(claude_module, "_probe_native_metadata", side_effect=probe) as discover, \
                ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.adapter.available) for _ in range(8)]
            self.assertTrue(entered.wait(3))
            release.set()
            self.assertEqual([future.result(5) for future in futures],
                             [(False, "fixture unauthenticated")] * 8)
            self.assertEqual(discover.call_count, 1)

    def test_default_policy_executes_with_private_settings_and_empty_sources(self):
        context = self.context()
        context.environment.pop("BUDDY_CLAUDE_SETTINGS_POLICY", None)
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        argv = self.fixture_state()["argv"]
        self.assertEqual(flag_value(argv, "--setting-sources"), "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertTrue(Path(flag_value(argv, "--settings")).resolve().is_relative_to(context.directory.resolve()))

    def test_unsupported_settings_policy_is_refused_before_any_model_input(self):
        for policy in ("", "global"):
            with self.subTest(policy=policy):
                context = self.context()
                context.environment["BUDDY_CLAUDE_SETTINGS_POLICY"] = policy
                with self.assertRaises(BoardError) as caught:
                    self.adapter.prepare(context)
                self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
                self.assertIn("BUDDY_CLAUDE_SETTINGS_POLICY=isolated", caught.exception.message)
        self.assertFalse((self.root / "fixture.json").exists(), "no native child may start with an unsupported policy")

    def test_third_party_env_override_is_refused_before_any_model_input(self):
        context = self.context()
        context.environment["ANTHROPIC_BASE_URL"] = "https://secret-gateway.example.invalid"
        with self.assertRaises(BoardError) as caught:
            self.adapter.prepare(context)
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertIn("ANTHROPIC_BASE_URL", caught.exception.message)
        self.assertNotIn("secret-gateway.example.invalid", caught.exception.message)
        self.assertFalse((self.root / "fixture.json").exists())
        outcome = self.execute(self.context())
        self.assertEqual(outcome.status, "ok", outcome.to_report())

    def test_model_and_effort_must_be_in_the_native_catalog(self):
        for index, (model, effort) in enumerate((("claude-unknown", "low"),
                                                 ("claude-haiku-4-5-20251001", "medium"),
                                                 ("claude-opus-5-5[1m]", "default")), 1):
            with self.subTest(model=model, effort=effort):
                outcome = self.execute(self.context(model=model, effort=effort, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertEqual(outcome.result["code"], "invalid-configuration")
                self.assertNotIn("turn", outcome.result)

    def test_default_effort_omits_the_flag_for_a_model_without_levels(self):
        outcome = self.execute(self.context(model="claude-haiku-4-5-20251001", effort="default"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertNotIn("--effort", self.fixture_state()["argv"])

    def test_native_session_resume_is_refused_and_fresh_sessions_are_allocated(self):
        with self.assertRaises(BoardError) as blocked:
            self.adapter.prepare(self.context(mode="native-session", previous="old-session"))
        self.assertIn("never resumes", blocked.exception.message)
        with self.assertRaises(BoardError):
            self.adapter.prepare(self.context(mode="unknown"))
        carried = self.execute(self.context(mode="initial", previous="old-session"))
        self.assertEqual(carried.status, "failed")
        self.assertEqual(carried.result["code"], "invalid-resume-mode")
        first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        second = self.execute(self.context(index=2, previous=first.result["turn"]["sessionId"]))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertNotEqual(second.result["turn"]["sessionId"], first.result["turn"]["sessionId"])
        uuid.UUID(second.result["turn"]["sessionId"], version=4)

    def test_isolated_settings_and_sandbox_file_are_strict(self):
        outcome = self.execute(self.context())
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        argv = self.fixture_state()["argv"]
        for flag in ("--safe-mode", "--strict-mcp-config", "--restricted"):
            self.assertIn(flag, argv)
        self.assertEqual(flag_value(argv, "--setting-sources"), "")
        self.assertEqual(flag_value(argv, "--mcp-config"), '{"mcpServers":{}}')
        self.assertEqual(flag_value(argv, "--permission-prompt-tool"), "stdio")
        self.assertEqual(flag_value(argv, "--permission-mode"), "acceptEdits")
        self.assertEqual(flag_value(argv, "--json-schema"), turn_io.canonical_json(OUTCOME_SCHEMA))
        control = self.control()
        settings_path = Path(control["nativeRoot"], "settings.json")
        settings = json.loads(settings_path.read_text())
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertTrue(settings["sandbox"]["network"]["strictAllowlist"])
        self.assertEqual(stat.S_IMODE(settings_path.stat().st_mode), 0o600)

    def test_read_only_workspace_refuses_write_and_bash_tools(self):
        context = self.context()
        context.turn["input"]["executionWorkspace"] = {"path": str(self.cwd), "access": "read"}
        # The hand-made manifest carries only the access decision; workspace
        # verification and sealing are owned by other tests.
        with mock.patch.object(turn_io, "verify_workspace", lambda target: None), \
             mock.patch.object(turn_io, "seal_workspace", lambda target: (None, None)):
            outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        argv = self.fixture_state()["argv"]
        tools = flag_value(argv, "--tools")
        self.assertEqual(set(tools.split(",")), {"Glob", "Grep", "LS", "Read"})
        self.assertEqual(flag_value(argv, "--permission-mode"), "default")
        self.assertEqual(flag_value(argv, "--disallowedTools").split(","),
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])
        control = self.control()
        self.assertEqual(control["access"], "read")

    def test_attempt_scoped_credential_stays_private_and_out_of_result(self):
        context = self.context()
        context.agent_credential = "private-attempt-secret"
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(stat.S_IMODE(context.credential_file().stat().st_mode), 0o600)
        self.assertEqual(context.environment["BUDDY_AGENT_CREDENTIAL_FILE"], str(context.credential_file()))
        self.assertNotIn("private-attempt-secret", json.dumps(outcome.to_report()))
        # The allowlisted native child environment never sees Buddy credentials.
        state = self.fixture_state()
        self.assertEqual(state["seenBuddyEnv"], ["BUDDY_CLAUDE_FIXTURE_CASE", "BUDDY_CLAUDE_FIXTURE_STATE"])
        self.assertEqual(state["seenAnthropicEnv"], [])

    def test_runner_refuses_overrides_and_unsupported_policy_before_the_native_child(self):
        control = {"directory": str(self.root / "runner-direct"), "nativeRoot": str(self.root / "runner-direct" / "native"),
                   "cwd": str(self.cwd), "timeoutSeconds": 5, "sessionId": str(uuid.uuid4()),
                   "inputFile": str(self.root / "in.json"), "outputFile": str(self.root / "out.json"),
                   "taskFile": str(self.root / "task.txt"), "taskId": "goal-1", "attemptId": "attempt-9",
                   "generation": 1, "spec": {"provider": "anthropic", "model": "claude-opus-5-5[1m]", "effort": "low"}}
        path = self.root / "runner-control.json"
        path.write_text(json.dumps(control))
        cases = {"third-party-provider": {"ANTHROPIC_AWS_BASE_URL": "https://aws.example.invalid"},
                 "settings-policy-unsupported": {"BUDDY_CLAUDE_SETTINGS_POLICY": "global"}}
        for code, extra in cases.items():
            with self.subTest(code=code):
                environment = {**self.environment, **extra}
                completed = subprocess.run([sys.executable, "-m", "buddy.adapters.claude_runner",
                                            "--control", str(path)], capture_output=True, text=True,
                                           env=environment, timeout=30)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["code"], code)
                self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.root / "fixture.json").exists(),
                         "the refusal must fire on the incoming environment, before any native child starts")

    def test_activity_file_tracks_the_native_turn(self):
        context = self.context()
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        activity = json.loads((context.directory / "activity.json").read_text())
        self.assertEqual(activity["attemptId"], context.attempt_id)
        self.assertEqual(activity["activity"]["phase"], "finishing")
        self.assertEqual(activity["activity"]["nativeSessionId"], self.control()["sessionId"])


if __name__ == "__main__":
    unittest.main()
