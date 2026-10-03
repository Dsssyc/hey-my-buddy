"""Quota warnings use retained native observations with explicit freshness and scope."""
import json

from hey_my_buddy.blackboard.service.harness_health import read_health
from hey_my_buddy.blackboard.evaluation.native_observations import quota_view, warnings
from support import BoardTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE


class QuotaVisibilityTests(BoardTestCase):
    def test_optional_quota_read_is_bounded_and_restores_the_execution_deadline(self):
        import time
        from types import SimpleNamespace
        from hey_my_buddy.buddy.harnesses.codex.runner import _observe_quota
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        connection = SimpleNamespace(deadline=time.monotonic() + 1000)
        previous = connection.deadline
        def read(_method, _params):
            self.assertLessEqual(connection.deadline - time.monotonic(), 2)
            raise CodexProtocolError("deadline", "fixture metadata read stalled")
        connection.call = read
        retained = {"source": "earlier-native-observation"}
        self.assertEqual(_observe_quota(connection, SimpleNamespace(quota_candidate=retained)), retained)
        self.assertEqual(connection.deadline, previous)

    def test_canonical_quota_keeps_its_native_scope_through_every_import(self):
        from hey_my_buddy.protocol.usage import normalize_quota
        raw = {"source": "native-fixture", "observedAt": "2026-09-29T10:00:00Z", "provider": "openai",
               "nativeAccountId": "fixture-account", "limitId": "fixture-limit", "planType": "fixture-plan",
               "windows": [{"name": "five-hour", "usedPercent": 94, "resetsAt": "2026-09-29T12:00:00Z"}]}
        once = normalize_quota(raw)
        self.assertEqual(normalize_quota(once), once)

    def test_missing_cache_cannot_be_relabelled_as_an_inclusive_input_total(self):
        from hey_my_buddy.protocol.usage import normalize_token_usage
        usage = normalize_token_usage({"inputTokens": 100, "outputTokens": 10,
                                       "inputBasis": "excludes-cached", "source": "native-fixture"})
        self.assertIsNone(usage["inputTokens"])
        self.assertEqual(usage["outputTokens"], 10)

    def test_only_recent_matching_native_windows_warn_and_unknown_stays_unknown(self):
        board = self.board()
        self.assertIsNone(quota_view(None))
        observed = {"source": "native-fixture", "observedAt": "2026-09-29T10:00:00Z", "provider": "openai",
                    "windows": [{"name": "five-hour", "usedPercent": 97, "resetsAt": "2026-09-29T12:00:00Z"}]}
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='codex'", (json.dumps(observed),))
            self.assertEqual(len(warnings(db, {"adapter": "codex", "provider": "openai"}, now="2026-09-29T10:01:00Z")), 1)
            self.assertEqual(warnings(db, {"adapter": "codex", "provider": "different"}, now="2026-09-29T10:01:00Z"), [])
            self.assertEqual(warnings(db, {"adapter": "codex", "provider": "openai"}, now="2026-09-29T12:01:00Z"), [])
        unknown = quota_view({**observed, "windows": [{"name": "five-hour", "usedPercent": None, "resetsAt": None}]}, now="2026-09-29T10:01:00Z")
        self.assertIsNone(unknown["windows"][0]["usedPercent"])

    def test_health_refresh_fields_cannot_overwrite_the_separate_quota_observation(self):
        board = self.board()
        quota = {"source": "native-fixture", "observedAt": "2026-09-29T10:00:00Z", "windows": []}
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET quota_json=?,record_json=? WHERE adapter='codex'",
                       (json.dumps(quota), json.dumps({"quota": {"source": "stale-embedded-copy"}, "version": "new"})))
            self.assertEqual(read_health(db, "codex")["quota"]["source"], "native-fixture")
            self.assertEqual(read_health(db, "codex")["version"], "new")

    def test_a_recent_native_quota_failure_warns_without_a_fabricated_window(self):
        from hey_my_buddy.blackboard.evaluation.native_observations import persist
        board = self.board()
        attempt = {"attempt_id": "fixture", "adapter": "codex", "model_adapter": "codex", "model_provider": "openai"}
        with board.store.db.write() as db:
            persist(db, attempt, {"quotaFailure": {"nativeCode": "usageLimitExceeded", "source": "codex/turn-error",
                                                  "observedAt": "2026-09-29T10:00:00Z"}})
            observed = read_health(db, "codex")["quota"]
            self.assertEqual(observed["windows"], [])
            warning = warnings(db, {"adapter": "codex", "provider": "openai"}, now="2026-09-29T10:01:00Z")
            self.assertEqual(warning[0]["code"], "HARNESS_QUOTA_EXHAUSTED")
            self.assertEqual(warnings(db, {"adapter": "codex", "provider": "openai"}, now="2026-09-29T12:01:00Z")[0]['code'], 'HARNESS_QUOTA_EXHAUSTED')


