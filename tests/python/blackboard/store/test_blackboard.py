"""Blackboard acceptance tests, organised by the ADR's ten evidence groups.

Every test uses a private state directory. Groups that require the real transport,
a real child process or a restart use the real daemon and the real CLI.
"""
from __future__ import annotations
from admission import admit_execution, release_before_spawn, run_next

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
from types import SimpleNamespace
from unittest import mock

# ``support`` puts the project's own ``src`` directory first on sys.path, so the
# tests exercise this checkout rather than any installed copy of the package.
from support import BoardTestCase, wait_for

from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome, ProcessHandle
from hey_my_buddy.private_dirs import attempt_root, ensure_private_dir
from hey_my_buddy.blackboard.store.store import ATTEMPT_TRANSITIONS, TASK_TRANSITIONS
from hey_my_buddy.buddy.runtime.execution import Execution


class TestAtomicity(BoardTestCase):
    """Group 1: injected failure between writes rolls everything back."""

    def test_injected_failure_between_state_and_event_rolls_back(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-1", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
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
        task = client.submit(requestId="atomic-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.cancel(runId=task["runId"], reason="test")
        kinds = [event["kind"] for event in board.store.events_read({"after": 0})["events"]]
        self.assertEqual(kinds, ["task.submitted", "task.cancelled"])
        self.assertEqual(client.get(runId=task["runId"])["status"], "cancelled")

    def test_reconnect_sees_only_committed_state_and_integrity_passes(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="atomic-3", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
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
        claim = admit_execution(client, "n" * 32)
        attempt = claim["claim"]["attempt"]
        with self.assertRaises(BoardError) as caught:
            client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, {
                    "status": "ok",
                    "result": {"status": "ok"},
                    "shutdownConfirmed": True,
                    "artifacts": [{"location": str(cwd / "missing.bin"), "kind": "file"}],
                })
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
        first = client.submit(requestId="dup", task="same", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        again = client.submit(requestId="dup", task="same", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["task"]["runId"], first["runId"])
        self.assertEqual(board.store.count_tasks(), 1)

    def test_changed_idempotency_input_conflicts(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="changed", task="same", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        with self.assertRaises(BoardError) as caught:
            client.submit(requestId="changed", task="different", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_conflict_survives_completion_and_restart(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="restart-conflict", task="same", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        client.cancel(runId=task["runId"])
        store_two = type(board.store)(self.directory, max_concurrent=2)
        store_two.initialize()
        with self.assertRaises(BoardError) as caught:
            store_two.task_submit({"requestId": "restart-conflict", "task": "changed", "cwd": str(cwd),
                                   "adapter": "command", "argv": ["/bin/true"]})
        self.assertEqual(caught.exception.code, "CONFLICT")




    def test_capacity_queues_instead_of_rejecting(self):
        board = self.board(max_concurrent=1)
        client = board.client()
        first_cwd = self.workdir("a")
        second_cwd = self.workdir("b")
        pass  # Admission is owned by the service.
        pass  # Admission is owned by the service.
        client.submit(requestId="cap-1", task="one", cwd=str(first_cwd), adapter="command", argv=["/bin/true"])
        admit_execution(client, "a" * 32)
        # Queued admission, not BUSY: the second task is accepted and says why it waits.
        second = client.submit(requestId="cap-2", task="two", cwd=str(second_cwd), adapter="command", argv=["/bin/true"])["task"]
        self.assertEqual(second["status"], "queued")
        self.assertEqual(second["queueReason"], "capacity")
        empty = admit_execution(client, "c" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "capacity")

    def test_overlapping_cwd_reserves_resources(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        parent = self.workdir("repo")
        child = parent / "nested"
        child.mkdir()
        pass  # Admission is owned by the service.
        pass  # Admission is owned by the service.
        client.submit(requestId="cwd-1", task="one", cwd=str(parent), adapter="command", argv=["/bin/true"])
        admit_execution(client, "a" * 32)
        other = client.submit(requestId="cwd-2", task="two", cwd=str(child), adapter="command", argv=["/bin/true"])["task"]
        self.assertEqual(other["queueReason"], "cwd-overlap")
        self.assertIsNone(admit_execution(client, "c" * 32)["claim"])

    def test_exclusive_resources_reserve(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        pass  # Admission is owned by the service.
        client.submit(
            requestId="res-1", task="one", cwd=str(self.workdir("r1")), adapter="command", argv=["/bin/true"],
            exclusiveResources=["gpu:0"],
        )
        admit_execution(client, "a" * 32)
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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        release_before_spawn(client, attempt["attemptId"], reason="test release")
        self.assertEqual(board.store.resource_claims(), [], "a released attempt frees its reservation")
        client.retry(runId=task["runId"], reason="test retry")
        claim_two = admit_execution(client, "a" * 32)
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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "failed", "result": {"status": "nonzero"}, "shutdownConfirmed": True, "error": "boom"})
        client.retry(runId=task["runId"], reason="again")
        pass  # Admission is owned by the service.
        second = admit_execution(client, "b" * 32)["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        with self.assertRaises(BoardError) as caught:
            client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True})
        self.assertEqual(caught.exception.code, "STALE_GENERATION")

    def test_rejected_execution_review_is_archived_before_retry_and_new_acceptance(self):
        board = self.board()
        client = board.client()
        task = client.submit(requestId="review-retry", task="do", cwd=str(self.workdir()),
                             adapter="command", argv=["/bin/true"])["task"]
        pass  # Admission is owned by the service.
        first = admit_execution(client, "a" * 32)["claim"]["attempt"]
        client.submit_result(first["attemptId"], first["generation"], "a" * 32, {"status": "failed", "result": {"status": "nonzero"},
                              "shutdownConfirmed": True, "error": "needs revision"})

        params = {"runId": task["runId"], "note": "output needs revision", "verdict": "rejected"}
        rejected = board.call("task_acknowledge", params)
        self.assertFalse(rejected["duplicate"])
        self.assertIsNone(rejected["task"]["acceptedAt"])
        self.assertIsNone(rejected["task"]["acceptanceVerdict"])
        self.assertIsNotNone(rejected["task"]["reviewRejectedAt"])
        self.assertEqual(rejected["task"]["reviewRejectionNote"], "output needs revision")
        self.assertTrue(board.call("task_acknowledge", params)["duplicate"])
        with self.assertRaises(BoardError) as caught:
            board.call("task_acknowledge", {"runId": task["runId"], "note": "now accepted"})
        self.assertEqual(caught.exception.code, "CONFLICT")

        retried = client.retry(runId=task["runId"], reason="revise output")
        self.assertIsNone(retried["reviewRejectedAt"])
        self.assertIsNone(retried["reviewRejectionNote"])
        events = board.call("events_read", {"runId": task["runId"], "after": 0})["events"]
        archived = [event for event in events if event["kind"] == "task.review_archived"]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["payload"]["verdict"], "rejected")
        self.assertEqual(archived[0]["payload"]["reviewRejectedAt"], rejected["task"]["reviewRejectedAt"])
        self.assertEqual(archived[0]["payload"]["note"], "output needs revision")

        second = admit_execution(client, "b" * 32)["claim"]["attempt"]
        client.submit_result(second["attemptId"], second["generation"], "b" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True})
        accepted = board.call("task_acknowledge", {"runId": task["runId"], "note": "revised output"})["task"]
        self.assertEqual(accepted["acceptanceVerdict"], "accepted")
        self.assertIsNotNone(accepted["acceptedAt"])
        self.assertIsNone(accepted["reviewRejectedAt"])


class TestCrashWindows(BoardTestCase):
    """Group 3: no duplicate execution and no silently lost committed result."""

    def _isolated_worker_claim(self, adapter: str):
        task_id = f"stop-proof-{adapter}"
        attempt_id = f"attempt-{adapter}"
        native_harness = "codex" if adapter == "decision" else adapter
        client = SimpleNamespace(
            progress=lambda *_args, **_kwargs: None,
            call=lambda *_args, **_kwargs: {
                "harness": {"harness": native_harness, "available": True, "revision": 1, "command": ["mock"]}
            },
        )
        claim = {
            "task": {"taskId": task_id, "spec": {
                "adapter": adapter, "task": "inspect stop evidence", "cwd": str(self.workdir()), **({"routerBudgetSeconds": 60} if adapter == "decision" else {"expectedSeconds": 60}),
            }},
            "attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": 1},
            "decisionInput": {"routingMode": "fast", "buddy": {"harness": "codex"}}
            if adapter == "decision" else None,
        }
        execution = Execution(claim, self.directory, "a" * 32,
                              client=client, log=lambda _message: None)
        return execution, claim

    @staticmethod
    def _finished_fake_handle():
        handle = ProcessHandle(mock.Mock(pid=123), own_group=False, log_paths={})
        handle.pgid = 123
        handle.wait = lambda timeout: 0
        handle.shutdown_confirmed = lambda: True
        handle.identity = lambda: {
            'bootId': 'macos:00000000-0000-4000-8000-000000000001', 'pid': 123,
            'startedAt': 10, 'processGroupId': 123, 'jobName': None,
}
        return handle

    def test_crash_before_spawn_releases_the_attempt_honestly(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-pre", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        board.store.release_execution(claim["claim"]["attempt"]["attemptId"])
        task = client.get(runId=claim["claim"]["task"]["runId"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attemptState"], "finished")
        self.assertEqual(
            board.store.active_work()["attempts"], [], "a never-spawned attempt must not retain resources"
        )

    # Real killed-Worker recovery is covered by buddy/runtime/test_repair_recovery.py.

    def test_native_controller_exit_does_not_prove_native_stop_after_collect_failure(self):
        """A gone outer controller cannot release credentials for an unseen native child."""
        import hey_my_buddy.buddy.runtime.execution as worker_module

        for adapter in ("codex", "zcode", "claude", "dsh", "decision"):
            with self.subTest(adapter=adapter):
                worker, claim = self._isolated_worker_claim(adapter)
                private_harness = "codex" if adapter == "decision" else adapter
                private_root = ensure_private_dir(attempt_root(
                    self.directory, private_harness, claim["task"]["taskId"], claim["attempt"]["attemptId"]
                ))
                credential = private_root / "agent-credential.json"
                credential.write_text('{"token":"private-test-sentinel"}')
                handle = self._finished_fake_handle()
                handle.shutdown_confirmed = lambda: False
                cancelled = []

                def collect_failure(_handle, _context):
                    raise RuntimeError("native collection failed")

                fake_adapter = SimpleNamespace(
                    name=claim["task"]["spec"]["adapter"],
                    available=lambda: (True, None), prepare=lambda _context: None,
                    start=lambda _context: handle, collect=collect_failure,
                    cancel=lambda owned: cancelled.append(owned),
                )
                with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                        mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}):
                    receipt = worker.execute()
                self.assertFalse(receipt["report"]["shutdownConfirmed"])
                self.assertEqual(cancelled, [handle])
                self.assertTrue(credential.exists(), "unknown native stop must retain credentials")

    def test_native_collect_proof_survives_later_receipt_failure(self):
        """A durable receipt error cannot erase a completed native stop proof."""
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("codex")
        private_root = ensure_private_dir(attempt_root(
            self.directory, "codex", claim["task"]["taskId"], claim["attempt"]["attemptId"]
        ))
        credential = private_root / "agent-credential.json"
        credential.write_text('{"token":"private-test-sentinel"}')
        handle = self._finished_fake_handle()
        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=lambda _context: None,
            start=lambda _context: handle,
            collect=lambda _handle, _context: AdapterOutcome(
                status="failed", result={"status": "native-error"}, shutdown_confirmed=True,
            ),
            cancel=lambda _handle: self.fail("proved stopped handle must not need cancellation"),
        )
        original_receipt = worker.receipt
        calls = 0

        def fail_first_receipt(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected receipt failure")
            return original_receipt(*args)

        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}), \
                mock.patch.object(worker, "receipt", side_effect=fail_first_receipt):
            receipt = worker.execute()
        self.assertEqual(calls, 2)
        self.assertTrue(receipt["report"]["shutdownConfirmed"])
        self.assertFalse(credential.exists(), "native stop proof permits credential cleanup")

    def test_persistent_cleanup_error_cannot_erase_native_stop_or_prevent_receipt(self):
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("claude")
        private_root = ensure_private_dir(attempt_root(
            self.directory, "claude", claim["task"]["taskId"], claim["attempt"]["attemptId"]
        ))
        credential = private_root / "agent-credential.json"
        credential.write_text('{"token":"private-test-sentinel"}')
        handle = self._finished_fake_handle()
        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=lambda _context: None, start=lambda _context: handle,
            collect=lambda _handle, _context: AdapterOutcome(status="failed", shutdown_confirmed=True),
            cancel=lambda _handle: self.fail("complete native stop proof already exists"),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}), \
                mock.patch.object(worker, "_cleanup_attempt_credentials", side_effect=RuntimeError("cleanup unavailable")):
            receipt = worker.execute()
        self.assertTrue(receipt["report"]["shutdownConfirmed"])
        self.assertEqual(receipt["report"]["status"], "failed")
        self.assertTrue(credential.exists(), "cleanup failed, so credentials must remain for diagnosis")

    def test_virtual_adapter_receipt_retains_evidence_failure_paths(self):
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("decision")
        handle = self._finished_fake_handle()
        private_root = attempt_root(self.directory, "codex", claim["task"]["taskId"], claim["attempt"]["attemptId"])
        diagnostic = {"status": "failed", "privateRoot": str(private_root / "no-tool-fixture"),
                      "evidenceRoot": str(self.directory / "attempts" / claim["task"]["taskId"] /
                                          claim["attempt"]["attemptId"] / "no-tool-fixture")}
        handle.evidence_retention_failure = diagnostic
        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=lambda _context: None, start=lambda _context: handle,
            collect=lambda _handle, _context: AdapterOutcome(
                status="failed", result={"status": "error", "code": "evidence-retention-failed"},
                error="evidence-retention-failed", shutdown_confirmed=True),
            cancel=lambda _handle: self.fail("complete native stop proof already exists"),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}):
            receipt = worker.execute()
        self.assertTrue(receipt["report"]["shutdownConfirmed"])
        self.assertEqual(receipt["report"]["result"]["evidenceRetention"], diagnostic)

    def test_retry_start_discounts_previous_stop_proof_and_keeps_credentials(self):
        """The next start can spawn before raising without returning a handle."""
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("codex")
        private_root = ensure_private_dir(attempt_root(
            self.directory, "codex", claim["task"]["taskId"], claim["attempt"]["attemptId"]
        ))
        credential = private_root / "agent-credential.json"
        first_handle = self._finished_fake_handle()
        starts = 0

        def start(_context):
            nonlocal starts
            starts += 1
            credential.write_text('{"token":"private-test-sentinel"}')
            if starts == 2:
                raise RuntimeError("spawn may have happened before start returned")
            return first_handle

        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=lambda _context: None,
            start=start,
            collect=lambda _handle, _context: AdapterOutcome(
                status="failed", result={"modelStarted": False, "code": "native-exit"},
                shutdown_confirmed=True,
            ),
            cancel=lambda _handle: self.fail("the prior stopped handle is no longer current"),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}):
            receipt = worker.execute()
        self.assertEqual(starts, 2, "the first confirmed pre-model failure must trigger one retry")
        self.assertFalse(receipt["report"]["shutdownConfirmed"])
        self.assertTrue(credential.exists(), "second start's uncertain child retains its credential")

    def test_start_error_without_handle_does_not_retry_unknown_child(self):
        """A matching executable error cannot prove start failed before spawn."""
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("codex")
        private_root = ensure_private_dir(attempt_root(
            self.directory, "codex", claim["task"]["taskId"], claim["attempt"]["attemptId"]
        ))
        credential = private_root / "agent-credential.json"
        starts = 0

        def uncertain_start(_context):
            nonlocal starts
            starts += 1
            credential.write_text('{"token":"private-test-sentinel"}')
            raise FileNotFoundError(2, "injected after possible spawn", "mock")

        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=lambda _context: None,
            start=uncertain_start,
            collect=lambda _handle, _context: self.fail("no handle was returned"),
            cancel=lambda _handle: self.fail("no handle was returned"),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}):
            receipt = worker.execute()
        self.assertEqual(starts, 1, "unknown first start cannot safely launch a retry")
        self.assertFalse(receipt["report"]["shutdownConfirmed"])
        self.assertTrue(credential.exists(), "unknown stop retains the credential")

    def test_retry_preparation_failure_preserves_previous_stop_proof(self):
        """A failed validation before the next start leaves the prior proof intact."""
        import hey_my_buddy.buddy.runtime.execution as worker_module

        worker, claim = self._isolated_worker_claim("codex")
        prepared = 0
        started = 0

        def prepare(_context):
            nonlocal prepared
            prepared += 1
            if prepared == 2:
                raise BoardError("HARNESS_UNAVAILABLE", "injected retry validation failure")

        def start(_context):
            nonlocal started
            started += 1
            return self._finished_fake_handle()

        fake_adapter = SimpleNamespace(
            name=claim["task"]["spec"]["adapter"],
            available=lambda: (True, None), prepare=prepare, start=start,
            collect=lambda _handle, _context: AdapterOutcome(
                status="failed", result={"modelStarted": False, "code": "native-exit"},
                shutdown_confirmed=True,
            ),
            cancel=lambda _handle: self.fail("the prior handle already stopped"),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=fake_adapter), \
                mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "test"}):
            receipt = worker.execute()
        self.assertEqual(prepared, 2)
        self.assertEqual(started, 1, "the second preparation must fail before another spawn")
        self.assertTrue(receipt["report"]["shutdownConfirmed"])

    def test_commit_before_lost_reply_is_replayed_not_duplicated(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-reply", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        report = {
            "status": "ok",
            "result": {"status": "ok"},
            "shutdownConfirmed": True,
        }
        with self.assertRaises(BoardError) as caught:
            client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32,
                                 {**report, "commandId": "retired-result-command"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        first = client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, dict(report))
        second = client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, dict(report))
        self.assertTrue(second.get("duplicate") or second.get("replayed"))
        completed = [event for event in board.store.events_read({"after": 0})["events"] if event["kind"] == "task.completed"]
        self.assertEqual(len(completed), 1, "a replayed result must not append a second completion event")

    def test_conflicting_replay_of_the_same_attempt_fails(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="crash-conflict", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True})
        with self.assertRaises(BoardError) as caught:
            client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "failed", "result": {"status": "nonzero"}, "shutdownConfirmed": True})
        self.assertEqual(caught.exception.code, "CONFLICT")


