"""Codex worker and review lifecycle through the registered role seam, with no model calls.

The micro task Worker turn runs through the shared role controller and the
registered Codex run module (``worker_executor("codex")``); the review calls go
through ``start_router_preparation`` exactly as the Router's DecisionAdapter
starts them. The offline ``mock_codex.py`` App Server stands in for the native
process; no paid model is reached.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from buddy.harnesses.session_record_support import notifications
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.roles import controller
from hey_my_buddy.buddy.roles.controller import ReviewPreparation, start_router_preparation, worker_executor

FIXTURE = Path(__file__).parent / "fixtures/mock_codex.py"
SOURCE = Path(__file__).resolve().parents[5] / "src"


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        developer_source = mock.patch.dict(os.environ, {'HMB_DEV_SOURCE': '1'})
        developer_source.start()
        self.addCleanup(developer_source.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-codex-test-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir(mode=0o700)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        self.home = self.root / 'native-account'
        self.home.mkdir()
        FIXTURE.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("HMB_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(HMB_CONSOLE_PORT="0", HMB_CODEX_CLI=str(FIXTURE), HMB_CODEX_FIXTURE_STATE=str(self.root / "fixture.json"),
                                CODEX_HOME=str(self.home), HOME=str(self.root),
                                HMB_STATE_DIR=str(self.root / "state"), HMB_RUNTIME_ROOT=str(self.root / "runtime"),
                                HMB_DEV_SOURCE="1",
                                PYTHONPATH=str(SOURCE) + os.pathsep + self.environment.get("PYTHONPATH", ""))
        self.adapter = worker_executor("codex")

    def context(self, case="ok", *, index=1, previous=None, mode=None, effort="low", timeout=8):
        turn_input = {"version": 1, "taskId": "micro-task-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": mode or ("native-session" if previous else "initial"),
                      "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(session_record_path=str(self.root / f"session-{index}.jsonl"), task_id="micro-task-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "Write a fixture result", "expectedSeconds": 1,
                                      **({"routerBudgetSeconds": timeout} if timeout is not None else {}),
                                      "provider": "openai", "model": "fixture-model", "effort": effort},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "HMB_CODEX_FIXTURE_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, context):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def wait_native_turn_start(self, timeout=10):
        """Bounded wait for the fixture's explicit native turn-start fact.

        The marker is written only after the fixture actually emitted the
        turn/start response and the turn/started notification, so waiting on it
        proves the native turn had started before the cancel — never a blind
        sleep, and never a thread merely existing.
        """
        path = Path(self.environment["HMB_CODEX_FIXTURE_STATE"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = json.loads(path.read_text()) if path.is_file() else {}
            except ValueError:  # a state write in flight; retry within the bound
                state = {}
            for thread in (state.get("threads") or {}).values():
                started = thread.get("turnStart")
                if isinstance(started, dict) and started.get("threadId") and started.get("turnId"):
                    return started
            time.sleep(0.05)
        self.fail("the fixture never recorded a started native turn before the cancel")

    def review(self, case, *, index, budget_options=None, capture=True):
        from hey_my_buddy.buddy.roles.structured_call import collect
        from hey_my_buddy.blackboard.routing.router import answer_schema, budget
        context = self.context(case, index=index)
        context.turn = None
        context.agent_credential = None
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet", answer_schema(["legal"]),
                                            {**budget(), **(budget_options or {})}, capture_evidence=capture)
        handle = start_router_preparation(ReviewPreparation("codex", CodexAdapter(), request, context,
                                                            (None, self.cwd, "fixture-digest")))
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(30), "Codex fixture controller did not exit")
        return collect(handle), context

    def test_completed_native_turn_has_structured_provenance_and_preserved_final_activity(self):
        context = self.context()
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["nativeSession"]["nativeAppVisibility"], "listed-in-native-app")
        self.assertEqual(outcome.result["nativeSession"]["storageScope"], "harness-user-store")
        self.assertEqual(outcome.result["nativeSession"]["storageOwner"], "harness-user-store")
        self.assertTrue(outcome.result["nativeSession"]["resumable"])
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["nativeThreadId"], turn["sessionId"])
        self.assertEqual(turn["provenance"]["nativeTurnId"], "native-turn-1")
        self.assertNotIn("tool", turn["provenance"])
        # The retired activity.json sidecar is replaced by the run's own final
        # fact: the preserved RunResult frame the controller published, decoded
        # here from its own stdout log. It is a final fact, not a process
        # observation: the counts and the native session id below are the ones
        # the native events actually drove, and no publication success is
        # claimed for the live channel (live observation is the holder's
        # C-Two binding, exercised by the zcode live activity suite).
        self.assertFalse((context.directory / "activity.json").exists())
        from hey_my_buddy.buddy.harnesses.run_contract import RunResult
        frame = handle.result
        self.assertEqual(frame.identity.attempt_id, context.attempt_id)
        self.assertIsNotNone(frame.activity, "the run result carries no preserved activity")
        self.assertEqual(frame.identity.task_id, "micro-task-1")
        activity = frame.activity.value
        self.assertEqual(activity["phase"], "finishing")
        self.assertEqual(activity["nativeSessionId"], turn["sessionId"])
        self.assertEqual(activity["counts"], {"modelTurns": 1, "toolCalls": 0})
        self.assertEqual(outcome.result["activity"], {"phase": "finishing",
                                                      "eventSeq": activity["eventSeq"]})

    def test_assistance_uses_the_same_strict_outcome_schema(self):
        outcome = self.execute(self.context("assistance"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")

    def test_last_completed_final_answer_wins_and_superseded_facts_reach_receipt(self):
        cases = (("final-invalid-then-valid", False), ("final-valid-then-valid", True),
                 ("ok", None), ("final-last-invalid", True))
        for index, (case, parsable) in enumerate(cases, 1):
            with self.subTest(case=case):
                outcome = self.execute(self.context(case, index=index))
                self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
                invalid = case == "final-last-invalid"
                self.assertEqual(outcome.status, "failed" if invalid else "ok", outcome.to_report())
                checkpoint = outcome.result["nativeCheckpoint"]
                if invalid:
                    self.assertEqual(outcome.result["code"], "invalid-result")
                    self.assertEqual(checkpoint["lastAssistantMessage"]["text"], "not json")
                else:
                    turn = outcome.result["turn"]
                    self.assertEqual(turn["outcome"]["summary"], "fixture work completed")
                    self.assertEqual(turn["provenance"]["finalItemId"], "final-1" if case == "ok" else "final-2")
                if parsable is None:
                    self.assertEqual(checkpoint["supersededFinalAnswers"], {"count": 0, "records": []})
                    continue
                state = json.loads(Path(self.environment["HMB_CODEX_FIXTURE_STATE"]).read_text())
                native_turn = state["threads"][outcome.result["sessionId"]]["turns"][-1]
                raw = native_turn["items"][0]["text"].encode("utf-8")
                self.assertEqual(checkpoint["supersededFinalAnswers"], {"count": 1, "records": [{
                    "itemId": "final-1", "sourceBytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(), "jsonParsable": parsable}]})

    def test_an_unlisted_model_reaches_the_native_turn_and_its_public_fact_reaches_the_receipt(self):
        # The full micro task chain: the relaxed check hands the selected name to
        # the native turn, the native rejection (message retained) is the
        # failure, and the absence fact lands on the coding receipt beside the
        # selected identity.
        outcome = self.execute(self.context("unlisted-model"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "native-rpc-error")
        self.assertEqual(outcome.result["error"],
                         "Codex rejected turn/start: model not found: fixture-model")
        self.assertIs(outcome.result["selectedModelListed"], False)
        self.assertEqual(outcome.result["selectedModel"],
                         {"provider": "openai", "model": "fixture-model", "effort": "low"})

    def test_a_long_multibyte_native_error_keeps_its_reason_in_the_public_receipt(self):
        # The micro task receipt carries the native reason past the public byte
        # budget: cut on UTF-8 byte safety, prefix and readable CJK intact,
        # never erased — with the absence fact still beside it.
        outcome = self.execute(self.context("long-native-error"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "native-rpc-error")
        error = outcome.result["error"]
        self.assertIsNotNone(error)
        self.assertLessEqual(len(error.encode("utf-8")), 512)
        self.assertTrue(error.startswith("Codex rejected turn/start: 模型不可用"), error)
        self.assertNotIn("\N{REPLACEMENT CHARACTER}", error)
        self.assertIs(outcome.result["selectedModelListed"], False)
        self.assertEqual(outcome.result["selectedModel"],
                         {"provider": "openai", "model": "fixture-model", "effort": "low"})

    def test_known_native_launch_failure_leaves_user_auth_untouched(self):
        import stat as stat_module
        (self.home / 'auth.json').write_text('source login')
        broken = self.root / 'broken-codex'
        broken.write_text('not an executable payload')
        broken.chmod(stat_module.S_IXUSR | stat_module.S_IRUSR)
        context = self.context()
        context.environment["HMB_CODEX_CLI"] = str(broken)
        outcome = self.execute(context)
        self.assertEqual(outcome.status, 'failed', outcome.to_report())
        self.assertIs(outcome.result['modelStarted'], False)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertFalse(list(Path(context.environment['HMB_STATE_DIR']).rglob('codex-home')))
        self.assertEqual((self.home / 'auth.json').read_text(), 'source login')

    def test_worker_inherits_native_home_and_preserves_user_configuration(self):
        auth = self.home / 'auth.json'
        auth.write_text('fixture login')
        config = self.home / 'config.toml'
        config.write_text('model_provider = "fixture-provider"\n[features]\napps = true\n')
        context = self.context()
        context.spec['provider'] = 'fixture-provider'
        outcome = self.execute(context)
        self.assertEqual(outcome.status, 'ok', outcome.to_report())
        state = json.loads(Path(self.environment['HMB_CODEX_FIXTURE_STATE']).read_text())
        thread = state['threads'][outcome.result['sessionId']]
        self.assertEqual(thread['codexHome'], str(self.home))
        self.assertEqual(thread['home'], self.environment['HOME'])
        self.assertIsNone(thread['sqliteHome'])
        self.assertEqual(thread['start']['modelProvider'], 'fixture-provider')
        self.assertNotIn('ephemeral', thread['start'])
        self.assertFalse(list(Path(self.environment['HMB_STATE_DIR']).rglob('codex-home')))
        self.assertEqual(auth.read_text(), 'fixture login')
        self.assertFalse(auth.is_symlink())
        self.assertEqual(config.read_text(), 'model_provider = "fixture-provider"\n[features]\napps = true\n')

    def test_native_binding_contains_only_current_task_and_session_facts(self):
        from hey_my_buddy.private_dirs import native_root
        from hey_my_buddy.buddy.harnesses.codex.native_run import _binding_path
        first = self.execute(self.context())
        self.assertEqual(first.status, 'ok', first.to_report())
        path = _binding_path(native_root(Path(self.environment['HMB_STATE_DIR']), 'codex', 'micro-task-1'),
                             first.result['sessionId'])
        binding = json.loads(path.read_text())
        self.assertEqual(set(binding), {'taskId', 'threadId', 'cwd', 'buddy',
                                        'lastTurnId', 'lastAttemptId', 'lastInputSha256'})
        second = self.execute(self.context(index=2, previous=first.result['sessionId']))
        self.assertEqual(second.status, 'ok', second.to_report())
        self.assertEqual(second.result['sessionId'], first.result['sessionId'])
        binding = json.loads(path.read_text())
        binding.update(codexHome='retired-private-home', credentialSource={'source': 'native'})
        path.write_text(json.dumps(binding))
        refused = self.execute(self.context(index=3, previous=first.result['sessionId']))
        self.assertEqual(refused.result['code'], 'native-resume-unavailable')
        self.assertIs(refused.result['modelStarted'], False)
        rebuilt = self.execute(self.context(index=4, previous=first.result['sessionId'],
                                            mode='reconstructed-new-session'))
        self.assertEqual(rebuilt.status, 'ok', rebuilt.to_report())
        self.assertNotEqual(rebuilt.result['sessionId'], first.result['sessionId'])

    def test_discovery_reports_user_configured_provider(self):
        (self.home / 'config.toml').write_text('model_provider = "fixture-provider"\n')
        with mock.patch.dict(os.environ, self.environment, clear=True):
            catalog = CodexAdapter().discover_models()
        self.assertEqual([provider['provider'] for provider in catalog['providers']], ['fixture-provider'])
        self.assertEqual(catalog['providers'][0]['models'][0]['id'], 'fixture-model')

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
        self.assertIs(second.result["modelStarted"], False)

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
        state_path = Path(self.environment['HMB_CODEX_FIXTURE_STATE'])
        state = json.loads(state_path.read_text())
        state["threads"][first.result["sessionId"]]["turns"].append({"id": "foreign-turn", "status": "completed"})
        state_path.write_text(json.dumps(state))
        second = self.execute(self.context(index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.status, "failed")
        self.assertEqual(second.result["code"], "native-resume-unavailable")
        self.assertIs(second.result["modelStarted"], False)

    def test_native_resume_requires_matching_task_binding_and_history(self):
        first = self.execute(self.context())
        self.assertEqual(first.status, "ok", first.to_report())
        thread_id = first.result["turn"]["sessionId"]
        second = self.execute(self.context(index=2, previous=thread_id))
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["turn"]["sessionId"], thread_id)
        changed = self.execute(self.context(index=3, previous=thread_id, effort="high"))
        self.assertEqual(changed.status, "failed")
        self.assertEqual(changed.result["code"], "native-resume-unavailable")
        self.assertIs(changed.result["modelStarted"], False)
        reconstructed = self.execute(self.context(index=4, previous=thread_id, mode="reconstructed-new-session", effort="high"))
        self.assertEqual(reconstructed.status, "ok", reconstructed.to_report())
        self.assertNotEqual(reconstructed.result["turn"]["sessionId"], thread_id)

    def test_api_key_auth_is_allowed_to_execute(self):
        outcome = self.execute(self.context("api-key"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")

    def test_capability_readiness_does_not_start_a_native_probe(self):
        with mock.patch("hey_my_buddy.buddy.harnesses.codex.adapter.cli_command", return_value=[str(FIXTURE)]), \
             mock.patch.object(CodexAdapter, "discover_models", side_effect=AssertionError("native probe")):
            self.assertEqual(self.adapter.available(), (True, None))

    def test_complete_empty_native_catalog_is_not_a_discovery_failure(self):
        with mock.patch.dict(os.environ, {**self.environment, "HMB_CODEX_FIXTURE_CASE": "empty-catalog"}, clear=True):
            catalog = CodexAdapter().discover_models()
        self.assertEqual(catalog["providers"][0]["models"], [])
        self.assertFalse((self.root / "fixture.json").exists(), "discovery must not start a thread")

    def test_inherited_api_key_is_not_exposed_in_the_public_result(self):
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
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", context.environment)
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
        from hey_my_buddy.buddy.harnesses.codex.native_run import validate_turn_provenance
        self.assertIsNotNone(validate_turn_provenance(forged))

    def test_denied_request_with_failed_native_turn_is_not_a_completed_attention(self):
        outcome = self.execute(self.context("approval-failed"))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertNotIn("turn", outcome.result)

    def test_deadline_ends_native_process_group(self):
        outcome = self.execute(self.context("hang", timeout=1))
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "deadline")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_absent_budget_executes_unbounded_without_an_immediate_deadline(self):
        context = self.context("ok", timeout=None)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertEqual(handle.deadline, math.inf)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")

    def test_absent_budget_still_honors_explicit_user_cancellation(self):
        context = self.context("hang", timeout=None)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertEqual(handle.deadline, math.inf)
        started = self.wait_native_turn_start()
        self.assertEqual(set(started), {"threadId", "turnId"})
        time.sleep(1.2)
        self.assertIsNone(handle.wait(0), "the micro task must outlive its one-second estimate")
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(10))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)

    def test_positive_timeout_keeps_a_finite_stamped_deadline(self):
        context = self.context(timeout=8)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertGreater(handle.deadline, time.monotonic())
        self.assertFalse(math.isinf(handle.deadline))

    def test_user_cancel_interrupts_the_owned_native_turn(self):
        context = self.context("hang", timeout=12)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.wait_native_turn_start()
        self.adapter.cancel(handle, grace_seconds=8)
        self.assertIsNotNone(handle.wait(10))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)

    def test_host_wrap_up_resumes_same_native_session_for_structured_summary(self):
        from hey_my_buddy.protocol.session_content import SessionNotification

        context = self.context("wrap-up", timeout=None)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        started = self.wait_native_turn_start()
        session_path = Path(context.session_record_path)
        record_deadline = time.monotonic() + 5
        while not session_path.exists() and time.monotonic() < record_deadline:
            time.sleep(0.01)
        first_record = notifications(session_path)
        controller.worker_stop(self.adapter, handle, stop_initiator="host",
                               grace_deadline=time.monotonic() + 10)
        self.assertIsNotNone(handle.wait(15), "Codex fixture summary controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertIs(outcome.result.get("wrapUpDelivered"), True, outcome.to_report())
        from hey_my_buddy.buddy.runtime.execution import classify_termination
        self.assertEqual(classify_termination(outcome, timed_out=False, cancel_requested=True,
                                              stop_initiator="host"), "wrap-up")
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        from hey_my_buddy.buddy.roles.turn_io import validate_outcome
        self.assertIsNone(validate_outcome(turn["outcome"]))
        self.assertEqual(turn["outcome"]["summary"], "Stopped partial work summary")
        self.assertEqual(turn["sessionId"], started["threadId"])
        self.assertEqual(turn["provenance"]["nativeTurnId"], "native-turn-2")
        state = json.loads(Path(self.environment["HMB_CODEX_FIXTURE_STATE"]).read_text())
        self.assertEqual(set(state["threads"]), {started["threadId"]})
        native_turns = state["threads"][started["threadId"]]["turns"]
        self.assertEqual([(item["id"], item["status"]) for item in native_turns],
                         [(started["turnId"], "interrupted"), ("native-turn-2", "completed")])
        self.assertEqual(len(state["threads"][started["threadId"]]["resumeRequests"]), 1)
        lines = notifications(session_path)
        self.assertEqual(lines[:len(first_record)], first_record, 'the original turn must stay intact')
        for line in lines:
            SessionNotification.from_payload(line)
            self.assertEqual(line['sessionId'], context.attempt_id)
        inputs = [line['update']['content']['text'] for line in lines
                  if line['update']['sessionUpdate'] == 'user_message_chunk']
        self.assertEqual(len(inputs), 2, 'the original and wrap-up runs share one attempt record')
        self.assertNotEqual(inputs[0], inputs[1])
        self.assertEqual(json.loads(lines[-1]['update']['content']['text'])['outcome']['summary'],
                         'Stopped partial work summary')

    def test_read_only_refuses_a_conflicting_policy_receipt(self):
        result, _context = self.review('readonly-policy-mismatch', index=41)
        self.assertEqual(result.status, 'failed', result.result)
        self.assertEqual(result.result['code'], 'readonly-policy-unverified')
        self.assertIs(result.result['modelStarted'], False)
        self.assertEqual(result.result['policyCheck']['threadReceipt']['status'], 'unknown')
        self.assertIn('nativeConfigPolicy', result.result)
        # The native receipt keeps its actual networkAccess value.
        self.assertIn('nativePolicy', result.result)
        self.assertIs(result.result['nativePolicy']['sandbox']['networkAccess'], True)
        self.assertFalse(result.result['toolEvidence']['streamComplete'])
        self.assertTrue(result.shutdown_confirmed)

    def test_config_readback_conflict_refuses_before_model_input(self):
        result, _context = self.review('readonly-config-mismatch', index=42)
        self.assertEqual(result.status, 'failed', result.result)
        self.assertEqual(result.result['code'], 'readonly-policy-unverified')
        self.assertIs(result.result['modelStarted'], False)
        self.assertEqual(result.result['policyCheck']['configReadback']['status'], 'unknown')
        self.assertIs(result.result['nativeConfigPolicy']['permissions']['hey-my-buddy-router']['network']['enabled'], True)
        self.assertFalse(result.result['toolEvidence']['streamComplete'])
        self.assertTrue(result.shutdown_confirmed)

    def test_read_only_accepts_user_features_and_external_configuration(self):
        result, _context = self.review('ok', index=46)
        self.assertEqual(result.status, 'ok', result.result)
        self.assertIs(result.result['nativeConfigPolicy']['features']['apps'], True)
        self.assertTrue(result.result['nativeConfigPolicy']['mcp_servers'])
        self.assertTrue(result.shutdown_confirmed)

    def test_generic_read_only_call_has_no_workflow_turn_or_agent_credential(self):
        result, _context = self.review('ok', index=43, capture=False)
        self.assertEqual(result.status, "ok", result.result)
        self.assertTrue(result.shutdown_confirmed)
        raw = result.result["rawAnswer"]
        answer = json.loads(raw) if isinstance(raw, str) else raw
        self.assertEqual(answer["buddyId"], "legal")
        self.assertIsNone(result.result["usage"]["bytesRead"])
        self.assertFalse((self.root / "attempt-43" / "turn-output.json").exists())

    def test_read_only_inherits_user_home_and_leaves_user_auth_untouched(self):
        account_home = self.root / 'account-home'
        account_home.mkdir()
        source_auth = account_home / 'auth.json'
        source_auth.write_text('private fixture auth')
        context = self.context(index=44)
        context.turn = None
        context.environment['CODEX_HOME'] = str(account_home)
        from hey_my_buddy.buddy.roles.structured_call import collect
        from hey_my_buddy.blackboard.routing.router import answer_schema, budget
        request = ReadOnlyStructuredRequest(str(self.cwd), 'Select', answer_schema(['legal']), budget())
        handle = start_router_preparation(ReviewPreparation("codex", CodexAdapter(), request, context,
                                                            (None, self.cwd, "fixture-digest")))
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(20))
        result = collect(handle)
        self.assertEqual(result.status, 'ok', result.result)
        self.assertTrue(result.shutdown_confirmed)
        state = json.loads(Path(self.environment["HMB_CODEX_FIXTURE_STATE"]).read_text())
        thread = state["threads"][result.result["sessionId"]]
        self.assertEqual(thread["codexHome"], str(account_home))
        self.assertEqual(thread["home"], context.environment["HOME"])
        self.assertFalse(list(Path(context.environment["HMB_STATE_DIR"]).rglob("codex-home")))
        self.assertEqual(source_auth.read_text(), 'private fixture auth')

    def test_read_only_budget_interrupts_the_native_turn_and_keeps_unknown_read_bytes(self):
        result, _context = self.review('readonly-budget', index=45, budget_options={'toolCalls': 0})
        self.assertEqual(result.status, 'failed', result.result)
        self.assertEqual(result.result['code'], 'readonly-budget-exhausted')
        self.assertTrue(result.shutdown_confirmed)
        # The interrupt facts are the run's own: the request happened and the
        # native side acknowledged it — never inferred from the confirmed stop.
        self.assertIs(result.result['nativeInterruptRequested'], True)
        self.assertTrue(result.result['nativeInterruptAcknowledged'])
        self.assertIsNone(result.result['usage']['bytesRead'])
        self.assertEqual(result.result['usage']['toolCalls'], 1)

    def test_denied_raw_tool_call_still_consumes_router_budget(self):
        result, _context = self.review('readonly-denied-budget', index=46, budget_options={'toolCalls': 0})
        self.assertEqual(result.result['code'], 'readonly-budget-exhausted')
        self.assertEqual(result.result['usage']['toolCalls'], 1)
        self.assertTrue(result.shutdown_confirmed)
        self.assertEqual(result.result['nativeRawToolEvents'][0]['params']['item']['call_id'], 'denied-1')

    def test_readonly_repairs_format_once_in_same_thread_but_not_bounds(self):
        for index, (case, calls) in enumerate((('readonly-repair', 2), ('readonly-outside', 1)), 47):
            with self.subTest(case=case):
                result, _context = self.review(case, index=index, capture=False)
                self.assertEqual(result.status, 'ok', result.result)
                self.assertEqual(result.result['correctionCount'], calls - 1)
                state = json.loads((self.root / 'fixture.json').read_text())
                self.assertEqual(len(state['threads'][result.result['sessionId']]['turns']), calls)


if __name__ == "__main__":
    unittest.main()
