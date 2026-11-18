"""Canonical cwd paths, observation failures and privacy before deletion."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from hey_my_buddy.blackboard.tasks import checkout_processes as guard
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.owned_process import owned_popen
from hey_my_buddy.protocol.process_handle import ProcessHandle


@unittest.skipUnless(os.name == "nt", "native Windows rename occupancy proof")
class WindowsCheckoutIdleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-win-idle-")
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name).resolve() / "workspaces" / ("ws-" + "1" * 32) / "checkout"
        (self.checkout / "nested").mkdir(parents=True)
        (self.checkout / "tracked.txt").write_bytes(b"checkout content")
        (self.checkout / "nested" / "evidence.txt").write_bytes(b"nested content")

    @contextmanager
    def occupied(self, cwd):
        child = owned_popen(
            [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.buffer.read(1)"],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        handle = ProcessHandle(child, log_paths={}, stop_channel="stdin")
        try:
            # The directory is a live working directory only once the child runs.
            self.assertEqual(child.stdout.readline().rstrip(b"\r\n"), b"ready")
            yield
        finally:
            stopped, _ = handle.terminate(grace_seconds=5.0, settle_seconds=5.0)
            if child.stdout is not None:
                child.stdout.close()
            if child.stdin is not None:
                child.stdin.close()
            if not stopped:
                self.fail("Occupying Windows process tree did not confirm shutdown")

    def contents(self):
        return {(str(path.relative_to(self.checkout)), path.read_bytes())
                for path in self.checkout.rglob("*") if path.is_file()}

    def test_an_occupied_checkout_is_refused_with_every_file_untouched(self):
        intact = self.contents()
        for cwd in (self.checkout, self.checkout / "nested"):
            with self.subTest(cwd=cwd):
                with self.occupied(cwd):
                    with self.assertRaises(BoardError) as caught:
                        guard.require_idle_checkout(self.checkout, allocated_at=time.time())
                self.assertEqual(caught.exception.code, "NOT_READY")
                self.assertEqual(caught.exception.details["reasons"], ["checkout-in-use"])
                self.assertEqual(self.contents(), intact)
                self.assertFalse((self.checkout.parent / "checkout.removing").exists())
        guard.require_idle_checkout(self.checkout, allocated_at=time.time())
        self.assertEqual(self.contents(), intact)
        self.assertFalse((self.checkout.parent / "checkout.removing").exists())


@unittest.skipIf(os.name == "nt", "POSIX working-directory observation")
class CheckoutProcessTests(unittest.TestCase):
    def process(self, cwd, *, created=100):
        process = Mock(pid=321)
        process.cwd.side_effect = cwd if isinstance(cwd, Exception) else None
        process.cwd.return_value = cwd
        process.create_time.return_value = created
        process.name.return_value = "python"
        return process

    def observe(self, checkout, processes, *, allocated_at=100):
        with patch.object(guard, "_processes", return_value=(processes, True)), \
             patch.object(guard, "process_start", return_value=456):
            return guard.require_idle_checkout(checkout, allocated_at=allocated_at)

    def blocked(self, checkout, processes, **kwargs):
        with self.assertRaises(BoardError) as caught:
            self.observe(checkout, processes, **kwargs)
        self.assertEqual(caught.exception.code, "NOT_READY")
        return caught.exception.details["reason"]

    def test_real_empty_directory_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            guard.require_idle_checkout(Path(directory), allocated_at=time.time())

    def test_resolves_only_checkout_and_compares_complete_path_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "checkout"
            root.mkdir()
            alias = root.parent / "alias"
            alias.symlink_to(root, target_is_directory=True)
            for cwd in (root, root / "subdir"):
                with self.subTest(cwd=cwd):
                    cause = self.blocked(alias, [self.process(str(cwd))])
                    self.assertEqual(cause, {"code": "checkout.process-blockers", "params": {
                        "processes": [{"pid": 321, "startedAt": 456, "executable": "python"}], "unreadableProcesses": []}})
            self.observe(root, [self.process(str(root) + "-other")])

    def test_unopenable_cwd_outside_checkout_does_not_block_or_get_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            process = self.process("/protected/container/Data")
            # cwd() succeeds; opening/stat'ing that unrelated directory would
            # be denied. Neither directory access belongs in this observation.
            with patch.object(os, "open", side_effect=AssertionError("opened another cwd")), \
                 patch.object(os, "stat", side_effect=AssertionError("stat'ed another cwd")), \
                 patch.object(Path, "resolve", side_effect=AssertionError("resolved another cwd")):
                self.observe(root, [process])

    def test_unreadable_cwd_excludes_only_processes_older_than_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.observe(root, [self.process(psutil.AccessDenied(), created=99)], allocated_at=100)
            for created in (100, 101):
                with self.subTest(created=created):
                    cause = self.blocked(root, [self.process(psutil.AccessDenied(), created=created)],
                                         allocated_at=100)
                    self.assertEqual(cause, {"code": "checkout.process-blockers", "params": {
                        "processes": [], "unreadableProcesses": [{"pid": 321, "startedAt": 456, "executable": "python"}]}})

    def test_live_and_unreadable_processes_have_distinct_facts_without_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            processes = [self.process(str(root)), self.process(psutil.AccessDenied())]
            cause = self.blocked(root, processes, allocated_at=50)
            item = {"pid": 321, "startedAt": 456, "executable": "python"}
            self.assertEqual(cause, {"code": "checkout.process-blockers", "params": {
                "processes": [item], "unreadableProcesses": [item]}})
            for process in processes:
                process.cmdline.assert_not_called()
                process.environ.assert_not_called()

    def test_incomplete_scan_and_identity_change_do_not_confirm_idle_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(guard, "_processes", return_value=([], False)):
                with self.assertRaises(BoardError) as caught:
                    guard.require_idle_checkout(root, allocated_at=100)
                self.assertEqual(caught.exception.details["reason"]["code"], "checkout.process-scan-failed")
            with patch.object(guard, "process_start", side_effect=[456, 789]):
                with patch.object(guard, "_processes", return_value=([self.process(str(root))], True)):
                    with self.assertRaises(BoardError) as caught:
                        guard.require_idle_checkout(root, allocated_at=100)
                self.assertEqual(caught.exception.details["reason"]["code"], "checkout.process-scan-failed")

    def test_exit_during_cwd_scan_does_not_block_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(guard, "process_start", side_effect=[456, None]), \
                 patch.object(guard, "_processes", return_value=([self.process(str(root))], True)):
                guard.require_idle_checkout(root, allocated_at=100)
