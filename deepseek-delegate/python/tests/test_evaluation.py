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
        return board.call(
            "evaluation_write_begin",
            {"requestId": request_id, "expectedRevision": expected, "kind": kind},
        )

    def publish_with(self, board, grant: dict, command_id: str, *, expected: int | None = None, **collections) -> dict:
        return board.call(
            "evaluation_write_publish",
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
        return self.publish(board, profiles=list(profiles or [PROFILE]))

    def abort(self, board, grant: dict, command_id: str = "abort-1") -> dict:
        return board.call(
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

    def modelled_task(
        self,
        board,
        request_id: str = "task-m1",
        *,
        verdict: str | None = "accepted",
        failed: bool = False,
        profile: dict | None = None,
    ) -> str:
        """One dsh-identity task whose reported request matches the fixture profile.

        The test acts as the worker and reports exactly what a real dsh run reports,
        including its requested configuration. ``verdict=None`` leaves the work
        unreviewed; a verdict records the Host's acceptance or rejection.
        """
        identity = profile or PROFILE
        client = board.client()
        submitted = client.submit(
            requestId=request_id,
            task="produce a model result",
            cwd=str(self.workdir()),
            model=identity["model"],
            provider=identity["provider"],
            effort=identity["effort"],
        )
        run_id = submitted["task"]["runId"]
        worker_id = f"worker-{request_id}"
        client.register_worker(worker_id, adapter="dsh", capabilities=["dsh"])
        claim = client.claim(worker_id, f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        client.submit_result(
            worker_id,
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {
                "status": "failed" if failed else "ok",
                "result": {
                    "status": "failed" if failed else "ok",
                    "mode": "run",
                    "requested": {
                        "provider": identity["provider"],
                        "model": identity["model"],
                        "reasoningEffort": identity["effort"],
                    },
                    "error": "the model produced unusable output" if failed else None,
                    "finalText": "" if failed else "done",
                },
                "shutdownConfirmed": True,
                "exitCode": 1 if failed else 0,
            },
        )
        if verdict is not None:
            client.acknowledge(note="reviewed the real result", verdict=verdict, runId=run_id)
        return run_id


# ---------------------------------------------------------------------------
# Published revisions and validation
# ---------------------------------------------------------------------------
class EvaluationPublishTests(EvaluationTestCase):
    def test_fresh_snapshot_is_unconfigured_with_available_decision_operations(self):
        board = self.board()
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["tableRevision"], 0)
        self.assertEqual(snapshot["configuration"], {"revision": 0, "decisionProfileId": None, "autoMaintain": False})
        self.assertEqual(snapshot["profiles"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.assertEqual(snapshot["cards"], [])
        self.assertEqual(snapshot["evidence"], [])
        self.assertEqual(snapshot["decisions"], [])
        self.assertEqual(snapshot["pendingEvidence"], 0)
        self.assertEqual(snapshot["gate"], {"phase": "open", "readers": 0, "writer": None, "waitingWriters": 0})
        self.assertTrue(snapshot["capabilities"]["selection"])
        self.assertTrue(snapshot["capabilities"]["maintenance"])
        self.assertTrue(snapshot["capabilities"]["evaluationWriteGate"])
        self.assertIn("runs", snapshot["tasks"])

    def test_publish_replaces_only_provided_collections(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.publish(
            board,
            request_id="w1",
            command_id="c1",
            profiles=[PROFILE, SECOND_PROFILE],
            preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}],
        )
        self.assertTrue(first["published"])
        self.assertEqual(first["revision"], 1)
        snapshot = self.snapshot(board)
        self.assertEqual([item["profileId"] for item in snapshot["profiles"]], [PROFILE_ID, SECOND_PROFILE_ID])
        self.assertEqual(snapshot["preferences"], [{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}])

        # Cards only: the profiles and preferences published above are preserved.
        second = self.publish(
            board,
            request_id="w2",
            command_id="c2",
            cards=[{"profileId": PROFILE_ID, "summary": "solid for routine work", "strengths": ["fast"], "limitations": [], "risks": [], "evidenceIds": []}],
        )
        self.assertEqual(second["revision"], 2)
        snapshot = self.snapshot(board)
        self.assertEqual(len(snapshot["profiles"]), 2)
        self.assertEqual(len(snapshot["preferences"]), 1)
        self.assertEqual(snapshot["cards"][0]["summary"], "solid for routine work")
        self.assertEqual(snapshot["cards"][0]["revision"], 1)
        self.assertIsNotNone(snapshot["cards"][0]["updatedAt"])

        # An explicit empty collection is intentional replacement, not omission.
        self.publish(board, request_id="w3", command_id="c3", cards=[])
        self.assertEqual(self.snapshot(board)["cards"], [])
        self.assertEqual(len(self.snapshot(board)["profiles"]), 2)

    def test_unknown_collection_fields_and_fabricated_counters_are_rejected(self):
        board = self.board()
        self.seed_profiles(board)
        error = self.assert_code(
            "INVALID_ARGUMENT",
            self.publish,
            board,
            request_id="w2",
            command_id="c2",
            cards=[
                {
                    "profileId": PROFILE_ID,
                    "summary": "looks good",
                    "strengths": [],
                    "limitations": [],
                    "risks": [],
                    "evidenceIds": [],
                    "sampleCount": 99,
                }
            ],
        )
        self.assertIn("sampleCount", error.message)
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)
        self.assertEqual(self.snapshot(board)["cards"], [])

    def test_profile_execution_identity_is_immutable(self):
        board = self.board()
        self.seed_profiles(board)
        error = self.assert_code(
            "CONFLICT",
            self.publish,
            board,
            request_id="w2",
            command_id="c2",
            profiles=[{**PROFILE, "model": "deepseek-v4-pro"}],
        )
        self.assertIn("immutable", error.message)
        self.assertEqual(self.snapshot(board)["profiles"][0]["model"], "deepseek-flash")

    def test_dangling_references_are_refused(self):
        board = self.board()
        self.seed_catalog(board)
        self.publish(
            board,
            request_id="w1",
            command_id="c1",
            profiles=[PROFILE, SECOND_PROFILE],
            preferences=[{"profileId": SECOND_PROFILE_ID, "mode": "pin", "reason": "user pinned"}],
            configuration={"decisionProfileId": SECOND_PROFILE_ID, "autoMaintain": False},
        )
        # Removing the pinned/decision profile while it is still referenced is refused.
        self.refused_publish(board, "CONFLICT", request_id="w2", command_id="c2", profiles=[PROFILE])
        # Replacing every reference in the same publish is allowed.
        result = self.publish(board, request_id="w3", command_id="c3", profiles=[PROFILE], preferences=[], configuration={"decisionProfileId": None, "autoMaintain": False})
        self.assertEqual(result["revision"], 2)
        self.assertEqual([item["profileId"] for item in self.snapshot(board)["profiles"]], [PROFILE_ID])
        # A preference for an unknown profile is refused.
        self.refused_publish(
            board,
            "CONFLICT",
            request_id="w4",
            command_id="c4",
            preferences=[{"profileId": "dsh:nope:nope:off", "mode": "prefer", "reason": "unknown"}],
        )
        # A decision profile that is not published is refused.
        self.refused_publish(
            board,
            "CONFLICT",
            request_id="w5",
            command_id="c5",
            configuration={"decisionProfileId": "dsh:nope:nope:off", "autoMaintain": False},
        )

    def test_configuration_accepts_auto_maintain_as_bounded_preauthorization(self):
        board = self.board()
        self.seed_profiles(board)
        published = self.publish(
            board,
            request_id="w2",
            command_id="c2",
            configuration={"decisionProfileId": PROFILE_ID, "autoMaintain": True},
        )
        self.assertEqual(published["revision"], 2)
        self.assertEqual(
            self.snapshot(board)["configuration"],
            {"revision": 1, "decisionProfileId": PROFILE_ID, "autoMaintain": True},
        )
        # The preauthorization is still bounded to the published table: an unknown
        # decision profile is refused exactly as before.
        self.refused_publish(
            board,
            "CONFLICT",
            request_id="w3",
            command_id="c3",
            configuration={"decisionProfileId": "dsh:nope:nope:off", "autoMaintain": True},
        )

    def test_availability_must_be_backed_by_the_discovered_catalog(self):
        board = self.board()
        # Without any recorded discovery an availability claim is refused; the rejected
        # draft costs nothing and the same writer intent can retry with a fixed payload.
        grant = self.begin(board, request_id="w1")
        error = self.assert_code("CATALOG_UNAVAILABLE", self.publish_with, board, grant, "c1", profiles=[PROFILE])
        self.assertIn("model_catalog_refresh", error.message)
        self.assert_code(
            "INVALID_ARGUMENT",
            self.publish_with,
            board,
            grant,
            "c2",
            profiles=[{**PROFILE, "available": False, "enabled": True}],
        )
        unavailable = self.publish_with(
            board, grant, "c3", profiles=[{**PROFILE, "available": False, "enabled": False}]
        )
        self.assertTrue(unavailable["published"])
        self.assertFalse(self.snapshot(board)["profiles"][0]["available"])
        # Discovery actually makes the availability claim verifiable.
        self.seed_catalog(board)
        self.publish(board, request_id="w2", command_id="c4", profiles=[PROFILE])
        published = self.snapshot(board)["profiles"][0]
        self.assertTrue(published["available"])
        self.assertNotIn("unavailableReason", published)
        # A model the installed harness does not advertise can never be claimed live.
        self.refused_publish(
            board,
            "CATALOG_UNAVAILABLE",
            request_id="w3",
            command_id="c5",
            profiles=[{**PROFILE, "profileId": "dsh:deepseek-official:made-up:off", "model": "made-up"}],
        )
        # An effort outside the installed harness union is not a legal profile.
        self.refused_publish(
            board,
            "INVALID_ARGUMENT",
            request_id="w4",
            command_id="c6",
            profiles=[{**PROFILE, "profileId": "dsh:deepseek-official:deepseek-flash:turbo", "effort": "turbo"}],
        )

    def test_pin_requires_an_available_enabled_profile(self):
        board = self.board()
        self.publish(
            board,
            request_id="w1",
            command_id="c1",
            profiles=[{**PROFILE, "available": False, "enabled": False}],
        )
        error = self.refused_publish(
            board,
            "CONFLICT",
            request_id="w2",
            command_id="c2",
            preferences=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "wants it"}],
        )
        self.assertIn("pin", error.message.lower())
        # Exclude is allowed for an unavailable profile and stays distinct from prefer.
        self.publish(
            board,
            request_id="w3",
            command_id="c3",
            preferences=[{"profileId": PROFILE_ID, "mode": "exclude", "reason": "not for this work"}],
        )
        self.assertEqual(self.snapshot(board)["preferences"][0]["mode"], "exclude")


    def test_omitted_collections_and_explicit_null_are_distinct_commands(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="w1")
        base = {
            "commandId": "same-command",
            "writerId": grant["writerId"],
            "generation": grant["generation"],
            "writerToken": grant["writerToken"],
            "expectedRevision": 0,
        }
        first = board.call("evaluation_write_publish", {**base, "profiles": [PROFILE]})
        self.assertFalse(first["duplicate"])
        # An identical replay is still a duplicate.
        self.assertTrue(board.call("evaluation_write_publish", {**base, "profiles": [PROFILE]})["duplicate"])
        # The same commandId with a different provided-field set is never a replay:
        # an omitted collection and an explicit null are distinct requests.
        self.assert_code("CONFLICT", board.call, "evaluation_write_publish", {**base, "profiles": None})
        self.assert_code("CONFLICT", board.call, "evaluation_write_publish", {**base, "cards": None})
        self.assert_code("CONFLICT", board.call, "evaluation_write_publish", {**base, "cards": []})
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)
        # A fresh commandId with an explicit null collection is invalid input, not an
        # omitted collection.
        other = self.begin(board, request_id="w2", expected=1)
        self.assert_code(
            "INVALID_ARGUMENT",
            board.call,
            "evaluation_write_publish",
            {
                "commandId": "fresh-command",
                "writerId": other["writerId"],
                "generation": other["generation"],
                "writerToken": other["writerToken"],
                "expectedRevision": 1,
                "profiles": None,
            },
        )
        # A missing required field is rejected before any stored receipt could satisfy it.
        self.assert_code(
            "INVALID_ARGUMENT",
            board.call,
            "evaluation_write_publish",
            {
                "commandId": "same-command",
                "writerId": other["writerId"],
                "writerToken": other["writerToken"],
                "expectedRevision": 1,
            },
        )
        self.assert_code(
            "INVALID_ARGUMENT",
            board.call,
            "evaluation_write_abort",
            {"commandId": "abort-missing", "writerId": other["writerId"], "writerToken": other["writerToken"]},
        )
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)

    def test_expired_or_unknown_writer_cannot_publish(self):
        board = self.board()
        grant = self.begin(board, request_id="w1")
        self.assert_code(
            "UNAUTHORIZED",
            board.call,
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": "0" * 64,
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assert_code(
            "STALE_GENERATION",
            board.call,
            "evaluation_write_publish",
            {
                "commandId": "c2",
                "writerId": grant["writerId"],
                "generation": grant["generation"] + 1,
                "writerToken": grant["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(self.snapshot(board)["tableRevision"], 0)

    def test_snapshot_never_leaks_credentials(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.begin(board, request_id="w1")
        self.publish_with(board, grant, "c1", profiles=[PROFILE])
        raw = json.dumps(self.snapshot(board))
        self.assertNotIn(grant["writerToken"], raw)
        self.assertNotIn(board.service.token, raw)
        self.assertNotIn("writerToken", raw)
        self.assertNotIn("token_verifier", raw)
        self.assertNotIn("capability", raw)


# ---------------------------------------------------------------------------
# Gate: fairness, draining, fencing
# ---------------------------------------------------------------------------
class EvaluationGateTests(EvaluationTestCase):
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
            "evaluation_write_publish",
            {
                "commandId": "c-b",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        first_result = board.call(
            "evaluation_write_publish",
            {
                "commandId": "c-a",
                "writerId": first["writerId"],
                "generation": first["generation"],
                "writerToken": first["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(first_result["revision"], 1)
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
            "evaluation_write_publish",
            {
                "commandId": "c-b2",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 0,
                "profiles": [SECOND_PROFILE],
            },
        )
        second_result = board.call(
            "evaluation_write_publish",
            {
                "commandId": "c-b3",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 1,
                "profiles": [PROFILE, SECOND_PROFILE],
            },
        )
        self.assertEqual(second_result["revision"], 2)
        self.assertEqual(len(self.snapshot(board)["profiles"]), 2)

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
            board.call,
            "evaluation_write_begin",
            {"requestId": "same", "expectedRevision": 5, "kind": "human"},
        )
        self.assert_code(
            "CONFLICT",
            board.call,
            "evaluation_write_begin",
            {"requestId": "same", "expectedRevision": 0, "kind": "maintenance"},
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
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": writer["writerId"],
                "generation": writer["generation"],
                "writerToken": writer["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        released = board.call("evaluation_reader_release", {"readerId": reader["readerId"]})
        self.assertTrue(released["released"])
        self.assertEqual(released["phase"], "writing")
        self.assertEqual(self.snapshot(board)["gate"]["readers"], 0)
        self.assertEqual(self.snapshot(board)["gate"]["writer"]["writerId"], writer["writerId"])
        result = board.call(
            "evaluation_write_publish",
            {
                "commandId": "c2",
                "writerId": writer["writerId"],
                "generation": writer["generation"],
                "writerToken": writer["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(result["revision"], 1)
        # Readers are admitted again once the gate is open, and idempotent release works.
        third = board.call("evaluation_reader_begin", {"kind": "selection", "revision": 1})
        self.assertEqual(third["phase"], "open")
        self.assertTrue(board.call("evaluation_reader_release", {"readerId": third["readerId"]})["released"])
        repeat = board.call("evaluation_reader_release", {"readerId": third["readerId"]})
        self.assertFalse(repeat["released"])
        self.assertTrue(repeat["alreadyReleased"])
        self.assert_code("REVISION_CONFLICT", board.call, "evaluation_reader_begin", {"kind": "selection", "revision": 0})
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
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(result["revision"], 1)
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
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(error.details.get("state"), "aborted")
        self.assertEqual(self.snapshot(board)["tableRevision"], 0)
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
            "expectedRevision": 0,
            "profiles": [PROFILE],
        }
        first = board.call("evaluation_write_publish", payload)
        replay = board.call("evaluation_write_publish", payload)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["revision"], first["revision"])
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)
        # The writer already published; beginning again with the same identity is a
        # terminal conflict rather than a silent second write.
        self.assert_code("ALREADY_PUBLISHED", board.call, "evaluation_write_begin", {"requestId": "write-1", "expectedRevision": 0})
        # The same commandId with a different payload is rejected by the receipt.
        other = self.begin(board, request_id="write-2", expected=1)
        self.assert_code(
            "CONFLICT",
            board.call,
            "evaluation_write_publish",
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
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": first["writerId"],
                "generation": first["generation"],
                "writerToken": first["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
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
            "evaluation_write_publish",
            {
                "commandId": "c2",
                "writerId": second["writerId"],
                "generation": second["generation"],
                "writerToken": second["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(result["revision"], 1)
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
            "evaluation_write_publish",
            {
                "commandId": "c1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
        )
        self.assertEqual(result["revision"], 1)
        # After the restart a new writer queues, is aborted, and its late token stays fenced.
        stale = self.begin(second, request_id="w2", expected=1)
        second.call(
            "evaluation_write_abort",
            {"commandId": "abort-1", "writerId": stale["writerId"], "generation": stale["generation"], "writerToken": stale["writerToken"]},
        )
        third = self.board(clock=clock, writer_lease_seconds=30)
        self.assert_code(
            "WRITER_NOT_ACTIVE",
            third.call,
            "evaluation_write_publish",
            {
                "commandId": "c2",
                "writerId": stale["writerId"],
                "generation": stale["generation"],
                "writerToken": stale["writerToken"],
                "expectedRevision": 1,
                "profiles": [SECOND_PROFILE],
            },
        )
        self.assertEqual(self.snapshot(third)["tableRevision"], 1)


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
        self.seed_catalog(board)
        return self.publish(board, request_id="seed", command_id="seed-1", profiles=[PROFILE], **collections)

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
        board.client().acknowledge(runId=run_id, note="inspected the artifact and ran the checks", verdict="accepted")
        refreshed = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-success", "summary": "process exited 0", "source": "cli", "runId": run_id},
        )
        self.assertTrue(refreshed["counted"])
        self.assertTrue(refreshed["duplicate"])
        card = self.publish(
            board,
            request_id="w-card",
            command_id="c-card",
            cards=[{"profileId": PROFILE_ID, "summary": "reviewed once", "strengths": [], "limitations": [], "risks": [], "evidenceIds": [report["evidence"]["evidenceId"]]}],
        )
        self.assertEqual(card["revision"], 2)
        self.assertEqual(self.snapshot(board)["cards"][0]["sampleCount"], 1)

    def cancelled_task(self, board, request_id: str = "task-cancelled") -> str:
        """One real dsh-identity task whose run is durably cancelled."""
        client = board.client()
        run_id = client.submit(
            requestId=request_id,
            task="produce a model result",
            cwd=str(self.workdir()),
            model=PROFILE["model"],
            provider=PROFILE["provider"],
            effort=PROFILE["effort"],
        )["task"]["runId"]
        worker_id = f"worker-{request_id}"
        client.register_worker(worker_id, adapter="dsh", capabilities=["dsh"])
        claim = client.claim(worker_id, f"claim-{request_id}", "n" * 32, task_id=run_id)["claim"]
        client.cancel(runId=run_id, reason="operator changed their mind")
        client.submit_result(
            worker_id,
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {
                "status": "cancelled",
                "result": {
                    "status": "cancelled",
                    "mode": "run",
                    "requested": {
                        "provider": PROFILE["provider"],
                        "model": PROFILE["model"],
                        "reasoningEffort": PROFILE["effort"],
                    },
                },
                "shutdownConfirmed": True,
                "exitCode": None,
            },
        )
        client.acknowledge(note="reviewed the cancellation", verdict="rejected", runId=run_id)
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
        # A genuinely failed attempt with a Host rejection is the same sample class.
        failed_run = self.modelled_task(board, request_id="task-failed", verdict="rejected", failed=True)
        failed = board.call(
            "evaluation_evidence_record",
            {"profileId": PROFILE_ID, "kind": "task-failure", "summary": "the model failed outright", "source": "cli", "runId": failed_run},
        )
        self.assertTrue(failed["counted"])
        with board.store.db.read() as connection:
            self.assertEqual(board.evaluation._sample_count(connection, PROFILE_ID), 2)

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
        self.seed_catalog(board)
        self.publish(board, request_id="seed", command_id="seed-1", profiles=[PROFILE, SECOND_PROFILE])
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
            preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "still preferred"}],
        )
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 1)
        # Only an actual card reference incorporates it.
        self.publish(
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
            configuration={"decisionProfileId": PROFILE_ID, "autoMaintain": False},
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
        card = self.publish(
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
        self.assertEqual(card["revision"], 2)
        published = self.snapshot(board)["cards"][0]
        self.assertEqual(published["sampleCount"], 1)
        self.assertEqual(published["revision"], 1)
        first_updated = published["updatedAt"]
        self.assertEqual(self.snapshot(board)["pendingEvidence"], 0)
        # A card may not reference evidence owned by another profile.
        self.publish(board, request_id="w3", command_id="c3", profiles=[PROFILE, SECOND_PROFILE])
        self.refused_publish(
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
        self.publish(
            board,
            request_id="w5",
            command_id="c5",
            cards=[{"profileId": PROFILE_ID, "summary": "可靠", "strengths": ["fast"], "limitations": ["small context"], "risks": ["fixture risk"], "evidenceIds": [evidence["evidence"]["evidenceId"]]}],
        )
        unchanged = self.snapshot(board)["cards"][0]
        self.assertEqual(unchanged["revision"], 1)
        self.assertEqual(unchanged["updatedAt"], first_updated)
        self.publish(
            board,
            request_id="w6",
            command_id="c6",
            cards=[{"profileId": PROFILE_ID, "summary": "可靠（更正）", "strengths": ["fast"], "limitations": ["small context"], "risks": ["fixture risk"], "evidenceIds": [evidence["evidence"]["evidenceId"]]}],
        )
        changed = self.snapshot(board)["cards"][0]
        self.assertEqual(changed["revision"], 2)
        self.assertNotEqual(changed["updatedAt"], first_updated)
        # A profile referenced by recorded evidence cannot be removed.
        self.refused_publish(board, "CONFLICT", request_id="w7", command_id="c7", profiles=[SECOND_PROFILE])

    def test_catalog_refresh_is_explicit_attributed_and_failure_safe(self):
        board = self.board()
        refreshed = board.call("model_catalog_refresh", {"requestId": "cat-1"})
        self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
        self.assertEqual(refreshed["catalog"]["harnessVersion"], "test-harness-1")
        self.assertEqual(refreshed["catalog"]["providers"][0]["efforts"], ["off", "low", "high", "max"])
        proposals = refreshed["profiles"]
        self.assertTrue(proposals)
        self.assertTrue(all(item["enabled"] is False for item in proposals), "discovered profiles are proposals only")
        self.assertTrue(all(item["available"] is True for item in proposals))
        self.assertEqual(refreshed["note"].count("proposed"), 1)
        self.assertEqual(self.snapshot(board)["tableRevision"], 0, "a refresh never publishes profiles")
        # Identical discovery is recorded once.
        self.assertTrue(board.call("model_catalog_refresh", {"requestId": "cat-2"})["duplicate"])
        self.assertEqual(len(self.snapshot(board)["profiles"]), 0)
        # A failed discovery keeps the previous recorded catalog and the table.
        from buddy import catalog
        from buddy.errors import BoardError as _BoardError

        original = catalog.discover
        catalog.discover = lambda: (_ for _ in ()).throw(_BoardError("CATALOG_UNAVAILABLE", "no harness"))
        try:
            self.assert_code("CATALOG_UNAVAILABLE", board.call, "model_catalog_refresh", {"requestId": "cat-3"})
        finally:
            catalog.discover = original
        self.publish(board, request_id="w1", command_id="c1", profiles=[PROFILE])
        self.assertEqual(self.snapshot(board)["tableRevision"], 1)


class InstalledHarnessDiscoveryTests(BoardTestCase):
    """The real discovery helper against the actually installed DSH harness.

    Skipped when no harness is installed: this test never installs or downloads one,
    and it never runs a model.
    """

    def test_installed_harness_discovery_is_truthful_and_credential_free(self):
        from buddy import catalog

        if not catalog.helper_available():
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
            self.assertEqual(provider["adapter"], "dsh")
            for model in provider["models"]:
                self.assertTrue(model["id"])
        raw = json.dumps(payload)
        for forbidden in ("apiKeyEnv", "API_KEY", "Authorization", "Bearer ", "sk-"):
            self.assertNotIn(forbidden, raw)
        proposals = view.proposed_profiles()
        self.assertTrue(proposals)
        for proposal in proposals:
            self.assertFalse(proposal["enabled"], "discovered profiles stay proposals until published")
            self.assertTrue(proposal["available"])
            self.assertEqual(proposal["adapter"], "dsh")

        # Publishing real discovered proposals obeys the same writer gate as any edit.
        board = self.board()
        refreshed = board.call("model_catalog_refresh", {"requestId": "installed-catalog"})
        proposals = refreshed["profiles"]
        grant = board.call(
            "evaluation_write_begin", {"requestId": "installed-1", "expectedRevision": 0, "kind": "human"}
        )
        result = board.call(
            "evaluation_write_publish",
            {
                "commandId": "installed-publish",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": 0,
                "profiles": proposals,
            },
        )
        self.assertEqual(result["counts"]["profiles"], len(proposals))
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(len(snapshot["profiles"]), len(proposals))
        self.assertTrue(all(item["source"].startswith(("catalog:", "dsh:")) for item in snapshot["profiles"]))


if __name__ == "__main__":
    unittest.main()
