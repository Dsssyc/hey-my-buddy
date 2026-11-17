"""Real POSIX session cleanup and final pipe evidence in native discovery."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

import tempfile
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.protocol.process_handle import ProcessHandle
from hey_my_buddy.protocol.process_start import process_start


_FIXTURE = '''
import json, os, signal, sys, time
from pathlib import Path
from hey_my_buddy.protocol.process_start import process_start
root, mode, exit_code = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
child = os.fork()
if child == 0:
    os.setpgid(0, 0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.close(0)
    os.close(2)
    if mode != 'pipe':
        os.close(1)
    pending = root / 'pending.json'
    pending.write_text(json.dumps({'pid': os.getpid(), 'startedAt': process_start(os.getpid(), include_exited=True)}), encoding='utf-8')
    pending.replace(root / 'ready.json')
    while True:
        time.sleep(1)
while not (root / 'ready.json').exists():
    time.sleep(0.002)
if mode == 'pipe':
    while True:
        time.sleep(1)
print('1.2.3', flush=True)
os._exit(exit_code)
'''


@unittest.skipIf(os.name == "nt", "POSIX sessions; Windows has Job/pipe tests")
class DiscoveryStopTimingTests(unittest.TestCase):
    @contextmanager
    def fixture(self, mode="group", *, exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            script = root / "probe.py"
            script.write_text(_FIXTURE, encoding="utf-8", newline="\n")
            handles = []
            initialize = ProcessHandle.__init__

            def capture(handle, *args, **kwargs):
                initialize(handle, *args, **kwargs)
                handles.append(handle)

            try:
                with patch.object(ProcessHandle, "__init__", capture):
                    yield root, [sys.executable, str(script), str(root), mode, str(exit_code)], handles
            finally:
                for handle in handles:
                    self.assertTrue(handle.terminate(grace_seconds=0)[0])
                    self.assertIsNotNone(handle.process.returncode)
                ready = root / "ready.json"
                if ready.exists():
                    member = json.loads(ready.read_text(encoding="utf-8"))
                    if process_start(member["pid"], include_exited=False) == member["startedAt"]:
                        os.kill(member["pid"], signal.SIGKILL)
                    deadline = time.monotonic() + 5
                    while process_start(member["pid"], include_exited=True) == member["startedAt"]:
                        self.assertLess(time.monotonic(), deadline, "fixture identity survived cleanup")
                        time.sleep(0.01)

    def environment(self, root):
        return {"PATH": "/usr/bin:/bin", "HOME": str(root), "PYTHONPATH": os.environ["PYTHONPATH"]}

    def test_short_lived_background_child_preserves_success(self):
        with self.fixture() as (root, _, handles):
            self.assertEqual(discovery.probe(
                ["/bin/sh", "-c", "sleep 0.2 >/dev/null 2>&1 & echo 1.2.3"], self.environment(root)),
                (None, b"1.2.3\n"))
            self.assertTrue(handles[0].shutdown_confirmed(settle_seconds=0))

    def test_tool_in_another_group_is_stopped_before_success(self):
        with self.fixture() as (root, command, handles):
            self.assertEqual(discovery.probe(command, self.environment(root)), (None, b"1.2.3\n"))
            self.assertTrue(handles[0].shutdown_confirmed(settle_seconds=0))

    def after_pipe_drain(self, *, read_error=None, dsh=False):
        with self.fixture("pipe") as (root, command, handles):
            selector = discovery.selectors.DefaultSelector()
            select = selector.select
            original_kill = os.kill
            original_stop = ProcessHandle.terminate
            blocked = [False]
            stops = []
            original_snapshot = None

            def snapshot():
                if blocked[0]:
                    raise OSError("session scan unavailable until pipe drain")
                return original_snapshot()

            def kill(pid, sig):
                original_kill(pid, sig)
                if sig == signal.SIGKILL:
                    blocked[0] = True

            def stop(handle, **kwargs):
                nonlocal original_snapshot
                original_snapshot = handle._members.snapshot
                handle._members.snapshot = snapshot
                try:
                    result = original_stop(handle, **kwargs)
                finally:
                    handle._members.snapshot = original_snapshot
                stops.append(result[0])
                # The read/drain and final observation must clear this fault.
                handle._members.snapshot = snapshot
                return result

            first = [True]

            def read(timeout):
                if first[0]:
                    first[0] = False
                    deadline = time.monotonic() + 5
                    while not (root / "ready.json").exists():
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.005)
                    if read_error is not None:
                        raise read_error
                    # Advancing the probe clock makes the next read a timeout.
                    clock_offset[0] = 10.0
                    return []
                events = select(timeout)
                if events:
                    blocked[0] = False
                return events

            clock_offset = [0.0]
            from types import SimpleNamespace
            clock = SimpleNamespace(monotonic=lambda: time.monotonic() + clock_offset[0], sleep=time.sleep)
            def probe():
                if dsh:
                    return discovery._probe_dsh_version(command, self.environment(root), time.monotonic() + 5,
                                                        state_dir=Path(os.environ["HMB_STATE_DIR"]))
                return discovery.probe(command, self.environment(root), deadline=time.monotonic() + 5)
            try:
                with patch.object(discovery.selectors, "DefaultSelector", return_value=selector), \
                     patch.object(selector, "select", side_effect=read), \
                     patch.object(ProcessHandle, "terminate", stop), \
                     patch.object(os, "kill", side_effect=kill), \
                     patch.object(discovery, "time", clock):
                    if read_error is None or isinstance(read_error, OSError):
                        result = probe()
                        self.assertEqual(result, ("pipe-failed" if read_error else "scan-timeout", b""))
                    else:
                        with self.assertRaises(type(read_error)) as caught:
                            probe()
                        self.assertIs(caught.exception, read_error)
                        self.assertTrue(caught.exception.discovery_shutdown_confirmed)
                self.assertEqual(stops, [False])
                self.assertTrue(handles[0].shutdown_confirmed(settle_seconds=0))
            finally:
                blocked[0] = False
                if handles and original_snapshot is not None:
                    handles[0]._members.snapshot = original_snapshot

    def test_timeout_refreshes_stop_fact_after_actual_pipe_eof(self):
        self.after_pipe_drain()

    def test_reader_exception_refreshes_stop_fact_after_actual_pipe_eof(self):
        self.after_pipe_drain(read_error=ValueError("synthetic reader failure"))

    def test_dsh_final_stop_preserves_timeout_after_actual_pipe_eof(self):
        self.after_pipe_drain(dsh=True)

    def test_dsh_final_stop_preserves_pipe_failure_after_actual_pipe_eof(self):
        self.after_pipe_drain(dsh=True, read_error=OSError("synthetic pipe failure"))

    def test_dsh_final_stop_preserves_success_and_nonzero_output(self):
        for code in (0, 3):
            with self.subTest(exit_code=code), self.fixture(exit_code=code) as (root, command, handles):
                result = discovery._probe_dsh_version(command, self.environment(root), time.monotonic() + 5,
                                                     state_dir=Path(os.environ["HMB_STATE_DIR"]))
                self.assertEqual(result, ("probe-failed" if code else None, b"1.2.3\n"))
                self.assertTrue(handles[0].shutdown_confirmed(settle_seconds=0))
