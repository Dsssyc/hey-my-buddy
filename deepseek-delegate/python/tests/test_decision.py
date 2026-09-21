"""Durable decision orchestration: selection, maintenance and their fencing.

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

from buddy.errors import BoardError
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
    "MOCK_DECISION_CARD_MODE",
    "MOCK_DECISION_SLEEP",
    "MOCK_DECISION_SURVIVOR",
)


class DecisionTestCase(BoardTestCase):
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
        auto_maintain: bool = False,
        decision_profile: str | None = PROFILE_ID,
        preferences=None,
        cards=None,
    ) -> dict:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        grant = board.call(
            "evaluation_write_begin", {"requestId": "seed", "expectedRevision": 0, "kind": "human"}
        )
        return board.call(
            "evaluation_write_publish",
            {
                "commandId": "seed-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                "profiles": list(profiles),
                "cards": cards or [],
                "preferences": preferences or [],
                "configuration": {"decisionProfileId": decision_profile, "autoMaintain": auto_maintain},
            },
        )

    def card_with_pending_evidence(self, board) -> tuple[str, str]:
        """One published card plus one newer pending evidence row for maintenance."""
        first = self.evidence(board, "task-1", summary="card sample")
        self.publish_more(
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

    def publish_more(self, board, *, request_id: str, command_id: str, **collections) -> dict:
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": request_id, "expectedRevision": revision, "kind": "human"},
        )
        return board.call(
            "evaluation_write_publish",
            {
                "commandId": command_id,
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": revision,
                **collections,
            },
        )

    def record(
        self,
        board,
        request_id: str = "task-1",
        *,
        model: str | None = None,
        effort: str | None = None,
        accept: bool = True,
    ) -> str:
        """One dsh-identity task with a reviewed result, for evidence fixtures."""
        client = board.client()
        run_id = client.submit(
            requestId=request_id,
            task="produce a model result",
            cwd=str(self.workdir()),
            model=model or PROFILE["model"],
            provider=PROFILE["provider"],
            effort=effort or PROFILE["effort"],
        )["task"]["runId"]
        worker_id = f"worker-{request_id}"
        client.register_worker(worker_id, adapter="dsh", capabilities=["dsh"])
        claim = client.claim(worker_id, f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        client.submit_result(
            worker_id,
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {
                "status": "ok",
                "result": {
                    "status": "ok",
                    "mode": "run",
                    "requested": {
                        "provider": PROFILE["provider"],
                        "model": model or PROFILE["model"],
                        "reasoningEffort": effort or PROFILE["effort"],
                    },
                    "finalText": "done",
                },
                "shutdownConfirmed": True,
                "exitCode": 0,
            },
        )
        if accept:
            client.acknowledge(runId=run_id, note="reviewed", verdict="accepted")
        return run_id

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
        self.publish_more(
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
        self.publish_more(
            board,
            request_id="pin-1",
            command_id="pin-1",
            preferences=[{"profileId": SECOND_PROFILE_ID, "mode": "pin", "reason": "only this one"}],
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
            {"requestId": "pick-caps", "task": "needs a large context", "requiredCapabilities": ["context:large"]},
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
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-missing"})
        self.assertEqual(maintain["status"], "failed")
        self.assertIn("ADAPTER_UNAVAILABLE", self.decision(board, maintain["decisionId"])["error"])
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_capabilities_report_real_availability(self):
        board = self.board()
        self.seed(board)
        capabilities = board.call("console_snapshot", {})["capabilities"]
        self.assertTrue(capabilities["selection"])
        self.assertTrue(capabilities["maintenance"])
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
            "evaluation_write_begin", {"requestId": "w-active", "expectedRevision": revision, "kind": "human"}
        )
        request = self.request(board)
        self.run_worker(board)
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["queueReason"], "evaluation-writer-pending")
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "queued")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["phase"], "writing")

        board.call(
            "evaluation_write_publish",
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

    def test_one_slot_maintenance_runs_before_the_queued_selector_without_deadlock(self):
        board = self.board(max_concurrent=1)
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        selection = self.request(board)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-1"})
        self.assertEqual(maintain["status"], "queued")
        worker = self.run_worker(board, worker_id="w-slot")
        maintenance = self.decision(board, maintain["decisionId"])
        self.assertEqual(maintenance["status"], "completed")
        self.assertEqual(maintenance["publishedRevision"], 3)
        self.assertEqual(self.decision(board, selection["decisionId"])["status"], "queued")
        # The waiting selector is not lost: the next claim runs it on the new revision.
        worker.run_once()
        selected = self.decision(board, selection["decisionId"])
        self.assertEqual(selected["status"], "completed")
        self.assertEqual(selected["expectedRevision"], 3)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 0)

    def test_admitted_reader_drains_before_the_writer_and_the_released_reader_fails_honestly(self):
        board = self.board(max_concurrent=2)
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        selection = self.request(board)
        client = board.client()
        client.register_worker("w-reader", adapter="dsh", capabilities=["dsh", "decision"])
        reader_claim = client.claim(
            "w-reader", "claim-reader", "n" * 32, task_id=selection["runId"]
        )["claim"]
        self.assertIsNotNone(reader_claim)
        self.assertEqual(self.decision(board, selection["decisionId"])["status"], "running")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["readers"], 1)

        maintain = board.call("evaluation_maintain", {"requestId": "tidy-1"})
        client.register_worker("w-maint", adapter="dsh", capabilities=["dsh", "decision"])
        blocked = client.claim("w-maint", "claim-maint", "m" * 32, task_id=maintain["runId"])
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "evaluation-writer-pending")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["waitingWriters"], 1)

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
        # The writer is granted once the reader settled, and the maintenance runs.
        self.run_worker(board, worker_id="w-maint-run")
        maintenance = self.decision(board, maintain["decisionId"])
        self.assertEqual(maintenance["status"], "completed")
        self.assertEqual(board.call("console_snapshot", {})["gate"]["phase"], "open")

    def test_queued_selection_cancel_is_durable_and_never_runs(self):
        board = self.board()
        self.seed(board)
        revision = board.call("console_snapshot", {})["tableRevision"]
        board.call("evaluation_write_begin", {"requestId": "w-hold", "expectedRevision": revision, "kind": "human"})
        request = self.request(board)
        board.call("task_cancel", {"runId": request["runId"], "reason": "user cancelled the pending selection"})
        self.assertEqual(self.decision(board, request["decisionId"])["status"], "cancelled")
        self.run_worker(board)
        self.assertEqual(board.call("task_get", {"runId": request["runId"]})["task"]["status"], "cancelled")
        self.assertIsNone(self.decision(board, request["decisionId"])["attemptId"])


class DecisionFailureTests(DecisionTestCase):
    def outcome(self, board, mode: str, *, kind: str = "select", request_id: str = "pick-x") -> dict:
        if mode == "foreign_evidence":
            self.use_helper(mode="select_first", evidence="foreign")
        else:
            self.use_helper(mode=mode)
        if kind == "select":
            request = self.request(board, request_id=request_id)
        else:
            request = board.call("evaluation_maintain", {"requestId": request_id})
        self.run_worker(board, worker_id=f"w-{request_id}")
        return self.decision(board, request["decisionId"])

    def test_malformed_output_fails_without_touching_the_table(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "malformed")
        self.assertEqual(decision["status"], "failed")
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 1)
        self.assertIn("no parseable result", decision["error"])

    def test_helper_error_envelope_fails_honestly(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "error")
        self.assertEqual(decision["status"], "failed")
        self.assertIn("call-timeout", decision["error"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 1)

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

    def test_unconfirmed_shutdown_is_never_reported_as_stopped(self):
        board = self.board()
        self.seed(board)
        decision = self.outcome(board, "no_shutdown", request_id="pick-shutdown")
        self.assertEqual(decision["status"], "failed")
        self.assertIn("shutdown", decision["error"].lower())
        attempt = board.call("task_get", {"runId": decision["runId"]})["task"]
        self.assertFalse(attempt["shutdownConfirmed"])

    def test_in_flight_cancel_with_a_surviving_child_is_honest(self):
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
            "decision": {"profileId": PROFILE_ID, "reason": "too late", "evidenceIds": []},
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
        self.assertEqual(restarted.call("console_snapshot", {})["tableRevision"], 1)

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
            "decision": {"profileId": PROFILE_ID, "reason": "replayed once", "evidenceIds": []},
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


class MaintenanceTests(DecisionTestCase):
    def test_explicit_maintenance_without_preauthorization_retains_the_proposal(self):
        board = self.board()
        self.seed(board, auto_maintain=False)
        self.card_with_pending_evidence(board)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-1"})
        self.run_worker(board)
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "needs-host")
        self.assertFalse(decision["autoPublish"])
        self.assertIn("autoMaintain is false", decision["reason"])
        self.assertIsNotNone(decision["proposal"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)
        self.assertEqual(board.call("console_snapshot", {})["gate"]["waitingWriters"], 0)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)

    def test_auto_maintain_adopts_only_cards_and_preserves_risks_and_references(self):
        board = self.board()
        self.seed(board, auto_maintain=True)
        evidence_id, second = self.card_with_pending_evidence(board)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-1"})
        self.run_worker(board)
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["publishedRevision"], 3)
        self.assertEqual(decision["consideredEvidence"], 1)
        self.assertEqual(decision["pendingEvidenceRemaining"], 0)
        card = board.call("console_snapshot", {})["cards"][0]
        self.assertEqual(card["summary"], f"mock maintenance summary for {PROFILE_ID}")
        self.assertEqual(card["risks"], ["still open: fixture risk"])
        self.assertEqual(card["evidenceIds"], [evidence_id, second])
        self.assertEqual(card["sampleCount"], 2)
        # The processed evidence is incorporated, not left pending forever.
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 0)
        self.assertEqual(decision["proposal"]["basisRevision"], decision["expectedRevision"])
        self.assertEqual(decision["proposal"]["retiredEvidenceIds"], [])

    def test_a_risk_removing_or_overreaching_proposal_is_never_adopted(self):
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        before = board.call("console_snapshot", {})["cards"][0]
        for mode, request_id, expected in (
            ("drop_risk", "tidy-risk", "unresolved risk"),
            ("drop_limitation", "tidy-limitation", "known limitation"),
            ("extra_field", "tidy-field", "cards only"),
            ("config_change", "tidy-config", "unexpected field"),
            ("foreign_evidence", "tidy-foreign", "not supplied"),
        ):
            self.use_helper(mode="maintain", card_mode=mode)
            maintain = board.call("evaluation_maintain", {"requestId": request_id})
            self.run_worker(board, worker_id=f"w-{request_id}")
            decision = self.decision(board, maintain["decisionId"])
            self.assertEqual(decision["status"], "needs-host", f"{mode}: {decision['reason']}")
            self.assertIn(expected, decision["reason"], mode)
            self.assertEqual(board.call("console_snapshot", {})["cards"][0]["summary"], before["summary"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)

    def test_maintenance_never_touches_profiles_preferences_or_configuration(self):
        board = self.board()
        self.seed(board, auto_maintain=True, preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap"}])
        self.card_with_pending_evidence(board)
        before = board.call("console_snapshot", {})
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-new"})
        self.run_worker(board)
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed")
        after = board.call("console_snapshot", {})
        self.assertEqual(after["profiles"], before["profiles"])
        self.assertEqual(after["preferences"], before["preferences"])
        self.assertEqual(after["configuration"], before["configuration"])
        self.assertEqual(len(after["cards"]), 1)

    def test_maintenance_batch_publishes_a_subset_and_leaves_unrelated_cards_untouched(self):
        """A batch touching A must republish B byte-for-byte, not fail validation."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        first = self.evidence(board, "task-1", summary="profile A sample")
        second = self.evidence(
            board,
            "task-2",
            summary="profile B sample",
            profile=SECOND_PROFILE_ID,
            model=SECOND_PROFILE["model"],
            effort=SECOND_PROFILE["effort"],
        )
        card_a = {
            "profileId": PROFILE_ID,
            "summary": "card A before",
            "strengths": ["fast"],
            "limitations": [],
            "risks": ["open risk A"],
            "evidenceIds": [first],
        }
        card_b = {
            "profileId": SECOND_PROFILE_ID,
            "summary": "card B untouched",
            "strengths": ["stable"],
            "limitations": ["narrow"],
            "risks": ["open risk B"],
            "evidenceIds": [second],
        }
        self.publish_more(board, request_id="cards-seed", command_id="cards-seed", cards=[card_a, card_b])
        # Releasing A's evidence reference makes it pending again, so the batch carries
        # only profile A; B keeps its published card byte-for-byte.
        self.publish_more(
            board,
            request_id="card-a-release",
            command_id="card-a-release",
            cards=[{**card_a, "evidenceIds": []}, card_b],
        )
        before = {card["profileId"]: card for card in board.call("console_snapshot", {})["cards"]}
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-subset"})
        self.run_worker(board)
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed", decision["reason"])
        self.assertEqual(decision["publishedRevision"], 4)
        after = {card["profileId"]: card for card in board.call("console_snapshot", {})["cards"]}
        self.assertEqual(after[PROFILE_ID]["summary"], f"mock maintenance summary for {PROFILE_ID}")
        self.assertEqual(after[PROFILE_ID]["risks"], ["open risk A"])
        # The unrelated card keeps its exact content, revision and timestamp.
        self.assertEqual(after[SECOND_PROFILE_ID], before[SECOND_PROFILE_ID])

    def test_an_invalid_proposal_never_poisons_the_worker_receipt(self):
        """A rejected proposal settles the decision and still commits the result once."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        self.use_helper(mode="maintain", card_mode="drop_risk")
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-poison"})
        client = board.client()
        worker = Worker("w-poison", self.directory, client=client)
        worker.register()
        self.assertEqual(worker.run_once(), "ran")
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "needs-host")
        task = board.call("task_get", {"runId": maintain["runId"]})["task"]
        self.assertEqual(task["status"], "completed")
        self.assertTrue(task["shutdownConfirmed"])
        self.assertTrue(worker.spool.pending() == [], "the receipt must be committed, not retried")
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)
        self.assertEqual(board.call("console_snapshot", {})["cards"][0]["summary"], "before maintenance")

    def test_no_op_proposal_settles_completed_without_a_new_revision(self):
        """A valid proposal that changes nothing is a no-op, never a task failure."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        self.use_helper(mode="maintain", card_mode="no_op")
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-noop"})
        worker = self.run_worker(board, worker_id="w-noop")
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed", decision["reason"])
        self.assertIsNone(decision["publishedRevision"])
        self.assertIn("no card change was needed", decision["reason"])
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 2)
        # The evidence the model declined to incorporate stays pending and visible.
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        self.assertEqual(decision["pendingEvidenceRemaining"], 1)
        task = board.call("task_get", {"runId": maintain["runId"]})["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(worker.spool.pending(), [])

    def test_safe_compaction_retires_a_reference_without_requeueing_it(self):
        """Retiring a current reference keeps it incorporated and archived."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        first, second = self.card_with_pending_evidence(board)
        self.use_helper(mode="maintain", card_mode="drop_evidence")
        before = board.call("console_snapshot", {})["cards"][0]
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-compact"})
        self.run_worker(board, worker_id="w-compact")
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed", decision["reason"])
        self.assertEqual(decision["proposal"]["retiredEvidenceIds"], [first])
        self.assertEqual(decision["proposal"]["basisRevision"], decision["expectedRevision"])
        card = board.call("console_snapshot", {})["cards"][0]
        self.assertEqual(card["evidenceIds"], [])
        # The risk text survived compaction; the retired reference is not requeued.
        self.assertEqual(card["risks"], ["still open: fixture risk"])
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        self.assertNotEqual(second, first)
        with board.store.db.read() as connection:
            archived = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_evidence WHERE evidence_id=?", (first,)
            ).fetchone()["count"]
            self.assertEqual(int(archived), 1, "the retired source stays in the archive")
            history = board.evaluation.card_history(connection, PROFILE_ID)
        # The new revision archives the compacted card; the earlier publication
        # snapshot still holds the reference that was retired.
        self.assertEqual(history[0]["tableRevision"], decision["publishedRevision"])
        self.assertEqual(history[0]["evidenceIds"], [])
        previous = {row["tableRevision"]: row for row in history}[2]
        self.assertEqual(previous["evidenceIds"], before["evidenceIds"])
        self.assertEqual(previous["risks"], before["risks"])

    def test_only_cited_pending_evidence_is_consumed(self):
        """A batch row the model omitted stays pending and is reported as remaining."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.publish_more(
            board,
            request_id="card-seed",
            command_id="card-seed",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "bounded card",
                    "strengths": [],
                    "limitations": [],
                    "risks": ["open risk"],
                    "evidenceIds": [],
                }
            ],
        )
        first = self.evidence(board, "task-1", summary="cited sample")
        second = self.evidence(board, "task-2", summary="omitted sample")
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 2)
        # The proposal cites the first pending sample only: no compaction window here.
        self.use_helper(mode="maintain", card_mode="cite_first_pending")
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-cite"})
        self.run_worker(board, worker_id="w-cite")
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed", decision["reason"])
        card = board.call("console_snapshot", {})["cards"][0]
        self.assertEqual(card["evidenceIds"], [first])
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        self.assertEqual(decision["pendingEvidenceRemaining"], 1)
        self.assertEqual(decision["proposal"]["incorporatedEvidenceIds"], [first])
        with board.store.db.read() as connection:
            pending_ids = {
                row["evidence_id"]
                for row in connection.execute("SELECT evidence_id FROM evaluation_evidence_pending")
            }
        self.assertEqual(pending_ids, {second}, "an omitted row must stay pending")

    def test_evidence_arriving_during_the_call_stays_pending(self):
        """The input snapshot is frozen; evidence recorded after it is never consumed."""
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.card_with_pending_evidence(board)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-during"})
        client = board.client()
        client.register_worker("w-during", adapter="dsh", capabilities=["dsh", "decision"])
        claim = client.claim("w-during", "claim-during", "n" * 32, task_id=maintain["runId"])["claim"]
        self.assertIsNotNone(claim)
        self.assertEqual(self.decision(board, maintain["decisionId"])["status"], "running")
        # New evidence lands after the frozen snapshot, inside the writer interval.
        late = self.evidence(board, "task-late", summary="arrived during the call")
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(snapshot["pendingEvidence"], 2)
        document = claim["decisionInput"]
        self.assertNotIn(late, json.dumps(document["evidence"]))
        envelope = {
            "status": "ok",
            "operation": "maintain",
            "tableRevision": self.decision(board, maintain["decisionId"])["expectedRevision"],
            "requested": {"provider": PROFILE["provider"], "model": PROFILE["model"], "reasoningEffort": PROFILE["effort"]},
            "resolved": None,
            "observed": None,
            "usage": None,
            "elapsedSeconds": 0.1,
            "shutdownConfirmed": True,
            "proposal": {
                "cards": [
                    {
                        "profileId": PROFILE_ID,
                        "summary": "mock maintenance summary for " + PROFILE_ID,
                        "strengths": ["fast"],
                        "limitations": ["small"],
                        "risks": ["still open: fixture risk"],
                        "evidenceIds": [document["evidence"][0]["evidenceId"]],
                    }
                ],
                "reason": "cited only the frozen snapshot",
            },
        }
        client.submit_result(
            "w-during",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {"status": "ok", "result": envelope, "shutdownConfirmed": True, "exitCode": 0},
        )
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["status"], "completed", decision["reason"])
        self.assertEqual(decision["proposal"]["incorporatedEvidenceIds"], [document["evidence"][0]["evidenceId"]])
        # The late evidence is untouched and the remaining count includes it.
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 1)
        self.assertEqual(decision["pendingEvidenceRemaining"], 1)
        with board.store.db.read() as connection:
            pending_ids = {
                row["evidence_id"]
                for row in connection.execute("SELECT evidence_id FROM evaluation_evidence_pending")
            }
        self.assertEqual(pending_ids, {late})

    def test_more_than_sixty_four_sequential_samples_compact_and_stay_bounded(self):
        board = self.board()
        self.seed(board, auto_maintain=True)
        self.publish_more(
            board,
            request_id="card-seed",
            command_id="card-seed",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "bounded card",
                    "strengths": [],
                    "limitations": ["narrow but known"],
                    "risks": ["open risk that never clears"],
                    "evidenceIds": [],
                }
            ],
        )
        for index in range(70):
            board.call(
                "evaluation_evidence_record",
                {
                    "profileId": PROFILE_ID,
                    "kind": "observation",
                    "summary": f"sequential observation {index}",
                    "source": "cli",
                },
            )
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 70)
        first = board.call("evaluation_maintain", {"requestId": "tidy-1"})
        self.run_worker(board, worker_id="w-compact-1")
        one = self.decision(board, first["decisionId"])
        self.assertEqual(one["status"], "completed", one["reason"])
        self.assertEqual(one["consideredEvidence"], 64)
        card = board.call("console_snapshot", {})["cards"][0]
        self.assertEqual(len(card["evidenceIds"]), 64)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 6)
        second = board.call("evaluation_maintain", {"requestId": "tidy-2"})
        self.run_worker(board, worker_id="w-compact-2")
        two = self.decision(board, second["decisionId"])
        self.assertEqual(two["status"], "completed", two["reason"])
        self.assertEqual(two["consideredEvidence"], 6)
        self.assertEqual(len(two["proposal"]["retiredEvidenceIds"]), 6)
        card = board.call("console_snapshot", {})["cards"][0]
        self.assertEqual(len(card["evidenceIds"]), 64, "the current card stays bounded")
        self.assertEqual(card["risks"], ["open risk that never clears"])
        self.assertEqual(card["limitations"], ["narrow but known"])
        # Every processed observation stays consumed: pending is not a re-run queue.
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 0)
        self.assertEqual(two["pendingEvidenceRemaining"], 0)
        with board.store.db.read() as connection:
            archived = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_evidence WHERE profile_id=?", (PROFILE_ID,)
            ).fetchone()["count"]
            pending_rows = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_evidence_pending WHERE profile_id=?", (PROFILE_ID,)
            ).fetchone()["count"]
            history = board.evaluation.card_history(connection, PROFILE_ID)
        self.assertEqual(int(archived), 70, "storage limits never delete evidence")
        self.assertEqual(int(pending_rows), 0)
        # The pre-compaction snapshot keeps the reference list that was retired.
        snapshots = {row["tableRevision"]: row for row in history}
        self.assertEqual(snapshots[two["publishedRevision"]]["summary"], card["summary"])
        retired = set(two["proposal"]["retiredEvidenceIds"])
        older = snapshots[one["publishedRevision"]]
        self.assertTrue(retired.issubset(set(older["evidenceIds"])), "the archive holds every retired ref")

    def test_maintenance_with_nothing_pending_makes_no_model_call(self):
        board = self.board()
        self.seed(board, auto_maintain=True)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-none"})
        self.assertEqual(maintain["status"], "needs-host")
        self.assertIsNone(maintain["runId"])
        self.assertIn("no pending evidence", self.decision(board, maintain["decisionId"])["reason"])
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])


