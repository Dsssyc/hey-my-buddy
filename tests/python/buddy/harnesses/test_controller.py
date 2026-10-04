"""The shared mechanical controller layer, against private model-free fixtures.

One real plain-Python child proves the launch wiring end to end; real callers
(CodexAdapter.start, structured_call.collect) prove that the baseline's
evaluation order, log-file creation, descriptor finalization and outer stop
coercion survive the shared face, including on the failure paths.
"""
from __future__ import annotations

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

from hey_my_buddy.buddy.harnesses import controller
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses import runtime_selection
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.controller import (
    ControllerCollection,
    collect_controller,
    launch_controller,
    legacy_node_stop_confirmed,
    read_last_line_result,
    read_router_result,
    read_strict_result,
    router_stop_confirmed,
    signal_name,
    stop_confirmed,
)
from hey_my_buddy.buddy.roles import structured_call
from hey_my_buddy.buddy.roles import turn_io


CHILD_SOURCE = "print('controller-out')"


def strict_decode(raw):
    """A caller's strict decoder: duplicate members and non-finite numbers refused.

    The real harness decoders (codex/zcode/claude protocol ``decode_json``) keep
    their own pinned tests; this stands in for the contract the shared reader
    must propagate.
    """
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _name: (_ for _ in ()).throw(ValueError("non-finite JSON")))


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


class LaunchControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-launch-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log_paths = {"stdout": str(self.root / "stdout"), "stderr": str(self.root / "stderr")}

    def test_real_child_writes_through_the_opened_logs_and_stamps_its_deadline(self):
        handle = launch_controller(prepare=lambda _environment: ([sys.executable, "-c", CHILD_SOURCE],
                                                    str(self.root), dict(os.environ)),
                                   log_paths=self.log_paths, timeout_seconds=30)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(10))
        self.assertEqual(Path(self.log_paths["stdout"]).read_text().strip(), "controller-out")
        self.assertEqual(Path(self.log_paths["stderr"]).read_text(), "")
        self.assertGreater(handle.deadline, time.monotonic())
        self.assertFalse(math.isinf(handle.deadline))
        self.assertTrue(handle.shutdown_confirmed())
        self.assertEqual(handle.process.returncode, 0)

    def test_prepare_runs_between_the_logs_and_the_owned_spawn(self):
        process = FakeProcess()
        trace = []

        def prepare(_environment):
            trace.append("prepare")
            return (["fixture"], str(self.root), {"FIXTURE": "1"})

        original_open_logs = controller.open_logs

        def traced_open_logs(paths):
            trace.append("logs")
            return original_open_logs(paths)

        with mock.patch.object(controller, "open_logs", traced_open_logs), \
                mock.patch.object(controller, "owned_popen", return_value=process) as spawn:
            handle = launch_controller(prepare=prepare, log_paths=self.log_paths,
                                       timeout_seconds=0, unbounded_deadline=math.inf)
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

    def test_zero_timeout_stamps_exactly_each_path_explicit_unbounded_deadline(self):
        for unbounded, expected in ((math.inf, math.inf), (None, None)):
            with self.subTest(unbounded=unbounded):
                with mock.patch.object(controller, "owned_popen", return_value=FakeProcess()):
                    handle = launch_controller(prepare=lambda _environment: (["fixture"], None, {}),
                                               log_paths=self.log_paths,
                                               timeout_seconds=0, unbounded_deadline=unbounded)
                self.assertIs(handle.deadline, expected)

    def test_positive_timeout_stamps_now_plus_timeout_and_grace(self):
        with mock.patch.object(controller, "owned_popen", return_value=FakeProcess()):
            handle = launch_controller(prepare=lambda _environment: (["fixture"], None, {}),
                                       log_paths=self.log_paths, timeout_seconds=5, grace_seconds=10)
        self.assertAlmostEqual(handle.deadline, time.monotonic() + 15, delta=1.0)

    def test_discovery_launch_without_timeout_stamps_no_deadline(self):
        with mock.patch.object(controller, "owned_popen", return_value=FakeProcess()) as spawn:
            handle = launch_controller(prepare=lambda _environment: (["fixture"], None, {}), log_paths=self.log_paths)
        self.assertFalse(hasattr(handle, "deadline"))
        self.assertIsNone(spawn.call_args.kwargs["cwd"])


