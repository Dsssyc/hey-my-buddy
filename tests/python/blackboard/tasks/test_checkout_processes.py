"""Canonical cwd paths, observation failures and privacy before deletion."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from hey_my_buddy.blackboard.tasks import checkout_processes as guard
from hey_my_buddy.errors import BoardError


@unittest.skipIf(os.name == "nt", "POSIX working-directory observation")
class CheckoutProcessTests(unittest.TestCase):
    def process(self, cwd, *, created=100):
        process = Mock(pid=321)
        process.cwd.side_effect = cwd if isinstance(cwd, Exception) else None
        process.cwd.return_value = cwd
        process.create_time.return_value = created
        process.name.return_value = "python"
        return process

    def observe(self, checkout, processes, **kwargs):
        with patch.object(guard, "_processes", return_value=(processes, True)), \
             patch.object(guard, "process_start", return_value=456):
            return guard.require_idle_checkout(checkout, **kwargs)

    def blocked(self, checkout, processes, **kwargs):
        with self.assertRaises(BoardError) as caught:
            self.observe(checkout, processes, **kwargs)
        self.assertEqual(caught.exception.code, "NOT_READY")
        return caught.exception.details["reason"]

    def test_real_empty_directory_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            guard.require_idle_checkout(Path(directory), first_execution_at=time.time())

    def test_resolves_only_checkout_and_compares_complete_path_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "checkout"
            root.mkdir()
            alias = root.parent / "alias"
            alias.symlink_to(root, target_is_directory=True)
            for cwd in (root, root / "subdir"):
                with self.subTest(cwd=cwd):
                    cause = self.blocked(alias, [self.process(str(cwd))])
                    self.assertEqual(cause, {"code": "checkout.live-processes", "params": {
                        "processes": [{"pid": 321, "startedAt": 456, "executable": "python"}]}})
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

    def test_unreadable_cwd_excludes_only_processes_older_than_first_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.observe(root, [self.process(psutil.AccessDenied(), created=99)], first_execution_at=100)
            for first_execution_at in (None, 100):
                with self.subTest(first_execution_at=first_execution_at):
                    cause = self.blocked(root, [self.process(psutil.AccessDenied())],
                                         first_execution_at=first_execution_at)
                    self.assertEqual(cause, {"code": "checkout.unreadable-processes", "params": {
                        "processes": [{"pid": 321, "startedAt": 456, "executable": "python"}]}})

    def test_live_and_unreadable_processes_have_distinct_facts_without_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            processes = [self.process(str(root)), self.process(psutil.AccessDenied())]
            cause = self.blocked(root, processes, first_execution_at=50)
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
                    guard.require_idle_checkout(root)
                self.assertEqual(caught.exception.details["reason"]["code"], "checkout.process-scan-failed")
            with patch.object(guard, "process_start", side_effect=[456, 789]):
                with patch.object(guard, "_processes", return_value=([self.process(str(root))], True)):
                    with self.assertRaises(BoardError) as caught:
                        guard.require_idle_checkout(root)
                self.assertEqual(caught.exception.details["reason"]["code"], "checkout.process-scan-failed")
