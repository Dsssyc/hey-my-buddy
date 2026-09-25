"""Durable decision orchestration: selection and its fencing.

Every test here uses the production store, the production resource implementations
and the production Worker against a private state directory. The bounded decision
helper is a deterministic stand-in (``fixtures/mock_decision_helper.py``) that speaks
the real CLI and envelope, so the adapter, the owned process, the durable receipt and
the publication transaction are exercised for real without a model call.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import unittest
from pathlib import Path

from support import BoardTestCase, wait_for
from test_evaluation import EvaluationTestCase as EvaluationFixtures

from buddy.errors import BoardError
from buddy import selection_policy
from buddy.worker.worker import Worker

MOCK_HELPER = Path(__file__).resolve().parent / "fixtures" / "mock_decision_helper.py"

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

MOCK_ENV_KEYS = (
    "BUDDY_DECISION_HELPER",
    "MOCK_DECISION_MODE",
    "MOCK_DECISION_PROFILE_ID",
    "MOCK_DECISION_EVIDENCE",
    "MOCK_DECISION_SLEEP",
    "MOCK_DECISION_SURVIVOR",
)


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
        previous = {key: os.environ.get(key) for key in MOCK_ENV_KEYS}
        os.environ["BUDDY_DECISION_HELPER"] = str(path if path is not None else MOCK_HELPER)
        for key, value in mode.items():
            os.environ[f"MOCK_DECISION_{key.upper()}"] = value

        def restore() -> None:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)

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
                "configuration": {"decisionProfileId": decision_profile},
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
    def valid_decision(
        cls,
        document: dict,
        profile_id: str | None,
        *,
        reason: str = "fixture selection",
        evidence_ids: list[str] | None = None,
        policy_check: dict | None = None,
        support: dict | None = None,
    ) -> dict:
        """One strict-shape decision carrying the program-derived policy check.

        The derived ``policyCheck`` is exactly what the helper's typed contract
        requires for ``profile_id``; when the derived outcome is an alternative,
        eligible annotation/card support is cited from the frozen document so the
        answer is adoptable. Tests pass ``policy_check``/``support`` explicitly
        to exercise refusal paths.
        """
        empty = {"cardProfileIds": [], "annotationProfileIds": []}
        if profile_id is None:
            return {"profileId": None, "reason": reason, "evidenceIds": [], "policyCheck": None, "support": empty}
        facts = document["policyFacts"]
        routing_preferences = document.get("routingPreferences") or []
        check = policy_check if policy_check is not None else selection_policy.expected_policy_check(
            facts, routing_preferences, profile_id
        )
        built = dict(empty)
        evidence = list(evidence_ids or [])
        if support is not None:
            built = dict(support)
        elif selection_policy.alternative_requires_support(check) and not evidence:
            scoped = {
                profile_id,
                *facts["taskPreference"]["matchingProfileIds"],
                *facts["userPreferredProfileIds"],
            }
            annotations: list[str] = []
            for entry in document.get("annotations") or []:
                if entry["profileId"] in scoped and entry["profileId"] not in annotations:
                    annotations.append(entry["profileId"])
            if annotations:
                built["annotationProfileIds"] = annotations[: selection_policy.MAX_SUPPORT_IDS]
            else:
                cards: list[str] = []
                for entry in document.get("cards") or []:
                    if entry["profileId"] in scoped and entry["profileId"] not in cards:
                        cards.append(entry["profileId"])
                built["cardProfileIds"] = cards[: selection_policy.MAX_SUPPORT_IDS]
        return {
            "profileId": profile_id,
            "reason": reason,
            "evidenceIds": evidence,
            "policyCheck": check,
            "support": built,
        }


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
        self.assertIsNone(queued["input"])
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
        self.assertTrue(decision["helperShutdownConfirmed"])
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
        self.assertEqual(decision["evidenceIds"], [first_evidence])
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
        self.assertIn("not a legal candidate", pinned_decision["reason"])
        self.assertEqual(
            [profile["profileId"] for profile in pinned_decision["input"]["profiles"]], [SECOND_PROFILE_ID]
        )

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

    def test_missing_decision_profile_is_needs_host_without_a_model_call(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        request = self.request(board)
        self.assertEqual(request["status"], "needs-host")
        self.assertIsNone(request["runId"])
        self.assertIn("no compatible fixed decision profile", self.decision(board, request["decisionId"])["reason"])
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_helper_absence_is_an_honest_adapter_unavailable_outcome(self):
        board = self.board()
        self.seed(board)
        self.use_helper(path=self.directory / "missing-helper")
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertFalse(capabilities["selection"])
        self.assertFalse(capabilities["maintenance"])
        self.assertFalse(capabilities["decisionAdapter"])
        request = self.request(board)
        self.assertEqual(request["status"], "failed")
        self.assertIsNone(request["runId"])
        self.assertIn("ADAPTER_UNAVAILABLE", self.decision(board, request["decisionId"])["error"])
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
    def test_writer_intent_closes_admission_and_the_selector_reads_the_new_revision(self):
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
        self.assertEqual(decision["expectedRevision"], revision + 1)
        self.assertEqual(decision["input"]["tableRevision"], revision + 1)
        self.assertEqual([card["profileId"] for card in decision["input"]["cards"]], [SECOND_PROFILE_ID])

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
        self.assertEqual(selected["expectedRevision"], revision + 1)
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
            self.use_helper(mode="select_first", evidence="foreign")
        else:
            self.use_helper(mode=mode)
        request = self.request(board, request_id=request_id)
        self.run_worker(board, worker_id=f"w-{request_id}")
        return self.decision(board, request["decisionId"])

    def test_malformed_output_fails_without_touching_the_table(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "malformed")
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)
        self.assertIn("no parseable result", decision["error"])

    def test_helper_error_envelope_fails_honestly(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "error")
        self.assertEqual(decision["status"], "failed")
        self.assertIn("call-timeout", decision["error"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)

    def test_out_of_candidate_and_wrong_revision_outputs_are_never_published(self):
        board = self.board()
        self.seed(board)
        out_of_candidate = self.outcome(board, "out_of_candidate", request_id="pick-out")
        self.assertEqual(out_of_candidate["status"], "needs-host")
        self.assertIn("not a legal candidate", out_of_candidate["reason"])
        wrong_revision = self.outcome(board, "wrong_revision", request_id="pick-rev")
        self.assertEqual(wrong_revision["status"], "stale")
        self.assertIn("revision", wrong_revision["reason"])
        wrong_operation = self.outcome(board, "wrong_operation", request_id="pick-op")
        self.assertEqual(wrong_operation["status"], "needs-host")
        self.assertIn("different operation", wrong_operation["reason"])

    def test_unknown_evidence_reference_is_refused_before_adoption(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "foreign_evidence", request_id="pick-ev")
        self.assertEqual(decision["status"], "needs-host")
        self.assertIn("not supplied", decision["reason"])

    def test_positive_dsh_task_preference_is_matched_and_never_an_exclusion(self):
        """The historical inversion: a positive match.adapter=dsh rule must be
        honored as a match when a legal DSH candidate exists, never restated as
        an avoid/fallback, and no constraint may be invented from the task text."""
        board = self.board()
        self.seed(board)
        request = board.call("selection_request", {
            "requestId": "pick-positive", "task": "work on the DSH harness source files",
            "routingPreferences": [{"match": {"adapter": "dsh"}, "reason": "Use the installed DSH harness"}],
        })
        self.run_worker(board)
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["profileId"], PROFILE_ID)
        facts = decision["input"]["policyFacts"]
        self.assertEqual(facts["hardConstraints"], {})
        self.assertEqual(facts["taskPreference"], {"ruleIndex": 0, "matchingProfileIds": [PROFILE_ID, SECOND_PROFILE_ID]})
        self.assertEqual(facts["userPreferredProfileIds"], [])
        check = decision["output"]["decision"]["policyCheck"]
        self.assertEqual(check["hardConstraints"], {})
        self.assertEqual(check["taskPreference"], {"ruleIndex": 0, "outcome": "matched"})
        self.assertEqual(check["userPreference"], "none")
        self.assertNotIn("avoid", decision["reason"].lower())

    def test_false_fallback_and_invented_constraints_settle_needs_host(self):
        board = self.board()
        self.seed(board)
        false_fallback = self.outcome(board, "policy_false_fallback", request_id="pick-false-fb")
        self.assertEqual(false_fallback["status"], "needs-host")
        self.assertIn("policy-outcome-false", false_fallback["reason"])
        invented = self.outcome(board, "policy_invented_constraint", request_id="pick-invented")
        self.assertEqual(invented["status"], "needs-host")
        self.assertIn("policy-constraint-mismatch", invented["reason"])
        # The last complete revision is unchanged and nothing was retried: each
        # boundary is one finished decision the Host resolves on the same goal.
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)
        self.assertEqual(
            [run["runId"] for run in board.call("task_list", {"limit": 10})["runs"]],
            [invented["runId"], false_fallback["runId"]],
        )

    def test_unsupported_alternative_is_needs_host_and_support_completes_it(self):
        board = self.board()
        self.seed(board, preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "user prefers the flash model"}])
        self.use_helper(profile_id=SECOND_PROFILE_ID, mode="policy_unsupported_alternative")
        request = self.request(board, request_id="pick-unsup")
        self.run_worker(board, worker_id="w-unsup")
        unsupported = self.decision(board, request["decisionId"])
        self.assertEqual(unsupported["status"], "needs-host")
        self.assertIn("policy-alternative-unsupported", unsupported["reason"])
        self.publish_user_patch(
            board, request_id="annot-1", command_id="annot-1",
            annotationChanges=[{"profileId": PROFILE_ID, "text": "economical and adequate for this user"}],
        )
        # The same alternative with eligible supplied annotation support completes.
        self.use_helper(profile_id=SECOND_PROFILE_ID, mode="select_first")
        supported_request = self.request(board, request_id="pick-sup")
        self.run_worker(board, worker_id="w-sup")
        supported = self.decision(board, supported_request["decisionId"])
        self.assertEqual(supported["status"], "completed", supported.get("reason"))
        self.assertEqual(supported["profileId"], SECOND_PROFILE_ID)
        check = supported["output"]["decision"]["policyCheck"]
        self.assertEqual(check["userPreference"], "alternative")
        self.assertEqual(
            supported["output"]["decision"]["support"],
            {"cardProfileIds": [], "annotationProfileIds": [PROFILE_ID]},
        )
        self.assertEqual(supported["output"]["decision"]["evidenceIds"], [])

    def test_helper_policy_error_settles_needs_host_without_hiding_the_worker_outcome(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "policy_error", request_id="pick-policy-error")
        self.assertEqual(decision["status"], "needs-host")
        self.assertIn("policy-outcome-false", decision["reason"])
        task = board.call("task_get", {"runId": decision["runId"]})["task"]
        # The Worker receipt keeps its real outcome: the helper exited nonzero.
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task["shutdownConfirmed"])
        self.assertIn("policy-outcome-false", task["selectedAttempt"]["error"])

    def test_malformed_rule_index_through_publication_is_needs_host_without_exception(self):
        """bool/float/string rule indexes from the helper must never compare equal
        to the derived integer index nor reach list indexing: each settles
        needs-host inside the result transaction, releases the reader and starts
        no coding work."""
        board = self.board()
        self.seed(board)
        for variant in ("false", "float", "string"):
            with self.subTest(variant=variant):
                self.use_helper(mode="policy_bad_index", rule_index=variant)
                request = board.call("selection_request", {
                    "requestId": f"pick-index-{variant}",
                    "task": "work on the DSH harness source files",
                    "routingPreferences": [{"match": {"adapter": "dsh"}, "reason": "Use the installed DSH harness"}],
                })
                self.run_worker(board, worker_id=f"w-index-{variant}")
                decision = self.decision(board, request["decisionId"])
                self.assertEqual(decision["status"], "needs-host", decision.get("reason"))
                self.assertIn("policy-index-mismatch", decision["reason"])
                self.assertIsNone(decision["profileId"])
                self.assertEqual(
                    decision["output"]["decision"]["policyCheck"]["taskPreference"]["ruleIndex"],
                    {"false": False, "float": 0.0, "string": "0"}[variant],
                    "the malformed helper answer is retained in the audit unchanged",
                )
                # The selection reader was released inside the same settlement.
                self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)
                # No coding launch: every task on this board is a decision run.
                self.assertEqual(
                    sorted(task["adapter"] for task in board.call("task_list", {"limit": 20})["runs"]),
                    ["decision"] * (["false", "float", "string"].index(variant) + 1),
                )

    def test_malformed_abstention_through_publication_is_needs_host(self):
        board = self.board()
        self.seed(board)
        self.use_helper(mode="policy_bad_abstention")
        request = self.request(board, request_id="pick-bad-abstain")
        self.run_worker(board, worker_id="w-bad-abstain")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "needs-host", decision.get("reason"))
        self.assertIn("policy-check-shape", decision["reason"])
        self.assertIn("must not cite evidence", decision["reason"])
        self.assertIsNone(decision["profileId"])
        self.assertEqual(decision["evidenceIds"], [])
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)
        self.assertEqual(
            [task["adapter"] for task in board.call("task_list", {"limit": 20})["runs"]],
            ["decision"],
        )

    def test_unconfirmed_shutdown_is_never_reported_as_stopped(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "no_shutdown", request_id="pick-shutdown")
        self.assertEqual(decision["status"], "failed")
        self.assertIn("shutdown", decision["error"].lower())
        attempt = board.call("task_get", {"runId": decision["runId"]})["task"]
        self.assertFalse(attempt["shutdownConfirmed"])

    def test_string_shutdown_evidence_cannot_release_capacity(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "string_shutdown", request_id="pick-string-shutdown")
        task = board.call("task_get", {"runId": decision["runId"]})["task"]
        self.assertEqual(decision["status"], "failed")
        self.assertFalse(task["shutdownConfirmed"])
        self.assertEqual(task["attemptState"], "uncertain")

    def test_in_flight_cancel_with_a_surviving_child_is_honest(self):
        """A real cancelled helper may leave an unconfirmed detached child."""
        board = self.board()
        self.seed(board)
        self.use_helper(mode="sleep", survivor="1", sleep="30")
        request = self.request(board, request_id="pick-cancel")
        client = board.client()
        worker = Worker("w-cancel", self.directory, client=client)
        worker.register()
        outcome: dict = {}

        def run() -> None:
            outcome["result"] = worker.run_once()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(
            wait_for(lambda: self.decision(board, request["decisionId"])["status"] == "running", timeout=20),
            "the decision never reached running",
        )
        board.call("task_cancel", {"runId": request["runId"], "reason": "operator stopped it"})
        thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "the worker did not observe the cancel")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "cancelled")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        # A detached survivor keeps the stop unconfirmed: the service never claims it
        # stopped, and the decision is not a recommendation.
        self.assertFalse(task["shutdownConfirmed"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attemptState"], "uncertain", "the survivor keeps the attempt slot")
        # The gate is open again: the cancelled decision released its reader.
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 0)

    def test_worker_deadline_is_a_bounded_honest_failure(self):
        board = self.board()
        self.seed(board)
        self.use_helper(mode="sleep", sleep="40")
        request = board.call(
            "selection_request", {"requestId": "pick-deadline", "task": "slow", "timeoutSeconds": 5}
        )
        started = time.monotonic()
        self.run_worker(board, worker_id="w-deadline")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 60, "the worker deadline did not bound the helper")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["status"], "failed")
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertFalse(task["shutdownConfirmed"], "a killed helper is never reported as stopped")
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
            "usage": None,
            "elapsedSeconds": 0.1,
            "shutdownConfirmed": True,
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
            "usage": None,
            "elapsedSeconds": 0.1,
            "shutdownConfirmed": True,
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
                "evidenceIds",
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
        self.assertLess(len(json.dumps(compact)), 2000, "the default read must stay small")
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

class DecisionDaemonTests(DecisionTestCase):
    def test_real_daemon_runs_selection_through_the_independent_worker(self):
        """The whole path: daemon + detached supervisor + real helper process."""
        from test_console import Browser

        helper_env = {"BUDDY_DECISION_HELPER": str(MOCK_HELPER)}
        with self.daemon(env=helper_env):
            code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "daemon-cat"}))
            self.assertEqual(code, 0, refreshed)
            code, opened = self.cli("console", json.dumps({"action": "open"}))
            self.assertEqual(code, 0, opened)
            browser = Browser(opened["url"])
            csrf = browser.bootstrap()["csrfToken"]
            status, _headers, body = browser.command(
                "evaluation_write_begin", {"requestId": "daemon-seed", "expectedRevision": refreshed["tableRevision"], "kind": "human"}, csrf=csrf
            )
            self.assertEqual(status, 200, body)
            begin = json.loads(body)["result"]
            status, _headers, body = browser.command("user_policy_publish", {
                "commandId": "daemon-seed", "writerId": begin["writerId"],
                "generation": begin["generation"], "writerToken": begin["writerToken"],
                "expectedRevision": begin["tableRevision"],
                "profileSettings": [{"profileId": item["profileId"], "enabled": True} for item in (PROFILE, SECOND_PROFILE)],
                "configuration": {"decisionProfileId": PROFILE_ID},
            }, csrf=csrf)
            self.assertEqual(status, 200, body)
            published = json.loads(body)["result"]

            code, created = self.cli(
                "selection-request", json.dumps({"requestId": "daemon-pick", "task": "over the real daemon"})
            )
            self.assertEqual(code, 0, created)
            self.assertEqual(created["status"], "queued")

            def terminal() -> dict | None:
                code, view = self.cli(
                    "selection-get", json.dumps({"decisionId": created["decisionId"], "includeAudit": True})
                )
                if code != 0:
                    return None
                return view["decision"] if view["decision"]["status"] not in ("queued", "running") else None

            decision = wait_for(terminal, timeout=60)
            self.assertIsNotNone(decision, "the decision never reached a terminal state")
            self.assertEqual(decision["status"], "completed", decision.get("reason"))
            self.assertEqual(decision["profileId"], PROFILE_ID)
            self.assertEqual(decision["reason"], f"mock select chose {PROFILE_ID}")
            # The real worker wrote a durable attempt with its own helper process.
            code, task = self.cli("status", json.dumps({"runId": decision["runId"]}))
            self.assertEqual(code, 0, task)
            self.assertEqual(task["status"], "completed")
            self.assertTrue(task["shutdownConfirmed"])
            attempt_dir = self.directory / "attempts" / decision["runId"] / decision["attemptId"]
            self.assertTrue((attempt_dir / "decision-input.json").is_file())
            self.assertTrue((attempt_dir / "decision-output.json").is_file())
            self.assertEqual(
                json.loads((attempt_dir / "decision-input.json").read_text())["tableRevision"],
                decision["expectedRevision"],
            )

            # A card-only patch over the same daemon is an ordinary writer intent:
            # no second model call and no internal maintenance task.
            code, second_begin = self.cli(
                "evaluation-write-begin",
                json.dumps({"requestId": "daemon-card", "expectedRevision": published["revision"], "kind": "maintenance"}),
            )
            self.assertEqual(code, 0, second_begin)
            code, card = self.cli(
                "assessment-publish",
                json.dumps(
                    {
                        "commandId": "daemon-card",
                        "writerId": second_begin["writerId"],
                        "generation": second_begin["generation"],
                        "writerToken": second_begin["writerToken"],
                        "expectedRevision": published["revision"],
                        "cards": [
                            {
                                "profileId": PROFILE_ID,
                                "summary": "patched by the external Harness",
                                "strengths": [],
                                "limitations": [],
                                "risks": ["daemon open risk"],
                                "evidenceIds": [],
                            }
                        ],
                    }
                ),
            )
            self.assertEqual(code, 0, card)
            code, snapshot = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, snapshot)
            self.assertEqual(snapshot["tableRevision"], published["revision"] + 1)
            self.assertEqual(snapshot["cards"][0]["summary"], "patched by the external Harness")
            self.assertEqual(snapshot["cards"][0]["risks"], ["daemon open risk"])
            self.assertFalse(snapshot["capabilities"]["maintenance"])
            # Nothing on an ordinary refresh calls a model: the decision history is read.
            self.assertEqual([item["kind"] for item in snapshot["decisions"]], ["select"])


if __name__ == "__main__":
    unittest.main()
