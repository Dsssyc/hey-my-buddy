"""Shared discovery process mechanics and inline Router collection over private fixtures."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
from subprocess import DEVNULL
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import base, controller
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.protocol import owned_process as windows_process
from hey_my_buddy.protocol.owned_process import ProcessCreationEvidence
from hey_my_buddy.protocol import process_controller, process_handle
from hey_my_buddy.buddy.harnesses.controller import (
    ControllerCollection,
    collect_controller,
    launch_controller,
    read_strict_result,
    signal_name,
    stop_confirmed,
)
from hey_my_buddy.buddy.roles import structured_call


CHILD_SOURCE = "print('controller-out')"
NATIVE_STOPPED = b'{"processState":{"shutdownConfirmed":true}}'
NATIVE_UNKNOWN = b'{"processState":{"shutdownConfirmed":false}}'


@contextmanager
def partial_log_open_failure(*, close_error=False):
    """Open stdout for real, refuse stderr, and retain any leaked FD for cleanup."""
    state = {"error": OSError(errno.EMFILE, "fixture stderr open exhausted"),
             "close_error": OSError(errno.EIO, "fixture stdout close failed"),
             "stdout": None, "closed": False}
    original_open = process_handle.open_regular_fd
    original_close, original_fstat = os.close, os.fstat

    def open_file(path, *args, **kwargs):
        name = Path(path).name
        if name in ("stderr", "runner.stderr.log"):
            raise state["error"]
        descriptor = original_open(path, *args, **kwargs)
        if name in ("stdout", "runner.stdout.log"):
            state["stdout"] = descriptor
        return descriptor

    def close(descriptor):
        original_close(descriptor)
        if descriptor == state["stdout"] and not state["closed"]:
            state["closed"] = True
            if close_error:
                raise state["close_error"]

    try:
        with mock.patch.object(process_handle, "open_regular_fd", open_file), \
             mock.patch.object(process_handle.os, "close", close):
            yield state
    finally:
        if state["stdout"] is not None:
            try:
                original_fstat(state["stdout"])
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
            else:
                original_close(state["stdout"])


@contextmanager
def controller_log_close_failure(*, log="stdout", receipt=None,
                                 observation_blocked=False, observation_raises=False,
                                 exit_after_receipt=False):
    """A real private controller writes its receipt before one post-spawn FD fault."""
    state = {"error": OSError(errno.EIO, "fixture controller log close failed"),
             "process": None, "descriptors": [], "closed": [], "observations": 0}
    original_open = process_controller.open_logs
    original_spawn = process_controller.owned_popen
    original_close = os.close
    original_killpg = os.killpg if os.name != "nt" else None
    original_observe = ProcessHandle.shutdown_confirmed

    def open_logs(paths):
        state["paths"] = paths
        pair = original_open(paths)
        state["descriptors"] = list(pair)
        return pair

    def spawn(*args, **kwargs):
        ready = Path(state["paths"]["stdout"]).parent / ("fixture-ready-" + str(time.monotonic_ns()))
        source = ("import os,time\nfrom pathlib import Path\n"
                  + (f"os.write(1, {receipt!r})\n" if receipt is not None else "")
                  + f"Path({str(ready)!r}).write_text('ready', encoding='utf-8', newline='\\n')\n"
                  + ("" if exit_after_receipt else "time.sleep(60)\n"))
        state["process"] = original_spawn([sys.executable, "-c", source], *args[1:], **kwargs)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if state["process"].poll() is not None or time.monotonic() > deadline:
                raise AssertionError("private controller did not publish its fixture receipt")
            time.sleep(0.01)
        return state["process"]

    def observe(handle, *args, **kwargs):
        state["observations"] += 1
        if observation_raises:
            raise OSError("fixture stop observation failed")
        return original_observe(handle, *args, **kwargs)

    def close(descriptor):
        original_close(descriptor)
        if state["process"] is not None and descriptor in state["descriptors"] and descriptor not in state["closed"]:
            state["closed"].append(descriptor)
            if log is not None and descriptor == state["descriptors"][0 if log == "stdout" else 1]:
                raise state["error"]

    def killpg(pgid, sig):
        if state["process"] is not None and pgid == state["process"].pid and sig == 0:
            raise PermissionError(errno.EPERM, "fixture group observation refused")
        return original_killpg(pgid, sig)

    def active(job):
        if state["process"] is not None and job is state["process"]._buddy_job:
            raise PermissionError(errno.EPERM, "fixture Job observation refused")
        return original_active(job)

    original_active = windows_process.OwnedJob.active

    try:
        with mock.patch.object(process_controller, "open_logs", open_logs), \
             mock.patch.object(process_controller, "owned_popen", spawn), \
             mock.patch.object(process_controller.os, "close", close), \
             mock.patch.object(ProcessHandle, "shutdown_confirmed", observe):
            if observation_blocked:
                if os.name == "nt":
                    with mock.patch.object(windows_process.OwnedJob, "active", active):
                        yield state
                else:
                    with mock.patch.object(process_controller.os, "killpg", killpg):
                        yield state
            else:
                yield state
    finally:
        if state["process"] is not None:
            handle = ProcessHandle(state["process"], own_group=True, log_paths=state["paths"])
            handle.terminate(grace_seconds=0.2)
            handle.shutdown_confirmed()
        for descriptor in state["descriptors"]:
            if descriptor not in state["closed"]:
                original_close(descriptor)


class FakeProcess:
    """The Popen surface the outer layer touches, without a real child."""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = None

    def poll(self):
        return self.returncode


class FakeHandle:
    """A handle whose outer stop observation is recorded, not waited on."""

    def __init__(self, confirmed):
        self.confirmed = confirmed
        self.calls = 0

    def shutdown_confirmed(self, settle_seconds=2.0):
        self.calls += 1
        return self.confirmed


class PartialLogOpenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-partial-logs-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = {"stdout": str(self.root / "stdout"), "stderr": str(self.root / "stderr")}

    def assert_closed(self, descriptor):
        self.assertIsNotNone(descriptor)
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptor)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_existing_log_is_not_truncated_or_followed(self):
        sentinel = self.root / 'sentinel'
        sentinel.write_bytes(b'existing log')
        for linked in (False, True):
            path = self.root / ('linked-log' if linked else 'existing-log')
            if linked:
                path.symlink_to(sentinel)
            else:
                path.write_bytes(b'existing log')
            with self.subTest(linked=linked), self.assertRaises(FileExistsError):
                base.open_logs({'stdout': str(path), 'stderr': self.paths['stderr']})
            self.assertEqual(sentinel.read_bytes(), b'existing log')
            self.assertEqual(path.read_bytes(), b'existing log')
        self.assertFalse(Path(self.paths['stderr']).exists())

    def test_stderr_open_failure_closes_the_real_stdout_fd(self):
        with partial_log_open_failure() as state:
            with self.assertRaises(OSError) as caught:
                base.open_logs(self.paths)
            self.assertIs(caught.exception, state["error"])
            self.assert_closed(state["stdout"])
            self.assertTrue(Path(self.paths["stdout"]).exists())
            self.assertFalse(Path(self.paths["stderr"]).exists())

    def test_stdout_close_error_preserves_the_original_stderr_open_error(self):
        with partial_log_open_failure(close_error=True) as state:
            with self.assertRaises(OSError) as caught:
                base.open_logs(self.paths)
            self.assertIs(caught.exception, state["error"])
            self.assertIs(caught.exception.__cause__, state["close_error"])
            self.assert_closed(state["stdout"])

    def test_partial_open_failure_never_spawns_and_carries_no_child_receipt(self):
        with partial_log_open_failure() as state, \
             mock.patch.object(process_controller, "owned_popen", wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                launch_controller(prepare=lambda: ([sys.executable, "-c", "pass"], str(self.root), {}),
                                  log_paths=self.paths)
            spawn.assert_not_called()
            self.assertIs(caught.exception, state["error"])
            self.assertEqual(caught.exception.owned_popen_evidence, ProcessCreationEvidence(False, True, None))
            self.assert_closed(state["stdout"])

    def test_command_uses_the_same_partial_open_cleanup_before_spawn(self):
        from hey_my_buddy.buddy.runtime import command
        context = base.ExecutionContext(task_id="private-task", attempt_id="private-attempt", generation=1,
                                        spec={"argv": [sys.executable, "-c", "pass"], "task": "fixture",
                                              "cwd": str(self.root), "expectedSeconds": 1},
                                        directory=self.root, runtime={}, environment=dict(os.environ), session_record_path=None)
        with partial_log_open_failure() as state, \
             mock.patch.object(command, "owned_popen", wraps=command.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                command.CommandAdapter().start(context)
            spawn.assert_not_called()
            self.assertIs(caught.exception, state["error"])
            self.assert_closed(state["stdout"])


class LaunchControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-launch-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log_paths = {"stdout": str(self.root / "stdout"), "stderr": str(self.root / "stderr")}

    def test_real_child_writes_through_the_opened_logs_and_stamps_its_deadline(self):
        handle = launch_controller(prepare=lambda: ([sys.executable, "-c", CHILD_SOURCE],
                                                    str(self.root), dict(os.environ)),
                                   log_paths=self.log_paths, timeout_seconds=30)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(10))
        self.assertEqual(Path(self.log_paths["stdout"]).read_text(encoding="utf-8").strip(), "controller-out")
        self.assertEqual(Path(self.log_paths["stderr"]).read_text(encoding="utf-8"), "")
        self.assertGreater(handle.deadline, time.monotonic())
        self.assertFalse(math.isinf(handle.deadline))
        self.assertTrue(handle.shutdown_confirmed())
        self.assertEqual(handle.process.returncode, 0)

    def test_prepare_runs_between_the_logs_and_the_owned_spawn(self):
        process = FakeProcess()
        trace = []

        def prepare():
            trace.append("prepare")
            return (["fixture"], str(self.root), {"FIXTURE": "1"})

        original_open_logs = process_controller.open_logs

        def traced_open_logs(paths):
            trace.append("logs")
            return original_open_logs(paths)

        with mock.patch.object(process_controller, "open_logs", traced_open_logs), \
                mock.patch.object(process_controller, "owned_popen", return_value=process) as spawn:
            handle = launch_controller(prepare=prepare, log_paths=self.log_paths)
        self.assertEqual(trace, ["logs", "prepare"])
        arguments, keyword = spawn.call_args
        self.assertEqual(arguments[0], ["fixture"])
        self.assertEqual(keyword["cwd"], str(self.root))
        self.assertEqual(keyword["env"], {"FIXTURE": "1"})
        self.assertIs(keyword["stdin"], DEVNULL)
        self.assertTrue(keyword["start_new_session"])
        self.assertTrue(keyword["close_fds"])
        for role in ("stdout", "stderr"):
            with self.assertRaises(OSError, msg=role):
                os.fstat(keyword[role])
        self.assertIs(handle.process, process)
        self.assertTrue(handle.own_group)
        self.assertEqual(handle.log_paths, self.log_paths)

    def test_positive_timeout_stamps_now_plus_timeout_and_grace(self):
        with mock.patch.object(process_controller, "owned_popen", return_value=FakeProcess()):
            handle = launch_controller(prepare=lambda: (["fixture"], None, {}),
                                       log_paths=self.log_paths, timeout_seconds=5, grace_seconds=10)
        self.assertAlmostEqual(handle.deadline, time.monotonic() + 15, delta=1.0)

    def test_discovery_launch_without_timeout_stamps_no_deadline(self):
        with mock.patch.object(process_controller, "owned_popen", return_value=FakeProcess()) as spawn:
            handle = launch_controller(prepare=lambda: (["fixture"], None, {}), log_paths=self.log_paths)
        self.assertFalse(hasattr(handle, "deadline"))
        self.assertIsNone(spawn.call_args.kwargs["cwd"])


class LaunchFailureEvidenceTests(unittest.TestCase):
    """The creating side proves no child, observed cleanup, or unknown ownership."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-failure-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = {"stdout": str(self.root / "stdout"), "stderr": str(self.root / "stderr")}

    def launch(self):
        return launch_controller(prepare=lambda: ([sys.executable, "-c", "import time; time.sleep(60)"],
                                                 str(self.root), dict(os.environ)), log_paths=self.paths)

    def test_log_open_emfile_proves_spawn_was_never_called(self):
        error = OSError(errno.EMFILE, "fixture logs exhausted")
        with mock.patch.object(process_controller, "open_logs", side_effect=error), \
             mock.patch.object(process_controller, "owned_popen", wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                self.launch()
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertEqual(error.owned_popen_evidence, ProcessCreationEvidence(False, True, None))

    def test_prepare_failure_proves_no_child_and_closes_both_logs(self):
        error = ValueError("fixture command preparation failed")
        opened = []
        original_open = process_controller.open_logs

        def open_logs(paths):
            pair = original_open(paths)
            opened.extend(pair)
            return pair

        with mock.patch.object(process_controller, "open_logs", open_logs), \
             mock.patch.object(process_controller, "owned_popen", wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(ValueError) as caught:
                launch_controller(prepare=mock.Mock(side_effect=error), log_paths=self.paths)
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertEqual(error.owned_popen_evidence, ProcessCreationEvidence(False, True, None))
        self.assertEqual(len(opened), 2)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        self.assertTrue(Path(self.paths["stdout"]).exists())
        self.assertTrue(Path(self.paths["stderr"]).exists())

    def test_posix_spawn_failure_consumes_owned_no_child_receipt(self):
        with self.assertRaises(OSError) as caught:
            launch_controller(prepare=lambda: ([str(self.root / "missing-executable")], None, {}),
                              log_paths=self.paths)
        self.assertEqual(caught.exception.owned_popen_evidence, ProcessCreationEvidence(False, True, None))

    def windows_failure(self, *, stopped, close_log=False):
        from buddy.runtime.test_windows_process import FakeAPI, FakeProcess

        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = "assign"
        if not stopped:
            process.kill = mock.Mock(side_effect=OSError("fixture held child cannot be killed"))
        original_spawn = windows_process.owned_popen
        original_open = process_controller.open_logs
        original_close = os.close
        seen = []
        opened = []

        def open_logs(paths):
            pair = original_open(paths)
            opened.extend(pair)
            return pair

        def close(descriptor):
            original_close(descriptor)
            if close_log and descriptor == opened[1]:
                raise OSError(errno.EIO, "fixture Windows launch log close failed")

        def spawn(*args, **kwargs):
            try:
                return original_spawn(*args, windows_api=api, **kwargs)
            except BaseException as error:
                seen.append(error.owned_popen_evidence)
                raise

        with mock.patch.object(windows_process.subprocess, "Popen", return_value=process), \
             mock.patch.object(process_controller, "owned_popen", spawn), \
             mock.patch.object(process_controller, "open_logs", open_logs), \
             mock.patch.object(process_controller.os, "close", close):
            with self.assertRaises((RuntimeError, OSError)) as caught:
                self.launch()
        evidence = caught.exception.owned_popen_evidence
        self.assertTrue(evidence.created)
        self.assertIs(evidence.shutdown_confirmed, stopped)
        self.assertIs(evidence, seen[0])
        self.assertNotIn(("resume", process._handle), api.events)
        if close_log:
            self.assertEqual(caught.exception.errno, errno.EIO)
            self.assertIs(caught.exception.__context__.owned_popen_evidence, seen[0])

    def test_windows_created_before_registration_keeps_unknown_cleanup(self):
        self.windows_failure(stopped=False)

    def test_windows_created_before_registration_keeps_confirmed_cleanup(self):
        self.windows_failure(stopped=True)

    def test_windows_ownership_and_log_close_failures_keep_unknown_receipt(self):
        self.windows_failure(stopped=False, close_log=True)

    def test_posix_each_log_close_failure_keeps_observed_owned_cleanup(self):
        for log in ("stdout", "stderr"):
            self.paths = {name: str(self.root / (log + "-" + name)) for name in ("stdout", "stderr")}
            with self.subTest(log=log), controller_log_close_failure(log=log) as state:
                with self.assertRaises(OSError) as caught:
                    self.launch()
                self.assertIs(caught.exception, state["error"])
                evidence = caught.exception.owned_popen_evidence
                self.assertTrue(evidence.created)
                self.assertIs(evidence.shutdown_confirmed, True)
                self.assertIsNotNone(state["process"].poll())
                self.assertEqual(evidence.exit_code, state["process"].returncode)
                self.assertEqual(state["closed"], state["descriptors"])

    def test_posix_log_close_failure_keeps_unobservable_group_unknown(self):
        with controller_log_close_failure(observation_blocked=True) as state:
            with self.assertRaises(OSError) as caught:
                self.launch()
            self.assertIs(caught.exception, state["error"])
            self.assertIs(caught.exception.execution_handle.process, state["process"])
            evidence = caught.exception.owned_popen_evidence
            self.assertTrue(evidence.created)
            self.assertIs(evidence.shutdown_confirmed, False)
            self.assertIsNotNone(state["process"].poll())

    def test_cleanup_observation_error_carries_created_unknown_receipt(self):
        with controller_log_close_failure(observation_raises=True) as state:
            with self.assertRaises(OSError) as caught:
                self.launch()
            self.assertEqual(str(caught.exception), "fixture stop observation failed")
            self.assertIs(caught.exception.__context__, state["error"])
            self.assertEqual(caught.exception.owned_popen_evidence,
                             ProcessCreationEvidence(True, False, state["process"].returncode))

    def test_handle_registration_failure_stops_and_hands_out_the_created_child(self):
        error = ValueError("fixture handle registration failed")
        from hey_my_buddy.protocol import process_identity
        with controller_log_close_failure(log=None) as state:
            with mock.patch.object(process_identity, "_creation_time", side_effect=error):
                with self.assertRaises(ValueError) as caught:
                    self.launch()
            self.assertIs(caught.exception, error)
            self.assertEqual(error.owned_popen_evidence,
                             ProcessCreationEvidence(True, True, state["process"].returncode))
            self.assertIsNotNone(state["process"].poll())
            self.assertIs(error.execution_handle.process, state["process"])


class StrictReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-read-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "stdout"

    def write(self, raw: bytes):
        self.path.write_bytes(raw)

    def test_a_complete_object_result_is_decoded_by_the_common_strict_decoder(self):
        self.write(b'{"status": "ok", "processState": {"shutdownConfirmed": true}}')
        self.assertEqual(read_strict_result(self.path),
                         {"status": "ok", "processState": {"shutdownConfirmed": True}})

    def test_non_object_values_and_undecodable_bytes_return_none(self):
        for raw in (b"[1, 2]", b'"text"', b"17", b"null", b"{broken", b"\xff\xfe\x00"):
            with self.subTest(raw=raw):
                self.write(raw)
                self.assertIsNone(read_strict_result(self.path))

    def test_duplicate_members_and_non_finite_numbers_are_refused(self):
        for raw in (b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}', b'{"a": 1e999}'):
            with self.subTest(raw=raw):
                self.write(raw)
                self.assertIsNone(read_strict_result(self.path))

    def test_the_512_kib_read_cap_refuses_before_decoding(self):
        self.write(b" " * (512 * 1024 + 1))

        def explode(raw):
            raise AssertionError("an over-limit result must never reach the strict decoder")

        with mock.patch.object(controller, "decode_strict_json", explode):
            self.assertIsNone(read_strict_result(self.path))
        self.assertEqual(self.path.stat().st_size, 512 * 1024 + 1)

    def test_exactly_the_cap_is_still_read_and_decoded(self):
        payload = json.dumps({"padding": "x" * (512 * 1024 - 15)}).encode()
        self.assertEqual(len(payload), 512 * 1024)
        self.write(payload)
        self.assertEqual(read_strict_result(self.path)["padding"], payload[13:-2].decode())

    def test_a_missing_result_file_returns_none(self):
        self.assertIsNone(read_strict_result(self.path))


class PlainEvidenceReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-plain-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "stdout"


    def test_router_read_is_the_same_raising_read_the_retention_uses(self):
        from hey_my_buddy.errors import BoardError
        self.path.write_bytes(b'{"status": "ok"}')
        self.assertEqual(controller.read_plain_evidence(self.path), {"status": "ok"})
        linked = self.root / "linked"
        linked.symlink_to(self.path)
        with self.assertRaises(BoardError):
            controller.read_plain_evidence(linked)

    def test_router_read_enforces_bound_on_open_file(self):
        from hey_my_buddy.errors import BoardError

        self.path.write_bytes(b" " * (controller.ROUTER_EVIDENCE_BYTES + 1))
        with self.assertRaises(BoardError) as caught:
            controller.read_plain_evidence(self.path)
        self.assertEqual(caught.exception.code, "PRIVATE_PATH_UNSAFE")


class StopConfirmedTests(unittest.TestCase):
    def test_both_layers_confirm_in_order(self):
        handle = FakeHandle(True)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle.shutdown_confirmed), True)
        self.assertEqual(handle.calls, 1)

    def test_a_missing_or_untrue_receipt_never_starts_the_outer_observation(self):
        for payload in (None, {}, {"processState": {}}, {"other": True},
                        {"processState": {"shutdownConfirmed": False}},
                        {"processState": {"shutdownConfirmed": "true"}},
                        {"processState": {"shutdownConfirmed": 1}}):
            with self.subTest(payload=payload):
                handle = FakeHandle(True)
                self.assertIs(stop_confirmed(payload, handle.shutdown_confirmed), False)
                self.assertEqual(handle.calls, 0, "the outer group must not even be polled")

    def test_a_confirmed_receipt_with_a_live_outer_group_stays_unconfirmed(self):
        handle = FakeHandle(False)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle.shutdown_confirmed), False)
        self.assertEqual(handle.calls, 1)

    def test_a_malformed_process_state_never_starts_the_outer_observation(self):
        for state in (None, [], "stopped", True, 1):
            with self.subTest(state=state):
                handle = FakeHandle(True)
                self.assertIs(stop_confirmed({"processState": state}, handle.shutdown_confirmed), False)
                self.assertEqual(handle.calls, 0)

    def test_a_creation_receipt_observation_is_consumed_once_without_a_handle(self):
        receipt = ProcessCreationEvidence(True, True, -15)
        observations = []

        def observe():
            observations.append(receipt.shutdown_confirmed)
            return receipt.shutdown_confirmed

        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, observe), True)
        self.assertEqual(observations, [True])


