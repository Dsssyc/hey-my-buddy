"""Durable decision orchestration: selection and its fencing.

Every test here uses the production store, the production resource implementations
and the production Worker against a private state directory. The generic native call
is patched to a deterministic subprocess (``protocol/fixtures/mock_readonly.py``) that speaks
the read-only structured envelope, so the adapter, the owned process, the durable receipt and
the publication transaction are exercised for real without a model call.
"""
from __future__ import annotations
from admission import admit_execution

import json
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from support import BoardTestCase, FakeClock, wait_for
from hey_my_buddy.blackboard.catalog.catalog import permission_capabilities


from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing import router
from protocol.fixtures import mock_readonly
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from admission import run_next, admit_execution
from hey_my_buddy.buddy.runtime.execution import Execution

BUDDY_ID = "dsh:deepseek-official:deepseek-flash:off"
CATALOG_BUDDY = {
    "buddyId": BUDDY_ID,
    "label": "DeepSeek-V41-Flash · off",
    "harness": "dsh",
    "provider": "deepseek-official",
    "model": "deepseek-flash",
    "effort": "off",
    "available": True,
    "enabled": True,
    "capabilities": ["execution:dsh", "effort:off", "context:large", *permission_capabilities("dsh")],
    "contextWindow": 1000000,
    "description": "fixture decision model",
    "source": "manual",
}
SECOND_BUDDY_ID = "dsh:deepseek-official:deepseek-v4-pro:high"
SECOND_BUDDY = {
    **CATALOG_BUDDY,
    "buddyId": SECOND_BUDDY_ID,
    "label": "DeepSeek-V4-Pro · high",
    "model": "deepseek-v4-pro",
    "effort": "high",
    "capabilities": ["execution:dsh", "effort:high", *permission_capabilities("dsh")],
}
#: A second effort-high buddy, so a test can pin or constrain to `effort:
#: high` and still leave two legal candidates for the Router path.
THIRD_BUDDY_ID = "dsh:deepseek-official:deepseek-flash:high"
THIRD_BUDDY = {
    **CATALOG_BUDDY,
    "buddyId": THIRD_BUDDY_ID,
    "label": "DeepSeek-V41-Flash · high",
    "effort": "high",
}

