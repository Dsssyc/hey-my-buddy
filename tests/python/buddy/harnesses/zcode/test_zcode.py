"""Owned-process ZCode protocol tests; no model network or shared board is used."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import c_two as cc

from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
from hey_my_buddy.buddy.roles.controller import worker_executor
from hey_my_buddy.buddy.harnesses.registry import adapter

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
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith(("BUDDY_", "ANTHROPIC_", "C2_")) and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_ZCODE_CLI=str(FIXTURE.resolve()), BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_DEV_SOURCE="1",
                                ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.builtin), ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.personal))
        from hey_my_buddy.protocol.rpc_config import configure_client
        configure_client(self.root / "state")
        self.addCleanup(cc.shutdown)
        self.adapter = worker_executor("zcode")
        self.description = adapter("zcode")

    def context(self, case="ok", *, index=1, previous=None, mode=None, timeout=10, effort="low"):
        # Concurrent private suites keep an attempt stable within this fixture
        # and distinct across roots, so journals and live endpoints never cross.
        attempt_id = f"attempt-{self.root.name}-{index}"
        identity = {"version": 1, "taskId": "goal-1", "attemptId": attempt_id, "generation": index,
                    "turnId": f"turn-{index}", "resumeMode": mode or ("native-session" if previous else "initial"),
                    "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=attempt_id, generation=index,
                                spec={"cwd": str(self.cwd), "task": "fixture task", "timeoutSeconds": timeout,
                                      "provider": "fixture-api", "model": "fixture-model", "effort": effort},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_ZCODE_TEST_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": identity})

    def execute(self, context):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(context.timeout_seconds + 10), "controller did not exit within its deadline and shutdown grace")
        outcome = self.adapter.collect(handle, context)
        return handle, outcome

    # -- the integrated live and inquiry entry points (shared with the
    # checkpoint and refusal flow suites) -----------------------------------

    def own_handle(self, handle):
        """Own a live controller: stop its group, then release only its endpoint."""
        from hey_my_buddy.buddy.roles.live import release_live_binding

        def release():
            release_live_binding(handle)
            descriptor = getattr(handle, "role_live_descriptor", None)
            if descriptor is not None:
                self.assertEqual(cc.inspect_endpoint(descriptor.address)["status"], "absent",
                                 "the owned endpoint was left behind")
        # LIFO: stop the actual held group before cleaning its captured endpoint.
        self.addCleanup(release)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)

    def live_channel(self, handle):
        """The holder's bound C-Two channel of this own run, once its ready file lands."""
        from hey_my_buddy.buddy.roles.live import handle_live_binding

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state, channel = handle_live_binding(handle)
            if channel is not None:
                return channel
            time.sleep(0.05)
        self.fail("the held controller endpoint never became ready")

    def ready_channel(self, handle):
        """The bound channel once the owner admitted the root turn (ready fact)."""
        channel = self.live_channel(handle)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = channel.observe(timeout_ms=1000, limit=1, fields=("observation",))
            observation = snapshot.observation if snapshot.observed else None
            if observation is not None and observation.ready:
                self.assertEqual(channel.identity, handle.role_run_identity,
                                 "the live channel must carry the run's complete identity")
                return channel
            time.sleep(0.05)
        self.fail("the attempt never mounted its private inquiry bridge and admitted the root turn")

    def ask(self, handle, inquiry_id, question, channel=None, timeout_ms=4000):
        """Queue one Host question through the real C-Two request operation."""
        channel = channel or self.ready_channel(handle)
        reply = channel.request(LiveRequest(identity=channel.identity, request_id=inquiry_id,
                                            kind="inquiry",
                                            payload=InquiryPayload(question_id=inquiry_id, question=question)),
                                timeout_ms=timeout_ms)
        self.assertTrue(reply.observed, reply.model_dump())
        self.assertEqual(reply.status, "queued", reply.model_dump())
        correlation = reply.native_correlation.value
        self.assertEqual(correlation["inquiryId"], inquiry_id)
        self.assertFalse(correlation["duplicate"])
        return channel, reply

    def inquiry_results_path(self, context):
        """The attempt-private journal path from the role's real control record."""
        from hey_my_buddy.private_dirs import context_root

        control = json.loads((context_root(context, "zcode") / "role-run-control.json").read_text())
        self.assertEqual(control["inquiry"], {"resultsPath": str(context.directory / "inquiry.results.jsonl")})
        return Path(control["inquiry"]["resultsPath"])

    def inquiry_records(self, context, inquiry_id):
        """The durable journal's ordered records of one inquiry of this attempt."""
        path = self.inquiry_results_path(context)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip() and json.loads(line).get("inquiryId") == inquiry_id]

    def release(self, context):
        from hey_my_buddy.private_dirs import context_root

        (context_root(context, "zcode") / "native-logs" / "release-turn").touch()

    def native_log(self, context, name):
        from hey_my_buddy.private_dirs import context_root

        path = context_root(context, "zcode") / "native-logs" / name
        return path.read_text() if path.exists() else ""

    def wait_file(self, context, name, timeout=20):
        from hey_my_buddy.private_dirs import context_root

        path = context_root(context, "zcode") / "native-logs" / name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not path.exists():
            time.sleep(0.05)
        return path.read_text() if path.exists() else None


