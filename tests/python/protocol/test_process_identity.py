"""Complete process identity and orphan-stop evidence."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as identity


RECORD = {"bootId": "linux:00000000-0000-4000-8000-000000000001", "pid": 301,
          "startedAt": 105, "processGroupId": 301, "jobName": None}


class Source:
    def __init__(self):
        self.boot = RECORD["bootId"]
        self.start = 105
        self.group = True
        self.signals = []

    def boot_id(self):
        return self.boot

    def started_at(self, pid):
        return self.start

    def group_id(self, pid):
        return 301

    def group_alive(self, pgid):
        return self.group

    def signal_group(self, pgid, sig):
        self.signals.append((pgid, sig))
        self.start = None
        self.group = False


class ProcessIdentityTests(unittest.TestCase):
    def test_identity_has_one_current_form_without_worker(self):
        self.assertIs(identity.validate_process_identity(RECORD), RECORD)
        for value in ({**RECORD, "worker": {}}, {**RECORD, "pid": True},
                      {**RECORD, "startedAt": 10.5}, {**RECORD, "processGroupId": None}):
            with self.subTest(value=value), self.assertRaises(BoardError):
                identity.validate_process_identity(value)

    def test_boot_change_and_root_absence_prove_stopped(self):
        source = Source()
        source.boot = "linux:00000000-0000-4000-8000-000000000002"
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "boot-changed")
        source.boot = RECORD["bootId"]
        source.start = None
        source.group = False
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "identity-absent")

    def test_reused_pid_with_surviving_group_is_unknown_and_never_signalled(self):
        source = Source()
        source.start = 220
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "unknown")
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "unknown")
        self.assertEqual(source.signals, [])

    def test_caller_proven_orphan_is_reverified_and_stopped(self):
        source = Source()
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "alive")
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "orphan-stopped")
        self.assertEqual(source.signals, [(301, signal.SIGTERM)])

    def test_unknown_observation_does_not_authorize_signal(self):
        source = Source()
        with mock.patch.object(source, "group_alive", side_effect=PermissionError):
            self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "unknown")
        self.assertEqual(source.signals, [])

    @unittest.skipIf(os.name == "nt", "POSIX process group test")
    def test_real_owned_process_identity_and_orphan_stop(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            record = handle.identity()
            self.assertEqual(set(record), set(RECORD))
            self.assertEqual(identity.inspect_process_identity(record), "alive")
            reaper = threading.Thread(target=process.wait, kwargs={"timeout": 8}, daemon=True)
            reaper.start()
            self.assertEqual(identity.stop_orphan_process(record), "orphan-stopped")
            reaper.join(timeout=8)
            self.assertEqual(identity.inspect_process_identity(record), "identity-absent")
        finally:
            if process.poll() is None:
                handle.terminate(grace_seconds=0)