class RealStopTests(unittest.TestCase):
    def test_a_terminated_real_child_confirms_only_with_its_receipt(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-stop-")
        self.addCleanup(temporary.cleanup)
        log_paths = {"stdout": str(Path(temporary.name) / "stdout"),
                     "stderr": str(Path(temporary.name) / "stderr")}
        handle = launch_controller(prepare=lambda: ([sys.executable, "-c", "import time; time.sleep(30)"],
                                                    str(temporary.name), dict(os.environ)),
                                   log_paths=log_paths)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertTrue(handle.group_alive())
        self.assertFalse(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle.shutdown_confirmed),
                         "a live group is never stopped while it is observable")
        handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(5))
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle.shutdown_confirmed), True)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": False}}, handle.shutdown_confirmed), False)


class ObservationErrorStopTests(unittest.TestCase):
    """An unavailable POSIX group observation is not termination evidence.

    The Windows Job branch of the same conservative rule is pinned by
    ``buddy.runtime.test_windows_process``; this is the POSIX owned-group branch
    at the shared face: a leader that already exited, whose group can no longer
    be observed, keeps the attempt unconfirmed — unknown stays alive, so the
    collection never reports a stop on the strength of a failed observation.
    """

    def test_an_observation_error_of_the_owned_group_keeps_the_attempt_unconfirmed(self):
        if os.name == "nt":
            self.skipTest("POSIX owned groups only")
        with tempfile.TemporaryDirectory(prefix="controller-observe-") as name:
            log_paths = {"stdout": str(Path(name) / "stdout"), "stderr": str(Path(name) / "stderr")}
            handle = launch_controller(prepare=lambda: ([sys.executable, "-c", "pass"],
                                                    name, dict(os.environ)),
                                       log_paths=log_paths)
            self.assertIsNotNone(handle.wait(5))
            receipt = {"processState": {"shutdownConfirmed": True}}
            self.assertIs(stop_confirmed(receipt, handle.shutdown_confirmed), True,
                          "the group must be observed gone before the fault is injected")
            for error in (PermissionError("the group belongs to another user"),
                          OSError("the group cannot be observed")):
                with self.subTest(error=type(error).__name__), \
                        mock.patch("hey_my_buddy.protocol.process_handle.os.killpg", side_effect=error):
                    self.assertTrue(handle.group_alive())
                    self.assertIs(handle.shutdown_confirmed(settle_seconds=0), False)
                    collection = collect_controller(handle, read=lambda _path: None, stop=lambda payload, owned: stop_confirmed(payload, owned.shutdown_confirmed))
                    self.assertIs(collection.stop_confirmed, False)


class CollectControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-collect-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_collection_carries_payload_exit_code_and_stop_together(self):
        stdout = self.root / "stdout"
        stdout.write_text(json.dumps({"status": "cancelled", "processState": {"shutdownConfirmed": True}}), encoding="utf-8", newline="\n")
        handle = SimpleNamespace(log_paths={"stdout": str(stdout)}, process=FakeProcess(-15),
                                 shutdown_confirmed=lambda: True)
        collection = collect_controller(handle, read=read_strict_result, stop=lambda payload, owned: stop_confirmed(payload, owned.shutdown_confirmed))
        self.assertIsInstance(collection, ControllerCollection)
        self.assertEqual(collection.payload, {"status": "cancelled", "processState": {"shutdownConfirmed": True}})
        self.assertEqual(collection.exit_code, -15)
        self.assertIs(collection.stop_confirmed, True)
        self.assertEqual(list(collection.__dataclass_fields__), ["payload", "exit_code", "stop_confirmed"])

    def test_every_collection_requires_an_explicit_stop_rule(self):
        handle = SimpleNamespace(log_paths={}, process=FakeProcess(0))
        with self.assertRaises(TypeError):
            collect_controller(handle, read=read_strict_result)


    def test_an_unreadable_result_keeps_the_attempt_unconfirmed(self):
        handle = SimpleNamespace(log_paths={"stdout": str(self.root / "missing")},
                                 process=FakeProcess(1), shutdown_confirmed=lambda: True)
        collection = collect_controller(handle, read=read_strict_result, stop=lambda payload, owned: stop_confirmed(payload, owned.shutdown_confirmed))
        self.assertIsNone(collection.payload)
        self.assertIs(collection.stop_confirmed, False)
        self.assertEqual(collection.exit_code, 1)


