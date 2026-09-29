"""Durable decision orchestration: selection and its fencing.

Every test here uses the production store, the production resource implementations
and the production Worker against a private state directory. The generic native call
is patched to a deterministic subprocess (``fixtures/mock_readonly.py``) that speaks
the read-only structured envelope, so the adapter, the owned process, the durable receipt and
the publication transaction are exercised for real without a model call.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest
from pathlib import Path

from support import BoardTestCase, wait_for
from test_evaluation import EvaluationTestCase as EvaluationFixtures

from buddy.errors import BoardError
from buddy import router
from fixtures import mock_readonly
from buddy.worker.worker import Worker

PROFILE_ID = "dsh:deepseek-official:deepseek-flash:off"
PROFILE = {
    "profileId": PROFILE_ID,
    "label": "DeepSeek-V41-Flash · off",
    "adapter": "dsh",
    "provider": "deepseek-official",
    "model": "deepseek-flash",
    "effort": "off",
    "available": True,
    "enabled": True,
    "capabilities": ["execution:dsh", "effort:off", "context:large"],
    "contextWindow": 1000000,
    "description": "fixture decision model",
    "source": "manual",
}
SECOND_PROFILE_ID = "dsh:deepseek-official:deepseek-v4-pro:high"
SECOND_PROFILE = {
    **PROFILE,
    "profileId": SECOND_PROFILE_ID,
    "label": "DeepSeek-V4-Pro · high",
    "model": "deepseek-v4-pro",
    "effort": "high",
    "capabilities": ["execution:dsh", "effort:high"],
}

class DecisionTestCase(BoardTestCase):
    model_claim = EvaluationFixtures.model_claim
    review_modelled_task = EvaluationFixtures.review_modelled_task
    modelled_task = EvaluationFixtures.modelled_task

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
        profiles=(PROFILE, SECOND_PROFILE),
        decision_profile: str | None = PROFILE_ID,
        preferences=None,
        cards=None,
    ) -> dict:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call(
            "evaluation_write_begin", {"requestId": "seed", "expectedRevision": revision, "kind": "human"}
        )
        result = board.console_call(
            "user_policy_publish",
            {
                "commandId": "seed-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                "profileSettings": [{"profileId": item["profileId"], "enabled": True} for item in profiles],
                "preferenceChanges": preferences or [],
                "configuration": {"defaultRoutingMode": "review", "reviewRouterProfileId": decision_profile},
            },
        )
        if cards:
            self.publish_cards(board, request_id="seed-cards", command_id="seed-cards", cards=cards)
        return result

    def card_with_pending_evidence(self, board) -> tuple[str, str]:
        """One published card plus one newer pending evidence row for maintenance."""
        first = self.evidence(board, "task-1", summary="card sample")
        self.publish_cards(
            board,
            request_id="card-1",
            command_id="card-1",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "before maintenance",
                    "strengths": ["fast"],
                    "limitations": ["small"],
                    "risks": ["still open: fixture risk"],
                    "evidenceIds": [first],
                }
            ],
        )
        second = self.evidence(board, "task-2", summary="new pending sample")
        return first, second

    def publish_cards(self, board, *, request_id: str, command_id: str, cards: list[dict]) -> dict:
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": request_id, "expectedRevision": revision, "kind": "maintenance"},
        )
        return board.call(
            "assessment_publish",
            {
                "commandId": command_id,
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": revision,
                "cards": cards,
            },
        )

    def publish_user_patch(self, board, *, request_id: str, command_id: str, **changes) -> dict:
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call("evaluation_write_begin", {"requestId": request_id, "expectedRevision": revision, "kind": "human"})
        return board.console_call("user_policy_publish", {
            "commandId": command_id, "writerId": grant["writerId"],
            "generation": grant["generation"], "writerToken": grant["writerToken"],
            "expectedRevision": revision, **changes,
        })

    def record(
        self,
        board,
        request_id: str = "task-1",
        *,
        model: str | None = None,
        effort: str | None = None,
        accept: bool = True,
    ) -> str:
        """A governed model artifact reviewed through the current Host path."""
        identity = {**PROFILE, "model": model or PROFILE["model"], "effort": effort or PROFILE["effort"]}
        return self.modelled_task(board, request_id, profile=identity, verdict="accepted" if accept else None)


    def evidence(
        self,
        board,
        request_id: str,
        *,
        summary: str = "accepted sample",
        profile: str = PROFILE_ID,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        run_id = self.record(board, request_id, model=model, effort=effort)
        recorded = board.call(
            "evaluation_evidence_record",
            {"profileId": profile, "kind": "task-success", "summary": summary, "source": "cli", "runId": run_id},
        )
        return recorded["evidence"]["evidenceId"]

    def run_worker(self, board, worker_id: str = "w-decision", iterations: int = 1) -> Worker:
        client = board.client()
        worker = Worker(worker_id, self.directory, client=client)
        worker.register()
        for _ in range(iterations):
            worker.run_once()
        return worker

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
    def valid_decision(cls, document: dict, profile_id: str | None, *,
                       reason: str = "fixture selection", evidence: list[dict] | None = None) -> dict:
        return {"profileId": profile_id, "reason": reason, "evidence": list(evidence or [])}


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
        self.assertEqual([p["profileId"] for p in queued["input"]["profiles"]],
                         [PROFILE_ID, SECOND_PROFILE_ID])
        # The queued recommendation is cancelled through the ordinary task operation.
        board.call("task_cancel", {"runId": first["runId"], "reason": "not needed"})
        self.assertEqual(self.decision(board, first["decisionId"])["status"], "cancelled")
        # The idempotent replay still resolves the same decision after it terminated.
        replayed = self.request(board)
        self.assertEqual(replayed["status"], "cancelled")
        self.assertTrue(replayed["duplicate"])

    def test_recommendation_never_creates_or_authorizes_a_business_task(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["profileId"], PROFILE_ID)
        self.assertIn("mock select chose", decision["reason"])
        # Exactly the decision's own run exists; no business task was submitted.
        tasks = board.call("task_list", {"limit": 50})["runs"]
        self.assertEqual([task["runId"] for task in tasks], [request["runId"]])
        self.assertEqual(tasks[0]["adapter"], "decision")
        # The Host decides and starts work explicitly: the recommendation returns the
        # parameters, and the decision run itself is not an execution permit.
        self.assertEqual(decision["requestedProfile"]["model"], PROFILE["model"])
        self.assertEqual(decision["resolvedProfile"]["reasoningEffort"], PROFILE["effort"])
        self.assertIsNone(decision["observedProfile"])
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
        self.assert_code("UNSUPPORTED", board.call, "task_retry", {"runId": request["runId"]})
        self.assert_code(
            "UNSUPPORTED", board.call, "task_acknowledge", {"runId": request["runId"], "note": "looks good"}
        )

    def test_real_worker_publishes_a_bounded_input_and_records_evidence_references(self):
        board = self.board()
        self.seed(board)
        run_id = self.record(board, "task-1")
        first_evidence = board.call(
            "evaluation_evidence_record",
            {
                "profileId": PROFILE_ID,
                "kind": "task-success",
                "summary": "card sample",
                "source": "cli",
                "runId": run_id,
            },
        )["evidence"]["evidenceId"]
        self.publish_cards(
            board,
            request_id="card-1",
            command_id="card-1",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "solid",
                    "strengths": ["fast"],
                    "limitations": [],
                    "risks": ["fixture risk"],
                    "evidenceIds": [first_evidence],
                }
            ],
        )
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["profileId"], PROFILE_ID)
        self.assertEqual(decision["evidence"], [{"kind": "card", "ref": PROFILE_ID}])
        document = decision["input"]
        self.assertEqual(document["operation"], "select")
        self.assertEqual(document["requestId"], "pick-1")
        self.assertEqual(document["task"], "fix the failing parser test")
        self.assertEqual(document["tableRevision"], decision["expectedRevision"])
        self.assertEqual(
            [profile["profileId"] for profile in document["profiles"]], [PROFILE_ID, SECOND_PROFILE_ID]
        )
        self.assertEqual(document["cards"][0]["risks"], ["fixture risk"])
        self.assertEqual(document["cards"][0]["origin"], "maintenance")
        self.assertEqual(document["evidence"][0]["evidenceId"], first_evidence)
        self.assertEqual(document["evidence"][0]["project"], None)
        # The persisted audit input carries the bounded table and evidence summaries
        # only: no credential, no raw project log, no service token.
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
        self.seed(board, preferences=[{"profileId": PROFILE_ID, "mode": "exclude", "reason": "user excluded"}])
        request = self.request(board)
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["profileId"], SECOND_PROFILE_ID)
        self.assertEqual([profile["profileId"] for profile in decision["input"]["profiles"]], [SECOND_PROFILE_ID])

        # A pin limits the candidate set; a recommendation outside it is refused.
        self.use_helper(profile_id=PROFILE_ID)
        self.publish_user_patch(
            board,
            request_id="pin-1",
            command_id="pin-1",
            preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "pin", "reason": "only this one"}],
        )
        pinned = self.request(board, request_id="pick-2")
        self.run_worker(board, worker_id="w-pinned")
        pinned_decision = self.decision(board, pinned["decisionId"])
        self.assertEqual(pinned_decision["status"], "needs-host")
        self.assertEqual(pinned_decision["output"]["code"], "router-out-of-bounds")
        self.assertEqual(
            [profile["profileId"] for profile in pinned_decision["input"]["profiles"]], [SECOND_PROFILE_ID]
        )
        # The Router input carries the effective preference with its source.
        self.assertEqual(pinned_decision["input"]["preferences"],
                         [{"profileId": SECOND_PROFILE_ID, "mode": "pin",
                           "reason": "only this one", "source": "override"}])

    def test_requested_capabilities_filter_the_candidate_set(self):
        board = self.board()
        self.seed(board)
        request = board.call(
            "selection_request",
            {"requestId": "pick-caps", "task": "needs image input", "requiredCapabilities": ["input:image"]},
        )
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual([profile["profileId"] for profile in decision["input"]["profiles"]], [PROFILE_ID])
        no_candidate = board.call(
            "selection_request",
            {"requestId": "pick-none", "task": "impossible", "requiredCapabilities": ["capability:nonexistent"]},
        )
        self.assertEqual(no_candidate["status"], "needs-host")
        self.assertIsNone(no_candidate["runId"])
        self.assertIn("legal candidate", self.decision(board, no_candidate["decisionId"])["reason"])

    def test_new_pin_and_exclusion_cannot_adopt_an_old_frozen_candidate(self):
        board = self.board()
        self.seed(board)
        for mode, chosen, bounded in (("pin", SECOND_PROFILE_ID, PROFILE_ID),
                                     ("exclude", PROFILE_ID, PROFILE_ID)):
            with self.subTest(mode=mode):
                # Each case begins with both profiles legal, then changes the hard
                # policy after request. Claim must retain the original input.
                with board.store.db.write() as connection:
                    connection.execute("DELETE FROM evaluation_preferences")
                self.use_helper(profile_id=chosen)
                request = self.request(board, request_id=f"freeze-{mode}")
                frozen = self.decision(board, request["decisionId"])["input"]
                self.assertEqual([p["profileId"] for p in frozen["profiles"]], [PROFILE_ID, SECOND_PROFILE_ID])
                self.publish_user_patch(board, request_id=f"policy-{mode}", command_id=f"policy-{mode}",
                                        preferenceChanges=[{"profileId": bounded, "mode": mode, "reason": "new hard bound"}])
                self.run_worker(board, worker_id=f"w-freeze-{mode}")
                decision = self.decision(board, request["decisionId"])
                self.assertEqual(decision["input"], frozen)
                self.assertEqual(decision["status"], "needs-host")
                self.assertEqual(decision["output"]["code"], "router-out-of-bounds")
                self.assertIsNone(decision["selectedProfile"])

    def test_fixed_fields_and_capabilities_are_rechecked_at_adoption(self):
        board = self.board()
        self.seed(board)
        for name, constraints, field, replacement in (
            ("fixed", {"effort": "high"}, "effort", "off"),
            ("capability", {"requiredCapabilities": ["execution:dsh"]}, "capabilities_json", "[]"),
        ):
            with self.subTest(name=name):
                self.use_helper(profile_id=SECOND_PROFILE_ID)
                if name == "fixed":
                    # Only workflow routing carries fixed configuration fields.
                    # Supply the same internal request used by workflow admission.
                    request = board.store.decisions._create(
                        request_id=f"freeze-{name}", request={"kind": "select", "task": "bounded selection",
                            "constraints": constraints, "requiredCapabilities": [], "routingPreferences": [],
                            "timeoutSeconds": 60, "budget": router.budget("brief")})
                else:
                    request = board.call("selection_request", {"requestId": f"freeze-{name}",
                                         "task": "bounded selection", **constraints})
                frozen = self.decision(board, request["decisionId"])["input"]
                with board.store.db.write() as connection:
                    before = connection.execute(f"SELECT {field} FROM evaluation_profiles WHERE profile_id=?", (SECOND_PROFILE_ID,)).fetchone()[0]
                    connection.execute(f"UPDATE evaluation_profiles SET {field}=? WHERE profile_id=?", (replacement, SECOND_PROFILE_ID))
                self.run_worker(board, worker_id=f"w-freeze-{name}")
                decision = self.decision(board, request["decisionId"])
                self.assertEqual(decision["input"], frozen)
                self.assertEqual(decision["status"], "needs-host")
                self.assertEqual(decision["output"]["code"], "router-out-of-bounds")
                with board.store.db.write() as connection:
                    connection.execute(f"UPDATE evaluation_profiles SET {field}=? WHERE profile_id=?", (before, SECOND_PROFILE_ID))

    def test_missing_decision_profile_is_needs_host_without_a_model_call(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        request = self.request(board)
        self.assertEqual(request["status"], "needs-host")
        self.assertIsNone(request["runId"])
        self.assertIn("Router is not configured", self.decision(board, request["decisionId"])["reason"])
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_native_fixture_absence_is_an_honest_adapter_unavailable_outcome(self):
        board = self.board()
        self.seed(board)
        self.use_helper(path=self.directory / "missing-readonly")
        with board.store.db.write() as connection:
            connection.execute("UPDATE harness_health SET status='missing' WHERE adapter='dsh'")
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertFalse(capabilities["selection"])
        self.assertFalse(capabilities["maintenance"])
        self.assertFalse(capabilities["decisionAdapter"])
        request = self.request(board)
        self.assertEqual(request["status"], "needs-host")
        self.assertIsNone(request["runId"])
        self.assertIn("available", self.decision(board, request["decisionId"])["reason"])
        # Maintenance is not a blackboard model call at all any more, so an absent
        # helper cannot report it as an adapter-unavailable maintenance failure.
        self.assertFalse(capabilities["maintenance"])
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_capabilities_report_real_availability(self):
        board = self.board()
        self.seed(board)
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertTrue(capabilities["selection"])
        self.assertFalse(capabilities["maintenance"])
        self.assertTrue(capabilities["decisionAdapter"])
        report = board.call("capabilities", {})
        self.assertIn("decision", report["adapters"])
        self.assertIn("decision", report["localCapabilities"])
        self.assertNotIn("selection", report["limitations"])
        self.assertIn("selectionFallback", report["limitations"])

    def test_public_task_submit_cannot_create_a_decision_run(self):
        board = self.board()
        self.assert_code(
            "UNSUPPORTED_ADAPTER",
            board.call,
            "task_submit",
            {"requestId": "forged", "task": "x", "cwd": str(self.workdir()), "adapter": "decision"},
        )


class DecisionGateTests(DecisionTestCase):
    def test_writer_gate_keeps_request_frozen_until_claim(self):
        board = self.board()
        self.seed(board)
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin", {"requestId": "w-active", "expectedRevision": revision, "kind": "maintenance"}
        )
        request = self.request(board)
        self.run_worker(board)
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["queueReason"], "evaluation-writer-pending")
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "queued")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["phase"], "writing")

        board.call(
            "assessment_publish",
            {
                "commandId": "w-active",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": revision,
                "cards": [
                    {
                        "profileId": SECOND_PROFILE_ID,
                        "summary": "published while the selection waited",
                        "strengths": [],
                        "limitations": [],
                        "risks": [],
                        "evidenceIds": [],
                    }
                ],
            },
        )
        self.run_worker(board, worker_id="w-after")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["expectedRevision"], revision)
        self.assertEqual(decision["input"]["tableRevision"], revision)
        self.assertEqual([card["profileId"] for card in decision["input"]["cards"]], [])

    def test_one_slot_maintenance_patch_runs_before_the_queued_selector_without_deadlock(self):
        """The external Harness patch is an ordinary writer intent, not a model job."""
        board = self.board(max_concurrent=1)
        self.seed(board)
        first, second = self.card_with_pending_evidence(board)
        selection = self.request(board)
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "tidy-1", "expectedRevision": revision, "kind": "maintenance"},
        )
        self.assertEqual(grant["phase"], "writing")
        worker = self.run_worker(board, worker_id="w-slot")
        # The queued selector cannot claim while the maintenance writer holds the gate.
        self.assertEqual(self.decision(board, selection["decisionId"])["status"], "queued")
        published = board.call(
            "assessment_publish",
            {
                "commandId": "tidy-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": revision,
                "cards": [
                    {
                        "profileId": PROFILE_ID,
                        "summary": "card synthesized by the external Harness",
                        "strengths": ["fast"],
                        "limitations": ["small"],
                        "risks": ["still open: fixture risk"],
                        "evidenceIds": [first, second],
                    }
                ],
            },
        )
        self.assertEqual(published["revision"], revision + 1)
        worker.run_once()
        selected = self.decision(board, selection["decisionId"])
        self.assertEqual(selected["status"], "completed")
        self.assertEqual(selected["expectedRevision"], revision)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 0)

    def test_admitted_reader_drains_before_the_writer_and_the_released_reader_fails_honestly(self):
        board = self.board(max_concurrent=2)
        self.seed(board)
        first, second = self.card_with_pending_evidence(board)
        selection = self.request(board)
        client = board.client()
        client.register_worker("w-reader", adapter="dsh", capabilities=["dsh", "decision"])
        reader_claim = client.claim(
            "w-reader", "claim-reader", "n" * 32, task_id=selection["runId"]
        )["claim"]
        self.assertIsNotNone(reader_claim)
        self.assertEqual(self.decision(board, selection["decisionId"])["status"], "running")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 1)

        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "tidy-1", "expectedRevision": revision, "kind": "maintenance"},
        )
        self.assertEqual(grant["state"], "waiting")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["waitingWriters"], 1)
        # A queued selector is refused admission while the writer waits; it is not
        # silently admitted against a revision the writer is about to move.
        self.assert_code("TABLE_BUSY", board.call, "evaluation_reader_begin", {"kind": "selection"})

        # The admitted reader gives the attempt back without ever spawning a helper.
        client.release(
            "w-reader",
            reader_claim["attempt"]["attemptId"],
            reader_claim["attempt"]["generation"],
            "n" * 32,
            "the reader never spawned a helper",
            evidence={"spawnIntentWritten": False},
        )
        self.assertEqual(self.decision(board, selection["decisionId"])["status"], "failed")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)
        self.assertEqual(board.call("console_snapshot", {})["gate"]["phase"], "writing")
        # The writer is granted once the reader settled, and its card-only patch commits.
        published = board.call(
            "assessment_publish",
            {
                "commandId": "tidy-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": revision,
                "cards": [
                    {
                        "profileId": PROFILE_ID,
                        "summary": "patched after the reader drained",
                        "strengths": ["fast"],
                        "limitations": ["small"],
                        "risks": ["still open: fixture risk"],
                        "evidenceIds": [first, second],
                    }
                ],
            },
        )
        self.assertEqual(published["revision"], revision + 1)
        self.assertEqual(board.call("console_snapshot", {})["gate"]["phase"], "open")

    def test_queued_selection_cancel_is_durable_and_never_runs(self):
        board = self.board()
        self.seed(board)
        revision = board.call("console_snapshot", {})["tableRevision"]
        board.call("evaluation_write_begin", {"requestId": "w-hold", "expectedRevision": revision, "kind": "maintenance"})
        request = self.request(board)
        board.call("task_cancel", {"runId": request["runId"], "reason": "user cancelled the pending selection"})
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "cancelled")
        self.run_worker(board)
        self.assertEqual(board.call("task_get", {"runId": request["runId"]})["task"]["status"], "cancelled")
        self.assertIsNone(self.decision(board, request["decisionId"])["attemptId"])


class DecisionFailureTests(DecisionTestCase):
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
        with patch('buddy.router_input.prepare', side_effect=BoardError('router-input-changed', 'changed')):
            decision = self.outcome(board, 'select_first')
        self.assertEqual(decision['status'], 'needs-host')
        self.assertEqual(board.store.decisions.health_summary()['inputChangedCount'], 1)
        self.readonly_start.assert_not_called()

    def test_malformed_native_output_fails_without_touching_the_table(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "malformed")
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(decision["output"]["code"], "invalid-native-result")
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)

    def test_native_error_envelope_fails_honestly(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "error")
        self.assertEqual(decision["status"], "failed")
        self.assertIn("call-timeout", decision["error"])
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)

    def test_out_of_candidate_is_not_adopted_and_has_its_own_health_count(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "out_of_candidate")
        self.assertEqual(decision["status"], "needs-host")
        self.assertEqual(decision["output"]["code"], "router-out-of-bounds")
        self.assertIsNone(decision["selectedProfile"])
        health = board.call("health", {})["routingHealth"]
        self.assertEqual(health["boundsRejectedCount"], 1)
        self.assertEqual(health["failureCount"], 0)
        self.assertEqual(health["abstentionCount"], 0)

    def test_unknown_card_reference_is_recorded_before_adoption(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "foreign_evidence")
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["evidence"], [{"kind": "card", "ref": "card-not-supplied"}])
        self.assertEqual(decision["output"]["decision"]["evidence"], decision["evidence"])

    def test_positive_dsh_task_preference_records_program_outcome(self):
        board = self.board()
        self.seed(board)
        request = board.call("selection_request", {
            "requestId": "pick-positive", "task": "work on the DSH harness source files",
            "routingPreferences": [{"match": {"adapter": "dsh"}, "reason": "Use the installed DSH harness"}],
        })
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["input"]["policyFacts"]["hardConstraints"], {})
        self.assertEqual(decision["input"]["policyFacts"]["taskPreference"],
                         {"ruleIndex": 0, "matchingProfileIds": [PROFILE_ID, SECOND_PROFILE_ID]})
        check = decision["output"]["policyCheck"]
        self.assertEqual(check["taskPreference"], {"ruleIndex": 0, "outcome": "matched"})
        self.assertEqual(check["hardConstraints"], {})
        self.assertNotIn("policyCheck", decision["output"]["decision"])
        self.assertEqual(decision["policyCheck"], check)

    def test_alternative_without_cards_or_annotations_completes_and_is_audited(self):
        board = self.board()
        self.seed(board, preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "prefer flash"}])
        self.use_helper(profile_id=SECOND_PROFILE_ID, evidence="none")
        request = board.call("selection_request", {
            "requestId": "pick-alternative", "task": "choose for this task",
            "routingPreferences": [{"match": {"model": "deepseek-flash"}, "reason": "economical"}],
        })
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["profileId"], SECOND_PROFILE_ID)
        self.assertEqual(decision["output"]["policyCheck"]["userPreference"], "alternative")
        self.assertEqual(decision["output"]["policyCheck"]["taskPreference"],
                         {"ruleIndex": 0, "outcome": "alternative"})
        self.assertEqual(decision["evidence"], [])
        self.assertEqual(decision["input"]["cards"], [])
        self.assertEqual(decision["input"]["annotations"], [])

    def test_answer_shape_json_and_path_boundaries_keep_one_attempt_each(self):
        board = self.board()
        self.seed(board)
        for mode, code in (("answer_error", "answer-invalid-json"), ("answer_extra", "answer-shape"),
                           ("answer_bad_evidence", "answer-shape"), ("answer_escape", "answer-shape")):
            with self.subTest(mode=mode):
                decision = self.outcome(board, mode, request_id=mode)
                self.assertEqual(decision["status"], "needs-host")
                self.assertEqual(decision["output"]["code"], code)
                task = board.call("task_get", {"runId": decision["runId"]})["task"]
                self.assertEqual(task["status"], "failed")
                self.assertTrue(task["shutdownConfirmed"])
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                                        (decision["runId"],)).fetchone()[0], 1)
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)

    def test_json_string_answer_is_parsed_by_the_generic_collector(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "json_answer")
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(set(decision["output"]["decision"]), {"profileId", "reason", "evidence"})
        self.assertEqual(decision["usage"], {"elapsedMs": 200, "toolCalls": 1, "bytesRead": 33})
        self.assertEqual(decision["nativeIdentity"]["sessionId"], "mock-native")

    def test_budget_and_input_changed_are_separate_from_failures_and_abstentions(self):
        board = self.board()
        self.seed(board)
        for mode, code in (("budget", "router-budget-exhausted"), ("deadline", "router-budget-exhausted"),
                           ("input_changed", "router-input-changed")):
            decision = self.outcome(board, mode, request_id=mode)
            self.assertEqual(decision["status"], "needs-host")
            self.assertEqual(decision["output"]["code"], code)
            self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
            self.assertIsNone(decision["selectedProfile"])
        self.assertFalse(decision["inputVerification"]["unchanged"])
        # A changed copy is kept for inspection.
        _, changed_root, _ = self.input_verify.call_args.args
        self.assertTrue(Path(changed_root).exists())
        report = board.call("health", {})["routingHealth"]
        self.assertEqual(report["budgetExhaustedCount"], 2)
        self.assertEqual(report["inputChangedCount"], 1)
        self.assertEqual(report["failureCount"], 0)
        self.assertEqual(report["abstentionCount"], 0)
        self.assertEqual(report["consecutiveFailures"], 0)

    def test_missing_native_result_keeps_infrastructure_failure_semantics(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "protocol_error")
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(decision["output"]["code"], "invalid-native-result")

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
        self.assertIsNone(decision["profileId"])
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
        worker = Worker("w-cancel", self.directory, client=board.client())
        worker.register()
        thread = threading.Thread(target=worker.run_once, daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: self.readonly_start.called, timeout=20))
        board.call("task_cancel", {"runId": request["runId"], "reason": "operator stopped it"})
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "the worker did not observe the cancel")
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "cancelled")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertFalse(task["shutdownConfirmed"])
        self.assertEqual(task["attemptState"], "uncertain")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)

    def test_worker_deadline_is_bounded_without_inventing_native_stop_evidence(self):
        board = self.board()
        self.seed(board)
        self.use_helper(mode="sleep", sleep="40")
        request = board.call("selection_request", {"requestId": "pick-deadline", "task": "slow", "timeoutSeconds": 5})
        started = time.monotonic()
        self.run_worker(board, worker_id="w-deadline")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "failed")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertFalse(task["shutdownConfirmed"])
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)

    def test_restart_fences_a_running_decision_and_a_late_result_never_publishes(self):
        board = self.board()
        self.seed(board)
        first = self.request(board, request_id="pick-restart")
        client = board.client()
        client.register_worker("w-restart", adapter="dsh", capabilities=["dsh", "decision"])
        claim = client.claim("w-restart", "claim-restart", "n" * 32, task_id=first["runId"])["claim"]
        self.assertIsNotNone(claim)
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 1)

        # A fresh store over the same state directory is exactly what a daemon restart
        # does: in-flight attempts become uncertain and the decision is fenced.
        restarted = self.board()
        decision = self.decision(restarted, first["decisionId"])
        self.assertEqual(decision["status"], "stale")
        self.assertIn("restarted", decision["reason"])
        self.assertEqual(restarted.call("console_snapshot", {})["gate"]["readers"], 0)

        envelope = {
            "status": "ok",
            "operation": "select",
            "tableRevision": decision["expectedRevision"],
            "requested": {"provider": PROFILE["provider"], "model": PROFILE["model"], "reasoningEffort": PROFILE["effort"]},
            "resolved": None,
            "observed": None,
            "usage": {"elapsedMs": 100, "toolCalls": 0, "bytesRead": 0},
            "stopEvidence": {"shutdownConfirmed": True},
            "decision": self.valid_decision(claim["decisionInput"], PROFILE_ID, reason="too late"),
        }
        restarted.client().submit_result(
            "w-restart",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {"status": "ok", "result": envelope, "shutdownConfirmed": True, "exitCode": 0},
        )
        late = self.decision(restarted, first["decisionId"])
        self.assertEqual(late["status"], "stale")
        self.assertIsNone(late["profileId"])
        self.assertEqual(late["output"]["decision"]["reason"], "too late")
        self.assertEqual(restarted.call("console_snapshot", {})["tableRevision"], 2)

    def test_a_lost_reply_replays_without_re_executing_or_republishing(self):
        board = self.board()
        self.seed(board)
        request = self.request(board, request_id="pick-replay")
        client = board.client()
        client.register_worker("w-replay", adapter="dsh", capabilities=["dsh", "decision"])
        claim = client.claim("w-replay", "claim-replay", "n" * 32, task_id=request["runId"])["claim"]
        document = claim["decisionInput"]
        envelope = {
            "status": "ok",
            "operation": "select",
            "tableRevision": document["tableRevision"],
            "requested": document["profile"],
            "resolved": None,
            "observed": None,
            "usage": {"elapsedMs": 100, "toolCalls": 0, "bytesRead": 0},
            "stopEvidence": {"shutdownConfirmed": True},
            "decision": self.valid_decision(document, PROFILE_ID, reason="replayed once"),
        }
        report = {"status": "ok", "result": envelope, "shutdownConfirmed": True, "exitCode": 0}
        first = client.submit_result(
            "w-replay", claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, report
        )
        revisions = board.call("console_snapshot", {})["decisions"]
        replay = client.submit_result(
            "w-replay", claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, report
        )
        self.assertTrue(first["committed"])
        # The identical result is replayed idempotently: no second execution, no new
        # publication, and the decision history is byte-identical.
        self.assertTrue(replay.get("replayed") or replay.get("duplicate"), replay.get("duplicate"))
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "completed")
        self.assertEqual(board.call("console_snapshot", {})["decisions"], revisions)


class DecisionSurfaceTests(DecisionTestCase):
    def test_http_cli_and_ctwo_expose_the_same_named_operations(self):
        from buddy.console import CONSOLE_OPERATIONS
        from buddy.service import CONTROL_OPERATIONS
        from buddy.transport import METHOD_MAP
        import buddy.cli as cli

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
        """The default read is the low-token delegation path, not the full audit."""
        board = self.board()
        self.seed(board)
        # The decision model and the selected worker profile are different, so the two
        # identities cannot be confused in the summary.
        self.use_helper(profile_id=SECOND_PROFILE_ID)
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
                "profileId",
                "selectedProfile",
                "decisionModel",
                "tableRevision",
                "expectedRevision",
                "publishedRevision",
                "noOp",
                "reason",
                "evidence",
                "policyCheck",
                "budget",
                "routingMode", "requestedRoutingMode", "fallback",
                "usage",
                "nativeIdentity",
                "stopEvidence",
                "inputVerification",
                "createdAt",
                "updatedAt",
                "pendingEvidenceRemaining",
            },
        )
        self.assertEqual(compact["selectedProfile"]["profileId"], SECOND_PROFILE_ID)
        self.assertEqual(compact["selectedProfile"]["model"], SECOND_PROFILE["model"])
        self.assertEqual(compact["selectedProfile"]["effort"], SECOND_PROFILE["effort"])
        self.assertEqual(compact["selectedProfile"]["adapter"], "dsh")
        self.assertEqual(compact["decisionModel"]["requested"]["model"], PROFILE["model"])
        self.assertLess(len(json.dumps(compact)), 2200, "the default read includes mode and fallback facts but stays small")
        audit = self.decision(board, request["decisionId"])
        self.assertEqual(audit["input"]["operation"], "select")
        self.assertEqual(audit["requested"]["task"], "fix the failing parser test")
        self.assertEqual(audit["requestedProfile"]["model"], PROFILE["model"])
        self.assertEqual(audit["attemptId"], compact["runId"] and audit["attemptId"])
        self.assertEqual(audit["profileId"], compact["profileId"])
        self.assertEqual(audit["reason"], compact["reason"])
        self.assertEqual(audit["selectedProfile"], compact["selectedProfile"])
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


class DecisionGrowthTests(DecisionTestCase):
    def seed_history(self, board, count: int) -> None:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        self.seed(board)
        for index in range(count):
            board.call(
                "evaluation_evidence_record",
                {
                    "profileId": PROFILE_ID,
                    "kind": "observation",
                    "summary": f"observation {index}",
                    "source": "cli",
                    "project": "fixture",
                },
            )

    def test_hot_path_reads_fixed_size_counters_and_never_scans_the_ledger(self):
        board = self.board()
        self.seed_history(board, 150)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 150)
        statements: list[str] = []
        original = board.store.db._configure

        def configure(connection: sqlite3.Connection) -> None:
            original(connection)
            connection.set_trace_callback(statements.append)

        board.store.db._configure = configure
        try:
            snapshot = board.call("console_snapshot", {})
        finally:
            board.store.db._configure = original
        self.assertEqual(snapshot["pendingEvidence"], 150)
        self.assertFalse([sql for sql in statements if "evaluation_samples" in sql])
        evidence_statements = [sql for sql in statements if "evaluation_evidence" in sql]
        self.assertTrue(evidence_statements)
        for sql in evidence_statements:
            self.assertIn("LIMIT", sql.upper(), sql)
            self.assertNotIn("json_each", sql)
        for sql in evidence_statements:
            with board.store.db.read() as connection:
                plan = [row["detail"] for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}")]
            # The newest-evidence page walks a bounded index range; it is never an
            # unindexed table scan and never a temporary sort of the archive.
            self.assertNotIn("SCAN evaluation_evidence", plan)
            self.assertFalse([detail for detail in plan if "TEMP B-TREE" in detail], plan)
            self.assertTrue(
                [detail for detail in plan if "evaluation_evidence" in detail and "USING" in detail], plan
            )
        # The counters themselves are single primary-key lookups, not recounts.
        for call in (
            lambda connection: board.evaluation._pending_evidence(connection),
            lambda connection: board.evaluation._sample_count(connection, PROFILE_ID),
        ):
            statements.clear()
            board.store.db._configure = configure
            try:
                with board.store.db.read() as connection:
                    call(connection)
            finally:
                board.store.db._configure = original
            aggregate = [sql for sql in statements if "evaluation_aggregates" in sql]
            self.assertEqual(len(aggregate), 1, statements)
            self.assertNotIn("COUNT", aggregate[0].upper())
            self.assertFalse([sql for sql in statements if "evaluation_samples" in sql])

    def test_counters_never_drift_from_the_ledgers(self):
        board = self.board()
        self.seed_history(board, 20)
        evidence_id = self.evidence(board, "task-1")
        self.publish_cards(
            board,
            request_id="card-1",
            command_id="card-1",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "consumes one reference",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [evidence_id],
                }
            ],
        )
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 20)
        # Maintenance retirement keeps the earlier evidence incorporated in card history.
        self.publish_cards(
            board,
            request_id="card-2",
            command_id="card-2",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "released the reference",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [],
                }
            ],
        )
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 20)
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)).fetchone())
            history = connection.execute("SELECT evidence_ids_json FROM evaluation_card_history WHERE profile_id=? ORDER BY table_revision", (PROFILE_ID,)).fetchall()
            self.assertTrue(any(evidence_id in json.loads(row["evidence_ids_json"]) for row in history))
            ledger = connection.execute("SELECT COUNT(*) AS count FROM evaluation_evidence_pending").fetchone()["count"]
            self.assertEqual(board.evaluation._pending_evidence(connection), int(ledger))
            samples = connection.execute("SELECT COUNT(*) AS count FROM evaluation_samples").fetchone()["count"]
            self.assertEqual(board.evaluation._sample_count(connection, PROFILE_ID), int(samples))

class DecisionWorkerProcessTests(DecisionTestCase):
    def test_independent_worker_records_native_logs_and_card_publication_stays_external(self):
        board = self.board()
        self.seed(board)
        request = self.request(board, request_id="worker-pick")
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        attempt_dir = self.directory / "attempts" / decision["runId"] / decision["attemptId"]
        envelope = json.loads((attempt_dir / "runner.stdout.log").read_text())
        self.assertEqual(set(envelope["rawAnswer"]), {"profileId", "reason", "evidence"})
        self.assertTrue(envelope["processState"]["shutdownConfirmed"])
        self.assertTrue(decision["stopEvidence"]["shutdownConfirmed"])
        self.assertTrue(decision["inputVerification"]["unchanged"])
        native, context, request_view = self.readonly_start.call_args.args
        manifest, frozen_root, expected_digest = self.input_verify.call_args.args
        self.assertEqual(manifest, decision["input"]["executionWorkspace"])
        self.assertEqual(request_view.cwd, str(frozen_root))
        self.input_prepare.assert_called_once_with(manifest, context.directory)
        self.input_verify.assert_called_once_with(manifest, frozen_root, expected_digest)
        # A verified, stopped Router leaves only digests behind, not a repository copy.
        self.assertFalse(Path(frozen_root).exists())
        self.publish_cards(board, request_id="worker-card", command_id="worker-card", cards=[{
            "profileId": PROFILE_ID, "summary": "patched by the external Harness",
            "strengths": [], "limitations": [], "risks": ["open fixture risk"], "evidenceIds": [],
        }])
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(snapshot["cards"][0]["summary"], "patched by the external Harness")
        self.assertFalse(snapshot["capabilities"]["maintenance"])
        self.assertEqual([item["kind"] for item in snapshot["decisions"]], ["select"])
        self.assertEqual(self.readonly_start.call_count, 1)


if __name__ == "__main__":
    unittest.main()
