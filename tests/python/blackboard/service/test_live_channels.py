"""Private command channels survive waits and bounded service restarts."""
from __future__ import annotations

import time

from support import BoardTestCase, wait_for
from blackboard.service import test_execution_recovery as recovery_fixture
from hey_my_buddy.protocol.process_identity import inspect_process_identity, kill_verified_process_group


class PrivateLiveChannelTests(BoardTestCase):
    client = recovery_fixture.PrivateExecutionRecoveryTests.client
    command = recovery_fixture.PrivateExecutionRecoveryTests.command
    _running = recovery_fixture.PrivateExecutionRecoveryTests._running
    restart = recovery_fixture.PrivateExecutionRecoveryTests.restart
    _finished = recovery_fixture.PrivateExecutionRecoveryTests._finished
    _live_bridge = recovery_fixture.PrivateExecutionRecoveryTests._live_bridge
    freeze_execution = recovery_fixture.PrivateExecutionRecoveryTests.freeze_execution
    resume_execution = recovery_fixture.PrivateExecutionRecoveryTests.resume_execution

    def test_task_wait_preserves_an_already_connected_command_channel(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            run_id, release, before = self.command("wait-preserves-live")
            try:
                self.assertIsNotNone(wait_for(lambda: self._live_bridge(run_id), timeout=30))
                waiting = self.client().wait_task(runId=run_id, timeout_ms=100,
                                                  afterRevision=self.client().get(runId=run_id)["revision"])
                self.assertEqual(waiting["selectedAttempt"]["attemptId"], before["attemptId"])
                observed = self.client().call("inquiry_observe", {"runId": run_id, "timeoutMs": 1000})
                self.assertTrue(observed["bridge"]["enabled"], observed)
                release.touch()
                self.assertIsNotNone(wait_for(lambda: self._finished(run_id), timeout=30))
            finally:
                release.touch()

    def test_restart_releases_owner_locks_with_a_frozen_execution(self):
        from hey_my_buddy.install.upgrade import OWNER_LOCK_TIMEOUT_SECONDS, file_lock
        identity = None
        frozen_process = None
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
                run_id, release, before = self.command("frozen-restart")
                identity = before["processIdentity"]
                self.assertIsNotNone(wait_for(lambda: self._live_bridge(run_id), timeout=30))
                frozen_process = self.freeze_execution(identity)
                started = time.monotonic()
                self.restart(daemon)
                with file_lock(self.directory / "control-daemon.lock", timeout=OWNER_LOCK_TIMEOUT_SECONDS), \
                     file_lock(self.directory / "board-owner.lock", timeout=OWNER_LOCK_TIMEOUT_SECONDS):
                    elapsed = time.monotonic() - started
                print(f"Frozen execution restart and owner-lock release: {elapsed:.3f}s", flush=True)
                self.assertLess(elapsed, OWNER_LOCK_TIMEOUT_SECONDS)
            self.assertEqual(inspect_process_identity(identity), "alive")
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                print(f"Frozen execution restart through replacement readiness: {time.monotonic() - started:.3f}s",
                      flush=True)
                self.resume_execution(identity, frozen_process)
                release.touch()
                self.assertIsNotNone(wait_for(lambda: self._finished(run_id), timeout=35))
        finally:
            if identity is not None and inspect_process_identity(identity) == "alive":
                self.resume_execution(identity, frozen_process)
                release.touch()
                wait_for(lambda: inspect_process_identity(identity) == "identity-absent", timeout=5)
                kill_verified_process_group(identity)