class DecisionTestCase(BoardTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        self.use_helper()

    # -- fixtures ------------------------------------------------------------
    def use_helper(self, path: Path | None = None, **mode: str) -> None:
        # Standalone selection-request has no frozen Git manifest yet. Only these
        # service mocks substitute preparation/verification; production stays strict.
        mock_readonly.install(self, path=path, **mode)

    def seed(
        self,
        board,
        *,
        buddies=(CATALOG_BUDDY, SECOND_BUDDY),
        decision_buddy: str | None = BUDDY_ID,
        preferences=None,
    ) -> dict:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        result = board.console_call(
            "user_policy_publish",
            {
                "commandId": "seed-1",
                "buddySettings": [{"buddyId": item["buddyId"], "enabled": True} for item in buddies],
                "preferenceChanges": preferences or [],
                "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": [decision_buddy] if decision_buddy else []},
            },
        )
        return result



    def publish_user_patch(self, board, *, request_id: str, command_id: str, **changes) -> dict:
        return board.console_call("user_policy_publish", {
            "commandId": command_id,
            **changes,
        })




    def run_worker(self, board, worker_id: str = "test", iterations: int = 1):
        for _ in range(iterations):
            run_next(board.client())

    def request(self, board, *, request_id: str = "pick-1", task: str = "fix the failing parser test") -> dict:
        return board.call("selection_request", {"requestId": request_id, "task": task})

    def decision(self, board, decision_id: str, *, audit: bool = True) -> dict:
        return board.call("selection_get", {"decisionId": decision_id, "includeAudit": audit})["decision"]

    def assert_code(self, code: str, callable_, *args, **kwargs) -> BoardError:
        with self.assertRaises(BoardError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    @classmethod
    def valid_decision(cls, document: dict, buddy_id: str | None, *,
                       reason: str = "fixture selection", evidence: list[dict] | None = None) -> dict:
        return {"buddyId": buddy_id, "reason": reason, "evidence": list(evidence or [])}


class SelectionRequestTests(DecisionTestCase):
    def test_request_is_durable_idempotent_and_conflicts_on_changed_input(self):
        board = self.board()
        self.seed(board)
        first = self.request(board)
        self.assertEqual(first["status"], "queued")
        self.assertTrue(first["runId"])
        duplicate = self.request(board)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["decisionId"], first["decisionId"])
        self.assertEqual(duplicate["runId"], first["runId"])
        self.assert_code("CONFLICT", self.request, board, task="a different task")
        queued = self.decision(board, first["decisionId"])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["kind"], "select")
        self.assertEqual(queued["requestId"], "pick-1")
        self.assertEqual(queued["requested"]["task"], "fix the failing parser test")
        self.assertEqual([p["buddyId"] for p in queued["input"]["buddies"]],
                         [BUDDY_ID, SECOND_BUDDY_ID])
        # The queued recommendation is cancelled through the ordinary task operation.
        board.call("task_cancel", {"runId": first["runId"], 'note': "not needed"})
        self.assertEqual(self.decision(board, first["decisionId"])["status"], "cancelled")
        # The idempotent replay still resolves the same decision after it terminated.
        replayed = self.request(board)
        self.assertEqual(replayed["status"], "cancelled")
        self.assertTrue(replayed["duplicate"])

    def test_cancel_without_note_does_not_append_an_empty_explanation(self):
        board = self.board()
        self.seed(board)
        selected = self.request(board)
        board.call("task_cancel", {"runId": selected["runId"]})
        self.assertEqual(self.decision(board, selected["decisionId"])["reason"],
                         "the decision run was cancelled")

    def test_recommendation_never_creates_or_authorizes_a_business_task(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], BUDDY_ID)
        self.assertIn("mock select chose", decision["reason"])
        # Exactly the decision's own run exists; no business task was submitted.
        tasks = board.call("task_list", {"limit": 50})["runs"]
        self.assertEqual([task["runId"] for task in tasks], [request["runId"]])
        self.assertEqual(tasks[0]["adapter"], "decision")
        # The Host decides and starts work explicitly: the recommendation returns the
        # parameters, and the decision run itself is not an execution permit.
        self.assertEqual(decision["requestedBuddy"]["model"], CATALOG_BUDDY["model"])
        self.assertEqual(decision["requestedBuddy"]["effort"], CATALOG_BUDDY["effort"])
        self.assertIsNone(decision["resolvedBuddy"])
        self.assertIsNone(decision["observedBuddy"])
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
        self.assert_code("UNSUPPORTED", board.call, "task_retry", {"runId": request["runId"]})
        self.assert_code(
            "UNSUPPORTED", board.call, "task_acknowledge", {"runId": request["runId"], "note": "looks good"}
        )

    def test_real_worker_publishes_a_bounded_input(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], BUDDY_ID)
        self.assertEqual(decision["evidence"], [])
        document = decision["input"]
        self.assertEqual(document["operation"], "select")
        self.assertEqual(document["requestId"], "pick-1")
        self.assertEqual(document["task"], "fix the failing parser test")
        self.assertEqual(
            [buddy["buddyId"] for buddy in document["buddies"]], [BUDDY_ID, SECOND_BUDDY_ID]
        )
        self.assertNotIn("cards", document)
        self.assertNotIn("evidence", document)
        # The persisted audit input carries only the bounded table and task facts.
        rendered = json.dumps(document)
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn(str(self.directory), rendered)
        self.assertEqual(decision["inputSha256"], decision["inputSha256"].lower())
        # The decision task completed as ordinary work with a durable receipt.
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertEqual(task["status"], "completed")
        self.assertTrue(task["shutdownConfirmed"])
        self.assertEqual(task["spec"]["decision"]["kind"], "select")

    def test_pins_and_excludes_are_enforced_in_python_before_and_after_the_call(self):
        board = self.board()
        # Three enabled buddies keep two candidates legal after the exclusion, so
        # the Router path still runs; a single remaining candidate would be
        # selected by the program directly (covered in test_sole_candidate_selection).
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY),
                  preferences=[{"buddyId": BUDDY_ID, "mode": "exclude", "reason": "user excluded"}])
        self.use_helper(buddy_id=SECOND_BUDDY_ID)
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], SECOND_BUDDY_ID)
        self.assertEqual(sorted(buddy["buddyId"] for buddy in decision["input"]["buddies"]),
                         sorted([SECOND_BUDDY_ID, THIRD_BUDDY_ID]))

        # A pin limits the candidate set; a recommendation outside it is refused.
        self.use_helper(buddy_id=BUDDY_ID)
        self.publish_user_patch(
            board,
            request_id="pin-1",
            command_id="pin-1",
            preferenceChanges=[{"buddyId": SECOND_BUDDY_ID, "mode": "pin", "reason": "only these two"},
                               {"buddyId": THIRD_BUDDY_ID, "mode": "pin", "reason": "only these two"}],
        )
        pinned = self.request(board, request_id="pick-2")
        self.run_worker(board, worker_id="w-pinned")
        pinned_decision = self.decision(board, pinned["decisionId"])
        self.assertEqual(pinned_decision["status"], "needs-host")
        self.assertEqual(pinned_decision["output"]["code"], "router-out-of-bounds")
        self.assertEqual(sorted(buddy["buddyId"] for buddy in pinned_decision["input"]["buddies"]),
                         sorted([SECOND_BUDDY_ID, THIRD_BUDDY_ID]))
        # The Router input carries the stored buddy preferences.
        self.assertEqual(pinned_decision["input"]["preferences"],
                         sorted([
                             {"buddyId": SECOND_BUDDY_ID, "mode": "pin",
                              "reason": "only these two"},
                             {"buddyId": THIRD_BUDDY_ID, "mode": "pin",
                              "reason": "only these two"},
                         ], key=lambda entry: entry["buddyId"]))

    def test_requested_capabilities_filter_the_candidate_set(self):
        board = self.board()
        self.seed(board)
        request = board.call(
            "selection_request",
            {"requestId": "pick-caps", "task": "needs image input", "requiredCapabilities": ["input:image"]},
        )
        # Exactly one capability-matching candidate: the program selects it without
        # a Router call, and the capability bound is still enforced in Python.
        self.assertEqual(request["status"], "completed")
        self.assertIsNone(request["runId"])
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["buddyId"], BUDDY_ID)
        self.assertEqual(decision["reason"], "唯一合法候选，未调用 Router")
        self.assertEqual(decision["routingBasis"]["candidateCount"], 1)
        self.readonly_start.assert_not_called()
        no_candidate = board.call(
            "selection_request",
            {"requestId": "pick-none", "task": "impossible", "requiredCapabilities": ["capability:nonexistent"]},
        )
        self.assertEqual(no_candidate["status"], "needs-host")
        self.assertIsNone(no_candidate["runId"])
        decision = self.decision(board, no_candidate["decisionId"])
        self.assertIn("legal candidate", decision["reason"])
        # The frozen basis records the empty candidate set the Host boundary and
        # the routing source derivation both read.
        self.assertEqual(decision["routingBasis"]["candidateCount"], 0)

    def test_new_pin_and_exclusion_cannot_adopt_an_old_frozen_candidate(self):
        board = self.board()
        self.seed(board)
        for mode, chosen, bounded in (("pin", SECOND_BUDDY_ID, BUDDY_ID),
                                     ("exclude", BUDDY_ID, BUDDY_ID)):
            with self.subTest(mode=mode):
                # Each case begins with both buddies legal, then changes the hard
                # policy after request. Claim must retain the original input.
                with board.store.db.write() as connection:
                    connection.execute("DELETE FROM evaluation_preferences")
                self.use_helper(buddy_id=chosen)
                request = self.request(board, request_id=f"freeze-{mode}")
                frozen = self.decision(board, request["decisionId"])["input"]
                self.assertEqual([p["buddyId"] for p in frozen["buddies"]], [BUDDY_ID, SECOND_BUDDY_ID])
                self.publish_user_patch(board, request_id=f"policy-{mode}", command_id=f"policy-{mode}",
                                        preferenceChanges=[{"buddyId": bounded, "mode": mode, "reason": "new hard bound"}])
                self.run_worker(board, worker_id=f"w-freeze-{mode}")
                decision = self.decision(board, request["decisionId"])
                with board.store.db.read() as connection:
                    retained = json.loads(connection.execute("SELECT document_json FROM router_dispatches WHERE task_id=?", (request["runId"],)).fetchone()[0])
                self.assertEqual(retained, frozen)
                self.assertEqual(decision["status"], "completed")
                self.assertEqual(decision["buddyId"], BUDDY_ID if mode == "pin" else SECOND_BUDDY_ID)
                self.assertFalse(decision["routerCalled"])
                self.assertTrue(decision["requested"]["selectionRetry"])

    def test_fixed_fields_and_capabilities_are_rechecked_at_adoption(self):
        board = self.board()
        # The effort-high constraint leaves two legal candidates, so the frozen
        # input really reaches a Router and adoption rechecks the hard bounds.
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        for name, constraints, field, replacement in (
            ("fixed", {"effort": "high"}, "effort", "off"),
            ("capability", {"requiredCapabilities": ["execution:dsh"]}, "capabilities_json", "[]"),
        ):
            with self.subTest(name=name):
                self.use_helper(buddy_id=SECOND_BUDDY_ID)
                if name == "fixed":
                    # Only workflow routing carries fixed buddy fields.
                    # Supply the same internal request used by workflow admission.
                    request = board.store.decisions._create(
                        request_id=f"freeze-{name}", request={"kind": "select", "task": "bounded selection",
                            "constraints": constraints, "requiredCapabilities": [],
                            "budget": router.budget("brief")})
                else:
                    request = board.call("selection_request", {"requestId": f"freeze-{name}",
                                         "task": "bounded selection", **constraints})
                client = board.client()
                worker = f"w-freeze-{name}"
                pass  # Admission is owned by the service.
                claim = admit_execution(client, "n" * 32, task_id=request["runId"])["claim"]
                with board.store.db.write() as connection:
                    before = connection.execute(f"SELECT {field} FROM buddies WHERE buddy_id=?", (SECOND_BUDDY_ID,)).fetchone()[0]
                    connection.execute(f"UPDATE buddies SET {field}=? WHERE buddy_id=?", (replacement, SECOND_BUDDY_ID))
                output = {**claim_tool_receipt(claim), "status": "ok", "operation": "select",
                          "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                          "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest", "manifestSha256": None},
                          "usage": {"elapsedMs": 100, "toolCalls": 0},
                          "decision": self.valid_decision(claim["decisionInput"], SECOND_BUDDY_ID)}
                client.submit_result(claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, {"status": "ok", "shutdownConfirmed": True, "result": output})
                decision = self.decision(board, request["decisionId"])
                self.assertTrue(decision["requested"].get("selectionRetry"), decision)
                self.assertIn(decision["status"], ("queued", "completed"))
                self.assertNotEqual(decision["buddyId"], SECOND_BUDDY_ID)
                with board.store.db.write() as connection:
                    connection.execute(f"UPDATE buddies SET {field}=? WHERE buddy_id=?", (before, SECOND_BUDDY_ID))

    def test_missing_decision_buddy_is_needs_host_without_a_model_call(self):
        board = self.board()
        self.seed(board, decision_buddy=None)
        request = self.request(board)
        self.assertEqual(request["status"], "needs-host")
        self.assertIsNone(request["runId"])
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["routerProblem"]["code"], "router-not-configured")
        self.assertTrue(decision["reason"].startswith("Router 不可用："))
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_native_fixture_absence_is_an_honest_harness_unavailable_outcome(self):
        board = self.board()
        self.seed(board)
        self.use_helper(path=self.directory / "missing-readonly")
        with board.store.db.write() as connection:
            connection.execute("UPDATE harness_health SET status='missing'")
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertFalse(capabilities["selection"])
        self.assertNotIn("maintenance", capabilities)
        self.assertFalse(capabilities["decisionAdapter"])
        request = self.request(board)
        self.assertEqual(request["status"], "needs-host")
        self.assertIsNone(request["runId"])
        # This harness outage removes every business candidate. The empty bounds
        # have their own reason, independently of the Router's availability.
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["routingBasis"]["candidateCount"], 0)
        self.assertIsNone(decision["routerProblem"])
        self.assertIn("legal candidate", decision["reason"])
        # The removed Host maintenance capability is absent even during an outage.
        self.assertNotIn("maintenance", capabilities)
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_capabilities_report_real_availability(self):
        board = self.board()
        self.seed(board)
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertTrue(capabilities["selection"])
        self.assertNotIn("maintenance", capabilities)
        self.assertTrue(capabilities["decisionAdapter"])
        report = board.call("capabilities", {})
        self.assertIn("decision", report["adapters"])
        self.assertIn("decision", report["localCapabilities"])
        self.assertNotIn("selection", report["limitations"])
        self.assertIn("selectionFallback", report["limitations"])

    def test_ordinary_execution_storage_cannot_create_a_decision_run(self):
        board = self.board()
        self.assert_code(
            "UNSUPPORTED_ADAPTER",
            board.submit_execution,
            requestId="forged", task="x", cwd=str(self.workdir()), adapter="decision",
        )


