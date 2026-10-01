"""Durable Goal routing with real transactions and no provider/model calls."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from buddy.db import SCHEMA_VERSION
from buddy.decision import MAX_DECISION_TASK_BYTES
from buddy.errors import BoardError
from buddy.worker.worker import Worker
from test_decision import (DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE,
                             SECOND_PROFILE_ID, THIRD_PROFILE, THIRD_PROFILE_ID)
from test_workflow import CONFIGURATION, NONCE, WorkflowTestCase


class TestWorkflowRouting(WorkflowTestCase):
    seed = DecisionTestCase.seed
    valid_decision = DecisionTestCase.valid_decision

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)

    def routed(self, board, *, request_id="route-1", **constraints):
        response = board.call("workflow_submit", {
            "requestId": request_id, "hostId": "host-1", "submissionToken": "submission-secret-1",
            "task": "Produce a verified implementation", "cwd": str(self.workdir(request_id)),
            "executionWorkspace": {"kind": "existing", "access": "write"}, **constraints,
        })
        if response.get("control"):
            self.controls[response["runId"]] = response["control"]
        return response

    def prepare_reroute(self, board, view):
        board.store.workflow.prepare_continuation_workspace({"runId": view["runId"]})
        return board.call("workflow_get", {"runId": view["runId"]})

    def router_claim(self, board, view, *, claim_id="router-1"):
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        return self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id=claim_id)

    def select(self, board, claim, *, profile_id=PROFILE_ID, shutdown=True, status="ok"):
        owned = claim["claim"]
        return board.call("worker_result", {
            "workerId": "router", "attemptId": owned["attempt"]["attemptId"],
            "generation": owned["attempt"]["generation"], "nonce": NONCE,
            "status": status, "shutdownConfirmed": shutdown,
            "result": {"status": "ok", "operation": "select", "tableRevision": owned["decisionInput"]["tableRevision"],
                       "stopEvidence": {"shutdownConfirmed": shutdown, "native": {"shutdownConfirmed": shutdown}},
                       "usage": {"elapsedMs": 100, "toolCalls": 0},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest", "manifestSha256": owned["decisionInput"]["executionWorkspace"]["manifestSha256"]},
                       "decision": self.valid_decision(owned["decisionInput"], profile_id)},
        })

    def test_objective_description_never_enters_selector_or_worker(self):
        board = self.board()
        self.seed(board)
        view = self.routed(board, objective={'title': 'Human-only group', 'description': 'Hidden description sentinel'}, title='Human-only title')
        claim = self.router_claim(board, view)
        serialized = json.dumps(claim)
        for hidden in ('Hidden description sentinel', 'Human-only group', 'Human-only title'):
            self.assertNotIn(hidden, serialized)
        self.select(board, claim)
        self.register(board)
        self.assertNotIn('Hidden description sentinel', json.dumps(self.claim(board)))

    def test_explicit_validated_and_no_selection_or_coding_bypass(self):
        board = self.board()
        submitted = self.submit(board)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.assertEqual(submitted["executionConfiguration"], CONFIGURATION)
        self.assertEqual(board.store.db.meta("schema_version"), str(SCHEMA_VERSION))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)
        for adapter in (None, "dsh", "zcode"):
            with self.subTest(adapter=adapter):
                with self.assertRaises(BoardError) as raised:
                    board.call("task_submit", {"requestId": f"bypass-{adapter}", "task": "code",
                                               "cwd": str(self.workdir("bypass")),
                                               **({"adapter": adapter} if adapter else {})})
                self.assertEqual(raised.exception.code, "GOVERNED_REQUIRED")
        self.register(board)
        claim = self.claim(board)
        self.assertEqual({key: claim["claim"]["task"]["spec"][key] for key in CONFIGURATION}, CONFIGURATION)
        self.assertEqual(self.catalog_validation.call_count, 1)

    def test_omitted_configuration_has_one_durable_decision_and_immutable_request(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        duplicate = self.routed(board)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["control"], submitted["control"])
        self.assertEqual(duplicate["routing"]["decisionId"], submitted["routing"]["decisionId"])
        self.assertIsNone(submitted["executionConfiguration"])
        self.assertEqual(submitted["counts"]["turns"], 0)
        self.register(board)
        self.assertEqual(self.claim(board, run_id=submitted["runId"])["reason"], "awaiting-model-selection")
        claim = self.router_claim(board, submitted)
        self.assertNotIn("turn", claim["claim"])
        self.select(board, claim)
        selected = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(selected["executionConfiguration"], CONFIGURATION)
        self.assertEqual(selected["requestFingerprint"], submitted["requestFingerprint"])
        self.assertEqual(selected["goal"]["fingerprint"], submitted["goal"]["fingerprint"])
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()
            self.assertNotIn("adapter", json.loads(task["spec_json"]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="execute")
        self.assertEqual(claimed["claim"]["task"]["spec"]["model"], CONFIGURATION["model"])
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["executionConfiguration"], CONFIGURATION)
        self.assertTrue(self.routed(board)["duplicate"])

    def test_partial_constraints_are_rejected_and_capabilities_filter_instead(self):
        board = self.board()
        # Two effort-high profiles keep the Router path; the required capability is
        # still a hard filter, and a third enabled profile would also be filtered out.
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE))
        with self.assertRaises(BoardError) as raised:
            self.routed(board, effort="high")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("all-or-nothing", raised.exception.message)
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        claim = self.router_claim(board, submitted)
        self.assertEqual(sorted(profile["profileId"] for profile in claim["claim"]["decisionInput"]["profiles"]),
                         sorted([SECOND_PROFILE_ID, THIRD_PROFILE_ID]))
        self.select(board, claim, profile_id=PROFILE_ID)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertTrue(view["awaitingHost"])
        self.assertEqual(view["routing"]["status"], "needs-host")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertIsNone(view["executionConfiguration"])

    def test_no_selector_or_candidates_is_recoverable_without_fake_turn(self):
        board = self.board()
        submitted = self.routed(board)
        self.assertTrue(submitted["awaitingHost"])
        self.assertIsNone(submitted["routing"]["taskId"])
        self.assertIn("routing", submitted["waitReason"])
        self.assertIsInstance(submitted["activeRequest"]["neededWork"], list)
        self.assertEqual(submitted["counts"]["turns"], 0)
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, submitted)
        self.assertEqual(raised.exception.code, "CONFIGURATION_REQUIRED")
        recovered = self.continue_run(board, submitted, configuration=CONFIGURATION, reason="Host selected an installed configuration after routing had no selector")
        self.assertEqual(recovered["executionConfiguration"], CONFIGURATION)
        self.assertFalse(recovered["awaitingHost"])
        self.assertTrue(self.continue_run(board, submitted, configuration=CONFIGURATION, reason="Host selected an installed configuration after routing had no selector")["duplicate"])

    def test_reroute_after_configuration_fix_is_durable_and_idempotent(self):
        board = self.board()
        submitted = self.routed(board)
        self.seed(board)
        continued = self.continue_run(board, submitted, reroute=True)
        self.assertIsNone(continued["routing"]["decisionId"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='workflow.continued' ORDER BY seq DESC LIMIT 1").fetchone()[0])
            self.assertTrue(event["reroute"])
        continued = self.prepare_reroute(board, continued)
        self.assertEqual(continued["routing"]["status"], "queued")
        self.assertNotEqual(continued["routing"]["decisionId"], submitted["routing"]["decisionId"])
        self.assertTrue(self.continue_run(board, submitted, reroute=True)["duplicate"])
        self.select(board, self.router_claim(board, continued))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)

    def test_catalog_drift_at_dispatch_is_attention_and_other_work_claims(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        self.select(board, self.router_claim(board, submitted))
        self.catalog_validation.side_effect = BoardError("CATALOG_UNAVAILABLE", "installed tuple disappeared")
        other = board.call("task_submit", {"requestId": "plain", "adapter": "command", "argv": ["true"],
                                           "task": "ordinary", "cwd": str(self.workdir("plain"))})
        self.register(board)
        claim = self.claim(board)
        self.assertEqual(claim["claim"]["task"]["runId"], other["task"]["runId"])
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["status"], "needs-host")
        self.assertEqual(view["counts"]["turns"], 0)

    def test_cancel_routes_owned_attempt_and_late_selection_cannot_start_goal(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        claim = self.router_claim(board, submitted)
        cancelled = board.call("workflow_cancel", {"runId": submitted["runId"], **self.control(submitted)})
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["shutdown"]["unconfirmedRunIds"], [submitted["routing"]["taskId"]])
        owned = board.call("task_get", {"runId": submitted["routing"]["taskId"]})["task"]
        self.assertTrue(owned["cancelRequested"])
        self.select(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(view["executionConfiguration"])
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["shutdown"]["unconfirmedCount"], 0)

    def test_takeover_fences_selection_and_requires_real_stop_before_recovery(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        claim = self.router_claim(board, submitted)
        taken = board.call("workflow_takeover", {"runId": submitted["runId"], "commandId": "takeover",
                           "expectedOwnerGeneration": 1, "newHostId": "host-2", **self.control(submitted)})
        self.controls[taken["runId"]] = taken["control"]
        self.assertTrue(taken["awaitingHost"])
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, taken, configuration=CONFIGURATION, reason="New Host selected an installed configuration after takeover")
        self.assertEqual(raised.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.select(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(view["executionConfiguration"])
        recovered = self.continue_run(board, view, configuration=CONFIGURATION, reason="New Host selected an installed configuration after shutdown confirmation")
        self.assertEqual(recovered["executionConfiguration"], CONFIGURATION)

    def test_host_configuration_cannot_break_locked_constraints(self):
        # The retired scenario locked a partial `effort` constraint; a partial tuple
        # is rejected input now, so the lock's surviving meaning is checked with the
        # complete quadruple it can still be supplied with.
        board = self.board()
        supplied = {**CONFIGURATION, "effort": "high"}
        submitted = self.submit(board, configurationLocked=True, **supplied)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        awaiting = board.call("workflow_get", {"runId": submitted["runId"]})
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, awaiting, configuration=CONFIGURATION, reason="Host attempted to override the locked buddy")
        self.assertEqual(raised.exception.code, "CONFIGURATION_CONFLICT")
        recovered = self.continue_run(board, awaiting, configuration=supplied, reason="Host re-selected the locked buddy")
        self.assertEqual(recovered["executionConfiguration"], supplied)

    def test_routing_attention_refuses_assistance_before_workspace_or_catalog_work(self):
        board = self.board()
        submitted = self.routed(board)
        prepared = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, submitted, submitted["activeRequest"]["requestId"], helpers=[{
                "requestId": "wrong-operation-helper", "task": "cannot resolve configuration",
                "cwd": str(self.workdir("wrong-operation-helper")),
                "executionWorkspace": {"kind": "worktree", "access": "write"},
            }])
        self.assertEqual(raised.exception.code, "CONFIGURATION_REQUIRED")
        self.assertEqual(len(self.workspace.prepare_calls), prepared)
        self.assertEqual(self.catalog_validation.call_count, 0)

    def helper_routing(self, board, parent, *, request_id="partial-helper", **constraints):
        view = board.call("workflow_get", {"runId": parent["runId"]})
        return board.call("workflow_decide", {
            "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
            "commandId": f"authorize-{request_id}", "expectedRevision": view["revision"],
            "decision": "approve", **self.control(parent), "helpers": [{
                "requestId": request_id, "task": "Produce one bounded helper output",
                "cwd": str(self.workdir(request_id)),
                "executionWorkspace": {"kind": "existing", "access": "write"}, **constraints,
            }],
        })

    def test_helper_routes_with_hard_filters_and_parent_owns_its_stop(self):
        board = self.board()
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE))
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        approved = self.helper_routing(board, parent, requiredCapabilities=["effort:high"])
        helper_id = approved["children"][0]["taskId"]
        helper = board.call("workflow_get", {"runId": helper_id})
        routing = self.router_claim(board, helper)
        self.assertEqual(sorted(profile["profileId"] for profile in routing["claim"]["decisionInput"]["profiles"]),
                         sorted([SECOND_PROFILE_ID, THIRD_PROFILE_ID]))
        cancelled = board.call("workflow_cancel", {"runId": parent["runId"], **self.control(parent)})
        self.assertIn(helper["routing"]["taskId"], cancelled["shutdown"]["unconfirmedRunIds"])
        self.select(board, routing, profile_id=SECOND_PROFILE_ID)
        closed = board.call("workflow_get", {"runId": helper_id})
        self.assertEqual(closed["state"], "cancelled")
        self.assertIsNone(closed["executionConfiguration"])

    def test_helper_routing_attention_can_be_resolved_by_root_owner(self):
        board = self.board()
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = self.helper_routing(board, parent, requiredCapabilities=["effort:high"])
        helper_id = view["children"][0]["taskId"]
        request = view["activeRequest"]
        self.assertTrue(request["routing"])
        self.assertEqual(request["origin"]["runId"], helper_id)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, view, request["requestId"], command_id="wrong-command")
        self.assertEqual(raised.exception.code, "CONFIGURATION_REQUIRED")
        continued = self.continue_run(board, view, targetRunId=helper_id, configuration={**CONFIGURATION, "effort": "high"}, reason="Root Host resolved the helper's high effort routing request")
        self.assertEqual(continued["runId"], parent["runId"])
        self.assertEqual(continued["targetRunId"], helper_id)
        self.assertEqual(continued["counts"]["openRequests"], 0)
        # The helper's required capability is part of its authorization, so the
        # coding worker declares it before claiming the resolved helper.
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh", "command", "effort:high"]})
        claimed = self.claim(board, run_id=helper_id, claim_request_id="helper")
        self.assertIsNotNone(claimed["claim"], claimed)
        self.assertEqual(claimed["claim"]["task"]["spec"]["effort"], "high")
        self.assertEqual(claimed["claim"]["turn"]["turnIndex"], 1)
        # The helper still owns a writer in this checkout; the unrelated goal
        # uses an isolated fixture worktree to reach the Host authorization check.
        unrelated = self.routed(board, request_id="unrelated", executionWorkspace={"kind": "worktree", "access": "write"})
        fresh = board.call("workflow_get", {"runId": parent["runId"]})
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, fresh, command_id="foreign", targetRunId=unrelated["runId"], configuration=CONFIGURATION, reason="Host attempted to configure an unrelated goal")
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")

    def test_pending_writer_gate_fences_changed_router_instead_of_refreshing_frozen_input(self):
        board = self.board()
        # Two effort-high candidates keep this a queued Router decision the writer
        # gate can fence; a sole candidate would complete before any claim.
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE))
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call("evaluation_write_begin", {"requestId": "changing", "expectedRevision": revision, "kind": "human"})
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        blocked = self.router_claim(board, submitted)
        self.assertEqual(blocked["reason"], "evaluation-writer-pending")
        board.console_call("user_policy_publish", {
            "commandId": "changed", "writerId": grant["writerId"], "generation": grant["generation"],
            "writerToken": grant["writerToken"], "expectedRevision": grant["tableRevision"],
            "configuration": {"routerProfileId": SECOND_PROFILE_ID},
        })
        claimed = self.router_claim(board, submitted, claim_id="after-writer")
        self.assertIsNone(claimed["claim"])
        old = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"], "includeAudit": True})["decision"]
        self.assertEqual(old["status"], "needs-host")
        self.assertEqual(old["input"]["profile"]["model"], PROFILE["model"])
        self.assertEqual(old["input"]["tableRevision"], revision)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        pending = self.continue_run(board, current, command_id="reroute-new-router", reroute=True)
        continued = self.prepare_reroute(board, pending)
        fresh = self.router_claim(board, continued, claim_id="new-router")
        self.assertEqual(fresh["claim"]["decisionInput"]["profile"]["model"], SECOND_PROFILE["model"])
        self.select(board, fresh, profile_id=SECOND_PROFILE_ID)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["tableRevision"], revision + 1)
        self.assertEqual(view["routing"]["configurationRevision"], 2)

    def test_installed_validation_race_with_takeover_cannot_start_old_snapshot(self):
        board = self.board()
        self.seed(board)
        view = self.routed(board)
        self.select(board, self.router_claim(board, view))
        self.register(board)

        def takeover(configuration, **_):
            board.call("workflow_takeover", {"runId": view["runId"], "commandId": "during-validation",
                       "expectedOwnerGeneration": 1, "newHostId": "host-2", **self.control(view)})
            return dict(configuration)

        self.catalog_validation.side_effect = takeover
        claim = self.claim(board)
        self.assertIsNone(claim["claim"])
        self.assertEqual(claim["reason"], "awaiting-configuration-validation")
        self.assertEqual(board.call("workflow_get", {"runId": view["runId"]})["counts"]["turns"], 0)

    def test_native_resume_requires_same_configuration_and_exact_previous_session(self):
        board = self.board()
        submitted = self.routed(board)
        configured = self.continue_run(board, submitted, configuration={**CONFIGURATION, "adapter": "zcode"}, reason="Host selected ZCode for native session continuity")
        board.call("worker_register", {"workerId": "w1", "adapter": "zcode", "capabilities": ["zcode", "dsh"]})
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="next-native")
        resumed = self.claim(board, claim_request_id="native-turn-2")
        self.assertEqual(resumed["claim"]["turn"]["resumeMode"], "native-session")
        self.assertEqual(resumed["claim"]["turn"]["input"]["previousSessionId"], "native-123")
        self.finish_turn(board, resumed, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="change-config", configuration=CONFIGURATION, reason="Host changed adapter after the prior turn")
        changed = self.claim(board, claim_request_id="new-harness")
        self.assertEqual(changed["claim"]["turn"]["resumeMode"], "reconstructed-new-session")

    def test_native_provenance_hook_cannot_bypass_common_identity_and_session_checks(self):
        board = self.board()
        parent = self.submit(board, adapter="zcode")
        board.call("worker_register", {"workerId": "w1", "adapter": "zcode", "capabilities": ["zcode"]})
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id="fixed-session")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, view)
        resumed = self.claim(board, claim_request_id="resume")
        wrong = self.turn_record(resumed, session_id="unrelated-session")
        result = self.finish_turn(board, resumed, record=wrong)
        self.assertFalse(result["workflow"]["accepted"])
        self.assertIn("exact previous session", result["attempt"]["error"])

        failed = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, failed, command_id="after-invalid-native-turn")
        reconstructed = self.claim(board, claim_request_id="claim-after-invalid-native-turn")
        self.assertEqual(reconstructed["claim"]["turn"]["resumeMode"], "reconstructed-new-session")

    def test_native_session_reconstructs_after_a_recorded_harness_version_change(self):
        board = self.board()
        submitted = self.routed(board)
        self.continue_run(board, submitted, configuration={**CONFIGURATION, 'adapter': 'zcode'}, reason='Host native-session test')
        board.call('worker_register', {'workerId': 'w1', 'adapter': 'zcode', 'capabilities': ['zcode']})
        first = self.claim(board)
        self.finish_turn(board, first, disposition='attention', session_id='old-native')
        with board.store.db.write() as db:
            attempt_id = first['claim']['attempt']['attemptId']
            result = json.loads(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
            result['harnessAttempts'] = [{'harness': {'version': '1.0'}}]
            db.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?', (json.dumps(result), attempt_id))
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='zcode'", (json.dumps({'version': '2.0'}),))
        view = board.call('workflow_get', {'runId': submitted['runId']})
        self.continue_run(board, view, command_id='new-harness-version')
        resumed = self.claim(board, claim_request_id='version-changed-turn')
        self.assertEqual(resumed['claim']['turn']['resumeMode'], 'reconstructed-new-session')
        self.assertEqual(resumed['claim']['turn']['input']['context']['resumeReason'], 'harness-version-changed')

    def test_adapter_specific_provenance_can_refuse_an_otherwise_valid_turn(self):
        board = self.board()
        self.submit(board, adapter="zcode")
        board.call("worker_register", {"workerId": "w1", "adapter": "zcode", "capabilities": ["zcode"]})
        claim = self.claim(board)
        self.executors["zcode"].validate_turn_provenance = lambda record: "native turn evidence is incomplete"
        result = self.finish_turn(board, claim)
        self.assertFalse(result["workflow"]["accepted"])
        self.assertIn("native turn evidence is incomplete", result["attempt"]["error"])

    def test_configuration_validation_is_once_per_revision_even_while_capacity_is_full(self):
        board = self.board(max_concurrent=1)
        self.seed(board)
        submitted = self.routed(board)
        self.select(board, self.router_claim(board, submitted))
        blocker = board.call("task_submit", {"requestId": "capacity-holder", "adapter": "command", "argv": ["true"],
                                             "task": "occupy one slot", "cwd": str(self.workdir("capacity-holder"))})
        self.register(board, "holder")
        held = self.claim(board, "holder", run_id=blocker["task"]["runId"], claim_request_id="capacity-holder")["claim"]
        self.register(board)
        for index in range(3):
            polled = self.claim(board, run_id=submitted["runId"], claim_request_id=f"poll-{index}")
            self.assertIsNone(polled["claim"])
            self.assertEqual(polled["reason"], "capacity")
        self.assertEqual(self.catalog_validation.call_count, 1)
        board.call("worker_result", {"workerId": "holder", "attemptId": held["attempt"]["attemptId"],
                                     "generation": held["attempt"]["generation"], "nonce": NONCE,
                                     "status": "ok", "result": {"finished": True}, "shutdownConfirmed": True})
        first = self.claim(board, run_id=submitted["runId"], claim_request_id="first-coding-turn")
        self.finish_turn(board, first, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        continued = self.continue_run(board, current, command_id="same-configuration")
        self.assertEqual(continued["executionConfigurationRevision"], 1)
        second = self.claim(board, claim_request_id="second-coding-turn")
        self.assertEqual(self.catalog_validation.call_count, 1)
        self.finish_turn(board, second, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        changed = self.continue_run(board, current, command_id="new-configuration", configuration={**CONFIGURATION, "effort": "high"}, reason="Host chose higher effort after capacity released")
        self.assertEqual(changed["executionConfigurationRevision"], 2)
        third = self.claim(board, claim_request_id="third-coding-turn")
        self.assertEqual(third["claim"]["turn"]["input"]["context"]["executionConfiguration"]["effort"], "high")
        self.assertEqual(self.catalog_validation.call_count, 2)

    def test_real_decision_worker_selects_once_and_persists_owned_shutdown_evidence(self):
        DecisionTestCase.use_helper(self, profile_id=SECOND_PROFILE_ID)
        board = self.board(max_concurrent=1)
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE))
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        worker = Worker("routing-worker", self.directory, client=board.client(), adapters=("decision",))
        worker.register()
        self.assertEqual(worker.run_once(), "ran")
        selected = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(selected["routing"]["status"], "completed")
        self.assertEqual(selected["executionConfiguration"], {key: SECOND_PROFILE[key] for key in CONFIGURATION})
        self.assertEqual(selected["counts"]["turns"], 0)
        decision_task = board.call("task_get", {"runId": selected["routing"]["taskId"]})["task"]
        self.assertTrue(decision_task["shutdownConfirmed"])
        decision_id = selected["routing"]["decisionId"]
        audit = board.call("selection_get", {"decisionId": decision_id, "includeAudit": True})["decision"]
        self.assertEqual(sorted(profile["profileId"] for profile in audit["input"]["profiles"]),
                         sorted([SECOND_PROFILE_ID, THIRD_PROFILE_ID]))
        self.assertEqual(audit["requestedProfile"]["model"], PROFILE["model"])
        control_path = self.directory / "attempts" / decision_task["taskId"] / selected["routing"]["attemptId"] / "mock-readonly.json"
        self.assertEqual(json.loads(control_path.read_text())["document"], audit["input"])
        self.assertTrue(audit["inputVerification"]["unchanged"])
        self.assertTrue(audit["stopEvidence"]["shutdownConfirmed"])
        self.assertEqual(worker.spool.pending(), [])
        self.assertTrue(self.routed(board, requiredCapabilities=["effort:high"])["duplicate"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh", "command", "effort:high"]})
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="authorized-coding-turn")
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["executionConfiguration"], selected["executionConfiguration"])

    def test_oversized_goal_stops_before_selection_and_recovers_with_explicit_configuration(self):
        DecisionTestCase.use_helper(self)
        board = self.board()
        self.seed(board)
        task = "目标🔒" * (MAX_DECISION_TASK_BYTES // 5)
        task_bytes = len(task.encode("utf-8"))
        self.assertGreater(task_bytes, MAX_DECISION_TASK_BYTES)
        self.assertLess(len(task), MAX_DECISION_TASK_BYTES)
        submitted = self.routed(board, task=task)
        self.assertTrue(submitted["awaitingHost"])
        self.assertEqual(submitted["routing"]["status"], "needs-host")
        self.assertIsNone(submitted["routing"]["taskId"])
        self.assertIn(str(task_bytes), submitted["routing"]["reason"])
        self.assertIn(str(MAX_DECISION_TASK_BYTES), submitted["routing"]["reason"])
        decision = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"], "includeAudit": True})["decision"]
        reference = {"runId": submitted["runId"], "bytes": task_bytes,
                     "sha256": hashlib.sha256(task.encode("utf-8")).hexdigest()}
        self.assertEqual(decision["taskReference"], reference)
        self.assertEqual(decision["requested"]["taskReference"], reference)
        self.assertIsNone(decision["input"])
        self.assertIsNone(decision["attemptId"])
        self.assertLess(len(json.dumps(decision["requested"])), 4096)
        self.assertNotIn(task, json.dumps(decision["requested"]))
        worker = Worker("oversized-router", self.directory, client=board.client(), adapters=("decision",))
        worker.register()
        with patch("buddy.adapters.decision.DecisionAdapter.start") as start:
            self.assertEqual(worker.run_once(), "idle")
        start.assert_not_called()
        replay = self.routed(board, task=task)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["routing"]["decisionId"], submitted["routing"]["decisionId"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
        continued = self.continue_run(board, submitted, configuration=CONFIGURATION, reason="Host chose an installed configuration for the oversized goal")
        self.assertEqual(continued["requestFingerprint"], submitted["requestFingerprint"])
        self.assertEqual(continued["goal"]["fingerprint"], submitted["goal"]["fingerprint"])
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})["audit"]
        self.assertEqual(audit["goal"]["task"], task)
        self.register(board)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="long-goal-coding")
        self.assertEqual(claimed["claim"]["task"]["spec"]["task"], task)

    def test_goal_at_exact_routing_byte_limit_is_passed_whole(self):
        board = self.board()
        self.seed(board)
        task = "界" * (MAX_DECISION_TASK_BYTES // 3) + "x" * (MAX_DECISION_TASK_BYTES % 3)
        self.assertEqual(len(task.encode("utf-8")), MAX_DECISION_TASK_BYTES)
        submitted = self.routed(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "queued")
        selected = self.router_claim(board, submitted)
        self.assertEqual(selected["claim"]["decisionInput"]["task"], task)

    def test_explicit_long_goal_does_not_require_selector(self):
        board = self.board()
        task = "Complete the entire goal. " * MAX_DECISION_TASK_BYTES
        submitted = self.submit(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.assertFalse(submitted["awaitingHost"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)
        self.register(board)
        claimed = self.claim(board)
        self.assertEqual(claimed["claim"]["task"]["spec"]["task"], task)