class ZcodeAdapterTests(ZcodeFixtureCase):
    def test_slow_version_metadata_does_not_block_native_catalog_discovery(self):
        with mock.patch.dict(os.environ, {**self.environment, "BUDDY_ZCODE_TEST_CASE": "slow-version"}, clear=True):
            result = self.description.discover_models()
        self.assertEqual(result["harnessVersion"], "unknown")
        self.assertEqual(result["providers"][0]["models"][0]["id"], "fixture-model")

    def test_success_uses_native_root_receipt_and_keeps_secrets_private(self):
        original = self.personal.read_bytes()
        context = self.context()
        handle, outcome = self.execute(context)
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
        # Observation and the cooperative inquiry channel are declared
        # capabilities backed by this attempt's private bridge: questions queue
        # for delivery at the root's own checkpoint inside the one admitted
        # native turn, never through an injected send. The session facts stay
        # separate from the Git workspace facts.
        self.assertIn("observe", self.description.capabilities)
        self.assertIn("inquiry", self.description.capabilities)
        self.assertTrue(outcome.result["inquiry"]["mounted"], outcome.result["inquiry"])
        self.assertTrue(outcome.result["inquiry"]["supported"])
        self.assertEqual(outcome.result["inquiry"]["deliveryMode"], "cooperative-checkpoint")
        self.assertEqual(outcome.result["inquiry"]["answered"], 0)
        native = outcome.result["nativeSession"]
        self.assertEqual(native["sessionId"], turn["sessionId"])
        self.assertEqual(native["storageScope"], "task-private")
        self.assertEqual(native["storageOwner"], "buddy-goal")
        self.assertEqual(native["nativeAppVisibility"], "not-listed-in-native-app")
        self.assertTrue(native["bindingPresent"])
        self.assertTrue(native["resumable"])
        # The integrated inquiry entry: the role's control record is the only
        # carrier of the durable journal path — the retired socket credentials
        # (token, socket, errorPath) and the retired per-turn inquiry.json no
        # longer exist on disk. Live access is the holder's own control.live
        # material plus the ready descriptor its controller published; the
        # narrow token stays in the holder-held material and never reaches the
        # descriptor or the report.
        from hey_my_buddy.private_dirs import context_root
        private_root = context_root(context, "zcode")
        control = json.loads((private_root / "role-run-control.json").read_text())
        self.assertEqual(control["inquiry"], {"resultsPath": str(context.directory / "inquiry.results.jsonl")})
        self.assertEqual(control, handle.role_run_control)
        self.assertFalse((private_root / "inquiry.json").exists(),
                         "the retired per-turn inquiry.json must not be written")
        material = control["live"]
        self.assertEqual(set(material), {"instanceId", "token", "readyFile"}, material)
        self.assertEqual(len(material["instanceId"]), 64)
        self.assertEqual(len(material["token"]), 64)
        ready = Path(material["readyFile"])
        self.assertEqual(ready.parent, context_root(context, "zcode"))
        self.assertEqual(oct(ready.stat().st_mode & 0o777), "0o600")
        descriptor = json.loads(ready.read_text())
        self.assertEqual(set(descriptor), {"address", "name", "instanceId", "hostPid", "endpointCredential"}, descriptor)
        self.assertEqual(descriptor["instanceId"], material["instanceId"])
        self.assertEqual(descriptor["hostPid"], handle.pid)
        self.assertNotIn(material["token"], json.dumps(descriptor))
        self.assertNotIn(material["token"], json.dumps(outcome.to_report()))

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

    def test_native_turn_failure_attribution_is_whitelisted_and_secret_free(self):
        from hey_my_buddy.buddy.runtime.worker import classify_termination

        for index, case in enumerate(("turn-failed-quota", "turn-failed-quiet"), 1):
            with self.subTest(case=case):
                _, outcome = self.execute(self.context(case, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed)
                self.assertEqual(outcome.result["code"], "native-turn-failed")
                failure = outcome.result["nativeFailure"]
                report = json.dumps(outcome.to_report())
                self.assertNotIn("sk-fixture-never-public", report)
                self.assertNotIn("models.example.com", report)
                if case == "turn-failed-quota":
                    self.assertEqual(failure["errorType"], "AiSdkModelAdapterError")
                    self.assertEqual(failure["code"], "model_rate_limited")
                    self.assertEqual(failure["attribution"]["reason"], "rate_limited")
                    self.assertEqual(failure["attribution"]["source"], "provider")
                    self.assertEqual(failure["attribution"]["statusCode"], 429)
                    self.assertEqual(failure["attribution"]["providerErrorCode"], "1308")
                    self.assertIs(failure["attribution"]["retryable"], False)
                    self.assertIn("rate_limited", outcome.result["error"])
                    # A provider-side native failure keeps the distinct harness-error
                    # termination; it is never relabelled deadline, user-cancel or transport.
                    self.assertEqual(classify_termination(outcome, timed_out=False, cancel_requested=False),
                                     "harness-error")
                else:
                    self.assertEqual(failure["summary"], "no structured failure attribution was exported")
                    self.assertEqual(failure["attribution"], {})

    def test_a_prompt_failure_never_manufactures_attribution(self):
        # The state.updated prompt_failed envelope exports only a reason string
        # and an opaque patch, so no attribution is invented for it.
        _, outcome = self.execute(self.context("prompt-failure"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result.get("code"), "native-turn-failed")
        self.assertNotIn("nativeFailure", outcome.result)

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
        from hey_my_buddy.private_dirs import context_root
        root = Path(json.loads((context_root(first_context, "zcode") / "role-run-control.json").read_text())["nativeRoot"])
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
        self.assertIsNotNone(self.description.validate_turn_provenance(reused))

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

    def test_zero_timeout_executes_unlimited_without_an_immediate_deadline(self):
        context = self.context("ok", timeout=0)
        handle, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["turn"]["outcome"]["summary"], "fixture work completed")
        self.assertEqual(handle.deadline, math.inf)

    def test_zero_timeout_still_honors_explicit_cancellation(self):
        context = self.context("hang", timeout=0)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertEqual(handle.deadline, math.inf)
        time.sleep(0.4)
        self.adapter.cancel(handle)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)

    def test_positive_timeout_keeps_a_finite_stamped_deadline(self):
        context = self.context(timeout=10)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertGreater(handle.deadline, time.monotonic())
        self.assertFalse(math.isinf(handle.deadline))

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
        from hey_my_buddy.errors import BoardError
        value = json.loads(self.personal.read_text())
        value["config"]["providerConfigRules"]["providerRules"][0]["config"]["access"] = {"type": "zhipu-account"}
        self.personal.write_text(json.dumps(value))
        context = self.context()
        with self.assertRaises(BoardError) as error:
            self.adapter.prepare(context)
        self.assertEqual(error.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertFalse(context.directory.exists())

    def test_coding_never_falls_back_to_native_defaults(self):
        from hey_my_buddy.errors import BoardError
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
            result = self.description.discover_models()
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
            result = self.description.discover_models()
        self.assertEqual(result["providers"][0]["models"][0]["efforts"], ["low", "high"])
        self.assertEqual(result["providers"][0]["adapter"], "zcode")
        self.assertEqual(result["providers"][0]["packageVersion"], "fixture-0.16.9")
        self.assertEqual(result["providers"][0]["models"][0]["contextWindow"], 200000)
        self.assertEqual(result["providers"][0]["models"][0]["inputModalities"], ["text"])
        self.assertTrue(result["providers"][0]["models"][0]["available"])
        self.assertNotIn("fixture-secret-never-public", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
