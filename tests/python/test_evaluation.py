"""Shared evaluation table, writer gate, evidence ledger and catalog discovery.

These tests exercise the production store and the production resource
implementations in-process against private state directories; only the C-Two
transport is skipped.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase, FIXTURE_CATALOG, FakeClock

from buddy.errors import BoardError
from buddy.db import sha256_text

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
    "capabilities": ["execution:dsh", "effort:off"],
    "contextWindow": 1000000,
    "description": "fixture profile",
    "source": "manual",
}
SECOND_PROFILE_ID = "dsh:deepseek-official:deepseek-v4-pro:high"
SECOND_PROFILE = {
    **PROFILE,
    "profileId": SECOND_PROFILE_ID,
    "label": "DeepSeek-V4-Pro · high",
    "model": "deepseek-v4-pro",
    "effort": "high",
}
CARD = {"profileId": PROFILE_ID, "summary": "fixture assessment", "strengths": [], "limitations": [], "risks": [], "evidenceIds": []}
SECOND_CARD = {**CARD, "profileId": SECOND_PROFILE_ID}


def family_key(profile: dict) -> dict:
    """The ``(adapter, provider, model)`` key a family-keyed row or patch uses."""
    return {key: profile[key] for key in ("adapter", "provider", "model")}


class EvaluationTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    # -- helpers -------------------------------------------------------------
    def assert_code(self, code: str, callable_, *args, **kwargs) -> BoardError:
        with self.assertRaises(BoardError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    def snapshot(self, board) -> dict:
        return board.call("console_snapshot", {})

    def seed_catalog(self, board) -> dict:
        return board.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def begin(self, board, request_id: str = "write-1", expected: int | None = None, kind: str = "human") -> dict:
        if expected is None:
            expected = self.snapshot(board)["tableRevision"]
        call = board.console_call if kind == "human" else board.call
        return call(
            "evaluation_write_begin",
            {"requestId": request_id, "expectedRevision": expected, "kind": kind},
        )

    def publish_with(self, board, grant: dict, command_id: str, *, expected: int | None = None, **collections) -> dict:
        return board.console_call(
            "user_policy_publish",
            {
                "commandId": command_id,
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"] if expected is None else expected,
                **collections,
            },
        )

    def publish(self, board, *, request_id: str = "write-1", command_id: str = "cmd-1", expected: int | None = None, **collections) -> dict:
        grant = self.begin(board, request_id=request_id, expected=expected)
        return self.publish_with(board, grant, command_id, **collections)

    def seed_profiles(self, board, *profiles) -> dict:
        self.seed_catalog(board)
        selected = profiles or (PROFILE,)
        return self.publish(board, profileSettings=[{"profileId": item["profileId"], "enabled": True} for item in selected])

    def publish_cards(self, board, *, request_id: str, command_id: str, cards: list[dict]) -> dict:
        grant = self.begin(board, request_id=request_id, kind="maintenance")
        return board.call("assessment_publish", {
            "commandId": command_id, "writerId": grant["writerId"],
            "generation": grant["generation"], "writerToken": grant["writerToken"],
            "expectedRevision": grant["tableRevision"], "cards": cards,
        })

    def refused_cards(self, board, code: str, *, request_id: str, command_id: str, cards: list[dict]) -> BoardError:
        grant = self.begin(board, request_id=request_id, kind="maintenance")
        payload = {"commandId": command_id, "writerId": grant["writerId"],
                   "generation": grant["generation"], "writerToken": grant["writerToken"],
                   "expectedRevision": grant["tableRevision"], "cards": cards}
        error = self.assert_code(code, board.call, "assessment_publish", payload)
        board.call("evaluation_write_abort", {"commandId": f"abort-{command_id}",
                    "writerId": grant["writerId"], "generation": grant["generation"],
                    "writerToken": grant["writerToken"]})
        return error

    def abort(self, board, grant: dict, command_id: str = "abort-1") -> dict:
        return board.console_call(
            "evaluation_write_abort",
            {
                "commandId": command_id,
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
            },
        )

    def refused_publish(self, board, code: str, *, request_id: str = "refused", command_id: str = "refused", **collections) -> BoardError:
        """A rejected draft keeps its grant; release it so the next writer is not queued."""
        grant = self.begin(board, request_id=request_id)
        error = self.assert_code(code, self.publish_with, board, grant, command_id, **collections)
        self.abort(board, grant, command_id=f"abort-{command_id}")
        return error

    def completed_task(self, board, request_id: str = "task-1", *, ok: bool = True) -> str:
        """Run one real command-adapter task to a committed, shutdown-confirmed result.

        A command task records no model identity, so it is real work but never a
        verified model-performance sample. The identity-bearing fixture is
        :meth:`modelled_task`.
        """
        submitted = board.call(
            "task_submit",
            {
                "requestId": request_id,
                "task": "produce a result",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "hello"],
            },
        )
        run_id = submitted["task"]["runId"]
        client = board.client()
        client.register_worker("worker-1", adapter="command", capabilities=["command"])
        claim = client.claim("worker-1", f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        client.submit_result(
            "worker-1",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {
                "status": "ok" if ok else "failed",
                "result": {"finalText": "hello"},
                "shutdownConfirmed": True,
                "exitCode": 0 if ok else 1,
            },
        )
        return run_id

    def model_claim(self, board, request_id: str, identity: dict):
        """Admit a governed model task; the fixture owns only process/model output."""
        from unittest.mock import patch
        from buddy import workflow
        from mock_workspace import MockWorkspace

        if not hasattr(self, "model_workspace"):
            self.model_workspace = MockWorkspace(self.directory)
            self.model_controls = {}
            self.enterContext(patch.object(workflow, "_workspace_module", self.model_workspace))
        configuration = {key: identity[key] for key in ("adapter", "provider", "model", "effort")}
        submitted = board.call("workflow_submit", {
            "requestId": request_id, "hostId": "evaluation-host", "task": "produce a model result",
            "cwd": str(self.workdir(request_id)), **configuration,
            "executionWorkspace": {"kind": "existing", "access": "write"},
        })
        run_id = submitted["runId"]
        self.model_controls[run_id] = submitted["control"]
        client = board.client()
        worker_id = f"worker-{request_id}"
        client.register_worker(worker_id, adapter=identity["adapter"], capabilities=[identity["adapter"]])
        claim = client.claim(worker_id, f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        return run_id, worker_id, claim

    def review_modelled_task(self, board, run_id: str, verdict: str = "accepted"):
        view = board.call("workflow_get", {"runId": run_id})
        if verdict == "accepted":
            # Acceptance carries its own explicit not-required integration decision
            # bound to the exact final artifact.
            return board.call("workflow_accept", {
                "runId": run_id, "artifactId": view["finalArtifactId"], "note": "reviewed the sealed result",
                "notRequired": "evaluation fixture output is reviewed without a separate repository target",
                **self.model_controls[run_id],
            })
        # A rejected delivered outcome is a continuation that records the negative
        # review; the evidence derivation reads it like any other Host rejection.
        return board.call("workflow_continue", {
            "runId": run_id, "commandId": f"continue-{run_id}", "expectedRevision": view["revision"],
            "input": "address the review", "reason": "not accepted", **self.model_controls[run_id],
        })

    def modelled_task(self, board, request_id: str = "task-m1", *, verdict: str | None = "accepted",
                      failed: bool = False, profile: dict | None = None) -> str:
        """One governed DSH attempt, with real admission/receipt/acceptance rules."""
        identity = profile or PROFILE
        run_id, worker_id, claim = self.model_claim(board, request_id, identity)
        turn, attempt = claim["turn"], claim["attempt"]
        record = {
            "version": 1, "taskId": run_id, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "turnId": turn["turnId"], "resumeMode": turn["resumeMode"], "previousSessionId": None,
            "sessionId": f"fixture-{request_id}", "inputSha256": turn["inputSha256"], "promptSha256": sha256_text("evaluation fixture"),
            "outcome": {"disposition": "completed", "summary": "model output", "remaining": [],
                        "decisions": [], "artifacts": [], "request": None},
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited", "rootSessionMatched": True},
        }
        board.client().submit_result(worker_id, attempt["attemptId"], attempt["generation"], "n" * 32, {
            "status": "failed" if failed else "ok",
            "result": {
                "status": "failed" if failed else "ok", "requested": {
                    "provider": identity["provider"], "model": identity["model"], "effort": identity["effort"],
                },
                "turn": record,
                "workspaceSeal": self.model_workspace.seal(self.directory, turn["input"]["executionWorkspace"],
                                                           run_id, attempt["attemptId"]),
                "error": "the model produced unusable output" if failed else None,
                "finalText": "" if failed else "done",
            },
            "shutdownConfirmed": True, "exitCode": 1 if failed else 0,
        })
        # A failed unfinished goal has no final artifact to acknowledge. Its report
        # can be recorded, but cannot acquire acceptance through execution primitives.
        if verdict is not None and not failed:
            self.review_modelled_task(board, run_id, verdict)
        return run_id


# ---------------------------------------------------------------------------
# Published revisions and validation
# ---------------------------------------------------------------------------
class EvaluationPublishTests(EvaluationTestCase):
    def test_fresh_snapshot_is_unconfigured_without_a_verified_router(self):
        board = self.board()
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["tableRevision"], 0)
        self.assertEqual({key: snapshot["configuration"][key] for key in ("revision", "reviewRouterProfileId")}, {"revision": 0, "reviewRouterProfileId": None})
        self.assertEqual(snapshot["configuration"]["routingBudget"], "standard")
        self.assertEqual(snapshot["sampleCounts"], {})
        self.assertEqual(snapshot["profiles"], [])
        self.assertEqual(snapshot["familyPreferences"], [])
        self.assertEqual(snapshot["preferenceOverrides"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.assertEqual(snapshot["familyAnnotations"], [])
        self.assertEqual(snapshot["cards"], [])
        self.assertEqual(snapshot["evidence"], [])
        self.assertEqual(snapshot["decisions"], [])
        self.assertEqual(snapshot["pendingEvidence"], 0)
        self.assertEqual(snapshot["gate"], {"phase": "open", "readers": 0, "writer": None, "waitingWriters": 0})
        self.assertTrue(snapshot["capabilities"]["evaluationWriteGate"])
        # A no-tool executor can offer selection without a configured Router;
        # fresh settings remain empty until the user chooses each slot.
        self.assertIsInstance(snapshot["capabilities"]["selection"], bool)
        self.assertEqual(snapshot["configuration"]["defaultRoutingMode"], "fast")
        self.assertIsNone(snapshot["configuration"]["fastRouterProfileId"])
        self.assertFalse(snapshot["capabilities"]["maintenance"])
        self.assertIn("runs", snapshot["tasks"])

    def test_user_patches_preserve_omitted_fields_and_maintenance_patches_cards(self):
        board = self.board()
        catalog = self.seed_catalog(board)
        self.assertTrue(all(not item["enabled"] for item in catalog["profiles"]))
        first = self.publish(board, request_id="w1", command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}],
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}],
                             familyAnnotationChanges=[{**family_key(PROFILE), "text": "human context"}])
        self.assertEqual(first["revision"], catalog["tableRevision"] + 1)
        card = self.publish_cards(board, request_id="m1", command_id="m1", cards=[CARD])
        self.assertEqual(card["revision"], first["revision"] + 1)
        snapshot = self.snapshot(board)
        self.assertTrue(next(item for item in snapshot["profiles"] if item["profileId"] == PROFILE_ID)["enabled"])
        self.assertEqual(snapshot["preferences"][0]["reason"], "cheap and fast")
        self.assertEqual(snapshot["preferences"][0]["source"], "override")
        note = snapshot["familyAnnotations"][0]
        self.assertEqual((note["model"], note["text"]), (PROFILE["model"], "human context"))
        self.assertEqual(snapshot["cards"][0]["summary"], CARD["summary"])
        self.publish_cards(board, request_id="m2", command_id="m2", cards=[])
        self.assertEqual(self.snapshot(board)["cards"][0]["summary"], CARD["summary"], "an empty maintenance patch preserves prior cards")
        self.assertEqual(self.snapshot(board)["familyAnnotations"][0]["text"], "human context")

    def test_unknown_collection_fields_and_fabricated_counters_are_rejected(self):
        board = self.board()
        self.seed_profiles(board)
        error = self.refused_cards(board, "INVALID_ARGUMENT", request_id="m1", command_id="m1",
                                   cards=[{**CARD, "sampleCount": 99}])
        self.assertIn("sampleCount", error.message)
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w2", command_id="c2",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True, "available": True}])
        self.assertEqual(self.snapshot(board)["cards"], [])

    def test_profile_execution_identity_is_program_owned(self):
        board = self.board()
        self.seed_profiles(board)
        original = next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w2", command_id="c2",
                             profiles=[{**PROFILE, "model": "deepseek-v4-pro"}])
        current = next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)
        self.assertEqual(current["model"], original["model"])
        self.assertEqual(current["source"], original["source"])

    def test_dangling_references_are_refused_and_existing_intent_is_retained(self):
        from fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed_profiles(board, PROFILE, SECOND_PROFILE)
        self.refused_publish(board, "NOT_FOUND", request_id="w2", command_id="c2",
                             preferenceChanges=[{"profileId": "dsh:nope:nope:off", "mode": "prefer", "reason": "unknown"}])
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "pin", "reason": "user pin"}],
                     configuration={"reviewRouterProfileId": SECOND_PROFILE_ID})
        self.publish(board, request_id="w4", command_id="c4",
                     profileSettings=[{"profileId": SECOND_PROFILE_ID, "enabled": False}],
                     familyAnnotationChanges=[{**family_key(PROFILE), "text": "other edit"}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["preferences"][0]["mode"], "pin")
        self.assertEqual(snapshot["configuration"]["reviewRouterProfileId"], SECOND_PROFILE_ID)
        self.assertEqual(snapshot["familyAnnotations"][0]["text"], "other edit")

    def test_configuration_carries_router_profile_and_budget_without_maintenance(self):
        from fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed_profiles(board)
        published = self.publish(board, request_id="w2", command_id="c2",
                                 configuration={"reviewRouterProfileId": PROFILE_ID})
        self.assertEqual(self.snapshot(board)["configuration"]["reviewRouterProfileId"], PROFILE_ID)
        self.assertEqual(self.snapshot(board)["configuration"]["revision"], 1)
        self.assertEqual(self.snapshot(board)["configuration"]["routingBudget"], "standard")
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w3", command_id="c3",
                             configuration={"reviewRouterProfileId": PROFILE_ID, "autoMaintain": True})
        self.refused_publish(board, "CONFIGURATION_UNAVAILABLE", request_id="w4", command_id="c4",
                             configuration={"reviewRouterProfileId": "dsh:nope:nope:off"})
        self.assertEqual(self.snapshot(board)["tableRevision"], published["revision"])

    def test_availability_is_refreshed_by_catalog_and_new_enable_requires_it(self):
        board = self.board()
        self.refused_publish(board, "NOT_FOUND", request_id="w1", command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        refreshed = self.seed_catalog(board)
        self.assertEqual(self.snapshot(board)["tableRevision"], refreshed["tableRevision"])
        self.assertTrue(next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)["available"])
        self.refused_publish(board, "NOT_FOUND", request_id="w2", command_id="c2",
                             profileSettings=[{"profileId": "dsh:nope:nope:off", "enabled": True}])

    def test_pin_requires_an_available_enabled_profile(self):
        board = self.board()
        self.seed_catalog(board)
        self.refused_publish(board, "CONFIGURATION_UNAVAILABLE", request_id="w1", command_id="c1",
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "wanted"}])
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "wanted"}])
        self.assertEqual(self.snapshot(board)["preferences"][0]["mode"], "pin")

    def test_omitted_collections_and_explicit_null_are_distinct_commands(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="w1")
        base = {"commandId": "same-command", "writerId": grant["writerId"],
                "generation": grant["generation"], "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"]}
        payload = {**base, "familyAnnotationChanges": [{**family_key(PROFILE), "text": "first"}]}
        first = board.console_call("user_policy_publish", payload)
        self.assertTrue(board.console_call("user_policy_publish", payload)["duplicate"])
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "familyAnnotationChanges": []})
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "familyAnnotationChanges": None})
        other = self.begin(board, request_id="w2")
        self.assert_code("INVALID_ARGUMENT", self.publish_with, board, other, "fresh-command",
                         familyAnnotationChanges=None)
        self.assertEqual(self.snapshot(board)["tableRevision"], first["revision"])

    def test_expired_or_unknown_writer_cannot_publish(self):
        board = self.board()
        grant = self.begin(board, request_id="w1")
        base = {"commandId": "c1", "writerId": grant["writerId"], "generation": grant["generation"],
                "writerToken": grant["writerToken"], "expectedRevision": grant["tableRevision"],
                "familyAnnotationChanges": [{**family_key(PROFILE), "text": "x"}]}
        self.assert_code("UNAUTHORIZED", board.console_call, "user_policy_publish",
                         {**base, "writerToken": "0" * 64})
        self.assert_code("STALE_GENERATION", board.console_call, "user_policy_publish",
                         {**base, "generation": grant["generation"] + 1})
        self.assertEqual(self.snapshot(board)["tableRevision"], 0)

    def test_snapshot_never_leaks_credentials(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="w1")
        self.publish_with(board, grant, "c1",
                          familyAnnotationChanges=[{**family_key(PROFILE), "text": "visible opinion"}])
        raw = json.dumps(self.snapshot(board))
        self.assertNotIn(grant["writerToken"], raw)
        self.assertNotIn(board.service.token, raw)
        self.assertNotIn("writerToken", raw)
        self.assertNotIn("token_verifier", raw)

    def test_family_preference_covers_every_effort_and_a_none_override_wins(self):
        board = self.board()
        self.seed_catalog(board)
        published = self.publish(
            board, request_id="w1", command_id="c1",
            familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer", "reason": "cheap for the family"}],
        )
        self.assertEqual(published["counts"]["familyPreferenceChanges"], 1)
        snapshot = self.snapshot(board)
        self.assertEqual(
            snapshot["familyPreferences"],
            [{**family_key(PROFILE), "mode": "prefer", "reason": "cheap for the family"}],
        )
        self.assertEqual(snapshot["preferenceOverrides"], [])
        efforts = [item for item in snapshot["profiles"] if item["model"] == PROFILE["model"]]
        self.assertGreater(len(efforts), 1, "the family has several effort profiles")
        self.assertEqual(
            {entry["profileId"] for entry in snapshot["preferences"]},
            {item["profileId"] for item in efforts},
        )
        self.assertTrue(all(
            (entry["mode"], entry["reason"], entry["source"]) == ("prefer", "cheap for the family", "family")
            for entry in snapshot["preferences"]
        ))
        # One effort opts out explicitly; only that effort leaves the effective view.
        self.publish(board, request_id="w2", command_id="c2",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": "none", "reason": "this effort is not preferred"}])
        snapshot = self.snapshot(board)
        self.assertNotIn(PROFILE_ID, {entry["profileId"] for entry in snapshot["preferences"]})
        self.assertEqual(snapshot["preferenceOverrides"],
                         [{"profileId": PROFILE_ID, "mode": "none", "reason": "this effort is not preferred"}])
        self.assertTrue(all(entry["source"] == "family" for entry in snapshot["preferences"]))
        # Deleting the override restores the family default for that effort.
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": None}])
        snapshot = self.snapshot(board)
        restored = next(entry for entry in snapshot["preferences"] if entry["profileId"] == PROFILE_ID)
        self.assertEqual((restored["mode"], restored["reason"], restored["source"]),
                         ("prefer", "cheap for the family", "family"))
        self.assertEqual(snapshot["preferenceOverrides"], [])

    def test_family_preference_null_clears_and_bad_families_are_refused(self):
        board = self.board()
        self.seed_catalog(board)
        self.publish(board, request_id="w1", command_id="c1",
                     familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer"}])
        self.assertEqual(len(self.snapshot(board)["familyPreferences"]), 1)
        self.publish(board, request_id="w2", command_id="c2",
                     familyPreferenceChanges=[{**family_key(PROFILE), "mode": None}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["familyPreferences"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.refused_publish(board, "NOT_FOUND", request_id="w3", command_id="c3",
                             familyPreferenceChanges=[{**family_key(PROFILE), "model": "no-such-model",
                                                       "mode": "prefer"}])
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w4", command_id="c4",
                             familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer"},
                                                      {**family_key(PROFILE), "mode": "pin"}])
        self.assertEqual(self.snapshot(board)["familyPreferences"], [])

    def test_family_annotations_write_update_and_clear(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.publish(board, request_id="w1", command_id="c1",
                             familyAnnotationChanges=[{**family_key(PROFILE), "text": "first note"}])
        self.assertEqual(first["counts"]["familyAnnotationChanges"], 1)
        note = self.snapshot(board)["familyAnnotations"][0]
        self.assertEqual((note["adapter"], note["provider"], note["model"]),
                         (PROFILE["adapter"], PROFILE["provider"], PROFILE["model"]))
        self.assertEqual((note["text"], note["revision"]), ("first note", first["revision"]))
        self.assertTrue(note["updatedAt"])
        second = self.publish(board, request_id="w2", command_id="c2",
                              familyAnnotationChanges=[{**family_key(PROFILE), "text": "second note"}])
        updated = self.snapshot(board)["familyAnnotations"][0]
        self.assertEqual((updated["text"], updated["revision"]), ("second note", second["revision"]))
        self.publish(board, request_id="w3", command_id="c3",
                     familyAnnotationChanges=[{**family_key(PROFILE), "text": ""}])
        self.assertEqual(self.snapshot(board)["familyAnnotations"], [])


# ---------------------------------------------------------------------------
# Gate: fairness, draining, fencing
# ---------------------------------------------------------------------------
class EvaluationGateTests(EvaluationTestCase):
    def begin(self, board, request_id="write-1", expected=None, kind="maintenance"):
        return super().begin(board, request_id=request_id, expected=expected, kind=kind)

    def test_two_tabs_queue_fairly_and_serialize(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.begin(board, request_id="tab-a")
        second = self.begin(board, request_id="tab-b")
        self.assertEqual(first["state"], "active")
        self.assertEqual(second["state"], "waiting")
        self.assertEqual(second["phase"], "writing")
        self.assertEqual(second["waitingWriters"], 1)
        self.assertEqual(second["queuePosition"], 1)
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["gate"]["phase"], "writing")
        self.assertEqual(snapshot["gate"]["writer"]["writerId"], first["writerId"])
        self.assertEqual(snapshot["gate"]["waitingWriters"], 1)

        # A publish from the queued tab is refused: it does not hold authority yet.
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "assessment_publish",
            {
                "commandId": "c-b",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        first_result = board.call(
            "assessment_publish",
            {
                "commandId": "c-a",
                "writerId": first["writerId"],
                "generation": first["generation"],
                "writerToken": first["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(first_result["revision"], 2)
        # The head of the queue is promoted only after the previous grant released.
        renewed = board.call(
            "evaluation_write_renew",
            {"writerId": second["writerId"], "generation": second["generation"], "writerToken": second["writerToken"]},
        )
        self.assertEqual(renewed["state"], "active")
        self.assertEqual(renewed["phase"], "writing")
        # The queued tab must re-read the published revision: its stale expectation fails.
        self.assert_code(
            "REVISION_CONFLICT",
            board.call,
            "assessment_publish",
            {
                "commandId": "c-b2",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 1,
                "cards": [SECOND_CARD],
            },
        )
        second_result = board.call(
            "assessment_publish",
            {
                "commandId": "c-b3",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 2,
                "cards": [CARD, SECOND_CARD],
            },
        )
        self.assertEqual(second_result["revision"], 3)
        self.assertEqual(len(self.snapshot(board)["cards"]), 2)

    def test_begin_is_idempotent_by_request_identity(self):
        board = self.board()
        first = self.begin(board, request_id="same")
        again = self.begin(board, request_id="same")
        self.assertEqual(again["writerId"], first["writerId"])
        self.assertEqual(again["generation"], first["generation"])
        self.assertEqual(again["writerToken"], first["writerToken"])
        self.assertEqual(self.snapshot(board)["gate"]["waitingWriters"], 0)
        # The same requestId with a changed payload is a conflict, never a second intent.
        self.assert_code(
            "CONFLICT",
            board.console_call,
            "evaluation_write_begin",
            {"requestId": "same", "expectedRevision": 5, "kind": "human"},
        )
        self.assert_code(
            "CONFLICT",
            board.call,
            "evaluation_write_begin",
            {"requestId": "same", "expectedRevision": 5, "kind": "maintenance"},
        )

    def test_writer_drains_readers_and_new_readers_are_refused(self):
        board = self.board()
        self.seed_catalog(board)
        reader = board.call("evaluation_reader_begin", {"kind": "selection"})
        self.assertEqual(reader["phase"], "open")
        self.assertEqual(reader["readers"], 1)
        writer = self.begin(board, request_id="write-1")
        self.assertEqual(writer["state"], "waiting")
        self.assertEqual(writer["phase"], "draining")
        self.assertEqual(self.snapshot(board)["gate"]["phase"], "draining")
        self.assert_code("TABLE_BUSY", board.call, "evaluation_reader_begin", {"kind": "selection"})
        # Authority is not granted while an admitted reader is still settling.
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "assessment_publish",
            {
                "commandId": "c1",
                "writerId": writer["writerId"],
                "generation": writer["generation"],
                "writerToken": writer["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        released = board.call("evaluation_reader_release", {"readerId": reader["readerId"]})
        self.assertTrue(released["released"])
        self.assertEqual(released["phase"], "writing")
        self.assertEqual(self.snapshot(board)["gate"]["readers"], 0)
        self.assertEqual(self.snapshot(board)["gate"]["writer"]["writerId"], writer["writerId"])
        result = board.call(
            "assessment_publish",
            {
                "commandId": "c2",
                "writerId": writer["writerId"],
                "generation": writer["generation"],
                "writerToken": writer["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(result["revision"], 2)
        # Readers are admitted again once the gate is open, and idempotent release works.
        third = board.call("evaluation_reader_begin", {"kind": "selection", "revision": 2})
        self.assertEqual(third["phase"], "open")
        self.assertTrue(board.call("evaluation_reader_release", {"readerId": third["readerId"]})["released"])
        repeat = board.call("evaluation_reader_release", {"readerId": third["readerId"]})
        self.assertFalse(repeat["released"])
        self.assertTrue(repeat["alreadyReleased"])
        self.assert_code("REVISION_CONFLICT", board.call, "evaluation_reader_begin", {"kind": "selection", "revision": 1})
        self.assert_code("NOT_FOUND", board.call, "evaluation_reader_release", {"readerId": "no-such-reader"})

    def test_viewers_and_running_business_tasks_are_not_readers(self):
        board = self.board()
        self.seed_catalog(board)
        submitted = board.call(
            "task_submit",
            {
                "requestId": "long-running",
                "task": "keep working while the table changes",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "hi"],
            },
        )
        run_id = submitted["task"]["runId"]
        client = board.client()
        client.register_worker("worker-1", adapter="command", capabilities=["command"])
        client.claim("worker-1", "claim-1", "n" * 32, task_id=run_id)
        for _ in range(3):
            self.assertEqual(self.snapshot(board)["gate"]["readers"], 0)
        # A writer publishes immediately: an already-started business task never waits.
        grant = self.begin(board, request_id="write-1")
        self.assertEqual(grant["state"], "active")
        result = board.call(
            "assessment_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(result["revision"], 2)
        # The task keeps its accepted route and stays cancellable during the publish.
        self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["status"], "running")
        cancelled = board.call("task_cancel", {"runId": run_id, "reason": "operator"})
        self.assertEqual(cancelled["task"]["status"], "cancelling")

    def test_abort_fences_a_late_publication(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="write-1")
        aborted = board.call(
            "evaluation_write_abort",
            {
                "commandId": "abort-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
            },
        )
        self.assertTrue(aborted["aborted"])
        self.assertIn("not evidence", aborted["note"])
        error = self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "assessment_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(error.details.get("state"), "aborted")
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)
        # Retrying the same identity revives the same intent instead of duplicating it.
        revived = self.begin(board, request_id="write-1")
        self.assertEqual(revived["writerId"], grant["writerId"])
        self.assertEqual(revived["writerToken"], grant["writerToken"])
        self.assertEqual(revived["state"], "active")

    def test_duplicate_commands_replay_and_changed_payload_conflicts(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="write-1")
        payload = {
            "commandId": "same-command",
            "writerId": grant["writerId"],
            "generation": grant["generation"],
            "writerToken": grant["writerToken"],
            "expectedRevision": 1,
            "cards": [CARD],
        }
        first = board.call("assessment_publish", payload)
        replay = board.call("assessment_publish", payload)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["revision"], first["revision"])
        self.assertEqual(self.snapshot(board)["tableRevision"], 2)
        # The writer already published; beginning again with the same identity is a
        # terminal conflict rather than a silent second write.
        self.assert_code("ALREADY_PUBLISHED", board.call, "evaluation_write_begin", {"requestId": "write-1", "expectedRevision": 1})
        # The same commandId with a different payload is rejected by the receipt.
        other = self.begin(board, request_id="write-2", expected=2)
        self.assert_code(
            "CONFLICT",
            board.call,
            "assessment_publish",
            {**payload, "writerId": other["writerId"], "generation": other["generation"], "writerToken": other["writerToken"]},
        )

    def test_expired_grant_is_fenced_and_the_queue_advances(self):
        clock = FakeClock()
        board = self.board(clock=clock, writer_lease_seconds=10)
        self.seed_catalog(board)
        first = self.begin(board, request_id="w1")
        second = self.begin(board, request_id="w2")
        clock.advance(11)
        error = self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "assessment_publish",
            {
                "commandId": "c1",
                "writerId": first["writerId"],
                "generation": first["generation"],
                "writerToken": first["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(error.details.get("state"), "expired")
        # The next queued writer takes over; nothing claims the first process stopped.
        renewed = board.call(
            "evaluation_write_renew",
            {"writerId": second["writerId"], "generation": second["generation"], "writerToken": second["writerToken"]},
        )
        self.assertEqual(renewed["state"], "active")
        result = board.call(
            "assessment_publish",
            {
                "commandId": "c2",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(result["revision"], 2)
        # Renew of a terminal intent is refused rather than silently extending it.
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "evaluation_write_renew",
            {"writerId": second["writerId"], "generation": second["generation"], "writerToken": second["writerToken"]},
        )

    def test_waiting_writer_queue_deadline_is_bounded(self):
        clock = FakeClock()
        board = self.board(clock=clock, writer_queue_seconds=5, writer_lease_seconds=30)
        self.seed_catalog(board)
        reader = board.call("evaluation_reader_begin", {"kind": "selection"})
        writer = self.begin(board, request_id="w1")
        self.assertEqual(writer["state"], "waiting")
        clock.advance(6)
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            board.call,
            "evaluation_write_renew",
            {"writerId": writer["writerId"], "generation": writer["generation"], "writerToken": writer["writerToken"]},
        )
        board.call("evaluation_reader_release", {"readerId": reader["readerId"]})
        self.assertEqual(self.snapshot(board)["gate"]["phase"], "open")

    def test_gate_survives_a_service_restart_and_fences_the_old_token(self):
        clock = FakeClock()
        first = self.board(clock=clock, writer_lease_seconds=30)
        self.seed_catalog(first)
        grant = self.begin(first, request_id="w1")
        # A second service process over the same private directory sees the durable gate.
        second = self.board(clock=clock, writer_lease_seconds=30)
        snapshot = self.snapshot(second)
        self.assertEqual(snapshot["gate"]["writer"]["writerId"], grant["writerId"])
        self.assertEqual(snapshot["gate"]["phase"], "writing")
        result = second.call(
            "assessment_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 1,
                "cards": [CARD],
            },
        )
        self.assertEqual(result["revision"], 2)
        # After the restart a new writer queues, is aborted, and its late token stays fenced.
        stale = self.begin(second, request_id="w2", expected=2)
        second.call(
            "evaluation_write_abort",
            {"commandId": "abort-1", "writerId": stale["writerId"], "generation": stale["generation"], "writerToken": stale["writerToken"]},
        )
        third = self.board(clock=clock, writer_lease_seconds=30)
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            third.call,
            "assessment_publish",
            {
                "commandId": "c2",
                "writerId": stale["writerId"],
                "generation": stale["generation"],
                "writerToken": stale["writerToken"],
                "expectedRevision": 2,
                "cards": [SECOND_CARD],
            },
        )
        self.assertEqual(self.snapshot(third)["tableRevision"], 2)


    def test_snapshot_reports_an_expired_grant_without_any_write(self):
        clock = FakeClock()
        board = self.board(clock=clock, writer_lease_seconds=10)
        self.seed_catalog(board)
        self.begin(board, request_id="w1")
        self.assertEqual(self.snapshot(board)["gate"]["phase"], "writing")
        clock.advance(11)
        # A read-only view filters the expired lease instead of writing a sweep.
        self.assertEqual(
            self.snapshot(board)["gate"],
            {"phase": "open", "readers": 0, "writer": None, "waitingWriters": 0},
        )

    def test_ordinary_reads_never_discover_models(self):
        board = self.board()
        from buddy import catalog

        original = catalog.discover

        def forbidden():
            raise AssertionError("a plain snapshot must never run model discovery")

        catalog.discover = forbidden
        try:
            self.snapshot(board)
            board.call("evaluation_reader_begin", {"kind": "selection"})
            board.call("task_list", {"limit": 5})
        finally:
            catalog.discover = original


# ---------------------------------------------------------------------------
# Evidence ledger and cards
# ---------------------------------------------------------------------------
class EvaluationEvidenceTests(EvaluationTestCase):
    def seed(self, board, **collections):
        result = self.seed_profiles(board)
        if collections:
            self.publish_cards(board, request_id="seed-cards", command_id="seed-cards", cards=collections["cards"])
        return result

    def test_verified_task_success_counts_and_manual_reports_do_not(self):
        board = self.board()
        self.seed(board)
        run_id = self.modelled_task(board)
        verified = board.call(
            "evaluation_evidence_record",
            {
                "profileId": PROFILE_ID,
                "kind": "task-success",
                "summary": "finished the fixture task",
                "source": "cli",
                "runId": run_id,
                "project": "fixture-project",
                "conditions": ["command adapter", "small task"],
            },
        )
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["counted"])
        self.assertFalse(verified["duplicate"])
        self.assertEqual(verified["evidence"]["runId"], run_id)
        self.assertEqual(verified["evidence"]["project"], "fixture-project")
        self.assertTrue(verified["evidence"]["identityBasis"]["profileMatch"])
        self.assertEqual(verified["evidence"]["identityBasis"]["requested"]["model"], PROFILE["model"])
        self.assertIsNone(verified["evidence"]["identityBasis"]["observed"])
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 1)

        manual = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "manual", "summary": "operator opinion", "source": "console"},
        )
        self.assertFalse(manual["verified"])
        self.assertFalse(manual["counted"])
        self.assertIn("not counted", manual["unverifiedReason"])
        self.assertEqual(len(self.snapshot(board)["evidence"]), 2)

    def test_unreviewed_success_is_corroborated_but_never_a_sample(self):
        """Host review is the sample boundary: an unreviewed process proves nothing."""
        board = self.board()
        self.seed(board)
        run_id = self.modelled_task(board, request_id="task-unreviewed", verdict=None)
        report = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "process exited 0", "source": "cli", "runId": run_id},
        )
        self.assertTrue(report["verified"], "the committed result corroborates the report")
        self.assertFalse(report["counted"], "an unreviewed successful process is not task-success evidence")
        self.assertIn("Host acceptance", report["unverifiedReason"])
        self.assertEqual(self.snapshot(board)["cards"], [])
        # The verdict can arrive afterwards; re-deriving inside the acknowledgement
        # transaction turns exactly one attempt into exactly one sample.
        self.review_modelled_task(board, run_id)
        refreshed = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "process exited 0", "source": "cli", "runId": run_id},
        )
        self.assertTrue(refreshed["counted"])
        self.assertTrue(refreshed["duplicate"])
        card = self.publish_cards(
            board,
            request_id="w-card",
            command_id="c-card",
            cards=[{"profileId": PROFILE_ID, "summary": "reviewed once", "strengths": [], "limitations": [], "risks": [], "evidenceIds": [report["evidence"]["evidenceId"]]}],
        )
        self.assertEqual(card["revision"], 3)
        self.assertEqual(self.snapshot(board)["cards"][0]["sampleCount"], 1)

    def cancelled_task(self, board, request_id: str = "task-cancelled") -> str:
        """A governed DSH task cancelled through its Host owner."""
        run_id, worker_id, claim = self.model_claim(board, request_id, PROFILE)
        board.call("workflow_cancel", {"runId": run_id, "reason": "operator changed their mind",
                                        **self.model_controls[run_id]})
        board.client().submit_result(worker_id, claim["attempt"]["attemptId"], claim["attempt"]["generation"], "n" * 32, {
            "status": "cancelled", "result": {"status": "cancelled", "requested": {
                "provider": PROFILE["provider"], "model": PROFILE["model"], "effort": PROFILE["effort"],
            }}, "shutdownConfirmed": True, "exitCode": None,
        })
        return run_id

    def test_rejected_completed_result_is_a_capability_failure_and_counts_once(self):
        """A rejected completed result may be a capability failure even when exit is ok."""
        board = self.board()
        self.seed(board)
        run_id = self.modelled_task(board, request_id="task-rejected", verdict="rejected")
        first = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "the answer was wrong", "source": "cli", "runId": run_id},
        )
        self.assertTrue(first["verified"])
        self.assertTrue(first["counted"])
        self.assertEqual(first["evidence"]["identityBasis"]["source"], "host-rejection")
        # Repeated prose about the same attempt appends a report but not a sample.
        second = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "same attempt, different words", "source": "cli", "runId": run_id},
        )
        self.assertNotEqual(second["evidence"]["evidenceId"], first["evidence"]["evidenceId"])
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 2)
        with board.store.db.read() as connection:
            self.assertEqual(board.evaluation._sample_count(connection, PROFILE_ID), 1)
        # An unfinished failed goal has no final acceptance; retain it as an observation.
        failed_run = self.modelled_task(board, request_id="task-failed", verdict="rejected", failed=True)
        failed = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "the model failed outright", "source": "cli", "runId": failed_run},
        )
        self.assertFalse(failed["counted"])
        with board.store.db.read() as connection:
            self.assertEqual(board.evaluation._sample_count(connection, PROFILE_ID), 1)

    def test_cancelled_and_infrastructure_failures_are_not_capability_samples(self):
        board = self.board()
        self.seed(board)
        # A cancelled run, even when the Host rejects it, is not a model failure.
        cancelled = self.cancelled_task(board)
        cancelled_report = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "cancelled", "source": "cli", "runId": cancelled},
        )
        self.assertFalse(cancelled_report["counted"])
        self.assertIn("cancel", cancelled_report["unverifiedReason"])
        # An adapter/environment failure is real evidence but not a capability sample.
        client = board.client()
        unavailable = client.submit(
            requestId="task-unavailable",
            task="run something absent",
            cwd=str(self.workdir()),
            adapter="command",
            argv=["/nonexistent/binary-xyz"],
        )["task"]["runId"]
        client.register_worker("worker-unavailable", adapter="command", capabilities=["command"])
        claim = client.claim("worker-unavailable", "claim-unavailable", "n" * 32, task_id=unavailable)["claim"]
        client.submit_result(
            "worker-unavailable",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {"status": "failed", "result": None, "error": "ADAPTER_UNAVAILABLE: argv[0] is not executable", "shutdownConfirmed": True},
        )
        client.acknowledge(runId=unavailable, note="the executable was missing", verdict="rejected")
        infrastructure = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "missing executable", "source": "cli", "runId": unavailable},
        )
        self.assertTrue(infrastructure["verified"])
        self.assertFalse(infrastructure["counted"])
        self.assertIn("infrastructure", infrastructure["unverifiedReason"])

    def test_unknown_identity_stays_unverified(self):
        board = self.board()
        self.seed_profiles(board, PROFILE, SECOND_PROFILE)
        # The same completed command task cannot be a sample of a dsh profile.
        run_id = self.completed_task(board)
        board.client().acknowledge(runId=run_id, note="reviewed", verdict="accepted")
        unknown = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "no model identity", "source": "cli", "runId": run_id},
        )
        self.assertTrue(unknown["verified"])
        self.assertFalse(unknown["counted"])
        self.assertIn("identity", unknown["unverifiedReason"])
        # A run that requested another profile never becomes a sample of this one.
        other = self.modelled_task(board, request_id="task-other", profile=SECOND_PROFILE)
        mismatch = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "labelled as another model", "source": "cli", "runId": other},
        )
        self.assertTrue(mismatch["verified"])
        self.assertFalse(mismatch["counted"])
        self.assertFalse(mismatch["evidence"]["identityBasis"]["profileMatch"])
        self.assertIn(SECOND_PROFILE["model"], mismatch["unverifiedReason"])

    def test_claimed_success_without_a_real_result_is_stored_but_unverified(self):
        board = self.board()
        self.seed(board)
        submitted = board.call(
            "task_submit",
            {"requestId": "queued-only", "task": "not started", "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/echo", "x"]},
        )
        result = board.call(
            "evaluation_evidence_record",
            {
                "profileId": PROFILE_ID,
                "kind": "task-success",
                "summary": "worker says it succeeded",
                "source": "worker:external",
                "runId": submitted["task"]["runId"],
            },
        )
        self.assertFalse(result["verified"])
        self.assertFalse(result["counted"])
        self.assertIn("unverified", result["unverifiedReason"])
        # A failed execution never counts as a success, and a cancellation is not a sample.
        failed_run = self.completed_task(board, request_id="task-2", ok=False)
        failed = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "claimed win", "source": "cli", "runId": failed_run},
        )
        self.assertFalse(failed["counted"])
        cancellation = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-cancelled", "summary": "user cancelled", "source": "cli", "runId": failed_run},
        )
        self.assertFalse(cancellation["counted"])

    def test_evidence_is_deduplicated_append_only_and_profile_scoped(self):
        board = self.board()
        self.seed(board)
        run_id = self.completed_task(board)
        payload = {
            "profileId": PROFILE_ID,
            "kind": "task-success",
            "summary": "first report",
            "source": "cli",
            "runId": run_id,
        }
        first = board.call("evaluation_evidence_record", payload)
        duplicate = board.call("evaluation_evidence_record", {**payload, "commandId": "evidence-1"})
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["evidence"]["evidenceId"], first["evidence"]["evidenceId"])
        self.assertEqual(len(self.snapshot(board)["evidence"]), 1)
        # A different observation of the same run appends a second attributed record.
        board.call("evaluation_evidence_record", {**payload, "summary": "second report"})
        self.assertEqual(len(self.snapshot(board)["evidence"]), 2)
        # A task-linked kind must carry the run it is about.
        self.assert_code(
            "INVALID_ARGUMENT",
            board.call,
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "no run", "source": "cli"},
        )
        # An unknown profile is refused; evidence never dangles.
        self.assert_code(
            "NOT_FOUND",
            board.call,
            "evaluation_evidence_record",
            {"profileId": "dsh:none:none:off", "kind": "manual", "summary": "x", "source": "cli"},
        )
        # Evidence is allowed while a writer holds or waits for the table.
        writer = self.begin(board, request_id="w2", expected=1)
        board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "observation", "summary": "recorded during maintenance", "source": "cli"},
        )
        self.assertEqual(self.snapshot(board)["gate"]["writer"]["writerId"], writer["writerId"])


    def test_pending_evidence_tracks_incorporation_not_publication(self):
        from fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed(board)
        run_id = self.completed_task(board)
        evidence = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "counted", "source": "cli", "runId": run_id},
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 1)
        # A revision that only changes preferences never consumes unconsumed evidence.
        self.publish(
            board,
            request_id="w2",
            command_id="c2",
            preferenceChanges=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "still preferred"}],
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 1)
        # Only an actual card reference incorporates it.
        self.publish_cards(
            board,
            request_id="w3",
            command_id="c3",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "稳定",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [evidence["evidence"]["evidenceId"]],
                }
            ],
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 0)
        # New evidence is pending again, and another non-card revision does not hide it.
        second_run = self.completed_task(board, request_id="task-2")
        board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "second sample", "source": "cli", "runId": second_run},
        )
        self.publish(
            board,
            request_id="w4",
            command_id="c4",
            configuration={"reviewRouterProfileId": PROFILE_ID},
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 1)

    def test_card_counters_are_derived_and_a_profile_with_evidence_cannot_vanish(self):
        board = self.board()
        self.seed(board)
        run_id = self.modelled_task(board)
        evidence = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "counted", "source": "cli", "runId": run_id},
        )
        card = self.publish_cards(
            board,
            request_id="w2",
            command_id="c2",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "可靠",
                    "strengths": ["fast"],
                    "limitations": ["small context"],
                    "risks": ["fixture risk"],
                    "evidenceIds": [evidence["evidence"]["evidenceId"]],
                }
            ],
        )
        self.assertEqual(card["revision"], 3)
        published = self.snapshot(board)["cards"][0]
        self.assertEqual(published["sampleCount"], 1)
        self.assertEqual(published["revision"], 1)
        first_updated = published["updatedAt"]
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 0)
        # A card may not reference evidence owned by another profile.
        self.refused_cards(
            board,
            "CONFLICT",
            request_id="w4",
            command_id="c4",
            cards=[
                {
                    "profileId": SECOND_PROFILE_ID,
                    "summary": "borrowed evidence",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [evidence["evidence"]["evidenceId"]],
                }
            ],
        )
        # Re-publishing identical content keeps revision and timestamp; changed content bumps both.
        self.publish_cards(
            board,
            request_id="w5",
            command_id="c5",
            cards=[{"profileId": PROFILE_ID, "summary": "可靠", "strengths": ["fast"], "limitations": ["small context"], "risks": ["fixture risk"], "evidenceIds": [evidence["evidence"]["evidenceId"]]}],
        )
        unchanged = self.snapshot(board)["cards"][0]
        self.assertEqual(unchanged["revision"], 1)
        self.assertEqual(unchanged["updatedAt"], first_updated)
        self.publish_cards(
            board,
            request_id="w6",
            command_id="c6",
            cards=[{"profileId": PROFILE_ID, "summary": "可靠（更正）", "strengths": ["fast"], "limitations": ["small context"], "risks": ["fixture risk"], "evidenceIds": [evidence["evidence"]["evidenceId"]]}],
        )
        changed = self.snapshot(board)["cards"][0]
        self.assertEqual(changed["revision"], 2)
        self.assertNotEqual(changed["updatedAt"], first_updated)
        # Human publication cannot remove a discovered profile or its evidence.
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w7", command_id="c7", profiles=[SECOND_PROFILE])

    def test_catalog_refresh_is_explicit_attributed_and_failure_safe(self):
        board = self.board()
        refreshed = board.call("model_catalog_refresh", {"requestId": "cat-1"})
        self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
        self.assertEqual(refreshed["catalog"]["harnessVersion"], "test-harness-1")
        self.assertEqual(refreshed["catalog"]["providers"][0]["efforts"], ["off", "low", "high", "max"])
        proposals = refreshed["profiles"]
        self.assertTrue(proposals)
        self.assertTrue(all(item["enabled"] is False for item in proposals), "discovered profiles start disabled")
        self.assertTrue(all(item["available"] is True for item in proposals))
        self.assertIn("refreshed", refreshed["note"])
        self.assertEqual(self.snapshot(board)["tableRevision"], refreshed["tableRevision"])
        self.assertEqual(len(self.snapshot(board)["profiles"]), len(proposals))
        # Identical native metadata is recognized while a new observation still advances the table.
        repeated = board.call("model_catalog_refresh", {"requestId": "cat-2"})
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(self.snapshot(board)["tableRevision"], repeated["tableRevision"])
        # A failed discovery keeps the previous recorded catalog and the table.
        from buddy import catalog
        from buddy.errors import BoardError as _BoardError

        original = catalog.discover
        catalog.discover = lambda **_kwargs: (_ for _ in ()).throw(_BoardError("CATALOG_UNAVAILABLE", "no harness"))
        try:
            self.assert_code("CATALOG_UNAVAILABLE", board.call, "model_catalog_refresh", {"requestId": "cat-3"})
        finally:
            catalog.discover = original
        self.assertEqual(self.snapshot(board)["tableRevision"], repeated["tableRevision"])


class InstalledHarnessDiscoveryTests(BoardTestCase):
    """Read actual metadata from the installed model-discovery harnesses.

    Skipped when no harness is installed: this test never installs or downloads one,
    and it never runs a model.
    """

    def test_installed_harness_discovery_is_truthful_and_credential_free(self):
        from buddy import catalog
        from buddy.adapters import adapters

        if not catalog.discovery_available():
            self.skipTest("the discovery helper and Node.js are not available in this build")
        try:
            payload = catalog.discover()
        except BoardError as error:
            self.assertEqual(error.code, "CATALOG_UNAVAILABLE")
            self.skipTest(f"no installed harness catalog could be read: {error.message}")
        view = catalog.CatalogView.from_payload(payload)
        metadata = view.metadata()
        self.assertTrue(metadata["source"])
        self.assertTrue(metadata["providers"])
        for provider in metadata["providers"]:
            self.assertTrue(provider["provider"])
            self.assertTrue(provider["models"])
            self.assertTrue(adapters()[provider["adapter"]].model_discovery)
            for model in provider["models"]:
                self.assertTrue(model["id"])
        raw = json.dumps(payload)
        for forbidden in ("apiKeyEnv", "API_KEY", "Authorization", "Bearer ", "sk-"):
            self.assertNotIn(forbidden, raw)
        proposals = view.proposed_profiles()
        self.assertTrue(proposals)
        for proposal in proposals:
            self.assertFalse(proposal["enabled"], "discovered profiles start disabled")
            native = view.lookup(proposal["adapter"], proposal["provider"], proposal["model"])
            self.assertIsNotNone(native)
            self.assertEqual(proposal["available"], native["available"])
            self.assertIn(proposal["effort"], native["efforts"])

        # A real discovered profile can be enabled only through the authenticated user path.
        board = self.board()
        refreshed = board.call("model_catalog_refresh", {"requestId": "installed-catalog"})
        proposals = refreshed["profiles"]
        if not any(item["available"] for item in proposals):
            self.skipTest("the installed harness did not advertise an available model profile")
        selected = next(item for item in proposals if item["available"])
        grant = board.console_call(
            "evaluation_write_begin", {"requestId": "installed-1", "expectedRevision": refreshed["tableRevision"], "kind": "human"}
        )
        result = board.console_call(
            "user_policy_publish",
            {
                "commandId": "installed-publish",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                "profileSettings": [{"profileId": selected["profileId"], "enabled": True}],
            },
        )
        self.assertEqual(result["counts"]["profileSettings"], 1)
        page = board.call("model_profiles", {"limit": 200, "includeUnavailable": True})
        self.assertTrue(next(item for item in page["profiles"] if item["profileId"] == selected["profileId"])["enabled"])


if __name__ == "__main__":
    unittest.main()
