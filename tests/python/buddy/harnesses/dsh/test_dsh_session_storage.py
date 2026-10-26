"""Task-shared Worker records and invocation-private evidence.

Native configuration stays in the user's home. Fast records keep their
attempt-private session path; the directory is not a configuration home.
"""
from __future__ import annotations

import math
from temporary import TemporaryDirectory
import threading
import time
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.dsh.native_run import execution_deadline, run
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.protocol.schemas import normalize_spec
from hey_my_buddy.paths import native_path

from buddy.harnesses.dsh.test_native_run import NativeRunCase


class DshWorkspaceDefaultTests(unittest.TestCase):
    def test_the_removed_workspace_switch_is_rejected_on_submission(self):
        from hey_my_buddy.errors import BoardError
        with TemporaryDirectory(prefix="hey-my-buddy-dsh-spec-") as root:
            common = {"requestId": "dsh-default", "task": "bounded task", "cwd": root}
            # Every DSH run is execution-private now: the grouping switch is an
            # unknown submit field whatever harness or value carries it.
            for params in ({**common, "workspace": True}, {**common, "workspace": False},
                           {**common, "adapter": "codex", "workspace": False}):
                with self.subTest(params=params):
                    with self.assertRaises(BoardError) as raised:
                        normalize_spec(params)
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                    self.assertEqual(raised.exception.details.get("field"), "workspace")
            spec = normalize_spec({**common, "adapter": "dsh"})
            self.assertNotIn("workspace", spec)


class StorageSplitTests(NativeRunCase):
    """A Worker run's rollout is task-shared; its control materials are not."""

    def test_a_worker_run_pins_task_sessions_and_keeps_controls_invocation_private(self):
        from hey_my_buddy.buddy.roles import worker_services
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        request, bound, mount = self.worker_request()
        checkpoint = f"mcp__{mount.server_name}__hey_my_buddy_checkpoint"
        self.extra_agent_args = ["--prompt-mode", "micro-task",
                                 "--bridge-config", mount.bridge_config_path,
                                 "--finish-tool", mount.finish_tool,
                                 "--checkpoint-tool", checkpoint,
                                 "--session-record", "usage"]
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        invocation = Path(request.private_state.invocation_root)
        session = result.native_identity.session_id
        # The rollout is in the task's shared native root; the marker the next
        # attempt's agent resumes from sits beside it.
        self.assertTrue(list(native_path(self.root / "sessions").glob(f"{session}.v3.jsonl*")),
                        "the Worker rollout lives under the task's native-root sessions dir")
        self.assertTrue((self.root / "sessions" / f"{session}.fake-session.json").is_file())
        self.assertFalse(native_path(invocation / "dsh-home").exists())
        self.assertTrue(native_path(invocation / "dsh-launch-patch.json").is_file())
        self.assertTrue(native_path(Path(mount.bridge_config_path)).is_file())
        self.assertFalse((self.root / "dsh-home").exists())

    def test_a_fast_run_keeps_its_original_attempt_private_session_path(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "usage"]
        result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        session = result.native_identity.session_id
        self.assertTrue((self.sessions_root / f"{session}.v3.jsonl.zstd").is_file(),
                        "the fast carrier retains its attempt-private session path")
        self.assertFalse((self.root / "sessions").exists(),
                         "the fast carrier never touches the task's shared sessions root")
        self.assertEqual(result.usage.value["inputTokens"], 3150)


class UnboundedWorkerTests(NativeRunCase):
    """An ordinary Worker run has no hard budget but remains cancellable."""

    def test_an_absent_budget_is_an_infinite_deadline(self):
        self.assertEqual(execution_deadline(None), math.inf)
        self.assertGreater(execution_deadline(3), time.monotonic())

    def test_an_unbounded_worker_survives_a_real_delay_and_completes(self):
        request, bound, mount = self.worker_request(timeout=None)
        self.micro_task_agent_args(mount)
        self.extra_agent_args += ["--delay", "session/prompt:2"]
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_an_unbounded_worker_stays_cancellable_through_its_owned_group(self):
        request, bound, mount = self.worker_request(timeout=None)
        self.micro_task_agent_args(mount)
        self.extra_agent_args.append("--hang-prompt")
        flag = threading.Event()
        result_holder: list = []

        def stop_soon():
            self.assertTrue(self.wait_for(lambda: any(
                entry.get("event") == "hanging-prompt" for entry in self.agent_log())))
            flag.set()

        thread = threading.Thread(target=stop_soon)
        thread.start()
        try:
            result_holder.append(run(request,
                                     observer=lambda _f: FEEDBACK_CONTINUE,
                                     services=bound.services, cancelled=flag.is_set))
        finally:
            thread.join()
        result = result_holder[0]
        self.assertEqual(result.end.status, "cancelled")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.native.group_state, "gone",
                         "the cancelled unbounded attempt's group is confirmed gone")


if __name__ == "__main__":
    unittest.main()
