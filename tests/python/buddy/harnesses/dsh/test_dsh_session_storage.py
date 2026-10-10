"""DSH native session storage: where each carrier's session rollout lives.

A Worker run pins its session-record root into the task's shared native-root
``sessions`` directory through the supported per-run patch overlay, while its
``DSH_HOME``, the acp profile, the patch and the frame logs stay inside the
invocation's own private root — the directory a later attempt of the same
micro-task resumes from. The fast and discovery carriers keep the rollout
inside their own attempt-private ``DSH_HOME``. In every shape the DSH home,
credentials store and settings document keep resolving in the owning harness
and native auth is never relocated (:func:`.native_run.session_facts` reports
these storage facts; the launch-patch pins are witnessed in the native-run
tests and the role projections in the role-wiring tests). ADR-021 decision 18
removed the DSH workspace grouping feature, so no run accepts a workspace
switch.

The first class pins the submission rule of that removal in the public spec
schema. The second class covers the normalized no-deadline sentinel
(``timeoutSeconds=0``) on the registered run: it is an unbounded deadline, it
survives a real delay, and it stays cancellable through its owned group; the
retired Node carriers' equivalent witnesses are registered in the disposition
table of the step 4-C acceptance record. The third class witnesses the two
carriers' storage split on a real governed run.
"""
from __future__ import annotations

import math
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.dsh.native_run import execution_deadline, run
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.protocol.schemas import normalize_spec

from buddy.harnesses.dsh.test_native_run import NativeRunCase


class DshWorkspaceDefaultTests(unittest.TestCase):
    def test_the_removed_workspace_switch_is_rejected_on_submission(self):
        from hey_my_buddy.errors import BoardError
        with tempfile.TemporaryDirectory(prefix="buddy-dsh-spec-") as root:
            common = {"requestId": "dsh-default", "task": "bounded task", "cwd": root}
            # Every DSH run is execution-private now: the grouping switch is an
            # unknown submit field whatever adapter or value carries it.
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

    def test_a_worker_run_pins_the_task_sessions_root_and_keeps_its_home_invocation_private(self):
        from hey_my_buddy.buddy.roles import worker_services
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        request, bound, mount = self.worker_request()
        checkpoint = f"mcp__{mount.server_name}__buddy_checkpoint"
        self.extra_agent_args = ["--prompt-mode", "governed",
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
        self.assertTrue(list((self.root / "sessions").glob(f"{session}.v3.jsonl*")),
                        "the Worker rollout lives under the task's native-root sessions dir")
        self.assertTrue((self.root / "sessions" / f"{session}.fake-session.json").is_file())
        # Every per-invocation control material — the private DSH_HOME with its
        # acp profile, the launch patch, the frame log and the bridge config —
        # stays inside this invocation's root, never in the shared root.
        self.assertTrue((invocation / "dsh-home" / "profiles" / "acp" / "package.json").is_file())
        self.assertTrue((invocation / "dsh-launch-patch.json").is_file())
        self.assertTrue((invocation / "logs" / "frames.jsonl").is_file())
        self.assertTrue(Path(mount.bridge_config_path).is_file())
        # The empty directory the fixture prepared under the native root stays
        # empty: the Worker run's DSH_HOME never lives in the shared root.
        self.assertEqual(list((self.root / "dsh-home").iterdir()), [],
                         "the native root's prepared home directory stays untouched")

    def test_a_fast_run_keeps_its_rollout_inside_its_own_private_home(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "usage"]
        result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        session = result.native_identity.session_id
        self.assertTrue((self.dsh_home / "sessions" / f"{session}.v3.jsonl.zstd").is_file(),
                        "the fast carrier's rollout stays in its attempt-private DSH_HOME")
        self.assertFalse((self.root / "sessions").exists(),
                         "the fast carrier never touches the task's shared sessions root")
        self.assertEqual(result.usage.value["inputTokens"], 3150)


class NoDeadlineSentinelTests(NativeRunCase):
    """``timeoutSeconds=0`` is a real unbounded deadline on the registered run.

    The registered run proves the sentinel at its own deadline seam: the
    normalized budget becomes an infinite deadline, a real delay survives it,
    and the bounded waits inside the ACP connection never turn the infinite
    budget into an expiry.
    """

    def test_the_normalized_zero_budget_is_an_infinite_deadline(self):
        self.assertEqual(execution_deadline(0), math.inf)
        self.assertGreater(execution_deadline(3), time.monotonic())

    def test_a_zero_timeout_attempt_survives_a_real_delay_and_completes(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--delay", "session/prompt:2",
                                 "--final-answer", '{"choice":"a"}']
        result = run(self.fast_request("prompt", timeout=0),
                     observer=lambda _facts: FEEDBACK_CONTINUE, services=None,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_a_zero_timeout_attempt_stays_cancellable_through_its_owned_group(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--hang-prompt"]
        flag = threading.Event()
        result_holder: list = []

        def stop_soon():
            self.assertTrue(self.wait_for(lambda: any(
                entry.get("event") == "hanging-prompt" for entry in self.agent_log())))
            flag.set()

        thread = threading.Thread(target=stop_soon)
        thread.start()
        try:
            result_holder.append(run(self.fast_request("prompt", timeout=0, scope="write"),
                                     observer=lambda _f: FEEDBACK_CONTINUE,
                                     services=None, cancelled=flag.is_set))
        finally:
            thread.join()
        result = result_holder[0]
        self.assertEqual(result.end.status, "cancelled")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.native.group_state, "gone",
                         "the cancelled unbounded attempt's group is confirmed gone")


if __name__ == "__main__":
    unittest.main()