class CodexCallerLaunchOrderTests(unittest.TestCase):
    """The real Codex caller keeps the baseline order on the failure paths too.

    The baseline opened the run's logs first and evaluated the working directory
    and environment only inside the spawn ``try``: an evaluation failure leaves
    both log files behind, closes their descriptors in the ``finally`` and never
    spawns. The probe ``controller-first-review`` pinned exactly this.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-caller-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_caller(self, failure):
        directory = self.root / f"attempt-{failure}"
        directory.mkdir(mode=0o700)
        paths = {"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")}
        context = SimpleNamespace(directory=directory, environment={}, log_paths=lambda: paths,
                                  timeout_seconds=0)
        trace = []
        opened = []
        original_open_logs = controller.open_logs

        def traced_open_logs(traced_paths):
            trace.append("logs")
            pair = original_open_logs(traced_paths)
            opened.extend(pair)
            return pair

        def traced_cwd(_context):
            trace.append("cwd")
            if failure == "cwd":
                raise ValueError("fixture cwd failure")
            return str(directory)

        def traced_env(*_args, **_kwargs):
            trace.append("env")
            if failure == "env":
                raise ValueError("fixture env failure")
            return {}

        def traced_spawn(*_args, **_kwargs):
            trace.append("spawn")
            raise OSError("fixture spawn failure")

        with mock.patch.object(CodexAdapter, "prepare"), \
                mock.patch.object(controller, "open_logs", traced_open_logs), \
                mock.patch.object(turn_io, "workspace_cwd", traced_cwd), \
                mock.patch.object(runtime_selection, "controller_environment", traced_env), \
                mock.patch.object(controller, "owned_popen", traced_spawn):
            raised = None
            try:
                CodexAdapter().start(context)
            except (ValueError, OSError) as error:
                raised = error
        return paths, trace, opened, raised

    def assert_failure_leaves_finalized_logs(self, failure, expected_trace, expected_error):
        paths, trace, opened, raised = self.run_caller(failure)
        self.assertEqual(trace, expected_trace)
        self.assertIsInstance(raised, expected_error)
        self.assertTrue(Path(paths["stdout"]).exists(), "the baseline leaves both logs behind")
        self.assertTrue(Path(paths["stderr"]).exists(), "the baseline leaves both logs behind")
        for descriptor in opened:
            with self.assertRaises(OSError, msg="the finally must close every opened descriptor"):
                os.fstat(descriptor)

    def test_cwd_failure_keeps_the_baseline_order_logs_then_cwd(self):
        self.assert_failure_leaves_finalized_logs("cwd", ["logs", "cwd"], ValueError)

    def test_env_failure_keeps_the_baseline_order_logs_cwd_then_env(self):
        self.assert_failure_leaves_finalized_logs("env", ["logs", "cwd", "env"], ValueError)

    def test_spawn_failure_still_finalizes_the_opened_logs(self):
        self.assert_failure_leaves_finalized_logs("spawn", ["logs", "cwd", "env", "spawn"], OSError)


class DshCallerFdBoundaryTests(unittest.TestCase):
    """The real DSH caller keeps the baseline's between-step FD boundary.

    The DSH baseline evaluated its selected harness record and native
    environment after opening the run's logs and before its spawn try/finally:
    a failure there leaves the two log descriptors open and the two log files
    in place, while a failure inside the spawn ``try`` (the command assembly)
    closes them. The probe ``1b-dsh-fd-order`` pinned exactly this. The tests
    close only the descriptors they themselves created and still hold; files
    are left to the fixture.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-dsh-fd-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def caller_start(self, failure):
        directory = self.root / f"attempt-{failure}"
        directory.mkdir(mode=0o700)
        paths = {"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")}
        context = SimpleNamespace(environment={"HOME": str(self.root / "home"),
                                              "DSH_HOME": str(self.root / "dsh-home")},
                                  log_paths=lambda: paths, timeout_seconds=0)
        inquiry = {"socketPath": str(directory / "socket"),
                   "resultsPath": str(directory / "results"),
                   "errorPath": str(directory / "error")}
        trace = []
        opened = []
        original_open_logs = controller.open_logs

        def traced_open_logs(traced_paths):
            trace.append("logs")
            pair = original_open_logs(traced_paths)
            opened.extend(pair)
            return pair

        def traced_selected(*_args, **_kwargs):
            trace.append("selected")
            if failure == "selected":
                raise ValueError("fixture selected failure")
            return None

        def traced_arguments(*_args, **_kwargs):
            trace.append("arguments")
            if failure == "arguments":
                raise OSError("fixture arguments failure")
            return []

        with mock.patch.object(DshAdapter, "prepare"), \
                mock.patch.object(DshAdapter, "inquiry_paths", return_value=inquiry), \
                mock.patch.object(controller, "open_logs", traced_open_logs), \
                mock.patch.object(controller, "owned_popen") as spawn, \
                mock.patch.object(runtime_selection, "selected", traced_selected), \
                mock.patch.object(discovery, "native_environment", return_value={}), \
                mock.patch.object(DshAdapter, "arguments", traced_arguments):
            raised = None
            try:
                DshAdapter().start(context)
            except (ValueError, OSError) as error:
                raised = error
        return paths, trace, opened, raised, spawn

    def test_a_selection_failure_keeps_both_log_descriptors_open_as_the_baseline_did(self):
        paths, trace, opened, raised, spawn = self.caller_start("selected")
        self.assertIsInstance(raised, ValueError)
        self.assertEqual(trace, ["logs", "selected"])
        self.assertTrue(Path(paths["stdout"]).exists(), "the baseline leaves both logs behind")
        self.assertTrue(Path(paths["stderr"]).exists(), "the baseline leaves both logs behind")
        still_open = 0
        for descriptor in opened:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            still_open += 1
            os.close(descriptor)  # This test's own resource finalization.
        self.assertEqual(still_open, 2, "the between step runs outside the spawn try: descriptors stay open")
        spawn.assert_not_called()

    def test_a_failure_inside_the_spawn_try_closes_the_descriptors(self):
        paths, trace, opened, raised, spawn = self.caller_start("arguments")
        self.assertIsInstance(raised, OSError)
        self.assertEqual(trace, ["logs", "selected", "arguments"])
        self.assertTrue(Path(paths["stdout"]).exists())
        self.assertTrue(Path(paths["stderr"]).exists())
        for descriptor in opened:
            with self.assertRaises(OSError, msg="the finally must close every opened descriptor"):
                os.fstat(descriptor)
        spawn.assert_not_called()


class StrictReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-read-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "stdout"

    def write(self, raw: bytes):
        self.path.write_bytes(raw)

    def test_a_complete_object_result_is_decoded_through_the_calling_decoder(self):
        self.write(b'{"status": "ok", "processState": {"shutdownConfirmed": true}}')
        self.assertEqual(read_strict_result(self.path, decode=json.loads),
                         {"status": "ok", "processState": {"shutdownConfirmed": True}})

    def test_non_object_values_and_undecodable_bytes_return_none(self):
        for raw in (b"[1, 2]", b'"text"', b"17", b"null", b"{broken", b"\xff\xfe\x00"):
            with self.subTest(raw=raw):
                self.write(raw)
                self.assertIsNone(read_strict_result(self.path, decode=json.loads))

    def test_duplicate_members_and_non_finite_numbers_are_refused(self):
        for raw in (b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}'):
            with self.subTest(raw=raw):
                self.write(raw)
                self.assertIsNone(read_strict_result(self.path, decode=strict_decode))

    def test_the_512_kib_read_cap_refuses_before_decoding(self):
        self.write(b" " * (512 * 1024 + 1))

        def explode(raw):
            raise AssertionError("an over-limit result must never be decoded")

        self.assertIsNone(read_strict_result(self.path, decode=explode))
        self.assertEqual(self.path.stat().st_size, 512 * 1024 + 1)

    def test_exactly_the_cap_is_still_read_and_decoded(self):
        payload = json.dumps({"padding": "x" * (512 * 1024 - 15)}).encode()
        self.assertEqual(len(payload), 512 * 1024)
        self.write(payload)
        self.assertEqual(read_strict_result(self.path, decode=json.loads)["padding"], payload[13:-2].decode())

    def test_a_missing_result_file_returns_none(self):
        self.assertIsNone(read_strict_result(self.path, decode=json.loads))


class PlainAndLastLineReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-plain-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "stdout"

    def test_router_read_folds_its_baseline_exceptions_to_none(self):
        for raw in (None, b"{broken", b"\xff\xfe\x00", b" " * (256 * 1024 + 1)):
            with self.subTest(raw=raw):
                if raw is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_bytes(raw)
                self.assertIsNone(read_router_result(self.path))

    def test_router_read_returns_parsed_non_objects_for_the_role_fallback(self):
        self.path.write_bytes(b"[1, 2]")
        self.assertEqual(read_router_result(self.path), [1, 2])

    def test_router_read_is_the_same_raising_read_the_retention_uses(self):
        from hey_my_buddy.errors import BoardError
        self.path.write_bytes(b'{"status": "ok"}')
        self.assertEqual(controller.read_plain_evidence(self.path), {"status": "ok"})
        linked = self.root / "linked"
        linked.symlink_to(self.root / "outside", target_is_directory=True)
        with self.assertRaises(BoardError):
            controller.read_plain_evidence(linked / "request.json")

    def test_dsh_read_takes_the_last_non_empty_line_and_keeps_failure_semantics(self):
        self.path.write_text('garbage line\n{"status": "ok", "processState": {"shutdownConfirmed": true}}\n')
        self.assertEqual(read_last_line_result(self.path),
                         {"status": "ok", "processState": {"shutdownConfirmed": True}})
        for raw in (None, b"", b"   \n  \n", b"not json", b'["array"]', b'{"unclosed": ', b"\xff\xfe\x00"):
            with self.subTest(raw=raw):
                if raw is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_bytes(raw)
                self.assertIsNone(read_last_line_result(self.path))


