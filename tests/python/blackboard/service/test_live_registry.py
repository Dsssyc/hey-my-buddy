"""Focused facts for the blackboard-side in-memory live registry (step 5-B1).

Every test drives the real private ``BoardStore`` through the production
verification path — the store's own attempt-actor proof, its attempts and
``workflow_turns`` rows, and the real restart fence — against the new
``LiveRegistry`` with a recording fake peer standing in only for the Host's
future ``WorkerRuntimeLive`` channel factory. The service-side ``_guard``
operations and the inquiry ``channel_for`` call remain Host wiring and are not
exercised here.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from blackboard.store.test_store import StoreConcurrencyTestCase

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service import live_registry
from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
from hey_my_buddy.protocol.worker_live import WorkerLiveAttach

INSTANCE_A = "a" * 64
INSTANCE_B = "b" * 64
LIVE_TOKEN = "c" * 64
ADDRESS = "127.0.0.1:46901"
NONCE = "live-nonce-SECRETMARKER"
NAME = "zcode-worker"


class RecordingChannel:
    """The fake peer channel: it records close calls and refuses locked ones."""

    def __init__(self, binding, registry=None, probe=None):
        self.binding = binding
        self.identity = binding.identity
        self.registry = registry
        self.probe = probe
        self.closed = []

    def close(self, *, reason: str) -> None:
        if self.probe is not None:
            self.probe.assert_outside(self.registry)
        if self.registry is not None and self.registry._lock.locked():
            raise AssertionError("the registry closed a channel while holding its map lock")
        self.closed.append(reason)


class RecordingFactory:
    """The fake endpoint factory: records frames and never holds locks/transactions."""

    def __init__(self, registry: LiveRegistry | None = None, probe: "TransactionProbe | None" = None):
        self.registry = registry
        self.probe = probe
        self.calls: list[WorkerLiveAttach] = []
        self.channels: list[RecordingChannel] = []
        self.mid_build = None
        self.error: Exception | None = None
        self.return_none = False

    def __call__(self, binding: WorkerLiveAttach):
        if self.probe is not None:
            self.probe.assert_outside(self.registry)
        self.calls.append(binding)
        channel = RecordingChannel(binding, registry=self.registry, probe=self.probe)
        self.channels.append(channel)
        if self.mid_build is not None:
            action = self.mid_build
            self.mid_build = None
            action()
        if self.error is not None:
            raise self.error
        if self.return_none:
            return None
        return channel


class TransactionProbe:
    """Records whether any store transaction is open at factory/close time."""

    def __init__(self, db):
        self.depth = {"read": 0, "write": 0}
        original_read, original_write = db.read, db.write

        def wrapped_read(*args, **kwargs):
            self.depth["read"] += 1
            try:
                with original_read(*args, **kwargs) as connection:
                    yield connection

            finally:
                self.depth["read"] -= 1

        def wrapped_write(*args, **kwargs):
            self.depth["write"] += 1
            try:
                with original_write(*args, **kwargs) as connection:
                    yield connection

            finally:
                self.depth["write"] -= 1

        from contextlib import contextmanager

        db.read = contextmanager(wrapped_read)
        db.write = contextmanager(wrapped_write)

    def assert_outside(self, registry: LiveRegistry) -> None:
        if any(self.depth.values()):
            raise AssertionError(f"a store transaction is open during a peer call: {self.depth}")
        if registry is not None and registry._lock.locked():
            raise AssertionError("the registry map lock is held during a peer call")


class LiveRegistryTestCase(StoreConcurrencyTestCase):
    """The shared fixtures: one claimed governed attempt and its registry."""

    governed = True

    def setUp(self):
        super().setUp()
        self.board = self.board()
        self.client = self.board.client()
        adapter = "dsh" if self.governed else "command"
        self.client.register_worker("w1", adapter=adapter, capabilities=[adapter])
        if self.governed:
            self.run_id = self.submit_governed(self.board, "live-1")
        else:
            self.run_id = self.client.submit(
                requestId="live-1", task="no governed turn", cwd=str(self.workdir("live-1")),
                adapter="command", argv=["/bin/true"],
            )["task"]["runId"]
        self.claim = self.client.claim("w1", "claim-live-1", NONCE, worker_instance="inst-real")
        self.attempt = self.claim["claim"]["attempt"]
        self.factory = RecordingFactory()
        self.registry = LiveRegistry(self.board.store, self.factory)
        self.factory.registry = self.registry

    # -- payload helpers -----------------------------------------------------
    def identity(self, **overrides) -> dict:
        identity = {
            "taskId": self.attempt["taskId"],
            "attemptId": self.attempt["attemptId"],
            "generation": self.attempt["generation"],
            "invocationId": "inv-" + self.attempt["attemptId"][:8],
        }
        turn = self.claim["claim"].get("turn")
        if turn is not None:
            identity.update(turnId=turn["turnId"], inputSha256=turn["inputSha256"])
        identity.update(overrides)
        return identity

    def attach_payload(self, *, instance_id: str = INSTANCE_A, identity: dict | None = None,
                       worker_instance: str = "inst-real", nonce: str = NONCE, **overrides) -> dict:
        payload = {
            "workerId": self.attempt["workerId"],
            "attemptId": self.attempt["attemptId"],
            "generation": self.attempt["generation"],
            "nonce": nonce,
            "workerInstance": worker_instance,
            "identity": identity or self.identity(),
            "address": ADDRESS,
            "name": NAME,
            "instanceId": instance_id,
            "liveToken": LIVE_TOKEN,
        }
        payload.update(overrides)
        return payload

    def detach_payload(self, *, instance_id: str = INSTANCE_A, identity: dict | None = None,
                       worker_instance: str = "inst-real", **overrides) -> dict:
        payload = {
            "workerId": self.attempt["workerId"],
            "attemptId": self.attempt["attemptId"],
            "generation": self.attempt["generation"],
            "nonce": NONCE,
            "workerInstance": worker_instance,
            "identity": identity or self.identity(),
            "instanceId": instance_id,
        }
        payload.update(overrides)
        return payload

    def view(self, **attempt_overrides) -> dict:
        view = dict(self.board.store.task_get({"runId": self.run_id})["task"])
        if attempt_overrides:
            selected = dict(view["selectedAttempt"])
            selected.update(attempt_overrides)
            view["selectedAttempt"] = selected
        return view

    def durable_snapshot(self) -> tuple:
        with self.board.store.db.read() as connection:
            return (
                tuple(connection.execute("SELECT * FROM attempts").fetchall()),
                tuple(connection.execute("SELECT * FROM tasks").fetchall()),
                tuple(connection.execute("SELECT * FROM workflow_turns").fetchall()),
                tuple(connection.execute("SELECT * FROM workflow_runs").fetchall()),
                connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"],
            )


class AttachTests(LiveRegistryTestCase):
    def test_attach_records_the_holders_endpoint_and_states_only_public_facts(self):
        reply = self.registry.attach(self.attach_payload())
        self.assertEqual(
            reply,
            {"attached": True, "attemptId": self.attempt["attemptId"], "instanceId": INSTANCE_A, "replayed": False},
        )
        self.assertEqual(self.factory.calls, [])

    def test_the_identical_binding_again_is_an_idempotent_replay(self):
        payload = self.attach_payload()
        first = self.registry.attach(payload)
        replay = self.registry.attach(payload)
        self.assertEqual(first["replayed"], False)
        self.assertEqual(replay["replayed"], True)
        self.assertEqual(self.factory.calls, [])

    def test_attach_refuses_a_wrong_actor_nonce_worker_or_generation(self):
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload(nonce="f" * 32))
        self.client.register_worker("w2", adapter="dsh", capabilities=["dsh"])
        self.assert_code(
            "UNAUTHORIZED", self.registry.attach, self.attach_payload(**{"workerId": "w2"})
        )
        self.assert_code(
            "STALE_GENERATION", self.registry.attach, self.attach_payload(generation=self.attempt["generation"] + 1)
        )

    def test_attach_refuses_an_identity_that_is_not_this_attempt_actor(self):
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload(identity=self.identity(taskId="task-other")))
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload(identity=self.identity(attemptId="attempt-other")))
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload(identity=self.identity(generation=self.attempt["generation"] + 5)))
        self.assert_code(
            "UNAUTHORIZED",
            self.registry.attach,
            self.attach_payload(identity=self.identity(inputSha256=hashlib.sha256(b"invented").hexdigest())),
        )

    def test_attach_refuses_an_endpoint_of_a_different_worker_process_instance(self):
        self.assert_code(
            "UNAUTHORIZED", self.registry.attach, self.attach_payload(worker_instance="inst-fake")
        )

    def test_an_unknown_recorded_worker_instance_has_no_live_capability(self):
        self.registry.attach(self.attach_payload())
        with self.board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET worker_instance=NULL WHERE attempt_id=?",
                               (self.attempt["attemptId"],))
        before = self.durable_snapshot()
        for operation, payload in ((self.registry.attach, self.attach_payload()),
                                   (self.registry.detach, self.detach_payload())):
            with self.subTest(operation=operation.__name__):
                self.assert_code("UNAUTHORIZED", operation, payload)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertIn(self.attempt["attemptId"], self.registry._bindings)

    def test_attach_refuses_a_finished_attempt_and_detach_still_works(self):
        self.registry.attach(self.attach_payload())
        self.client.submit_result(
            "w1",
            self.attempt["attemptId"],
            self.attempt["generation"],
            NONCE,
            {"status": "ok", "result": {"finalText": "done"}, "shutdownConfirmed": True, "exitCode": 0},
        )
        self.assert_code("ATTEMPT_FINISHED", self.registry.attach, self.attach_payload())
        reply = self.registry.detach(self.detach_payload())
        self.assertEqual(
            reply,
            {"detached": True, "attemptId": self.attempt["attemptId"], "instanceId": INSTANCE_A},
        )

    def test_after_a_restart_attach_requires_reconcile_and_a_new_registry_is_empty(self):
        self.board.store.reconcile_startup()
        self.assert_code("ATTEMPT_UNCERTAIN", self.registry.attach, self.attach_payload())
        self.client.reconcile(
            "w1", self.attempt["attemptId"], self.attempt["generation"], NONCE, worker_instance="inst-real"
        )
        reply = self.registry.attach(self.attach_payload())
        self.assertEqual(reply["attached"], True)
        fresh = LiveRegistry(self.board.store, self.factory)
        self.assertIsNone(fresh.channel_for(self.view()))
        self.assertEqual(fresh.attach(self.attach_payload())["replayed"], False)


class TurnIdentityTests(LiveRegistryTestCase):
    def test_a_turn_identity_must_be_the_recorded_turn_of_this_attempt(self):
        turn = self.claim["claim"]["turn"]
        with self.board.store.db.read() as connection:
            row = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=?", (turn["turnId"],)).fetchone()
        self.assertIsNone(row["input_sha256"])
        self.assertEqual(json.loads(row["input_json"]), turn["input"])
        self.assertEqual(hashlib.sha256(row["input_json"].encode("utf-8")).hexdigest(), turn["inputSha256"])
        self.assertEqual(self.registry.attach(self.attach_payload())["attached"], True)
        for overrides in ({"inputSha256": hashlib.sha256(b"other").hexdigest()},
                          {"turnId": "turn-missing"}, {"turnId": None}, {"inputSha256": None}):
            with self.subTest(overrides=overrides):
                self.assert_code("UNAUTHORIZED", self.registry.attach,
                                 self.attach_payload(identity=self.identity(**overrides)))

    def test_a_governed_turn_cannot_be_hidden_by_a_both_null_identity(self):
        self.assert_code("UNAUTHORIZED", self.registry.attach,
                         self.attach_payload(identity=self.identity(turnId=None, inputSha256=None)))

    def test_a_turn_recorded_for_another_generation_is_refused(self):
        with self.board.store.db.write() as connection:
            connection.execute("UPDATE workflow_turns SET generation=generation+1 WHERE turn_id=?",
                               (self.identity()["turnId"],))
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload())

    def test_the_current_turn_and_run_must_both_bind_this_attempt(self):
        for table, column, key, value in (
            ("workflow_turns", "attempt_id", "turn_id", self.identity()["turnId"]),
            ("workflow_runs", "current_attempt_id", "run_id", self.run_id),
        ):
            with self.subTest(table=table):
                with self.board.store.db.write() as connection:
                    connection.execute(f"UPDATE {table} SET {column}=NULL WHERE {key}=?", (value,))
                self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload())
                self.assert_code("UNAUTHORIZED", self.registry.attach,
                                 self.attach_payload(identity=self.identity(turnId=None, inputSha256=None)))
                with self.board.store.db.write() as connection:
                    connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?",
                                       (self.attempt["attemptId"], value))

    def test_a_stored_digest_must_agree_with_the_actual_input(self):
        with self.board.store.db.write() as connection:
            connection.execute("UPDATE workflow_turns SET input_sha256=? WHERE turn_id=?",
                               (self.identity()["inputSha256"], self.identity()["turnId"]))
        self.assertTrue(self.registry.attach(self.attach_payload())["attached"])
        with self.board.store.db.write() as connection:
            connection.execute("UPDATE workflow_turns SET input_sha256=? WHERE turn_id=?",
                               (hashlib.sha256(b"wrong stored digest").hexdigest(), self.identity()["turnId"]))
        self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload())

    def test_an_old_row_of_the_same_attempt_cannot_replace_the_current_turn(self):
        # Copy the real prepared input to a historical row with the same actor.
        # A lookup by caller turnId alone would incorrectly authorize it.
        with self.board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO workflow_turns(turn_id, run_id, attempt_id, generation, turn_index, resume_mode,"
                " input_json, state, created_at, updated_at)"
                " SELECT 'historical-turn', run_id, attempt_id, generation, turn_index+1, resume_mode,"
                " input_json, state, created_at, updated_at FROM workflow_turns WHERE turn_id=?",
                (self.identity()["turnId"],),
            )
        self.assert_code("UNAUTHORIZED", self.registry.attach,
                         self.attach_payload(identity=self.identity(turnId="historical-turn")))
        self.assertTrue(self.registry.attach(self.attach_payload())["attached"])


class UngovernedTests(LiveRegistryTestCase):
    governed = False

    def test_a_real_ungoverned_claim_keeps_the_both_null_rule(self):
        self.assertIsNone(self.claim["claim"].get("turn"))
        with self.board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (self.run_id,)).fetchone())
        self.assertTrue(self.registry.attach(self.attach_payload())["attached"])
        for overrides in ({"turnId": "invented-turn"}, {"inputSha256": "d" * 64},
                          {"turnId": "invented-turn", "inputSha256": "d" * 64}):
            with self.subTest(overrides=overrides):
                self.assert_code("UNAUTHORIZED", self.registry.attach,
                                 self.attach_payload(identity=self.identity(**overrides)))
        self.assertTrue(self.registry.detach(self.detach_payload())["detached"])


class DetachTests(LiveRegistryTestCase):
    def test_detach_removes_only_the_exact_identity_and_instance(self):
        self.registry.attach(self.attach_payload())
        self.assert_code(
            "UNAUTHORIZED",
            self.registry.detach,
            self.detach_payload(instance_id=INSTANCE_A, identity=self.identity(taskId="task-other")),
        )
        self.assert_code("CONFLICT", self.registry.detach, self.detach_payload(instance_id=INSTANCE_B))
        self.assertEqual(
            self.registry.detach(self.detach_payload(instance_id=INSTANCE_A)),
            {"detached": True, "attemptId": self.attempt["attemptId"], "instanceId": INSTANCE_A},
        )
        self.assertEqual(
            self.registry.detach(self.detach_payload(instance_id=INSTANCE_A)),
            {"detached": False, "attemptId": self.attempt["attemptId"], "instanceId": INSTANCE_A},
        )

    def test_a_wrong_actor_cannot_detach_anything(self):
        self.registry.attach(self.attach_payload())
        self.assert_code("UNAUTHORIZED", self.registry.detach, self.detach_payload(nonce="f" * 32))
        self.assert_code(
            "UNAUTHORIZED", self.registry.detach, self.detach_payload(worker_instance="inst-fake")
        )
        self.assert_code(
            "STALE_GENERATION", self.registry.detach, self.detach_payload(generation=self.attempt["generation"] + 1)
        )
        self.client.register_worker("w2", adapter="dsh", capabilities=["dsh"])
        self.assert_code("UNAUTHORIZED", self.registry.detach, self.detach_payload(workerId="w2"))

    def test_every_full_identity_field_is_checked_before_detaching(self):
        self.registry.attach(self.attach_payload())
        for field, value, code in (("taskId", "foreign-task", "UNAUTHORIZED"),
                                   ("attemptId", "foreign-attempt", "UNAUTHORIZED"),
                                   ("generation", self.attempt["generation"] + 1, "UNAUTHORIZED"),
                                   ("invocationId", "old-invocation", "CONFLICT"),
                                   ("turnId", "old-turn", "CONFLICT"),
                                   ("inputSha256", "d" * 64, "CONFLICT")):
            with self.subTest(field=field):
                self.assert_code(code, self.registry.detach,
                                 self.detach_payload(identity=self.identity(**{field: value})))
        self.assertTrue(self.registry.detach(self.detach_payload())["detached"])

    def test_an_old_instance_or_identity_cannot_detach_a_replacement(self):
        self.registry.attach(self.attach_payload())
        old_channel = self.registry.channel_for(self.view())
        new_identity = self.identity(invocationId="new-invocation")
        self.registry.attach(self.attach_payload(instance_id=INSTANCE_B, identity=new_identity))
        new_channel = self.registry.channel_for(self.view())
        self.assertEqual(old_channel.closed, ["replaced"])
        self.assert_code("CONFLICT", self.registry.detach, self.detach_payload(instance_id=INSTANCE_B))
        self.assert_code("CONFLICT", self.registry.detach, self.detach_payload(identity=new_identity))
        self.assertIs(self.registry.channel_for(self.view()), new_channel)
        self.assertTrue(self.registry.detach(self.detach_payload(instance_id=INSTANCE_B, identity=new_identity))["detached"])
        self.assertEqual(new_channel.closed, ["detached"])

    def test_verification_writes_no_rows_and_appends_no_events(self):
        before = self.durable_snapshot()
        self.registry.attach(self.attach_payload())
        self.registry.detach(self.detach_payload())
        self.assertEqual(self.durable_snapshot(), before)


class ChannelTests(LiveRegistryTestCase):
    def test_channel_for_caches_one_channel_and_rechecks_the_real_view(self):
        self.registry.attach(self.attach_payload())
        first = self.registry.channel_for(self.view())
        second = self.registry.channel_for(self.view())
        self.assertIsNotNone(first)
        self.assertIs(first, second)
        self.assertEqual(len(self.factory.calls), 1)
        self.assertEqual(self.factory.calls[0].identity.task_id, self.attempt["taskId"])
        self.assertEqual(self.factory.calls[0].address, ADDRESS)
        stale = self.view(generation=self.attempt["generation"] + 1)
        foreign = self.view(workerId="w-other")
        foreign_instance = self.view(workerInstance="inst-other")
        unknown_instance = self.view(workerInstance=None)
        missing_instance = self.view()
        missing_instance["selectedAttempt"].pop("workerInstance")
        uncertain = self.view(executionState="uncertain")
        fenced = self.view(ownership="uncertain")
        for refused in (stale, foreign, foreign_instance, unknown_instance, missing_instance, uncertain, fenced):
            self.assertIsNone(self.registry.channel_for(refused))
        terminal = self.view()
        terminal["state"] = "completed"
        self.assertIsNone(self.registry.channel_for(terminal))
        empty = self.view()
        empty["selectedAttempt"] = None
        self.assertIsNone(self.registry.channel_for(empty))
        self.assertIs(self.registry.channel_for(self.view()), first)
        self.assertEqual(len(self.factory.calls), 1)

    def test_a_fresh_registry_has_no_channels(self):
        self.assertIsNone(self.registry.channel_for(self.view()))
        self.assertEqual(self.factory.calls, [])

    def test_build_and_close_happen_outside_transactions_and_the_map_lock(self):
        probe = TransactionProbe(self.board.store.db)
        self.factory.probe = probe
        self.factory.registry = self.registry
        self.registry.attach(self.attach_payload())
        channel = self.registry.channel_for(self.view())
        self.assertIsNotNone(channel)
        self.registry.detach(self.detach_payload())
        self.assertEqual(channel.closed, ["detached"])

    def test_replacement_closes_the_owned_channel_and_rebuilds(self):
        self.registry.attach(self.attach_payload())
        first = self.registry.channel_for(self.view())
        self.registry.attach(self.attach_payload(instance_id=INSTANCE_B, liveToken="9" * 64, address="127.0.0.1:46902"))
        self.assertEqual(first.closed, ["replaced"])
        second = self.registry.channel_for(self.view())
        self.assertIsNot(second, first)
        self.assertEqual(len(self.factory.calls), 2)

    def test_a_binding_replaced_while_the_channel_builds_supersedes_the_built_channel(self):
        self.registry.attach(self.attach_payload())
        replacement = self.attach_payload(instance_id=INSTANCE_B, liveToken="9" * 64)
        self.factory.mid_build = lambda: self.registry.attach(replacement)
        built = self.registry.channel_for(self.view())
        self.assertIsNone(built)
        self.assertEqual(self.factory.channels[-1].closed, ["superseded"])
        after = self.registry.channel_for(self.view())
        self.assertIsNotNone(after)

    def test_a_binding_detached_while_the_channel_builds_reports_unavailable(self):
        self.registry.attach(self.attach_payload())
        self.factory.mid_build = lambda: self.registry.detach(self.detach_payload())
        self.assertIsNone(self.registry.channel_for(self.view()))
        self.assertEqual(self.factory.channels[-1].closed, ["superseded"])

    def test_a_failing_or_empty_factory_is_unavailable_and_writes_no_stop_fact(self):
        before = self.durable_snapshot()
        self.registry.attach(self.attach_payload())
        self.factory.error = BoardError("PEER_UNAVAILABLE", "the worker endpoint refused the transport")
        self.assertIsNone(self.registry.channel_for(self.view()))
        self.factory.error = None
        self.factory.return_none = True
        self.assertIsNone(self.registry.channel_for(self.view()))
        self.assertEqual(self.durable_snapshot(), before)


class SecretTests(LiveRegistryTestCase):
    def test_responses_errors_and_reprs_never_echo_the_endpoint_secrets(self):
        reply = self.registry.attach(self.attach_payload())
        blob = json.dumps(reply)
        for secret in (NONCE, LIVE_TOKEN, ADDRESS):
            self.assertNotIn(secret, blob)
        error = self.assert_code("UNAUTHORIZED", self.registry.attach, self.attach_payload(nonce="wrong-nonce-OTHERMARKER"))
        error_blob = json.dumps(error.payload())
        self.assertEqual(sorted(error.payload().get("details", {})), ["attemptId"])
        for secret in (NONCE, LIVE_TOKEN, "OTHERMARKER"):
            self.assertNotIn(secret, error_blob)
        frame = WorkerLiveAttach.from_payload(self.attach_payload())
        self.assertNotIn("SECRETMARKER", repr(frame))
        self.assertNotIn(LIVE_TOKEN, repr(frame))


class BoundTests(LiveRegistryTestCase):
    def test_the_binding_map_is_bounded(self):
        with patch.object(live_registry, "MAX_LIVE_BINDINGS", 0):
            self.assert_code("NOT_READY", self.registry.attach, self.attach_payload())


if __name__ == "__main__":
    unittest.main()
