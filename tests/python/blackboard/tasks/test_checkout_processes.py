"""Directory identity, failure and privacy at the checkout deletion boundary."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import checkout_processes as guard
from hey_my_buddy.errors import BoardError


@unittest.skipIf(os.name == "nt", "POSIX directory observation")
class CheckoutProcessTests(unittest.TestCase):
    def test_real_empty_directory_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            guard.require_idle_checkout(Path(directory))

    def test_directory_identity_accepts_aliases_and_rejects_a_similar_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            root.mkdir()
            (root / "subdir").mkdir()
            sibling = Path(directory) / "checkout-other"
            sibling.mkdir()
            alias = Path(directory) / "alias"
            alias.symlink_to(root, target_is_directory=True)
            info = root.stat()
            identity = (info.st_dev, info.st_ino)
            self.assertTrue(guard._below_directory(str(alias / "subdir"), identity))
            self.assertFalse(guard._below_directory(str(sibling), identity))

    def test_incomplete_process_list_and_denied_cwd_block_deletion(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            for processes, complete in (([], False), ([psutil.Process()], True)):
                with self.subTest(complete=complete), patch.object(guard, "_processes", return_value=(processes, complete)), \
                     patch.object(psutil.Process, "cwd", side_effect=psutil.AccessDenied):
                    with self.assertRaises(BoardError) as caught:
                        guard.require_idle_checkout(Path(directory))
                    self.assertEqual(caught.exception.details["reason"],
                                     {"code": "checkout.process-scan-failed", "params": {}})
