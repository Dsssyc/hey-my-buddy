"""The portable lock keeps flock semantics on POSIX (ADR-015 portability work)."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import tempfile
import unittest

from hey_my_buddy import locking


class LockingTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="buddy-lock-")) / "owner.lock"
        self.fds = []

    def tearDown(self):
        for fd in self.fds:
            os.close(fd)
        self.path.unlink(missing_ok=True)
        self.path.parent.rmdir()

    def open(self) -> int:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        self.fds.append(fd)
        return fd

    def test_exclusive_owner_blocks_a_non_blocking_second_owner_until_unlock(self):
        first, second = self.open(), self.open()
        locking.lock(first, blocking=False)
        with self.assertRaises(BlockingIOError):
            locking.lock(second, blocking=False)
        locking.unlock(first)
        locking.lock(second, blocking=False)

    def test_shared_owners_coexist_and_exclude_a_writer(self):
        first, second, writer = self.open(), self.open(), self.open()
        locking.lock(first, shared=True)
        locking.lock(second, shared=True)
        with self.assertRaises(BlockingIOError):
            locking.lock(writer, blocking=False)

    def test_it_interoperates_with_a_raw_flock_holder(self):
        raw, portable = self.open(), self.open()
        fcntl.flock(raw, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(BlockingIOError):
            locking.lock(portable, blocking=False)
        fcntl.flock(raw, fcntl.LOCK_UN)
        locking.lock(portable, blocking=False)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(raw, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == "__main__":
    unittest.main()
