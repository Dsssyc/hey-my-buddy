"""Session admission, exact signaling and late reaping, including escape boundary."""
from dataclasses import replace
import os
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from hey_my_buddy.protocol.process_members import ProcessMember, SessionProcesses
from hey_my_buddy.protocol import process_members
from hey_my_buddy.protocol.process_start import process_start
from hey_my_buddy.protocol import process_identity
from protocol.session_case import descendant_case


class AdmissionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process observation")
    def test_pid_enumeration_and_session_query_failures_are_not_empty_scans(self):
        with patch.object(process_members.psutil, "pids", side_effect=OSError("enumeration failed")):
            with self.assertRaises(OSError):
                process_members.process_snapshot()
        with patch.object(process_members.psutil, "pids", return_value=[os.getpid()]), \
             patch.object(process_members.os, "getsid", side_effect=PermissionError("session denied")):
            with self.assertRaises(OSError):
                process_members.process_snapshot()

    def observer(self, rows, starts):
        signals = []
        observer = SessionProcesses(101, 10, session=101, snapshot=lambda: dict(rows),
                                    creation=lambda pid: starts.get(pid),
                                    alive=lambda pid: starts.get(pid),
                                    send=lambda pid, sig: signals.append((pid, sig)))
        return observer, signals

    def test_root_reaped_before_admission_never_authorizes_session_members(self):
        rows = {102: ProcessMember(102, 11, 1, 101, True)}
        observer, signals = self.observer(rows, {102: 11})
        self.assertEqual(observer.observe(), "unknown")
        observer.signal(signal.SIGKILL)
        self.assertEqual(signals, [])
        rows.clear()
        self.assertEqual(observer.observe(), "identity-absent")

    def test_admitted_identity_survives_root_reaping_and_reparenting(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 102, True)}
        starts = {101: 10, 102: 11}
        observer, signals = self.observer(rows, starts)
        self.assertEqual(observer.observe(), "alive")
        del rows[101], starts[101]
        rows[102] = replace(rows[102], parent=1)
        observer.signal(signal.SIGKILL)
        self.assertEqual(signals, [(102, signal.SIGKILL)])
        starts[102] = 12
        rows[102] = replace(rows[102], started_at=12)
        observer.signal(signal.SIGKILL)
        self.assertEqual(len(signals), 1)

    def test_root_changes_during_scan_does_not_admit_members(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 101, True)}
        observer, _ = self.observer(rows, {101: 10, 102: 11})
        with patch.object(observer, "creation", side_effect=[10, None]):
            with self.assertRaises(OSError):
                observer.observe()
        self.assertEqual(observer.known, {})

    def test_identity_is_rechecked_immediately_before_signal(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True)}
        observer, signals = self.observer(rows, {101: 10})
        with patch.object(observer, "creation", side_effect=[10, 10, 20]):
            observer.signal(signal.SIGKILL)
        self.assertEqual(signals, [])


@unittest.skipIf(os.name == "nt", "POSIX descendant/session boundary")
class RealSessionTests(unittest.TestCase):
    def test_five_descendant_shapes_are_stopped_and_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("same", "group", "session", "double-fork", "nohup"):
                with self.subTest(mode=mode), descendant_case(self, directory, mode) as (handle, member):
                    self.assertEqual(handle.terminate(grace_seconds=0.03), (True, True))
                    self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))
                    self.assertIsNone(process_start(member["pid"], include_exited=False))

    def test_setsid_double_fork_is_outside_the_stop_boundary(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "daemon") as (handle, member):
            self.assertTrue(handle.terminate(grace_seconds=0.03)[0])
            self.assertEqual(process_start(member["pid"], include_exited=False), member["startedAt"])

    def test_scan_failure_keeps_root_waitable_and_stop_unconfirmed(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "same") as (handle, member):
            handle.group_alive()  # Admit the leaf while the root pins its PID.
            os.kill(handle.pid, signal.SIGKILL)
            with patch.object(handle._members, "snapshot", side_effect=OSError("injected scan failure")):
                self.assertFalse(handle.terminate(grace_seconds=0, settle_seconds=0.05)[0])
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
                self.assertIsNone(handle.process.returncode)
                self.assertEqual(process_start(handle.pid, include_exited=True), handle.process_started_at)
            self.assertTrue(handle.terminate(grace_seconds=0)[0])

    def test_recovery_force_stops_another_group_after_root_was_killed(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "group") as (handle, member):
            record = handle.identity()
            os.kill(handle.pid, signal.SIGKILL)
            # Root remains unreaped, so recovery can still admit its session.
            with patch.object(process_identity, "COMMAND_STOP_GRACE_SECONDS", 0.03):
                self.assertEqual(process_identity.stop_orphan_process(record), "orphan-stopped")
            self.assertTrue(handle.shutdown_confirmed())
            self.assertIsNone(process_start(member["pid"], include_exited=False))
