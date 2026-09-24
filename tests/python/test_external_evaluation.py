"""Harness-owned evaluation maintenance: prepare, card-only patches and history.

The external Harness is the maintenance host. These tests exercise the store
operations the Host wires as ``evaluation_prepare``/``evaluation-pre`` and
``evaluation_history`` against private state directories, with the production
transactions, classification and counters. ``prepare`` must collect actual
Host-reviewed facts deterministically and make no model call; publication is an
ordinary card-only writer-gate patch; history is a bounded read.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from support import FakeClock
from test_evaluation import EvaluationTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE, SECOND_PROFILE_ID

from buddy import evaluation as evaluation_module

PROFILE_EFFORT_OFF = PROFILE
PROFILE_EFFORT_HIGH = SECOND_PROFILE


class ExternalEvaluationTestCase(EvaluationTestCase):
    """The shared evaluation fixtures plus the external-maintenance helpers."""

    def prepare(self, board, request_id: str = "prepare-1", **params) -> dict:
        return board.evaluation.prepare({"requestId": request_id, **params})

    def history(self, board, **params) -> dict:
        return board.evaluation.history(params)

    def seed_maintenance_board(self, board) -> None:
        """Two enabled dsh profiles, one card each, one preference, one decision profile."""
        self.seed_catalog(board)
        self.publish(
            board,
            request_id="seed",
            command_id="seed-1",
            profileSettings=[{"profileId": item["profileId"], "enabled": True} for item in (PROFILE, SECOND_PROFILE)],
            preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "prefer", "reason": "cheap"}],
            configuration={"decisionProfileId": PROFILE_ID},
        )
        self.publish_cards(board, request_id="seed-cards", command_id="seed-cards", cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "first card",
                    "strengths": ["fast"],
                    "limitations": ["small"],
                    "risks": ["open risk"],
                    "evidenceIds": [],
                },
                {
                    "profileId": SECOND_PROFILE_ID,
                    "summary": "second card",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [],
                },
            ])

    def _ensure_model_workspace(self):
        """The deterministic workspace double every model fixture in this class shares."""
        from unittest.mock import patch as _patch
        from buddy import workflow
        from mock_workspace import MockWorkspace

        if not hasattr(self, "model_workspace"):
            self.model_workspace = MockWorkspace(self.directory)
            self.model_controls = {}
            self.enterContext(_patch.object(workflow, "_workspace_module", self.model_workspace))
        return self.model_workspace

    def model_attempt(
        self,
        board,
        run_id: str,
        request_id: str,
        *,
        profile: dict = PROFILE,
        verdict: str | None = "accepted",
        failed: bool = False,
        error: str | None = None,
    ) -> dict:
        """Claim, execute and review exactly one turn of an existing governed run."""
        from buddy.db import sha256_text

        workspace = self._ensure_model_workspace()
        client = board.client()
        worker_id = f"worker-{request_id}"
        client.register_worker(worker_id, adapter=profile["adapter"], capabilities=[profile["adapter"]])
        claim = client.claim(worker_id, f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        turn, attempt = claim["turn"], claim["attempt"]
        record = {
            "version": 1, "taskId": run_id, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "turnId": turn["turnId"], "resumeMode": turn["resumeMode"], "previousSessionId": turn["input"].get("previousSessionId"),
            "sessionId": f"fixture-{request_id}", "inputSha256": turn["inputSha256"],
            "promptSha256": sha256_text("external maintenance fixture"),
            "outcome": {"disposition": "completed", "summary": "model output", "remaining": [],
                        "decisions": [], "artifacts": [], "request": None},
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited", "rootSessionMatched": True},
        }
        client.submit_result(
            worker_id,
            attempt["attemptId"],
            attempt["generation"],
            "n" * 32,
            {
                "status": "failed" if failed else "ok",
                "result": {
                    "status": "failed" if failed else "ok",
                    "requested": {
                        "provider": profile["provider"], "model": profile["model"], "effort": profile["effort"],
                    },
                    "turn": record,
                    "workspaceSeal": workspace.seal(
                        self.directory, turn["input"]["executionWorkspace"], run_id, attempt["attemptId"]
                    ),
                    "error": error,
                    "finalText": "" if failed else "done",
                },
                "shutdownConfirmed": True,
                "exitCode": 1 if failed else 0,
            },
        )
        if verdict is not None and not failed:
            view = board.call("workflow_get", {"runId": run_id})
            board.call(
                "workflow_acknowledge",
                {
                    "runId": run_id,
                    "artifactId": view["finalArtifactId"],
                    "note": "reviewed the sealed result",
                    "verdict": verdict,
                    **self.model_controls[run_id],
                },
            )
        return claim

    def model_task(
        self,
        board,
        request_id: str,
        *,
        host_id: str = "host-a",
        cwd=None,
        profile: dict = PROFILE,
        verdict: str | None = "accepted",
        failed: bool = False,
        error: str | None = None,
    ) -> str:
        """One governed model attempt from an explicit source Host and project cwd.

        The production admission, receipt, acceptance and shutdown rules are used; the
        fixture only supplies the process/model output.
        """
        self._ensure_model_workspace()
        configuration = {key: profile[key] for key in ("adapter", "provider", "model", "effort")}
        submitted = board.call(
            "workflow_submit",
            {
                "requestId": request_id,
                "hostId": host_id,
                "task": "produce a model result",
                "cwd": str(cwd or self.workdir(request_id)),
                **configuration,
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        run_id = submitted["runId"]
        self.model_controls[run_id] = submitted["control"]
        self.model_attempt(board, run_id, request_id, profile=profile, verdict=verdict, failed=failed, error=error)
        return run_id

    def continue_model_run(self, board, run_id: str, *, command_id: str = "continue-model-1") -> dict:
        """Continue one governed run through the real Host continuation path.

        The continuation archives the previous review exactly like an explicit retry,
        so the next attempt starts from a Host boundary with no borrowed verdict.
        """
        view = board.call("workflow_get", {"runId": run_id})
        return board.call(
            "workflow_continue",
            {
                "runId": run_id,
                "commandId": command_id,
                "expectedRevision": view["revision"],
                "input": "produce the next turn",
                "helperPolicy": "keep",
                **self.model_controls[run_id],
            },
        )

    def cancelled_task(self, board, request_id: str = "task-cancelled") -> str:
        """A governed model attempt cancelled through its Host owner."""
        run_id, worker_id, claim = self.model_claim(board, request_id, PROFILE)
        board.call(
            "workflow_cancel",
            {"runId": run_id, "reason": "operator changed their mind", **self.model_controls[run_id]},
        )
        board.client().submit_result(
            worker_id,
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {
                "status": "cancelled",
                "result": {
                    "status": "cancelled",
                    "requested": {
                        "provider": PROFILE["provider"], "model": PROFILE["model"], "effort": PROFILE["effort"],
                    },
                },
                "shutdownConfirmed": True,
                "exitCode": None,
            },
        )
        return run_id

    def maintenance_publish(self, board, packet: dict, cards: list, *, request_id: str = "patch-1",
                            command_id: str = "patch-cmd-1", expected: int | None = None) -> dict:
        grant = board.call(
            "evaluation_write_begin",
            {
                "requestId": request_id,
                "expectedRevision": packet["tableRevision"] if expected is None else expected,
                "kind": "maintenance",
            },
        )
        return self.maintenance_publish_with(board, grant, command_id, cards=cards)

    def maintenance_publish_with(self, board, grant: dict, command_id: str, *, expected: int | None = None, **changes) -> dict:
        return board.call("assessment_publish", {
            "commandId": command_id, "writerId": grant["writerId"],
            "generation": grant["generation"], "writerToken": grant["writerToken"],
            "expectedRevision": grant["tableRevision"] if expected is None else expected,
            **changes,
        })

    def abort(self, board, grant: dict, command_id: str = "abort-1") -> dict:
        return board.call("evaluation_write_abort", {
            "commandId": command_id, "writerId": grant["writerId"],
            "generation": grant["generation"], "writerToken": grant["writerToken"],
        })

    @staticmethod
    def counts(board) -> dict:
        with board.store.db.read() as connection:
            return {
                "evidence": int(connection.execute("SELECT COUNT(*) AS c FROM evaluation_evidence").fetchone()["c"]),
                "samples": int(connection.execute("SELECT COUNT(*) AS c FROM evaluation_samples").fetchone()["c"]),
                "checkpoints": int(
                    connection.execute("SELECT COUNT(*) AS c FROM evaluation_maintenance_checkpoints").fetchone()["c"]
                ),
            }


# ---------------------------------------------------------------------------
# Deterministic fact collection
# ---------------------------------------------------------------------------
class PrepareCollectionTests(ExternalEvaluationTestCase):
    def test_collects_across_source_hosts_and_projects_without_a_model_call(self):
        board = self.board()
        self.seed_maintenance_board(board)
        first = self.model_task(board, "goal-a", host_id="host-a", cwd=self.workdir("repo-a"), profile=PROFILE)
        second = self.model_task(board, "goal-b", host_id="host-b", cwd=self.workdir("repo-b"), profile=SECOND_PROFILE)
        before = self.snapshot(board)

        packet = self.prepare(board, "prep-1", limit=32)
        self.assertEqual(len(packet["newEvidenceIds"]), 2)
        self.assertEqual([profile["profileId"] for profile in packet["profiles"] if profile["enabled"]], [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(packet["tableRevision"], before["tableRevision"])
        self.assertEqual(packet["progress"]["scanned"], 2)
        self.assertEqual(packet["progress"]["newFacts"], 2)
        self.assertTrue(packet["progress"]["complete"])
        self.assertEqual(packet["remaining"], {"pendingEvidence": 0, "reviewedBacklog": 0})
        self.assertEqual(packet["skipped"], [])
        self.assertEqual(packet["preferences"], [{"profileId": SECOND_PROFILE_ID, "mode": "prefer", "reason": "cheap"}])

        runs = {entry["runId"]: entry for entry in packet["evidence"]}
        self.assertEqual(set(runs), {first, second})
        for entry in runs.values():
            self.assertEqual(entry["source"], "host-review")
            self.assertTrue(entry["verified"])
            self.assertTrue(entry["counted"])
            self.assertTrue(entry["pending"])
            self.assertEqual(entry["fact"]["acceptanceVerdict"], "accepted")
            self.assertEqual(entry["fact"]["acceptanceNote"], "reviewed the sealed result")
            self.assertTrue(entry["fact"]["shutdownConfirmed"])
            self.assertEqual(entry["fact"]["attemptId"], entry["attemptId"])
            # Artifact provenance is frozen as an immutable reference and hash, with
            # the accepted binding and an explicit truncation signal.
            artifacts = entry["fact"]["artifacts"]
            self.assertEqual(
                set(artifacts),
                {"acceptedArtifactId", "acceptedAttemptId", "acceptedForThisAttempt", "artifacts", "total", "truncated"},
            )
            self.assertTrue(artifacts["artifacts"])
            self.assertEqual(artifacts["total"], len(artifacts["artifacts"]))
            self.assertFalse(artifacts["truncated"])
            self.assertTrue(artifacts["acceptedArtifactId"])
            self.assertEqual(artifacts["artifacts"][0]["artifactId"], artifacts["acceptedArtifactId"])
            self.assertTrue(all(set(item) == {"artifactId", "kind", "manifestSha256"} for item in artifacts["artifacts"]))
            # Bounded original scope and source Host, never a transcript.
            self.assertEqual(entry["fact"]["task"], "produce a model result")
            self.assertFalse(entry["fact"]["taskTruncated"])
            self.assertTrue(entry["fact"]["sourceHostId"])
        self.assertEqual({entry["profileId"] for entry in packet["evidence"]}, {PROFILE_ID, SECOND_PROFILE_ID})
        self.assertEqual(runs[first]["project"].endswith("repo-a"), True)
        self.assertEqual(runs[second]["project"].endswith("repo-b"), True)

        # No model call, no lease: the review collection records facts only.
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["tableRevision"], before["tableRevision"])
        self.assertEqual(snapshot["gate"], {"phase": "open", "readers": 0, "writer": None, "waitingWriters": 0})
        self.assertEqual(snapshot["decisions"], [])
        self.assertEqual(snapshot["capabilities"]["maintenance"], False)
        self.assertEqual(snapshot["tasks"]["total"], 2, "prepare must not create a task")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS c FROM evaluation_readers").fetchone()["c"], 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS c FROM evaluation_writers WHERE state IN ('waiting','active')"
                ).fetchone()["c"],
                0,
            )
        # Sample counts exist independently of any prose card: the aggregates cover
        # every discovered profile even though neither card has been rewritten yet.
        self.assertEqual({key: snapshot["sampleCounts"][key] for key in (PROFILE_ID, SECOND_PROFILE_ID)}, {PROFILE_ID: 1, SECOND_PROFILE_ID: 1})

        # A stable replay returns the recorded packet unchanged; a changed payload conflicts.
        self.assertEqual(self.prepare(board, "prep-1", limit=32), packet)
        self.assert_code("CONFLICT", board.evaluation.prepare, {"requestId": "prep-1", "limit": 8})

        # Repeat preparation with a new request records no duplicate fact or sample.
        repeated = self.prepare(board, "prep-2")
        self.assertEqual(repeated["newEvidenceIds"], [])
        self.assertEqual(repeated["progress"]["scanned"], 0)
        self.assertEqual(repeated["progress"]["alreadyPrepared"], 0)
        self.assertEqual(sorted(repeated["pendingEvidenceIds"]), sorted(packet["newEvidenceIds"]))
        # One durable cursor per assessed profile, not one per request scope.
        self.assertEqual(self.counts(board), {"evidence": 2, "samples": 2, "checkpoints": len(self.snapshot(board)["profiles"])})

    def test_attributes_the_frozen_attempt_configuration_not_a_later_choice(self):
        board = self.board()
        self.seed_profiles(board, PROFILE, SECOND_PROFILE)
        run_id = self.model_task(board, "goal-high", profile=SECOND_PROFILE)
        # A later projection can describe another configuration; the frozen turn input
        # of the attempt is what the fact is attributed to.
        with board.store.db.write() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            spec = json.loads(task["spec_json"])
            spec.update({"adapter": "dsh", "provider": PROFILE["provider"], "model": PROFILE["model"], "effort": PROFILE["effort"]})
            connection.execute("UPDATE tasks SET adapter='dsh', spec_json=? WHERE task_id=?", (json.dumps(spec), run_id))

        packet = self.prepare(board, "prep-frozen")
        self.assertEqual(len(packet["newEvidenceIds"]), 1)
        fact = packet["evidence"][0]["fact"]
        self.assertEqual(packet["evidence"][0]["profileId"], SECOND_PROFILE_ID)
        self.assertEqual(
            fact["requested"],
            {"adapter": "dsh", "provider": SECOND_PROFILE["provider"], "model": SECOND_PROFILE["model"], "effort": SECOND_PROFILE["effort"]},
        )
        samples = self.snapshot(board)["sampleCounts"]
        self.assertEqual(samples[SECOND_PROFILE_ID], 1)
        self.assertEqual(samples[PROFILE_ID], 0)

    def test_unreviewed_and_cancelled_runs_are_never_collected(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        self.model_task(board, "goal-unreviewed", verdict=None)
        cancelled = self.cancelled_task(board, "goal-cancelled")

        packet = self.prepare(board, "prep-none")
        self.assertEqual(packet["newEvidenceIds"], [])
        self.assertEqual(packet["evidence"], [])
        self.assertEqual(packet["progress"]["scanned"], 0)
        self.assertEqual(packet["remaining"]["reviewedBacklog"], 0)
        self.assertEqual(packet["skipped"], [])
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 0)
        self.assertEqual(self.counts(board)["evidence"], 0)

        # The recorded-report path still refuses to count cancelled work as quality.
        report = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "cancelled", "source": "cli", "runId": cancelled},
        )
        self.assertFalse(report["counted"])
        self.assertIn("cancel", report["unverifiedReason"])

    def test_reviewed_rejection_counts_but_an_infrastructure_failure_does_not(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        accepted = self.model_task(board, "goal-accepted")
        rejected = self.model_task(board, "goal-rejected", verdict="rejected")
        infrastructure = self.model_task(board, "goal-infra")
        # A completed attempt whose durable result names the environment, not the model.
        with board.store.db.write() as connection:
            row = connection.execute("SELECT result_json FROM attempts WHERE task_id=?", (infrastructure,)).fetchone()
            result = json.loads(row["result_json"])
            result["error"] = "worker failure: the supervisor lost the handle"
            connection.execute("UPDATE attempts SET result_json=? WHERE task_id=?", (json.dumps(result), infrastructure))

        packet = self.prepare(board, "prep-verdicts")
        facts = {entry["runId"]: entry for entry in packet["evidence"]}
        self.assertEqual(set(facts), {accepted, rejected, infrastructure})
        self.assertTrue(facts[accepted]["counted"])
        self.assertEqual(facts[accepted]["kind"], "task-success")
        self.assertTrue(facts[rejected]["counted"])
        self.assertEqual(facts[rejected]["kind"], "task-failure")
        self.assertEqual(facts[rejected]["identityBasis"]["source"], "host-rejection")
        self.assertTrue(facts[infrastructure]["verified"])
        self.assertFalse(facts[infrastructure]["counted"])
        self.assertIn("infrastructure", facts[infrastructure]["unverifiedReason"])
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 2)
        self.assertEqual(self.counts(board)["samples"], 2)

    def test_prepare_is_bounded_and_incremental_over_existing_accepted_history(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        runs = [self.model_task(board, f"goal-{index}") for index in range(5)]

        first = self.prepare(board, "page-1", limit=2)
        self.assertEqual(len(first["newEvidenceIds"]), 2)
        self.assertEqual(first["progress"]["scanned"], 2)
        self.assertEqual(first["remaining"]["reviewedBacklog"], 3)
        self.assertFalse(first["progress"]["complete"])
        second = self.prepare(board, "page-2", limit=2)
        self.assertEqual(len(second["newEvidenceIds"]), 2)
        self.assertEqual(second["remaining"]["reviewedBacklog"], 1)
        third = self.prepare(board, "page-3", limit=2)
        self.assertEqual(len(third["newEvidenceIds"]), 1)
        self.assertEqual(third["remaining"]["reviewedBacklog"], 0)
        self.assertTrue(third["progress"]["complete"])
        collected = set(first["newEvidenceIds"]) | set(second["newEvidenceIds"]) | set(third["newEvidenceIds"])
        self.assertEqual(len(collected), 5)
        # Existing accepted records are reachable on the first maintenance call, and
        # every one of them is counted exactly once.
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 5)
        self.assertEqual(self.counts(board), {"evidence": 5, "samples": 5, "checkpoints": len(self.snapshot(board)["profiles"])})
        with board.store.db.read() as connection:
            covered = {
                row["run_id"]
                for row in connection.execute("SELECT run_id FROM evaluation_evidence WHERE source='host-review'")
            }
        self.assertEqual(covered, set(runs))
        # A drained scope scans nothing and re-records nothing.
        drained = self.prepare(board, "page-4")
        self.assertEqual(drained["newEvidenceIds"], [])
        self.assertEqual(drained["progress"]["scanned"], 0)
        self.assertEqual(self.counts(board)["evidence"], 5)

    def test_an_oversized_packet_is_refused_without_consuming_progress(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        self.model_task(board, "goal-bound")
        with patch.object(evaluation_module, "MAX_PACKET_EVIDENCE", 0):
            error = self.assert_code("PACKET_TOO_LARGE", board.evaluation.prepare, {"requestId": "bound-1"})
        self.assertIn("profileId", error.message)
        # The refusal rolled the whole preparation back: no fact, no checkpoint.
        self.assertEqual(self.counts(board), {"evidence": 0, "samples": 0, "checkpoints": 0})
        recovered = self.prepare(board, "bound-2")
        self.assertEqual(len(recovered["newEvidenceIds"]), 1)
        self.assertEqual(self.counts(board), {"evidence": 1, "samples": 1, "checkpoints": len(self.snapshot(board)["profiles"])})


class PrepareFilterTests(ExternalEvaluationTestCase):
    def test_filters_address_profiles_and_adapters_only(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.model_task(board, "goal-flash", host_id="host-a", cwd=self.workdir("repo-a"), profile=PROFILE)
        self.model_task(board, "goal-pro", host_id="host-b", cwd=self.workdir("repo-b"), profile=SECOND_PROFILE)

        unfiltered = self.prepare(board, "all-1")
        self.assertEqual(len(unfiltered["newEvidenceIds"]), 2)
        self.assertEqual(self.counts(board)["checkpoints"], len(self.snapshot(board)["profiles"]))

        # Progress is per profile, so a filtered request for an already-current
        # profile scans nothing and rewrites nothing.
        filtered = self.prepare(board, "pro-1", profileId=SECOND_PROFILE_ID)
        self.assertEqual([profile["profileId"] for profile in filtered["profiles"]], [SECOND_PROFILE_ID])
        self.assertEqual(filtered["newEvidenceIds"], [])
        self.assertEqual(filtered["progress"]["scope"], f"profile:{SECOND_PROFILE_ID}")
        self.assertEqual(filtered["progress"]["scanned"], 0)
        self.assertEqual(filtered["skipped"], [])
        self.assertEqual(sorted(filtered["progress"]["cursors"]), [SECOND_PROFILE_ID])
        # The source Host and the source project are provenance, never filters.
        self.assertNotIn("host", filtered["progress"]["scope"])
        self.assertEqual(self.counts(board)["checkpoints"], len(self.snapshot(board)["profiles"]))

        by_adapter = self.prepare(board, "dsh-1", adapter="dsh")
        self.assertEqual([profile["profileId"] for profile in by_adapter["profiles"] if profile["enabled"]], [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(by_adapter["newEvidenceIds"], [])
        self.assertEqual(by_adapter["skipped"], [])
        self.assertEqual(by_adapter["progress"]["scope"], "adapter:dsh")

        self.assert_code("NOT_FOUND", board.evaluation.prepare, {"requestId": "x", "profileId": "dsh:none:none:off"})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.prepare, {"requestId": "x", "adapter": "command"})
        self.assert_code(
            "INVALID_ARGUMENT",
            board.evaluation.prepare,
            {"requestId": "x", "profileId": SECOND_PROFILE_ID, "adapter": "zcode"},
        )
        self.assert_code("INVALID_ARGUMENT", board.evaluation.prepare, {"requestId": "x", "limit": 0})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.prepare, {"requestId": "x", "limit": 65})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.prepare, {"requestId": "x", "sourceHost": "host-a"})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.prepare, {"requestId": "bad id", "limit": 4})

    def test_a_newly_included_profile_backfills_through_the_same_request(self):
        """A profile omitted from the first assessment reaches older reviewed facts later."""
        board = self.board()
        self.seed_catalog(board)
        self.publish(
            board,
            request_id="seed-a",
            command_id="seed-a",
            profileSettings=[{"profileId": PROFILE_ID, "enabled": True}],
            configuration={"decisionProfileId": PROFILE_ID},
        )
        flash_one = self.model_task(board, "goal-flash-1", profile=PROFILE)
        pro_one = self.model_task(board, "goal-pro-1", profile=SECOND_PROFILE)
        flash_two = self.model_task(board, "goal-flash-2", profile=PROFILE)

        # A profile-scoped pass advances only Flash's assessment cursor.
        first = self.prepare(board, "backfill-1", profileId=PROFILE_ID)
        self.assertEqual(len(first["newEvidenceIds"]), 2)
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 2)
        self.assertEqual([entry["reason"] for entry in first["skipped"]], ["profile-not-assessed"])

        # Enabling the second profile and broadening the scope must reach its older
        # records in bounded batches without rescanning Flash's completed facts.
        self.publish(
            board,
            request_id="seed-b",
            command_id="seed-b",
            profileSettings=[{"profileId": SECOND_PROFILE_ID, "enabled": True}],
            configuration={"decisionProfileId": PROFILE_ID},
        )
        step = self.prepare(board, "backfill-2", limit=1, profileId=SECOND_PROFILE_ID)
        self.assertEqual(step["newEvidenceIds"], [])
        self.assertEqual(step["progress"]["scanned"], 1)
        self.assertEqual(step["progress"]["scope"], f"profile:{SECOND_PROFILE_ID}")
        self.assertEqual(step["remaining"]["reviewedBacklog"], 2)
        self.assertFalse(step["progress"]["complete"])
        step = self.prepare(board, "backfill-3", limit=1, profileId=SECOND_PROFILE_ID)
        self.assertEqual(len(step["newEvidenceIds"]), 1)
        self.assertEqual(step["evidence"][0]["profileId"], SECOND_PROFILE_ID)
        self.assertEqual(step["evidence"][0]["runId"], pro_one)
        self.assertEqual(step["progress"]["scope"], f"profile:{SECOND_PROFILE_ID}")
        step = self.prepare(board, "backfill-4", limit=1, profileId=SECOND_PROFILE_ID)
        self.assertEqual(step["newEvidenceIds"], [])
        self.assertEqual(step["progress"]["backfilling"], [])
        self.assertTrue(step["progress"]["complete"])
        self.assertEqual(step["remaining"]["reviewedBacklog"], 0)
        # Both profiles now hold exactly the facts of their own reviewed attempts, and
        # the earlier pass recorded nothing twice.
        samples = self.snapshot(board)["sampleCounts"]
        self.assertEqual(samples[PROFILE_ID], 2)
        self.assertEqual(samples[SECOND_PROFILE_ID], 1)
        self.assertEqual(self.counts(board), {"evidence": 3, "samples": 3, "checkpoints": 2})
        with board.store.db.read() as connection:
            covered = {
                row["run_id"]
                for row in connection.execute("SELECT run_id FROM evaluation_evidence WHERE source='host-review'")
            }
        self.assertEqual(covered, {flash_one, pro_one, flash_two})

    def test_a_late_review_after_a_clock_rollback_is_never_lost(self):
        """The review cursor is the immutable event sequence, never a wall clock."""
        clock = FakeClock("2026-05-01T00:00:00.000Z")
        board = self.board(clock=clock)
        self.seed_profiles(board, PROFILE)
        first = self.model_task(board, "goal-first")
        collected = self.prepare(board, "roll-1")
        self.assertEqual(len(collected["newEvidenceIds"]), 1)
        self.assertEqual(collected["evidence"][0]["runId"], first)
        cursor = collected["progress"]["cursors"][PROFILE_ID]["reviewSeq"]
        self.assertIsInstance(cursor, int)

        # The clock rolls back before the next review, so its recorded accepted_at is
        # older than the cursor's timestamp. The append-only event sequence is still
        # higher, and the next bounded call collects the review instead of skipping it.
        clock.advance(-3600)
        second = self.model_task(board, "goal-second")
        with board.store.db.read() as connection:
            rolled_back = connection.execute(
                "SELECT accepted_at FROM tasks WHERE task_id=?", (second,)
            ).fetchone()["accepted_at"]
            earlier = connection.execute(
                "SELECT accepted_at FROM tasks WHERE task_id=?", (first,)
            ).fetchone()["accepted_at"]
        self.assertLess(rolled_back, earlier)
        later = self.prepare(board, "roll-2")
        self.assertEqual(len(later["newEvidenceIds"]), 1)
        self.assertEqual(later["evidence"][0]["runId"], second)
        self.assertEqual(later["progress"]["scanned"], 1)
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 2)
        self.assertEqual(self.counts(board)["samples"], 2)

    def test_a_superseded_or_unproven_review_is_reported_not_collected(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        # A review that a later retry superseded: the attempt binding is no longer the
        # task's current one, so the old event is reported and never attributed.
        superseded = self.model_task(board, "goal-superseded")
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE tasks SET accepted_at=NULL, acceptance_note=NULL, acceptance_verdict=NULL WHERE task_id=?",
                (superseded,),
            )
        # A review whose immutable artifact proof is gone: the pinned manifest the
        # event named no longer exists, so no fact is invented from it.
        unproven = self.model_task(board, "goal-unproven")
        with board.store.db.read() as connection:
            review = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.acknowledged'",
                (unproven,),
            ).fetchone()
            artifact_id = json.loads(review["payload_json"])["artifactId"]
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,))

        packet = self.prepare(board, "proof-1")
        self.assertEqual(packet["newEvidenceIds"], [])
        self.assertEqual(packet["evidence"], [])
        reasons = {entry["reason"]: entry["count"] for entry in packet["skipped"]}
        self.assertEqual(reasons, {"superseded-review": 1, "missing-artifact-proof": 1})
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 0)
        self.assertEqual(self.counts(board)["evidence"], 0)

    def test_a_rejected_attempt_and_an_accepted_continuation_are_both_qualified(self):
        """A retry must not collapse real rejected work into two successes.

        The first governed attempt is rejected and the Host continues before any
        preparation. Both reviews are real: the archived rejection is collected from
        its matching ``task.review_archived`` record and the accepted second attempt
        from the current task binding, each with its own attempt-bound artifact proof.
        """
        board = self.board()
        self.seed_profiles(board, PROFILE)
        run_id = self.model_task(board, "goal-retry", verdict="rejected")
        with board.store.db.read() as connection:
            attempts = [
                row["attempt_id"]
                for row in connection.execute(
                    "SELECT attempt_id FROM attempts WHERE task_id=? ORDER BY generation", (run_id,)
                )
            ]
        rejected_attempt = attempts[0]
        self.continue_model_run(board, run_id, command_id="continue-after-rejection")
        self.model_attempt(board, run_id, "goal-retry-2", verdict="accepted")
        with board.store.db.read() as connection:
            attempts = [
                row["attempt_id"]
                for row in connection.execute(
                    "SELECT attempt_id FROM attempts WHERE task_id=? ORDER BY generation", (run_id,)
                )
            ]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], rejected_attempt)

        packet = self.prepare(board, "prep-retry")
        self.assertEqual(len(packet["newEvidenceIds"]), 2)
        facts = {entry["fact"]["attemptId"]: entry for entry in packet["evidence"]}
        self.assertEqual(set(facts), set(attempts))
        rejected, accepted = facts[attempts[0]], facts[attempts[1]]
        self.assertEqual((rejected["kind"], rejected["verified"], rejected["counted"]), ("task-failure", True, True))
        self.assertEqual((accepted["kind"], accepted["verified"], accepted["counted"]), ("task-success", True, True))
        # The archived rejection carries its own archived acceptance, never the newer
        # task binding, and its own attempt never claims the newer accepted artifact.
        self.assertEqual(rejected["fact"]["reviewBinding"], "archived")
        self.assertEqual(rejected["fact"]["acceptanceVerdict"], "rejected")
        self.assertEqual(rejected["fact"]["acceptanceNote"], "reviewed the sealed result")
        self.assertEqual(rejected["fact"]["attemptId"], rejected_attempt)
        self.assertEqual(rejected["identityBasis"]["source"], "host-rejection")
        artifacts = rejected["fact"]["artifacts"]
        self.assertIsNone(artifacts["acceptedArtifactId"])
        self.assertFalse(artifacts["acceptedForThisAttempt"])
        self.assertEqual(accepted["fact"]["reviewBinding"], "current")
        self.assertEqual(accepted["fact"]["artifacts"]["acceptedAttemptId"], attempts[1])
        self.assertTrue(accepted["fact"]["artifacts"]["acceptedArtifactId"])
        # Exactly one failure and one success, each counted once on this profile.
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 2)
        self.assertEqual(self.counts(board), {"evidence": 2, "samples": 2, "checkpoints": len(self.snapshot(board)["profiles"])})
        # The deterministic per-attempt/verdict identity keeps a repeat preparation
        # idempotent: no double sample on retry.
        repeated = self.prepare(board, "prep-retry-again")
        self.assertEqual(repeated["newEvidenceIds"], [])
        self.assertEqual(repeated["progress"]["scanned"], 0)
        self.assertEqual(self.counts(board), {"evidence": 2, "samples": 2, "checkpoints": len(self.snapshot(board)["profiles"])})

    def test_mismatched_or_missing_artifact_proof_is_skipped_not_collected(self):
        """An artifact id that merely exists never becomes an attempt-bound fact.

        One review names the run's pinned *input* manifest instead of its sealed
        output artifact (same run and attempt, wrong kind), and another run's sealed
        output hash no longer matches its pinned artifact. Both are reported, and no
        sample or number is borrowed from the task's current state.
        """
        board = self.board()
        self.seed_profiles(board, PROFILE)
        accepted = self.model_task(board, "goal-kind-mismatch")
        with board.store.db.write() as connection:
            review = connection.execute(
                "SELECT seq, payload_json FROM events WHERE task_id=? AND kind='workflow.acknowledged'",
                (accepted,),
            ).fetchone()
            artifact_id = json.loads(review["payload_json"])["artifactId"]
            # The artifact exists for the same run and attempt but is no longer a
            # sealed output, so it cannot prove the reviewed work.
            connection.execute("UPDATE workflow_artifacts SET kind='turn-input' WHERE artifact_id=?", (artifact_id,))

        tampered = self.model_task(board, "goal-hash-mismatch")
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE workflow_artifacts SET manifest_sha256=? WHERE run_id=? AND kind='output'",
                ("0" * 64, tampered),
            )

        packet = self.prepare(board, "prep-mismatch")
        self.assertEqual(packet["newEvidenceIds"], [])
        self.assertEqual(packet["evidence"], [])
        reasons = {entry["reason"]: entry["count"] for entry in packet["skipped"]}
        self.assertEqual(reasons, {"missing-artifact-proof": 2})
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 0)
        self.assertEqual(self.counts(board), {"evidence": 0, "samples": 0, "checkpoints": len(self.snapshot(board)["profiles"])})

    def test_an_unknown_identity_is_reported_as_skipped_not_invented(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        # A real reviewed task with no requested model identity is never guessed onto
        # a profile: it is reported as an explicit skipped reason.
        run_id = self.completed_task(board, "goal-command")
        board.client().acknowledge(runId=run_id, note="reviewed", verdict="accepted")
        packet = self.prepare(board, "prep-command")
        self.assertEqual(packet["newEvidenceIds"], [])
        self.assertEqual(len(packet["skipped"]), 1)
        self.assertEqual(packet["skipped"][0]["reason"], "identity-unknown")
        self.assertEqual(packet["skipped"][0]["count"], 1)
        self.assertEqual(packet["skipped"][0]["examples"], [run_id])
        self.assertEqual(self.snapshot(board)["sampleCounts"][PROFILE_ID], 0)


class ReviewLedgerPlanTests(ExternalEvaluationTestCase):
    def test_bounded_review_scan_reads_the_partial_review_index(self):
        """A bounded prepare must read the review index, not every unrelated event.

        ``events_review_seq_idx`` is a partial index over the literal review kinds.
        The range query must render the same literal predicate: with bound
        placeholders SQLite cannot prove the partial predicate and falls back to a
        rowid range that reads every unrelated event.
        """
        board = self.board()
        self.seed_profiles(board, PROFILE)
        self.model_task(board, "goal-plan")
        with board.store.db.write() as connection:
            for index in range(400):
                board.store._append_event(connection, "task.progress", payload={"index": index})
            query = evaluation_module.EvaluationStore._review_query(cursor=True)
            plan = " ".join(
                row["detail"]
                for row in connection.execute("EXPLAIN QUERY PLAN " + query, (0, 32)).fetchall()
            )
        self.assertIn("events_review_seq_idx", plan)
        self.assertNotIn("SCAN event", plan)
        # The indexed range still returns the one real review among the noise.
        packet = self.prepare(board, "plan-1")
        self.assertEqual(len(packet["newEvidenceIds"]), 1)
        self.assertEqual(packet["progress"]["scanned"], 1)
        self.assertEqual(packet["remaining"]["reviewedBacklog"], 0)


class EvaluationWiringTests(ExternalEvaluationTestCase):
    def test_named_operations_are_wired_and_the_console_exposes_only_the_read(self):
        from buddy.cli import METHODS
        from buddy.console import CONSOLE_OPERATIONS
        from buddy.service import CONTROL_OPERATIONS
        from buddy.transport import METHOD_MAP

        for operation in ("evaluation_prepare", "evaluation_history"):
            self.assertIn(operation, CONTROL_OPERATIONS)
        self.assertEqual(METHOD_MAP["evaluation-prepare"], ("control", "evaluation_prepare"))
        self.assertEqual(METHOD_MAP["evaluation-history"], ("control", "evaluation_history"))
        for method in ("evaluation-prepare", "evaluation-history"):
            self.assertIn(method, METHODS)
        # The removed internal maintenance call leaves no CLI name, method, contract
        # operation or console entry behind.
        self.assertNotIn("evaluation-maintain", METHOD_MAP)
        self.assertNotIn("evaluation-maintain", METHODS)
        self.assertNotIn("evaluation_maintain", CONTROL_OPERATIONS)
        # The browser reads the publication log but never prepares maintenance and
        # never starts a selection/evidence model path.
        self.assertIn("evaluation_history", CONSOLE_OPERATIONS)
        self.assertNotIn("evaluation_prepare", CONSOLE_OPERATIONS)
        for removed in (
            "evaluation_maintain",
            "evaluation_evidence_record",
            "selection_request",
            "evaluation_reader_begin",
            "evaluation_reader_release",
        ):
            self.assertNotIn(removed, CONSOLE_OPERATIONS)

    def test_prepare_and_history_are_reachable_as_guarded_named_operations(self):
        board = self.board()
        self.seed_profiles(board, PROFILE)
        run_id = self.model_task(board, "goal-wired")
        packet = board.call("evaluation_prepare", {"requestId": "wire-1", "limit": 4})
        self.assertEqual([entry["runId"] for entry in packet["evidence"]], [run_id])
        history = board.call("evaluation_history", {"limit": 5})
        self.assertGreaterEqual(history["total"], 1)
        # The same validated operations are guarded like every other named operation:
        # an unknown field is refused, never silently ignored.
        self.assert_code("INVALID_ARGUMENT", board.call, "evaluation_prepare", {"requestId": "wire-2", "scope": "all"})
        self.assert_code("INVALID_ARGUMENT", board.call, "evaluation_history", {"scope": "all"})
        # The console route exposes the read but has no removed maintenance or
        # preparation operation to call.
        listed = board.console.command("evaluation_history", {"limit": 5})
        self.assertEqual(listed["total"], history["total"])
        for removed in (
            "evaluation_maintain",
            "evaluation_prepare",
            "evaluation_evidence_record",
            "selection_request",
        ):
            self.assert_code("METHOD_NOT_FOUND", board.console.command, removed, {"requestId": "x"})


# ---------------------------------------------------------------------------
# Card-only maintenance publication
# ---------------------------------------------------------------------------
class MaintenancePatchTests(ExternalEvaluationTestCase):
    def test_a_patch_merges_cards_and_preserves_everything_else(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.model_task(board, "goal-1", profile=PROFILE)
        packet = self.prepare(board, "prep-patch")
        evidence_id = packet["newEvidenceIds"][0]

        published = self.maintenance_publish(
            board,
            packet,
            [
                {
                    "profileId": PROFILE_ID,
                    "summary": "synthesized from the reviewed fact",
                    "strengths": ["fast"],
                    "limitations": ["small"],
                    "risks": ["open risk"],
                    "evidenceIds": [evidence_id],
                }
            ],
        )
        self.assertEqual(published["revision"], packet["tableRevision"] + 1)
        self.assertEqual(published["counts"], {"cards": 1, "provided": ["cards"]})
        snapshot = self.snapshot(board)
        cards = {card["profileId"]: card for card in snapshot["cards"]}
        self.assertEqual(cards[PROFILE_ID]["summary"], "synthesized from the reviewed fact")
        self.assertEqual(cards[PROFILE_ID]["evidenceIds"], [evidence_id])
        # The rewritten card carries the code-owned count derived from the aggregate.
        self.assertEqual(cards[PROFILE_ID]["sampleCount"], 1)
        self.assertEqual(cards[SECOND_PROFILE_ID]["summary"], "second card")
        self.assertEqual(snapshot["preferences"], [{"profileId": SECOND_PROFILE_ID, "mode": "prefer", "reason": "cheap"}])
        self.assertEqual(snapshot["configuration"], {"revision": 1, "decisionProfileId": PROFILE_ID})
        self.assertEqual([profile["profileId"] for profile in snapshot["profiles"] if profile["enabled"]], [PROFILE_ID, SECOND_PROFILE_ID])
        # The reference consumer leaves the pending ledger, and the next packet is empty.
        self.assertEqual(snapshot["pendingEvidence"], 0)
        self.assertEqual(self.prepare(board, "prep-after")["pendingEvidenceIds"], [])
        history = self.history(board)["revisions"]
        self.assertEqual(history[0]["kind"], "maintenance")
        self.assertEqual(history[0]["counts"]["provided"], ["cards"])
        self.assertEqual(history[0]["counts"]["cards"], 1)

    def test_a_patch_cannot_touch_profiles_preferences_configuration_or_counters(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.model_task(board, "goal-other", profile=SECOND_PROFILE)
        packet = self.prepare(board, "prep-reject")
        other_evidence = packet["newEvidenceIds"][0]
        for name, payload in (
            ("profiles", [PROFILE]),
            ("preferences", [{"profileId": PROFILE_ID, "mode": "exclude", "reason": "nope"}]),
            ("configuration", {"decisionProfileId": None}),
        ):
            grant = board.call(
                "evaluation_write_begin",
                {"requestId": f"bad-{name}", "expectedRevision": packet["tableRevision"], "kind": "maintenance"},
            )
            error = self.assert_code(
                "INVALID_ARGUMENT", self.maintenance_publish_with, board, grant, f"bad-{name}-cmd", **{name: payload}
            )
            self.assertIn(name, error.message)
            self.abort(board, grant, command_id=f"bad-{name}-abort")
        self.assertEqual(self.snapshot(board)["tableRevision"], packet["tableRevision"])

        # A maintenance publication must provide the cards it is patching.
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "empty", "expectedRevision": packet["tableRevision"], "kind": "maintenance"},
        )
        error = self.assert_code("INVALID_ARGUMENT", self.maintenance_publish_with, board, grant, "empty-cmd")
        self.assertIn("cards", error.message)
        self.abort(board, grant, command_id="empty-abort")

        # A card that carries a derived counter is still an unknown field.
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "counter", "expectedRevision": packet["tableRevision"], "kind": "maintenance"},
        )
        error = self.assert_code(
            "INVALID_ARGUMENT",
            self.maintenance_publish_with,
            board,
            grant,
            "counter-cmd",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "fabricated",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [],
                    "sampleCount": 99,
                }
            ],
        )
        self.assertIn("sampleCount", error.message)
        self.abort(board, grant, command_id="counter-abort")
        # Evidence stays bound to its own profile: a patch for one profile can never
        # cite the real evidence of another.
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "foreign", "expectedRevision": packet["tableRevision"], "kind": "maintenance"},
        )
        error = self.assert_code(
            "CONFLICT",
            self.maintenance_publish_with,
            board,
            grant,
            "foreign-cmd",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "wrong profile reference",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [other_evidence],
                }
            ],
        )
        self.assertIn("another profile", error.message)
        self.abort(board, grant, command_id="foreign-abort")
        self.assertEqual(self.snapshot(board)["tableRevision"], packet["tableRevision"])

    def test_a_stale_revision_is_refused_and_re_preparing_recovers(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.model_task(board, "goal-stale", profile=PROFILE)
        packet = self.prepare(board, "prep-stale")
        # Another writer publishes first; the prepared revision is no longer current.
        self.publish(
            board,
            request_id="human-later",
            command_id="human-later",
            annotationChanges=[{"profileId": PROFILE_ID, "text": "human edit while maintenance prepared"}],
        )
        grant = board.call(
            "evaluation_write_begin",
            {"requestId": "stale-patch", "expectedRevision": packet["tableRevision"], "kind": "maintenance"},
        )
        error = self.assert_code(
            "REVISION_CONFLICT",
            self.maintenance_publish_with,
            board,
            grant,
            "stale-cmd",
            expected=packet["tableRevision"],
            cards=[{"profileId": PROFILE_ID, "summary": "stale patch", "strengths": [], "limitations": [], "risks": [], "evidenceIds": []}],
        )
        self.assertEqual(error.details["currentRevision"], packet["tableRevision"] + 1)
        # Recovery: re-prepare at the new revision and publish with the same intent.
        fresh = self.prepare(board, "prep-stale-2")
        self.assertEqual(fresh["tableRevision"], packet["tableRevision"] + 1)
        recovered = self.maintenance_publish_with(
            board,
            grant,
            "stale-cmd-2",
            expected=fresh["tableRevision"],
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "recovered patch",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": fresh["newEvidenceIds"],
                }
            ],
        )
        self.assertEqual(recovered["revision"], fresh["tableRevision"] + 1)
        self.assertEqual(self.snapshot(board)["cards"][0]["summary"], "recovered patch")

    def test_retired_references_stay_compacted_and_archived(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.model_task(board, "goal-compact", profile=PROFILE)
        packet = self.prepare(board, "prep-compact")
        evidence_id = packet["newEvidenceIds"][0]
        self.maintenance_publish(
            board,
            packet,
            [{"profileId": PROFILE_ID, "summary": "cites the fact", "strengths": [], "limitations": [], "risks": [], "evidenceIds": [evidence_id]}],
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 0)
        # A later patch compacts the reference away; it must not become pending again.
        second = self.prepare(board, "prep-compact-2")
        self.maintenance_publish(
            board,
            second,
            [
                {
                    "profileId": PROFILE_ID,
                    "summary": "compacted the reference",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [],
                }
            ],
            request_id="patch-compact-2",
            command_id="patch-compact-2",
        )
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["pendingEvidence"], 0)
        self.assertEqual(snapshot["cards"][0]["evidenceIds"], [])
        with board.store.db.read() as connection:
            history = board.evaluation.card_history(connection, PROFILE_ID)
        archived = {entry["tableRevision"]: entry for entry in history}
        self.assertIn(evidence_id, archived[packet["tableRevision"] + 1]["evidenceIds"])


# ---------------------------------------------------------------------------
# Publication history
# ---------------------------------------------------------------------------
class EvaluationHistoryTests(ExternalEvaluationTestCase):
    def test_history_is_an_exact_bounded_read(self):
        board = self.board()
        self.seed_maintenance_board(board)
        self.publish(
            board,
            request_id="human-2",
            command_id="human-2",
            annotationChanges=[{"profileId": PROFILE_ID, "text": "second human revision"}],
        )
        before = self.snapshot(board)
        page = self.history(board, limit=1)
        self.assertEqual(set(page), {"revisions", "nextCursor", "total"})
        self.assertEqual(page["total"], 4)
        self.assertEqual(page["nextCursor"], 4)
        self.assertEqual(set(page["revisions"][0]), {"revision", "kind", "actor", "counts", "createdAt"})
        self.assertEqual(page["revisions"][0]["revision"], 4)
        self.assertEqual(page["revisions"][0]["kind"], "human")
        self.assertIsInstance(page["revisions"][0]["actor"], str)
        self.assertEqual(page["revisions"][0]["counts"]["annotationChanges"], 1)
        self.assertEqual(page["revisions"][0]["counts"]["provided"], ["annotationChanges"])
        older = self.history(board, limit=1, before=page["nextCursor"])
        self.assertEqual(older["revisions"][0]["revision"], 3)
        self.assertEqual(older["revisions"][0]["counts"]["cards"], 2)
        self.assertEqual(older["revisions"][0]["kind"], "maintenance")
        self.assertIsNotNone(older["nextCursor"])
        self.assertEqual(older["total"], 4)
        # A read: no writer, no reader, no new revision.
        after = self.snapshot(board)
        self.assertEqual(after["tableRevision"], before["tableRevision"])
        self.assertEqual(after["gate"], before["gate"])
        self.assert_code("INVALID_ARGUMENT", board.evaluation.history, {"limit": 101})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.history, {"limit": 0})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.history, {"before": 0})
        self.assert_code("INVALID_ARGUMENT", board.evaluation.history, {"scope": "all"})

    def test_history_pages_stay_stable_while_new_revisions_arrive(self):
        board = self.board()
        self.seed_maintenance_board(board)
        for index in range(2, 6):
            self.publish(
                board,
                request_id=f"human-{index}",
                command_id=f"human-{index}",
                annotationChanges=[{"profileId": PROFILE_ID, "text": f"revision {index}"}],
            )
        first = self.history(board, limit=2)
        self.assertEqual([entry["revision"] for entry in first["revisions"]], [7, 6])
        cursor = first["nextCursor"]
        self.assertEqual(cursor, 6)
        # A newer publication appears on page one only; the cursor page is unchanged.
        self.publish(
            board,
            request_id="human-9",
            command_id="human-9",
            annotationChanges=[{"profileId": PROFILE_ID, "text": "newest"}],
        )
        older = self.history(board, limit=2, before=cursor)
        self.assertEqual([entry["revision"] for entry in older["revisions"]], [5, 4])
        self.assertEqual(older["nextCursor"], 4)
        newest = self.history(board, limit=2)
        self.assertEqual([entry["revision"] for entry in newest["revisions"]], [8, 7])
        self.assertEqual(newest["total"], 8)


if __name__ == "__main__":
    unittest.main()
