"""The portable lock keeps flock semantics on POSIX (ADR-015 portability work)."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest

from hey_my_buddy import locking


class LockingTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="hey-my-buddy-lock-")) / "owner.lock"
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

    @unittest.skipUnless(os.name == "nt", "Windows msvcrt has a finite blocking-lock retry window")
    def test_blocking_owner_waits_beyond_the_crt_retry_window(self):
        first, second = self.open(), self.open()
        locking.lock(first)
        entered, finished = threading.Event(), threading.Event()
        errors = []

        def acquire():
            entered.set()
            try:
                locking.lock(second)
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        contender = threading.Thread(target=acquire)
        contender.start()
        try:
            self.assertTrue(entered.wait(5), "the contender never attempted its lock")
            # LK_LOCK retries once a second, ten times, then raises EDEADLK.
            # Hold across that documented finite window, not a machine-speed guess.
            returned_while_held = finished.wait(11)
        finally:
            locking.unlock(first)
            contender.join(30)
        self.assertFalse(contender.is_alive(), "the contender did not acquire the released lock")
        self.assertFalse(returned_while_held, errors)
        self.assertEqual(errors, [])
        self.assertTrue(finished.is_set())

    @unittest.skipUnless(os.name == "posix", "raw fcntl.flock interoperability requires POSIX")
    def test_it_interoperates_with_a_raw_flock_holder(self):
        import fcntl

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
