"""Worker invariants that must survive the role seam (ADR-025 step 1-C).

The seam routes the runtime's prepare/start/collect/cancel through
``hey_my_buddy.buddy.roles.controller``; these tests pin the protections the
micro-task names explicitly: a spawn-marker write failure after start must
never drop the live handle or the attempt, and preparation results keep their
exact receipt mapping. Real private state, real ``command`` children, no board
daemon, no harness and no model.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.worker import TERMINATION_COMPLETED, TERMINATION_HARNESS_ERROR, Worker

#: Inherited variables that would point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


def silent(_message: str) -> None:
    """A private worker log sink; the tests assert state, not log prose."""


class MarkerWriteFailureTests(unittest.TestCase):
    """A marker write that fails after the spawn keeps the live handle supervising."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-marker-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        self.worker = Worker("w-marker", self.state, client=BoardClient(self.state, autostart=False), log=silent)

    def claim(self, attempt_id: str, task_id: str) -> dict:
        return {
            "attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "command", "cwd": str(self.work), "task": "marker invariant",
                              "timeoutSeconds": 30,
                              "argv": [sys.executable, "-c", "print('marker-ok')"]}},
        }

    def test_marker_failure_after_spawn_never_drops_the_live_handle(self):
        attempt_id, task_id = "attempt-marker", "task-marker"
        directory = self.state / "attempts" / task_id / attempt_id
        original = worker_module.fsync_json

        def failing(path, value):
            if Path(path).name == "spawn.marker":
                raise OSError("injected marker write failure")
            return original(path, value)

        with mock.patch("hey_my_buddy.buddy.runtime.worker.fsync_json", side_effect=failing):
            receipt = self.worker.execute(self.claim(attempt_id, task_id))
        report = receipt["report"]
        # The durable intent exists, the marker write failed, and the attempt
        # still ran to a completed receipt: the live handle supervised it.
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(report["result"]["finalText"], "marker-ok")
        self.assertIs(report["shutdownConfirmed"], True)

    def test_a_clean_attempt_still_writes_both_marker_files(self):
        receipt = self.worker.execute(self.claim("attempt-clean", "task-clean"))
        directory = self.state / "attempts" / "task-clean" / "attempt-clean"
        report = receipt["report"]
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertTrue((directory / "spawn.marker").is_file())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)

    def test_preparation_refusals_keep_their_exact_receipts(self):
        unavailable = self.claim("attempt-unavailable", "task-unavailable")
        unavailable["task"]["spec"]["argv"] = ["/buddy-tests/no-such-executable"]
        receipt = self.worker.execute(unavailable)
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["result"])
        self.assertIn("not an executable this worker can find", report["error"])
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)
        directory = self.state / "attempts" / "task-unavailable" / "attempt-unavailable"
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())