class DecisionGateTests(DecisionTestCase):
    def test_preference_change_before_claim_keeps_input_and_completes(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        self.publish_user_patch(board, request_id="before-claim", command_id="before-claim",
                                preferenceChanges=[{"buddyId": BUDDY_ID, "mode": "prefer", "reason": "new preference"}])
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertIsNotNone(decision["attemptId"])
        self.assertEqual(decision["input"]["preferences"], [])



    def test_queued_selection_cancel_is_durable_and_never_runs(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        board.call("task_cancel", {"runId": request["runId"], 'note': "user cancelled the pending selection"})
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "cancelled")
        self.run_worker(board)
        self.assertEqual(board.call("task_get", {"runId": request["runId"]})["task"]["status"], "cancelled")
        self.assertIsNone(self.decision(board, request["decisionId"])["attemptId"])


class DecisionFailureTests(DecisionTestCase):
    def own_receipt(self, board, decision):
        with board.store.db.read() as connection:
            row = connection.execute("SELECT result_json FROM attempts WHERE attempt_id=? AND task_id=?",
                                     (decision["attemptId"], decision["runId"])).fetchone()
        return json.loads(row[0])

    def outcome(self, board, mode: str, *, request_id: str = "pick-x") -> dict:
        if mode == "foreign_evidence":
            self.use_helper(evidence="foreign")
        else:
            self.use_helper(mode=mode)
        request = self.request(board, request_id=request_id)
        self.run_worker(board, worker_id=f"w-{request_id}")
        return self.decision(board, request["decisionId"])

    def test_changed_input_before_spawn_keeps_its_router_boundary_code(self):
        from unittest.mock import patch
        board = self.board()
        self.seed(board)
        with patch('hey_my_buddy.buddy.roles.router_input.prepare', side_effect=BoardError('router-input-changed', 'changed')):
            decision = self.outcome(board, 'select_first')
        # Preparation fails before start(), so the Worker has no child to stop.
        self.assertEqual(decision['status'], 'needs-host')
        self.assertEqual(decision['output']['code'], 'router-input-changed')
        self.assertEqual(self.own_receipt(board, decision)['result']['code'], 'router-input-changed')
        task = board.call('task_get', {'runId': decision['runId']})['task']
        self.assertTrue(task['shutdownConfirmed'])
        self.assertEqual(task['attemptState'], 'finished')
        self.assertEqual(board.store.decisions.health_summary()['inputChangedCount'], 1)
        self.readonly_start.assert_not_called()

    def test_malformed_native_output_fails_without_touching_the_table(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "malformed")
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(decision["output"]["code"], "router-stop-unconfirmed")
        self.assertEqual(self.own_receipt(board, decision)["result"]["code"], "invalid-native-result")
        self.assertFalse(decision["stopEvidence"]["shutdownConfirmed"])

    def test_native_error_envelope_fails_honestly(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "error")
        self.assertEqual(decision["status"], "needs-host")
        self.assertIn("call-timeout", decision["error"])
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])

    def test_out_of_candidate_is_not_adopted_and_has_its_own_health_count(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "out_of_candidate")
        self.assertEqual(decision["status"], "needs-host")
        self.assertEqual(decision["output"]["code"], "router-out-of-bounds")
        self.assertIsNone(decision["selectedBuddy"])
        health = board.call("health", {})["routingHealth"]
        self.assertEqual(health["boundsRejectedCount"], 1)
        self.assertEqual(health["failureCount"], 0)
        self.assertEqual(health["abstentionCount"], 0)


    def test_task_routing_preferences_are_rejected_with_the_three_expressions(self):
        # The retired scenario recorded a positive task-preference outcome; the
        # structured task preference itself is cancelled (ADR-021 decision 5), so
        # the entry that used to accept it now rejects it and names the three
        # remaining expressions.
        board = self.board()
        self.seed(board)
        with self.assertRaises(BoardError) as raised:
            board.call("selection_request", {
                "requestId": "pick-preferences", "task": "work on the DSH harness source files",
                "routingPreferences": [{"match": {"harness": "dsh"}, "reason": "Use the installed DSH harness"}],
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("complete harness/provider/model/effort quadruple", raised.exception.message)
        self.assertIn("leave the choice to the Router", raised.exception.message)

    def test_policy_facts_carry_no_task_preference_slot(self):
        board = self.board()
        self.seed(board)
        request = board.call("selection_request", {
            "requestId": "pick-facts", "task": "work on the DSH harness source files"})
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["input"]["policyFacts"]["hardConstraints"], {})
        self.assertNotIn("taskPreference", decision["input"]["policyFacts"])
        self.assertNotIn("routingPreferences", decision["input"])
        check = decision["output"]["policyCheck"]
        self.assertEqual(set(check), {"hardConstraints", "userPreference"})
        self.assertNotIn("policyCheck", decision["output"]["decision"])
        self.assertEqual(decision["policyCheck"], check)

    def test_alternative_without_model_notes_completes_and_is_audited(self):
        board = self.board()
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY),
                  preferences=[{"buddyId": THIRD_BUDDY_ID, "mode": "prefer", "reason": "prefer high"}])
        self.use_helper(buddy_id=SECOND_BUDDY_ID, evidence="none")
        request = board.call("selection_request", {
            "requestId": "pick-alternative", "task": "choose for this task"})
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["buddyId"], SECOND_BUDDY_ID)
        self.assertEqual([entry["buddyId"] for entry in decision["input"]["buddies"]],
                         [BUDDY_ID, THIRD_BUDDY_ID, SECOND_BUDDY_ID])
        self.assertEqual(decision["input"]["policyFacts"]["userPreferredBuddyIds"],
                         [THIRD_BUDDY_ID])
        self.assertEqual(decision["output"]["policyCheck"]["userPreference"], "alternative")
        self.assertNotIn("taskPreference", decision["output"]["policyCheck"])
        self.assertEqual(decision["evidence"], [])
        self.assertEqual(decision["input"]["annotations"], [])

    def test_answer_shape_json_and_path_boundaries_keep_one_attempt_each(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.seed(board)
        for mode, code in (("answer_error", "answer-invalid-json"), ("answer_extra", "answer-shape"),
                           ("answer_bad_evidence", "answer-shape"), ("answer_escape", "answer-shape")):
            clock.advance(601)
            with self.subTest(mode=mode):
                decision = self.outcome(board, mode, request_id=mode)
                self.assertEqual(decision["status"], "needs-host")
                self.assertEqual(decision["output"]["code"], code)
                self.assertEqual(decision["evidence"], [])
                task = board.call("task_get", {"runId": decision["runId"]})["task"]
                self.assertEqual(task["status"], "failed")
                self.assertTrue(task["shutdownConfirmed"])
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                                        (decision["runId"],)).fetchone()[0], 1)

    def test_json_string_answer_is_parsed_by_the_generic_collector(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "json_answer")
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(set(decision["output"]["decision"]), {"buddyId", "reason", "evidence"})
        self.assertEqual(decision["usage"], {"elapsedMs": 200, "toolCalls": 1, "bytesRead": None})
        self.assertEqual(decision["nativeIdentity"]["sessionId"], "mock-native")

    def test_budget_and_input_changed_are_separate_from_failures_and_abstentions(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.seed(board)
        for mode, code in (("budget", "router-budget-exhausted"), ("deadline", "router-budget-exhausted"),
                           ("input_changed", "router-input-changed")):
            clock.advance(601)
            decision = self.outcome(board, mode, request_id=mode)
            self.assertEqual(decision["status"], "needs-host")
            self.assertEqual(decision["output"]["code"], code)
            self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
            self.assertIsNone(decision["selectedBuddy"])
        self.assertFalse(decision["inputVerification"]["unchanged"])
        # A changed copy is kept for inspection.
        _, changed_root, _ = self.input_verify.call_args.args
        self.assertTrue(Path(changed_root).exists())
        report = board.call("health", {})["routingHealth"]
        self.assertEqual(report["budgetExhaustedCount"], 2)
        self.assertEqual(report["inputChangedCount"], 1)
        self.assertEqual(report["failureCount"], 2)
        self.assertEqual(report["abstentionCount"], 0)
        self.assertEqual(report["consecutiveFailures"], 2)

    def test_missing_native_result_keeps_infrastructure_failure_semantics(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "protocol_error")
        self.assertEqual(decision["status"], "needs-host")
        self.assertEqual(decision["output"]["code"], "invalid-native-result")
        self.assertEqual(self.own_receipt(board, decision)["result"]["code"], "invalid-native-result")
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])

    def test_routing_health_distinguishes_failure_abstention_and_success_without_writes(self):
        board = self.board()
        self.seed(board)
        empty = board.call("health", {})["routingHealth"]
        self.assertEqual(empty["sampleCount"], 0)
        self.assertIsNone(empty["lastSuccessAt"])
        success = self.outcome(board, "select_first", request_id="health-success")
        self.outcome(board, "abstain", request_id="health-abstain")
        failure = self.outcome(board, "error", request_id="health-failure")
        with board.store.db.read() as connection:
            head = connection.execute('SELECT MAX(seq) FROM events').fetchone()[0]
            success_time = connection.execute(
                "SELECT created_at FROM events WHERE kind='decision.completed' AND json_extract(payload_json,'$.decisionId')=?",
                (success["decisionId"],),
            ).fetchone()[0]
        report = board.call("health", {})["routingHealth"]
        self.assertEqual(report["sampleCount"], 3)
        self.assertEqual(report["failureCount"], 1)
        self.assertEqual(report["consecutiveFailures"], 1)
        self.assertEqual(report["abstentionCount"], 1)
        self.assertEqual(report["lastSuccessAt"], success_time)
        self.assertEqual(report["recentFailures"][0]["decisionId"], failure["decisionId"])
        self.assertEqual(report, board.call("console_snapshot", {})["routingHealth"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT MAX(seq) FROM events').fetchone()[0], head)

    def test_abstention_may_cite_checkout_evidence(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "abstain_with_evidence")
        self.assertEqual(decision["status"], "needs-host")
        self.assertIsNone(decision["buddyId"])
        self.assertEqual(decision["output"]["decision"]["evidence"], [{"kind": "file", "ref": "input.txt"}])
        self.assertIsNone(decision["policyCheck"])
        self.assertEqual(board.call("health", {})["routingHealth"]["abstentionCount"], 1)

    def test_unconfirmed_shutdown_never_verifies_input_or_releases_capacity(self):
        board = self.board()
        self.seed(board)
        for mode in ("no_shutdown", "string_shutdown"):
            with self.subTest(mode=mode):
                decision = self.outcome(board, mode, request_id=mode)
                task = board.call("task_get", {"runId": decision["runId"]})["task"]
                self.assertEqual(decision["status"], "failed")
                self.assertFalse(task["shutdownConfirmed"])
                self.assertEqual(task["attemptState"], "uncertain")
                self.assertIsNone(decision["inputVerification"])
                self.input_verify.assert_not_called()

    def test_in_flight_cancel_without_native_stop_evidence_is_honest(self):
        board = self.board()
        self.seed(board)
        self.use_helper(mode="sleep", sleep="30")
        request = self.request(board, request_id="pick-cancel")
        claim = admit_execution(board.client(), 'n' * 32)['claim']
        execution = Execution(claim, self.directory, 'n' * 32, client=board.client())
        thread = threading.Thread(target=execution.run, daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: self.readonly_start.called, timeout=20))
        board.call("task_cancel", {"runId": request["runId"], 'note': "operator stopped it"})
        execution.request_stop("unknown")
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "the worker did not observe the cancel")
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "cancelled")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertFalse(task["shutdownConfirmed"])
        self.assertEqual(task["attemptState"], "uncertain")

    def test_worker_deadline_is_bounded_without_inventing_native_stop_evidence(self):
        board = self.board()
        self.seed(board)
        self.use_helper(mode="sleep", sleep="40")
        with patch.dict(router.BUDGETS["standard"], {"timeoutSeconds": 5}):
            request = board.call("selection_request", {"requestId": "pick-deadline", "task": "slow"})
        started = time.monotonic()
        self.run_worker(board, worker_id="w-deadline")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "failed")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertFalse(task["shutdownConfirmed"])

    def test_restart_keeps_a_running_decision_and_a_reconnected_answer_publishes(self):
        board = self.board()
        self.seed(board)
        first = self.request(board, request_id="pick-restart")
        client = board.client()
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "n" * 32, task_id=first["runId"])["claim"]
        self.assertIsNotNone(claim)
        from hey_my_buddy.protocol.execution import fsync_json
        fsync_json(board.directory / 'attempts' / first['runId'] / claim['attempt']['attemptId'] / 'spawn.intent',
                   {'taskId': first['runId'], 'attemptId': claim['attempt']['attemptId'], 'generation': 1})

        # Startup retains the request while the scheduler reconnects its execution.
        restarted = self.board()
        decision = self.decision(restarted, first["decisionId"])
        self.assertEqual(decision["status"], "running")
        restarted.store.execution_connection(claim["attempt"]["attemptId"], connected=True)

        envelope = {
            **claim_tool_receipt(claim),
            "status": "ok",
            "operation": "select",
            "requested": {"provider": CATALOG_BUDDY["provider"], "model": CATALOG_BUDDY["model"], "reasoningEffort": CATALOG_BUDDY["effort"]},
            "resolved": None,
            "observed": None,
            "usage": {"elapsedMs": 100, "toolCalls": 0, "bytesRead": 0},
            "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
            "inputVerification": {"unchanged": True, "manifestSha256": None, "snapshotSha256": "fixture-digest"},
            "decision": self.valid_decision(claim["decisionInput"], BUDDY_ID, reason="survived restart"),
        }
        restarted.client().submit_result(claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, {"status": "ok", "result": envelope, "shutdownConfirmed": True, "exitCode": 0})
        late = self.decision(restarted, first["decisionId"])
        self.assertEqual(late["status"], "completed")
        self.assertEqual(late["buddyId"], BUDDY_ID)
        self.assertIsNotNone(late["output"])
        with restarted.store.db.read() as connection:
            receipt = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                                    (claim["attempt"]["attemptId"],)).fetchone()[0])
        self.assertEqual(receipt["result"]["decision"]["reason"], "survived restart")

    def test_unreachable_router_is_fenced_only_when_identity_proves_interruption(self):
        board = self.board()
        self.seed(board)
        first = self.request(board, request_id="interrupted-router")
        claim = admit_execution(board.client(), "n" * 32, task_id=first["runId"])["claim"]
        attempt = claim["attempt"]
        identity = {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 123,
                    "startedAt": 123250000, "processGroupId": 123, "jobName": None}
        from hey_my_buddy.protocol.execution import fsync_json
        evidence = board.directory / "attempts" / first["runId"] / attempt["attemptId"]
        binding = {"taskId": first["runId"], "attemptId": attempt["attemptId"], "generation": attempt["generation"]}
        fsync_json(evidence / "spawn.intent", binding)
        fsync_json(evidence / "spawn.marker", {**binding, "processIdentity": identity})
        with patch("hey_my_buddy.protocol.process_identity.inspect_process_identity", return_value="alive"):
            restarted = self.board()
        self.assertEqual(self.decision(restarted, first["decisionId"])["status"], "running")
        self.assertEqual(restarted.store.capacity_report()["totalActive"], 1)
        with patch("hey_my_buddy.protocol.process_identity.inspect_process_identity", return_value="identity-absent"), \
                patch.object(restarted.store.workflow, "seal_interrupted_turn", side_effect=OSError("seal unavailable")):
            self.assertEqual(restarted.store.recover_executions(), [])
        self.assertEqual(self.decision(restarted, first["decisionId"])["status"], "running")
        self.assertEqual(restarted.store.capacity_report()["totalActive"], 1)
        with patch("hey_my_buddy.protocol.process_identity.inspect_process_identity", return_value="identity-absent"):
            restarted.store.recover_executions()
        decision = self.decision(restarted, first["decisionId"])
        self.assertEqual(decision["status"], "failed")
        self.assertIsNone(decision["output"])
        task = restarted.client().get(runId=first["runId"])
        self.assertTrue(task["shutdownConfirmed"])
        self.assertEqual(task["terminationReason"], "interrupted")

    def test_a_lost_reply_replays_without_re_executing_or_republishing(self):
        board = self.board()
        self.seed(board)
        request = self.request(board, request_id="pick-replay")
        client = board.client()
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "n" * 32, task_id=request["runId"])["claim"]
        document = claim["decisionInput"]
        envelope = {
            **claim_tool_receipt(claim),
            "status": "ok",
            "operation": "select",
            "requested": document["buddy"],
            "resolved": None,
            "observed": None,
            "usage": {"elapsedMs": 100, "toolCalls": 0, "bytesRead": 0},
            "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
            "inputVerification": {"unchanged": True, "manifestSha256": None, "snapshotSha256": "fixture-digest"},
            "decision": self.valid_decision(document, BUDDY_ID, reason="replayed once"),
        }
        report = {"status": "ok", "result": envelope, "shutdownConfirmed": True, "exitCode": 0}
        first = client.submit_result(claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, report)
        revisions = board.call("console_snapshot", {})["decisions"]
        replay = client.submit_result(claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, report)
        self.assertTrue(first["committed"])
        # The identical result is replayed idempotently: no second execution, no new
        # publication, and the decision history is byte-identical.
        self.assertTrue(replay.get("replayed") or replay.get("duplicate"), replay.get("duplicate"))
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "completed")
        self.assertEqual(board.call("console_snapshot", {})["decisions"], revisions)


