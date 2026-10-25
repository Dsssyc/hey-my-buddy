"""Real POSIX fork/group timing; Windows Job and pipe timing live in test_windows_probe_pipe."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
from temporary import TemporaryDirectory
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.base import ProcessHandle


# The guardian is a registered descendant in the same session, outside the
# target group except until TERM in fork mode. It owns and reaps every member.
# Keeping a killed member unreaped holds a real group open. In fork mode its
# TERM handler forks a TERM-ignoring child; a separate group and a join gate
# put that child deterministically after the first KILL's membership snapshot.
_GROUP_FIXTURE = '''
import os, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
mode = sys.argv[2]
pgid = os.getpid()
def await_file(name):
    deadline = time.monotonic() + 30
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('fixture gate expired: ' + name)
        time.sleep(0.002)
def record(name, value):
    (root / name).write_text(str(value), encoding='utf-8', newline='\\n')
guardian = os.fork()
if guardian:
    await_file('ready')
    print('1.2.3', flush=True)
    os._exit(int(sys.argv[3]))
record('guardian.pid', os.getpid())
if mode != 'fork':
    os.setpgid(0, 0)
member = os.fork()
if member == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.setpgid(0, pgid)
    if mode != 'pipe':
        os.close(1)
    os.close(0)
    os.close(2)
    record('member.pid', os.getpid())
    while True:
        signal.pause()
os.close(0)
os.close(1)
os.close(2)
newborn = None
def on_term(signum, frame):
    global newborn
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.setpgid(0, 0)
    newborn = os.fork()
    if newborn == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        record('newborn.pid', os.getpid())
        await_file('join')
        os.setpgid(0, pgid)
        record('joined', os.getpid())
        while True:
            signal.pause()
    await_file('newborn.pid')
    record('forked', newborn)
if mode == 'fork':
    signal.signal(signal.SIGTERM, on_term)
await_file('member.pid')
record('ready', pgid)
await_file('reap')
reaped = [os.waitpid(member, 0)]
if mode == 'fork':
    await_file('forked')
    reaped.append(os.waitpid(newborn, 0))
record('reaped', repr(reaped))
os._exit(0)
'''


@unittest.skipIf(os.name == "nt", "POSIX fork/group race; Windows Job probe has its own pipe tests")
class DiscoveryStopTimingTests(unittest.TestCase):
    def wait_for(self, predicate, description):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail(description)

    @staticmethod
    def group_gone(pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # An unobservable group is still unconfirmed.
        return False

    @staticmethod
    def pid_gone(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    @contextmanager
    def controlled_group(self, mode, *, exit_code=0):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            script = root / 'group.py'
            script.write_text(_GROUP_FIXTURE, encoding="utf-8", newline="\n")
            processes = []
            popen = subprocess.Popen

            def spawn(*args, **kwargs):
                process = popen(*args, **kwargs)
                processes.append(process)
                return process

            try:
                with patch.object(discovery.subprocess, 'Popen', side_effect=spawn):
                    yield root, [sys.executable, str(script), str(root), mode, str(exit_code)], processes
            finally:
                self.assertEqual(len(processes), 1, 'fixture must have one owned leader')
                process = processes[0]
                # Missing registration is a fixture failure, never stop evidence.
                self.wait_for(lambda: (root / 'ready').exists(), 'fixture registration missing')
                guardian = int((root / 'guardian.pid').read_text(encoding="utf-8"))
                member = int((root / 'member.pid').read_text(encoding="utf-8"))
                if mode == 'fork':
                    if not (root / 'forked').exists():
                        os.kill(guardian, signal.SIGTERM)
                    self.wait_for(lambda: (root / 'forked').exists(), 'TERM child not registered')
                    (root / 'join').touch()
                    self.wait_for(lambda: (root / 'joined').exists(), 'TERM child did not join')
                (root / 'reap').touch()
                children = [member]
                if mode == 'fork':
                    children.append(int((root / 'newborn.pid').read_text(encoding="utf-8")))
                for child in children:
                    try:
                        os.kill(child, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.wait_for(lambda: (root / 'reaped').exists(), 'guardian did not reap its children')
                self.wait_for(lambda: self.pid_gone(guardian), 'guardian still exists')
                self.clean_group(process)
                self.assertTrue(self.pid_gone(member), 'registered member still exists')
                if mode == 'fork':
                    newborn = int((root / 'newborn.pid').read_text(encoding="utf-8"))
                    self.assertTrue(self.pid_gone(newborn), 'registered TERM child still exists')

    def start_group(self, root, command):
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   env={'PATH': '/usr/bin:/bin', 'HOME': str(root)})
        self.wait_for(lambda: (root / 'ready').exists(), 'group did not register')
        process.wait(timeout=20)
        return process

    def clean_group(self, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=20)
        self.wait_for(lambda: self.group_gone(process.pid), "fixture group still exists")
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    @contextmanager
    def capture_probe(self):
        processes = []
        popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        try:
            with patch.object(discovery.subprocess, "Popen", side_effect=spawn):
                yield processes
        finally:
            self.assertTrue(processes, "probe did not create a fixture process")
            for process in processes:
                self.clean_group(process)

    def test_short_lived_background_child_preserves_success(self):
        with TemporaryDirectory() as directory, self.capture_probe() as processes:
            result = discovery.probe(
                ["/bin/sh", "-c", "sleep 0.2 >/dev/null 2>&1 & echo 1.2.3"],
                {"PATH": "/usr/bin:/bin", "HOME": directory})
            self.assertEqual(result, (None, b"1.2.3\n"))
            self.assertEqual(len(processes), 1)
            self.assertTrue(self.group_gone(processes[0].pid))

    def test_group_disappearance_is_observed_after_delayed_reap(self):
        with self.controlled_group('hold') as (root, command, processes):
            process = self.start_group(root, command)
            killpg = os.killpg
            observations = 0
            killed = False

            def observe(pgid, sig):
                nonlocal observations, killed
                if pgid == process.pid and sig == signal.SIGKILL:
                    killed = True
                if pgid == process.pid and sig == 0 and killed:
                    observations += 1
                    if observations == 3:
                        (root / 'reap').touch()
                        self.wait_for(lambda: self.group_gone(pgid), 'delayed reap did not finish')
                return killpg(pgid, sig)

            with patch.object(discovery.os, 'killpg', side_effect=observe):
                self.assertTrue(discovery._terminate_group(process))
            self.assertGreaterEqual(observations, 3)
            self.assertTrue(self.group_gone(process.pid))

    def test_term_fork_joining_after_first_kill_is_stopped_by_later_signal(self):
        with self.controlled_group('fork') as (root, command, processes):
            process = self.start_group(root, command)
            killpg = os.killpg
            kills = 0

            def signal_at_gate(pgid, sig):
                nonlocal kills
                result = killpg(pgid, sig)
                if pgid == process.pid and sig == signal.SIGTERM:
                    self.wait_for(lambda: (root / 'forked').exists(), 'TERM did not fork a child')
                if pgid == process.pid and sig == signal.SIGKILL:
                    kills += 1
                    if kills == 1:
                        (root / 'join').touch()
                        self.wait_for(lambda: (root / 'joined').exists(), 'new child did not join')
                        newborn = int((root / 'newborn.pid').read_text(encoding="utf-8"))
                        self.assertEqual(os.getpgid(newborn), process.pid)
                        os.kill(newborn, signal.SIGTERM)
                        self.assertFalse(self.pid_gone(newborn), 'new child must ignore TERM')
                        (root / 'reap').touch()
                return result

            with patch.object(discovery.os, 'killpg', side_effect=signal_at_gate):
                self.assertTrue(discovery._terminate_group(process))
            self.assertGreaterEqual(kills, 2)
            self.assertTrue(self.group_gone(process.pid))
            newborn = int((root / 'newborn.pid').read_text(encoding="utf-8"))
            self.assertTrue(self.pid_gone(newborn))

    def test_unreaped_real_group_exhausts_stop_budget_without_confirmation(self):
        with self.controlled_group('hold') as (root, command, processes):
            result = []
            started = time.monotonic()
            thread = threading.Thread(
                target=lambda: result.append(discovery.probe(
                    command, {'PATH': '/usr/bin:/bin', 'HOME': str(root)}, deadline=started + 2)),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), 'probe exceeded its bounded stop wait')
            self.assertEqual(result, [('shutdown-unverified', b'')])
            self.assertFalse(self.group_gone(processes[0].pid))

    def pipe_stop_after_drain(self, read_error=None):
        with self.controlled_group('pipe') as (root, command, processes):
            selector = discovery.selectors.DefaultSelector()
            select = selector.select
            terminate = discovery._terminate_group
            stop_results = []
            first = True
            clock_offset = [0.0]
            probe_clock = SimpleNamespace(monotonic=lambda: time.monotonic() + clock_offset[0],
                                          sleep=time.sleep)

            def stop(process):
                result = terminate(process)
                stop_results.append(result)
                return result

            def drain_then_reap(timeout):
                nonlocal first
                if first and read_error is not None:
                    first = False
                    self.wait_for(lambda: (root / 'ready').exists(), 'reader fixture not ready')
                    raise read_error
                if first:
                    first = False
                    self.wait_for(lambda: (root / 'ready').exists(), 'reader fixture not ready')
                    clock_offset[0] = 10.0
                    return []
                events = select(timeout)
                if stop_results and events:
                    (root / 'reap').touch()
                    self.wait_for(lambda: self.group_gone(processes[0].pid), 'drain reap did not finish')
                return events

            with patch.object(discovery.selectors, 'DefaultSelector', return_value=selector), \
                 patch.object(selector, 'select', side_effect=drain_then_reap), \
                 patch.object(discovery, '_terminate_group', side_effect=stop), \
                 patch.object(discovery, 'time', probe_clock):
                if read_error is None:
                    result = discovery.probe(command, {'PATH': '/usr/bin:/bin', 'HOME': str(root)},
                                              deadline=time.monotonic() + 5)
                else:
                    with self.assertRaises(type(read_error)) as caught:
                        discovery.probe(command, {'PATH': '/usr/bin:/bin', 'HOME': str(root)},
                                         deadline=time.monotonic() + 2)
                    result = caught.exception
            self.assertTrue(self.group_gone(processes[0].pid))
            return result, stop_results

    def test_timeout_refreshes_group_fact_after_actual_pipe_eof(self):
        result, stop_results = self.pipe_stop_after_drain()
        self.assertEqual(result, ('scan-timeout', b''))
        self.assertEqual(stop_results, [False, True])

    def test_reader_exception_refreshes_group_fact_after_actual_pipe_eof(self):
        read_error = ValueError('synthetic reader failure')
        result, stop_results = self.pipe_stop_after_drain(read_error)
        self.assertIs(result, read_error)
        self.assertIs(getattr(result, 'discovery_shutdown_confirmed', None), True)
        self.assertEqual(stop_results, [False, True])

    def dsh_final_stop(self, mode, *, exit_code=0, pipe_error=False):
        with self.controlled_group(mode, exit_code=exit_code) as (root, command, processes):
            private = root / 'dsh-probe'
            private.mkdir()
            observe = ProcessHandle.shutdown_confirmed
            final_facts = []

            def final_observation(handle, **kwargs):
                (root / 'reap').touch()
                self.wait_for(lambda: self.group_gone(handle.pid), 'final owned group did not disappear')
                actual = observe(handle, **kwargs)
                final_facts.append(actual)
                return actual

            selector = discovery.selectors.DefaultSelector()
            select = selector.select
            first = True
            clock_offset = [0.0]
            probe_clock = SimpleNamespace(monotonic=lambda: time.monotonic() + clock_offset[0],
                                          sleep=time.sleep)

            def read(timeout):
                nonlocal first
                if first and pipe_error:
                    first = False
                    self.wait_for(lambda: (root / 'ready').exists(), 'reader fixture not ready')
                    raise OSError('synthetic pipe read error')
                if first and mode == 'pipe':
                    first = False
                    self.wait_for(lambda: (root / 'ready').exists(), 'reader fixture not ready')
                    clock_offset[0] = 10.0
                    return []
                return select(timeout)

            with patch.object(discovery.private_dirs, 'make_temporary_dir', return_value=private), \
                 patch.object(ProcessHandle, 'shutdown_confirmed', final_observation), \
                 patch.object(discovery.selectors, 'DefaultSelector', return_value=selector), \
                 patch.object(selector, 'select', side_effect=read), \
                 patch.object(discovery, 'time', probe_clock):
                result = discovery._probe_dsh_version(command, {'PATH': '/usr/bin:/bin', 'HOME': str(root)},
                                                      time.monotonic() + 5, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(final_facts, [True])
            self.assertFalse(private.exists())
            self.assertTrue(self.group_gone(processes[0].pid))
            return result

    def test_dsh_final_owned_stop_preserves_success_output(self):
        self.assertEqual(self.dsh_final_stop('hold'), (None, b'1.2.3\n'))

    def test_dsh_final_owned_stop_preserves_nonzero_exit_output(self):
        self.assertEqual(self.dsh_final_stop('hold', exit_code=3), ('probe-failed', b'1.2.3\n'))

    def test_dsh_final_owned_stop_preserves_timeout(self):
        self.assertEqual(self.dsh_final_stop('pipe'), ('scan-timeout', b''))

    def test_dsh_final_owned_stop_preserves_pipe_error(self):
        self.assertEqual(self.dsh_final_stop('hold', pipe_error=True), ('pipe-failed', b''))
