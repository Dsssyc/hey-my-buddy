"""The managed pool under the one machine-wide ceiling (ADR-011).

The pool is sized to ``BUDDY_MAX_CONCURRENT`` total slots — there are no separate
business/decision supervisors any more. Ownership stays manifest-based: only the
exact IDs this pool started are managed, and a scale-down keeps surplus members
until their evidence is settled.
"""
from __future__ import annotations

import json
import fcntl
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

from support import BoardTestCase

from hey_my_buddy.blackboard.service.daemon import WorkerPool


class WorkerPoolSizingTests(BoardTestCase):
    def test_new_start_reclaims_a_slot_whose_old_stopped_owner_exits_late(self):
        board = self.board()
        pool = WorkerPool(self.directory, prefix="local", total_limit=1)
        handle = pool.handle("local")
        handle.prepare()
        fd = os.open(handle.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        process = mock.Mock()
        process.poll.return_value = None
        try:
            handle.request_stop()  # the previous daemon's owner has not exited yet
            with mock.patch("hey_my_buddy.blackboard.service.daemon.subprocess.Popen", return_value=process) as spawn, mock.patch(
                "hey_my_buddy.blackboard.service.daemon.runtime.launch_target", return_value={"python": sys.executable, "pythonPath": None, "stable": False}
            ):
                pool.start()
                spawn.assert_not_called()  # never duplicate a live owner
                self.assertFalse(handle.stop_request.exists(), "new startup must withdraw its predecessor's stop")
                fcntl.flock(fd, fcntl.LOCK_UN)  # the old owner now finishes exiting
                report = pool.reconcile(board.store)
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(report["stoppedWorkerIds"], [])
        finally:
            os.close(fd)

    def test_the_pool_has_one_supervisor_slot_per_machine_wide_slot(self):
        pool = WorkerPool(self.directory, prefix="local", total_limit=3)
        self.assertEqual(pool.worker_ids, ["local", "local-2", "local-3"])
        self.assertEqual(pool.total_limit, 3)
        pool = WorkerPool(self.directory, prefix="team", total_limit=1)
        self.assertEqual(pool.worker_ids, ["team"])

    def test_the_report_carries_the_total_ceiling_and_no_lane_fields(self):
        pool = WorkerPool(self.directory, prefix="local", total_limit=4)
        report = pool.report()
        self.assertEqual(report["totalLimit"], 4)
        self.assertEqual(report["workerIds"], ["local", "local-2", "local-3", "local-4"])
        self.assertNotIn("businessLimit", report)
        self.assertNotIn("decisionLimit", report)

    def test_the_manifest_records_the_exact_owned_ids_durably(self):
        pool = WorkerPool(self.directory, prefix="local", total_limit=2)
        pool._adopt_manifest()
        manifest = json.loads((self.directory / "worker-pool.json").read_text())
        self.assertEqual(manifest["workerIds"], ["local", "local-2"])
        # Re-adopting with a smaller ceiling keeps the previously owned IDs as
        # surplus members: they are never forgotten while evidence may exist.
        smaller = WorkerPool(self.directory, prefix="local", total_limit=1)
        smaller._adopt_manifest()
        self.assertEqual(smaller.managed_ids(), ["local", "local-2"])

    def test_a_manifest_with_malformed_entries_drops_them_instead_of_using_them(self):
        path = self.directory / "worker-pool.json"
        path.write_text(json.dumps({"workerIds": ["local", 7, "../escape", "local-9", "local"]}))
        pool = WorkerPool(self.directory, prefix="local", total_limit=1)
        pool._adopt_manifest()
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["workerIds"], ["local", "local-9"])

    def test_a_missing_manifest_file_is_created_with_the_desired_ids(self):
        fresh = self.directory / "fresh"
        fresh.mkdir(parents=True)
        pool = WorkerPool(fresh, prefix="local", total_limit=2)
        pool._adopt_manifest()
        manifest = json.loads((fresh / "worker-pool.json").read_text())
        self.assertEqual(manifest["workerIds"], ["local", "local-2"])


if __name__ == "__main__":
    unittest.main()
