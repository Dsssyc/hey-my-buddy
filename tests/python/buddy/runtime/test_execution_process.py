"""One execution endpoint stays available across native run transitions."""
from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.runtime.execution import Execution
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import execution as launch_protocol
from hey_my_buddy.protocol.execution import read_execution_endpoint
from hey_my_buddy.protocol.process_identity import boot_id, _creation_time


class ExecutionEndpointTests(unittest.TestCase):
    def test_launch_persists_intent_and_private_startup_before_child_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-3", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-3", "attemptId": "attempt-3", "generation": 1}}
            handle = SimpleNamespace(pid=301)
            identity = {"bootId": "linux:00000000-0000-4000-8000-000000000001",
                        "pid": 301, "startedAt": 105, "processGroupId": 301, "jobName": None}

            def launched(**_kwargs):
                evidence = state / "attempts/task-3/attempt-3"
                self.assertTrue((evidence / "spawn.intent").exists())
                private = launch_protocol.execution_directory(claim, state)
                self.assertTrue((private / "execution-start.json").exists())
                return handle

            with mock.patch.object(launch_protocol, "launch_controller",
                            side_effect=launched), \
                 mock.patch.object(launch_protocol, "capture_process_identity", return_value=identity):
                launched_handle = launch_protocol.launch_execution(claim, state, "n" * 64)
            self.assertIs(launched_handle, handle)
            self.assertEqual(handle.process_identity, identity)
            self.assertEqual((state / "attempts/task-3/attempt-3/spawn.marker").is_file(), True)
            self.assertEqual(json.loads((handle.execution_directory / "execution-start.json").read_text()),
                             {"claim": claim, "stateDir": str(state), "nonce": "n" * 64,
                              "jobName": None})

    def test_marker_failure_keeps_created_handle_for_service_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-4", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-4", "attemptId": "attempt-4", "generation": 1}}
            handle = SimpleNamespace(pid=401)
            identity = {"bootId": "linux:00000000-0000-4000-8000-000000000001",
                        "pid": 401, "startedAt": 106, "processGroupId": 401, "jobName": None}
            original = launch_protocol.fsync_json

            def write(path, value):
                if path.name == "spawn.marker":
                    raise OSError("marker write failed")
                original(path, value)

            with mock.patch.object(launch_protocol, "launch_controller",
                            return_value=handle), \
                 mock.patch.object(launch_protocol, "capture_process_identity", return_value=identity), \
                 mock.patch.object(launch_protocol, "fsync_json", side_effect=write):
                with self.assertRaises(OSError) as caught:
                    launch_protocol.launch_execution(claim, state, "n" * 64)
            self.assertTrue(caught.exception.spawn_created)
            self.assertIs(caught.exception.execution_handle, handle)
            self.assertEqual(handle.process_identity, identity)
            self.assertFalse((state / "attempts/task-4/attempt-4/spawn.marker").exists())

    def test_startup_write_failure_never_creates_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-5", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-5", "attemptId": "attempt-5", "generation": 1}}
            original = launch_protocol.fsync_json

            def write(path, value):
                if path.name == "execution-start.json":
                    raise OSError("startup write failed")
                original(path, value)

            with mock.patch.object(launch_protocol, "fsync_json", side_effect=write), \
                 mock.patch.object(launch_protocol, "launch_controller") as spawn:
                with self.assertRaises(OSError) as caught:
                    launch_protocol.launch_execution(claim, state, "n" * 64)
            spawn.assert_not_called()
            self.assertFalse(caught.exception.spawn_created)
            self.assertTrue(caught.exception.spawn_intent_written)

    def test_capture_failure_retains_the_unidentified_owned_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-6", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-6", "attemptId": "attempt-6", "generation": 1}}
            handle = SimpleNamespace(pid=601)
            with mock.patch.object(launch_protocol, "launch_controller", return_value=handle), \
                 mock.patch.object(launch_protocol, "capture_process_identity",
                                   side_effect=OSError("identity capture failed")):
                with self.assertRaises(OSError) as caught:
                    launch_protocol.launch_execution(claim, state, "n" * 64)
            self.assertIs(caught.exception.execution_handle, handle)
            self.assertIsNone(handle.process_identity)
            self.assertTrue(caught.exception.spawn_created)

    def test_private_endpoint_authenticates_and_accepts_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-1", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}}
            execution = Execution(claim, state, "a" * 64, client=object())
            execution.directory.mkdir(parents=True, mode=0o700)
            process_identity = {"bootId": boot_id(), "pid": os.getpid(),
                "startedAt": _creation_time(os.getpid()), "processGroupId": os.getpid(), "jobName": None}
            execution.live.start(process_identity)
            try:
                client = read_execution_endpoint(execution.directory, execution.live.identity,
                    state_dir=state, process_identity=process_identity)
                self.assertIsNotNone(client)
                self.assertTrue(client.wait())
                response = client.stop("console", force=True)
                self.assertEqual(response, {"accepted": True})
                self.assertTrue(execution.stop_requested())
                self.assertEqual(client.observe(limit=1)["unavailable"], "source-unavailable")
                with self.assertRaises(BoardError):
                    read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity={**process_identity, "pid": 1})
                with mock.patch.object(launch_protocol, "inspect_process_identity", return_value="alive"), \
                     mock.patch.object(launch_protocol.cc, "reap_endpoint") as reap:
                    self.assertEqual(launch_protocol.cleanup_execution_endpoint(
                        execution.directory, process_identity, state_dir=state)["status"], "unverified")
                    reap.assert_not_called()
                    self.assertTrue((execution.directory / "execution-live.json").exists())
                with mock.patch.object(launch_protocol, "inspect_process_identity",
                                       return_value="identity-absent"), \
                     mock.patch.object(launch_protocol.cc, "reap_endpoint",
                                       return_value={"status": "reaped"}) as reap:
                    self.assertEqual(launch_protocol.cleanup_execution_endpoint(
                        execution.directory, process_identity, state_dir=state)["status"], "reaped")
                    reap.assert_called_once()
                    self.assertFalse((execution.directory / "execution-live.json").exists())
            finally:
                execution.live.finish()

    def test_endpoint_material_survives_native_stop_while_receipt_waits_for_service(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-2", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-2", "attemptId": "attempt-2", "generation": 1}}
            offline = threading.Event()
            online = threading.Event()

            class Client:
                def submit_result(self, *_args):
                    offline.set()
                    if not online.is_set():
                        raise BoardError("SERVICE_UNAVAILABLE", "private service is offline")

            execution = Execution(claim, state, "b" * 64, client=Client())
            execution.directory.mkdir(parents=True, mode=0o700)
            process_identity = {"bootId": boot_id(), "pid": os.getpid(),
                "startedAt": _creation_time(os.getpid()), "processGroupId": os.getpid(), "jobName": None}
            execution.live.start(process_identity)
            receipt = execution.receipt(claim, {"status": "ok", "shutdownConfirmed": True},
                                        execution.directory)
            with mock.patch.object(execution, "execute", return_value=receipt):
                thread = threading.Thread(target=execution.run, daemon=True)
                thread.start()
                try:
                    self.assertTrue(offline.wait(5))
                    self.assertTrue((execution.directory / "result-receipt.json").exists())
                    client = read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity=process_identity)
                    self.assertTrue(client.wait())
                    self.assertTrue((execution.directory / "execution-live.json").exists())
                finally:
                    online.set()
                    thread.join(10)
                    execution.live.finish()
            self.assertFalse(thread.is_alive())
            self.assertFalse((execution.directory / "execution-live.json").exists())
            self.assertTrue((execution.directory / "result-delivery.json").exists())
