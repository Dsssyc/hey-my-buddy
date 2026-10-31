"""Session admission, exact signaling and late reaping, including escape boundary."""
from dataclasses import replace
import os
import json
import subprocess
import sys
import threading
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
        observer.signal(signal.SIGTERM)
        self.assertEqual(signals, [])
        rows.clear()
        self.assertEqual(observer.observe(), "empty")
        self.assertEqual(observer.observe(), "identity-absent")

    def test_new_session_member_after_root_reaping_is_unknown_and_unsignalled(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 202, True)}
        starts = {101: 10, 102: 11}
        observer, signals = self.observer(rows, starts)
        self.assertEqual(observer.observe(), "alive")
        del rows[101], starts[101]
        rows[102] = replace(rows[102], parent=1)
        rows[103] = ProcessMember(103, 12, 1, 101, True)
        starts[103] = 12
        self.assertEqual(observer.observe(), "unknown")
        observer.signal(signal.SIGTERM)
        self.assertEqual(signals, [(102, signal.SIGTERM)])
        del rows[103], starts[103]
        self.assertEqual(observer.observe(), "alive")

    def test_admitted_identity_survives_root_reaping_and_reparenting(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 102, True)}
        starts = {101: 10, 102: 11}
        observer, signals = self.observer(rows, starts)
        self.assertEqual(observer.observe(), "alive")
        del rows[101], starts[101]
        rows[102] = replace(rows[102], parent=1)
        observer.signal(signal.SIGTERM)
        self.assertEqual(signals, [(102, signal.SIGTERM)])
        starts[102] = 12
        rows[102] = replace(rows[102], started_at=12)
        observer.signal(signal.SIGTERM)
        self.assertEqual(len(signals), 1)

    def test_root_changes_during_scan_does_not_admit_members(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 101, True)}
        observer, _ = self.observer(rows, {101: 10, 102: 11})
        with patch.object(observer, "creation", side_effect=[10, None]):
            self.assertEqual(observer.observe(), "unknown")
        self.assertEqual(observer.known, {101: 10})

    def test_identity_is_rechecked_immediately_before_signal(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True)}
        observer, signals = self.observer(rows, {101: 10})
        observer.observe()
        with patch.object(observer, "creation", return_value=20):
            observer.signal(signal.SIGTERM)
        self.assertEqual(signals, [])

    def test_pinned_zombie_root_still_proves_a_child_edge_without_its_table_row(self):
        rows = {102: ProcessMember(102, 11, 101, 202, True)}
        observer, _ = self.observer(rows, {101: 10, 102: 11})
        observer.alive = lambda pid: None if pid == 101 else 11
        self.assertEqual(observer.observe(), "alive")
        self.assertEqual(observer.live, {102: 11})

    def test_two_empty_scans_are_required_and_a_late_fork_resets_them(self):
        rows = {101: ProcessMember(101, 10, 1, 101, False)}
        starts = {101: 10}
        observer, _ = self.observer(rows, starts)
        observer.alive = lambda pid: None  # Unreaped root pins its PID.
        self.assertEqual(observer.observe(), "empty")
        # First PID list missed a child born while its parent was being read.
        rows[102] = ProcessMember(102, 11, 1, 101, True)
        starts[102] = 11
        self.assertEqual(observer.observe(), "alive")
        rows.pop(102)
        starts.pop(102)
        self.assertEqual(observer.observe(), "empty")
        with patch.object(observer, "snapshot", side_effect=OSError):
            with self.assertRaises(OSError):
                observer.observe()
        self.assertEqual(observer.observe(), "empty")
        self.assertEqual(observer.observe(), "identity-absent")

    @unittest.skipIf(os.name == "nt", "POSIX process observation")
    def test_process_exiting_mid_scan_is_disappearance_not_identity_change(self):
        process = Mock()
        process.uids.return_value.effective = os.geteuid()
        process.ppid.return_value = 1
        process.status.return_value = "running"
        with patch.object(process_members.psutil, "pids", return_value=[101]), \
             patch.object(process_members.psutil, "Process", return_value=process), \
             patch.object(process_members.os, "getsid", return_value=101), \
             patch.object(process_members, "process_start", side_effect=[10, None]):
            self.assertEqual(process_members.process_snapshot(), {})

    def test_failed_scan_still_allows_signalling_verified_known_members(self):
        rows = {101: ProcessMember(101, 10, 1, 101, True),
                102: ProcessMember(102, 11, 101, 101, True)}
        observer, signals = self.observer(rows, {101: 10, 102: 11})
        observer.observe()
        with patch.object(observer, "snapshot", side_effect=OSError):
            with self.assertRaises(OSError):
                observer.observe()
            observer.signal(signal.SIGTERM)
        self.assertEqual(signals, [(102, signal.SIGTERM), (101, signal.SIGTERM)])


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
            handle.session_alive()  # Admit the leaf while the root pins its PID.
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


    def test_member_born_after_term_is_stopped_while_root_pid_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "late") as (handle, member):
            try:
                self.assertTrue(handle.terminate(grace_seconds=0.05)[0])
                late = json.loads((Path(directory) / "late.late").read_text())
                self.assertIsNone(process_start(late["pid"], include_exited=False))
            finally:
                path = Path(directory) / "late.late"
                if path.exists():
                    late = json.loads(path.read_text())
                    if process_start(late["pid"], include_exited=False) == late["startedAt"]:
                        os.kill(late["pid"], signal.SIGKILL)

    def test_budget_exhaustion_does_not_confirm_surviving_members(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "group") as (handle, member):
            with patch.object(handle._members, "send", return_value=None):
                self.assertEqual(handle.terminate(grace_seconds=0, settle_seconds=0.03), (False, True))
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
            self.assertTrue(handle.terminate(grace_seconds=0)[0])

    def test_missing_start_identity_still_signals_owned_root_without_confirming(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "same") as (handle, member):
            observer = handle._members
            handle._members = None
            try:
                self.assertFalse(handle.terminate(grace_seconds=0, settle_seconds=0.05)[0])
                self.assertIsNotNone(handle.poll())
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
            finally:
                # Popen's safe send can reap the root, so preserve previously
                # admitted exact identities for cleanup without readmitting.
                handle._members = observer
                if process_start(member["pid"], include_exited=False) == member["startedAt"]:
                    os.kill(member["pid"], signal.SIGKILL)

    def test_observation_and_signalling_share_the_owned_stop_lock(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "same") as (handle, member):
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            original = handle._members.snapshot
            failures = []
            def scan():
                entered.set()
                if not release.wait(5):
                    raise OSError("test lock release timed out")
                return original()
            def stop():
                try:
                    handle.terminate(grace_seconds=0)
                except BaseException as error:
                    failures.append(error)
                finally:
                    finished.set()
            with patch.object(handle._members, "snapshot", side_effect=scan):
                observe = threading.Thread(target=handle.shutdown_confirmed, kwargs={"settle_seconds": 0})
                observe.start()
                stopper = threading.Thread(target=stop)
                try:
                    self.assertTrue(entered.wait(5))
                    stopper.start()
                    self.assertFalse(finished.wait(0.05))
                finally:
                    release.set()
                    observe.join(8)
                    if stopper.ident is not None:
                        stopper.join(8)
                self.assertFalse(observe.is_alive())
                self.assertFalse(stopper.is_alive())
                self.assertEqual(failures, [])

    def test_init_reaped_root_keeps_known_members_stoppable_and_new_members_unknown(self):
        import psutil
        from hey_my_buddy.protocol.process_identity import boot_id
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "init.json"
            fixture = Path(__file__).parent / "fixtures/session_descendants.py"
            command = [sys.executable, str(fixture), "late", str(ready)]
            intermediate = subprocess.Popen([sys.executable, "-c",
                "import subprocess,sys; subprocess.Popen(sys.argv[1:], start_new_session=True, "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)", *command])
            intermediate.wait(timeout=5)
            records = []
            try:
                deadline = time.monotonic() + 5
                while not ready.exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                root = json.loads(ready.with_suffix(".root").read_text())
                member = json.loads(ready.read_text())
                records.extend([root, member])
                self.assertEqual(psutil.Process(root["pid"]).ppid(), 1)
                record = dict(root, bootId=boot_id(), jobName=None)
                source = process_identity._SystemSource()
                def send(pid, sig):
                    os.kill(pid, sig)
                    if pid == root["pid"] and sig == signal.SIGTERM:
                        deadline = time.monotonic() + 5
                        while (process_start(root["pid"], include_exited=True) is not None
                               or not ready.with_suffix(".late").exists()):
                            self.assertLess(time.monotonic(), deadline)
                            time.sleep(0.005)
                source.signal_process = send
                with patch.object(process_identity, "COMMAND_STOP_GRACE_SECONDS", 0.08):
                    self.assertEqual(process_identity.stop_orphan_process(record, source=source, settle_seconds=0.1), "unknown")
                late = json.loads(ready.with_suffix(".late").read_text())
                records.append(late)
                self.assertIsNone(process_start(root["pid"], include_exited=True))
                self.assertIsNone(process_start(member["pid"], include_exited=False))
                self.assertEqual(process_start(late["pid"], include_exited=False), late["startedAt"])
                self.assertEqual(process_identity.inspect_process_identity(record), "unknown")
                # A fresh recovery observer has no admission proof for that leaf.
                process_identity.kill_verified_process_session(record)
                self.assertEqual(process_start(late["pid"], include_exited=False), late["startedAt"])
            finally:
                for path in (ready, ready.with_suffix(".root"), ready.with_suffix(".late")):
                    if path.exists():
                        row = json.loads(path.read_text())
                        if row not in records:
                            records.append(row)
                for row in records:
                    if process_start(row["pid"], include_exited=False) == row["startedAt"]:
                        os.kill(row["pid"], signal.SIGKILL)
                deadline = time.monotonic() + 5
                while any(process_start(row["pid"], include_exited=True) == row["startedAt"] for row in records):
                    self.assertLess(time.monotonic(), deadline, "fixture identity survived cleanup")
                    time.sleep(0.01)

    def test_command_collection_keeps_unreaped_root_exit_and_signal_consistent(self):
        from hey_my_buddy.buddy.runtime.command import CommandAdapter
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "group") as (handle, member):
            stdout, stderr = Path(directory) / "stdout", Path(directory) / "stderr"
            stdout.touch()
            stderr.touch()
            handle.log_paths = {"stdout": str(stdout), "stderr": str(stderr)}
            handle.session_observation()
            os.kill(handle.pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while handle.poll() is None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertIsNone(handle.process.returncode)
            with patch.object(handle, "shutdown_confirmed", return_value=False):
                outcome = CommandAdapter().collect(handle, SimpleNamespace(spec={"argv": []}, task_file=lambda: Path(directory) / "task.json"))
            self.assertEqual(outcome.result["exitCode"], -signal.SIGKILL)
            self.assertEqual(outcome.result["signal"], "SIGKILL")
            self.assertEqual(outcome.exit_code, -signal.SIGKILL)
            self.assertEqual(outcome.signal, "SIGKILL")

    def test_first_scan_failure_still_stops_the_already_owned_root(self):
        with tempfile.TemporaryDirectory() as directory, descendant_case(self, directory, "group") as (handle, member):
            with patch.object(handle._members, "snapshot", side_effect=OSError("first scan failed")):
                self.assertFalse(handle.terminate(grace_seconds=0, settle_seconds=0.05)[0])
                self.assertIsNotNone(handle.poll())
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
                self.assertIsNone(handle.process.returncode)  # Keep the root's number pinned.
            self.assertTrue(handle.terminate(grace_seconds=0)[0])
