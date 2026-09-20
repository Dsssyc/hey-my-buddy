"""Blackboard acceptance tests, organised by the ADR's ten evidence groups.

Every test uses a private state directory. Groups that require the real transport,
a real child process or a restart use the real daemon and the real CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# ``support`` puts the project's own ``python`` directory first on sys.path, so the
# tests exercise this checkout rather than any installed copy of the package.
from support import BoardTestCase, wait_for

from buddy.errors import BoardError
from buddy.schemas import FINGERPRINT_VERSION_LEGACY
from buddy.store import ATTEMPT_TRANSITIONS, TASK_TRANSITIONS
from buddy.worker.worker import Worker


class TestAtomicity(BoardTestCase):
    """Group 1: injected failure between writes rolls everything back."""

    def test_injected_failure_between_state_and_event_rolls_back(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-1", task="do", cwd=str(cwd))["task"]
        before = client.get(runId=task["runId"])
        original = board.store._append_event

        def explode(*args, **kwargs):
            raise RuntimeError("injected failure between the state change and its event")

        board.store._append_event = explode
        try:
            with self.assertRaises(RuntimeError):
                client.cancel(runId=task["runId"])
        finally:
            board.store._append_event = original
        after = client.get(runId=task["runId"])
        self.assertEqual(before["status"], "queued")
        self.assertEqual(after["status"], "queued", "the task state must roll back with its missing event")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(board.store.events_read({"after": 0})["events"][-1]["kind"], "task.submitted")

    def test_state_change_and_event_commit_together(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-2", task="do", cwd=str(cwd))["task"]
        client.cancel(runId=task["runId"], reason="test")
        kinds = [event["kind"] for event in board.store.events_read({"after": 0})["events"]]
        self.assertEqual(kinds, ["task.submitted", "task.cancelled"])
        self.assertEqual(client.get(runId=task["runId"])["status"], "cancelled")

    def test_reconnect_sees_only_committed_state_and_integrity_passes(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-3", task="do", cwd=str(cwd))["task"]
        integrity = board.store.integrity()
        self.assertEqual(integrity["integrity"], "ok")
        self.assertEqual(integrity["foreignKeyViolations"], 0)
        store_two = type(board.store)(self.directory, max_concurrent=2)
        store_two.initialize()
        self.assertEqual(store_two.task_get({"runId": task["runId"]})["task"]["requestId"], "atomic-3")

    def test_failed_artifact_validation_publishes_no_success_event(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-4", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        worker = Worker("w-atomic", self.directory, client=client)
        worker.register()
        claim = client.claim("w-atomic", "claim-atomic-4", "n" * 32)
        attempt = claim["claim"]["attempt"]
        with self.assertRaises(BoardError) as caught:
            client.submit_result(
                "w-atomic",
                attempt["attemptId"],
                attempt["generation"],
                "n" * 32,
                {
                    "status": "ok",
                    "result": {"status": "ok"},
                    "shutdownConfirmed": True,
                    "artifacts": [{"location": str(cwd / "missing.bin"), "kind": "file"}],
                },
            )
        self.assertEqual(caught.exception.code, "ARTIFACT_MISSING")
        kinds = [event["kind"] for event in board.store.events_read({"after": 0})["events"]]
        self.assertNotIn("task.completed", kinds)
        self.assertNotIn("task.completed", kinds)
        self.assertEqual(client.get(runId=task["runId"])["status"], "running")


class TestConcurrency(BoardTestCase):
    """Group 2: duplicates, conflicts, capacity and overlapping resources."""

    def test_duplicate_submission_returns_the_same_task(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        first = client.submit(requestId="dup", task="same", cwd=str(cwd))["task"]
        again = client.submit(requestId="dup", task="same", cwd=str(cwd))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["task"]["runId"], first["runId"])
        self.assertEqual(board.store.count_tasks(), 1)

    def test_changed_idempotency_input_conflicts(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="changed", task="same", cwd=str(cwd))
        with self.assertRaises(BoardError) as caught:
            client.submit(requestId="changed", task="different", cwd=str(cwd))
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_conflict_survives_completion_and_restart(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="restart-conflict", task="same", cwd=str(cwd))["task"]
        client.cancel(runId=task["runId"])
        store_two = type(board.store)(self.directory, max_concurrent=2)
        store_two.initialize()
        with self.assertRaises(BoardError) as caught:
            store_two.task_submit({"requestId": "restart-conflict", "task": "changed", "cwd": str(cwd)})
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_duplicate_claim_with_the_same_command_replays_one_generation(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="claim-dup", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("w1", adapter="command", capabilities=["command"])
        first = client.claim("w1", "claim-dup-1", "a" * 32)
        second = client.claim("w1", "claim-dup-1", "a" * 32)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["claim"]["attempt"]["attemptId"], second["claim"]["attempt"]["attemptId"])
        self.assertEqual(first["claim"]["capability"], second["claim"]["capability"])
        self.assertEqual(len(client.list_tasks()["tasks"]), 1)
        self.assertEqual(client.get(runId=first["claim"]["task"]["runId"])["attemptGeneration"], 1)

    def test_a_different_worker_cannot_replay_another_workers_claim_receipt(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="claim-owner", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("owner", adapter="command", capabilities=["command"])
        client.register_worker("thief", adapter="command", capabilities=["command"])
        client.claim("owner", "claim-owner-1", "a" * 32)
        with self.assertRaises(BoardError) as caught:
            client.claim("thief", "claim-owner-1", "b" * 32)
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")

    def test_one_worker_runs_one_attempt_at_a_time(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="busy-1", task="one", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.submit(requestId="busy-2", task="two", cwd=str(self.workdir("other")), adapter="command", argv=["/bin/true"])
        client.register_worker("w-busy", adapter="command", capabilities=["command"])
        client.claim("w-busy", "claim-busy-1", "a" * 32)
        with self.assertRaises(BoardError) as caught:
            client.claim("w-busy", "claim-busy-2", "a" * 32)
        self.assertEqual(caught.exception.code, "WORKER_BUSY")

    def test_capacity_queues_instead_of_rejecting(self):
        board = self.board(max_concurrent=1)
        client = board.client()
        first_cwd = self.workdir("a")
        second_cwd = self.workdir("b")
        client.register_worker("w-cap", adapter="command", capabilities=["command"])
        client.register_worker("w-cap2", adapter="command", capabilities=["command"])
        client.submit(requestId="cap-1", task="one", cwd=str(first_cwd), adapter="command", argv=["/bin/true"])
        client.claim("w-cap", "claim-cap-1", "a" * 32)
        # Queued admission, not BUSY: the second task is accepted and says why it waits.
        second = client.submit(requestId="cap-2", task="two", cwd=str(second_cwd), adapter="command", argv=["/bin/true"])["task"]
        self.assertEqual(second["status"], "queued")
        self.assertEqual(second["queueReason"], "capacity")
        empty = client.claim("w-cap2", "claim-cap-2", "c" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "capacity")

    def test_overlapping_cwd_reserves_resources(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        parent = self.workdir("repo")
        child = parent / "nested"
        child.mkdir()
        client.register_worker("w-cwd", adapter="command", capabilities=["command"])
        client.register_worker("w-cwd2", adapter="command", capabilities=["command"])
        client.submit(requestId="cwd-1", task="one", cwd=str(parent), adapter="command", argv=["/bin/true"])
        client.claim("w-cwd", "claim-cwd-1", "a" * 32)
        other = client.submit(requestId="cwd-2", task="two", cwd=str(child), adapter="command", argv=["/bin/true"])["task"]
        self.assertEqual(other["queueReason"], "cwd-overlap")
        self.assertIsNone(client.claim("w-cwd2", "claim-cwd-2", "c" * 32)["claim"])

    def test_exclusive_resources_reserve(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        client.register_worker("w-res", adapter="command", capabilities=["command"])
        client.submit(
            requestId="res-1", task="one", cwd=str(self.workdir("r1")), adapter="command", argv=["/bin/true"],
            exclusiveResources=["gpu:0"],
        )
        client.claim("w-res", "claim-res-1", "a" * 32)
        second = client.submit(
            requestId="res-2", task="two", cwd=str(self.workdir("r2")), adapter="command", argv=["/bin/true"],
            exclusiveResources=["gpu:0"],
        )["task"]
        self.assertEqual(second["queueReason"], "exclusive-resource")

    def test_retry_re_holds_released_resources(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        cwd = self.workdir("retry-res")
        task = client.submit(requestId="retry-res", task="one", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-retry", adapter="command", capabilities=["command"])
        claim = client.claim("w-retry", "claim-retry-res-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.release("w-retry", attempt["attemptId"], attempt["generation"], "a" * 32, "test release")
        self.assertEqual(board.store.resource_claims(), [], "a released attempt frees its reservation")
        client.retry(runId=task["runId"], reason="test retry")
        claim_two = client.claim("w-retry", "claim-retry-res-2", "a" * 32)
        self.assertIsNotNone(claim_two["claim"])
        self.assertEqual(claim_two["claim"]["attempt"]["generation"], 2)
        # The retried attempt holds the same reservation again: a released claim row
        # must be re-held, not silently left released.
        claims = board.store.resource_claims()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["attemptId"], claim_two["claim"]["attempt"]["attemptId"])
        self.assertEqual(claims[0]["state"], "held")
        nested = cwd / "sub"
        nested.mkdir()
        blocker = client.submit(
            requestId="retry-res-blocked", task="two", cwd=str(nested), adapter="command", argv=["/bin/true"]
        )
        self.assertEqual(blocker["task"]["queueReason"], "cwd-overlap")

    def test_revision_conflict_is_rejected(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="rev", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-rev", adapter="command", capabilities=["command"])
        claim = client.claim("w-rev", "claim-rev-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.submit_result(
            "w-rev", attempt["attemptId"], attempt["generation"], "a" * 32,
            {"status": "failed", "result": {"status": "nonzero"}, "shutdownConfirmed": True, "error": "boom"},
        )
        client.retry(runId=task["runId"], reason="again")
        client.register_worker("w-rev2", adapter="command", capabilities=["command"])
        second = client.claim("w-rev2", "claim-rev-2", "b" * 32)["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        with self.assertRaises(BoardError) as caught:
            client.submit_result(
                "w-rev", attempt["attemptId"], attempt["generation"], "a" * 32,
                {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True},
            )
        self.assertEqual(caught.exception.code, "STALE_GENERATION")


class TestCrashWindows(BoardTestCase):
    """Group 3: no duplicate execution and no silently lost committed result."""

    def test_crash_before_spawn_releases_the_attempt_honestly(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-pre", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("w-pre", adapter="command", capabilities=["command"])
        claim = client.claim("w-pre", "claim-crash-pre-1", "a" * 32)
        worker = Worker("w-pre", self.directory, client=client)
        worker.spool.ensure()
        worker.spool.write_startup(
            {
                "workerId": "w-pre",
                "nonce": "a" * 32,
                "claimRequestId": "claim-crash-pre-1",
                "attemptId": claim["claim"]["attempt"]["attemptId"],
                "generation": 1,
                "createdAt": "2026-01-01T00:00:00.000Z",
            }
        )
        # No spawn.intent was ever written: the worker never reached the boundary.
        worker.reattach()
        task = client.get(runId=claim["claim"]["task"]["runId"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attemptState"], "finished")
        self.assertEqual(
            board.store.active_work()["attempts"], [], "a never-spawned attempt must not retain resources"
        )

    def test_a_killed_worker_keeps_the_surviving_child_uncertain_and_unretryable(self):
        """Real fault injection: kill the worker while its child is confirmed alive.

        Nothing in this test hand-writes an intent file. A real supervisor process
        (whose handle this test owns) claims a real task, spawns a real child, and is
        then SIGKILLed. The adapter starts children in their own session, so the
        grandchild survives; the board must keep the attempt uncertain, keep its
        resource claims and refuse to retry it, and only this test may clean up the
        processes it created.
        """
        supervisor = None
        pgid = None
        try:
            with self.daemon(env={"BUDDY_WORKER_ID": "idle-local"}):
                environment = {
                    **os.environ,
                    "BUDDY_STATE_DIR": str(self.directory),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    "VIRTUAL_ENV": "",
                }
                supervisor = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "buddy.worker.supervisor",
                        "--worker-id",
                        "fault",
                        "--state-dir",
                        str(self.directory),
                        "--capabilities",
                        "fault-worker",
                    ],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                code, task = self.cli(
                    "submit",
                    json.dumps(
                        {
                            "requestId": "fault-1",
                            "task": "long running work",
                            "cwd": str(self.workdir()),
                            "adapter": "command",
                            "argv": ["/bin/sh", "-c", "sleep 25"],
                            "timeoutSeconds": 120,
                            "requiredCapabilities": ["fault-worker"],
                        }
                    ),
                )
                self.assertEqual(code, 0, task)
                run_id = task["runId"]
                self.assertTrue(
                    wait_for(lambda: self.cli("status", json.dumps({"runId": run_id}))[1].get("status") == "running", 30),
                    "the fault worker must claim the task",
                )
                status = self.cli("status", json.dumps({"runId": run_id}))[1]
                attempt_id = status["selectedAttemptId"]
                marker_path = self.directory / "attempts" / run_id / attempt_id / "spawn.marker"
                self.assertTrue(wait_for(lambda: marker_path.exists(), 20), "the worker must record its real spawn")
                marker = json.loads(marker_path.read_text())
                pgid = marker["pgid"]
                self.assertIsInstance(pgid, int)
                self.assertTrue(self._group_alive(pgid), "the spawned child must be alive before the fault")
                # Kill only the worker process itself; the child leads its own session.
                supervisor.kill()
                supervisor.wait(timeout=20)
                self.assertTrue(self._group_alive(pgid), "the child must survive its killed worker")
            # A restarted daemon reconciles the attempt as uncertain and keeps its claims.
            with self.daemon(env={"BUDDY_WORKER_ID": "idle-local"}):
                status = self.cli("status", json.dumps({"runId": run_id}))[1]
                self.assertEqual(status["attemptState"], "uncertain")
                self.assertNotEqual(status["status"], "failed", "an unknown attempt is never reported as stopped")
                self.assertTrue(self._group_alive(pgid), "the surviving child is still running")
                code, retry = self.cli("retry", json.dumps({"runId": run_id, "reason": "operator says it is gone"}))
                self.assertEqual(code, 1)
                self.assertEqual(retry["error"]["code"], "SHUTDOWN_UNCONFIRMED")
                self.assertEqual(status["selectedAttemptId"], attempt_id, "no replacement generation may exist")
                # A replacement worker with the same identity must not release it either:
                # it no longer holds the handle, so it has no evidence of termination.
                replacement = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "buddy.worker.supervisor",
                        "--worker-id",
                        "fault",
                        "--state-dir",
                        str(self.directory),
                        "--capabilities",
                        "fault-worker",
                    ],
                    env={
                        **os.environ,
                        "BUDDY_STATE_DIR": str(self.directory),
                        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "VIRTUAL_ENV": "",
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.children.append(replacement)
                time.sleep(3)
                after = self.cli("status", json.dumps({"runId": run_id}))[1]
                self.assertEqual(
                    after["attemptState"],
                    "uncertain",
                    "a new process with the same worker id has no handle, so it must not resume or release the work",
                )
                self.assertIn(after["status"], ("running", "cancelling"))
                self.assertEqual(after["selectedAttemptId"], attempt_id)
                code, retry_after = self.cli("retry", json.dumps({"runId": run_id, "reason": "still gone"}))
                self.assertEqual(code, 1)
                self.assertEqual(retry_after["error"]["code"], "SHUTDOWN_UNCONFIRMED")
        finally:
            # Clean up only the processes this test created.
            if supervisor is not None and supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=20)
            if pgid is not None and self._group_alive(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                wait_for(lambda: not self._group_alive(pgid), 20)

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True

    def test_a_receipt_that_cannot_be_committed_is_replayed_never_re_executed(self):
        """A daemon outage past the retry budget must not run the task twice."""
        from buddy.client import BoardClient

        board = self.board()
        online = {"value": False}
        real_call = board.call

        def flaky(operation: str, params: dict) -> dict:
            if operation == "worker_result" and not online["value"]:
                raise BoardError("SERVICE_UNAVAILABLE", "simulated daemon outage")
            return real_call(operation, params)

        client = BoardClient(self.directory, call=flaky)
        cwd = self.workdir()
        log = cwd / "executions.txt"
        task = client.submit(
            requestId="offline-1",
            task="count executions",
            cwd=str(cwd),
            adapter="command",
            argv=["/bin/sh", "-c", f"echo run >> {log}"],
            timeoutSeconds=60,
        )["task"]
        worker = Worker("w-offline", self.directory, client=client, retry_seconds=2, log=lambda _m: None)
        worker.register()
        self.assertEqual(worker.run_once(), "replay")
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertIsNotNone(worker.spool.read(worker.spool.read_startup()["attemptId"]))
        # The next round may only replay the durable receipt.
        self.assertEqual(worker.run_once(), "replay")
        self.assertEqual(len(log.read_text().splitlines()), 1, "the task must not execute a second time")
        online["value"] = True
        self.assertEqual(worker.run_once(), "replayed")
        self.assertEqual(len(log.read_text().splitlines()), 1, "replay must not execute anything")
        self.assertEqual(client.get(runId=task["runId"])["status"], "completed")
        self.assertIsNone(worker.spool.read_startup(), "the intent is cleared only after a durable commit")

    def test_a_marker_write_failure_still_supervises_the_live_child(self):
        """The live handle, never a missing marker, decides whether work stopped."""
        from buddy.adapters.command import CommandAdapter
        import buddy.worker.worker as worker_module

        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(
            requestId="marker-fail",
            task="long work",
            cwd=str(cwd),
            adapter="command",
            argv=["/bin/sh", "-c", "sleep 30"],
            timeoutSeconds=60,
        )["task"]
        worker = Worker("w-marker", self.directory, client=client, log=lambda _m: None)
        worker.register()
        handles: list = []
        original_start = CommandAdapter.start
        original_fsync = worker_module.fsync_json

        def capturing_start(self, context):
            handle = original_start(self, context)
            handles.append(handle)
            return handle

        def failing_marker(path, value):
            if Path(path).name == "spawn.marker":
                raise RuntimeError("injected marker write failure")
            return original_fsync(path, value)

        CommandAdapter.start = capturing_start
        worker_module.fsync_json = failing_marker
        try:
            worker.run_once()
        finally:
            CommandAdapter.start = original_start
            worker_module.fsync_json = original_fsync
        self.assertEqual(len(handles), 1, "exactly one child was created")
        attempt_id = client.get(runId=task["runId"])["selectedAttemptId"]
        marker = self.directory / "attempts" / task["runId"] / attempt_id / "spawn.marker"
        self.assertFalse(marker.exists(), "the marker write really did fail")
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("worker failure", result["resultMeta"]["error"])
        self.assertFalse(handles[0].group_alive(), "the live handle must be used to stop the child")
        self.assertTrue(
            result["resultMeta"]["shutdownConfirmed"],
            "shutdown is confirmed from the owned handle, not inferred from the missing marker",
        )
        self.assertIsNone(result["result"], "no adapter outcome exists for a worker failure")

    def test_commit_before_lost_reply_is_replayed_not_duplicated(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-reply", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("w-reply", adapter="command", capabilities=["command"])
        claim = client.claim("w-reply", "claim-crash-reply-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        report = {
            "status": "ok",
            "result": {"status": "ok"},
            "shutdownConfirmed": True,
            "commandId": f"result:{attempt['attemptId']}",
        }
        first = client.submit_result("w-reply", attempt["attemptId"], attempt["generation"], "a" * 32, dict(report))
        second = client.submit_result("w-reply", attempt["attemptId"], attempt["generation"], "a" * 32, dict(report))
        self.assertTrue(second.get("duplicate") or second.get("replayed"))
        completed = [event for event in board.store.events_read({"after": 0})["events"] if event["kind"] == "task.completed"]
        self.assertEqual(len(completed), 1, "a replayed result must not append a second completion event")

    def test_conflicting_replay_of_the_same_attempt_fails(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-conflict", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("w-conf", adapter="command", capabilities=["command"])
        claim = client.claim("w-conf", "claim-crash-conflict-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.submit_result(
            "w-conf", attempt["attemptId"], attempt["generation"], "a" * 32,
            {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True},
        )
        with self.assertRaises(BoardError) as caught:
            client.submit_result(
                "w-conf", attempt["attemptId"], attempt["generation"], "a" * 32,
                {"status": "failed", "result": {"status": "nonzero"}, "shutdownConfirmed": True},
            )
        self.assertEqual(caught.exception.code, "CONFLICT")


class TestLifecycle(BoardTestCase):
    """Group 4: wait interruption and daemon restart keep the same attempt."""

    def test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result(self):
        with self.daemon() as first:
            cwd = self.workdir()
            artifact = self.directory / "artifact.txt"
            code, task = self.cli(
                "submit",
                json.dumps(
                    {
                        "requestId": "life-1",
                        "task": "produce an artifact",
                        "cwd": str(cwd),
                        "adapter": "command",
                        "argv": ["/bin/sh", "-c", f"sleep 8; echo durable > {artifact}"],
                        "timeoutSeconds": 120,
                    }
                ),
            )
            self.assertEqual(code, 0, task)
            run_id = task["runId"]
            self.assertEqual(task["status"], "queued")
            started = wait_for(lambda: self.cli("status", json.dumps({"runId": run_id}))[1].get("status") == "running", 20)
            self.assertTrue(started, "the worker must claim the task")
            before = self.cli("status", json.dumps({"runId": run_id}))[1]
            attempt_id = before["selectedAttemptId"]
            worker_id = before["workerId"]
            generation = before["attemptGeneration"]
            # Interrupt the waiting client: it observes the same task and never cancels it.
            waiting = subprocess.Popen(
                [sys.executable, "-m", "buddy.cli", "await", json.dumps({"runId": run_id, "waitSeconds": 60})],
                env={
                    **os.environ,
                    "BUDDY_STATE_DIR": str(self.directory),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
            waiting.send_signal(signal.SIGINT)
            waiting.wait(timeout=20)
            after_interrupt = self.cli("status", json.dumps({"runId": run_id}))[1]
            self.assertIn(
                after_interrupt["status"],
                ("running", "completed"),
                "an interrupted wait must never cancel or fail the task",
            )
            self.assertNotEqual(after_interrupt.get("cancelRequested"), True)
            # Restart the daemon while the same work keeps running.
            code, restart = self.cli("restart")
            self.assertEqual(code, 0, restart)
            self.assertTrue(restart["restarting"])
            self.assertTrue(restart["workersPreserved"])
            first.wait(timeout=25)
        with self.daemon() as second:
            self.assertIsNotNone(second.pid)
            status = self.cli("status", json.dumps({"runId": run_id}))[1]
            self.assertEqual(status["selectedAttemptId"], attempt_id, "the same attempt must survive the restart")
            self.assertEqual(status["workerId"], worker_id)
            self.assertEqual(status["attemptGeneration"], generation)
            self.assertEqual(status["timeoutSeconds"], 120, "the execution deadline must survive the restart")
            completed = wait_for(
                lambda: self.cli("status", json.dumps({"runId": run_id}))[1]["status"] == "completed", 30
            )
            self.assertTrue(completed, "the worker must finish the same attempt after the restart")
            code, result = self.cli("result", json.dumps({"runId": run_id}))
            self.assertEqual(code, 0, result)
            self.assertTrue(result["resultAvailable"])
            self.assertEqual(artifact.read_text().strip(), "durable")
            stdout_artifacts = [
                entry["location"] for entry in result["artifacts"] if entry["location"].endswith("runner.stdout.log")
            ]
            self.assertEqual(len(stdout_artifacts), 1, "the runner log is registered as a verified artifact")
            self.assertEqual(
                Path(stdout_artifacts[0]).resolve(),
                Path(os.path.realpath(self.directory / "attempts" / run_id / attempt_id / "runner.stdout.log")),
            )
            self.assertTrue(result["result"]["processState"]["shutdownConfirmed"])

    def test_restart_keeps_worker_receipts_and_claims(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="life-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        client.register_worker("w-life", adapter="command", capabilities=["command"])
        claim = client.claim("w-life", "claim-life-2", "a" * 32)
        summary = board.store.reconcile_startup()
        self.assertEqual(summary["uncertain"], 1)
        self.assertEqual(summary["retained"], 1)
        self.assertEqual(client.get(runId=claim["claim"]["task"]["runId"])["attemptState"], "uncertain")


class TestIdentity(BoardTestCase):
    """Group 5: capabilities, generations and diagnostic PIDs cannot mutate or kill."""

    def test_wrong_capability_is_rejected(self):
        board = self.board()
        client = board.client()
        client.submit(requestId="id-1", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"])
        client.register_worker("w-id", adapter="command", capabilities=["command"])
        claim = client.claim("w-id", "claim-id-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        with self.assertRaises(BoardError) as caught:
            client.renew("w-id", attempt["attemptId"], attempt["generation"], "b" * 32)
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")

    def test_stale_generation_is_rejected_and_cannot_mutate(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="id-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-id2", adapter="command", capabilities=["command"])
        first = client.claim("w-id2", "claim-id-2a", "a" * 32)["claim"]["attempt"]
        client.release("w-id2", first["attemptId"], first["generation"], "a" * 32, "test")
        client.retry(runId=task["runId"], reason="test")
        client.register_worker("w-id3", adapter="command", capabilities=["command"])
        second = client.claim("w-id3", "claim-id-2b", "b" * 32)["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        for call in (
            lambda: client.renew("w-id2", first["attemptId"], first["generation"], "a" * 32),
            lambda: client.reconcile("w-id2", first["attemptId"], first["generation"], "a" * 32),
            lambda: client.submit_result(
                "w-id2", first["attemptId"], first["generation"], "a" * 32,
                {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True},
            ),
        ):
            with self.assertRaises(BoardError) as caught:
                call()
            self.assertIn(caught.exception.code, ("STALE_GENERATION", "UNAUTHORIZED", "ATTEMPT_FINISHED"))
        self.assertEqual(client.get(runId=task["runId"])["attemptGeneration"], 2)

    def test_a_stored_pid_is_never_used_to_signal_a_process(self):
        """A sentinel PID recorded as a worker PID must survive cancel and stop."""
        sentinel = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: (sentinel.terminate(), sentinel.wait(timeout=10)))
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="id-3", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-sentinel", adapter="command", capabilities=["command"])
        claim = client.claim("w-sentinel", "claim-id-3", "a" * 32)
        # The service stores PIDs as diagnostics only.
        with board.store.db.write() as connection:
            connection.execute("UPDATE workers SET pid=? WHERE worker_id='w-sentinel'", (sentinel.pid,))
            connection.execute(
                "UPDATE attempts SET log_paths=? WHERE attempt_id=?",
                (json.dumps({"sentinelPid": sentinel.pid}), claim["claim"]["attempt"]["attemptId"]),
            )
        client.cancel(runId=task["runId"], reason="test cancel")
        board.call("service_control", {"action": "stop", "drainSeconds": 0})
        board.store.reconcile_startup()
        self.assertIsNone(sentinel.poll(), "no stored PID may ever be signalled")
        self.assertEqual(client.get(runId=task["runId"])["status"], "cancelling")

    def test_lease_expiry_is_uncertain_not_stopped(self):
        board = self.board()
        client = board.client()
        task = client.submit(
            requestId="id-4", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"]
        )["task"]
        client.register_worker("w-lease", adapter="command", capabilities=["command"])
        claim = client.claim("w-lease", "claim-id-4", "a" * 32)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET lease_expires_at='2000-01-01T00:00:00.000Z'")
        self.assertEqual(board.store.mark_expired_leases(), 1)
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "uncertain")
        self.assertEqual(client.get(runId=task["runId"])["status"], "running")
        self.assertTrue(board.store.active_work()["attempts"])
        with self.assertRaises(BoardError):
            client.retry(runId=task["runId"], reason="unsafe")


class TestDelivery(BoardTestCase):
    """Group 6: cursors, waits under load, cancellation races and inquiry."""

    def test_cursor_replay_and_bounded_pages(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        for index in range(5):
            client.submit(requestId=f"ev-{index}", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        page = client.read_events(after=0, limit=3)
        self.assertEqual(len(page["events"]), 3)
        self.assertTrue(page["truncated"])
        rest = client.read_events(after=page["cursor"], limit=10)
        self.assertGreaterEqual(len(rest["events"]), 2)
        self.assertEqual(client.read_events(after=rest["cursor"])["events"], [])

    def test_events_wait_returns_committed_events_and_times_out_honestly(self):
        board = self.board()
        client = board.client()
        empty = client.wait_events(after=board.store.head(), timeout_ms=200)
        self.assertTrue(empty["timedOut"])
        client.submit(requestId="ev-wait", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"])
        page = client.wait_events(after=0, timeout_ms=1000)
        self.assertFalse(page["timedOut"])
        self.assertTrue(page["events"])

    def test_more_waiters_than_capacity_never_blocks_mutations(self):
        board = self.board(wait_capacity=2)
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="load", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-load", adapter="command", capabilities=["command"])
        claim = client.claim("w-load", "claim-load-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        results: list[str] = []
        barrier = threading.Barrier(9, timeout=30)

        def waiter():
            barrier.wait()
            try:
                client.wait_events(after=0, timeout_ms=2000)
                results.append("ok")
            except BoardError as error:
                results.append(error.code)

        threads = [threading.Thread(target=waiter) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        time.sleep(0.3)
        # Mutations must stay responsive: renew, cancel and the result commit.
        client.renew("w-load", attempt["attemptId"], attempt["generation"], "a" * 32)
        client.cancel(runId=task["runId"], reason="race")
        client.submit_result(
            "w-load", attempt["attemptId"], attempt["generation"], "a" * 32,
            {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True},
        )
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(results), 8)
        self.assertIn("WAIT_OVERLOAD", results, "overflow must be an honest, resumable overload error")
        self.assertEqual(client.get(runId=task["runId"])["status"], "completed")

    def test_cancellation_race_has_one_legal_outcome(self):
        board = self.board()
        client = board.client()
        task = client.submit(
            requestId="race", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"]
        )["task"]
        client.register_worker("w-race", adapter="command", capabilities=["command"])
        claim = client.claim("w-race", "claim-race-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.cancel(runId=task["runId"], reason="cancel then complete")
        completed = client.submit_result(
            "w-race", attempt["attemptId"], attempt["generation"], "a" * 32,
            {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True},
        )
        self.assertEqual(completed["taskState"], "completed", "a confirmed completion is the one durable outcome")
        kinds = [event["kind"] for event in board.store.events_read({"after": 0})["events"]]
        self.assertIn("task.cancel_requested", kinds)
        self.assertIn("task.completed", kinds)
        self.assertEqual(sum(1 for kind in kinds if kind in ("task.completed", "task.cancelled", "task.failed")), 1)

    def test_cancelled_without_confirmed_shutdown_retains_resources(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="cancel-unconf", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.register_worker("w-cu", adapter="command", capabilities=["command"])
        claim = client.claim("w-cu", "claim-cancel-unconf", "a" * 32)
        attempt = claim["claim"]["attempt"]
        result = client.submit_result(
            "w-cu", attempt["attemptId"], attempt["generation"], "a" * 32,
            {"status": "cancelled", "result": {"status": "cancelled"}, "shutdownConfirmed": False},
        )
        self.assertEqual(result["taskState"], "reconciliation-needed")
        claims = board.store.resource_claims()
        self.assertEqual(len(claims), 1, "an unconfirmed cancellation must retain its reservation")
        self.assertEqual(claims[0]["state"], "retained")
        nested = cwd / "sub"
        nested.mkdir()
        blocked = client.submit(
            requestId="cancel-unconf-blocked", task="two", cwd=str(nested), adapter="command", argv=["/bin/true"]
        )["task"]
        self.assertEqual(blocked["queueReason"], "cwd-overlap")

    def test_question_deduplication_and_conflict(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(
            requestId="q-1", task="do", cwd=str(cwd), adapter="command", argv=["/bin/sleep", "5"]
        )["task"]
        client.register_worker("w-q", adapter="command", capabilities=["command"])
        client.claim("w-q", "claim-q-1", "a" * 32)
        first = client.post_question("q1", "what is blocking you?", runId=task["runId"])
        again = client.post_question("q1", "what is blocking you?", runId=task["runId"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        with self.assertRaises(BoardError) as caught:
            client.post_question("q1", "a different question", runId=task["runId"])
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_inquiry_bounds_and_honest_unsupported_adapter(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="inq-1", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        with self.assertRaises(BoardError) as caught:
            client.post_question("q1", "x" * 4001, runId=task["runId"])
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        observed = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(observed["status"], "queued")
        self.assertFalse(observed["bridge"]["enabled"])
        self.assertIn("no inquiry capability", observed["bridge"]["reason"])
        self.assertEqual(observed["limits"]["maxQuestionBytes"], 4000)
        self.assertEqual(observed["limits"]["maxInquiriesPerRun"], 32)

    def test_correlated_answer_survives_a_daemon_restart(self):
        with self.daemon():
            cwd = self.workdir()
            code, task = self.cli(
                "submit",
                json.dumps({"requestId": "inq-restart", "task": "do", "cwd": str(cwd), "adapter": "command", "argv": ["/bin/sleep", "8"]}),
            )
            self.assertEqual(code, 0, task)
            run_id = task["runId"]
            self.assertTrue(wait_for(lambda: self.cli("status", json.dumps({"runId": run_id}))[1]["status"] == "running", 20))
            code, posted = self.cli("message", json.dumps({"runId": run_id, "inquiryId": "q-restart", "question": "how is it going?"}))
            self.assertEqual(code, 0, posted)
            self.assertEqual(posted["message"]["state"], "queued")
            code, update = self.cli(
                "message-update",
                json.dumps({"runId": run_id, "inquiryId": "q-restart", "answer": {"text": "still running", "via": "reply-tool"}}),
            )
            self.assertEqual(code, 0, update)
            self.assertEqual(update["message"]["state"], "answered")
        with self.daemon():
            code, read = self.cli("message-get", json.dumps({"runId": run_id, "inquiryId": "q-restart"}))
            self.assertEqual(code, 0, read)
            self.assertEqual(read["message"]["answer"]["text"], "still running")

    def test_answers_require_the_correlated_id(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="inq-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/sleep", "5"])["task"]
        client.register_worker("w-inq2", adapter="command", capabilities=["command"])
        client.claim("w-inq2", "claim-inq-2", "a" * 32)
        client.post_question("q-known", "hello?", runId=task["runId"])
        with self.assertRaises(BoardError):
            board.call("message_update", {"runId": task["runId"], "inquiryId": "q-unknown", "answer": {"text": "hi"}})
        with self.assertRaises(BoardError) as caught:
            board.call("message_update", {"runId": task["runId"], "inquiryId": "q-known", "state": "answered"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


class TestExtensibility(BoardTestCase):
    """Group 7: built-in adapters plus a real external worker over the public API."""

    def test_capabilities_are_reported_honestly(self):
        board = self.board()
        report = board.call("capabilities", {})
        self.assertIn("dsh", report["adapters"])
        self.assertIn("command", report["adapters"])
        self.assertEqual(report["adapters"]["external"]["executedBy"], "caller-owned-agent")
        self.assertNotIn("external", report["localCapabilities"])
        self.assertIn("steer", report["limitations"])

    def test_command_adapter_runs_explicit_argv_without_a_shell(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        marker = cwd / "marker.txt"
        task = client.submit(
            requestId="cmd-1",
            task="write a marker",
            cwd=str(cwd),
            adapter="command",
            argv=["/bin/sh", "-c", f"echo one > {marker}; echo two"],
        )["task"]
        worker = Worker("w-cmd", self.directory, client=client)
        worker.register()
        worker.run_once()
        result = client.result(runId=task["runId"])
        self.assertEqual(result["result"]["status"], "ok")
        self.assertEqual(result["result"]["argv"][0], "/bin/sh")
        self.assertEqual(result["resultMeta"]["status"], "ok")
        self.assertEqual(marker.read_text().strip(), "one")
        self.assertEqual(client.get(runId=task["runId"])["status"], "completed")
        self.assertTrue(client.get(runId=task["runId"])["shutdownConfirmed"])

    def test_command_adapter_rejects_a_missing_executable_honestly(self):
        board = self.board()
        client = board.client()
        task = client.submit(
            requestId="cmd-missing",
            task="run something absent",
            cwd=str(self.workdir()),
            adapter="command",
            argv=["/nonexistent/binary-xyz", "arg"],
        )["task"]
        worker = Worker("w-missing", self.directory, client=client)
        worker.register()
        worker.run_once()
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["result"], "no adapter result exists for an unusable executable")
        self.assertIn("ADAPTER_UNAVAILABLE", result["resultMeta"]["error"])
        self.assertTrue(result["shutdownConfirmed"], "no process was ever created, so shutdown is confirmed")
        self.assertEqual(client.get(runId=task["runId"])["status"], "failed")

    def test_external_task_is_not_claimed_by_a_built_in_worker(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="ext-1", task="produce a report", cwd=str(cwd), adapter="external")["task"]
        worker = Worker("w-builtin", self.directory, client=client)
        worker.register()
        worker.run_once()
        self.assertEqual(client.get(runId=task["runId"])["status"], "queued")

    def test_an_external_agent_completes_a_real_task_through_the_public_api(self):
        """The external worker is a separate process using only the public client."""
        scenario = self.directory / "external_scenario.json"
        runner = self.directory / "external_worker.py"
        artifact = self.directory / "external-report.txt"
        runner.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, %r)\n"
            "from buddy.client import BoardClient, new_nonce\n"
            "from buddy.worker.worker import fsync_json\n"
            "state = Path(%r)\n"
            "board = BoardClient(state)\n"
            "board.register_worker('external-agent', adapter='external', capabilities=['external', 'artifacts', 'task-text'])\n"
            "nonce = new_nonce()\n"
            "intent = {'workerId': 'external-agent', 'nonce': nonce, 'claimRequestId': 'claim-external-1'}\n"
            "fsync_json(state / 'workers' / 'external-agent' / 'startup.json', intent)\n"
            "claim = board.claim('external-agent', 'claim-external-1', nonce)\n"
            "assert claim['claim'], claim\n"
            "attempt = claim['claim']['attempt']\n"
            "text = claim['claim']['task']['task']\n"
            "body = 'external agent executed: ' + text\n"
            "target = Path(%r)\n"
            "target.write_text(body)\n"
            "board.progress('external-agent', attempt['attemptId'], attempt['generation'], nonce, 'agent finished reasoning', phase='executing')\n"
            "import hashlib\n"
            "report = {'status': 'ok', 'result': {'status': 'ok', 'mode': 'external', 'finalText': body},\n"
            "          'shutdownConfirmed': True,\n"
            "          'artifacts': [{'kind': 'result', 'location': str(target),\n"
            "                         'contentHash': hashlib.sha256(target.read_bytes()).hexdigest(),\n"
            "                         'sizeBytes': target.stat().st_size}]}\n"
            "answer = board.submit_result('external-agent', attempt['attemptId'], attempt['generation'], nonce, report)\n"
            "json.dump(answer, open(%r, 'w'))\n" % (str(Path(__file__).resolve().parents[1]), str(self.directory), str(artifact), str(scenario))
        )
        with self.daemon() as process:
            code, submitted = self.cli(
                "submit",
                json.dumps({"requestId": "ext-real", "task": "summarize the repository", "cwd": str(self.workdir()), "adapter": "external"}),
            )
            self.assertEqual(code, 0, submitted)
            self.assertEqual(submitted["status"], "queued")
            completed = subprocess.run(
                [sys.executable, str(runner)],
                env={
                    **os.environ,
                    "BUDDY_STATE_DIR": str(self.directory),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(scenario.is_file(), completed.stderr)
            self.assertEqual(artifact.read_text(), "external agent executed: summarize the repository")
            code, task = self.cli("status", json.dumps({"runId": submitted["runId"]}))
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["workerId"], "external-agent")
            code, acknowledged = self.cli(
                "acknowledge",
                json.dumps({"runId": submitted["runId"], "note": "verified the artifact bytes", "verdict": "accepted"}),
            )
            self.assertEqual(code, 0, acknowledged)
            self.assertEqual(acknowledged["acceptedAt"] is not None, True)
            self.assertEqual(acknowledged["acceptanceVerdict"], "accepted")
            self.assertIsNone(process.poll())


class TestPackaging(BoardTestCase):
    """Group 8 (service side): runtime identity, no source leaks, staging."""

    def test_runtime_identity_is_reported_and_stable(self):
        from buddy import runtime

        described = runtime.describe(destination=self.directory / "runtime")
        self.assertEqual(len(described["contentId"]), 32)
        self.assertTrue(described["assets"] > 5)
        self.assertFalse(described["installed"])
        identity = runtime.runtime_identity()
        self.assertTrue(identity.startswith(("source:", "runtime:")))
        # The declaration is never trusted on its own: `stable` requires the actual
        # running interpreter and package to live inside the runtime.
        resolved = runtime.resolve_runtime()
        self.assertIn("actual", resolved)
        self.assertIn("package", resolved["actual"])
        self.assertFalse(resolved["stable"])

    def test_the_default_cold_start_installs_and_reports_the_stable_runtime(self):
        """No magic PYTHONPATH: the cold start installs and runs the stable runtime."""
        import shutil as shutil_module
        from buddy import runtime

        if not shutil_module.which(os.environ.get("UV_BIN", "uv")):
            self.skipTest("uv is not available")
        runtime_root = self.directory / "runtime-root"
        environment = {"BUDDY_DEV_SOURCE": "", "BUDDY_RUNTIME_ROOT": str(runtime_root)}
        self.addCleanup(lambda: self.cli("stop", "{}", env=environment, timeout=60))
        # The production cold-start path: no daemon pre-started by the test.
        code, health = self.cli("health", env=environment, timeout=600)
        self.assertEqual(code, 0, health)
        self.assertTrue(health["runtimeStable"], health)
        self.assertTrue(str(health["runtimeIdentity"]).startswith("runtime:"))
        runtime_dir = runtime.find_ready(destination=runtime_root)
        self.assertIsNotNone(runtime_dir, "the first cold start must install a READY runtime")
        code, info = self.cli("runtime", "{}", env=environment, timeout=120)
        self.assertEqual(code, 0, info)
        actual = info["identity"]["actual"]
        # macOS /var is a symlink to /private/var: compare resolved paths for the
        # package and scripts, but keep the venv interpreter and prefix lexical --
        # a venv's bin/python is a symlink to the uv-managed shared base interpreter
        # by design, and resolving it would point outside the runtime.
        root = os.path.realpath(runtime_dir)
        self.assertTrue(actual["executable"].startswith(str(runtime_dir)), actual)
        self.assertTrue(os.path.realpath(actual["prefix"]).startswith(root), actual)
        self.assertTrue(os.path.realpath(actual["package"]).startswith(root), actual)
        self.assertTrue(os.path.realpath(actual["adapterScript"]).startswith(root), actual)
        self.assertFalse(info["identity"]["leaks"])
        # Real work runs through the runtime-hosted daemon and its worker.
        code, task = self.cli(
            "submit",
            json.dumps(
                {
                    "requestId": "runtime-1",
                    "task": "run from the runtime",
                    "cwd": str(self.workdir()),
                    "adapter": "command",
                    "argv": ["/bin/echo", "from-runtime"],
                }
            ),
            env=environment,
        )
        self.assertEqual(code, 0, task)
        self.assertTrue(
            wait_for(
                lambda: self.cli("status", json.dumps({"runId": task["runId"]}), env=environment)[1]["status"]
                == "completed",
                90,
            ),
            "the runtime-hosted worker must complete real work",
        )

    def test_no_secrets_or_environments_are_copied_into_a_runtime(self):
        from buddy import runtime

        copied = {relative for relative, _path in runtime._iter_assets(runtime.project_root())}
        for forbidden in (".venv", "node_modules", ".env", "tests", ".git"):
            self.assertFalse(any(part == forbidden for path in copied for part in Path(path).parts), forbidden)
        self.assertFalse(any("__pycache__" in Path(path).parts for path in copied))


class TestLegacyMigration(BoardTestCase):
    """Group 9: dry-run, idempotency, rollback and unchanged source files."""

    def build_legacy(
        self,
        *,
        run_id: str = "11111111-2222-3333-4444-555555555555",
        status: str = "completed",
        request_id: str = "legacy-request",
    ) -> Path:
        from buddy.db import sha256_text, node_json

        source = self.directory / "legacy"
        directory = source / run_id
        directory.mkdir(parents=True, exist_ok=True)
        task_text = "legacy task"
        # The legacy engine canonicalized cwd with realpathSync before hashing.
        input_value = {
            "cwd": os.path.realpath(self.workdir("legacy-work")),
            "task": task_text,
            "timeoutSeconds": 1800,
            "workspace": True,
        }
        record = {
            "runId": run_id,
            "requestId": request_id,
            "inputHash": sha256_text(node_json(input_value)),
            "input": {key: value for key, value in input_value.items() if key != "task"},
            "timeoutSeconds": 1800,
            "cwd": input_value["cwd"],
            "status": status,
            "revision": 3,
            "createdAt": "2026-09-01T00:00:00.000Z",
            "updatedAt": "2026-09-01T00:01:00.000Z",
            "resultAvailable": status == "completed",
            "shutdownConfirmed": status == "completed",
            "exitCode": 0,
            "result": {"status": "ok", "finalText": "legacy done"} if status == "completed" else None,
            "acceptedAt": "2026-09-01T00:02:00.000Z" if status == "completed" else None,
            "acceptanceNote": "reviewed" if status == "completed" else None,
            "inquiries": {
                "q-legacy": {
                    "inquiryId": "q-legacy",
                    "state": "answered",
                    "questionSha256": sha256_text("legacy question"),
                    "questionBytes": 15,
                    "questionPreview": "legacy question",
                    "submittedAt": "2026-09-01T00:00:30.000Z",
                    "updatedAt": "2026-09-01T00:00:40.000Z",
                    "attempts": 2,
                    "delivery": {"deliveredAt": "2026-09-01T00:00:35.000Z"},
                    "answer": {"text": "legacy answer", "bytes": 13, "via": "reply-tool"},
                }
            },
        }
        (directory / "record.json").write_text(json.dumps(record))
        (directory / "task.txt").write_text(task_text)
        return source

    def test_dry_run_then_idempotent_import_with_unchanged_sources(self):
        board = self.board()
        source = self.build_legacy()
        before = {path.name: path.read_bytes() for path in sorted(source.rglob("*")) if path.is_file()}
        dry = board.call("legacy_import", {"sourceDir": str(source), "dryRun": True})
        self.assertTrue(dry["dryRun"])
        self.assertEqual(dry["imported"], 0)
        self.assertEqual(dry["counts"]["tasks"], 1)
        self.assertEqual(board.store.count_tasks(), 0)
        real = board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(real["imported"], 1)
        again = board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["duplicates"], 1)
        self.assertEqual(board.store.count_tasks(), 1)
        after = {path.name: path.read_bytes() for path in sorted(source.rglob("*")) if path.is_file()}
        self.assertEqual(before, after, "the source files must be read-only for the importer")

    def test_records_predating_inquiry_support_import_without_changing_sources(self):
        board = self.board()
        source = self.build_legacy()
        path = next(source.glob("*/record.json"))
        record = json.loads(path.read_text())
        del record["inquiries"]
        path.write_text(json.dumps(record))
        before = path.read_bytes()
        dry = board.call("legacy_import", {"sourceDir": str(source), "dryRun": True})
        self.assertEqual(dry["counts"]["inquiries"], 0)
        imported = board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(imported["imported"], 1)
        result = board.client().result(requestId="legacy-request")
        self.assertEqual(result["result"], record["result"])
        self.assertEqual(result["acceptedAt"], record["acceptedAt"])
        self.assertEqual(path.read_bytes(), before)

    def test_malformed_inquiry_collection_is_rejected_before_import(self):
        board = self.board()
        source = self.build_legacy()
        path = next(source.glob("*/record.json"))
        record = json.loads(path.read_text())
        record["inquiries"] = ["not an inquiry table"]
        path.write_text(json.dumps(record))
        with self.assertRaises(BoardError) as caught:
            board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(caught.exception.code, "LEGACY_CONFLICT")
        self.assertEqual(board.store.count_tasks(), 0)

    def test_imported_result_and_acceptance_are_readable(self):
        board = self.board()
        client = board.client()
        source = self.build_legacy()
        board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        task = client.get(requestId="legacy-request")
        self.assertTrue(task["legacy"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["acceptedAt"], "2026-09-01T00:02:00.000Z")
        self.assertEqual(task["acceptanceVerdict"], "accepted")
        self.assertEqual(task["fingerprintVersion"], FINGERPRINT_VERSION_LEGACY)
        result = client.result(requestId="legacy-request")
        self.assertEqual(result["result"]["finalText"], "legacy done")
        message = client.get_message("q-legacy", runId=task["runId"])
        self.assertEqual(message["answer"]["text"], "legacy answer")

    def test_identical_legacy_start_resolves_without_relaunching(self):
        board = self.board()
        client = board.client()
        source = self.build_legacy()
        board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        again = client.submit(
            requestId="legacy-request",
            task="legacy task",
            cwd=os.path.realpath(self.workdir("legacy-work")),
            timeoutSeconds=1800,
            workspace=True,
        )
        self.assertTrue(again["duplicate"])
        self.assertTrue(again.get("legacy"))
        self.assertEqual(board.store.count_tasks(), 1)
        with self.assertRaises(BoardError) as caught:
            client.submit(requestId="legacy-request", task="a different task", cwd=str(self.workdir("legacy-work")))
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_active_legacy_run_rolls_back_the_whole_import(self):
        board = self.board()
        source = self.build_legacy()
        self.build_legacy(run_id="99999999-2222-3333-4444-555555555555", status="running", request_id="legacy-active")
        with self.assertRaises(BoardError) as caught:
            board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(caught.exception.code, "LEGACY_ACTIVE_RUNS")
        self.assertEqual(board.store.count_tasks(), 0, "nothing may be imported when the import rolls back")

    def test_conflicting_and_malformed_records_roll_back(self):
        board = self.board()
        source = self.build_legacy()
        broken = source / "88888888-2222-3333-4444-555555555555"
        broken.mkdir()
        (broken / "record.json").write_text("{not json")
        with self.assertRaises(BoardError) as caught:
            board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(caught.exception.code, "LEGACY_CONFLICT")
        self.assertEqual(board.store.count_tasks(), 0)

    def test_tampered_task_text_is_detected(self):
        board = self.board()
        source = self.build_legacy()
        (source / "11111111-2222-3333-4444-555555555555" / "task.txt").write_text("tampered")
        with self.assertRaises(BoardError) as caught:
            board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(caught.exception.code, "LEGACY_CONFLICT")

    def test_missing_task_text_is_history_with_an_explicit_limit(self):
        board = self.board()
        client = board.client()
        source = self.build_legacy()
        (source / "11111111-2222-3333-4444-555555555555" / "task.txt").unlink()
        report = board.call("legacy_import", {"sourceDir": str(source), "dryRun": False})
        self.assertEqual(report["counts"]["withoutTaskText"], 1)
        task = client.get(requestId="legacy-request")
        self.assertIn("cannot be shown", task["queueReason"])
        with self.assertRaises(BoardError) as caught:
            client.submit(requestId="legacy-request", task="invented", cwd=str(self.workdir("legacy-work")))
        self.assertEqual(caught.exception.code, "CONFLICT")


class TestTransitionTables(BoardTestCase):
    """The documented transition tables are the ones enforced."""

    def test_illegal_task_transition_is_rejected(self):
        board = self.board()
        client = board.client()
        task = client.submit(requestId="t-1", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"])["task"]
        client.cancel(runId=task["runId"])
        from buddy.errors import BoardError as _BoardError

        with board.store.db.write() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task["runId"],)).fetchone()
            with self.assertRaises(_BoardError) as caught:
                board.store._transition_task(connection, row, "running")
        self.assertEqual(caught.exception.code, "ILLEGAL_TRANSITION")

    def test_tables_cover_every_declared_state(self):
        from buddy.db import ATTEMPT_STATES, TASK_STATES

        self.assertEqual(set(TASK_TRANSITIONS), set(TASK_STATES))
        self.assertEqual(set(ATTEMPT_TRANSITIONS), set(ATTEMPT_STATES))
