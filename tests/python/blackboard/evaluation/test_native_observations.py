"""Native attempt facts update token usage and provider cooling on a private board."""
import json

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.evaluation.native_observations import failure_view, persist
from hey_my_buddy.blackboard.routing.quota_routing import cooling, provider_state
from hey_my_buddy.blackboard.service.harness_health import read_health
from support import BoardTestCase, FakeClock
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE


T0 = "2026-09-29T10:00:00.000Z"


def denial(*, observed_at=T0, resets_at=None, phase="before-model"):
    return {"code": "quota-exceeded", "nativeCode": "usageLimitExceeded",
            "source": "fixture/native-rejection", "observedAt": observed_at,
            "resetsAt": resets_at, "rejectionPhase": phase}


class NativeCoolingTests(BoardTestCase):
    def test_only_structured_failure_is_projected(self):
        failure = denial()
        self.assertEqual(failure_view({"quotaFailure": failure}), failure)
        self.assertIsNone(failure_view({"code": "quota-rejected"}))
        self.assertIsNone(failure_view({"quotaFailure": {"rateLimitType": "five_hour", "resetsAt": 1790467200}}))
        self.assertIsNone(failure_view({"error": "quota exhausted"}))

    def test_denial_records_provider_cooling_without_usage_or_native_window(self):
        board = self.board()
        attempt = {"attempt_id": "fixture-denial", "adapter": "codex",
                   "model_harness": "codex", "model_provider": "openai",
                   "started_at": "2026-09-29T09:59:00.000Z"}
        with board.store.db.write() as db:
            persist(db, attempt, {"quotaFailure": denial()}, status="failed",
                    shutdown_confirmed=True, now=T0)
            self.assertEqual(provider_state(db, "codex", "openai"), {
                "provider": "openai", "coolingUntil": "2026-09-29T10:30:00.000Z",
                "consecutiveRejections": 1})
            self.assertEqual(cooling(db, {"harness": "codex", "provider": "openai"},
                                     now=T0)["provider"], "openai")
            self.assertIsNone(cooling(db, {"harness": "codex", "provider": "another"}, now=T0))
            self.assertEqual(read_health(db, "codex")["providers"], [
                {"provider": "openai", "coolingUntil": "2026-09-29T10:30:00.000Z",
                 "consecutiveRejections": 1}])

    def test_late_success_does_not_erase_newer_denial(self):
        board = self.board()
        attempt = {"attempt_id": "fixture", "adapter": "codex",
                   "model_harness": "codex", "model_provider": "openai",
                   "started_at": "2026-09-29T09:59:00.000Z"}
        with board.store.db.write() as db:
            persist(db, attempt, {"quotaFailure": denial()}, status="failed",
                    shutdown_confirmed=True, now=T0)
            persist(db, attempt, {}, status="ok", shutdown_confirmed=True,
                    now="2026-09-29T10:01:00.000Z")
            self.assertIsNotNone(provider_state(db, "codex", "openai"))
            persist(db, {**attempt, "started_at": "2026-09-29T10:02:00.000Z"}, {},
                    status="ok", shutdown_confirmed=True, now="2026-09-29T10:03:00.000Z")
            self.assertIsNone(provider_state(db, "codex", "openai"))


class NativeReceiptTests(WorkflowTestCase):
    """Token counters cross the actual execution receipt and read-model boundary."""

    def test_native_token_receipt_persists_once(self):
        from hey_my_buddy.protocol.usage import normalize_token_usage
        from protocol.test_usage import dsh_usage_record
        clock = FakeClock(T0)
        board = self.board(clock=clock)
        submitted = self.submit(board, request_id="native-usage", cwd=str(self.workdir("native-usage")))
        claim = self.claim(board, worker_id="worker-native", claim_request_id="native-usage",
                           run_id=submitted["runId"])
        captured = {}
        usage = dsh_usage_record()

        def native_result(result):
            result["tokenUsage"] = usage
            captured.update(result)

        self.finish_turn(board, claim, worker_id="worker-native", result_mutator=native_result)
        attempt = claim["claim"]["attempt"]
        with board.store.db.read() as db:
            event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with self.assertRaises(BoardError) as caught:
            board.call("execution_result", {"attemptId": attempt["attemptId"],
                                         "generation": attempt["generation"], "nonce": NONCE,
                                         "status": "ok", "result": captured,
                                         "shutdownConfirmed": True, "exitCode": 0})
        self.assertEqual(caught.exception.code, "ATTEMPT_FINISHED")
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        expected = normalize_token_usage(usage)
        self.assertEqual(view["currentTurn"]["tokenUsage"], expected)
        self.assertEqual(view["task"]["tokenUsage"], expected)
        self.assertEqual(view["audit"]["turns"][0]["tokenUsage"], expected)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], event_count)
            stored = db.execute("SELECT token_usage_json FROM attempts WHERE attempt_id=?",
                                (attempt["attemptId"],)).fetchone()[0]
            self.assertEqual(json.loads(stored), expected)
