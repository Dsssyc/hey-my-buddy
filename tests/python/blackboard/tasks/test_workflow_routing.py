"""Durable Micro task routing with real transactions and no provider/model calls."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.routing.decision import MAX_DECISION_TASK_BYTES
from hey_my_buddy.errors import BoardError
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from admission import run_next
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.roles.prompts import router_prompt
from blackboard.routing.test_decision import (DecisionTestCase, CATALOG_BUDDY, BUDDY_ID, SECOND_BUDDY,
                             SECOND_BUDDY_ID, THIRD_BUDDY, THIRD_BUDDY_ID)
from blackboard.tasks.test_workflow import BUDDY, NONCE, WorkflowTestCase


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
        pass  # Admission is owned by the service.
        return self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id=claim_id)

    def select(self, board, claim, *, buddy_id=BUDDY_ID, shutdown=True, status="ok"):
        owned = claim["claim"]
        return board.call('execution_result', {'attemptId': owned['attempt']['attemptId'], 'generation': owned['attempt']['generation'], 'nonce': NONCE, 'status': status, 'shutdownConfirmed': shutdown, 'result': {**claim_tool_receipt(owned), 'status': 'ok', 'operation': 'select', 'stopEvidence': {'shutdownConfirmed': shutdown, 'native': {'shutdownConfirmed': shutdown}}, 'usage': {'elapsedMs': 100, 'toolCalls': 0}, 'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest', 'manifestSha256': owned['decisionInput']['executionWorkspace']['manifestSha256']}, 'decision': self.valid_decision(owned['decisionInput'], buddy_id)}})

    def test_macro_task_description_never_enters_selector_or_worker(self):
        board = self.board()
        self.seed(board)
        view = self.routed(board, macroTask={'title': 'Human-only group', 'description': 'Hidden description sentinel'}, title='Human-only title')
        claim = self.router_claim(board, view)
        serialized = json.dumps(claim)
        for hidden in ('Hidden description sentinel', 'Human-only group', 'Human-only title'):
            self.assertNotIn(hidden, serialized)
        self.select(board, claim)
        self.register(board)
        worker_claim = self.claim(board)
        for hidden in ('Hidden description sentinel', 'Human-only group', 'Human-only title'):
            self.assertNotIn(hidden, json.dumps(worker_claim))
        for owned in (claim['claim'], worker_claim['claim']):
            self.assertNotIn('lineage', owned['task'])
            self.assertNotIn('workflow', owned['task'])

    def test_explicit_validated_and_no_selection_or_coding_bypass(self):
        board = self.board()
        submitted = self.submit(board)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.assertEqual(submitted["buddy"], BUDDY)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)
        for harness in (None, "dsh", "zcode"):
            with self.subTest(harness=harness):
                with self.assertRaises(BoardError) as raised:
                    board.call("task_submit", {"requestId": f"bypass-{harness}", "task": "code",
                                               "cwd": str(self.workdir("bypass")),
                                               **({"adapter": harness} if harness else {})})
                self.assertEqual(raised.exception.code, "MICRO_TASK_REQUIRED")
        self.register(board)
        claim = self.claim(board)
        self.assertEqual({key: claim["claim"]["task"]["spec"][key] for key in BUDDY}, BUDDY)
        self.assertEqual(self.catalog_validation.call_count, 1)

    def test_settings_edit_does_not_stop_a_queued_router_selection(self):
        board = self.board()
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        decision_id = submitted["routing"]["decisionId"]
        board.console_call("user_policy_publish", {
            "commandId": "change-router-list",
            "configuration": {"routerBuddyIds": [SECOND_BUDDY_ID]},
        })
        claimed = self.router_claim(board, submitted, claim_id="after-settings-change")
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(claimed["claim"]["decisionInput"]["buddy"]["model"], CATALOG_BUDDY["model"])
        self.select(board, claimed, buddy_id=SECOND_BUDDY_ID)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["decisionId"], decision_id)
        self.assertEqual(view["routing"]["status"], "completed")

    def test_omitted_buddy_has_one_durable_decision_and_immutable_request(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        duplicate = self.routed(board)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["control"], submitted["control"])
        self.assertEqual(duplicate["routing"]["decisionId"], submitted["routing"]["decisionId"])
        self.assertIsNone(submitted["buddy"])
        self.assertEqual(submitted["counts"]["turns"], 0)
        self.register(board)
        self.assertEqual(self.claim(board, run_id=submitted["runId"])["reason"], "awaiting-model-selection")
        claim = self.router_claim(board, submitted)
        self.assertNotIn("turn", claim["claim"])
        self.select(board, claim)
        selected = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(selected["buddy"], BUDDY)
        self.assertEqual(selected["requestFingerprint"], submitted["requestFingerprint"])
        self.assertEqual(selected["microTask"]["fingerprint"], submitted["microTask"]["fingerprint"])
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()
            self.assertNotIn("adapter", json.loads(task["spec_json"]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="execute")
        self.assertEqual(claimed["claim"]["task"]["spec"]["model"], BUDDY["model"])
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["buddy"], BUDDY)
        self.assertTrue(self.routed(board)["duplicate"])

    def test_partial_constraints_are_rejected_and_capabilities_filter_instead(self):
        board = self.board()
        # Two effort-high buddies keep the Router path; the required capability is
        # still a hard filter, and a third enabled buddy would also be filtered out.
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        with self.assertRaises(BoardError) as raised:
            self.routed(board, effort="high")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("all-or-nothing", raised.exception.message)
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        claim = self.router_claim(board, submitted)
        self.assertEqual(sorted(buddy["buddyId"] for buddy in claim["claim"]["decisionInput"]["buddies"]),
                         sorted([SECOND_BUDDY_ID, THIRD_BUDDY_ID]))
        self.select(board, claim, buddy_id=BUDDY_ID)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertTrue(view["awaitingHost"])
        self.assertEqual(view["routing"]["status"], "needs-host")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertIsNone(view["buddy"])

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
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        recovered = self.continue_run(board, submitted, buddy=BUDDY, reason="Host selected an installed buddy after routing had no selector")
        self.assertEqual(recovered["buddy"], BUDDY)
        self.assertFalse(recovered["awaitingHost"])
        self.assertTrue(self.continue_run(board, submitted, buddy=BUDDY, reason="Host selected an installed buddy after routing had no selector")["duplicate"])

    def test_reroute_after_buddy_fix_is_durable_and_idempotent(self):
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

    def test_cancel_routes_owned_attempt_and_late_selection_cannot_start_micro_task(self):
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
        self.assertIsNone(view["buddy"])
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
            self.continue_run(board, taken, buddy=BUDDY, reason="New Host selected an installed buddy after takeover")
        self.assertEqual(raised.exception.code, "SHUTDOWN_UNCONFIRMED")
        self.select(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(view["buddy"])
        recovered = self.continue_run(board, view, buddy=BUDDY, reason="New Host selected an installed buddy after shutdown confirmation")
        self.assertEqual(recovered["buddy"], BUDDY)

    def test_host_buddy_cannot_break_locked_constraints(self):
        # The retired scenario locked a partial `effort` constraint; a partial tuple
        # is rejected input now, so the lock's surviving meaning is checked with the
        # complete quadruple it can still be supplied with.
        board = self.board()
        supplied = {**BUDDY, "effort": "high"}
        submitted = self.submit(board, buddyLocked=True, **supplied)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        awaiting = board.call("workflow_get", {"runId": submitted["runId"]})
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, awaiting, buddy=BUDDY, reason="Host attempted to override the locked buddy")
        self.assertEqual(raised.exception.code, "BUDDY_CONFLICT")
        recovered = self.continue_run(board, awaiting, buddy=supplied, reason="Host re-selected the locked buddy")
        self.assertEqual(recovered["buddy"], supplied)

    def test_routing_attention_refuses_assistance_before_workspace_or_catalog_work(self):
        board = self.board()
        submitted = self.routed(board)
        prepared = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, submitted, submitted["activeRequest"]["requestId"], helpers=[{
                "requestId": "wrong-operation-helper", "task": "cannot resolve buddy",
                "cwd": str(self.workdir("wrong-operation-helper")),
                "executionWorkspace": {"kind": "worktree", "access": "write"},
            }])
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
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
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        approved = self.helper_routing(board, parent, requiredCapabilities=["effort:high"])
        helper_id = approved["children"][0]["taskId"]
        helper = board.call("workflow_get", {"runId": helper_id})
        routing = self.router_claim(board, helper)
        self.assertEqual(sorted(buddy["buddyId"] for buddy in routing["claim"]["decisionInput"]["buddies"]),
                         sorted([SECOND_BUDDY_ID, THIRD_BUDDY_ID]))
        cancelled = board.call("workflow_cancel", {"runId": parent["runId"], **self.control(parent)})
        self.assertIn(helper["routing"]["taskId"], cancelled["shutdown"]["unconfirmedRunIds"])
        self.select(board, routing, buddy_id=SECOND_BUDDY_ID)
        closed = board.call("workflow_get", {"runId": helper_id})
        self.assertEqual(closed["state"], "cancelled")
        self.assertIsNone(closed["buddy"])

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
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        continued = self.continue_run(board, view, targetRunId=helper_id, buddy={**BUDDY, "effort": "high"}, reason="Root Host resolved the helper's high effort routing request")
        self.assertEqual(continued["runId"], parent["runId"])
        self.assertEqual(continued["targetRunId"], helper_id)
        self.assertEqual(continued["counts"]["openRequests"], 0)
        # The helper's required capability is part of its authorization, so the
        # coding worker declares it before claiming the resolved helper.
        pass  # Admission is owned by the service.
        claimed = self.claim(board, run_id=helper_id, claim_request_id="helper")
        self.assertIsNotNone(claimed["claim"], claimed)
        self.assertEqual(claimed["claim"]["task"]["spec"]["effort"], "high")
        self.assertEqual(claimed["claim"]["turn"]["turnIndex"], 1)
        # The helper still owns a writer in this checkout; the unrelated micro task
        # uses an isolated fixture worktree to reach the Host authorization check.
        unrelated = self.routed(board, request_id="unrelated", executionWorkspace={"kind": "worktree", "access": "write"})
        fresh = board.call("workflow_get", {"runId": parent["runId"]})
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, fresh, command_id="foreign", targetRunId=unrelated["runId"], buddy=BUDDY, reason="Host attempted to configure an unrelated micro task")
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")

    def test_installed_validation_race_with_takeover_cannot_start_old_snapshot(self):
        board = self.board()
        self.seed(board)
        view = self.routed(board)
        self.select(board, self.router_claim(board, view))
        self.register(board)

        def takeover(buddy, **_):
            board.call("workflow_takeover", {"runId": view["runId"], "commandId": "during-validation",
                       "expectedOwnerGeneration": 1, "newHostId": "host-2", **self.control(view)})
            return dict(buddy)

        self.catalog_validation.side_effect = takeover
        claim = self.claim(board)
        self.assertIsNone(claim["claim"])
        self.assertEqual(claim["reason"], "awaiting-buddy-validation")
        self.assertEqual(board.call("workflow_get", {"runId": view["runId"]})["counts"]["turns"], 0)

    def test_native_resume_requires_same_buddy_and_exact_previous_session(self):
        board = self.board()
        submitted = self.routed(board)
        configured = self.continue_run(board, submitted, buddy={**BUDDY, "harness": "zcode"}, reason="Host selected ZCode for native session continuity")
        pass  # Admission is owned by the service.
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="next-native")
        resumed = self.claim(board, claim_request_id="native-turn-2")
        self.assertEqual(resumed["claim"]["turn"]["resumeMode"], "native-session")
        self.assertEqual(resumed["claim"]["turn"]["input"]["previousSessionId"], "native-123")
        self.finish_turn(board, resumed, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="change-config", buddy=BUDDY, reason="Host changed harness after the prior turn")
        changed = self.claim(board, claim_request_id="new-harness")
        self.assertEqual(changed["claim"]["turn"]["resumeMode"], "reconstructed-new-session")

    def test_native_provenance_hook_cannot_bypass_common_identity_and_session_checks(self):
        board = self.board()
        parent = self.submit(board, harness="zcode")
        pass  # Admission is owned by the service.
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
        self.continue_run(board, submitted, buddy={**BUDDY, 'harness': 'zcode'}, reason='Host native-session test')
        pass  # Admission is owned by the service.
        first = self.claim(board)
        self.finish_turn(board, first, disposition='attention', session_id='old-native')
        with board.store.db.write() as db:
            attempt_id = first['claim']['attempt']['attemptId']
            result = json.loads(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
            result['harnessAttempts'] = [{'harness': {'version': '1.0'}}]
            db.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?', (json.dumps(result), attempt_id))
            db.execute("UPDATE harness_health SET record_json=? WHERE harness='zcode'", (json.dumps({'version': '2.0'}),))
        view = board.call('workflow_get', {'runId': submitted['runId']})
        self.continue_run(board, view, command_id='new-harness-version')
        resumed = self.claim(board, claim_request_id='version-changed-turn')
        self.assertEqual(resumed['claim']['turn']['resumeMode'], 'reconstructed-new-session')
        self.assertEqual(resumed['claim']['turn']['input']['context']['resumeReason'], 'harness-version-changed')

    def test_adapter_specific_provenance_can_refuse_an_otherwise_valid_turn(self):
        board = self.board()
        self.submit(board, harness="zcode")
        pass  # Admission is owned by the service.
        claim = self.claim(board)
        self.executors["zcode"].validate_turn_provenance = lambda record: "native turn evidence is incomplete"
        result = self.finish_turn(board, claim)
        self.assertFalse(result["workflow"]["accepted"])
        self.assertIn("native turn evidence is incomplete", result["attempt"]["error"])

    def test_buddy_validation_is_once_per_revision_even_while_capacity_is_full(self):
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
        board.call('execution_result', {'attemptId': held['attempt']['attemptId'], 'generation': held['attempt']['generation'], 'nonce': NONCE, 'status': 'ok', 'result': {'finished': True}, 'shutdownConfirmed': True})
        first = self.claim(board, run_id=submitted["runId"], claim_request_id="first-coding-turn")
        self.finish_turn(board, first, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        continued = self.continue_run(board, current, command_id="same-buddy")
        self.assertEqual(continued["buddyRevision"], 1)
        second = self.claim(board, claim_request_id="second-coding-turn")
        self.assertEqual(self.catalog_validation.call_count, 1)
        self.finish_turn(board, second, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        changed = self.continue_run(board, current, command_id="new-buddy", buddy={**BUDDY, "effort": "high"}, reason="Host chose higher effort after capacity released")
        self.assertEqual(changed["buddyRevision"], 2)
        third = self.claim(board, claim_request_id="third-coding-turn")
        self.assertEqual(third["claim"]["turn"]["input"]["context"]["buddy"]["effort"], "high")
        self.assertEqual(self.catalog_validation.call_count, 2)

    def test_real_decision_worker_selects_once_and_persists_owned_shutdown_evidence(self):
        DecisionTestCase.use_helper(self, buddy_id=SECOND_BUDDY_ID)
        held = []
        start = self.readonly_start.side_effect

        def remember_handle(*args, **kwargs):
            handle = start(*args, **kwargs)
            held.append(handle)
            return handle

        self.readonly_start.side_effect = remember_handle
        board = self.board(max_concurrent=1)
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        self.assertEqual(run_next(board.client()), "ran")
        selected = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(selected["routing"]["status"], "completed")
        self.assertEqual(selected["buddy"], {key: SECOND_BUDDY[key] for key in BUDDY})
        self.assertEqual(selected["counts"]["turns"], 0)
        decision_task = board.call("task_get", {"runId": selected["routing"]["taskId"]})["task"]
        self.assertTrue(decision_task["shutdownConfirmed"])
        decision_id = selected["routing"]["decisionId"]
        audit = board.call("selection_get", {"decisionId": decision_id, "includeAudit": True})["decision"]
        self.assertEqual(sorted(buddy["buddyId"] for buddy in audit["input"]["buddies"]),
                         sorted([SECOND_BUDDY_ID, THIRD_BUDDY_ID]))
        self.assertEqual(audit["requestedBuddy"]["model"], CATALOG_BUDDY["model"])
        self.assertEqual(len(held), 1)
        control = held[0].role_run_control
        self.assertEqual(control["attemptId"], selected["routing"]["attemptId"])
        self.assertEqual(held[0].request.identity.attempt_id, selected["routing"]["attemptId"])
        harness, context, fixture_request = self.readonly_start.call_args.args
        self.assertEqual(harness, audit["input"]["buddy"]["harness"])
        self.assertEqual(context.decision_input, audit["input"])
        expected_prompt = router_prompt(router.render_prompt(audit["input"]))
        self.assertEqual(fixture_request.prompt, expected_prompt)
        self.assertEqual(held[0].request.input_text, expected_prompt)
        self.assertEqual(fixture_request.output_schema, audit["input"]["outputSchema"])
        self.assertEqual(fixture_request.budget, audit["input"]["budget"])
        self.assertTrue(audit["inputVerification"]["unchanged"])
        self.assertTrue(audit["stopEvidence"]["shutdownConfirmed"])
        self.assertTrue(self.routed(board, requiredCapabilities=["effort:high"])["duplicate"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        pass  # Admission is owned by the service.
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="authorized-coding-turn")
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["buddy"], selected["buddy"])

    def test_oversized_micro_task_stops_before_selection_and_recovers_with_explicit_buddy(self):
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
        with patch("hey_my_buddy.buddy.roles.router.DecisionAdapter.start") as start:
            self.assertEqual(run_next(board.client()), "idle")
        start.assert_not_called()
        replay = self.routed(board, task=task)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["routing"]["decisionId"], submitted["routing"]["decisionId"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
        continued = self.continue_run(board, submitted, buddy=BUDDY, reason="Host chose an installed buddy for the oversized micro task")
        self.assertEqual(continued["requestFingerprint"], submitted["requestFingerprint"])
        self.assertEqual(continued["microTask"]["fingerprint"], submitted["microTask"]["fingerprint"])
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})["audit"]
        self.assertEqual(audit["microTask"]["task"], task)
        self.register(board)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="long-micro-task-coding")
        self.assertEqual(claimed["claim"]["task"]["spec"]["task"], task)

    def test_micro_task_at_exact_routing_byte_limit_is_passed_whole(self):
        board = self.board()
        self.seed(board)
        task = "界" * (MAX_DECISION_TASK_BYTES // 3) + "x" * (MAX_DECISION_TASK_BYTES % 3)
        self.assertEqual(len(task.encode("utf-8")), MAX_DECISION_TASK_BYTES)
        submitted = self.routed(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "queued")
        selected = self.router_claim(board, submitted)
        self.assertEqual(selected["claim"]["decisionInput"]["task"], task)

    def test_explicit_long_micro_task_does_not_require_selector(self):
        board = self.board()
        task = "Complete the entire micro task. " * MAX_DECISION_TASK_BYTES
        submitted = self.submit(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.assertFalse(submitted["awaitingHost"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)
        self.register(board)
        claimed = self.claim(board)
        self.assertEqual(claimed["claim"]["task"]["spec"]["task"], task)