class DecisionSurfaceTests(DecisionTestCase):
    def test_http_cli_and_ctwo_expose_the_same_named_operations(self):
        from buddy.console import CONSOLE_OPERATIONS
        from buddy.service import CONTROL_OPERATIONS
        from buddy.transport import METHOD_MAP
        import buddy.cli as cli

        for operation in ("selection_request", "selection_get", "evaluation_maintain"):
            self.assertIn(operation, CONTROL_OPERATIONS)
            self.assertIn(operation, CONSOLE_OPERATIONS)
        self.assertEqual(METHOD_MAP["selection-request"], ("control", "selection_request"))
        self.assertEqual(METHOD_MAP["selection-get"], ("control", "selection_get"))
        self.assertEqual(METHOD_MAP["evaluation-maintain"], ("control", "evaluation_maintain"))
        for method in ("selection-request", "selection-get", "evaluation-maintain"):
            self.assertIn(method, cli.METHODS)
        board = self.board()
        self.seed(board)
        # The console command route calls exactly the same validated operation.
        created = board.console.command("selection_request", {"requestId": "http-1", "task": "over http"})
        fetched = board.console.command("selection_get", {"decisionId": created["decisionId"]})
        self.assertEqual(fetched["decision"]["requestId"], "http-1")
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
            "INVALID_ARGUMENT", board.call, "evaluation_maintain", {"requestId": "r", "timeoutSeconds": 4}
        )
        self.assert_code("INVALID_ARGUMENT", board.call, "selection_request", {"requestId": "r", "task": "x", "extra": 1})


