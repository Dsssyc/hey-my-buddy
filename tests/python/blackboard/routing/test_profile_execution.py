"""Profile runs use console authority and the ordinary private execution queue."""
from __future__ import annotations

import json
from unittest.mock import patch

from admission import admit_board
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from support import BoardTestCase, FakeClock, seed_buddy

from hey_my_buddy.blackboard.evaluation.models import current_model, ensure_model_service
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.console.reads import console_snapshot_projection
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import profile as files
from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.protocol.owned_process import WorkingDirectoryRejected


NONCE = "p" * 32
PROFILE_BUDDY = "dsh:fixture:profile:off"
PROFILE_SERVICE = {"harness": "dsh", "provider": "fixture", "model": "profile"}
TARGET_BUDDY = "dsh:fixture:target:off"
TARGET_SERVICE = {"harness": "dsh", "provider": "fixture", "model": "target"}
CANDIDATE_BUDDY = "dsh:fixture:candidate:off"
CANDIDATE_SERVICE = {"harness": "dsh", "provider": "fixture", "model": "candidate"}
OBSERVATIONS = {"toolCalls": 0, "toolMarkerFrames": 0, "deniedInteractions": 0}


class ProfileExecutionTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(clock=FakeClock("2026-01-01T00:00:00.000Z"))
        with self.board_.store.db.write() as db:
            for buddy_id, service in ((PROFILE_BUDDY, PROFILE_SERVICE), (TARGET_BUDDY, TARGET_SERVICE),
                                      (CANDIDATE_BUDDY, CANDIDATE_SERVICE)):
                seed_buddy(db, buddy_id=buddy_id, **service, effort="off", now=self.board_.store.now())
            self.model_id = current_model(db, *TARGET_SERVICE.values())
            self.candidate_id = current_model(db, *CANDIDATE_SERVICE.values())

    def configure(self):
        self.board_.console_call("user_policy_publish", {"commandId": "profile-settings", "configuration": {
            "profileBuddyId": PROFILE_BUDDY, "modelResponseWaitSeconds": 60}})

    def start(self):
        return self.board_.console_call("profile_start", {"modelId": self.model_id})

    def claim(self, started):
        admitted = admit_board(self.board_, {"nonce": NONCE, "taskId": started["runId"]})
        self.assertIsNotNone(admitted["claim"], admitted)
        return admitted["claim"]

    def packet(self, claim):
        document = claim["decisionInput"]
        return files.read(self.board_.directory, document["profileRunId"], document["input"])["packet"]

    def answer(self, claim, round_index, answer):
        run_id = claim["decisionInput"]["profileRunId"]
        reference = files.publish(self.board_.directory, run_id, {"answer": answer, "observations": OBSERVATIONS})
        attempt = claim["attempt"]
        result = self.board_.call("execution_profile", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": NONCE,
            "round": round_index, "answer": reference})
        return reference, files.read(self.board_.directory, run_id, result["feedback"])

    def valid_answer(self, *, positioning="Strong general model", correspondences=None):
        return {"positioning": positioning, "claims": [{"polarity": "strength", "claim": "Handles code tasks",
            "sourceUrl": "https://example.org/model", "sourceDate": "2026-01-01", "sourceKind": "vendor"}],
            "correspondences": correspondences or []}

    def settle(self, claim, *, tool_calls=0, tool_name=None, stream_complete=True, outcomes=None):
        result = {**claim_tool_receipt(claim, tool_calls, state_dir=self.board_.directory,
                                       tool_name=tool_name, stream_complete=stream_complete, outcomes=outcomes), "status": "ok",
                  "operation": "profile", "requested": claim["decisionInput"]["buddy"],
                  "usage": {"elapsedMs": 120, "toolCalls": tool_calls},
                  "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}}}
        attempt = claim["attempt"]
        self.board_.call("execution_result", {"attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "nonce": NONCE, "status": "ok",
            "shutdownConfirmed": True, "result": result})

    def model_view(self):
        return next(row for row in console_snapshot_projection(self.board_.service)[0]["models"]
                    if row["modelId"] == self.model_id)

    def test_trigger_requires_console_configuration_and_enforced_web(self):
        with self.assertRaises(BoardError) as denied:
            self.board_.call("profile_start", {"modelId": self.model_id})
        self.assertEqual(denied.exception.code, "UNAUTHORIZED")
        self.assertEqual(self.start()["reason"]["code"], "profile-not-configured")
        self.assertEqual(console_snapshot_projection(self.board_.service)[0]['configuration']['profileUnavailableReason']['code'],
                         'profile-not-configured')
        self.configure()
        with self.assertRaises(BoardError) as missing:
            self.board_.console_call('profile_start', {'modelId': 'missing'})
        self.assertEqual(missing.exception.code, 'NOT_FOUND')
        for levels in ({"none": "enforced", "read": "enforced"},
                       {"none": "enforced", "read": "enforced", "web": "convention"}):
            with self.subTest(levels=levels), patch.dict("hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS", {"dsh": levels}):
                blocked = self.start()
                self.assertEqual((blocked["status"], blocked["reason"]["code"]),
                                 ("unavailable", "profile-web-unsupported"))
                self.assertEqual(console_snapshot_projection(self.board_.service)[0]['configuration']['profileUnavailableReason']['code'],
                                 'profile-web-unsupported')
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM profile_runs").fetchone()[0], 0)

    def test_console_trigger_claim_replace_and_latest_usage(self):
        self.configure()
        started = self.start()
        self.assertEqual(started['buddyId'], PROFILE_BUDDY)
        self.assertTrue(started['usesQuota'])
        self.assertEqual(self.start()["profileRunId"], started["profileRunId"])
        claim = self.claim(started)
        self.assertEqual(claim["decisionInput"]["routingMode"], "profile")
        self.assertEqual(claim['decisionInput']['modelResponseWaitSeconds'], 60)
        self.assertEqual(claim["decisionInput"]["buddy"]["model"], "profile")
        self.assertEqual(self.packet(claim)["model"]["modelId"], self.model_id)
        self.assertIn(CANDIDATE_SERVICE, [{key: item[key] for key in CANDIDATE_SERVICE}
                                          for item in self.packet(claim)["candidates"]])
        self.assertTrue(self.answer(claim, 0, self.valid_answer())[1]["accepted"])
        self.assertIsNone(self.model_view()['profile'])
        self.settle(claim)
        view = self.model_view()
        self.assertEqual(view["profile"]["positioning"], "Strong general model")
        self.assertEqual(view["profile"]["claims"][0]["claim"], "Handles code tasks")
        self.assertEqual(view["latestProfileRun"]["state"], "completed")
        self.assertEqual(view["latestProfileRun"]["usage"]["attempts"][0]["elapsedMs"], 120)
        self.assertEqual(view["latestProfileRun"]["correctionRounds"], 0)
        self.assertEqual(view["latestProfileRun"]["toolSummary"], [])
        replacement = self.start()
        self.assertNotEqual(replacement["profileRunId"], started["profileRunId"])
        next_claim = self.claim(replacement)
        revised = self.valid_answer(positioning="Revised positioning")
        revised["claims"][0]["claim"] = "Different sourced claim"
        self.assertTrue(self.answer(next_claim, 0, revised)[1]["accepted"])
        self.assertEqual(self.model_view()['profile'], view['profile'])
        self.settle(next_claim)
        replaced = self.model_view()
        self.assertEqual(replaced["profile"]["positioning"], "Revised positioning")
        self.assertEqual([item["claim"] for item in replaced["profile"]["claims"]],
                         ["Different sourced claim"])
        self.assertEqual(replaced["profile"]["profileRunId"], replacement["profileRunId"])

    def test_working_directory_rejection_keeps_its_reason_parameters(self):
        self.configure()
        attempt = self.claim(self.start())["attempt"]
        cause = WorkingDirectoryRejected.reason_for(
            str(self.board_.directory / "missing"), "WinError 267: The directory name is invalid")
        self.board_.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": NONCE, "status": "failed", "shutdownConfirmed": True,
            "terminationReason": "workspace-error",
            "result": {"operation": "profile", "status": "error", "code": cause["code"],
                       "reason": cause, "nativeRequestSent": False, "modelResponded": False,
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}}},
        })
        run = self.model_view()["latestProfileRun"]
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["reason"], cause)

    def test_latest_run_reads_outcome_summary_from_result_even_when_publication_fails(self):
        self.configure()
        claim = self.claim(self.start())
        self.answer(claim, 0, self.valid_answer())
        self.settle(claim, tool_calls=3, tool_name='read', outcomes=['success', 'failure', 'unknown'])
        # Reading a forbidden tool prevents publication; its observed outcomes
        # still explain this failed run and require no access to native files.
        with patch('hey_my_buddy.protocol.tool_evidence.read_tool_calls', side_effect=AssertionError('no file reads')):
            view = self.model_view()['latestProfileRun']
        self.assertEqual(view['state'], 'failed')
        self.assertEqual(view['toolSummary'], [
            {'toolName': 'read', 'calls': 3, 'failed': 1, 'unknown': 1}])
        with self.board_.store.db.read() as db:
            stored = json.loads(db.execute('SELECT usage_json FROM profile_runs WHERE task_id=?',
                                          (claim['task']['taskId'],)).fetchone()[0])
        self.assertNotIn('toolSummary', stored)
        self.assertNotIn('toolSummary', stored['attempts'][0])

    def test_old_run_without_outcome_summary_is_unknown_and_is_not_rewritten(self):
        self.configure()
        claim = self.claim(self.start())
        self.answer(claim, 0, self.valid_answer())
        self.settle(claim)
        attempt_id = claim['attempt']['attemptId']
        with self.board_.store.db.write() as db:
            recorded = json.loads(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
            evidence = recorded['result']['toolEvidence']
            evidence['version'] = 2
            evidence.pop('toolSummary')
            legacy = json.dumps(recorded)
            # Represent a pre-v3 sealed result, never feed old evidence to a
            # new execution or regenerate its per-call file.
            db.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?', (legacy, attempt_id))
        with patch('hey_my_buddy.protocol.tool_evidence.read_tool_calls', side_effect=AssertionError('no legacy file reads')):
            self.assertIsNone(self.model_view()['latestProfileRun']['toolSummary'])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0], legacy)

    def test_latest_run_sums_summaries_of_all_its_attempt_results(self):
        self.configure()
        claim = self.claim(self.start())
        self.answer(claim, 0, self.valid_answer())
        self.settle(claim, tool_calls=2, tool_name='read', outcomes=['failure', 'unknown'])
        with self.board_.store.db.write() as db:
            original = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (claim['attempt']['attemptId'],)).fetchone()
            next_attempt = dict(original)
            next_attempt.update(attempt_id='later-profile-attempt', generation=original['generation'] + 1)
            recorded = json.loads(next_attempt['result_json'])
            binding = {**recorded['result']['toolEvidence']['binding'],
                       'attemptId': next_attempt['attempt_id'], 'generation': next_attempt['generation']}
            collector = tool_evidence.ToolEventEvidence(binding, state_dir=self.board_.directory)
            identity = {'sessionId': 'later-profile-session'}
            for index, (name, outcome) in enumerate((('read', 'success'), ('read', 'failure'), ('search', 'unknown'))):
                for phase in ('start', 'end'):
                    collector.observe({'nativeIdentity': identity, 'callId': f'call-{index}',
                                       'toolName': name, 'category': 'read', 'phase': phase,
                                       'outcome': outcome if phase == 'end' else 'unknown'})
            recorded['result']['toolEvidence'] = collector.finish([identity], True)
            recorded['result']['nativeIdentity'] = identity
            recorded['result']['usage']['toolCalls'] = 3
            next_attempt['result_json'] = json.dumps(recorded)
            columns = ','.join(next_attempt)
            db.execute(f'INSERT INTO attempts({columns}) VALUES({",".join("?" for _ in next_attempt)})', tuple(next_attempt.values()))
        with patch('hey_my_buddy.protocol.tool_evidence.read_tool_calls', side_effect=AssertionError('no file reads')):
            self.assertEqual(self.model_view()['latestProfileRun']['toolSummary'], [
                {'toolName': 'read', 'calls': 4, 'failed': 2, 'unknown': 1},
                {'toolName': 'search', 'calls': 1, 'failed': 0, 'unknown': 1}])

    def test_invalid_answers_return_field_feedback_and_preserve_previous_profile(self):
        self.configure()
        first = self.claim(self.start())
        self.answer(first, 0, self.valid_answer(positioning="Earlier profile"))
        self.settle(first)
        second = self.claim(self.start())
        malformed = {"positioning": "Replacement", "claims": [{"polarity": "strength",
            "claim": "unsupported", "sourceUrl": "https://example.org/model", "sourceDate": "2026-02-30",
            "sourceKind": "vendor"}], "correspondences": [{**CANDIDATE_SERVICE,
            "sourceUrl": "https://example.org/correspondence", "sourceDate": "2026-01-01"},
            {"harness": "dsh", "provider": "fixture", "model": "not-a-candidate",
             "sourceUrl": "https://example.org/correspondence", "sourceDate": "2026-01-01"}]}
        _, feedback = self.answer(second, 0, malformed)
        self.assertFalse(feedback["accepted"])
        self.assertTrue(any("claims[0].sourceDate" in error for error in feedback["errors"]))
        self.assertTrue(any("correspondences[1]" in error and "candidates" in error for error in feedback["errors"]))
        # The relation label is no longer part of the answer form.
        malformed["correspondences"][0]["relation"] = "same"
        malformed["claims"][0].pop("sourceKind")
        round_errors = self.answer(second, 1, malformed)[1]["errors"]
        self.assertTrue(any("claims[0].sourceKind" in error for error in round_errors))
        self.assertTrue(any("correspondences[0]" in error and "additionalProperties" in error
                            for error in round_errors))
        self.assertFalse(self.answer(second, 2, {"positioning": [], "claims": [], "correspondences": []})[1]["accepted"])
        self.settle(second)
        view = self.model_view()
        self.assertEqual(view["profile"]["positioning"], "Earlier profile")
        self.assertEqual(view["latestProfileRun"]["state"], "failed")
        self.assertEqual(view["latestProfileRun"]["reason"]["code"], "profile-answer-invalid")
        self.assertEqual(view["latestProfileRun"]["correctionRounds"], 2)

    def test_missing_link_has_a_concrete_field_error(self):
        self.configure()
        claim = self.claim(self.start())
        answer = self.valid_answer()
        answer["claims"][0].pop("sourceUrl")
        feedback = self.answer(claim, 0, answer)[1]
        self.assertFalse(feedback["accepted"])
        self.assertTrue(any("claims[0].sourceUrl" in error for error in feedback["errors"]))
        answer['claims'][0]['sourceUrl'] = 'file:///local/private'
        feedback = self.answer(claim, 1, answer)[1]
        self.assertTrue(any('sourceUrl: must be an absolute http(s) URL' in error for error in feedback['errors']))

    def test_round_replay_conflict_limit_and_accepted_round(self):
        self.configure()
        claim = self.claim(self.start())
        invalid = self.valid_answer(positioning="")
        reference, first = self.answer(claim, 0, invalid)
        self.assertFalse(first["accepted"])
        attempt = claim["attempt"]
        duplicate = self.board_.call("execution_profile", {"attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "nonce": NONCE, "round": 0, "answer": reference})
        self.assertEqual(files.read(self.board_.directory, claim["decisionInput"]["profileRunId"],
                                    duplicate["feedback"]), first)
        with self.assertRaises(BoardError) as conflict:
            self.answer(claim, 0, self.valid_answer(positioning="Different"))
        self.assertEqual(conflict.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as skipped:
            self.answer(claim, 2, self.valid_answer())
        self.assertEqual(skipped.exception.code, "CONFLICT")
        self.assertTrue(self.answer(claim, 1, self.valid_answer())[1]["accepted"])
        with self.assertRaises(BoardError) as after_acceptance:
            self.answer(claim, 2, self.valid_answer(positioning="Too late"))
        self.assertEqual(after_acceptance.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as beyond_limit:
            self.answer(claim, 3, self.valid_answer())
        self.assertEqual(beyond_limit.exception.code, "INVALID_ARGUMENT")
        self.settle(claim)

    def test_same_model_correspondence_applies_and_user_undo_removes_it(self):
        self.configure()
        original = self.candidate_id
        claim = self.claim(self.start())
        correspondences = [{**CANDIDATE_SERVICE, "sourceUrl": "https://example.org/map",
                            "sourceDate": "2026-01-01"}]
        self.assertTrue(self.answer(claim, 0, self.valid_answer(correspondences=correspondences))[1]["accepted"])
        with self.board_.store.db.read() as db:
            self.assertEqual(current_model(db, *CANDIDATE_SERVICE.values()), original)
        self.settle(claim)
        with self.board_.store.db.read() as db:
            self.assertEqual(current_model(db, *CANDIDATE_SERVICE.values()), self.model_id)
            # The suggestion joined this model; it created no model of its own.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 3)
        snapshot = console_snapshot_projection(self.board_.service)[0]
        correspondence = next(item for item in snapshot["modelCorrespondences"] if item["model"] == "candidate")
        self.assertEqual(correspondence['modelId'], self.model_id)
        self.assertEqual(correspondence['source'], 'profile')
        self.assertEqual(correspondence['evidenceUrl'], 'https://example.org/map')
        self.assertEqual(correspondence['evidenceDate'], '2026-01-01')
        self.board_.console_call("user_policy_publish", {"commandId": "undo-profile-map",
            "modelCorrespondenceChanges": [{**CANDIDATE_SERVICE, "action": "undo",
                                            "correspondenceId": correspondence['correspondenceId']}]})
        with self.board_.store.db.read() as db:
            self.assertEqual(current_model(db, *CANDIDATE_SERVICE.values()), original)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM model_correspondences WHERE source='profile'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM models").fetchone()[0], 3)

    def test_router_input_contains_profile_without_user_note(self):
        self.configure()
        claim = self.claim(self.start())
        self.answer(claim, 0, self.valid_answer())
        self.settle(claim)
        self.board_.console_call("user_policy_publish", {"commandId": "profile-routing",
            "configuration": {"routerBuddyIds": [PROFILE_BUDDY, TARGET_BUDDY], "defaultRoutingMode": "fast"}})
        request = self.board_.store.decisions.request_select({"requestId": "profile-routing",
                                                               "task": "Fix parser", "expectedSeconds": 1800})
        decision = self.board_.call("selection_get", {"decisionId": request["decisionId"],
                                                      "includeAudit": True})["decision"]
        target = next(item for item in decision["input"]["buddies"] if item["buddyId"] == TARGET_BUDDY)
        self.assertEqual(target["modelProfile"]["positioning"], "Strong general model")
        self.assertNotIn("annotations", decision["input"])

    def test_execution_runs_three_fresh_web_sessions_through_service_feedback(self):
        from hey_my_buddy.buddy.harnesses.registry import run_seam
        from hey_my_buddy.buddy.harnesses.run_contract import (
            NativeIdentity, RunEnd, RunResult, RunValue, StopEvidence, StopLayer,
        )
        from hey_my_buddy.buddy.roles.router import DecisionAdapter
        from hey_my_buddy.buddy.runtime.execution import Execution
        from hey_my_buddy.paths import native_path
        from hey_my_buddy.protocol import process_identity

        self.configure()
        started = self.start()
        claim = self.claim(started)
        requests = []
        answers = [self.valid_answer(), self.valid_answer(), self.valid_answer()]
        answers[0]['claims'][0].pop('sourceUrl')
        answers[1]['claims'][0]['sourceDate'] = '2026-02-30'

        def fake_run(request, *, observer, services, cancelled):
            index = len(requests)
            requests.append(request)
            self.assertEqual(request.permission, 'web')
            self.assertIsNone(request.continuation)
            self.assertFalse(list(native_path(request.cwd).iterdir()))
            self.assertFalse(cancelled())
            native_identity = {'sessionId': f'profile-native-{index}'}
            receipt = claim_tool_receipt(claim, state_dir=self.board_.directory,
                                         native_identity=native_identity)
            return RunResult(identity=request.identity, harness=request.harness,
                end=RunEnd(status='ok', native_exit_code=0), model_responded=True,
                native_identity=NativeIdentity(session_id=native_identity['sessionId']),
                value=RunValue(schema_status='valid', raw=json.dumps(answers[index])),
                tool_evidence=receipt['toolEvidence'],
                stop_evidence=StopEvidence(native=StopLayer(group_state='gone')))

        module = run_seam('dsh')
        logs = []
        with patch.object(process_identity, '_active_native_journal', None):
            execution = Execution(claim, self.board_.directory, NONCE, client=self.board_.client(),
                                  session_record_path=claim['sessionRecordPath'], log=logs.append)
            native_path(execution.directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            with patch.object(module, 'run', side_effect=fake_run), \
                    patch.object(DecisionAdapter, 'available', return_value=(True, None)), \
                    patch.object(module, 'check_preparation', return_value=None), \
                    patch.object(module, 'native_evidence', return_value={}):
                execution.run()
        self.assertEqual(len(requests), 3, logs)
        self.assertEqual(len({r.identity.invocation_id for r in requests}), 3)
        self.assertIn('sourceUrl', requests[1].input_text)
        self.assertIn('sourceDate', requests[2].input_text)
        view = self.model_view()
        self.assertEqual(view['latestProfileRun']['state'], 'completed', logs)
        self.assertEqual(view['latestProfileRun']['correctionRounds'], 2)
        self.assertEqual(view['profile']['positioning'], 'Strong general model')
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?', (started['runId'],)).fetchone()[0], 1)

    def test_a_correction_round_that_never_launches_stores_no_correction_rounds(self):
        from hey_my_buddy.buddy.harnesses.registry import run_seam
        from hey_my_buddy.buddy.harnesses.run_contract import (
            NativeIdentity, RunEnd, RunResult, RunValue, StopEvidence, StopLayer,
        )
        from hey_my_buddy.buddy.roles.router import DecisionAdapter
        from hey_my_buddy.buddy.runtime.execution import Execution
        from hey_my_buddy.paths import native_path
        from hey_my_buddy.protocol import process_identity

        self.configure()
        started = self.start()
        claim = self.claim(started)
        requests = []
        answers = [self.valid_answer(), self.valid_answer()]
        answers[0]['claims'][0].pop('sourceUrl')

        def fake_run(request, *, observer, services, cancelled):
            index = len(requests)
            requests.append(request)
            native_identity = {'sessionId': f'profile-native-{index}'}
            receipt = claim_tool_receipt(claim, state_dir=self.board_.directory,
                                         native_identity=native_identity)
            if index == 0:
                # The stop lands once the first answer is on its way to the
                # service: the rejected correction round is then prepared but
                # must never launch another native session.
                execution.request_stop('console', force=True)
            return RunResult(identity=request.identity, harness=request.harness,
                end=RunEnd(status='ok', native_exit_code=0), model_responded=True,
                native_identity=NativeIdentity(session_id=native_identity['sessionId']),
                value=RunValue(schema_status='valid', raw=json.dumps(answers[index])),
                tool_evidence=receipt['toolEvidence'],
                stop_evidence=StopEvidence(native=StopLayer(group_state='gone')))

        module = run_seam('dsh')
        logs = []
        with patch.object(process_identity, '_active_native_journal', None):
            execution = Execution(claim, self.board_.directory, NONCE, client=self.board_.client(),
                                  session_record_path=claim['sessionRecordPath'], log=logs.append)
            native_path(execution.directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            with patch.object(module, 'run', side_effect=fake_run), \
                    patch.object(DecisionAdapter, 'available', return_value=(True, None)), \
                    patch.object(module, 'check_preparation', return_value=None), \
                    patch.object(module, 'native_evidence', return_value={}):
                execution.run()
        self.assertEqual(len(requests), 1, logs)
        # The one native round's answer was rejected, so the run fails on the
        # answer itself — but the correction round that never entered a native
        # session counts no correction.
        view = self.model_view()
        self.assertEqual(view['latestProfileRun']['state'], 'failed')
        self.assertEqual(view['latestProfileRun']['reason']['code'], 'profile-answer-invalid')
        self.assertEqual(view['latestProfileRun']['correctionRounds'], 0)
        self.assertIsNone(view['profile'])

    def test_a_correction_round_timing_out_before_launch_keeps_the_first_rounds_model_response(self):
        import time as time_module
        from pathlib import Path
        from hey_my_buddy.buddy.harnesses.registry import run_seam
        from hey_my_buddy.buddy.harnesses.run_contract import (
            NativeIdentity, RunEnd, RunResult, RunValue, StopEvidence, StopLayer,
        )
        from hey_my_buddy.buddy.roles.router import DecisionAdapter
        from hey_my_buddy.buddy.runtime import execution as execution_module
        from hey_my_buddy.buddy.runtime.execution import Execution
        from hey_my_buddy.paths import native_path
        from hey_my_buddy.protocol import process_identity

        self.board_.console_call("user_policy_publish", {"commandId": "profile-settings-timeout", "configuration": {
            "profileBuddyId": PROFILE_BUDDY, "modelResponseWaitSeconds": 1}})
        started = self.start()
        claim = self.claim(started)
        requests = []
        answers = [self.valid_answer(), self.valid_answer()]
        answers[0]['claims'][0].pop('sourceUrl')
        budgets = []
        original_fsync = execution_module.fsync_json

        def fsync(path, *args, **kwargs):
            original_fsync(path, *args, **kwargs)
            if Path(path).name == execution_module.BUDGET and len(budgets) < 2:
                budgets.append(path)
                if len(budgets) == 2:
                    # The correction round's re-armed start wait expires while
                    # its budget file is being published, before the round can
                    # enter its native session.
                    time_module.sleep(1.1)

        def fake_run(request, *, observer, services, cancelled):
            index = len(requests)
            requests.append(request)
            self.assertFalse(cancelled())
            # The first round's model answers, which clears its start wait.
            self.assertEqual(observer({"modelResponded": True, "toolCalls": 0}).action, "continue")
            native_identity = {'sessionId': f'profile-native-{index}'}
            receipt = claim_tool_receipt(claim, state_dir=self.board_.directory,
                                         native_identity=native_identity)
            return RunResult(identity=request.identity, harness=request.harness,
                end=RunEnd(status='ok', native_exit_code=0), model_responded=True,
                native_identity=NativeIdentity(session_id=native_identity['sessionId']),
                value=RunValue(schema_status='valid', raw=json.dumps(answers[index])),
                tool_evidence=receipt['toolEvidence'],
                stop_evidence=StopEvidence(native=StopLayer(group_state='gone')))

        module = run_seam('dsh')
        logs = []
        with patch.object(process_identity, '_active_native_journal', None), \
                patch.object(execution_module, 'model_start_unseen', return_value=None):
            execution = Execution(claim, self.board_.directory, NONCE, client=self.board_.client(),
                                  session_record_path=claim['sessionRecordPath'], log=logs.append)
            native_path(execution.directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            with patch.object(module, 'run', side_effect=fake_run), \
                    patch.object(DecisionAdapter, 'available', return_value=(True, None)), \
                    patch.object(module, 'check_preparation', return_value=None), \
                    patch.object(module, 'native_evidence', return_value={}), \
                    patch.object(execution_module, 'fsync_json', side_effect=fsync):
                execution.run()
        self.assertEqual(len(requests), 1, logs)
        self.assertEqual(len(budgets), 2, logs)
        # The expired wait fails the attempt as not responding — without
        # erasing the answer the one native round already delivered or
        # counting the correction round that never entered a session.
        attempt_id = claim['attempt']['attemptId']
        with self.board_.store.db.read() as db:
            recorded = json.loads(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                             (attempt_id,)).fetchone()[0])
            run_row = db.execute('SELECT state, reason_code, correction_rounds FROM profile_runs WHERE task_id=?',
                                 (started['runId'],)).fetchone()
        self.assertEqual(recorded['status'], 'failed')
        self.assertEqual(recorded['terminationReason'], 'deadline')
        self.assertEqual(recorded['result']['code'], 'router-not-responding')
        self.assertIs(recorded['result']['modelResponded'], True)
        self.assertEqual(recorded['result']['correctionCount'], 0)
        self.assertEqual(tuple(run_row)[:2], ('failed', 'router-not-responding'))
        self.assertEqual(run_row['correction_rounds'], 0)

    def test_late_receipt_records_usage_after_run_was_fenced(self):
        self.configure()
        started = self.start()
        claim = self.claim(started)
        with self.board_.store.db.write() as db:
            self.board_.store.profile.finish(db, task_id=started['runId'], now=self.board_.store.now(),
                                             code='router-stop-unconfirmed')
        self.settle(claim)
        latest = self.model_view()['latestProfileRun']
        self.assertEqual(latest['state'], 'failed')
        self.assertEqual(latest['reason']['code'], 'router-stop-unconfirmed')
        self.assertEqual(latest['usage']['attempts'][0]['elapsedMs'], 120)

    def test_service_rejects_tools_without_publishing_first_profile(self):
        self.configure()
        claim = self.claim(self.start())
        self.assertTrue(self.answer(claim, 0, self.valid_answer())[1]['accepted'])
        self.assertIsNone(self.model_view()['profile'])
        self.settle(claim, tool_calls=1, tool_name='bash')
        view = self.model_view()
        self.assertIsNone(view['profile'])
        self.assertEqual(view['latestProfileRun']['state'], 'failed')
        self.assertEqual(view['latestProfileRun']['reason']['code'], 'router-tools-forbidden')

    def test_service_keeps_profile_and_correspondences_on_rejected_tool_evidence(self):
        self.configure()
        original_claim = self.claim(self.start())
        self.answer(original_claim, 0, self.valid_answer(positioning='Original checked profile'))
        self.settle(original_claim)
        original_profile = self.model_view()['profile']
        with self.board_.store.db.read() as db:
            original_counts = [db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                               for table in ('models', 'model_correspondences')]
        for tool_name, complete, expected in (('read', True, 'router-tools-forbidden'),
                                               ('bash', True, 'router-tools-forbidden'),
                                               (None, False, 'router-tool-evidence-unverified')):
            with self.subTest(tool=tool_name, stream_complete=complete):
                claim = self.claim(self.start())
                suggestions = [{**CANDIDATE_SERVICE, 'sourceUrl': 'https://example.org/new-model',
                                'sourceDate': '2026-01-01'}]
                self.assertTrue(self.answer(claim, 0, self.valid_answer(
                    positioning='Do not publish', correspondences=suggestions))[1]['accepted'])
                self.assertEqual(self.model_view()['profile'], original_profile)
                self.settle(claim, tool_calls=int(tool_name is not None), tool_name=tool_name, stream_complete=complete)
                view = self.model_view()
                self.assertEqual(view['profile'], original_profile)
                self.assertEqual(view['latestProfileRun']['state'], 'failed')
                self.assertEqual(view['latestProfileRun']['reason']['code'], expected)
                with self.board_.store.db.read() as db:
                    self.assertEqual(current_model(db, *CANDIDATE_SERVICE.values()), self.candidate_id)
                    self.assertEqual([db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                      for table in ('models', 'model_correspondences')], original_counts)
