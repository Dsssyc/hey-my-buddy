"""Owned-process ZCode protocol tests; no model network or shared board is used."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from buddy.adapters.base import ExecutionContext
from buddy.adapters.zcode import ZcodeAdapter

FIXTURE = Path(__file__).parent / "fixtures/mock_zcode.py"


class ZcodeFixtureCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        self.builtin = self.root / "builtin.json"
        self.personal = self.root / "personal.json"
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {"templateRules": [], "providerRules": []}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [
            {"providerId": "fixture-api", "config": {"access": {"type": "api-key", "apiKey": "fixture-secret-never-public"}}}]}}}))
        FIXTURE.chmod(0o755)
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_") and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_ZCODE_CLI=str(FIXTURE.resolve()), BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_DEV_SOURCE="1",
                                ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.builtin), ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.personal))
        self.adapter = ZcodeAdapter()

    def context(self, case="ok", *, index=1, previous=None, mode=None, timeout=10, effort="low"):
        identity = {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{index}", "generation": index,
                    "turnId": f"turn-{index}", "resumeMode": mode or ("native-session" if previous else "initial"),
                    "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "fixture task", "timeoutSeconds": timeout,
                                      "provider": "fixture-api", "model": "fixture-model", "effort": effort},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_ZCODE_TEST_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": identity})

    def execute(self, context):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        return handle, outcome


class ZcodeAdapterTests(ZcodeFixtureCase):
    def test_success_uses_native_root_receipt_and_keeps_secrets_private(self):
        original = self.personal.read_bytes()
        context = self.context()
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["summary"], "fixture work completed")
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")
        self.assertNotIn("flush", turn["provenance"])
        self.assertEqual(outcome.result["requested"], {"provider": "fixture-api", "model": "fixture-model", "effort": "low"})
        self.assertEqual(outcome.result["resolved"], outcome.result["requested"])
        self.assertIsNone(outcome.result["observed"])
        self.assertNotIn("observedConfig", outcome.result)
        self.assertNotIn("requestedConfig", outcome.result)
        self.assertEqual(self.personal.read_bytes(), original)
        self.assertNotIn("fixture-secret-never-public", json.dumps(outcome.to_report()))
        self.assertFalse(any("provider" in x["location"] or "bridge" in x["location"] for x in outcome.artifacts))
        # Inquiry is a declared capability backed by this attempt's private bridge,
        # and the session facts stay separate from the Git workspace facts.
        self.assertIn("inquiry", self.adapter.capabilities)
        self.assertTrue(outcome.result["inquiry"]["mounted"], outcome.result["inquiry"])
        native = outcome.result["nativeSession"]
        self.assertEqual(native["sessionId"], turn["sessionId"])
        self.assertEqual(native["storageScope"], "task-private")
        self.assertEqual(native["nativeAppVisibility"], "not-listed-in-native-app")
        self.assertTrue(native["bindingPresent"])
        self.assertTrue(native["resumable"])
        credentials = json.loads((context.directory / "inquiry.json").read_text())
        self.assertEqual(len(credentials["token"]), 64)
        self.assertEqual(oct((context.directory / "inquiry.json").stat().st_mode & 0o777), "0o600")

    def test_child_finish_does_not_replace_or_disable_root_finish(self):
        _, outcome = self.execute(self.context("child-first"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["provenance"]["toolCallId"], "root-call")

    def test_protocol_failures_never_import_a_turn(self):
        cases = {"wrong-root": "missing-finish", "wrong-turn": "missing-finish", "tool-error": "finish-tool-failed",
                 "truncated": "finish-tool-failed", "duplicate": "duplicate-finish", "turn-failure": "native-turn-failed",
                 "prompt-failure": "native-turn-failed", "close-failed": "session-close-unconfirmed",
                 "wrong-input": "wrong-native-turn", "forged-receipt": "invalid-finish"}
        for index, (case, code) in enumerate(cases.items(), 1):
            with self.subTest(case=case):
                _, outcome = self.execute(self.context(case, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed)
                self.assertEqual(outcome.result.get("code"), code, outcome.to_report())
                self.assertNotIn("turn", outcome.result)
                self.assertEqual(outcome.result["resolved"], outcome.result["requested"])
                self.assertIsNone(outcome.result["observed"])

    def test_model_readback_mismatch_fails_before_work(self):
        _, outcome = self.execute(self.context("config-mismatch", effort="high"))
        self.assertEqual(outcome.result.get("code"), "configuration-mismatch", outcome.to_report())
        self.assertEqual(outcome.status, "failed")
        self.assertIsNone(outcome.result["resolved"])

    def test_duplicate_protocol_members_fail_without_work(self):
        _, outcome = self.execute(self.context("invalid-json"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result.get("code"), "invalid-protocol")
        self.assertIsNone(outcome.result["resolved"])
        self.assertNotIn("turn", outcome.result)

    def test_resume_keeps_exact_session_and_reinjects_the_new_bridge(self):
        _, first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        session = first.result["turn"]["sessionId"]
        _, second = self.execute(self.context(index=2, previous=session))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["turn"]["sessionId"], session)
        self.assertEqual(second.result["turn"]["resumeMode"], "native-session")
        self.assertEqual(second.result["resolved"]["effort"], "low")
        self.assertNotEqual(second.result["turn"]["inputSha256"], first.result["turn"]["inputSha256"])
        self.assertNotEqual(second.result["turn"]["provenance"]["nativeTurnId"], first.result["turn"]["provenance"]["nativeTurnId"])

    def test_failed_turn_can_reconstruct_a_new_root_without_a_verified_session(self):
        first_context = self.context("tool-error")
        _, first = self.execute(first_context)
        self.assertEqual(first.status, "failed", first.to_report())
        self.assertNotIn("turn", first.result)
        root = Path(json.loads((first_context.directory / "zcode-control.json").read_text())["nativeRoot"])
        prior_session = json.loads((root / "sessions.fixture.json").read_text())["sessionId"]
        _, second = self.execute(self.context(index=2, mode="reconstructed-new-session"))
        self.assertEqual(second.status, "ok", second.to_report())
        turn = second.result["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(turn["previousSessionId"])
        self.assertNotEqual(turn["sessionId"], prior_session)

    def test_native_resume_requires_same_configuration_and_reconstruction_uses_a_new_root(self):
        _, first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        previous = first.result["turn"]["sessionId"]
        _, refused = self.execute(self.context(index=2, previous=previous, effort="high"))
        self.assertEqual(refused.status, "failed", refused.to_report())
        self.assertEqual(refused.result.get("code"), "native-resume-unavailable")
        self.assertNotIn("turn", refused.result)
        _, reconstructed = self.execute(self.context(index=3, previous=previous, mode="reconstructed-new-session", effort="high"))
        self.assertEqual(reconstructed.status, "ok", reconstructed.to_report())
        turn = reconstructed.result["turn"]
        self.assertEqual(turn["previousSessionId"], previous)
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertNotEqual(turn["sessionId"], previous)
        self.assertEqual(reconstructed.result["resolved"]["effort"], "high")
        reused = {**turn, "sessionId": previous, "provenance": {**turn["provenance"], "nativeSessionId": previous}}
        self.assertIsNotNone(self.adapter.validate_turn_provenance(reused))

    def test_missing_native_resume_never_starts_fresh(self):
        _, outcome = self.execute(self.context(previous="sess-missing"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result.get("code"), "native-resume-unavailable")

    def test_bound_native_resume_rejects_missing_or_different_native_sessions(self):
        _, first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        session = first.result["turn"]["sessionId"]
        for index, (case, code) in enumerate((("resume-missing", "native-rpc-error"),
                                             ("resume-wrong-root", "wrong-native-session")), 2):
            with self.subTest(case=case):
                _, outcome = self.execute(self.context(case, index=index, previous=session))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed)
                self.assertEqual(outcome.result.get("code"), code)
                self.assertNotIn("turn", outcome.result)

    def test_assistance_comes_from_structured_receipt(self):
        _, outcome = self.execute(self.context("assistance"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")

    def test_deadline_stops_owned_native_process_group(self):
        _, outcome = self.execute(self.context("hang", timeout=1))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result.get("code"), "timeout")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_blocked_native_stdin_does_not_disable_the_deadline(self):
        context = self.context("blocked-input", timeout=1)
        context.spec["task"] = "x" * 262144
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "timeout")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_missing_controller_receipt_cannot_confirm_the_separate_native_group(self):
        context = self.context()
        handle, result = self.execute(context)
        self.assertEqual(result.status, "ok", result.to_report())
        Path(handle.log_paths["stdout"]).write_text("incomplete controller output")
        uncertain = self.adapter.collect(handle, context)
        self.assertEqual(uncertain.status, "failed")
        self.assertFalse(uncertain.shutdown_confirmed)
        self.assertEqual(uncertain.artifacts, [])

    def test_oauth_provider_is_rejected_before_any_native_execution(self):
        from buddy.errors import BoardError
        value = json.loads(self.personal.read_text())
        value["config"]["providerConfigRules"]["providerRules"][0]["config"]["access"] = {"type": "zhipu-account"}
        self.personal.write_text(json.dumps(value))
        context = self.context()
        with self.assertRaises(BoardError) as error:
            self.adapter.prepare(context)
        self.assertEqual(error.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertFalse(context.directory.exists())

    def test_coding_never_falls_back_to_native_defaults(self):
        from buddy.errors import BoardError
        for key in ("provider", "model", "effort"):
            for value in (None, "", " \t", False):
                with self.subTest(key=key, value=value):
                    context = self.context()
                    if value is None:
                        context.spec.pop(key)
                    else:
                        context.spec[key] = value
                    with self.assertRaises(BoardError) as error:
                        self.adapter.prepare(context)
                    self.assertEqual(error.exception.code, "INVALID_ARGUMENT")
                    self.assertFalse(context.directory.exists())

    def test_catalog_omits_models_without_native_effort_options(self):
        with mock.patch.dict(os.environ, {**self.environment, "BUDDY_ZCODE_TEST_CASE": "no-effort"}, clear=True):
            result = self.adapter.discover_models()
        self.assertEqual(result["providers"], [])
        self.assertTrue(any("effort" in warning for warning in result["warnings"]))

    def test_cancel_and_unconfirmed_stop_are_distinct(self):
        context = self.context("hang")
        handle = self.adapter.start(context)
        time.sleep(0.4)
        self.adapter.cancel(handle)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        with mock.patch.object(handle, "shutdown_confirmed", return_value=False):
            uncertain = self.adapter.collect(handle, context)
        self.assertFalse(uncertain.shutdown_confirmed)
        self.assertEqual(uncertain.status, "failed")
        self.assertEqual(uncertain.artifacts, [])

    def test_catalog_uses_native_efforts_without_a_turn_or_credentials(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = self.adapter.discover_models()
        self.assertEqual(result["providers"][0]["models"][0]["efforts"], ["low", "high"])
        self.assertEqual(result["providers"][0]["adapter"], "zcode")
        self.assertEqual(result["providers"][0]["packageVersion"], "fixture-0.16.9")
        self.assertEqual(result["providers"][0]["models"][0]["contextWindow"], 200000)
        self.assertEqual(result["providers"][0]["models"][0]["inputModalities"], ["text"])
        self.assertTrue(result["providers"][0]["models"][0]["available"])
        self.assertNotIn("fixture-secret-never-public", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
