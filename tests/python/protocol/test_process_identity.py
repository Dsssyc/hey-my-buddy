"""Evidence rules for restarting an interrupted execution."""
from __future__ import annotations

import copy
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
import json

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as identity


RECORD = {"bootId": "linux:00000000-0000-4000-8000-000000000001", "pid": 301, "startedAt": 105,
          "processGroupId": 301, "jobName": None,
          "worker": {"bootId": "linux:00000000-0000-4000-8000-000000000001", "pid": 201, "startedAt": 85}}


class Source:
    def __init__(self):
        self.boot = RECORD["bootId"]
        self.starts = {201: 85, 301: 105}
        self.pgid = 301
        self.group = True
        self.job = [301]
        self.signals = []
        self.inspections = 0

    def boot_id(self):
        return self.boot

    def started_at(self, pid):
        return self.starts.get(pid)

    def group_id(self, pid):
        return self.pgid

    def group_alive(self, pgid):
        self.inspections += 1
        return self.group

    def job_members(self, name):
        self.inspections += 1
        return self.job

    def signal_group(self, pgid, sig):
        self.signals.append(("group", pgid, sig))
        self.group = False
        self.starts.pop(301, None)

    def stop_job(self, name):
        self.signals.append(("job", name))
        self.job = []
        self.starts.pop(301, None)


