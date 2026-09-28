"""Production parallel dispatch under one total and per-model capacity ceilings.

The service keeps exactly one queue and one worker pool. These tests pin the
production rules of the priority slice:

* command work overlaps up to ``BUDDY_MAX_CONCURRENT`` and the next task queues;
* routing decisions consume the same total ceiling and their fixed model family's
  limit, while model-free command work has no family limit;
* a full family is filtered out before the bounded candidate LIMIT, so more than one
  hundred blocked rows cannot hide a runnable task from another family;
* workspace and exclusive-resource admission and the ``UNRESOLVED_SQL`` slot rule
  continue to apply;
* a fresh private daemon starts the real managed pool, a restart keeps the same
  supervisor IDs and attempts, stop reaches every managed worker, and lowering the
  limits retains a busy surplus supervisor with its receipt and drains it later;
* a retire intent at supervisor startup still replays a pending receipt or reconciles
  a startup intent, replay-only retirement can never claim work, a Worker exception
  under retirement keeps retrying while evidence remains, a missing surplus owner
  with unresolved evidence is retained and replayed, invalid manifest/prefix values
  never become paths, and a vanished desired supervisor is restarted after backoff.

Everything runs offline: the ``command`` adapter executes a local gate fixture and
the deterministic decision helper stands in for the bounded model call. No model is
ever called.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from support import BoardTestCase, wait_for

from buddy import daemon as daemon_module
from buddy.client import BoardClient
from buddy.store import BoardStore
from buddy.worker import supervisor as supervisor_module
from buddy.worker.worker import ReceiptSpool, Worker


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
PROFILE_FAMILY = {"adapter": PROFILE["adapter"], "provider": PROFILE["provider"], "model": PROFILE["model"]}

NONCE = "n" * 32
#: The adapters one generic worker can honestly serve. The real pool advertises the
#: same union through ``local_capabilities``; the fixed set keeps these tests
#: independent of which optional harnesses happen to be installed.
WORKER_CAPABILITIES = ["command", "decision", "dsh"]

#: Environment keys a Buddy process exports for its own work. Tests must never let
#: them leak into a private daemon, its workers or its state root.
INHERITED_BUDDY_KEYS = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)


@contextmanager
def isolated_buddy_environment():
    """Run a block without this process's own Buddy runtime/worker credentials."""
    saved = {key: os.environ.pop(key) for key in INHERITED_BUDDY_KEYS if key in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def lock_is_free(path: Path) -> bool:
    """True when no supervisor holds this lifetime lock (never a PID check)."""
    if not path.exists():
        return True
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


class CapacityTestCase(BoardTestCase):
    """Shared fixtures: the offline decision catalog, helper and claim helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        self.use_decision_helper()

    def use_decision_helper(self, **mode: str) -> None:
        from fixtures import mock_readonly
        mock_readonly.install(self, **mode)

    def seed_evaluation(self, board, *, model_limit: int | None = None) -> dict:
        """Enable one discovered profile through a registered console session."""
        refreshed = board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        grant = board.console_call(
            "evaluation_write_begin", {"requestId": "seed", "expectedRevision": refreshed["tableRevision"], "kind": "human"}
        )
        params = {
            "commandId": "seed-1",
            "writerId": grant["writerId"],
            "generation": grant["generation"],
            "writerToken": grant["writerToken"],
            "expectedRevision": grant["tableRevision"],
            "profileSettings": [{"profileId": PROFILE_ID, "enabled": True}],
            "configuration": {"decisionProfileId": PROFILE_ID},
        }
        if model_limit is not None:
            params["modelConcurrency"] = [{**PROFILE_FAMILY, "limit": model_limit}]
        return board.console_call("user_policy_publish", params)

    def selection(self, board, request_id: str) -> dict:
        return board.call(
            "selection_request", {"requestId": request_id, "task": "pick a configuration for this goal"}
        )

    def business_task(
        self, board, request_id: str, cwd: Path, *, exclusive: list[str] | None = None
    ) -> dict:
        params = {
            "requestId": request_id,
            "task": "business work",
            "cwd": str(cwd),
            "adapter": "command",
            "argv": ["/bin/true"],
        }
        if exclusive:
            params["exclusiveResources"] = list(exclusive)
        return board.client().submit(**params)["task"]

    def register(self, board, worker_id: str) -> None:
        board.client().register_worker(worker_id, adapter="command", capabilities=list(WORKER_CAPABILITIES))

    def claim(self, board, worker_id: str, *, request_id: str = NONCE, run_id: str | None = None) -> dict:
        # Registration is an idempotent upsert; no claim in these tests belongs to an
        # unregistered worker.
        self.register(board, worker_id)
        return board.client().claim(worker_id, request_id, NONCE, task_id=run_id)

    def queue_reason(self, board, run_id: str) -> str:
        return board.call("task_get", {"runId": run_id})["task"]["queueReason"]

    def status(self, board, run_id: str) -> str:
        return board.call("task_get", {"runId": run_id})["task"]["status"]

    # -- real daemon helpers -------------------------------------------------
    def daemon_client(self) -> BoardClient:
        return BoardClient(self.directory, autostart=False)

    def wait_worker_ids(self, client: BoardClient, expected: list[str], timeout: float = 45.0) -> set[str]:
        def registered():
            names = {row["workerId"] for row in client.call("worker_list", {"limit": 50})["workers"]}
            return names if names >= set(expected) else None

        found = wait_for(registered, timeout)
        self.assertIsNotNone(found, f"workers {expected} never registered: {client.call('worker_list', {'limit': 50})}")
        return found or set()

    def gate_task(self, client: BoardClient, name: str, *, cwd: Path | None = None, limit: float = 150.0) -> str:
        """Submit an offline command that runs until its release file appears."""
        started = self.directory / f"{name}.started"
        gate = self.directory / f"{name}.release"
        script = (
            "import pathlib,time\n"
            f"pathlib.Path({str(started)!r}).touch()\n"
            f"gate = pathlib.Path({str(gate)!r}); end = time.monotonic() + {limit}\n"
            "while not gate.exists() and time.monotonic() < end: time.sleep(0.05)\n"
        )
        workdir = cwd or self.workdir(name)
        task = client.submit(
            requestId=name,
            task="hold this attempt open",
            cwd=str(workdir),
            adapter="command",
            argv=[sys.executable, "-c", script],
            timeoutSeconds=300,
        )["task"]
        setattr(self, f"_gate_{name}", gate)
        return task["runId"]

    def release_gate(self, name: str) -> None:
        getattr(self, f"_gate_{name}").touch()

    def running(self, client: BoardClient, run_id: str) -> dict | None:
        view = client.get(runId=run_id)
        return view if view["status"] == "running" else None

    def supervisor_pid(self, worker_id: str) -> int | None:
        path = self.directory / "workers" / worker_id / "supervisor.json"
        try:
            return int(json.loads(path.read_text())["supervisorPid"])
        except (OSError, ValueError, KeyError):
            return None

    def pool_manifest(self) -> dict:
        return json.loads((self.directory / "worker-pool.json").read_text())

    def worker_dir(self, worker_id: str) -> Path:
        return self.directory / "workers" / worker_id

    def retire_file(self, worker_id: str) -> Path:
        return self.worker_dir(worker_id) / "retire.request"

    def write_pool_manifest(self, worker_ids: list, prefix: str = "local") -> None:
        (self.directory / "worker-pool.json").write_text(
            json.dumps({"prefix": prefix, "workerIds": worker_ids})
        )

    def fake_start(self) -> tuple[list, object]:
        """A supervisor spawn that records the request without creating a process."""
        starts: list = []

        def start(handle, state_dir, log_path, retirement=False):
            starts.append((handle.worker_id, retirement))
            return True

        return starts, start


class RetireStartupTests(CapacityTestCase):
    """A restarted surplus owner replays its receipts before it may retire."""

    def _startup_intent(self, worker: Worker, attempt: dict, claim_request_id: str) -> dict:
        intent = {
            "workerId": worker.worker_id,
            "instanceId": worker.instance_id,
            "nonce": NONCE,
            "claimRequestId": claim_request_id,
            "createdAt": "2026-01-01T00:00:00.000Z",
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
        }
        worker.spool.write_startup(intent)
        return intent

    def _receipt(self, attempt: dict, worker_id: str) -> dict:
        return {
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
            "workerId": worker_id,
            "nonce": NONCE,
            "commandId": f"result:{attempt['attemptId']}",
            "report": {
                "status": "ok",
                "result": {"text": "replayed by a retiring surplus owner"},
                "error": None,
                "exitCode": 0,
                "signal": None,
                "shutdownConfirmed": True,
                "artifacts": [],
            },
            "createdAt": "2026-01-01T00:00:00.000Z",
        }

    def test_retire_at_startup_replays_the_pending_receipt_and_never_claims(self):
        board = self.board(max_concurrent=2)
        first = self.business_task(board, "retire-replay", self.workdir("replay"))
        queued = self.business_task(board, "retire-replay-queued", self.workdir("replay-queued"))
        self.register(board, "w-retire")
        claim = self.claim(board, "w-retire", request_id="retire-replay-claim")
        attempt = claim["claim"]["attempt"]
        worker = Worker("w-retire", self.directory, client=board.client())
        # Exactly the durable state a crashed surplus owner leaves behind: a startup
        # intent, a completion receipt, and a retire intent already on disk. The old
        # supervisor skipped Worker creation here, which lost the receipt forever.
        self._startup_intent(worker, attempt, "retire-replay-claim")
        worker.spool.write(attempt["attemptId"], self._receipt(attempt, "w-retire"))
        worker.retire_request_path.write_text(json.dumps({"workerId": "w-retire", "requestedBy": "test"}))
        self.assertEqual(len(worker.recovery_pending()), 2)

        worker.run()

        self.assertEqual(self.status(board, first["runId"]), "completed")
        self.assertTrue(board.call("task_get", {"runId": first["runId"]})["task"]["resultAvailable"])
        self.assertEqual(worker.recovery_pending(), [])
        self.assertEqual(self.status(board, queued["runId"]), "queued", "retirement must never claim new work")

    def test_retire_at_startup_reconciles_a_never_spawned_intent_before_retiring(self):
        board = self.board(max_concurrent=2)
        task = self.business_task(board, "retire-release", self.workdir("release"))
        queued = self.business_task(board, "retire-release-queued", self.workdir("release-queued"))
        worker = Worker("w-release", self.directory, client=board.client(), adapters=("command",))
        self.register(board, "w-release")
        claim = board.client().claim(
            "w-release", "retire-release-claim", NONCE, worker_instance=worker.instance_id
        )
        attempt = claim["claim"]["attempt"]
        self._startup_intent(worker, attempt, "retire-release-claim")
        worker.retire_request_path.write_text(json.dumps({"workerId": "w-release", "requestedBy": "test"}))
        self.assertEqual(len(worker.recovery_pending()), 1)

        worker.run()

        self.assertEqual(worker.recovery_pending(), [])
        view = board.call("task_get", {"runId": task["runId"]})["task"]
        self.assertEqual(view["status"], "failed", "an evidence-based release fails the never-started attempt honestly")
        self.assertTrue(view["shutdownConfirmed"])
        self.assertEqual(self.status(board, queued["runId"]), "queued", "retirement must never claim new work")

    def test_retire_at_startup_keeps_retrying_an_ambiguous_spawn_intent(self):
        board = self.board(max_concurrent=2)
        task = self.business_task(board, "retire-ambiguous", self.workdir("ambiguous"))
        worker = Worker("w-ambiguous", self.directory, client=board.client(), adapters=("command",))
        self.register(board, "w-ambiguous")
        claim = board.client().claim(
            "w-ambiguous", "retire-ambiguous-claim", NONCE, worker_instance=worker.instance_id
        )
        attempt = claim["claim"]["attempt"]
        intent = self._startup_intent(worker, attempt, "retire-ambiguous-claim")
        spawn_intent = worker.spawn_intent_path(intent)
        spawn_intent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        spawn_intent.write_text(json.dumps({"attemptId": attempt["attemptId"], "at": "2026-01-01T00:00:00.000Z"}))
        worker.retire_request_path.write_text(json.dumps({"workerId": "w-ambiguous", "requestedBy": "test"}))

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        try:
            time.sleep(2.5)
            self.assertTrue(
                thread.is_alive(), "a retire intent must not retire past an unreconciled startup intent"
            )
            self.assertEqual(worker.recovery_pending(), [f"startup-intent:{attempt['attemptId']}"])
            # Settling the ambiguous attempt elsewhere lets the next reconciliation
            # clear the intent and only then may the worker retire.
            board.client().release(
                "w-ambiguous",
                attempt["attemptId"],
                attempt["generation"],
                NONCE,
                "test settled the ambiguous attempt",
                worker_instance=worker.instance_id,
            )
            self.assertTrue(
                wait_for(lambda: not thread.is_alive(), timeout=30),
                "the worker never retired after its startup intent reconciled",
            )
        finally:
            worker.stop.set()
            thread.join(timeout=10)
        self.assertEqual(worker.recovery_pending(), [])
        self.assertEqual(self.status(board, task["runId"]), "failed")


class SupervisorRetirementTests(BoardTestCase):
    """A supervisor retries a crashed Worker under retirement while evidence remains."""

    def test_supervisor_keeps_replaying_after_a_worker_exception_until_evidence_settles(self):
        supervisor = supervisor_module.Supervisor("w-retry", self.directory)
        supervisor.retire_request.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        supervisor.retire_request.write_text(json.dumps({"workerId": "w-retry", "requestedBy": "test"}))
        calls = {"runs": 0}

        class FlakyWorker:
            def __init__(self, worker_id, state_dir, **kwargs):
                self.worker_id = worker_id
                self.state_dir = state_dir

            def run(self):
                calls["runs"] += 1
                if calls["runs"] < 3:
                    raise RuntimeError("board unreachable while replaying")

            def recovery_pending(self):
                return ["pending-receipts"] if calls["runs"] < 3 else []

        with (
            patch.object(supervisor_module, "Worker", FlakyWorker),
            patch.object(supervisor_module, "RESTART_BACKOFF_SECONDS", 0.01),
            patch.object(supervisor_module, "MAX_BACKOFF_SECONDS", 0.02),
        ):
            self.assertEqual(supervisor.serve(), 0)

        self.assertEqual(calls["runs"], 3, "a Worker exception must not abandon unreconciled evidence")
        status = json.loads((self.directory / "workers" / "w-retry" / "supervisor.json").read_text())
        self.assertEqual(status["state"], "retired")


class TotalCapacityTests(CapacityTestCase):
    """Two independent attempts consume the whole machine ceiling."""

    def test_two_business_attempts_overlap_and_the_third_reports_capacity(self):
        board = self.board()
        self.assertEqual(board.store.max_concurrent, 2)
        first = self.business_task(board, "biz-1", self.workdir("a"))
        second = self.business_task(board, "biz-2", self.workdir("b"))
        for worker_id in ("w1", "w2", "w3"):
            self.register(board, worker_id)

        claimed = self.claim(board, "w1", request_id="claim-1")
        self.assertEqual(claimed["claim"]["task"]["runId"], first["runId"])
        claimed_two = self.claim(board, "w2", request_id="claim-2")
        self.assertEqual(claimed_two["claim"]["task"]["runId"], second["runId"])
        self.assertNotEqual(
            claimed["claim"]["attempt"]["attemptId"], claimed_two["claim"]["attempt"]["attemptId"]
        )
        # Two distinct workers now own two unresolved attempts at once.
        busy = board.store.capacity_report()
        self.assertEqual(busy, {"totalLimit": 2, "totalActive": 2, "models": []})

        # A third business task is admitted and states the real reason it waits.
        third = self.business_task(board, "biz-3", self.workdir("c"))
        self.assertEqual(self.queue_reason(board, third["runId"]), "capacity")
        refused = self.claim(board, "w3", request_id="claim-3")
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "capacity")

    def test_workspace_and_exclusive_resources_stay_serialized_with_free_capacity(self):
        board = self.board(max_concurrent=3)
        holder = self.business_task(board, "res-holder", self.workdir("repo"))
        nested = self.business_task(board, "res-nested", self.workdir("repo/nested"))
        exclusive_one = self.business_task(board, "res-exclusive-1", self.workdir("e1"), exclusive=["gpu:0"])
        for worker_id in ("w1", "w2", "w3"):
            self.register(board, worker_id)
        self.claim(board, "w1", request_id="claim-holder", run_id=holder["runId"])

        # A second slot is free, but the workspace overlap still refuses it.
        refused = self.claim(board, "w2", request_id="claim-nested", run_id=nested["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "cwd-overlap")

        claimed = self.claim(board, "w2", request_id="claim-exclusive-1", run_id=exclusive_one["runId"])
        self.assertIsNotNone(claimed["claim"])
        exclusive_two = self.business_task(board, "res-exclusive-2", self.workdir("e2"), exclusive=["gpu:0"])
        self.assertEqual(self.queue_reason(board, exclusive_two["runId"]), "exclusive-resource")
        refused = self.claim(board, "w3", request_id="claim-exclusive-2", run_id=exclusive_two["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "exclusive-resource")


class ModelFamilyFairnessTests(CapacityTestCase):
    """A full model family cannot hide other eligible work."""

    def test_decision_waits_for_total_capacity_then_completes(self):
        board = self.board(max_concurrent=2)
        self.seed_evaluation(board, model_limit=2)
        first = self.business_task(board, "hold-1", self.workdir("h1"))
        second = self.business_task(board, "hold-2", self.workdir("h2"))
        self.register(board, "w1")
        self.register(board, "w2")
        self.claim(board, "w1", request_id="hold-claim-1")
        self.claim(board, "w2", request_id="hold-claim-2")

        selection = self.selection(board, "pick-while-busy")
        self.assertEqual(selection["status"], "queued")
        self.assertEqual(self.queue_reason(board, selection["runId"]), "capacity")
        refused = self.claim(board, "w-decision", request_id="full-total", run_id=selection["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "capacity")
        # Release one command attempt so the real Worker and helper can finish the
        # routing decision while the other command attempt stays unresolved.
        held = board.call("task_get", {"runId": first["runId"]})["task"]
        board.client().release("w1", held["selectedAttemptId"], held["attemptGeneration"], NONCE, "test release")
        worker = Worker("w-decision", self.directory, client=board.client(), adapters=("decision",))
        worker.register()
        self.assertEqual(worker.run_once(), "ran")
        decision = board.call("selection_get", {"decisionId": selection["decisionId"], "includeAudit": True})["decision"]
        self.assertEqual(decision["status"], "completed", decision.get("reason"))
        self.assertEqual(decision["profileId"], PROFILE_ID)
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalActive"], 1)
        self.assertIn({**PROFILE_FAMILY, "limit": 2, "active": 0}, capacity["models"])
        self.assertEqual(self.status(board, first["runId"]), "failed")
        self.assertEqual(self.status(board, second["runId"]), "running")

    def test_model_free_command_progresses_while_decision_family_is_full(self):
        board = self.board(max_concurrent=3)
        self.seed_evaluation(board, model_limit=1)
        first = self.selection(board, "pick-hold")
        self.register(board, "w-decision")
        self.claim(board, "w-decision", request_id="decision-claim", run_id=first["runId"])
        self.assertEqual(board.store.capacity_report()["models"][0]["active"], 1)

        # A model-free task can claim another total slot.
        business = self.business_task(board, "biz-free", self.workdir("free"))
        self.register(board, "w-business")
        claimed = self.claim(board, "w-business", request_id="business-claim")
        self.assertEqual(claimed["claim"]["task"]["runId"], business["runId"])

        second = self.selection(board, "pick-queued")
        refused = self.claim(board, "w-decision-2", request_id="decision-claim-2", run_id=second["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "model-capacity")
        self.assertEqual(self.queue_reason(board, second["runId"]), "model-capacity")

    def test_more_than_hundred_full_family_rows_cannot_hide_other_work(self):
        board = self.board(max_concurrent=3)
        self.seed_evaluation(board, model_limit=1)
        held = self.selection(board, "decision-hold")
        self.register(board, "w-decision")
        self.claim(board, "w-decision", request_id="decision-hold-claim", run_id=held["runId"])
        blocked_run_id = None
        for index in range(120):
            queued = self.selection(board, f"decision-blocked-{index}")
            blocked_run_id = queued["runId"]
        refused = self.claim(board, "w-blocked", request_id="full-family", run_id=blocked_run_id)
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "model-capacity")
        business = self.business_task(board, "runnable-business", self.workdir("runnable"))

        self.register(board, "w-business")
        claimed = self.claim(board, "w-business", request_id="runnable-business-claim")
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(claimed["claim"]["task"]["runId"], business["runId"])


class UncertainCapacityTests(CapacityTestCase):
    """An uncertain attempt keeps its total and model family capacity."""

    def test_uncertain_attempts_keep_their_total_and_family_slots(self):
        board = self.board(max_concurrent=2)
        self.seed_evaluation(board, model_limit=1)
        selection_one = self.selection(board, "uncertain-pick-1")
        self.register(board, "w-decision")
        self.claim(board, "w-decision", request_id="uncertain-pick-claim", run_id=selection_one["runId"])
        self.assertEqual(board.store.reconcile_startup()["uncertain"], 1)
        self.assertEqual(
            board.call("task_get", {"runId": selection_one["runId"]})["task"]["attemptState"], "uncertain"
        )
        business_one = self.business_task(board, "uncertain-biz-1", self.workdir("u1"))
        business_two = self.business_task(board, "uncertain-biz-2", self.workdir("u2"))
        self.register(board, "w-biz")
        self.claim(board, "w-biz", request_id="uncertain-biz-claim", run_id=business_one["runId"])
        selection_two = self.selection(board, "uncertain-pick-2")
        self.assertEqual(self.queue_reason(board, selection_two["runId"]), "capacity")
        refused = self.claim(
            board, "w-decision-2", request_id="uncertain-pick-claim-2", run_id=selection_two["runId"]
        )
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "capacity")
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalActive"], 2)
        self.assertEqual(capacity["models"][0]["active"], 1)

        # Release the unspawned command attempt. The selector's uncertain family
        # claim must still hold its own quota even though a total slot is free.
        held = board.call("task_get", {"runId": business_one["runId"]})["task"]
        board.client().release("w-biz", held["selectedAttemptId"], held["attemptGeneration"], NONCE, "test release")
        refused = self.claim(board, "w-decision-3", request_id="uncertain-pick-claim-3", run_id=selection_two["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "model-capacity")
        self.assertEqual(self.queue_reason(board, selection_two["runId"]), "model-capacity")
        claimed = self.claim(board, "w-biz-2", request_id="uncertain-biz-claim-2", run_id=business_two["runId"])
        self.assertEqual(claimed["claim"]["task"]["runId"], business_two["runId"])


class HealthShapeTests(CapacityTestCase):
    """Health reports the single ceiling and model observations."""

    def test_health_reports_total_limit_and_managed_worker_ids(self):
        board = self.board(max_concurrent=3)
        health = board.call("health", {})
        self.assertEqual(health["maxConcurrent"], 3)
        self.assertEqual(
            health["capacity"],
            {
                "totalLimit": 3,
                "totalActive": 0,
                "models": [],
            },
        )
        # In-process resources have no daemon-managed pool and say so honestly.
        self.assertEqual(health["managedWorkerIds"], [])
        self.assertEqual(health["surplusWorkerIds"], [])


class RetireRaceTests(CapacityTestCase):
    """A scale-down intent must never cancel an attempt that raced it."""

    def test_a_retire_intent_written_during_an_attempt_never_cancels_it(self):
        board = self.board(max_concurrent=2)
        task = self.gate_task(board.client(), "retire-race")
        worker = Worker("w-race", self.directory, client=board.client())
        worker.register()
        outcome: dict = {}

        def run() -> None:
            outcome["result"] = worker.run()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: self.status(board, task) == "running", timeout=40), "the attempt never ran")

        # Exactly what a bounded reconcile can produce when its idle check races the
        # claim: the retire intent appears while the child is already executing.
        worker.retire_request_path.write_text(json.dumps({"workerId": "w-race", "requestedBy": "test-race"}))
        time.sleep(2.5)
        view = board.call("task_get", {"runId": task})["task"]
        self.assertEqual(view["status"], "running", "a retire intent must never cancel an owned child")
        self.assertFalse(view["cancelRequested"])
        self.assertFalse((worker.spool.directory / "stop.request").exists())

        # The child finishes, the receipt is delivered, and only then does the worker
        # retire: the scale-down loses neither the attempt nor its receipt.
        self.release_gate("retire-race")
        self.assertTrue(wait_for(lambda: self.status(board, task) == "completed", timeout=60))
        self.assertTrue(board.call("task_get", {"runId": task})["task"]["resultAvailable"])
        self.assertTrue(wait_for(lambda: not thread.is_alive(), timeout=30), "the worker did not retire")
        self.assertTrue(worker.retire_requested())
        self.assertEqual(board.store.active_work()["attempts"], [])


class WorkerPoolManifestTests(CapacityTestCase):
    """The pool owns exactly the IDs it recorded, and reclaims a wanted slot."""

    def _hold_lock(self, worker_id: str, held: list[int]) -> int:
        path = self.worker_dir(worker_id) / "supervisor.lock"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fd)
        return fd

    def _release_lock(self, fd: int, held: list[int]) -> None:
        if fd in held:
            held.remove(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

    def test_manifest_ownership_and_scale_up_reclaim(self):
        board = self.board()
        held: list[int] = []
        self.addCleanup(lambda: [self._release_lock(fd, held) for fd in list(held)])
        # A previous, larger pool left three recorded slots and a scale-down intent;
        # a hand-made worker merely named like a pool member is not ours.
        busy = self._hold_lock("local-3", held)
        self._hold_lock("local-99", held)
        self.retire_file("local-3").write_text(json.dumps({"workerId": "local-3", "requestedBy": "daemon-pool"}))
        (self.directory / "worker-pool.json").write_text(
            json.dumps({"prefix": "local", "workerIds": ["local", "local-2", "local-3"]})
        )
        spawned: list[str] = []

        def fake_start(handle, state_dir, log_path):
            # Keep the real lock/withdraw contract; only the process spawn is recorded.
            if handle.running():
                handle.withdraw_retire()
                return False
            spawned.append(handle.worker_id)
            return True

        pool = daemon_module.WorkerPool(self.directory, prefix="local", total_limit=3)
        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=fake_start):
            pool.start()
            self.assertEqual(sorted(spawned), ["local", "local-2"], "only desired slots may be started")
            # Withdrawing the intent keeps the wanted slot reclaimable: the supervisor
            # may already have observed the retire and be exiting.
            self.assertFalse(self.retire_file("local-3").exists())
            self.assertIn("local-3", pool._reclaimable)
            # The supervisor exits after the withdraw; only the manifest record remains.
            self._release_lock(busy, held)
            report = pool.reconcile(board.store)
        self.assertIn("local-3", spawned, "a wanted slot that disappeared must be reclaimed")
        self.assertEqual(report["workerIds"], ["local", "local-2", "local-3"])
        self.assertEqual(report["surplusWorkerIds"], [])
        self.assertFalse(self.retire_file("local-99").exists())
        self.assertFalse((self.worker_dir("local-99") / "stop.request").exists())
        self.assertEqual(self.pool_manifest()["workerIds"], ["local", "local-2", "local-3"])


class SurplusRetentionTests(CapacityTestCase):
    """A missing surplus owner is never forgotten while evidence remains."""

    def test_missing_surplus_with_pending_receipt_is_retained_and_replayed_in_retirement_mode(self):
        board = self.board()
        self.write_pool_manifest(["local", "local-2", "local-3"])
        spool = ReceiptSpool(self.directory, "local-3")
        spool.write(
            "attempt-crash",
            {
                "attemptId": "attempt-crash",
                "taskId": "task-crash",
                "generation": 1,
                "workerId": "local-3",
                "nonce": NONCE,
                "commandId": "result:attempt-crash",
                "report": {
                    "status": "ok",
                    "result": {},
                    "error": None,
                    "exitCode": 0,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                },
                "createdAt": "2026-01-01T00:00:00.000Z",
            },
        )
        starts, start = self.fake_start()
        pool = daemon_module.WorkerPool(self.directory, prefix="local", total_limit=2)
        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=start):
            for _ in range(3):
                # Clear both backoffs: the point is that the manifest entry and the
                # replay request survive lock-free reconciles, not timing.
                pool._spawn_backoff.clear()
                pool._replay_backoff.clear()
                report = pool.reconcile(board.store)

        self.assertEqual(
            self.pool_manifest()["workerIds"],
            ["local", "local-2", "local-3"],
            "a non-running surplus with recoverable evidence must never be forgotten",
        )
        retained = {row["workerId"]: row for row in report["surplusRetained"]}
        self.assertIn("local-3", retained)
        self.assertFalse(retained["local-3"]["running"])
        self.assertEqual(retained["local-3"]["pendingReceipts"], 1)
        self.assertEqual(retained["local-3"]["startupIntents"], 0)
        self.assertTrue(retained["local-3"]["replayable"])
        self.assertEqual(report["surplusWorkerIds"], [], "a missing supervisor is not reported as running")
        self.assertIn(("local-3", True), starts, "the retained evidence was never replayed in retirement mode")
        self.assertTrue(self.retire_file("local-3").is_file())
        self.assertFalse(
            (self.worker_dir("local-3") / "stop.request").exists(),
            "a retained surplus is drained with a retire intent, never stopped",
        )

    def test_db_only_unresolved_surplus_is_retained_without_speculative_restarts(self):
        board = self.board()
        task = self.business_task(board, "db-only", self.workdir("db-only"))
        self.register(board, "local-3")
        claim = self.claim(board, "local-3", request_id="db-only-claim", run_id=task["runId"])
        self.assertIsNotNone(claim["claim"])
        self.write_pool_manifest(["local", "local-2", "local-3"])
        starts, start = self.fake_start()
        pool = daemon_module.WorkerPool(self.directory, prefix="local", total_limit=2)
        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=start):
            for _ in range(3):
                pool._spawn_backoff.clear()
                pool._replay_backoff.clear()
                report = pool.reconcile(board.store)

        retained = {row["workerId"]: row for row in report["surplusRetained"]}
        self.assertIn("local-3", retained)
        self.assertFalse(retained["local-3"]["running"])
        self.assertEqual(retained["local-3"]["unresolvedAttempts"], 1)
        self.assertFalse(
            retained["local-3"]["replayable"],
            "a DB-only unresolved attempt has no child handle and no local replay path",
        )
        self.assertNotIn(("local-3", True), starts, "a doomed replay-only supervisor must not be launched")
        self.assertNotIn(("local-3", False), starts)
        self.assertEqual(
            self.pool_manifest()["workerIds"],
            ["local", "local-2", "local-3"],
            "missing process is never evidence that the attempt stopped",
        )
        self.assertEqual(report["surplusWorkerIds"], [])

    def test_an_explicit_stop_is_honored_and_the_slot_is_reported_unavailable(self):
        board = self.board()
        pool = daemon_module.WorkerPool(self.directory, prefix="local", total_limit=2)
        stop_path = self.worker_dir("local-2") / "stop.request"
        stop_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stop_path.write_text(json.dumps({"workerId": "local-2", "requestedBy": "cli"}))
        starts, start = self.fake_start()
        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=start):
            first = pool.reconcile(board.store)
            self.assertEqual(first["stoppedWorkerIds"], ["local-2"])
            self.assertIn("local-2", first["unstartedWorkerIds"])
            for _ in range(3):
                pool._spawn_backoff.clear()
                report = pool.reconcile(board.store)
            self.assertEqual(report["stoppedWorkerIds"], ["local-2"], "the stop must survive every sweep")
            self.assertNotIn(
                ("local-2", False), starts, "an explicitly stopped slot must never be silently resurrected"
            )
            # A deliberate worker-start clears the durable request before spawning.
            stop_path.unlink()
            starts.clear()
            report = pool.reconcile(board.store)
        self.assertEqual(report["stoppedWorkerIds"], [])
        self.assertIn(("local-2", False), starts)

    def test_invalid_manifest_values_and_prefix_never_become_paths(self):
        self.write_pool_manifest(
            ["local", 7, None, {"path": "../evil"}, "../evil", ".", "..", ["local-2"], "local-2", "a/b"]
        )
        pool = daemon_module.WorkerPool(self.directory, prefix="..", total_limit=2)
        self.assertEqual(pool.worker_ids, ["local", "local-2"])
        self.assertEqual(pool.prefix, "local", "an invalid configured prefix falls back to the default")
        self.assertFalse(pool.configured_prefix_valid)
        self.assertEqual(pool.managed_ids(), ["local", "local-2"], "no JSON value is stringified into a pool id")
        self.assertEqual(pool.report()["prefix"], "local")

        observed: list = []

        def start(handle, state_dir, log_path, retirement=False):
            observed.append(json.loads((self.directory / "worker-pool.json").read_text())["workerIds"])
            return True

        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=start):
            pool.start()

        self.assertEqual(
            observed[0], ["local", "local-2"], "exact ownership must be durable before the first spawn"
        )
        self.assertEqual(self.pool_manifest()["workerIds"], ["local", "local-2"])
        self.assertFalse((self.directory / "evil").exists(), "a malformed id escaped onto the filesystem")
        self.assertEqual(
            [path.name for path in (self.directory / "workers").glob("*") if path.name not in ("local", "local-2")],
            [],
            "only validated managed ids may create worker directories",
        )
        for worker_id in pool.managed_ids():
            resolved = (self.directory / "workers" / worker_id).resolve()
            self.assertEqual(resolved.parent, (self.directory / "workers").resolve(), worker_id)

    def test_missing_desired_slot_is_restarted_after_backoff_without_duplicate_spawns(self):
        board = self.board()
        pool = daemon_module.WorkerPool(self.directory, prefix="local", total_limit=2)
        starts, start = self.fake_start()
        with patch.object(daemon_module.SupervisorHandle, "start", autospec=True, side_effect=start):
            first = pool.reconcile(board.store)
            self.assertEqual(sorted(first["unstartedWorkerIds"]), ["local", "local-2"])
            self.assertEqual(sorted(worker_id for worker_id, _ in starts), ["local", "local-2"])
            pool.reconcile(board.store)
            self.assertEqual(len(starts), 2, "a recent spawn attempt must not be duplicated inside the backoff")
            pool._spawn_backoff.clear()
            pool.reconcile(board.store)
        self.assertEqual(
            [worker_id for worker_id, _ in starts[2:]], ["local", "local-2"], "desired slots are retried"
        )
        self.assertEqual(len(starts), 4, "a still-missing desired slot must be retried after the backoff")
        self.assertEqual(self.pool_manifest()["workerIds"], ["local", "local-2"])


class DaemonPoolTests(CapacityTestCase):
    """The real daemon-managed pool: start, overlap, decision, restart, stop, drain."""

    def test_fresh_private_daemon_launches_the_pool_and_overlaps_two_business_attempts(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env):
            client = self.daemon_client()
            health = client.health()
            self.assertEqual(health["maxConcurrent"], 2)
            self.assertEqual(
                health["capacity"],
                {"totalLimit": 2, "totalActive": 0, "models": []},
            )
            self.assertEqual(health["managedWorkerIds"], ["local", "local-2"])
            self.assertEqual(health["surplusWorkerIds"], [])
            self.wait_worker_ids(client, ["local", "local-2"])

            first = self.gate_task(client, "overlap-1")
            second = self.gate_task(client, "overlap-2")

            def both_running():
                views = [self.running(client, first), self.running(client, second)]
                return views if all(views) else None

            both = wait_for(both_running, timeout=60)
            self.assertIsNotNone(both, "two business attempts never overlapped")
            views = [client.get(runId=first), client.get(runId=second)]
            claimed_workers = {view["workerId"] for view in views}
            self.assertEqual(len(claimed_workers), 2)
            self.assertLessEqual(claimed_workers, set(health["managedWorkerIds"]))
            self.assertEqual(len({view["selectedAttemptId"] for view in views}), 2)
            self.assertEqual(client.health()["capacity"]["totalActive"], 2)

            third = self.gate_task(client, "overlap-3")
            self.assertEqual(self.queue_reason(client, third), "capacity")
            self.assertIsNone(client.get(runId=third).get("workerId"))

            self.release_gate("overlap-1")
            self.release_gate("overlap-2")
            for run_id in (first, second):
                self.assertTrue(
                    wait_for(lambda: client.get(runId=run_id)["status"] == "completed", timeout=90),
                    f"{run_id} never completed",
                )
            self.assertTrue(wait_for(lambda: self.running(client, third), timeout=60), "the queued third never started")
            self.release_gate("overlap-3")
            self.assertTrue(wait_for(lambda: client.get(runId=third)["status"] == "completed", timeout=90))

    def readonly_bootstrap_path(self):
        # Test-only Python startup instrumentation reaches the independent daemon
        # and supervisors without adding a production capability override.
        root = self.directory / 'mock-native-startup'
        root.mkdir()
        (root / 'sitecustomize.py').write_text(
            'from contextlib import ExitStack\nfrom types import SimpleNamespace\n'
            'from fixtures.mock_readonly import install\n'
            '_patches = ExitStack()\n_fixture = SimpleNamespace(enterContext=_patches.enter_context)\n'
            'install(_fixture)\n'
            'import os\nfrom buddy import runtime\n_original = runtime.launch_target\n'
            'def _launch(*args, **kwargs):\n'
            '    target = _original(*args, **kwargs)\n'
            '    return {**target, "pythonPath": os.environ["PYTHONPATH"]}\n'
            'runtime.launch_target = _launch\n')
        return os.pathsep.join([str(root), str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[2] / 'src')])

    def test_decision_completes_on_the_pool_with_a_free_total_slot(self):
        catalog = self.catalog_fixture()
        env = {
            "BUDDY_WORKER_ID": "local",
            "BUDDY_MAX_CONCURRENT": "3",
            "BUDDY_MODEL_CATALOG_FILE": str(catalog),
            "PYTHONPATH": self.readonly_bootstrap_path(),
        }
        with isolated_buddy_environment(), self.daemon(env=env):
            client = self.daemon_client()
            self.wait_worker_ids(client, ["local", "local-2", "local-3"])
            first = self.gate_task(client, "decision-1")
            second = self.gate_task(client, "decision-2")

            def both_running():
                views = [self.running(client, first), self.running(client, second)]
                return views if all(views) else None

            both = wait_for(both_running, timeout=60)
            self.assertIsNotNone(both, "two business attempts never overlapped")

            refreshed = client.call("model_catalog_refresh", {"requestId": "pool-catalog"})
            from test_console import Browser
            opened = client.call("console", {"action": "open"})
            browser = Browser(opened["url"])
            csrf = browser.bootstrap()["csrfToken"]
            status, _headers, body = browser.command("evaluation_write_begin", {
                "requestId": "pool-seed", "expectedRevision": refreshed["tableRevision"], "kind": "human",
            }, csrf=csrf)
            self.assertEqual(status, 200, body)
            grant = json.loads(body)["result"]
            status, _headers, body = browser.command("user_policy_publish", {
                "commandId": "pool-seed-1", "writerId": grant["writerId"],
                "generation": grant["generation"], "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                "profileSettings": [{"profileId": PROFILE_ID, "enabled": True}],
                "configuration": {"decisionProfileId": PROFILE_ID},
                "modelConcurrency": [{**PROFILE_FAMILY, "limit": 2}],
            }, csrf=csrf)
            self.assertEqual(status, 200, body)
            selection = client.call("selection_request", {"requestId": "pool-pick", "task": "pick while busy"})
            self.assertEqual(selection["status"], "queued")

            def terminal():
                view = client.call("selection_get", {"decisionId": selection["decisionId"]})["decision"]
                return view if view["status"] not in ("queued", "running") else None

            decision = wait_for(terminal, timeout=90)
            self.assertIsNotNone(decision, "the decision never reached a terminal state")
            self.assertEqual(decision["status"], "completed", decision.get("reason"))
            self.assertEqual(decision["profileId"], PROFILE_ID)
            capacity = client.health()["capacity"]
            self.assertEqual(capacity["totalLimit"], 3)
            self.assertEqual(capacity["totalActive"], 2)
            self.assertIn({**PROFILE_FAMILY, "limit": 2, "active": 0}, capacity["models"])
            self.release_gate("decision-1")
            self.release_gate("decision-2")

    def test_restart_preserves_pool_ids_and_the_same_attempt(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env) as first:
            client = self.daemon_client()
            worker_ids = client.health()["managedWorkerIds"]
            self.assertEqual(worker_ids, ["local", "local-2"])
            self.wait_worker_ids(client, worker_ids)

            def pids_published():
                values = [self.supervisor_pid(worker_id) for worker_id in worker_ids]
                return values if all(values) else None

            self.assertIsNotNone(wait_for(pids_published, timeout=30), "every pool supervisor must publish its status")
            pids = {worker_id: self.supervisor_pid(worker_id) for worker_id in worker_ids}
            task = self.gate_task(client, "restart-hold")
            live = wait_for(lambda: self.running(client, task), timeout=60)
            self.assertIsNotNone(live, "the attempt never started")
            view = client.get(runId=task)
            attempt_id, worker_id, generation = (
                view["selectedAttemptId"],
                view["workerId"],
                view["attemptGeneration"],
            )
            restart = client.call("service_control", {"action": "restart", "drainSeconds": 1})
            self.assertTrue(restart["restarting"])
            self.assertTrue(restart["workersPreserved"])
            first.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=env) as second:
            self.assertIsNotNone(second.pid)
            client = self.daemon_client()
            self.assertEqual(client.health()["managedWorkerIds"], worker_ids)
            for pool_worker in worker_ids:
                self.assertEqual(
                    self.supervisor_pid(pool_worker),
                    pids[pool_worker],
                    "a restart must reuse the running supervisor, never spawn a duplicate",
                )
            surviving = client.get(runId=task)
            self.assertEqual(surviving["selectedAttemptId"], attempt_id, "the same attempt must survive the restart")
            self.assertEqual(surviving["workerId"], worker_id)
            self.assertEqual(surviving["attemptGeneration"], generation)
            self.assertEqual(surviving["status"], "running")
            self.release_gate("restart-hold")
            completed = wait_for(lambda: client.get(runId=task)["status"] == "completed", timeout=90)
            self.assertTrue(completed, "the surviving worker must deliver the same attempt")
            self.assertTrue(client.get(runId=task)["resultAvailable"])

    def test_stop_reaches_every_managed_worker_and_confirms_shutdown(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env) as process:
            client = self.daemon_client()
            worker_ids = client.health()["managedWorkerIds"]
            self.assertEqual(worker_ids, ["local", "local-2"])
            self.wait_worker_ids(client, worker_ids)
            task = self.gate_task(client, "stop-hold")
            self.assertTrue(wait_for(lambda: self.running(client, task), timeout=60))
            reply = client.call("service_control", {"action": "stop", "drainSeconds": 20, "reason": "test stop"})
            self.assertTrue(reply["stopped"], reply)
            self.assertEqual(reply["unresolvedAttempts"], [])
            self.assertEqual(sorted(reply["workersAskedToStop"]), sorted(worker_ids))
            for worker_id in worker_ids:
                self.assertTrue(
                    (self.directory / "workers" / worker_id / "stop.request").is_file(),
                    f"pool worker {worker_id} never received a stop request",
                )
            process.wait(timeout=30)

        drained = wait_for(
            lambda: all(
                lock_is_free(self.directory / "workers" / worker_id / "supervisor.lock")
                for worker_id in worker_ids
            ),
            timeout=40,
        )
        self.assertTrue(drained, "a managed supervisor did not confirm shutdown")
        view = BoardStore(self.directory).task_get({"runId": task})["task"]
        self.assertEqual(view["status"], "cancelled")
        self.assertTrue(view["shutdownConfirmed"], "the cancelled attempt must have confirmed shutdown")

    def test_lowering_limits_retains_a_busy_surplus_worker_and_drains_it_later(self):
        high = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "5"}
        low = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "1"}
        pool_high = ["local", "local-2", "local-3", "local-4", "local-5"]
        with isolated_buddy_environment(), self.daemon(env=high) as first:
            client = self.daemon_client()
            self.wait_worker_ids(client, pool_high)
            tasks = [self.gate_task(client, f"surplus-{index}") for index in range(3)]

            def all_running():
                views = [self.running(client, run_id) for run_id in tasks]
                return views if all(views) else None

            self.assertIsNotNone(wait_for(all_running, timeout=90), "three business attempts never overlapped")
            attempts = {
                client.get(runId=run_id)["workerId"]: client.get(runId=run_id)["selectedAttemptId"]
                for run_id in tasks
            }
            self.assertEqual(len(set(attempts)), 3)
            self.assertEqual(self.pool_manifest()["workerIds"], pool_high)
            restart = client.call("service_control", {"action": "restart", "drainSeconds": 1})
            self.assertTrue(restart["workersPreserved"])
            first.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=low) as second:
            client = self.daemon_client()
            health = client.health()
            self.assertEqual(health["managedWorkerIds"], ["local"])
            surplus_ids = pool_high[1:]
            retained = {row["workerId"]: row for row in health["surplusRetained"]}
            busy_surplus = [worker_id for worker_id in surplus_ids if worker_id in attempts]
            idle_surplus = [worker_id for worker_id in surplus_ids if worker_id not in attempts]
            self.assertTrue(busy_surplus, f"a surplus worker must own an attempt: {attempts}")
            self.assertTrue(idle_surplus, f"a surplus worker must be idle: {attempts}")
            self.assertTrue(set(busy_surplus).issubset(health["surplusWorkerIds"]))
            self.assertEqual(set(busy_surplus), set(retained), health)
            for busy in busy_surplus:
                self.assertEqual(retained[busy]["attemptId"], attempts[busy])
            # Idle surplus workers retire, while busy ones keep their slots, attempts
            # and receipt until its work settles. Lowering cancelled nothing.
            self.assertTrue(
                wait_for(
                    lambda: all(lock_is_free(self.worker_dir(worker_id) / "supervisor.lock") for worker_id in idle_surplus),
                    timeout=40,
                ),
                "idle surplus supervisors were never drained",
            )
            for idle in idle_surplus:
                self.assertTrue(self.retire_file(idle).is_file(), "scale-down must use the retire intent")
                self.assertFalse((self.worker_dir(idle) / "stop.request").exists())
            for busy in busy_surplus:
                self.assertFalse(lock_is_free(self.worker_dir(busy) / "supervisor.lock"))
            for run_id in tasks:
                view = client.get(runId=run_id)
                self.assertEqual(view["status"], "running")
                self.assertEqual(view["selectedAttemptId"], attempts[view["workerId"]])

            # The exact race the bounded reconcile can hit: the intent lands while the
            # busy surplus is executing. It must neither cancel the child nor drop the
            # receipt — the worker finishes the attempt and retires afterwards.
            for busy in busy_surplus:
                self.retire_file(busy).write_text(json.dumps({"workerId": busy, "requestedBy": "test-race"}))
            time.sleep(2.5)
            for run_id in tasks:
                self.assertEqual(client.get(runId=run_id)["status"], "running")
            for index in range(3):
                self.release_gate(f"surplus-{index}")
            for run_id in tasks:
                self.assertTrue(
                    wait_for(lambda: client.get(runId=run_id)["status"] == "completed", timeout=90),
                    f"{run_id} did not deliver its receipt after the limit was lowered",
                )
                self.assertTrue(client.get(runId=run_id)["resultAvailable"])
            self.assertTrue(
                wait_for(
                    lambda: all(lock_is_free(self.worker_dir(worker_id) / "supervisor.lock") for worker_id in busy_surplus),
                    timeout=40,
                ),
                "busy surplus supervisors never drained after delivering",
            )
            restart = client.call("service_control", {"action": "restart", "drainSeconds": 1})
            self.assertTrue(restart["restarting"])
            second.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=low) as third:
            self.assertIsNotNone(third.pid)
            client = self.daemon_client()
            self.assertEqual(client.health()["managedWorkerIds"], ["local"])
            for worker_id in ("local",):
                self.assertFalse(
                    lock_is_free(self.worker_dir(worker_id) / "supervisor.lock"),
                    f"{worker_id} is a configured slot and must stay running",
                )

    def test_scale_up_withdraws_the_retire_intent_and_restores_desired_slots(self):
        high = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "3"}
        low = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "1"}
        pool_high = ["local", "local-2", "local-3"]
        with isolated_buddy_environment(), self.daemon(env=high) as first:
            client = self.daemon_client()
            self.wait_worker_ids(client, pool_high)
            client.call("service_control", {"action": "restart", "drainSeconds": 1})
            first.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=low) as second:
            client = self.daemon_client()
            self.assertTrue(
                wait_for(
                    lambda: all(
                        lock_is_free(self.worker_dir(worker_id) / "supervisor.lock")
                        for worker_id in ("local-2", "local-3")
                    ),
                    timeout=40,
                ),
                "the surplus supervisors were never retired",
            )
            client.call("service_control", {"action": "restart", "drainSeconds": 1})
            second.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=high) as third:
            client = self.daemon_client()
            self.assertEqual(client.health()["managedWorkerIds"], pool_high)
            self.wait_worker_ids(client, pool_high)
            for worker_id in pool_high:
                self.assertFalse(
                    lock_is_free(self.worker_dir(worker_id) / "supervisor.lock"),
                    f"{worker_id} is wanted again and must be running",
                )
                self.assertFalse(
                    self.retire_file(worker_id).is_file(),
                    f"a stale scale-down intent for {worker_id} must be withdrawn",
                )
            self.assertTrue(
                wait_for(
                    lambda: self.supervisor_pid("local-2") is not None
                    and self.supervisor_pid("local-3") is not None,
                    timeout=30,
                ),
                "the restored supervisors never published their status",
            )

    def test_a_desired_slot_that_disappears_is_restarted_by_the_pool(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env):
            client = self.daemon_client()
            self.wait_worker_ids(client, ["local", "local-2"])
            self.assertTrue(wait_for(lambda: self.supervisor_pid("local-2") is not None, timeout=30))
            original_pid = self.supervisor_pid("local-2")
            # An exact-ID stop removes that supervisor; the pool must treat the
            # configured slot as missing rather than as capacity that is gone.
            code, stopped = self.cli("worker-stop", json.dumps({"workerId": "local-2"}))
            self.assertEqual(code, 0, stopped)
            self.assertTrue(
                wait_for(lambda: lock_is_free(self.worker_dir("local-2") / "supervisor.lock"), timeout=40),
                "the stopped supervisor never released its lifetime lock",
            )
            # A crash leaves no stop request; removing the explicit one leaves exactly
            # the durable state a crashed supervisor leaves behind.
            (self.worker_dir("local-2") / "stop.request").unlink(missing_ok=True)
            restarted = wait_for(
                lambda: self.supervisor_pid("local-2") not in (None, original_pid)
                and not lock_is_free(self.worker_dir("local-2") / "supervisor.lock"),
                timeout=60,
            )
            self.assertTrue(restarted, "the pool never reconciled the missing desired supervisor")
            self.assertFalse(self.retire_file("local-2").is_file(), "a wanted slot is never retired")
            self.wait_worker_ids(client, ["local", "local-2"])

    def test_an_explicitly_stopped_slot_stays_down_until_explicitly_restarted(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env):
            client = self.daemon_client()
            self.wait_worker_ids(client, ["local", "local-2"])
            self.assertTrue(wait_for(lambda: self.supervisor_pid("local-2") is not None, timeout=30))
            code, stopped = self.cli("worker-stop", json.dumps({"workerId": "local-2"}))
            self.assertEqual(code, 0, stopped)
            self.assertTrue(
                wait_for(lambda: lock_is_free(self.worker_dir("local-2") / "supervisor.lock"), timeout=40),
                "the stopped supervisor never released its lifetime lock",
            )

            def reported():
                health = client.health()
                return health if "local-2" in health["stoppedWorkerIds"] else None

            observed = wait_for(reported, timeout=45)
            self.assertIsNotNone(observed, "the pool never reported the explicitly stopped slot")
            self.assertIn("local-2", observed["unstartedWorkerIds"])
            self.assertNotIn("local-2", observed["surplusWorkerIds"])
            self.assertTrue(
                lock_is_free(self.worker_dir("local-2") / "supervisor.lock"),
                "an explicitly stopped desired slot must not be resurrected by a reconcile",
            )
            self.assertTrue((self.worker_dir("local-2") / "stop.request").is_file())

            # A deliberate worker-start clears the durable stop and any stale
            # scale-down intent, then resumes the slot.
            self.retire_file("local-2").write_text(
                json.dumps({"workerId": "local-2", "requestedBy": "daemon-pool"})
            )
            code, started = self.cli("worker-start", json.dumps({"workerId": "local-2"}))
            self.assertEqual(code, 0, started)
            self.assertTrue(
                wait_for(
                    lambda: not lock_is_free(self.worker_dir("local-2") / "supervisor.lock"), timeout=40
                ),
                "a deliberate worker-start must resume the stopped slot",
            )
            self.assertFalse((self.worker_dir("local-2") / "stop.request").exists())
            self.assertFalse(self.retire_file("local-2").exists())
            self.wait_worker_ids(client, ["local", "local-2"])

    def test_custom_worker_ids_outside_the_manifest_are_never_touched(self):
        env = {"BUDDY_WORKER_ID": "local", "BUDDY_MAX_CONCURRENT": "2"}
        with isolated_buddy_environment(), self.daemon(env=env) as first:
            client = self.daemon_client()
            self.assertEqual(client.health()["managedWorkerIds"], ["local", "local-2"])
            self.wait_worker_ids(client, ["local", "local-2"])
            code, started = self.cli("worker-start", json.dumps({"workerId": "local-99"}))
            self.assertEqual(code, 0, started)
            self.assertTrue(
                wait_for(lambda: not lock_is_free(self.worker_dir("local-99") / "supervisor.lock"), timeout=40),
                "the custom supervisor never started",
            )
            client.call("service_control", {"action": "restart", "drainSeconds": 1})
            first.wait(timeout=25)

        with isolated_buddy_environment(), self.daemon(env=env) as second:
            client = self.daemon_client()
            self.assertTrue(
                wait_for(lambda: not lock_is_free(self.worker_dir("local-99") / "supervisor.lock"), timeout=30),
                "the custom supervisor must keep running across a daemon restart",
            )
            self.assertFalse(self.retire_file("local-99").exists(), "a custom worker is never retired")
            self.assertFalse((self.worker_dir("local-99") / "stop.request").exists())
            self.assertNotIn("local-99", client.health()["surplusWorkerIds"])
            self.assertEqual(self.pool_manifest()["workerIds"], ["local", "local-2"])
            # An explicit CLI worker-stop stops exactly the named supervisor.
            code, stopped = self.cli("worker-stop", json.dumps({"workerId": "local-99"}))
            self.assertEqual(code, 0, stopped)
            self.assertTrue((self.worker_dir("local-99") / "stop.request").is_file())
            for worker_id in ("local", "local-2"):
                self.assertFalse(
                    (self.worker_dir(worker_id) / "stop.request").exists(),
                    "worker-stop must not expand an explicit id into a pool-wide stop",
                )
            self.assertTrue(
                wait_for(lambda: lock_is_free(self.worker_dir("local-99") / "supervisor.lock"), timeout=40),
                "the custom supervisor never stopped",
            )


if __name__ == "__main__":
    unittest.main()
