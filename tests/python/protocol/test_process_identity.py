"""Complete process identity and orphan-stop evidence."""
from __future__ import annotations

from contextlib import nullcontext
import os
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from process_observation import wait_for_unreaped_child

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as identity


RECORD = {"bootId": "linux:00000000-0000-4000-8000-000000000001", "pid": 301,
          "startedAt": 105, "processGroupId": 301, "jobName": None}
WINDOWS_RECORD = {**RECORD, "bootId": "windows:00000000-0000-4000-8000-000000000001",
                  "processGroupId": None,
                  "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000003"}


class Source:
    def __init__(self):
        self.boot = RECORD["bootId"]
        self.start = 105
        self.group = True
        self.job = [301]
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

    def open_job(self, name):
        def terminate():
            self.signals.append(("job", name))
            self.job = []
            self.start = None

        return nullcontext(None if self.job is None else SimpleNamespace(
            members=lambda: self.job, active=lambda: len(self.job), terminate=terminate))


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

    def test_hard_stop_reverifies_identity_including_reuse_and_observation_failure(self):
        for failure in ("reused", "group-unreadable", "boot-changed", "absent"):
            source = Source()
            if failure == "reused":
                source.start += 1
            elif failure == "group-unreadable":
                source.group_alive = mock.Mock(side_effect=PermissionError)
            elif failure == "boot-changed":
                source.boot = "linux:00000000-0000-4000-8000-000000000002"
            else:
                source.start, source.group = None, False
            with self.subTest(failure=failure):
                identity.kill_verified_process_group(RECORD, source=source)
                self.assertEqual(source.signals, [])
        source = Source()
        identity.kill_verified_process_group(RECORD, source=source)
        self.assertEqual(source.signals, [(301, signal.SIGKILL)])
        source = Source()
        source.boot = WINDOWS_RECORD["bootId"]
        identity.kill_verified_process_group(WINDOWS_RECORD, source=source)
        self.assertEqual(source.signals, [("job", WINDOWS_RECORD["jobName"])])

    def test_windows_job_presence_absence_and_reverified_stop(self):
        source = Source()
        source.boot = WINDOWS_RECORD["bootId"]
        self.assertEqual(identity.stop_orphan_process(WINDOWS_RECORD, source=source), "orphan-stopped")
        self.assertEqual(source.signals, [("job", WINDOWS_RECORD["jobName"])])
        source.job = None
        self.assertEqual(identity.inspect_process_identity(WINDOWS_RECORD, source=source), "identity-absent")

    def test_empty_windows_job_waits_for_original_root_exit(self):
        source = Source()
        source.boot = WINDOWS_RECORD["bootId"]
        source.job = []
        self.assertEqual(identity.inspect_process_identity(WINDOWS_RECORD, source=source), "unknown")
        source.start = None
        self.assertEqual(identity.inspect_process_identity(WINDOWS_RECORD, source=source), "identity-absent")
        source.start = 220
        self.assertEqual(identity.inspect_process_identity(WINDOWS_RECORD, source=source), "identity-absent")

    def test_windows_stop_keeps_one_handle_for_verification_termination_and_empty_observation(self):
        from hey_my_buddy.protocol import windows_process

        for hard_stop in (False, True):
            with self.subTest(hard_stop=hard_stop):
                api = mock.Mock()
                api.open_job.side_effect = [77, 99]  # Reopening could select a different named object.
                api.process_ids.return_value = [301, 302]
                api.active.side_effect = [1, 0]
                with mock.patch.object(windows_process, "WindowsAPI", return_value=api), \
                     mock.patch.object(identity._SystemSource, "boot_id", return_value=WINDOWS_RECORD["bootId"]), \
                     mock.patch.object(identity._SystemSource, "started_at", side_effect=[105, None]):
                    if hard_stop:
                        identity.kill_verified_process_group(WINDOWS_RECORD)
                    else:
                        self.assertEqual(identity.stop_orphan_process(WINDOWS_RECORD), "orphan-stopped")
                self.assertEqual(api.mock_calls, [mock.call.open_job(WINDOWS_RECORD["jobName"]),
                    mock.call.process_ids(77), mock.call.terminate(77), mock.call.active(77),
                    mock.call.active(77), mock.call.close(77)])

    def test_windows_unreadable_mismatched_or_absent_job_never_authorizes_termination(self):
        from hey_my_buddy.protocol import windows_process

        for case in ("missing", "empty", "reused-root", "members-denied", "open-denied", "boot-changed"):
            with self.subTest(case=case):
                api = mock.Mock()
                api.open_job.return_value = None if case == "missing" else 77
                api.process_ids.return_value = [] if case == "empty" else [301, 302]
                if case == "members-denied":
                    api.process_ids.side_effect = PermissionError("members denied")
                if case == "open-denied":
                    api.open_job.side_effect = PermissionError("open denied")
                boot = RECORD["bootId"] if case == "boot-changed" else WINDOWS_RECORD["bootId"]
                with mock.patch.object(windows_process, "WindowsAPI", return_value=api), \
                     mock.patch.object(identity._SystemSource, "boot_id", return_value=boot), \
                     mock.patch.object(identity._SystemSource, "started_at", return_value=106 if case == "reused-root" else 105):
                    self.assertEqual(identity.stop_orphan_process(WINDOWS_RECORD, settle_seconds=0),
                                     "boot-changed" if case == "boot-changed" else "unknown")
                api.terminate.assert_not_called()
                if case in ("missing", "open-denied", "boot-changed"):
                    api.close.assert_not_called()
                else:
                    api.close.assert_called_once_with(77)

    def test_windows_root_exit_still_requires_job_empty_and_observable(self):
        from hey_my_buddy.protocol import windows_process

        for case in ("descendants", "active-denied", "terminate-denied", "root-alive", "stopped"):
            with self.subTest(case=case):
                api = mock.Mock()
                api.open_job.return_value = 77
                api.process_ids.return_value = [302]  # Original root has already exited.
                api.active.return_value = 1 if case == "descendants" else 0
                if case == "active-denied":
                    api.active.side_effect = PermissionError("active denied")
                if case == "terminate-denied":
                    api.terminate.side_effect = PermissionError("terminate denied")
                with mock.patch.object(windows_process, "WindowsAPI", return_value=api), \
                     mock.patch.object(identity._SystemSource, "boot_id", return_value=WINDOWS_RECORD["bootId"]), \
                     mock.patch.object(identity._SystemSource, "started_at", return_value=105 if case == "root-alive" else None):
                    self.assertEqual(identity.stop_orphan_process(WINDOWS_RECORD, settle_seconds=0),
                                     "orphan-stopped" if case == "stopped" else "unknown")
                api.open_job.assert_called_once_with(WINDOWS_RECORD["jobName"])
                api.terminate.assert_called_once_with(77)
                api.close.assert_called_once_with(77)

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

    @unittest.skipUnless(sys.platform in {"darwin", "linux"}, "POSIX unreaped child identity")
    def test_identity_of_child_that_exited_before_handle_construction(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        try:
            wait_for_unreaped_child(process.pid)
            handle = ProcessHandle(process, own_group=True, log_paths={})
            record = identity.capture_process_identity(handle)
            self.assertIs(identity.validate_process_identity(record), record)
            self.assertEqual(record["bootId"], identity.boot_id())
            self.assertEqual(record["pid"], process.pid)
            self.assertIsInstance(record["startedAt"], int)
            self.assertGreater(record["startedAt"], 0)
            self.assertEqual(record["processGroupId"], process.pid)
            self.assertIsNone(record["jobName"])
            self.assertEqual(identity.process_start(process.pid, include_exited=True), record["startedAt"])
            self.assertIsNone(identity.process_start(process.pid, include_exited=False))
        finally:
            process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        for include_exited in (False, True):
            with self.subTest(include_exited=include_exited):
                self.assertIsNone(identity.process_start(process.pid, include_exited=include_exited))
