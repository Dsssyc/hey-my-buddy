"""Durable Micro task routing with real transactions and no provider/model calls."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from admission import run_next
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.roles.prompts import router_prompt
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
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
        workspace = constraints.pop("workspace", {})
        task = constraints.pop("task", "Produce a verified implementation")
        return self.submit(
            board, request_id=request_id, task=task,
            cwd=str(self.workdir(request_id)), buddy="route",
            checkout=workspace.get("kind", "existing"),
            access=workspace.get("access", "write"), **constraints,
        )

    def prepare_reroute(self, board, view):
        board.store.workflow.prepare_continuation_workspace({"runId": view["runId"]})
        return board.call("workflow_get", {"runId": view["runId"]})

    def router_claim(self, board, view, *, claim_id="router-1"):
        pass  # Admission is owned by the service.
        return self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id=claim_id)

    def select(self, board, claim, *, buddy_id=BUDDY_ID, shutdown=True, status="ok"):
        owned = claim["claim"]
        return board.call('execution_result', {'attemptId': owned['attempt']['attemptId'], 'generation': owned['attempt']['generation'], 'nonce': NONCE, 'status': status, 'shutdownConfirmed': shutdown, 'result': {**claim_tool_receipt(owned, state_dir=board.directory), 'status': 'ok', 'operation': 'select', 'stopEvidence': {'shutdownConfirmed': shutdown, 'native': {'shutdownConfirmed': shutdown}}, 'usage': {'elapsedMs': 100, 'toolCalls': 0}, 'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest', 'manifestSha256': owned['decisionInput']['executionWorkspace']['manifestSha256']}, 'decision': self.valid_decision(owned['decisionInput'], buddy_id)}})

    def test_current_router_request_snapshot_and_dispatch_hold_manifest_reference(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board, request_id="routing-manifest-reference")
        decision_id = submitted["routing"]["decisionId"]
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT requested_json,request_form FROM decision_requests WHERE decision_id=?",
                (decision_id,)).fetchone()
            request = json.loads(row["requested_json"])
            snapshot = connection.execute(
                "SELECT snapshot_json,snapshot_form FROM router_requests WHERE decision_id=?",
                (decision_id,)).fetchone()
            frozen = json.loads(snapshot["snapshot_json"])
        reference = request["executionWorkspace"]
        self.assertEqual(set(reference), {"workspaceId", "manifestSha256"})
        self.assertEqual(row["request_form"], "manifest-reference-v2")
        self.assertEqual(snapshot["snapshot_form"], "manifest-reference-v2")
        self.assertEqual(frozen["baseInput"]["executionWorkspace"], reference)
        claim = self.router_claim(board, submitted)
        dispatched = claim["claim"]["decisionInput"]
        self.assertEqual(dispatched["executionWorkspace"], reference)
        self.assertEqual(dispatched["executionWorkspaceForm"], "manifest-reference-v2")

    def test_macro_task_goal_never_enters_selector_or_worker(self):
        board = self.board()
        self.seed(board)
        view = self.routed(board, create_macro={'title': 'Human-only group', 'goal': 'Hidden goal sentinel'}, title='Human-only title')
        claim = self.router_claim(board, view)
        serialized = json.dumps(claim)
        for hidden in ('Hidden goal sentinel', 'Human-only group', 'Human-only title'):
            self.assertNotIn(hidden, serialized)
        self.select(board, claim)
        self.register(board)
        worker_claim = self.claim(board)
        for hidden in ('Hidden goal sentinel', 'Human-only group', 'Human-only title'):
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
                    board.submit_execution(**{"requestId": f"bypass-{harness}", "task": "code",
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
        submitted = self.routed(board, )
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
        self.assertTrue(duplicate["alreadyExists"])
        self.assertEqual(duplicate["runId"], submitted["runId"])
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
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()
            self.assertNotIn("adapter", json.loads(task["spec_json"]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="execute")
        self.assertEqual(claimed["claim"]["task"]["spec"]["model"], BUDDY["model"])
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["buddy"], BUDDY)
        self.assertTrue(self.routed(board)["alreadyExists"])

    def test_partial_buddy_constraints_and_required_capabilities_are_rejected(self):
        board = self.board()
        self.seed(board)
        for field, value in (("effort", "high"), ("requiredCapabilities", ["effort:high"])):
            with self.subTest(field=field), self.assertRaises(BoardError) as raised:
                board.call("workflow_submit", {
                    "cwd": str(self.workdir(field)), "macroTaskId": "standalone",
                    "title": field, "task": "bounded work", "buddy": "route",
                    "checkout": "existing", "access": "write", "inputs": [],
                    "writeScope": ["."], "expectedSeconds": 1800,
                    "creationKey": "test-" + field, field: value,
                })
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_no_selector_or_candidates_is_recoverable_without_fake_turn(self):
        board = self.board()
        submitted = self.routed(board)
        self.assertTrue(submitted["awaitingHost"])
        self.assertIsNone(submitted["routing"]["taskId"])
        self.assertEqual(submitted["state"], "awaiting-host")
        self.assertEqual(submitted["activeRequest"]["reason"]["code"], "no-legal-candidate")
        with board.store.db.read() as connection:
            request = connection.execute(
                "SELECT summary,reason_code,reason_params_json,decision_id,payload_json"
                " FROM workflow_requests WHERE request_id=?",
                (submitted["activeRequest"]["requestId"],),
            ).fetchone()
            self.assertIsNone(request["summary"])
            self.assertIsNone(request["reason_code"])
            self.assertIsNone(request["reason_params_json"])
            self.assertEqual(request["decision_id"], submitted["routing"]["decisionId"])
            self.assertEqual(json.loads(request["payload_json"]), {"source": "routing"})
        self.assertEqual(submitted["counts"]["turns"], 0)
        with self.assertRaises(BoardError) as raised:
            self.continue_run(board, submitted)
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        recovered = self.continue_run(board, submitted, buddy=BUDDY, note="Host selected an installed buddy after routing had no selector")
        self.assertEqual(recovered["buddy"], BUDDY)
        self.assertFalse(recovered["awaitingHost"])
        replay = self.continue_run(board, submitted, requestId=submitted["activeRequest"]["requestId"], buddy=BUDDY, note="Host selected an installed buddy after routing had no selector")
        self.assertTrue(replay["alreadyResponded"])

    def test_route_request_can_be_retried_after_selector_becomes_available(self):
        board = self.board()
        submitted = self.routed(board)
        self.assertEqual(submitted["activeRequest"]["reason"]["code"], "no-legal-candidate")
        self.seed(board)
        request_id = submitted["activeRequest"]["requestId"]
        continued = self.continue_run(board, submitted, requestId=request_id,
                                      buddy="route", note="Use the now available selector")
        continued = self.prepare_reroute(board, continued)
        self.assertEqual(continued["routing"]["status"], "queued")
        self.assertNotEqual(continued["routing"]["decisionId"], submitted["routing"]["decisionId"])
        replay = self.continue_run(board, submitted, requestId=request_id,
                                   buddy="route", note="Use the now available selector")
        self.assertTrue(replay["alreadyResponded"])
        self.select(board, self.router_claim(board, continued))
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["buddy"], BUDDY)

    def test_catalog_drift_at_dispatch_is_attention_and_other_work_claims(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        self.select(board, self.router_claim(board, submitted))
        self.catalog_validation.side_effect = BoardError("CATALOG_UNAVAILABLE", "installed tuple disappeared")
        other = board.submit_execution(**{"requestId": "plain", "adapter": "command", "argv": ["true"],
                                           "task": "ordinary", "cwd": str(self.workdir("plain"))})
        self.register(board)
        claim = self.claim(board)
        self.assertEqual(claim["claim"]["task"]["taskId"], other["task"]["runId"])
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["status"], "needs-host")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertEqual(view["activeRequest"]["reason"],
                         {"code": "dispatch.buddy-rejected", "params": {"errorCode": "CATALOG_UNAVAILABLE"}})
        timeline = board.call("macro_task_timeline", {"macroTaskId": f"run:{submitted['runId']}"})
        host = next(span for span in timeline["spans"] if span.get("requestId") == view["activeRequest"]["requestId"])
        self.assertEqual(host["reason"], view["activeRequest"]["reason"])
        with board.store.db.read() as connection:
            request = connection.execute(
                "SELECT summary,reason_code,reason_params_json,decision_id,payload_json"
                " FROM workflow_requests WHERE request_id=?", (view["activeRequest"]["requestId"],),
            ).fetchone()
            self.assertIsNone(request["summary"])
            self.assertEqual(request["reason_code"], "dispatch.buddy-rejected")
            self.assertEqual(request["decision_id"], submitted["routing"]["decisionId"])
            self.assertEqual(json.loads(request["payload_json"]), {"source": "routing"})

    def test_explicit_dispatch_validation_has_own_reason_without_decision(self):
        board = self.board()
        self.seed(board)
        submitted = self.submit(board)
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET validated_buddy_revision=NULL WHERE run_id=?",
                               (submitted["runId"],))
        cause = {"code": "catalog-unavailable", "params": {}}
        self.catalog_validation.side_effect = BoardError(
            "BUDDY_UNAVAILABLE", "native diagnostic must not become a reason",
            buddy=BUDDY, enabled=False, catalogReadAt="2026-10-18T00:00:00Z", reason=cause)
        self.register(board)
        claim = self.claim(board, run_id=submitted["runId"])
        self.assertIsNone(claim["claim"])
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["activeRequest"]["reason"],
                         {"code": "dispatch.buddy-rejected", "params": {
                             "errorCode": "BUDDY_UNAVAILABLE", "buddy": BUDDY, "enabled": False,
                             "catalogReadAt": "2026-10-18T00:00:00Z", "cause": cause}})
        with board.store.db.read() as connection:
            request = connection.execute(
                "SELECT summary,reason_code,decision_id,payload_json FROM workflow_requests WHERE request_id=?",
                (view["activeRequest"]["requestId"],),
            ).fetchone()
            self.assertIsNone(request["summary"])
            self.assertEqual(request["reason_code"], "dispatch.buddy-rejected")
            self.assertIsNone(request["decision_id"])
            self.assertEqual(json.loads(request["payload_json"]), {"source": "routing"})

    def test_cancel_routes_owned_attempt_and_late_selection_cannot_start_micro_task(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        claim = self.router_claim(board, submitted)
        cancelled = board.call("workflow_cancel", {"runId": submitted["runId"]})
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["shutdown"]["unconfirmedRunIds"], [submitted["routing"]["taskId"]])
        owned = board.call("task_get", {"runId": submitted["routing"]["taskId"]})["task"]
        self.assertTrue(owned["cancelRequested"])
        self.select(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(view["buddy"])
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["shutdown"]["unconfirmedCount"], 0)







    def test_routing_attention_refuses_assistance_before_workspace_or_catalog_work(self):
        board = self.board()
        submitted = self.routed(board)
        prepared = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, submitted, submitted["activeRequest"]["requestId"], helpers=[{
                "title": "wrong-operation-helper", "task": "cannot resolve buddy",
                "cwd": str(self.workdir("wrong-operation-helper")),
                "workspace": {"kind": "worktree", "access": "write"},
            }])
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        self.assertEqual(len(self.workspace.prepare_calls), prepared)
        self.assertEqual(self.catalog_validation.call_count, 0)

    def helper_routing(self, board, parent, *, request_id="route-helper"):
        view = board.call("workflow_get", {"runId": parent["runId"]})
        reply = board.call("workflow_continue", {"runId": view["runId"],
            "requestId": view["activeRequest"]["requestId"],
            "decision": "approve", "autoContinue": True, "helpers": [{
            "title": request_id, "task": "Produce one bounded helper output",
            "cwd": str(self.workdir(request_id)), "buddy": "route",
            "checkout": "worktree", "access": "write", "inputs": [],
            "writeScope": ["."], "expectedSeconds": 1800,
        }]})
        return {**board.call("workflow_get", {"runId": parent["runId"]}), **reply}

    def test_helper_routing_attention_can_be_resolved_by_root_owner(self):
        board = self.board()
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        # Explicit parent admission registers a candidate in the shared catalog;
        # remove it to exercise a helper's unresolved routing boundary.
        with board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET enabled=0")
        view = self.helper_routing(board, parent)
        helper_id = view["children"][0]["taskId"]
        request = view["activeRequest"]
        self.assertTrue(request["routing"])
        self.assertEqual(request["origin"]["runId"], helper_id)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, view, request["requestId"])
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        from support import enable_fixture_buddy
        enable_fixture_buddy(board.store, BUDDY)
        continued = self.continue_run(board, view, requestId=request["requestId"],
                                      targetRunId=helper_id, buddy=BUDDY, note="Root Host selected an installed buddy")
        self.assertEqual(continued["counts"]["openRequests"], 0)
        claimed = self.claim(board, run_id=helper_id)
        self.assertIsNotNone(claimed["claim"], claimed)
        self.assertEqual(claimed["claim"]["task"]["spec"]["model"], BUDDY["model"])

    def test_native_resume_requires_same_buddy_and_exact_previous_session(self):
        board = self.board()
        submitted = self.routed(board)
        configured = self.continue_run(board, submitted, buddy={**BUDDY, "harness": "zcode"}, note="Host selected ZCode for native session continuity")
        pass  # Admission is owned by the service.
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view)
        resumed = self.claim(board, claim_request_id="native-turn-2")
        self.assertEqual(resumed["claim"]["turn"]["resumeMode"], "native-session")
        self.assertEqual(resumed["claim"]["turn"]["input"]["previousSessionId"], "native-123")
        self.finish_turn(board, resumed, disposition="attention", session_id="native-123")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view,  buddy=BUDDY, note="Host changed harness after the prior turn")
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
        self.continue_run(board, failed)
        reconstructed = self.claim(board, claim_request_id="claim-after-invalid-native-turn")
        self.assertEqual(reconstructed["claim"]["turn"]["resumeMode"], "reconstructed-new-session")

    def test_native_session_reconstructs_after_a_recorded_harness_version_change(self):
        board = self.board()
        submitted = self.routed(board)
        self.continue_run(board, submitted, buddy={**BUDDY, 'harness': 'zcode'}, note='Host native-session test')
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
        self.continue_run(board, view)
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
        blocker = board.submit_execution(**{"requestId": "capacity-holder", "adapter": "command", "argv": ["true"],
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
        continued = self.continue_run(board, current)
        self.assertEqual(continued["buddyRevision"], 1)
        second = self.claim(board, claim_request_id="second-coding-turn")
        self.assertEqual(self.catalog_validation.call_count, 1)
        self.finish_turn(board, second, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        changed = self.continue_run(board, current,  buddy={**BUDDY, "effort": "high"}, note="Host chose higher effort after capacity released")
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
        submitted = self.routed(board, )
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
                         sorted([BUDDY_ID, SECOND_BUDDY_ID, THIRD_BUDDY_ID]))
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
        self.assertEqual(held[0].request.input_text, structured_prompt(expected_prompt, audit["input"]["outputSchema"]))
        self.assertEqual(fixture_request.output_schema, audit["input"]["outputSchema"])
        self.assertEqual(fixture_request.budget, audit["input"]["budget"])
        self.assertTrue(audit["inputVerification"]["unchanged"])
        self.assertTrue(audit["stopEvidence"]["shutdownConfirmed"])
        self.assertTrue(self.routed(board)["alreadyExists"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        pass  # Admission is owned by the service.
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="authorized-coding-turn")
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["buddy"], selected["buddy"])

    def test_large_micro_task_reaches_router_whole(self):
        board = self.board()
        self.seed(board)
        task = "完整工作说明" * 15000
        self.assertGreater(len(task.encode("utf-8")), 128 * 1024)
        submitted = self.routed(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "queued")
        selected = self.router_claim(board, submitted)
        self.assertEqual(selected["claim"]["decisionInput"]["task"], task)
        self.assertGreater(len(router_prompt(router.render_prompt(selected["claim"]["decisionInput"])).encode()),
                           128 * 1024)

    def test_micro_task_above_old_routing_byte_limit_is_passed_whole(self):
        board = self.board()
        self.seed(board)
        task = "界" * 3000
        self.assertGreater(len(task.encode("utf-8")), 8192)
        submitted = self.routed(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "queued")
        selected = self.router_claim(board, submitted)
        self.assertEqual(selected["claim"]["decisionInput"]["task"], task)

    def test_explicit_long_micro_task_does_not_require_selector(self):
        board = self.board()
        task = "Complete the entire micro task. " * 10000
        submitted = self.submit(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "explicit")
        self.assertFalse(submitted["awaitingHost"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)
        self.register(board)
        claimed = self.claim(board)
        self.assertEqual(claimed["claim"]["task"]["spec"]["task"], task)