class StopConfirmedTests(unittest.TestCase):
    def test_both_layers_confirm_in_order(self):
        handle = FakeHandle(True)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle), True)
        self.assertEqual(handle.calls, 1)

    def test_a_missing_or_untrue_receipt_never_starts_the_outer_observation(self):
        for payload in (None, {}, {"processState": {}}, {"other": True},
                        {"processState": {"shutdownConfirmed": False}},
                        {"processState": {"shutdownConfirmed": "true"}},
                        {"processState": {"shutdownConfirmed": 1}}):
            with self.subTest(payload=payload):
                handle = FakeHandle(True)
                self.assertIs(stop_confirmed(payload, handle), False)
                self.assertEqual(handle.calls, 0, "the outer group must not even be polled")

    def test_a_confirmed_receipt_with_a_live_outer_group_stays_unconfirmed(self):
        handle = FakeHandle(False)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle), False)
        self.assertEqual(handle.calls, 1)


class RouterStopTests(unittest.TestCase):
    """The Router rule coerces the outer observation with ``is True`` as well."""

    RECEIPT = {"processState": {"shutdownConfirmed": True}}

    def test_true_outer_confirms(self):
        handle = FakeHandle(True)
        self.assertIs(router_stop_confirmed(self.RECEIPT, handle), True)
        self.assertEqual(handle.calls, 1)

    def test_non_boolean_outer_values_never_confirm(self):
        for value in (1, "unknown", None, False):
            with self.subTest(value=value):
                handle = FakeHandle(value)
                self.assertIs(router_stop_confirmed(self.RECEIPT, handle), False)
                self.assertEqual(handle.calls, 1)

    def test_a_missing_or_untrue_receipt_skips_the_outer_observation(self):
        handle = FakeHandle(True)
        self.assertIs(router_stop_confirmed({}, handle), False)
        self.assertIs(router_stop_confirmed({"processState": {"shutdownConfirmed": 1}}, handle), False)
        self.assertEqual(handle.calls, 0)


class LegacyNodeStopTests(unittest.TestCase):
    def test_a_truthy_receipt_counts_without_running_the_preflight(self):
        for receipt in (True, 1, "yes"):
            with self.subTest(receipt=receipt):
                handle = FakeHandle(True)

                def unexpected_preflight():
                    raise AssertionError("preflight must be deferred while the receipt is truthy")

                self.assertIs(legacy_node_stop_confirmed(handle, native_receipt=receipt,
                                                         preflight=unexpected_preflight), True)
                self.assertEqual(handle.calls, 1)

    def test_the_preflight_is_evaluated_only_when_the_receipt_is_falsy(self):
        calls = []

        def preflight():
            calls.append("preflight")
            return True

        handle = FakeHandle(True)
        self.assertIs(legacy_node_stop_confirmed(handle, native_receipt=None, preflight=preflight), True)
        self.assertEqual(calls, ["preflight"])
        handle = FakeHandle(False)
        self.assertIs(legacy_node_stop_confirmed(handle, native_receipt=None, preflight=preflight), False)
        self.assertEqual(calls, ["preflight", "preflight"])


class RealStopTests(unittest.TestCase):
    def test_a_terminated_real_child_confirms_only_with_its_receipt(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-stop-")
        self.addCleanup(temporary.cleanup)
        log_paths = {"stdout": str(Path(temporary.name) / "stdout"),
                     "stderr": str(Path(temporary.name) / "stderr")}
        handle = launch_controller(prepare=lambda _environment: ([sys.executable, "-c", "import time; time.sleep(30)"],
                                                    str(temporary.name), dict(os.environ)),
                                   log_paths=log_paths)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertTrue(handle.group_alive())
        self.assertFalse(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle),
                         "a live group is never stopped while it is observable")
        handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(5))
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": True}}, handle), True)
        self.assertIs(stop_confirmed({"processState": {"shutdownConfirmed": False}}, handle), False)


class CollectControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-collect-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_collection_carries_payload_exit_code_and_stop_together(self):
        stdout = self.root / "stdout"
        stdout.write_text(json.dumps({"status": "cancelled", "processState": {"shutdownConfirmed": True}}))
        handle = SimpleNamespace(log_paths={"stdout": str(stdout)}, process=FakeProcess(-15),
                                 shutdown_confirmed=lambda: True)
        collection = collect_controller(handle, read=lambda path: read_strict_result(path, decode=json.loads),
                                        stop=stop_confirmed)
        self.assertIsInstance(collection, ControllerCollection)
        self.assertEqual(collection.payload, {"status": "cancelled", "processState": {"shutdownConfirmed": True}})
        self.assertEqual(collection.exit_code, -15)
        self.assertIs(collection.stop_confirmed, True)
        self.assertEqual(list(collection.__dataclass_fields__), ["payload", "exit_code", "stop_confirmed"])

    def test_a_path_running_its_own_later_stop_rule_gets_none(self):
        stdout = self.root / "stdout"
        stdout.write_text('earlier frame\n{"status": "ok"}\n')
        handle = SimpleNamespace(log_paths={"stdout": str(stdout)}, process=FakeProcess(0),
                                 shutdown_confirmed=lambda: True)
        collection = collect_controller(handle, read=read_last_line_result)
        self.assertEqual(collection.payload, {"status": "ok"})
        self.assertEqual(collection.exit_code, 0)
        self.assertIsNone(collection.stop_confirmed)

    def test_an_unreadable_result_keeps_the_attempt_unconfirmed(self):
        handle = SimpleNamespace(log_paths={"stdout": str(self.root / "missing")},
                                 process=FakeProcess(1), shutdown_confirmed=lambda: True)
        collection = collect_controller(handle, read=lambda path: read_strict_result(path, decode=json.loads),
                                        stop=stop_confirmed)
        self.assertIsNone(collection.payload)
        self.assertIs(collection.stop_confirmed, False)
        self.assertEqual(collection.exit_code, 1)


class RouterCollectCoercionTests(unittest.TestCase):
    """The real Router collect keeps the baseline's outer ``is True`` coercion.

    The probe ``controller-first-review`` pinned exactly these inputs: an outer
    observation of 1, ``unknown`` or None must leave the attempt failed with a
    boolean False stop, never an ok with a truthy non-boolean field.
    """

    def collect_with_outer(self, value):
        temporary = tempfile.TemporaryDirectory(prefix="controller-router-")
        self.addCleanup(temporary.cleanup)
        stdout = Path(temporary.name) / "stdout"
        stdout.write_text(json.dumps({"status": "ok", "processState": {"shutdownConfirmed": True}}))
        handle = SimpleNamespace(process=SimpleNamespace(returncode=0), log_paths={"stdout": str(stdout)},
                                 shutdown_confirmed=lambda: value)
        return structured_call.collect(handle)

    def test_the_baseline_outer_coercion_table(self):
        for value, status, confirmed in ((True, "ok", True), (1, "failed", False),
                                         ("unknown", "failed", False), (None, "failed", False),
                                         (False, "failed", False)):
            with self.subTest(outer=value):
                outcome = self.collect_with_outer(value)
                self.assertEqual(outcome.status, status)
                self.assertIs(outcome.shutdown_confirmed, confirmed)

    def test_an_unreadable_router_result_stays_invalid_and_unconfirmed(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-router-bad-")
        self.addCleanup(temporary.cleanup)
        stdout = Path(temporary.name) / "stdout"
        stdout.write_bytes(b"{broken")
        handle = SimpleNamespace(process=SimpleNamespace(returncode=0), log_paths={"stdout": str(stdout)},
                                 shutdown_confirmed=lambda: True)
        outcome = structured_call.collect(handle)
        self.assertEqual(outcome.status, "failed")
        self.assertIs(outcome.shutdown_confirmed, False)
        self.assertEqual(outcome.result["code"], "invalid-native-result")


class SignalNameTests(unittest.TestCase):
    def test_only_negative_exit_codes_name_a_signal(self):
        for code, expected in ((None, None), (0, None), (15, None), (-15, "SIGTERM"), (-9, "SIGKILL"),
                               (-999, "signal-999")):
            with self.subTest(code=code):
                self.assertEqual(signal_name(code), expected)


if __name__ == "__main__":
    unittest.main()