class ProcessIdentityTests(unittest.TestCase):
    def test_validates_one_unambiguous_identity(self):
        self.assertIs(identity.validate_process_identity(RECORD), RECORD)
        for update in ({"pid": True}, {"startedAt": float("nan")}, {"startedAt": 10.5},
                       {"processGroupId": None}, {"jobName": "job"}):
            with self.subTest(update=update), self.assertRaises(BoardError):
                identity.validate_process_identity({**RECORD, **update})

    def test_boot_change_proves_stopped_without_pid_observation(self):
        source = Source()
        source.boot = "linux:00000000-0000-4000-8000-000000000002"
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "boot-changed")
        self.assertEqual(identity.worker_identity_alive(RECORD, source=source), False)

    def test_group_and_root_absence_prove_stopped(self):
        source = Source()
        source.starts.pop(301)
        source.group = False
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "identity-absent")

    def test_reused_root_pid_is_not_the_old_group(self):
        source = Source()
        source.starts[301] = 220
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "unknown")
        source.group = False
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "identity-absent")
        source.starts.pop(201)
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "identity-absent")
        self.assertEqual(source.signals, [])

    def test_live_root_and_absent_worker_are_reverified_and_stopped(self):
        source = Source()
        source.starts.pop(201)
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "alive")
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "worker-stopped")
        self.assertEqual(source.signals, [("group", 301, signal.SIGTERM)])
        self.assertGreaterEqual(source.inspections, 2)

    def test_live_original_worker_is_never_signalled(self):
        source = Source()
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "unknown")
        self.assertEqual(source.signals, [])

    def test_term_grace_then_kill_and_confirm(self):
        source = Source()
        source.starts.pop(201)
        now = [0.0]
        kill_time = [None]

        def signal_group(pgid, sig):
            source.signals.append((pgid, sig, now[0]))
            if sig == signal.SIGKILL:
                kill_time[0] = now[0]

        def advance(seconds):
            now[0] += seconds
            if kill_time[0] is not None and now[0] >= kill_time[0] + 1.5:
                source.group = False
                source.starts.pop(301)

        source.signal_group = signal_group
        with mock.patch.object(identity.time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch.object(identity.time, "sleep", side_effect=advance):
            self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "worker-stopped")
        self.assertEqual([(pgid, sig) for pgid, sig, _ in source.signals],
                         [(301, signal.SIGTERM), (301, signal.SIGKILL)])
        self.assertGreaterEqual(kill_time[0], identity.COMMAND_STOP_GRACE_SECONDS)
        self.assertLessEqual(kill_time[0], identity.COMMAND_STOP_GRACE_SECONDS + 0.05 + 1e-6)
        self.assertGreaterEqual(now[0] - kill_time[0], 1.5)

    def test_kill_observation_has_its_own_two_second_deadline(self):
        source = Source()
        source.starts.pop(201)
        now = [0.0]

        def signal_group(pgid, sig):
            source.signals.append((pgid, sig, now[0]))

        source.signal_group = signal_group
        with mock.patch.object(identity.time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch.object(identity.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "unknown")
        self.assertEqual(source.signals[1][1], signal.SIGKILL)
        self.assertGreaterEqual(now[0] - source.signals[1][2], 2.0)

    def test_group_probe_reports_only_esrch_as_absent(self):
        with mock.patch.object(identity.os, "killpg", side_effect=ProcessLookupError):
            self.assertFalse(identity._group_alive(301))
        with mock.patch.object(identity.os, "killpg", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                identity._group_alive(301)
        with mock.patch.object(identity.os, "killpg", return_value=None) as probe:
            self.assertTrue(identity._group_alive(301))
            probe.assert_called_once_with(301, 0)

    def test_uninspectable_identity_and_unanchored_descendants_remain_unknown(self):
        source = Source()
        source.starts.pop(301)
        self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "unknown")
        source.starts.pop(201)
        self.assertEqual(identity.stop_orphan_process(RECORD, source=source), "unknown")
        self.assertEqual(source.signals, [])
        with mock.patch.object(source, "group_alive", side_effect=PermissionError):
            self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "unknown")
        with mock.patch.object(source, "started_at", side_effect=PermissionError):
            self.assertEqual(identity.inspect_process_identity(RECORD, source=source), "unknown")
            self.assertIsNone(identity.worker_identity_alive(RECORD, source=source))

    def test_windows_job_presence_absence_and_reverified_stop(self):
        record = {**RECORD, "processGroupId": None,
                  "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000003"}
        source = Source()
        source.starts.pop(201)
        self.assertEqual(identity.stop_orphan_process(record, source=source), "worker-stopped")
        self.assertEqual(source.signals, [("job", record["jobName"])])
        source.job = None
        self.assertEqual(identity.inspect_process_identity(record, source=source), "identity-absent")

    def test_empty_windows_job_waits_for_original_root_exit(self):
        record = {**RECORD, "processGroupId": None,
                  "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000003"}
        source = Source()
        source.job = []
        self.assertEqual(identity.inspect_process_identity(record, source=source), "unknown")
        source.starts.pop(301)
        self.assertEqual(identity.inspect_process_identity(record, source=source), "identity-absent")
        source.starts[301] = 220
        self.assertEqual(identity.inspect_process_identity(record, source=source), "identity-absent")

    def test_windows_system_source_reopens_named_job_for_query_and_stop(self):
        from hey_my_buddy.protocol import windows_process

        api = mock.Mock()
        api.open_job.return_value = 77
        api.process_ids.return_value = [301, 302]
        with mock.patch.object(windows_process, "WindowsAPI", return_value=api):
            source = identity._SystemSource()
            self.assertEqual(source.job_members("Local\\HeyMyBuddy-example"), [301, 302])
            source.stop_job("Local\\HeyMyBuddy-example")
        self.assertEqual(api.terminate.call_args.args, (77,))
        self.assertEqual(api.close.call_count, 2)

    @unittest.skipIf(os.name == "nt", "POSIX process group injection")
    def test_killed_worker_leaves_real_child_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            record_path = Path(directory) / "identity.json"
            worker_code = """
import json, subprocess, sys, time
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
handle = ProcessHandle(child, own_group=True, log_paths={})
with open(sys.argv[1], 'w', encoding='utf-8') as output:
    json.dump(handle.identity(), output)
time.sleep(60)
"""
            worker = subprocess.Popen([sys.executable, "-c", worker_code, str(record_path)])
            record = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not record_path.exists():
                    self.assertIsNone(worker.poll(), "fixture Worker exited before capture")
                    time.sleep(0.025)
                self.assertTrue(record_path.exists(), "fixture Worker did not publish identity")
                record = json.loads(record_path.read_text(encoding="utf-8"))
                self.assertEqual(identity.inspect_process_identity(record), "alive")
                worker.kill()
                worker.wait(timeout=5)
                self.assertFalse(identity.worker_identity_alive(record))
                self.assertEqual(identity.inspect_process_identity(record), "alive")
                self.assertEqual(identity.stop_orphan_process(record), "worker-stopped")
                self.assertEqual(identity.inspect_process_identity(record), "identity-absent")
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
                if record is not None and identity.inspect_process_identity(record) == "alive":
                    identity.stop_orphan_process(record)

    @unittest.skipIf(os.name == "nt", "POSIX process group injection")
    def test_killed_worker_and_child_are_observed_absent(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            record = handle.identity()
            import psutil
            wall, boot = time.time(), psutil.boot_time()
            for shift in (-86_400, 86_400):
                with self.subTest(shift=shift), mock.patch.object(time, 'time', return_value=wall + shift), \
                     mock.patch.object(psutil, 'boot_time', return_value=boot + shift):
                    self.assertEqual(identity.inspect_process_identity(record), 'alive')
                    self.assertTrue(identity.worker_identity_alive(record))
            os.killpg(handle.pgid, signal.SIGKILL)
            process.wait(timeout=5)
            self.assertEqual(identity.inspect_process_identity(record), "identity-absent")
        finally:
            if process.poll() is None:
                handle.terminate(grace_seconds=0)

    @unittest.skipIf(os.name == "nt", "POSIX process group injection")
    def test_capture_fast_exited_child_before_wait_reaps_it(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            time.sleep(0.05)
            record = handle.identity()
            self.assertEqual(record["pid"], process.pid)
            self.assertGreater(record["startedAt"], 0)
        finally:
            process.wait(timeout=5)

    @unittest.skipIf(os.name == "nt", "POSIX process group injection")
    def test_real_owned_process_stops_only_after_worker_identity_is_absent(self):
        # The test owns this child. A source wrapper simulates the creating
        # Worker disappearing while retaining real OS observations for it.
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        self.addCleanup(lambda: handle.terminate(grace_seconds=0) if process.poll() is None else None)
        record = handle.identity()
        self.assertEqual(identity.inspect_process_identity(record), "alive")

        class MissingWorker(identity._SystemSource):
            @staticmethod
            def started_at(pid):
                if pid == record["worker"]["pid"]:
                    return None
                return identity._started_at(pid)

        reaper = threading.Thread(target=process.wait, kwargs={"timeout": 5}, daemon=True)
        reaper.start()
        self.assertEqual(identity.stop_orphan_process(record, source=MissingWorker()), "worker-stopped")
        reaper.join(timeout=5)
        self.assertEqual(identity.inspect_process_identity(record), "identity-absent")