class PreparationBoardErrorTests(unittest.TestCase):
    """The captured prepare() BoardError keeps its object, code and retry boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-prep-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"

    def worker(self, client=None):
        return Worker("w-prep", self.state, client=client, log=silent)

    @staticmethod
    def claim(task_id: str = "task-prep") -> dict:
        return {
            "attempt": {"attemptId": "attempt-prep", "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "dsh", "cwd": "/buddy-tests/nowhere", "task": "prep",
                              "timeoutSeconds": 30}},
        }

    class FailingExecutor:
        name = "fake"

        def __init__(self, error):
            self.error = error
            self.prepared = 0

        def available(self):
            return True, None

        def prepare(self, context):
            self.prepared += 1
            raise self.error

    def test_a_harness_history_reraises_the_original_board_error_object(self):
        error = BoardError("ADAPTER_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            holder = {"harnessHistory": [{"retry": False, "harness": {"adapter": "dsh", "revision": 3}}]}
            with self.assertRaises(BoardError) as caught:
                worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                         claim["attempt"], directory, holder)
        # The very exception object the adapter raised reaches the guarded retry:
        # same identity and code, never a bare-raise RuntimeError.
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertEqual(executor.prepared, 1)

    def test_without_a_harness_history_the_refusal_becomes_its_exact_receipt(self):
        error = BoardError("ADAPTER_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            receipt = worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                               claim["attempt"], directory, {})
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "ADAPTER_UNAVAILABLE: the native binary is missing")
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)

    def test_the_guarded_retry_reruns_a_flaky_prepare_and_recovers(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome

        record = {"adapter": "dsh", "available": True, "revision": 7, "status": "ready"}

        class StubClient:
            def __init__(self):
                self.calls = []

            def call(self, method, params):
                self.calls.append((method, params))
                if method == "harness_prepare":
                    return {"harness": record}
                return {"task": {}}

            def renew(self, *args, **kwargs):
                return {}

        class Handle:
            cancel_requested = False
            pid = 4242
            pgid = None

            def wait(self, timeout=None):
                return 0

            def shutdown_confirmed(self):
                return True

        class FlakyExecutor:
            name = "fake"

            def __init__(self):
                self.prepares = 0
                self.handle = Handle()

            def available(self):
                return True, None

            def prepare(self, context):
                self.prepares += 1
                if self.prepares == 1:
                    raise BoardError("ADAPTER_UNAVAILABLE", "first attempt fails")
                return None

            def start(self, context):
                return self.handle

            def collect(self, handle, context):
                return AdapterOutcome(status="ok", result={"status": "ok", "modelStarted": True},
                                      shutdown_confirmed=True)

            def cancel(self, handle, *, grace_seconds=None):
                self.handle.cancel_requested = True

        executor = FlakyExecutor()
        client = StubClient()
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            receipt = self.worker(client).execute(self.claim("task-retry"))
        report = receipt["report"]
        # The re-raised BoardError from the first prepare is exactly what sends
        # _execute_guarded around its second harness_prepare attempt, where the
        # same executor's prepare succeeds and the attempt completes.
        self.assertEqual(executor.prepares, 2)
        prepare_calls = [params for method, params in client.calls if method == "harness_prepare"]
        self.assertEqual(len(prepare_calls), 2)
        self.assertTrue(prepare_calls[1]["commandId"].endswith("-1"))
        self.assertTrue(prepare_calls[1]["retry"] is True)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(len(report["result"]["harnessAttempts"]), 2)


class LiveActivityForwardTests(unittest.TestCase):
    """Activity forwarding through the registered live seam (ADR-025 step 2-C2).

    A registered harness's attempt activity reaches the board through its live
    channel, bound to the stored public run request's identity; the same
    published activity is never forwarded twice, a foreign endpoint is refused,
    and channel loss says nothing about the native process. An unextracted
    command has no live activity channel or file fallback.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-live-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.work = self.root / "work"
        self.work.mkdir()

    class RecordingClient:
        def __init__(self):
            self.progress_calls = []

        def progress(self, worker_id, attempt_id, generation, nonce, message=None, phase=None, data=None):
            self.progress_calls.append({"attemptId": attempt_id, "generation": generation, "data": data})
            return {}

        def call(self, method, params):
            return {"task": {}}

        def renew(self, *args, **kwargs):
            return {}

    def renewal(self, *, with_request=True, harness="zcode", task_id="task-live",
                attempt_id="attempt-live", generation=1):
        import types

        from hey_my_buddy.buddy.harnesses.run_contract import (
            FrozenJson,
            PrivateStatePaths,
            RunBudget,
            RunConfiguration,
            RunIdentity,
            RunRequest,
            encode_run_request,
        )
        worker = Worker("w-live", self.state, client=self.RecordingClient(), log=silent)
        self.addCleanup(worker.live.stop)
        claim = {"attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": generation},
                 "task": {"taskId": task_id,
                          "spec": {"adapter": harness, "cwd": str(self.work), "task": "live activity",
                                   "timeoutSeconds": 30}}}
        directory = self.state / "attempts" / task_id / attempt_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        identity = RunIdentity(task_id=task_id, attempt_id=attempt_id, generation=generation,
                               invocation_id="invocation-live", turn_id="turn-live")
        request_file = directory / "role-run-request.json"
        if with_request:
            request = RunRequest(
                identity=identity, harness=harness,
                configuration=RunConfiguration(provider="fixture-zcode", model="fixture-glm", effort="low"),
                cwd=str(self.work),
                private_state=PrivateStatePaths(invocation_root=str(directory),
                                                native_root=str(directory / "native")),
                input_text="the governed turn input", tool_scope="write",
                output_schema=FrozenJson({"type": "object"}),
                budget=RunBudget(timeout_seconds=600))
            worker_module.fsync_json(request_file, json.loads(encode_run_request(request)))
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, CTwoLiveEndpoint, LiveEndpointDescriptor
        from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
        from hey_my_buddy.protocol.contracts import HarnessRunLive
        endpoint = CTwoLiveEndpoint(identity, LiveCapabilities(inquiry_delivery="unsupported"), HarnessRunLive,
                                    instance_id="c" * 64, token="d" * 64)
        self.addCleanup(endpoint.close, reason="fixture-ended")
        self.endpoints = getattr(self, "endpoints", {})
        self.endpoints[str(directory)] = endpoint
        ready_file = directory / "live-ready.json"
        ready_file.write_text(json.dumps(LiveEndpointDescriptor(
            address="fixture-address", name="Fixture Activity", instance_id="c" * 64, host_pid=42, endpoint_credential="opaque-native-credential").to_payload()))
        control = {"operation": "worker", "harness": harness, "requestFile": str(request_file),
                   "directory": str(directory),
                   "live": {"readyFile": str(ready_file), "instanceId": "c" * 64, "token": "d" * 64}}
        handle = types.SimpleNamespace(role_run_control=control, role_run_identity=identity,
                                       pid=42, cancel_requested=False)
        import c_two as cc
        self.enterContext(mock.patch.object(cc.EndpointCredential, "from_json", return_value=types.SimpleNamespace(
            address="fixture-address", context=cc.local_endpoint_context())))
        # Readiness/held-request validation, wire admission and forwarding all
        # run. Both SDK boundaries are local; no native PID is proven here.
        def connect(*args, timeout, **kwargs):
            self.assertGreaterEqual(timeout, 0)
            return nullcontext(self.endpoints[str(directory)])
        def with_call_options(peer, *, timeout):
            self.assertIs(peer, self.endpoints[str(directory)])
            self.assertGreaterEqual(timeout, 0)
            return peer
        self.enterContext(mock.patch.object(cc, "connect", side_effect=connect))
        self.enterContext(mock.patch.object(cc, "with_call_options", side_effect=with_call_options))
        return worker_module._Renewal(worker, claim, handle, implementation=object()), directory

    def publish_activity(self, directory: Path, *, task_id="task-live", attempt_id="attempt-live",
                         generation=1, event_seq=1):
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
        from hey_my_buddy.protocol.contracts import HarnessRunLive
        from hey_my_buddy.protocol.run_identity import RunIdentity
        endpoint = self.endpoints[str(directory)]
        source_identity = RunIdentity(**{**endpoint.identity.model_dump(), "task_id": task_id,
                                         "attempt_id": attempt_id, "generation": generation})
        if source_identity != endpoint.identity:
            endpoint = CTwoLiveEndpoint(source_identity, LiveCapabilities(inquiry_delivery="unsupported"), HarnessRunLive,
                                        instance_id="c" * 64, token="d" * 64)
            self.endpoints[str(directory)] = endpoint
            self.addCleanup(endpoint.close, reason="fixture-ended")
        self.assertTrue(endpoint.publish_activity({"phase": "streaming-model", "eventSeq": event_seq,
                                                   "counts": {"modelTurns": 1, "toolCalls": 0}}))

    def test_activity_flows_through_the_channel_and_never_repeats(self):
        renewal, directory = self.renewal()
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [], "nothing is forwarded before native activity")
        self.publish_activity(directory, event_seq=3)
        renewal._forward_activity()
        calls = renewal.worker.client.progress_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["attemptId"], "attempt-live")
        self.assertEqual(calls[0]["data"]["activity"]["eventSeq"], 3)
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 1, "the same publication never repeats")
        self.publish_activity(directory, event_seq=4)
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 2)

    def test_a_foreign_attempts_endpoint_is_never_forwarded(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, attempt_id="another-attempt", event_seq=9)
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])

    def test_a_stored_request_of_another_invocation_never_binds_or_bypasses(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, event_seq=3)
        request_path = Path(renewal.handle.role_run_control["requestFile"])
        original = request_path.read_text()
        # Tamper the stored request after the handle was issued: its invocation
        # no longer matches the held identity, so no channel binds — and the
        # extracted live path has no fallback: nothing is forwarded this tick.
        request = json.loads(original)
        request["identity"]["invocationId"] = "foreign-invocation"
        request_path.write_text(json.dumps(request))
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])
        self.assertIsNone(renewal._activity_channel)
        # The binding is retried, not cached as absent: once the stored request
        # is whole again, the very next tick binds and forwards.
        request_path.write_text(original)
        renewal._forward_activity()
        calls = renewal.worker.client.progress_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["data"]["activity"]["eventSeq"], 3)

    def test_an_unextracted_command_has_no_live_activity_or_file_fallback(self):
        renewal, directory = self.renewal(with_request=False, harness="command")
        renewal.handle.role_run_control = {"operation": "worker", "harness": "command"}
        state, channel = renewal._attempt_activity_binding()
        self.assertEqual(state, "unextracted")
        self.assertIsNone(channel)
        (directory / "activity.json").write_text('{"phase":"streaming-model","eventSeq":3}')
        with mock.patch("os.open", wraps=os.open) as opened:
            renewal._forward_activity()
        opened.assert_not_called()
        self.assertEqual(renewal.worker.client.progress_calls, [])

    def test_a_channel_unavailability_is_not_a_stop(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, event_seq=3)
        # A channel whose reads fail is an availability fact, never a stop: the
        # forwarding loop swallows it, forwards nothing and keeps running.
        state, channel = renewal._attempt_activity_binding()
        self.assertEqual(state, "bound")
        with mock.patch.object(channel, "observe", side_effect=BoardError("INTERNAL", "unreadable")):
            renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])
        # The same live fact still forwards once the channel reads again.
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 1)


if __name__ == "__main__":
    unittest.main()
