"""Codex adapter lifecycle against a private protocol fixture, with no model calls."""
from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from buddy.adapters.base import ExecutionContext
from buddy.adapters.codex import CodexAdapter

FIXTURE = Path(__file__).parent / "fixtures/mock_codex.py"


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-codex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        self.home = self.root / 'native-account'
        self.home.mkdir()
        FIXTURE.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CODEX_CLI=str(FIXTURE), BUDDY_CODEX_FIXTURE_STATE=str(self.root / "fixture.json"),
                                CODEX_HOME=str(self.home), HOME=str(self.root),
                                BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1")
        self.adapter = CodexAdapter()

    def context(self, case="ok", *, index=1, previous=None, mode=None, effort="low", timeout=8):
        turn_input = {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": mode or ("native-session" if previous else "initial"),
                      "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "Write a fixture result", "timeoutSeconds": timeout,
                                      "provider": "openai", "model": "fixture-model", "effort": effort},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_CODEX_FIXTURE_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, context):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def test_completed_native_turn_has_structured_provenance_and_activity(self):
        context = self.context()
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["nativeSession"]["nativeAppVisibility"], "not-listed-in-native-app")
        self.assertEqual(outcome.result["nativeSession"]["storageScope"], "buddy-goal-private")
        self.assertTrue(outcome.result["nativeSession"]["resumable"])
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["nativeThreadId"], turn["sessionId"])
        self.assertEqual(turn["provenance"]["nativeTurnId"], "native-turn-1")
        self.assertNotIn("tool", turn["provenance"])
        activity = json.loads((context.directory / "activity.json").read_text())
        self.assertEqual(activity["attemptId"], context.attempt_id)
        self.assertEqual(activity["activity"]["phase"], "finishing")

    def test_assistance_uses_the_same_strict_outcome_schema(self):
        outcome = self.execute(self.context("assistance"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")

    def test_coding_home_is_private_per_goal_and_keeps_source_auth_untouched(self):
        from buddy.private_dirs import native_root
        auth = self.home / 'auth.json'
        auth.write_text('fixture login')
        context = self.context()
        first = self.execute(context)
        self.assertEqual(first.status, 'ok', first.to_report())
        home = native_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex', context.task_id) / 'codex-home'
        stored = json.loads(Path(self.environment['BUDDY_CODEX_FIXTURE_STATE']).read_text())
        thread = stored['threads'][first.result['sessionId']]
        self.assertEqual(thread['codexHome'], str(home))
        self.assertEqual(thread['sqliteHome'], str(home))
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
        self.assertFalse((home / 'auth.json').exists())
        self.assertFalse((home / 'auth.json').is_symlink())
        self.assertEqual(auth.read_text(), 'fixture login')
        other = self.context(index=2)
        other.task_id = other.turn_input['taskId'] = 'other-goal'
        second = self.execute(other)
        self.assertEqual(second.status, 'ok', second.to_report())
        stored = json.loads(Path(self.environment['BUDDY_CODEX_FIXTURE_STATE']).read_text())
        self.assertNotEqual(stored['threads'][second.result['sessionId']]['codexHome'], str(home))

    def test_resumed_thread_reuses_home_and_recreates_only_a_private_auth_link(self):
        from buddy.private_dirs import native_root
        (self.home / 'auth.json').write_text('fixture login')
        first = self.execute(self.context())
        home = native_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex', 'goal-1') / 'codex-home'
        marker = home / 'retained-native-state'
        marker.write_text('native state')
        second = self.execute(self.context(index=2, previous=first.result['sessionId']))
        self.assertEqual(second.status, 'ok', second.to_report())
        self.assertEqual(second.result['sessionId'], first.result['sessionId'])
        self.assertEqual(marker.read_text(), 'native state')
        self.assertFalse((home / 'auth.json').is_symlink())

    def test_selected_worker_account_never_falls_back_to_native_credentials(self):
        from buddy.private_dirs import account_root
        (self.home / 'auth.json').write_text('shared login')
        context = self.context()
        context.runtime['workerAccount'] = {'source': 'worker', 'revision': 1}
        root = account_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex')
        root.mkdir(parents=True)
        first = self.execute(context)
        self.assertEqual(first.status, 'failed')
        self.assertEqual(first.result['code'], 'codex-account-unavailable')
        self.assertFalse(Path(self.environment['BUDDY_CODEX_FIXTURE_STATE']).exists())
        (root / 'auth.json').write_text('worker login')
        second_context = self.context(index=2)
        second_context.runtime['workerAccount'] = dict(context.runtime['workerAccount'])
        second = self.execute(second_context)
        self.assertEqual(second.status, 'ok', second.to_report())
        control = json.loads((second_context.directory / 'codex-control.json').read_text())
        self.assertEqual(control['credentialSource']['home'], str(root))
        changed = self.context(index=3, previous=second.result['sessionId'])
        changed.runtime['workerAccount'] = {'source': 'worker', 'revision': 2}
        third = self.execute(changed)
        self.assertEqual(third.result['code'], 'native-resume-unavailable')
        self.assertEqual((root / 'auth.json').read_text(), 'worker login')

    def test_previous_unsettled_auth_is_retained_and_cannot_be_replaced(self):
        from buddy.private_dirs import native_root
        root = native_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex', 'goal-1')
        home = root / 'codex-home'
        home.mkdir(parents=True)
        source = self.home / 'auth.json'
        source.write_text('source login')
        (home / 'auth.json').symlink_to(source)
        outcome = self.execute(self.context())
        self.assertEqual(outcome.status, 'failed')
        self.assertEqual(outcome.result['code'], 'codex-credential-retained')
        self.assertTrue((home / 'auth.json').is_symlink())
        self.assertFalse(Path(self.environment['BUDDY_CODEX_FIXTURE_STATE']).exists())

    def test_unconfirmed_stop_retains_goal_auth_and_confirmed_cleanup_handles_refresh(self):
        from buddy.private_dirs import native_root
        from buddy.adapters.codex_home import remove_coding_auth
        context = self.context()
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(15))
        home = native_root(Path(self.environment['BUDDY_STATE_DIR']), 'codex', 'goal-1') / 'codex-home'
        # A native refresh can replace a login link with an attempt-private file.
        (home / 'auth.json').write_text('refreshed private login')
        result = json.loads(Path(handle.log_paths['stdout']).read_text())
        result['processState']['shutdownConfirmed'] = False
        Path(handle.log_paths['stdout']).write_text(json.dumps(result))
        outcome = self.adapter.collect(handle, context)
        self.assertFalse(outcome.shutdown_confirmed)
        self.assertTrue((home / 'auth.json').exists())
        self.assertEqual(remove_coding_auth(home.parent), 1)
        self.assertFalse((home / 'auth.json').exists())

    def test_cleanup_failure_preserves_the_completed_turn_and_stop_evidence(self):
        context = self.context()
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(15))
        with mock.patch('buddy.adapters.codex_home.remove_coding_auth', side_effect=OSError('fixture cleanup failure')):
            outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, 'ok', outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result['turn']['outcome']['disposition'], 'completed')
        self.assertEqual(outcome.result['credentialCleanup'], {'complete': False, 'error': 'filesystem-error'})

    def test_pinned_credential_operations_never_follow_a_replaced_parent(self):
        from contextlib import contextmanager
        from buddy.adapters import codex_home
        from buddy.errors import BoardError
        real_pin = codex_home._pinned_home
        source = self.home / 'auth.json'
        source.write_text('source login')
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'auth.json'
        sentinel.write_text('unrelated login')
        for operation in ('create', 'remove'):
            root = self.root / operation
            home = root / 'codex-home'
            home.mkdir(parents=True)
            if operation == 'remove':
                (home / 'auth.json').symlink_to(source)
            @contextmanager
            def replaced(path):
                with real_pin(path) as fd:
                    path.rename(root / 'pinned-old-home')
                    path.symlink_to(outside, target_is_directory=True)
                    yield fd
            with mock.patch.object(codex_home, '_pinned_home', replaced), self.assertRaises(BoardError):
                if operation == 'create':
                    codex_home.prepare_coding_home(root, {'home': str(self.home), 'source': 'native'})
                else:
                    codex_home.remove_coding_auth(root)
            self.assertEqual(sentinel.read_text(), 'unrelated login')
            self.assertEqual(source.read_text(), 'source login')

    def test_known_native_launch_failure_cleans_the_current_private_auth(self):
        import threading
        from buddy.adapters import codex_runner
        from buddy.private_dirs import native_root
        (self.home / 'auth.json').write_text('source login')
        context = self.context()
        self.adapter.prepare(context)
        control = json.loads((context.directory / 'codex-control.json').read_text())
        with mock.patch.dict(os.environ, context.environment, clear=True), \
             mock.patch.object(codex_runner, 'owned_popen', side_effect=OSError('not started')):
            payload, code = codex_runner._run(control, threading.Event())
        self.assertEqual(code, 1)
        self.assertFalse(payload['modelStarted'])
        self.assertTrue(payload['processState']['shutdownConfirmed'])
        Path(context.log_paths()['stdout']).write_text(json.dumps(payload))
        fake = mock.Mock(process=mock.Mock(returncode=1), log_paths=context.log_paths())
        fake.shutdown_confirmed.return_value = True
        outcome = self.adapter.collect(fake, context)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertFalse((native_root(Path(context.environment['BUDDY_STATE_DIR']), 'codex', 'goal-1') / 'codex-home/auth.json').is_symlink())
        self.assertEqual((self.home / 'auth.json').read_text(), 'source login')

    def test_long_report_survives_native_collection_and_remains_resumable(self):
        first = self.execute(self.context("long-summary-citation"))
        self.assertEqual(first.status, "ok", first.to_report())
        summary = first.result["turn"]["outcome"]["summary"]
        self.assertGreater(len(summary.encode()), 8000)
        self.assertTrue(summary.endswith("</oai-mem-citation>"))
        second = self.execute(self.context(index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["sessionId"], first.result["sessionId"])

    def test_completed_result_cannot_also_request_assistance(self):
        outcome = self.execute(self.context("completed-request"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "invalid-result")
        self.assertIn("sessionId", outcome.result)
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(outcome.result["nativeSession"]["resumable"])

    def test_invalid_result_retains_bound_native_history_for_explicit_continuation(self):
        first = self.execute(self.context("invalid-json"))
        self.assertEqual(first.status, "failed")
        self.assertNotIn("turn", first.result)
        checkpoint = first.result["nativeCheckpoint"]
        self.assertEqual(checkpoint["lastAssistantMessage"]["text"], "not json")
        self.assertTrue(checkpoint["bindingSaved"])
        self.assertTrue(first.result["nativeSession"]["resumable"])
        second = self.execute(self.context(index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["nativeTurnId"], "native-turn-2")

    def test_failed_native_turn_retains_message_without_resume_authority(self):
        first = self.execute(self.context("failed"))
        self.assertEqual(first.status, "failed")
        self.assertIn("fixture work completed", first.result["nativeCheckpoint"]["lastAssistantMessage"]["text"])
        self.assertFalse(first.result["nativeSession"]["resumable"])
        second = self.execute(self.context(index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.result["code"], "native-resume-unavailable")

    def test_disconnect_retains_completed_message_without_inventing_native_completion(self):
        outcome = self.execute(self.context("disconnect-after-message"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "transport-error")
        self.assertTrue(outcome.shutdown_confirmed)
        checkpoint = outcome.result["nativeCheckpoint"]
        self.assertEqual(checkpoint["nativeTurnStatus"], "incomplete")
        self.assertIn("fixture work completed", checkpoint["lastAssistantMessage"]["text"])
        self.assertFalse(outcome.result["nativeSession"]["resumable"])

    def test_changed_native_history_is_not_resumed_after_invalid_result(self):
        first = self.execute(self.context("invalid-json"))
        state_path = Path(self.environment["BUDDY_CODEX_FIXTURE_STATE"])
        state = json.loads(state_path.read_text())
        state["threads"][first.result["sessionId"]]["turns"].append({"id": "foreign-turn", "status": "completed"})
        state_path.write_text(json.dumps(state))
        second = self.execute(self.context(index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.status, "failed")
        self.assertEqual(second.result["code"], "native-resume-unavailable")

    def test_native_resume_requires_matching_private_binding_and_history(self):
        first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        thread_id = first.result["turn"]["sessionId"]
        second = self.execute(self.context(index=2, previous=thread_id))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["turn"]["sessionId"], thread_id)
        changed = self.execute(self.context(index=3, previous=thread_id, effort="high"))
        self.assertEqual(changed.status, "failed")
        self.assertEqual(changed.result["code"], "native-resume-unavailable")
        reconstructed = self.execute(self.context(index=4, previous=thread_id, mode="reconstructed-new-session", effort="high"))
        self.assertEqual(reconstructed.status, "ok", reconstructed.to_report())
        self.assertNotEqual(reconstructed.result["turn"]["sessionId"], thread_id)

    def test_api_key_auth_never_falls_back_to_billed_execution(self):
        outcome = self.execute(self.context("api-key"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "account-plan-required")
        self.assertNotIn("turn", outcome.result)

    def test_capability_readiness_does_not_start_a_native_probe(self):
        with mock.patch("buddy.adapters.codex.cli_command", return_value=[str(FIXTURE)]), \
             mock.patch.object(CodexAdapter, "discover_models", side_effect=AssertionError("native probe")):
            self.assertEqual(self.adapter.available(), (True, None))

    def test_complete_empty_native_catalog_is_not_a_discovery_failure(self):
        with mock.patch.dict(os.environ, {**self.environment, "BUDDY_CODEX_FIXTURE_CASE": "empty-catalog"}, clear=True):
            catalog = self.adapter.discover_models()
        self.assertEqual(catalog["providers"][0]["models"], [])
        self.assertFalse((self.root / "fixture.json").exists(), "discovery must not start a thread")

    def test_inherited_api_key_is_removed_before_native_account_check(self):
        context = self.context()
        context.environment["OPENAI_API_KEY"] = "fixture-secret"
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertNotIn("fixture-secret", json.dumps(outcome.to_report()))

    def test_attempt_scoped_credential_stays_private_and_out_of_result(self):
        context = self.context()
        context.agent_credential = "private-attempt-secret"
        outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(stat.S_IMODE(context.credential_file().stat().st_mode), 0o600)
        self.assertEqual(context.environment["BUDDY_AGENT_CREDENTIAL_FILE"], str(context.credential_file()))
        self.assertNotIn("private-attempt-secret", json.dumps(outcome.to_report()))

    def test_invalid_final_or_failed_native_turn_cannot_be_imported(self):
        for index, case in enumerate(("invalid-json", "no-final", "failed"), 1):
            with self.subTest(case=case):
                outcome = self.execute(self.context(case, index=index))
                self.assertEqual(outcome.status, "failed", outcome.to_report())
                self.assertNotIn("turn", outcome.result)

    def test_correlated_denied_request_yields_honest_controller_attention(self):
        outcome = self.execute(self.context("approval"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "attention")
        provenance = turn["provenance"]
        self.assertTrue(provenance["controllerAttention"])
        self.assertFalse(provenance["outputSchemaValidated"])
        self.assertEqual(provenance["nativeRequestThreadId"], turn["sessionId"])
        self.assertEqual(provenance["nativeRequestTurnId"], provenance["nativeTurnId"])
        self.assertEqual(provenance["nativeRequestMethod"], "item/commandExecution/requestApproval")
        forged = {**turn, "provenance": {**provenance, "nativeRequestTurnId": "unrelated-turn"}}
        self.assertIsNotNone(self.adapter.validate_turn_provenance(forged))

    def test_denied_request_with_failed_native_turn_is_not_a_completed_attention(self):
        outcome = self.execute(self.context("approval-failed"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertNotIn("turn", outcome.result)

    def test_deadline_ends_native_process_group(self):
        outcome = self.execute(self.context("hang", timeout=1))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "deadline")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_zero_timeout_executes_unlimited_without_an_immediate_deadline(self):
        context = self.context("ok", timeout=0)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertEqual(handle.deadline, math.inf)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")

    def test_zero_timeout_still_honors_explicit_user_cancellation(self):
        context = self.context("hang", timeout=0)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertEqual(handle.deadline, math.inf)
        time.sleep(0.3)
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(10))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)

    def test_positive_timeout_keeps_a_finite_stamped_deadline(self):
        context = self.context(timeout=8)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertGreater(handle.deadline, time.monotonic())
        self.assertFalse(math.isinf(handle.deadline))

    def test_user_cancel_interrupts_the_owned_native_turn(self):
        context = self.context("hang", timeout=12)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        time.sleep(0.3)
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(10))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)


    def test_router_certificate_follows_the_selected_native_version_without_a_probe(self):
        from buddy.harness_runtime import bound
        import sys
        with bound([{'adapter': 'codex', 'status': 'ready', 'version': '0.157.0'}]):
            self.assertEqual(self.adapter.read_only_structured_verified, sys.platform == 'darwin')
        with bound([{'adapter': 'codex', 'status': 'ready', 'version': '0.158.0'}]):
            self.assertFalse(self.adapter.read_only_structured_verified)
            self.assertTrue(self.adapter.available()[0])

    def test_read_only_refuses_an_unacknowledged_policy_before_model_input(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        context = self.context('readonly-policy-mismatch')
        context.turn = None
        request = ReadOnlyStructuredRequest(str(self.cwd), 'No model work before policy acknowledgement', answer_schema(['legal']), budget(), capture_evidence=True)
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.result['code'], 'readonly-policy-unverified')
        self.assertIn('nativeConfigPolicy', result.result)
        self.assertIn('nativePolicy', result.result)
        self.assertIs(result.result['modelStarted'], False)
        self.assertTrue(result.shutdown_confirmed)

    def test_failed_config_readback_survives_without_a_model_call(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        context = self.context('readonly-config-mismatch')
        context.turn = None
        request = ReadOnlyStructuredRequest(str(self.cwd), 'No model input before verified policy', answer_schema(['legal']), budget(), capture_evidence=True)
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.result['code'], 'readonly-policy-unverified')
        self.assertIs(result.result['modelStarted'], False)
        self.assertIs(result.result['nativeConfigPolicy']['features']['apps'], True)
        self.assertTrue(result.shutdown_confirmed)

    def test_native_controller_probe_uses_the_same_profile_and_fixed_targets(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        from buddy.sandbox_probe import results
        context = self.context()
        context.turn = None
        (self.cwd / 'marker.txt').write_text('private-marker\n')
        sentinel = self.root / ('outside-' + 'a' * 32 + '.txt')
        sentinel.write_text('private-sentinel\n')
        request = ReadOnlyStructuredRequest(str(self.cwd.resolve()), 'fixture', answer_schema(['legal']), budget(),
            capture_evidence=True, native_probe={'sentinel': str(sentinel.resolve()), 'url': 'http://127.0.0.1:12345/'})
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        outcome = collect(handle)
        self.assertTrue(results(outcome.result['nativeSandboxProbes'])[1])
        self.assertEqual(len(json.loads((self.root/'fixture.json').read_text())['sandboxProbes']), 5)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(sentinel.read_text(), 'private-sentinel\n')

    def test_unrelated_native_probe_response_fails_before_model_start(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        context = self.context('probe-unrelated-reply')
        context.turn = None
        (self.cwd / 'marker.txt').write_text('private-marker\n')
        sentinel = self.root / ('outside-' + 'b' * 32 + '.txt')
        sentinel.write_text('private-sentinel\n')
        request = ReadOnlyStructuredRequest(str(self.cwd.resolve()), 'fixture', answer_schema(['legal']), budget(),
            capture_evidence=True, native_probe={'sentinel': str(sentinel.resolve()), 'url': 'http://127.0.0.1:12345/'})
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        outcome = collect(handle)
        self.assertFalse(outcome.result['modelStarted'])
        self.assertEqual(outcome.result['code'], 'invalid-protocol')
        self.assertTrue(outcome.shutdown_confirmed)

    def test_generic_read_only_call_has_no_workflow_turn_or_agent_credential(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        context = self.context()
        context.turn = None
        context.agent_credential = "must-not-reach-native"
        context.environment["BUDDY_AGENT_CREDENTIAL"] = "must-not-reach-native"
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet", answer_schema(["legal"]), budget())
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.status, "ok", result.result)
        self.assertTrue(result.shutdown_confirmed)
        raw = result.result["rawAnswer"]
        answer = json.loads(raw) if isinstance(raw, str) else raw
        self.assertEqual(answer["profileId"], "legal")
        self.assertIsNone(result.result["usage"]["bytesRead"])
        self.assertFalse(context.turn_output_file().exists())

    def test_read_only_shutdown_removes_only_private_auth_link(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        account_home = self.root / 'account-home'
        account_home.mkdir()
        source_auth = account_home / 'auth.json'
        source_auth.write_text('private fixture auth')
        context = self.context(index=42)
        context.turn = None
        context.environment['CODEX_HOME'] = str(account_home)
        request = ReadOnlyStructuredRequest(str(self.cwd), 'Select', answer_schema(['legal']), budget())
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.status, 'ok', result.result)
        self.assertTrue(result.shutdown_confirmed)
        from buddy.private_dirs import context_root
        self.assertFalse((context_root(context, "codex") / 'review-native/codex-home/auth.json').is_symlink())
        self.assertEqual(source_auth.read_text(), 'private fixture auth')

    def test_auth_cleanup_preserves_a_regular_file(self):
        from buddy.adapters.codex_runner import _remove_private_auth
        home = self.root / 'native/codex-home'
        home.mkdir(parents=True)
        auth = home / 'auth.json'
        auth.write_text('retain regular file')
        _remove_private_auth(self.root / 'native')
        self.assertEqual(auth.read_text(), 'retain regular file')


    def test_router_budget_interrupts_and_keeps_unknown_read_bytes(self):
        from buddy.adapters.decision import DecisionAdapter
        from buddy.router import answer_schema, budget
        context = self.context('readonly-budget')
        context.turn = None
        context.decision_input = {
            'profile': {'adapter': 'codex', 'provider': 'openai', 'model': 'fixture-model', 'effort': 'low'},
            'profiles': [{'profileId': 'legal'}], 'task': 'Select', 'tableRevision': 1,
            # A zero-call budget interrupts the fixture's first native tool call.
            'budget': {**budget(), 'toolCalls': 0}, 'outputSchema': answer_schema(['legal']),
        }
        adapter = DecisionAdapter()
        with mock.patch.object(CodexAdapter, 'read_only_structured_verified', True):
            handle = adapter.start(context)
            self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
            self.assertIsNotNone(handle.wait(20))
            outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, 'failed', outcome.result)
        self.assertEqual(outcome.result['code'], 'router-budget-exhausted')
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result['stopEvidence']['nativeInterruptAcknowledged'])
        self.assertIsNone(outcome.result['usage']['bytesRead'])
        self.assertEqual(outcome.result['usage']['toolCalls'], 1)

    def test_denied_raw_tool_call_still_consumes_router_budget(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        context = self.context('readonly-denied-budget')
        context.turn = None
        request = ReadOnlyStructuredRequest(str(self.cwd), 'Select', answer_schema(['legal']),
                                             {**budget(), 'toolCalls': 0}, capture_evidence=True)
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.result['code'], 'readonly-budget-exhausted')
        self.assertEqual(result.result['usage']['toolCalls'], 1)
        self.assertTrue(result.shutdown_confirmed)
        self.assertEqual(result.result['nativeRawToolEvents'][0]['params']['item']['call_id'], 'denied-1')


    def test_readonly_repairs_format_once_in_same_thread_but_not_bounds(self):
        from buddy.adapters.base import ReadOnlyStructuredRequest
        from buddy.adapters.read_only import collect
        from buddy.router import answer_schema, budget
        for index, (case, calls) in enumerate((('readonly-repair', 2), ('readonly-outside', 1)), 1):
            with self.subTest(case=case):
                context = self.context(case, index=index)
                context.turn = None
                request = ReadOnlyStructuredRequest(str(self.cwd), 'Select', answer_schema(['legal']), budget())
                handle = self.adapter.start_read_only_structured(context, request)
                self.addCleanup(lambda h=handle: h.terminate(grace_seconds=0.2) if h.group_alive() else None)
                self.assertIsNotNone(handle.wait(20))
                result = collect(handle)
                self.assertEqual(result.status, 'ok', result.result)
                self.assertEqual(result.result['correctionCount'], calls - 1)
                state = json.loads((self.root / 'fixture.json').read_text())
                self.assertEqual(len(state['threads'][result.result['sessionId']]['turns']), calls)


if __name__ == "__main__":
    unittest.main()
