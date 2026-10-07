"""DSH native session storage: the session rollout stays execution-private.

The ACP run pins the session-record root into the attempt's private ``DSH_HOME``
through the supported per-run patch overlay, so the DSH home, credentials store
and settings document keep resolving in the owning harness and native auth is
never relocated (:func:`.native_run.session_facts` reports these storage facts;
the launch-patch pins are witnessed in the native-run tests and the role
projections in the role-wiring tests). ADR-021 decision 18 removed the DSH
workspace grouping feature, so no run accepts a workspace switch.

The first class pins the submission rule of that removal in the public spec
schema. The second class covers the normalized no-deadline sentinel
(``timeoutSeconds=0``) on the registered run: it is an unbounded deadline, it
survives a real delay, and it stays cancellable through its owned group; the
retired Node carriers' equivalent witnesses are registered in the disposition
table of the step 4-C acceptance record.
"""
from __future__ import annotations

import math
import tempfile
import threading
import time
import unittest

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
