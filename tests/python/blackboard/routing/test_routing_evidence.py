"""Outcome and load facts over synthetic boards, without a model call."""
import json
import unittest

from blackboard.tasks.test_workflow import WorkflowTestCase, BUDDY
from support import FakeClock
from hey_my_buddy.blackboard.routing.evidence import candidate_facts, round_classification, NEITHER
from hey_my_buddy.protocol.stop import TERMINATION_REASONS
from hey_my_buddy.protocol.usage import normalize_token_usage


NO_USAGE = {"samples": 0, "medianInputTokens": None,
            "medianCachedInputTokens": None, "medianOutputTokens": None}


class ClassificationTests(unittest.TestCase):
    def test_every_termination_and_provider_rejection(self):
        self.assertEqual(round_classification(None), "neither")
        for termination in (*TERMINATION_REASONS, None):
            with self.subTest(termination=termination):
                receipt = {"terminationReason": termination, "result": {}}
                expected = "neither" if termination in NEITHER else "ability"
                self.assertEqual(round_classification(receipt), expected)
                for code in ("overloaded", "quota-exceeded", "rate-limited"):
                    receipt["result"] = {"providerRejection": {"code": code}}
                    self.assertEqual(round_classification(receipt),
                                     "neither" if termination in NEITHER else "infrastructure")
                receipt["result"] = {"code": "harness-unavailable"}
                self.assertEqual(round_classification(receipt),
                                 "neither" if termination in NEITHER else "infrastructure")
                receipt["result"] = {"code": "bad-answer", "error": "harness-unavailable quota-exceeded"}
                self.assertEqual(round_classification(receipt), expected)


class RoutingEvidenceTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board_ = self.board(clock=self.clock, max_concurrent=8)
        self.serial = 0

    def new_run(self, **extra):
        self.serial += 1
        return self.submit(self.board_, request_id=f"sample-{self.serial}",
                           cwd=str(self.workdir(f"work-{self.serial}")), kind="worktree", **extra)

    def view(self, run):
        return self.board_.call("workflow_get", {"runId": run["runId"]})

    @staticmethod
    def usage(input_tokens, cached_tokens, output_tokens, *, completeness="complete"):
        return normalize_token_usage({
            "inputTokens": input_tokens, "cachedInputTokens": cached_tokens,
            "outputTokens": output_tokens, "completeness": completeness})

    def end_turn(self, run, *, seconds=10, failed=False, category=None, disposition="completed", usage=None):
        claim = self.claim(self.board_, run_id=run["runId"])
        self.assertIsNotNone(claim["claim"])
        self.clock.advance(seconds)
        self.finish_turn(self.board_, claim, runner_status="failed" if failed else "ok",
                         disposition=disposition)
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        with self.board_.store.db.write() as db:
            receipt = json.loads(db.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0])
            receipt["terminationReason"] = category or ("harness-error" if failed else "completed")
            # Explicit false must not erase ability evidence under the revised rule.
            receipt["result"]["modelStarted"] = False
            if usage is not None:
                receipt["result"]["tokenUsage"] = usage
            db.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?", (json.dumps(receipt), attempt_id))
        return self.view(run), attempt_id

    def conclude(self, run):
        return self.board_.call("workflow_conclude", {"runId": run["runId"],
                                "note": "Observed this synthetic outcome", **self.control(run)})

    def facts(self, *buddies):
        rows = [{"buddy_id": str(index), **buddy} for index, buddy in enumerate(buddies or (BUDDY,))]
        with self.board_.store.db.read() as db:
            changes = db.total_changes
            value = candidate_facts(self.board_.store, db, rows, now=self.clock())
            self.assertEqual(db.total_changes, changes)
        return list(value.values())

    def test_no_data_is_explicit_and_provider_changes_share_only_kind_statistics(self):
        empty = self.facts()[0]
        self.assertEqual(empty["statistics"]["samples"], 0)
        self.assertIsNone(empty["statistics"]["failureRate"])
        self.assertIsNone(empty["statistics"]["medianDeliverySeconds"])
        self.assertEqual(empty["statistics"]["usage"], NO_USAGE)
        self.assertEqual(empty["billing"]["kind"], "unknown")
        run, _ = self.end_turn(self.new_run(), seconds=12)
        self.accept_run(self.board_, run)
        same, other_provider, other_effort, other_harness = self.facts(
            BUDDY, {**BUDDY, "provider": "other"}, {**BUDDY, "effort": "high"},
            {**BUDDY, "harness": "codex"})
        self.assertEqual(same["statistics"], other_provider["statistics"])
        self.assertEqual(same["statistics"], {"samples": 1, "successes": 1, "failures": 0,
                         "firstPasses": 1, "reworkRounds": 0, "firstPassRate": 1,
                         "failureRate": 0, "medianDeliverySeconds": 12, "usage": NO_USAGE})
        self.assertEqual(other_effort["statistics"]["samples"], 0)
        self.assertEqual(other_harness["statistics"]["samples"], 0)

    def test_usage_sums_ability_turns_per_sample_and_reports_medians(self):
        first, _ = self.end_turn(self.new_run(), disposition="assistance",
                                 usage=self.usage(10, 2, 3))
        self.continue_run(self.board_, first)
        first, _ = self.end_turn(first, usage=self.usage(20, 4, 5))
        self.accept_run(self.board_, first)
        second, _ = self.end_turn(self.new_run(), usage=self.usage(100, 10, 6))
        self.accept_run(self.board_, second)
        stats = self.facts()[0]["statistics"]
        self.assertEqual(stats["samples"], 2)
        self.assertEqual(stats["usage"], {"samples": 2, "medianInputTokens": 65,
                                          "medianCachedInputTokens": 8, "medianOutputTokens": 7})

    def test_partial_or_missing_usage_keeps_the_sample_out(self):
        mixed, _ = self.end_turn(self.new_run(), usage=self.usage(10, 2, 3))
        self.continue_run(self.board_, mixed)
        mixed, _ = self.end_turn(mixed, usage=self.usage(20, 4, 5, completeness="partial"))
        self.accept_run(self.board_, mixed)
        missing, _ = self.end_turn(self.new_run())
        self.accept_run(self.board_, missing)
        stats = self.facts()[0]["statistics"]
        self.assertEqual(stats["samples"], 2)
        self.assertEqual(stats["usage"], NO_USAGE)

    def test_unsettled_delivery_is_not_a_sample(self):
        self.end_turn(self.new_run())
        self.assertEqual(self.facts()[0]["statistics"]["samples"], 0)

    def test_never_spawned_release_has_no_receipt_and_discards_an_earlier_delivery(self):
        run, _ = self.end_turn(self.new_run())
        self.continue_run(self.board_, run)
        claim = self.claim(self.board_, run_id=run["runId"])["claim"]
        self.board_.store.release_execution(claim["attempt"]["attemptId"], reason="execution-process-not-created")
        self.assertEqual(self.facts()[0]["infrastructureFailures"]["count"], 0)
        self.conclude(run)
        self.assertEqual(self.facts()[0]["statistics"]["samples"], 0)

    def test_send_back_counts_manual_rounds_but_not_assistance_before_first_delivery(self):
        run, _ = self.end_turn(self.new_run(), disposition="assistance", seconds=3)
        self.continue_run(self.board_, run, command_id="resolve-assistance")
        run, _ = self.end_turn(run, seconds=10)
        self.continue_run(self.board_, run, command_id="review-first")
        run, _ = self.end_turn(run, seconds=30)
        self.accept_run(self.board_, run)
        stats = self.facts()[0]["statistics"]
        self.assertEqual((stats["samples"], stats["successes"], stats["firstPasses"], stats["reworkRounds"]), (1, 1, 0, 1))
        self.assertEqual(stats["medianDeliverySeconds"], 20)

    def test_adjusted_integration_is_not_first_pass(self):
        run, _ = self.end_turn(self.new_run())
        self.accept_run(self.board_, run)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_integrations SET verification_json=? WHERE run_id=?",
                       (json.dumps({"differingPaths": [], "missingPaths": [], "hostPaths": ["changed.py"]}), run["runId"]))
        stats = self.facts()[0]["statistics"]
        self.assertEqual((stats["successes"], stats["firstPasses"]), (1, 0))

    def test_handoff_makes_old_delivery_a_failure_and_new_delivery_a_success(self):
        other = {**BUDDY, "effort": "high"}
        run, _ = self.end_turn(self.new_run())
        self.continue_run(self.board_, run, buddy=other)
        run, _ = self.end_turn(run)
        self.accept_run(self.board_, run)
        first, second = [fact["statistics"] for fact in self.facts(BUDDY, other)]
        self.assertEqual((first["samples"], first["failures"], first["reworkRounds"]), (1, 1, 1))
        self.assertEqual((second["successes"], second["firstPasses"]), (1, 1))

    def test_failed_execution_and_unaccepted_delivery_count_as_failure(self):
        failed, _ = self.end_turn(self.new_run(), failed=True)
        self.conclude(failed)
        unaccepted, _ = self.end_turn(self.new_run())
        self.conclude(unaccepted)
        stats = self.facts()[0]["statistics"]
        self.assertEqual((stats["samples"], stats["failures"]), (2, 2))

    def test_cancellation_without_delivery_is_not_a_sample_but_delivery_counts(self):
        for disposition in ("assistance", "completed"):
            run, _ = self.end_turn(self.new_run(), disposition=disposition)
            self.board_.call("workflow_cancel", {"runId": run["runId"], "commandId": f"cancel-{disposition}",
                                                 **self.control(run)})
            self.conclude(run)
        stats = self.facts()[0]["statistics"]
        self.assertEqual((stats["samples"], stats["failures"]), (1, 1))

    def test_last_nonability_turn_discards_the_kind_sample(self):
        for reason in ("workspace-error", "harness-error"):
            run, _ = self.end_turn(self.new_run())
            self.continue_run(self.board_, run, command_id=f"continue-{reason}")
            run, attempt = self.end_turn(run, failed=True, category=reason)
            if reason == "harness-error":
                with self.board_.store.db.write() as db:
                    db.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.code','harness-unavailable') WHERE attempt_id=?", (attempt,))
            self.conclude(run)
        facts = self.facts()[0]
        self.assertEqual(facts["statistics"]["samples"], 0)
        self.assertEqual(facts["infrastructureFailures"], {"count": 1, "lastAt": self.clock()})

    def test_every_infrastructure_turn_counts_without_terminal_run_or_ability_sample(self):
        run, attempt = self.end_turn(self.new_run(), failed=True)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.providerRejection',json(?)) WHERE attempt_id=?",
                       (json.dumps({"code": "quota-exceeded", "rejectionPhase": "before-model"}), attempt))
        self.assertEqual(self.facts()[0]["infrastructureFailures"]["count"], 1)
        self.continue_run(self.board_, run)
        run, attempt = self.end_turn(run, failed=True)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.code','harness-unavailable') WHERE attempt_id=?", (attempt,))
        facts, other = self.facts(BUDDY, {**BUDDY, "provider": "other"})
        self.assertEqual(facts["infrastructureFailures"], {"count": 2, "lastAt": self.clock()})
        self.assertEqual(other["infrastructureFailures"], {"count": 0, "lastAt": None})
        self.conclude(run)
        self.assertEqual(self.facts()[0]["statistics"]["samples"], 0)

    def test_load_simulates_parallel_slots_and_fifo_queue_and_exposes_overdue_lower_bound(self):
        active = self.new_run(expectedSeconds=100)
        self.claim(self.board_, run_id=active["runId"])
        second = self.new_run(expectedSeconds=60, effort="high")
        self.claim(self.board_, run_id=second["runId"])
        self.clock.advance(20)
        self.new_run(expectedSeconds=30)
        self.new_run(expectedSeconds=10)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE attempts SET execution_state='uncertain',ownership='uncertain' WHERE task_id=?", (active["runId"],))
        load = self.facts()[0]["load"]
        self.assertEqual(load, {"active": 2, "limit": 2, "queued": 2,
                               "wait": {"seconds": 80, "atLeast": False, "overdue": 0}})
        self.clock.advance(50)
        self.assertEqual(self.facts()[0]["load"]["wait"], {"seconds": 30, "atLeast": True, "overdue": 1})
        # A reduced ceiling retains both occupancies until enough executions end.
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency VALUES(?,?,?,?)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"], 1))
        self.assertEqual(self.facts()[0]["load"], {"active": 2, "limit": 1, "queued": 2,
                         "wait": {"seconds": 70, "atLeast": True, "overdue": 1}})

    def test_same_timestamp_queue_uses_admission_task_id_order(self):
        runs = sorted([self.new_run() for _ in range(3)], key=lambda run: run["runId"])
        with self.board_.store.db.write() as db:
            # Admission order is 100,10,30. Force storage order to 30,10,100:
            # parallel slots give different wait estimates for those orders.
            for index, (run, seconds) in enumerate(zip(runs, (100, 10, 30))):
                db.execute("UPDATE tasks SET rowid=?,expected_seconds=? WHERE task_id=?",
                           (100 - index, seconds, run["runId"]))
        self.assertEqual(self.facts()[0]["load"], {"active": 0, "limit": 2, "queued": 3,
                         "wait": {"seconds": 40, "atLeast": False, "overdue": 0}})

    def test_timeline_uses_the_frozen_effort_and_not_the_later_run_buddy(self):
        run, _ = self.end_turn(self.new_run())
        self.continue_run(self.board_, run, buddy={**BUDDY, "effort": "high"})
        run, _ = self.end_turn(run)
        timeline = self.board_.call("macro_task_timeline", {"macroTaskId": f"run:{run['runId']}"})
        executions = [span for span in timeline["spans"] if span["kind"] == "execution"]
        self.assertEqual([span["buddy"]["effort"] for span in executions], ["off", "high"])