class NativeReceiptTests(WorkflowTestCase):
    """Native fixture counters cross the actual receipt and read-model boundary."""

    def test_dsh_and_codex_receipts_persist_once_and_submit_warns_from_native_quota(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from support import FakeClock
        from hey_my_buddy.buddy.harnesses.codex.protocol import quota_candidate_from_response
        from hey_my_buddy.protocol.usage import normalize_token_usage
        from protocol.test_usage import dsh_usage_record, codex_usage_record, CODEX_NATIVE
        clock = FakeClock("2026-09-29T10:00:00Z")
        board = self.board(clock=clock)
        self.executors["codex"] = SimpleNamespace(native_resume=True, validate_turn_provenance=lambda record: None)
        quota_record = deepcopy(CODEX_NATIVE["rateLimitsRead"])
        quota_record["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = 95
        quota = quota_candidate_from_response(quota_record, observed_at=clock())
        for adapter, raw_usage in (("dsh", dsh_usage_record()), ("codex", codex_usage_record())):
            with self.subTest(adapter=adapter):
                config = {} if adapter == "dsh" else {"adapter": adapter, "provider": "openai", "model": "fixture-model", "effort": "low"}
                submitted = self.submit(board, request_id=adapter, cwd=str(self.workdir(adapter)), **config)
                worker = "worker-" + adapter
                board.call("worker_register", {"workerId": worker, "adapter": adapter, "capabilities": [adapter]})
                claim = self.claim(board, worker_id=worker, claim_request_id=adapter, run_id=submitted["runId"])
                captured = {}
                def native_result(result):
                    result.update(tokenUsage=raw_usage, quota=quota if adapter == "codex" else None)
                    captured.update(result)
                self.finish_turn(board, claim, worker_id=worker, result_mutator=native_result)
                attempt = claim["claim"]["attempt"]
                with board.store.db.read() as db:
                    count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                board.call("worker_result", {"workerId": worker, "attemptId": attempt["attemptId"],
                    "generation": attempt["generation"], "nonce": NONCE, "status": "ok", "result": captured,
                    "shutdownConfirmed": True, "exitCode": 0})
                view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
                expected = normalize_token_usage(raw_usage)
                self.assertEqual(view["currentTurn"]["tokenUsage"], expected)
                self.assertEqual(view["task"]["tokenUsage"], expected)
                self.assertEqual(view["audit"]["turns"][0]["tokenUsage"], expected)
                with board.store.db.read() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], count)
                    stored = db.execute("SELECT token_usage_json FROM attempts WHERE attempt_id=?", (attempt["attemptId"],)).fetchone()[0]
                    self.assertEqual(json.loads(stored), expected)
        next_run = self.submit(board, request_id="native-warning", cwd=str(self.workdir("next")), **config)
        self.assertEqual(next_run["quotaWarnings"][0]["code"], "HARNESS_QUOTA_NEAR_LIMIT")
        self.assertEqual(next_run["quotaWarnings"][0]["window"]["usedPercent"], 95)
        # A delayed old result cannot replace a more recent harness observation.
        from hey_my_buddy.blackboard.evaluation.native_observations import persist
        with board.store.db.write() as db:
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attemptId"],)).fetchone()
            persist(db, row, {"quota": {**quota, "observedAt": "2026-09-29T09:00:00Z", "windows": []}})
            self.assertEqual(read_health(db, "codex")["quota"]["observedAt"], clock())
