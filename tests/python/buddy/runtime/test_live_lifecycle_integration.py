"""Worker process lifetime must cover consecutive directly driven attempts."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.buddy.runtime import live as runtime_live
from hey_my_buddy.protocol.contracts import HarnessRunLive


class WorkerLiveLifecycleTests(unittest.TestCase):
    def test_two_direct_attempts_use_the_same_live_runtime_until_its_owner_closes(self):
        with tempfile.TemporaryDirectory(prefix="buddy-live-lifetime-",
                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp")) as directory:
            client = mock.Mock(state_dir=Path(directory))
            attached = []
            client.live_attach.side_effect = lambda frame: attached.append(frame)
            with mock.patch.object(runtime_live.cc, "register"), \
                 mock.patch.object(runtime_live.cc, "server_address", return_value="ipc://fixture-worker"), \
                 mock.patch.object(runtime_live.cc, "unregister"), \
                 mock.patch.object(runtime_live.cc, "shutdown"), \
                 mock.patch.object(runtime_live, "WorkerLiveRuntime", wraps=runtime_live.WorkerLiveRuntime) as create_live:
                worker = Worker("lifetime-worker", Path(directory), client=client)
                runtime = worker.live
                self.assertEqual(create_live.call_args.kwargs.get("state_dir"), worker.state_dir,
                                 "Worker must explicitly supply its private state to the live runtime")
                self.assertEqual(client.state_dir, worker.state_dir,
                                 "Injected client must carry the private Worker state path")
                self.assertEqual(runtime._state_dir, worker.state_dir.resolve())
                accepted = []
                channel_roots = []
                def execute(claim, *args):
                    identity = RunIdentity(task_id=claim["task"]["taskId"],
                        attempt_id=claim["attempt"]["attemptId"], generation=1,
                        invocation_id=claim["task"]["taskId"]+"-invocation")
                    handle = SimpleNamespace(role_run_identity=identity)
                    channel = CTwoLiveChannel(identity, HarnessRunLive,
                        name="fixture-controller", address="ipc://fixture-controller",
                        instance_id="a"*64, token="b"*64, state_dir=worker.state_dir)
                    channel_roots.append(channel._state_dir)
                    runtime._resolve_channel = lambda owned: ("bound", channel)
                    accepted.append(runtime.bind(claim, handle, "c"*32))
                    return {"done": True}
                try:
                    with mock.patch.object(worker, "_execute_guarded", side_effect=execute):
                        for task_id in ("first", "second"):
                            worker.execute({"task": {"taskId": task_id, "spec": {"adapter": "command"}},
                                "attempt": {"taskId": task_id, "attemptId": task_id+"-attempt", "generation": 1}})
                    self.assertEqual(channel_roots, [worker.state_dir.resolve()] * 2,
                                     "Each live channel must use the private Worker state root")
                    self.assertEqual(accepted, [True, True])
                    self.assertEqual([frame.identity.task_id for frame in attached], ["first", "second"])
                    self.assertIs(worker.live, runtime)
                    self.assertEqual(client.live_detach.call_count, 2)
                finally:
                    runtime.stop()