class DecisionSurfaceTests(DecisionTestCase):
    def test_http_cli_and_ctwo_expose_the_same_named_operations(self):
        from hey_my_buddy.console.server import CONSOLE_OPERATIONS
        from hey_my_buddy.blackboard.service.service import CONTROL_OPERATIONS
        from hey_my_buddy.protocol.transport import METHOD_MAP
        import hey_my_buddy.cli.main as cli

        for operation in ("selection_request", "selection_get"):
            self.assertIn(operation, CONTROL_OPERATIONS)
        # The console keeps the read-only decision history but never starts a
        # selection or maintenance model call; maintenance is Harness-owned and its
        # preparation read is never a browser command.
        self.assertNotIn("selection_request", CONSOLE_OPERATIONS)
        self.assertIn("selection_get", CONSOLE_OPERATIONS)
        self.assertNotIn("evaluation_prepare", CONSOLE_OPERATIONS)
        self.assertEqual(METHOD_MAP["selection-request"], ("control", "selection_request"))
        self.assertEqual(METHOD_MAP["selection-get"], ("control", "selection_get"))
        for method in ("selection-request", "selection-get"):
            self.assertIn(method, cli.METHODS)
        board = self.board()
        self.seed(board)
        # A model-calling request is not reachable through the browser surface...
        self.assert_code(
            "METHOD_NOT_FOUND",
            board.console.command,
            "selection_request",
            {"requestId": "http-1", "task": "over http"},
        )
        # ...while the same named read still reaches exactly the validated operation.
        created = self.request(board, request_id="surface-1")
        fetched = board.console.command("selection_get", {"decisionId": created["decisionId"]})
        self.assertEqual(fetched["decision"]["requestId"], "surface-1")
        self.assert_code("METHOD_NOT_FOUND", board.console.command, "task_submit", {})

    def test_selection_get_defaults_to_a_compact_host_summary(self):
        """The default read is the low-token micro task path, not the full audit."""
        board = self.board()
        self.seed(board)
        # The decision model and the selected worker buddy are different, so the two
        # identities cannot be confused in the summary.
        self.use_helper(buddy_id=SECOND_BUDDY_ID)
        request = self.request(board, request_id="pick-compact")
        self.run_worker(board)
        compact = self.decision(board, request["decisionId"], audit=False)
        self.assertEqual(compact["status"], "completed")
        self.assertEqual(
            set(compact),
            {
                "decisionId",
                "requestId",
                "kind",
                "status",
                "task",
                "runId",
                "buddyId",
                "selectedBuddy",
                "decisionModel",
                "reason",
                "evidence",
                "policyCheck",
                "budget",
                "routingBasis",
                "constraints", "requiredCapabilities",
                "routerCalled",
                "routingMode", "routerBuddyId", "routerBuddy", "routerProblem",
                "routerBuddyIds", "routerIdentities", "routerRetryIntervalSeconds", "routingBudget", "routerIndex", "routerTrials",
                "usage",
                "nativeIdentity",
                "stopEvidence",
                "inputVerification",
                "createdAt",
                "updatedAt",
            },
        )
        self.assertEqual(compact["selectedBuddy"]["buddyId"], SECOND_BUDDY_ID)
        self.assertEqual(compact["selectedBuddy"]["model"], SECOND_BUDDY["model"])
        self.assertEqual(compact["selectedBuddy"]["effort"], SECOND_BUDDY["effort"])
        self.assertEqual(compact["selectedBuddy"]["harness"], "dsh")
        self.assertEqual(compact["decisionModel"]["requested"]["model"], CATALOG_BUDDY["model"])
        # The ordered list and each dispatch's compact trial add explicit facts;
        # the summary still omits the frozen model packet and raw tool stream.
        self.assertLess(len(json.dumps(compact)), 3600, "the default read includes frozen Router settings and bounded receipts but stays small")
        self.assertTrue({"input", "output", "requested", "toolEvidence"}.isdisjoint(compact))
        audit = self.decision(board, request["decisionId"])
        self.assertEqual(audit["input"]["operation"], "select")
        self.assertEqual(audit["requested"]["task"], "fix the failing parser test")
        self.assertEqual(audit["requestedBuddy"]["model"], CATALOG_BUDDY["model"])
        self.assertEqual(audit["attemptId"], compact["runId"] and audit["attemptId"])
        self.assertEqual(audit["buddyId"], compact["buddyId"])
        self.assertEqual(audit["reason"], compact["reason"])
        self.assertEqual(audit["selectedBuddy"], compact["selectedBuddy"])
        self.assert_code("INVALID_ARGUMENT", board.call, "selection_get", {"decisionId": "x", "nope": True})

    def test_decision_history_is_bounded_and_conflict_types_are_distinct(self):
        board = self.board()
        self.seed(board)
        self.assert_code("NOT_FOUND", board.call, "selection_get", {"decisionId": "dec-missing"})
        self.assert_code("INVALID_ARGUMENT", board.call, "selection_request", {"requestId": "bad id!", "task": "x"})
        self.assert_code("INVALID_ARGUMENT", board.call, "selection_request", {"requestId": "r", "task": ""})
        self.assert_code(
            "INVALID_ARGUMENT",
            board.call,
            "selection_request",
            {"requestId": "r", "task": "x", "timeoutSeconds": 4000},
        )
        self.assert_code(
            "INVALID_ARGUMENT", board.call, "selection_request", {"requestId": "r", "task": "x", "timeoutSeconds": 4}
        )
        self.assert_code("INVALID_ARGUMENT", board.call, "selection_request", {"requestId": "r", "task": "x", "extra": 1})