class TestLifecycle(BoardTestCase):
    """Group 4: wait interruption and daemon restart keep the same attempt."""

    def test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result(self):
        with self.daemon() as first:
            cwd = self.workdir()
            artifact = self.directory / "artifact.txt"
            code, task = self.cli(
                "execution-submit",
                json.dumps(
                    {
                        "requestId": "life-1",
                        "task": "produce an artifact",
                        "cwd": str(cwd),
                        "adapter": "command",
                        "argv": ["/bin/sh", "-c", f"sleep 8; echo durable > {artifact}"],
                        "expectedSeconds": 120,
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
            generation = before["attemptGeneration"]
            # Interrupt the waiting client: it observes the same task and never cancels it.
            waiting = subprocess.Popen(
                [sys.executable, "-m", "hey_my_buddy.cli.main", "await", json.dumps({"runId": run_id, "waitSeconds": 60})],
                env={
                    **os.environ,
                    "HMB_STATE_DIR": str(self.directory),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[4] / "src"),
                    "HMB_DEV_SOURCE": "1",
                    "HMB_RUNTIME_ROOT": str(self.directory / "runtime-root"),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.children.append(waiting)
            # Wait until the daemon has actually admitted the client's wait; that is
            # stronger evidence than elapsed time that it is genuinely waiting.
            from hey_my_buddy.protocol.transport import ServiceError, _read_endpoint, _request

            def wait_admitted() -> bool:
                endpoint = _read_endpoint(self.directory)
                if endpoint is None:
                    return False
                try:
                    return _request(endpoint, "wait_capacity", {}, resource="wait",
                                    state_dir=self.directory).get("admitted", 0) >= 1
                except ServiceError:
                    return False

            self.assertTrue(wait_for(wait_admitted, 20), "the client was never admitted before it was interrupted")
            self.assertIsNone(waiting.poll(), "the client must be waiting before it is interrupted")
            waiting.send_signal(signal.SIGINT)
            abandoned_stdout, abandoned_stderr = waiting.communicate(timeout=20)
            self.assertEqual(waiting.returncode, 1, abandoned_stderr)
            abandoned = json.loads(abandoned_stdout)
            self.assertEqual(abandoned["error"]["code"], "WAIT_ABANDONED")
            self.assertEqual(abandoned["runId"], run_id)
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
            first.wait(timeout=25)
        with self.daemon() as second:
            self.assertIsNotNone(second.pid)
            status = self.cli("status", json.dumps({"runId": run_id}))[1]
            self.assertEqual(status["selectedAttemptId"], attempt_id, "the same attempt must survive the restart")
            self.assertEqual(status["attemptGeneration"], generation)
            self.assertEqual(status["expectedSeconds"], 120, "the expected duration must survive the restart")
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

    def test_restart_retains_a_started_execution_and_its_claims(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        client.submit(requestId="life-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim['claim']['attempt']
        identity = {'bootId': 'macos:00000000-0000-0000-0000-000000000001', 'pid': 123,
                    'startedAt': 123250000, 'processGroupId': 123, 'jobName': None}
        client.progress(attempt['attemptId'], attempt['generation'], 'a' * 32, process_identity=identity)
        with mock.patch('hey_my_buddy.protocol.process_identity.inspect_process_identity', return_value='alive'):
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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        with self.assertRaises(BoardError) as caught:
            client.progress(attempt["attemptId"], attempt["generation"], "b" * 32)
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")

    def test_stale_generation_is_rejected_and_cannot_mutate(self):
        board = self.board()
        client = board.client()
        cwd = self.workdir()
        task = client.submit(requestId="id-2", task="do", cwd=str(cwd), adapter="command", argv=["/bin/true"])["task"]
        pass  # Admission is owned by the service.
        first = admit_execution(client, "a" * 32)["claim"]["attempt"]
        release_before_spawn(client, first["attemptId"], reason="test")
        client.retry(runId=task["runId"], reason="test")
        pass  # Admission is owned by the service.
        second = admit_execution(client, "b" * 32)["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        for call in (
            lambda: client.progress(first["attemptId"], first["generation"], "a" * 32),
            lambda: client.submit_result(first["attemptId"], first["generation"], "a" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True}),
        ):
            with self.assertRaises(BoardError) as caught:
                call()
            self.assertIn(caught.exception.code, ("STALE_GENERATION", "UNAUTHORIZED", "ATTEMPT_FINISHED"))
        self.assertEqual(client.get(runId=task["runId"])["attemptGeneration"], 2)




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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        cursor = board.store.head()
        results: list[str] = []
        barrier = threading.Barrier(9, timeout=30)

        def waiter():
            barrier.wait()
            try:
                client.wait_events(after=cursor, timeout_ms=2000)
                results.append("ok")
            except BoardError as error:
                results.append(error.code)

        threads = [threading.Thread(target=waiter) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        self.assertTrue(wait_for(lambda: board.admission.stats()["admitted"] == 2
                                 and board.admission.stats()["rejected"] >= 1, 10),
                        "two waits must occupy capacity and an overflow must be observed")
        # Mutations must stay responsive: renew, cancel and the result commit.
        client.progress(attempt["attemptId"], attempt["generation"], "a" * 32)
        client.cancel(runId=task["runId"], reason="race")
        client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True})
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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        client.cancel(runId=task["runId"], reason="cancel then complete")
        completed = client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "ok", "result": {"status": "ok"}, "shutdownConfirmed": True})
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
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "a" * 32)
        attempt = claim["claim"]["attempt"]
        result = client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "cancelled", "result": {"status": "cancelled"}, "shutdownConfirmed": False})
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
        pass  # Admission is owned by the service.
        admit_execution(client, "a" * 32)
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
        self.assertFalse(observed["bridge"]["canObserve"])
        self.assertFalse(observed["bridge"]["canAsk"])
        self.assertTrue(observed["bridge"]["reason"])
        self.assertEqual(observed["limits"]["maxQuestionBytes"], 4000)
        self.assertEqual(observed["limits"]["maxInquiriesPerRun"], 32)

    def test_correlated_answer_survives_a_daemon_restart(self):
        with self.daemon():
            cwd = self.workdir()
            code, task = self.cli(
                "execution-submit",
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
        pass  # Admission is owned by the service.
        admit_execution(client, "a" * 32)
        client.post_question("q-known", "hello?", runId=task["runId"])
        with self.assertRaises(BoardError):
            board.call("message_update", {"runId": task["runId"], "inquiryId": "q-unknown", "answer": {"text": "hi"}})
        with self.assertRaises(BoardError) as caught:
            board.call("message_update", {"runId": task["runId"], "inquiryId": "q-known", "state": "answered"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


class TestExtensibility(BoardTestCase):
    def setUp(self):
        super().setUp()

    """Group 7: built-in adapters plus a real external worker over the public API."""

    def test_capabilities_are_reported_honestly(self):
        board = self.board()
        report = board.call("capabilities", {})
        self.assertIn("dsh", report["adapters"])
        self.assertIn("command", report["adapters"])
        self.assertNotIn("external", report["adapters"])
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
        run_next(client)
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
        run_next(client)
        result = client.result(runId=task["runId"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result['result']['code'], 'ADAPTER_UNAVAILABLE')
        self.assertEqual(result['result']['stopEvidence'], {
            'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}})
        self.assertIn("ADAPTER_UNAVAILABLE", result["resultMeta"]["error"])
        self.assertTrue(result["shutdownConfirmed"], "no process was ever created, so shutdown is confirmed")
        self.assertEqual(client.get(runId=task["runId"])["status"], "failed")




class TestPackaging(BoardTestCase):
    """Group 8 (service side): runtime identity, no source leaks, staging."""

    def test_runtime_identity_is_reported_and_stable(self):
        from hey_my_buddy.install import runtime

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
        from hey_my_buddy.install import runtime

        if not shutil_module.which(os.environ.get("UV_BIN", "uv")):
            self.skipTest("uv is not available")
        runtime_root = self.directory / "runtime-root"
        environment = {"HMB_DEV_SOURCE": "", "HMB_RUNTIME_ROOT": str(runtime_root)}
        self.addCleanup(lambda: self.cli("stop", "{}", env=environment, timeout=60))
        # The production cold-start path: no daemon pre-started by the test.
        code, health = self.cli("health", env=environment, timeout=600)
        install_log = self.directory / "runtime-install.log"
        self.assertEqual(code, 0, {"health": health,
                                  "installLog": install_log.read_text()[-8000:] if install_log.exists() else None})
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
        self.assertEqual(set(actual["resources"]), {"console.assets", "roles.worker", "roles.router", "roles.lead", "roles.wrap_up"})
        for name, value in actual["resources"].items():
            self.assertTrue(os.path.realpath(value).startswith(root), actual)
            self.assertTrue(Path(value).exists(), name)
        self.assertFalse(info["identity"]["leaks"])
        # Real work runs through the runtime-hosted daemon and its worker.
        code, task = self.cli(
            "execution-submit",
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
        from hey_my_buddy.install import runtime

        copied = {relative for relative, _path in runtime.iter_assets(runtime.project_root())}
        for forbidden in (".venv", "node_modules", ".env", "tests", ".git"):
            self.assertFalse(any(part == forbidden for path in copied for part in Path(path).parts), forbidden)
        self.assertFalse(any("__pycache__" in Path(path).parts for path in copied))


class TestTransitionTables(BoardTestCase):
    """The documented transition tables are the ones enforced."""

    def test_illegal_task_transition_is_rejected(self):
        board = self.board()
        client = board.client()
        task = client.submit(requestId="t-1", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"])["task"]
        client.cancel(runId=task["runId"])
        from hey_my_buddy.errors import BoardError as _BoardError

        with board.store.db.write() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task["runId"],)).fetchone()
            with self.assertRaises(_BoardError) as caught:
                board.store._transition_task(connection, row, "running")
        self.assertEqual(caught.exception.code, "ILLEGAL_TRANSITION")

    def test_tables_cover_every_declared_state(self):
        from hey_my_buddy.blackboard.store.db import ATTEMPT_STATES, TASK_STATES

        self.assertEqual(set(TASK_TRANSITIONS), set(TASK_STATES))
        self.assertEqual(set(ATTEMPT_TRANSITIONS), set(ATTEMPT_STATES))


class TestTaskHistory(BoardTestCase):
    """Group 11: read-only micro task metadata and filtered task history."""

    MICRO_TASK_FIELDS = (
        "kind",
        "sourceHostId",
        "currentHostId",
        "parentRunId",
        "rootRunId",
        "project",
        "buddy",
        "delegationAuthority",
        "leadOwnership",
        "rootLeadRunId",
    )

    def submit_one(self, board, request_id: str, *, name: str = "work"):
        return board.client().submit(
            requestId=request_id,
            task=f"do {request_id}",
            cwd=str(self.workdir(name)),
            adapter="command",
            argv=["/bin/true"],
        )["task"]

    def test_every_task_view_carries_the_micro_task_contract(self):
        board = self.board()
        created = self.submit_one(board, "hist-1")
        listing = board.call("task_list", {"limit": 10})
        self.assertEqual(sorted(listing), sorted(["runs", "tasks", "total", "cursor", "nextCursor"]))
        self.assertEqual(listing["runs"], listing["tasks"])
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["cursor"], board.store.head())
        self.assertIsNone(listing["nextCursor"])
        metadata = listing["runs"][0]["lineage"]
        self.assertEqual(sorted(metadata), sorted(self.MICRO_TASK_FIELDS))
        self.assertEqual(metadata["kind"], "execution")
        self.assertIsNone(metadata["sourceHostId"])
        self.assertIsNone(metadata["currentHostId"])
        self.assertIsNone(metadata["parentRunId"])
        self.assertIsNone(metadata["rootRunId"])
        self.assertIsNone(metadata["buddy"])
        self.assertFalse(metadata["delegationAuthority"])
        self.assertIsNone(metadata["leadOwnership"])
        self.assertIsNone(metadata["rootLeadRunId"])
        self.assertEqual(sorted(metadata["project"]), sorted(["id", "path", "label"]))
        self.assertEqual(metadata["project"]["path"], os.path.realpath(str(self.workdir())))
        self.assertEqual(board.call("task_get", {"runId": created["runId"]})["task"]["lineage"], metadata)
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(snapshot["tasks"]["runs"][0]["lineage"], metadata)
        self.assertIsNone(snapshot["tasks"]["nextCursor"])
        self.assertEqual(snapshot["tasks"]["total"], 1)

    def test_existing_filters_compose_and_the_cursor_pages_the_filtered_set(self):
        board = self.board()
        client = board.client()
        for index in range(3):
            self.submit_one(board, f"hist-{index}", name=f"hist-{index}")
        cancelled = self.submit_one(board, "hist-cancelled", name="hist-cancelled")
        client.cancel(runId=cancelled["runId"])
        first = board.call("task_list", {"limit": 2, "state": "queued", "adapter": "command"})
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["runs"]), 2)
        self.assertIsNotNone(first["nextCursor"])
        self.assertEqual(board.call("task_list", {"state": "cancelled"})["total"], 1)
        self.assertEqual(board.call("task_list", {"adapter": "claude"})["total"], 0)
        rest = board.call(
            "task_list", {"limit": 2, "before": first["nextCursor"], "state": "queued", "adapter": "command"}
        )
        self.assertEqual(len(rest["runs"]), 1)
        self.assertIsNone(rest["nextCursor"])
        seen = {row["runId"] for row in [*first["runs"], *rest["runs"]]}
        self.assertEqual(seen, {row["runId"] for row in board.call("task_list", {"state": "queued"})["runs"]})

    def test_a_page_is_bounded_and_never_materializes_the_whole_board(self):
        board = self.board()
        for index in range(12):
            self.submit_one(board, f"hist-{index:02d}", name=f"hist-{index:02d}")
        page = board.call("task_list", {"limit": 5})
        self.assertEqual(len(page["runs"]), 5)
        self.assertEqual(page["total"], 12)
        self.assertIsNotNone(page["nextCursor"])
        # The cursor is opaque: it carries the (createdAt, taskId) keyset position and
        # never an offset the caller could turn into a full table walk.
        import base64

        cursor = page["nextCursor"]
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        self.assertEqual(sorted(decoded), ["createdAt", "taskId", "v"])
        self.assertEqual(decoded["taskId"], page["runs"][-1]["runId"])
        self.assertEqual(decoded["createdAt"], page["runs"][-1]["createdAt"])