class DecisionGrowthTests(DecisionTestCase):
    def seed_history(self, board, count: int) -> None:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        self.seed(board, auto_maintain=True)
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
        self.publish_more(
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
        # Replacing the card without the reference makes that evidence pending again.
        self.publish_more(
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
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 21)
        with board.store.db.read() as connection:
            ledger = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_evidence e WHERE NOT EXISTS ("
                " SELECT 1 FROM evaluation_cards c WHERE c.profile_id = e.profile_id AND EXISTS ("
                "  SELECT 1 FROM json_each(c.evidence_ids_json) WHERE json_each.value = e.evidence_id))"
            ).fetchone()["count"]
            self.assertEqual(board.evaluation._pending_evidence(connection), int(ledger))
            samples = connection.execute("SELECT COUNT(*) AS count FROM evaluation_samples").fetchone()["count"]
            self.assertEqual(board.evaluation._sample_count(connection, PROFILE_ID), int(samples))

    def test_maintenance_batch_stays_bounded_and_reports_the_rest(self):
        board = self.board()
        self.seed_history(board, 90)
        maintain = board.call("evaluation_maintain", {"requestId": "tidy-batch"})
        self.run_worker(board)
        decision = self.decision(board, maintain["decisionId"])
        self.assertEqual(decision["consideredEvidence"], 64)
        # No card existed for the batch, so the valid empty proposal is a completed
        # no-op: nothing is adopted and nothing is falsely consumed.
        self.assertEqual(decision["status"], "completed")
        self.assertIsNone(decision["publishedRevision"])
        self.assertEqual(decision["consideredEvidence"], 64)
        self.assertEqual(decision["pendingEvidenceRemaining"], 90)
        self.assertLessEqual(len(decision["input"]["evidence"]), 64)
        self.assertEqual(board.call("console_snapshot", {})["pendingEvidence"], 90)


class DecisionDaemonTests(DecisionTestCase):
    def test_real_daemon_runs_selection_and_maintenance_through_the_independent_worker(self):
        """The whole path: daemon + detached supervisor + real helper process."""
        helper_env = {"BUDDY_DECISION_HELPER": str(MOCK_HELPER)}
        with self.daemon(env=helper_env):
            code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "daemon-cat"}))
            self.assertEqual(code, 0, refreshed)
            code, begin = self.cli(
                "evaluation-write-begin",
                json.dumps({"requestId": "daemon-seed", "expectedRevision": 0, "kind": "human"}),
            )
            self.assertEqual(code, 0, begin)
            code, published = self.cli(
                "evaluation-write-publish",
                json.dumps(
                    {
                        "commandId": "daemon-seed",
                        "writerId": begin["writerId"],
                        "generation": begin["generation"],
                        "writerToken": begin["writerToken"],
                        "expectedRevision": 0,
                        "profiles": [PROFILE, SECOND_PROFILE],
                        "configuration": {"decisionProfileId": PROFILE_ID, "autoMaintain": True},
                    }
                ),
            )
            self.assertEqual(code, 0, published)

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

            # Maintenance over the same real worker: one pending observation plus a
            # card to preserve, adopted because autoMaintain is preauthorized.
            code, evidence = self.cli(
                "evaluation-evidence-record",
                json.dumps({"profileId": PROFILE_ID, "kind": "observation", "summary": "cli note", "source": "cli"}),
            )
            self.assertEqual(code, 0, evidence)
            code, second_begin = self.cli(
                "evaluation-write-begin",
                json.dumps({"requestId": "daemon-card", "expectedRevision": 1, "kind": "human"}),
            )
            self.assertEqual(code, 0, second_begin)
            code, card = self.cli(
                "evaluation-write-publish",
                json.dumps(
                    {
                        "commandId": "daemon-card",
                        "writerId": second_begin["writerId"],
                        "generation": second_begin["generation"],
                        "writerToken": second_begin["writerToken"],
                        "expectedRevision": 1,
                        "cards": [
                            {
                                "profileId": PROFILE_ID,
                                "summary": "before daemon maintenance",
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
            code, maintain = self.cli("evaluation-maintain", json.dumps({"requestId": "daemon-tidy"}))
            self.assertEqual(code, 0, maintain)

            def maintained() -> dict | None:
                code, view = self.cli(
                    "selection-get", json.dumps({"decisionId": maintain["decisionId"], "includeAudit": True})
                )
                if code != 0:
                    return None
                return view["decision"] if view["decision"]["status"] not in ("queued", "running") else None

            outcome = wait_for(maintained, timeout=60)
            self.assertIsNotNone(outcome, "maintenance never reached a terminal state")
            self.assertEqual(outcome["status"], "completed", outcome.get("reason"))
            self.assertEqual(outcome["publishedRevision"], 3)
            code, snapshot = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, snapshot)
            self.assertEqual(snapshot["tableRevision"], 3)
            self.assertEqual(snapshot["cards"][0]["summary"], f"mock maintenance summary for {PROFILE_ID}")
            self.assertEqual(snapshot["cards"][0]["risks"], ["daemon open risk"])
            # Nothing on an ordinary refresh calls a model: the decision history is read.
            self.assertEqual([item["kind"] for item in snapshot["decisions"]], ["maintain", "select"])


if __name__ == "__main__":
    unittest.main()