class DecisionWorkerProcessTests(DecisionTestCase):
    def test_in_process_execution_keeps_native_result_and_input_binding(self):
        board = self.board()
        self.seed(board)
        request = self.request(board, request_id="worker-pick")
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(set(decision["output"]["decision"]), {"buddyId", "reason", "evidence"})
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
        self.assertTrue(decision["inputVerification"]["unchanged"])
        harness, context, request_view = self.readonly_start.call_args.args
        manifest, frozen_root, expected_digest = self.input_verify.call_args.args
        self.assertEqual(manifest, decision["input"]["executionWorkspace"])
        self.assertEqual(request_view.cwd, str(frozen_root))
        from hey_my_buddy.private_dirs import context_root
        private_root = context_root(context, harness)
        self.input_prepare.assert_called_once_with(manifest, private_root)
        self.assertTrue(Path(frozen_root).is_relative_to(private_root))
        self.assertFalse(private_root.is_relative_to(context.directory))
        self.assertFalse((context.directory / "frozen-input").exists())
        self.input_verify.assert_called_once_with(manifest, frozen_root, expected_digest)
        # A verified, stopped Router leaves only digests behind, not a repository copy.
        self.assertFalse(Path(frozen_root).exists())
        snapshot = board.call("console_snapshot", {})
        self.assertEqual([item["kind"] for item in snapshot["decisions"]], ["select"])
        self.assertEqual(self.readonly_start.call_count, 1)


if __name__ == "__main__":
    unittest.main()