def _review_handle(root, outer=True):
    from buddy.harnesses.test_run_contract import full_request, full_result
    from hey_my_buddy.buddy.harnesses import run_contract as rc

    request = full_request(root)
    result = rc.RunResult.from_payload(full_result().to_payload() | {
        "nativeFailure": None,
        "value": {"schemaStatus": "valid", "parsed": {"answer": "ok"}},
    })
    return SimpleNamespace(exit_code=0, error=None, result=result, request=request,
                           verdict={"stopReason": None, "elapsedMs": 1},
                           shutdown_confirmed=lambda: outer, role_run_identity=request.identity,
                           role_run_original_identity=request.identity, role_run_active_identity=request.identity,
                           wrap_up_started=False,
                           role_run_control={"operation": "review", "harness": request.harness,
                                             "sessionRecordPath": request.session_record_path})


class RouterCollectCoercionTests(unittest.TestCase):
    """The real Router collect keeps the baseline's outer ``is True`` coercion.

    The probe ``controller-first-review`` pinned exactly these inputs: an outer
    observation of 1, ``unknown`` or None must leave the attempt failed with a
    boolean False stop, never an ok with a truthy non-boolean field.
    """

    def collect_with_outer(self, value):
        temporary = tempfile.TemporaryDirectory(prefix="controller-router-")
        self.addCleanup(temporary.cleanup)
        handle = _review_handle(Path(temporary.name), value)
        return structured_call.collect(handle)

    def test_the_baseline_outer_coercion_table(self):
        for value, status, confirmed in ((True, "ok", True), (1, "failed", False),
                                         ("unknown", "failed", False), (None, "failed", False),
                                         (False, "failed", False)):
            with self.subTest(outer=value):
                outcome = self.collect_with_outer(value)
                self.assertEqual(outcome.status, status)
                self.assertIs(outcome.shutdown_confirmed, confirmed)

    def test_a_failed_in_process_router_run_propagates_its_error(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-router-bad-")
        self.addCleanup(temporary.cleanup)
        handle = _review_handle(Path(temporary.name))
        handle.result = None
        error = RuntimeError("fixture router run failed")
        handle.error = error
        with self.assertRaises(RuntimeError) as caught:
            structured_call.collect(handle)
        self.assertIs(caught.exception, error)


class SignalNameTests(unittest.TestCase):
    def test_only_negative_exit_codes_name_a_signal(self):
        kill_name = "signal-9" if os.name == "nt" else "SIGKILL"
        for code, expected in ((None, None), (0, None), (15, None), (-15, "SIGTERM"), (-9, kill_name),
                               (-999, "signal-999")):
            with self.subTest(code=code):
                self.assertEqual(signal_name(code), expected)


if __name__ == "__main__":
    unittest.main()
