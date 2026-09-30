"""The no-reset exhaustion retry window and its explicit re-detect entry (ADR-019 stage 2).

A persisted exhaustion without a recorded reset carries exactly one single-use
window: one hour after the blocking observation (or the last consumed retry) one
routing decision may admit the configuration again. The claim is durable and
transactional with the selected configuration; reads never consume or
open a window; unknown or partial observations neither clear the exhaustion nor
advance the window; and the explicit re-detect opens the window without any
model call or balance query. Every case uses a private board and a fake clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from buddy.decision import DecisionCoordinator
from buddy.errors import BoardError
from buddy.native_observations import exhausted
from buddy.quota_routing import RETRY_WAIT_SECONDS, configuration_retry, record, retry_facts, routing_facts
from support import BoardTestCase, FakeClock
import test_decision as _decision_fixtures
from test_decision import PROFILE_ID, SECOND_PROFILE_ID

T0 = "2026-01-01T00:00:00.000Z"
CONFIG = {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash"}


def moment(offset: float) -> str:
    """One fake-clock timestamp without milliseconds, the persisted ISO format."""
    value = datetime.fromisoformat(T0.replace("Z", "+00:00")) + timedelta(seconds=offset)
    return value.isoformat().replace("+00:00", "Z")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class QuotaRetryBoard(BoardTestCase):
    """A private board on the fake clock with one seeded provider exhaustion."""

    seed = _decision_fixtures.DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.clock = FakeClock(T0)
        self.new_board = self.board
        self.board = self.new_board(clock=self.clock)
        self.seed(self.board, decision_profile=None)
        self.exhaust_provider()

    def exhaust_provider(self, **changes):
        with self.board.store.db.write() as db:
            record(db, "dsh", {"provider": CONFIG["provider"], "source": "native-fixture",
                               "observedAt": T0, "reachedType": "usage_limit_reached", **changes})

    def candidates(self, decision_id=None, *, consume=True, board=None):
        board = board or self.board
        with board.store.db.write() as db:
            rows, retried = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, decision_id=decision_id, consume_retries=consume, now=self.clock.value)
            return [row["profile_id"] for row in rows], retried

    def marker_value(self):
        with self.board.store.db.read() as db:
            from buddy.db import canonical_json
            row = db.execute("SELECT value FROM meta WHERE key=?",
                             ("quota-retry:" + canonical_json(["dsh", "deepseek-official", None]),)).fetchone()
        return json.loads(row[0]) if row else None


class QuotaRetryTests(QuotaRetryBoard):
    def test_window_opens_after_one_hour_and_is_consumed_exactly_once(self):
        # Before the hour the configuration stays excluded and no marker exists.
        self.assertEqual(self.candidates("dec-first")[0], [])
        self.assertIsNone(self.marker_value())
        self.clock.advance(RETRY_WAIT_SECONDS - 1)
        self.assertEqual(self.candidates("dec-early")[0], [])
        # One second later the window opens: one decision admits both provider
        # profiles and claims the single window durably in the same transaction.
        self.clock.advance(1)
        rows, retried = self.candidates("dec-first")
        self.assertEqual(rows, [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(retried, [PROFILE_ID, SECOND_PROFILE_ID])
        marker = self.marker_value()
        self.assertEqual(marker["consumedBy"], "dec-first")
        self.assertEqual(parse(marker["consumedAt"]), parse(self.clock.value))
        self.assertIsNone(marker["manualAt"])
        # A concurrent later decision re-reads the marker and stays excluded.
        self.assertEqual(self.candidates("dec-second"), ([], []))
        # The exhaustion itself is unchanged: reads and reminders keep reporting it.
        with self.board.store.db.read() as db:
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock.value))
        # A consumed retry without a newer usable observation waits another hour.
        self.clock.advance(RETRY_WAIT_SECONDS - 1)
        self.assertEqual(self.candidates("dec-third")[0], [])
        self.clock.advance(1)
        self.assertEqual(self.candidates("dec-third")[0], [PROFILE_ID, SECOND_PROFILE_ID])

    def test_publish_bounds_recheck_accepts_only_the_consuming_decision(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.candidates("dec-first")
        with self.board.store.db.write() as db:
            # The claim is not repeated, but the window this decision consumed
            # keeps its own frozen answer legal at the publish-time re-check.
            rows, retried = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, decision_id="dec-first", consume_retries=False, now=self.clock.value)
            self.assertEqual([row["profile_id"] for row in rows], [PROFILE_ID, SECOND_PROFILE_ID])
            self.assertEqual(retried, [PROFILE_ID, SECOND_PROFILE_ID])
            self.assertEqual(self.marker_value()["consumedBy"], "dec-first")
            rows, _retried = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, decision_id="dec-other", consume_retries=False, now=self.clock.value)
            self.assertEqual(rows, [])

    def test_reads_never_consume_or_open_a_window(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.board.call("console_snapshot", {})
        self.board.call("capabilities", {})
        self.board.call("model_profiles", {"query": "deepseek"})
        self.board.call("selection_list", {})
        # The read-only candidate projection still reports the configuration
        # excluded, and no marker was written by any read.
        with self.board.store.db.read() as db:
            rows, retried = DecisionCoordinator._scan_candidates(db, [], coding_only=True, now=self.clock.value)
            self.assertEqual(rows, [])
            self.assertEqual(retried, [])
        self.assertIsNone(self.marker_value())

    def test_unknown_observation_neither_clears_nor_advances_the_window(self):
        with self.board.store.db.write() as db:
            record(db, "dsh", {"provider": CONFIG["provider"], "source": "native-fixture",
                               "observedAt": moment(600), "windows": []})
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock.value))
        self.clock.advance(RETRY_WAIT_SECONDS - 1)
        self.assertEqual(self.candidates("dec-a")[0], [])
        self.clock.advance(1)
        rows, _retried = self.candidates("dec-a")
        self.assertEqual(rows, [PROFILE_ID, SECOND_PROFILE_ID])

    def test_newer_available_observation_clears_exhaustion_and_drops_the_marker(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.candidates("dec-a")
        self.assertIsNotNone(self.marker_value())
        with self.board.store.db.write() as db:
            record(db, "dsh", {"provider": CONFIG["provider"], "source": "native-fixture",
                               "observedAt": moment(RETRY_WAIT_SECONDS + 60), "ordinaryUsageAllowed": True})
            self.assertIsNone(exhausted(db, CONFIG, now=self.clock.value))
        self.assertIsNone(self.marker_value())
        self.assertEqual(self.candidates("dec-b")[0], [PROFILE_ID, SECOND_PROFILE_ID])

    def test_newer_blocked_observation_restarts_the_hour(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.candidates("dec-first")
        # The retried call failed again with an explicit quota error: the newer
        # observation supersedes the consumed marker and restarts the hour.
        with self.board.store.db.write() as db:
            record(db, "dsh", {"provider": CONFIG["provider"], "source": "native-fixture",
                               "observedAt": moment(RETRY_WAIT_SECONDS), "reachedType": "usage_limit_reached"})
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock.value))
        self.assertEqual(self.candidates("dec-second")[0], [])
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.assertEqual(self.candidates("dec-second")[0], [PROFILE_ID, SECOND_PROFILE_ID])

    def test_known_reset_keeps_scheduled_recovery_and_no_retry_window(self):
        self.exhaust_provider(observedAt=moment(10), resetsAt=moment(7200))
        with self.board.store.db.write() as db:
            item = json.loads(db.execute("SELECT value FROM meta WHERE key LIKE 'quota-routing:%'").fetchone()[0])
            self.assertIsNone(retry_facts(db, item, now=self.clock.value))
            self.assertIsNone(configuration_retry(db, CONFIG, now=self.clock.value))
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.assertEqual(self.candidates("dec-a")[0], [])
        # At the recorded reset the ordinary recovery applies with no claim.
        self.clock.advance(RETRY_WAIT_SECONDS)
        with self.board.store.db.write() as db:
            self.assertIsNone(exhausted(db, CONFIG, now=self.clock.value))
        rows, retried = self.candidates("dec-a")
        self.assertEqual(rows, [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(retried, [])
        self.assertIsNone(self.marker_value())

    def test_retained_display_observation_alone_blocks_without_a_window(self):
        # A display observation that was never processed into a persisted record
        # (upgrade-era data) keeps blocking, but only a persisted record can carry
        # a retry window, so it never opens one.
        config = {"requestId": "recheck-1", "adapter": "dsh", "provider": "display-only", "model": "any-model"}
        value = {"source": "native-fixture", "observedAt": T0, "provider": "display-only",
                 "reachedType": "usage_limit_reached", "windows": []}
        with self.board.store.db.write() as db:
            db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='dsh'", (json.dumps(value),))
            self.assertIsNotNone(exhausted(db, config, now=self.clock.value))
            rows, retried = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, decision_id="dec-fallback", now=self.clock.value)
            self.assertEqual(rows, [])
            self.assertEqual(retried, [])
            self.assertIsNone(configuration_retry(db, config, now=self.clock.value))
        self.clock.advance(RETRY_WAIT_SECONDS)
        with self.board.store.db.write() as db:
            from buddy.quota_routing import claim
            # Even with the hour elapsed, a retained display observation alone
            # carries no claimable window.
            self.assertIsNone(claim(db, config, decision_id="dec-fallback-open", now=self.clock.value))
            self.assertIsNotNone(exhausted(db, config, now=self.clock.value))

    def test_limit_scope_and_projection_surfaces(self):
        with self.board.store.db.write() as db:
            record(db, "codex", {"provider": "openai", "source": "native-fixture",
                                 "observedAt": moment(5), "reachedType": "usage_limit_reached",
                                 "scope": {"limitId": "limited-model"}})
            self.assertIsNotNone(exhausted(db, {"adapter": "codex", "provider": "openai", "model": "limited-model"}, now=self.clock.value))
            self.assertIsNone(exhausted(db, {"adapter": "codex", "provider": "openai", "model": "other-model"}, now=self.clock.value))
            facts = routing_facts(db, "codex", now=self.clock.value)
            self.assertEqual(facts[0]["limitId"], "limited-model")
            self.assertEqual(parse(facts[0]["retry"]["eligibleAt"]), parse(moment(5 + RETRY_WAIT_SECONDS)))
            self.assertFalse(facts[0]["retry"]["open"])
        snapshot = self.board.call("console_snapshot", {})
        profile = next(item for item in snapshot["profiles"] if item["profileId"] == PROFILE_ID)
        self.assertTrue(profile["quotaExhausted"])
        self.assertEqual(parse(profile["quotaRetry"]["eligibleAt"]), parse(moment(RETRY_WAIT_SECONDS)))
        self.assertFalse(profile["quotaRetry"]["open"])
        listed = self.board.call("model_profiles", {"query": "flash"})
        entry = next(item for item in listed["profiles"] if item["profileId"] == PROFILE_ID)
        self.assertEqual(parse(entry["quotaRetry"]["eligibleAt"]), parse(moment(RETRY_WAIT_SECONDS)))
        health = next(row for row in self.board.call("capabilities", {})["harnesses"] if row["adapter"] == "dsh")
        self.assertEqual(health["quotaRouting"][0]["provider"], CONFIG["provider"])
        # Read views project against the wall clock; the frozen eligible time is
        # the recorded fact the console explains from.
        self.assertEqual(parse(health["quotaRouting"][0]["retry"]["eligibleAt"]), parse(moment(RETRY_WAIT_SECONDS)))

    def test_explicit_host_configuration_is_reminded_and_never_consumes_a_window(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        result = self.board.call("workflow_submit", {"requestId": "explicit-after-window", "hostId": "host",
            "task": "Explicit choice", "cwd": str(self.git_workdir()),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"})
        self.assertEqual(result["executionConfiguration"]["model"], "deepseek-flash")
        self.assertEqual(result["quotaWarnings"][0]["code"], "HARNESS_QUOTA_EXHAUSTED")
        self.assertIsNone(self.marker_value())

    def test_sole_candidate_retry_completes_by_program_selection(self):
        # Exactly one capability-matching candidate: before the window opens the
        # decision is an honest Host boundary, after it the program selects the
        # retried configuration without any Router call.
        reply = self.board.call("selection_request", {"requestId": "pick-image", "task": "needs image input",
                                                      "requiredCapabilities": ["input:image"]})
        self.assertEqual(reply["status"], "needs-host")
        decision = self.board.call("selection_get", {"decisionId": reply["decisionId"]})["decision"]
        self.assertEqual(decision["routingBasis"]["candidateCount"], 0)
        self.assertNotIn("quotaRetryProfileIds", decision["routingBasis"])
        self.clock.advance(RETRY_WAIT_SECONDS)
        reply = self.board.call("selection_request", {"requestId": "pick-image-open", "task": "needs image input",
                                                      "requiredCapabilities": ["input:image"]})
        self.assertEqual(reply["status"], "completed")
        decision = self.board.call("selection_get", {"decisionId": reply["decisionId"]})["decision"]
        self.assertEqual(decision["profileId"], PROFILE_ID)
        self.assertEqual(decision["routingBasis"]["quotaRetryProfileIds"], [PROFILE_ID])
        # The single window is spent: a later decision cannot repeat the trial.
        again = self.board.call("selection_request", {"requestId": "pick-image-again", "task": "needs image input",
                                                      "requiredCapabilities": ["input:image"]})
        self.assertEqual(again["status"], "needs-host")

    def test_unresolved_router_does_not_consume_any_candidate_window(self):
        self.clock.advance(RETRY_WAIT_SECONDS)
        reply = self.board.call("selection_request", {"requestId": "no-router", "task": "choose a configuration"})
        self.assertEqual(reply["status"], "needs-host")
        self.assertIsNone(self.marker_value())
        with self.board.store.db.read() as db:
            self.assertTrue(configuration_retry(db, CONFIG, now=self.clock.value)["open"])

    def test_two_concurrent_program_selections_share_one_retry(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.clock.advance(RETRY_WAIT_SECONDS)
        barrier = Barrier(2)
        def choose(index):
            barrier.wait(timeout=5)
            return self.board.call("selection_request", {"requestId": "concurrent-" + str(index),
                "task": "requires image", "requiredCapabilities": ["input:image"]})["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(choose, (1, 2)))
        self.assertEqual(sorted(states), ["completed", "needs-host"])

    def test_legacy_native_exhaustion_is_materialized_by_admission_not_reads(self):
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM meta WHERE key LIKE 'quota-routing:%'")
            db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='dsh'",
                (json.dumps({"provider": CONFIG["provider"], "observedAt": T0, "source": "native-fixture",
                             "reachedType": "insufficient_quota", "balanceZero": True}),))
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.candidates()
        self.assertIsNone(self.marker_value())
        reply = self.board.call("selection_request", {"requestId": "legacy-retry", "task": "image",
            "requiredCapabilities": ["input:image"]})
        self.assertEqual(reply["status"], "completed")

    def git_workdir(self):
        import subprocess
        path = self.workdir()
        for args in (("init", "-q"), ("config", "user.name", "Fixture"),
                     ("config", "user.email", "fixture@example.invalid")):
            subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
        (path / "README.md").write_text("fixture\n")
        subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True, capture_output=True)
        return path


class RedetectTests(QuotaRetryBoard):
    def test_same_request_after_consumption_never_reopens_and_input_change_conflicts(self):
        params = {"requestId": "same-request", "adapter": "dsh", "provider": CONFIG["provider"]}
        self.board.call("quota_redetect", params)
        self.candidates("consume")
        before = self.marker_value()
        reply = self.board.call("quota_redetect", params)
        self.assertTrue(reply["duplicate"])
        self.assertEqual(self.marker_value(), before)
        self.assertEqual(self.candidates("other")[0], [])
        with self.assertRaises(BoardError):
            self.board.call("quota_redetect", {**params, "provider": "other"})
        with self.board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='harness.quota_redetect'").fetchone()[0], 1)

    def test_redetect_opens_one_window_idempotently(self):
        first = self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official"})["quota"]
        self.assertEqual(first["opened"], [None])
        self.assertEqual(first["alreadyOpen"], [])
        self.assertTrue(first["records"][0]["retry"]["open"])
        self.assertTrue(first["records"][0]["retry"]["pendingManual"])
        # Repeating the identical request changes nothing.
        second = self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official"})["quota"]
        self.assertEqual(second, first)
        # The opened window admits exactly one decision, immediately.
        self.assertEqual(self.candidates("dec-manual")[0], [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(self.candidates("dec-next")[0], [])
        # A lost reply replayed after consumption cannot reopen the chance.
        replay = self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official"})
        self.assertTrue(replay["duplicate"])
        self.assertEqual(self.candidates("dec-replay")[0], [])
        # After the trial a new explicit re-detect opens a fresh window.
        third = self.board.call("quota_redetect", {"requestId": "recheck-2", "adapter": "dsh", "provider": "deepseek-official"})["quota"]
        self.assertEqual(third["opened"], [None])
        self.assertEqual(self.candidates("dec-manual-2")[0], [PROFILE_ID, SECOND_PROFILE_ID])

    def test_redetect_reports_known_resets_without_state_change(self):
        with self.board.store.db.write() as db:
            record(db, "dsh", {"provider": "other-provider", "source": "native-fixture",
                               "observedAt": T0, "reachedType": "usage_limit_reached", "resetsAt": moment(7200)})
        reply = self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "other-provider"})["quota"]
        self.assertEqual(reply["opened"], [])
        self.assertEqual(reply["alreadyOpen"], [])
        self.assertIsNone(reply["records"][0]["retry"])
        self.assertEqual(parse(reply["records"][0]["resetsAt"]), parse(moment(7200)))
        with self.board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'quota-retry:%'").fetchone()[0], 0)

    def test_redetect_is_honest_about_unknown_providers_and_refuses_free_text(self):
        reply = self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "not-recorded"})["quota"]
        self.assertEqual(reply["records"], [])
        self.assertEqual(reply["opened"], [])
        with self.assertRaises(BoardError) as refused:
            self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "not an identifier"})
        self.assertEqual(refused.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as unsupported:
            self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "unknown-harness", "provider": "deepseek-official"})
        self.assertEqual(unsupported.exception.code, "UNSUPPORTED_ADAPTER")
        with self.assertRaises(BoardError) as unknown_field:
            self.board.call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official", "secret": "x"})
        self.assertEqual(unknown_field.exception.code, "INVALID_ARGUMENT")

    def test_console_surface_requires_a_writable_session(self):
        reply = self.board.console_call("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official"})
        self.assertEqual(reply["quota"]["opened"], [None])
        from buddy.console import CONSOLE_OPERATIONS
        from buddy.console_sessions import READ_OPERATIONS
        from buddy.workflow import AGENT_OPERATIONS
        self.assertIn("quota_redetect", CONSOLE_OPERATIONS)
        self.assertNotIn("quota_redetect", READ_OPERATIONS)
        self.assertNotIn("quota_redetect", AGENT_OPERATIONS)
        # Without an authenticated console session the mutation is refused.
        with self.assertRaises(BoardError) as refused:
            self.board.console.command("quota_redetect", {"requestId": "recheck-1", "adapter": "dsh", "provider": "deepseek-official"})
        self.assertEqual(refused.exception.code, "CONSOLE_SESSION_EXPIRED")


class RedetectSurfaceTests(unittest.TestCase):
    def test_cli_name_maps_to_the_named_operation(self):
        from buddy import transport
        self.assertEqual(transport.METHOD_MAP["quota-redetect"], ("control", "quota_redetect"))
        from buddy.service import CONTROL_OPERATIONS
        self.assertIn("quota_redetect", CONTROL_OPERATIONS)
        from buddy.contracts import BuddyControl
        self.assertTrue(hasattr(BuddyControl, "quota_redetect"))

    def test_help_documents_the_honest_parameters(self):
        from buddy import cli_help
        summary = cli_help.SUMMARIES["quota-redetect"]
        self.assertIn("no model or balance query", summary)
        method = cli_help.method_help("quota-redetect")
        names = {parameter.name for parameter in method.parameters}
        self.assertEqual(names, {"adapter", "provider", "requestId"})
        self.assertTrue(all(parameter.required for parameter in method.parameters))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
