"""End-to-end `buddy inquire` tests over the real service with a mock dsh.

The whole production chain runs here except the model: the Python CLI/transport,
the C-Two control service, the Node execution engine, `JobManager`, the real
`scripts/run.mjs` overlay, and the REAL per-run inquiry bridge plugin inside a
mock `dsh` process. No model is called and no installed dsh is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from buddy.blocking import await_run
from buddy.transport import ServiceError, call_service

SUPPORT = Path(__file__).resolve().parents[2] / "tests" / "support"
MOCK_DSH = SUPPORT / "mock-dsh-inquiry.mjs"
FAKE_RUN = SUPPORT / "fake-headless-run.mjs"


def eventually(check, description, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {description}")


class InquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-inquiry-")
        self.directory = Path(self.temp.name)
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        self.cwd = self.directory / "cwd"
        self.cwd.mkdir()
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.dsh = self.bin / "dsh"
        shutil.copy(MOCK_DSH, self.dsh)
        self.dsh.chmod(0o755)
        self.environment = patch.dict(os.environ, {
            "BUDDY_STATE_DIR": str(self.state),
            "DSH_BIN": str(self.dsh),
            "MOCK_INQUIRY_SUPPORT": str(FAKE_RUN),
            "MOCK_INQUIRY_HOLD_MS": "6000",
            "MOCK_INQUIRY_ANSWER_MS": "900",
            "MOCK_INQUIRY_ANSWER": "the answer to the operator question",
            "C2_ENV_FILE": "",
            "C2_RELAY_ANCHOR_ADDRESS": "",
        })
        self.environment.start()

    def tearDown(self):
        endpoint = self.state / "control.json"
        if endpoint.exists():
            try:
                call_service("stop", state_dir=self.state)
            except ServiceError:
                pass
            eventually(lambda: not endpoint.exists(), "the isolated service to stop")
        self.environment.stop()
        self.temp.cleanup()

    def start(self, request_id, **extra):
        params = {"requestId": request_id, "task": "mock delegated task", "cwd": str(self.cwd),
                  "timeoutSeconds": 600, "workspace": False, **extra}
        return call_service("start", params, self.state)

    def test_progress_question_answer_and_original_task_completion(self):
        run = self.start("inquiry-e2e")
        run_id = run["runId"]

        progress = eventually(
            lambda: (value if (value := call_service("inquire", {"runId": run_id}, self.state))["live"]["available"] else None),
            "the per-run bridge to bind the live agent",
        )
        self.assertEqual(progress["runId"], run_id)
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["phase"], "active")
        self.assertEqual(progress["deadline"]["timeoutSeconds"], 600)
        self.assertFalse(progress["deadline"]["expired"])
        self.assertGreater(progress["deadline"]["remainingSeconds"], 500)
        self.assertEqual(progress["deadline"]["kind"], "estimated-runner-deadline-from-record-createdAt")
        self.assertTrue(progress["deadline"]["estimated"])
        self.assertFalse(progress["deadline"]["exact"])
        self.assertIn("record createdAt", progress["deadline"]["clockOrigin"])
        self.assertEqual(progress["deadline"]["deadlineBasis"], "createdAt + timeoutSeconds")
        self.assertEqual(progress["live"]["agentStatus"], "running")
        self.assertEqual(progress["live"]["activity"][-1]["tool"], "bash")
        self.assertIn("sleep 600", progress["live"]["activity"][-1]["argumentPreview"])
        self.assertFalse(progress["live"]["limits"]["exposesModelReasoning"])
        self.assertIsNone(progress["inquiry"])
        self.assertNotIn("token", json.dumps(progress))

        question = "what is the current blocker?"
        asked = call_service("inquire", {"runId": run_id, "inquiryId": "q-1", "question": question}, self.state)
        self.assertEqual(asked["inquiry"]["inquiryId"], "q-1")
        self.assertTrue(asked["inquiry"]["recorded"])
        self.assertFalse(asked["inquiry"]["duplicate"])
        self.assertIn(asked["inquiry"]["state"], ("queued", "claimed"))
        self.assertFalse(asked["inquiry"]["answer"]["available"])
        self.assertEqual(asked["inquiry"]["correlation"], "inquiryId")
        self.assertEqual(asked["inquiry"]["questionBytes"], len(question.encode("utf-8")))

        repeated = call_service("inquire", {"runId": run_id, "inquiryId": "q-1", "question": question}, self.state)
        self.assertTrue(repeated["inquiry"]["duplicate"])

        with self.assertRaises(ServiceError) as conflict:
            call_service("inquire", {"runId": run_id, "inquiryId": "q-1", "question": "a different question"}, self.state)
        self.assertEqual(conflict.exception.code, "CONFLICT")

        answered = eventually(
            lambda: (value if (value := call_service("inquire", {"runId": run_id, "inquiryId": "q-1", "question": question}, self.state))["inquiry"]["state"] == "answered" else None),
            "the correlated answer",
        )
        self.assertEqual(answered["inquiry"]["answer"]["text"], "the answer to the operator question")
        self.assertEqual(answered["inquiry"]["answer"]["via"], "tool:buddy_inquiry_reply")
        self.assertTrue(answered["inquiry"]["claimedAt"], "the claim boundary is recorded separately")
        self.assertTrue(answered["inquiry"]["deliveredAt"], "delivery is the durable commit")
        self.assertEqual(answered["inquiry"]["answer"]["bytes"], len("the answer to the operator question".encode("utf-8")))
        self.assertFalse(answered["inquiry"]["answer"]["truncated"])
        self.assertTrue(answered["inquiry"]["answer"]["at"])

        # The original owned task finishes normally, with the same runId.
        envelope = await_run({"runId": run_id, "waitSeconds": 60}, state_dir=self.state)
        self.assertEqual(envelope["runId"], run_id)
        self.assertEqual(envelope["status"], "completed")
        self.assertTrue(envelope["ok"])
        self.assertTrue(envelope["resultDelivered"])

        # After the run ends the bridge is gone; inquiry degrades honestly and
        # still reports the durable inquiry this run already carries.
        after = call_service("inquire", {"runId": run_id, "inquiryId": "q-1", "question": question}, self.state)
        self.assertEqual(after["phase"], "terminal")
        self.assertFalse(after["live"]["available"])
        self.assertEqual(after["inquiry"]["state"], "answered")
        self.assertEqual(after["inquiry"]["answer"]["text"], "the answer to the operator question")
        self.assertEqual(after["inquiry"]["answer"]["source"], "live-bridge")

        late = call_service("inquire", {"runId": run_id, "inquiryId": "q-late", "question": "too late?"}, self.state)
        self.assertEqual(late["inquiry"]["state"], "unavailable")
        self.assertFalse(late["inquiry"]["recorded"])

    def test_answer_survives_the_end_of_the_run_through_the_bridge_journal(self):
        # The operator asks and never polls while the run is alive; the answer is
        # produced at the next boundary and must still be readable afterwards.
        os.environ["MOCK_INQUIRY_HOLD_MS"] = "3000"
        os.environ["MOCK_INQUIRY_ANSWER_MS"] = "600"
        run = self.start("inquiry-journal")
        run_id = run["runId"]
        eventually(
            lambda: call_service("inquire", {"runId": run_id}, self.state)["live"]["available"],
            "the per-run bridge to bind",
        )
        asked = call_service("inquire", {"runId": run_id, "inquiryId": "q-journal", "question": "answer before you finish"}, self.state)
        self.assertTrue(asked["inquiry"]["recorded"])

        envelope = await_run({"runId": run_id, "waitSeconds": 60}, state_dir=self.state)
        self.assertEqual(envelope["status"], "completed")

        recovered = call_service("inquire", {"runId": run_id, "inquiryId": "q-journal", "question": "answer before you finish"}, self.state)
        self.assertEqual(recovered["inquiry"]["state"], "answered")
        self.assertEqual(recovered["inquiry"]["answer"]["text"], "the answer to the operator question")
        self.assertEqual(recovered["inquiry"]["answer"]["source"], "bridge-journal")
        self.assertEqual(recovered["inquiry"]["answer"]["via"], "tool:buddy_inquiry_reply")
        self.assertEqual(recovered["journal"]["available"], True)

    def test_a_claimed_question_that_is_never_committed_is_terminal(self):
        # The mock claims the question for a proposed step but never commits it,
        # exactly like a rejected pre-step. It must never be reported delivered,
        # and the finished run must not keep reporting it as queued.
        os.environ["MOCK_INQUIRY_HOLD_MS"] = "2500"
        os.environ["MOCK_INQUIRY_ANSWER_MS"] = "1200"
        os.environ["MOCK_INQUIRY_DELIVER"] = "0"
        run = self.start("inquiry-claim-only")
        run_id = run["runId"]
        eventually(
            lambda: call_service("inquire", {"runId": run_id}, self.state)["live"]["available"],
            "the per-run bridge to bind",
        )
        asked = call_service("inquire", {"runId": run_id, "inquiryId": "q-claim", "question": "delivered?"}, self.state)
        self.assertTrue(asked["inquiry"]["recorded"])

        def claimed():
            value = call_service("inquire", {"runId": run_id, "inquiryId": "q-claim", "question": "delivered?"}, self.state)
            return value if value["inquiry"]["state"] == "claimed" else None

        merged = eventually(claimed, "the claim to be journaled and merged")
        self.assertTrue(merged["inquiry"]["claimedAt"])
        self.assertIsNone(merged["inquiry"]["deliveredAt"], "a claim is never delivery")

        envelope = await_run({"runId": run_id, "waitSeconds": 60}, state_dir=self.state)
        self.assertEqual(envelope["status"], "completed")
        after = call_service("inquire", {"runId": run_id, "inquiryId": "q-claim", "question": "delivered?"}, self.state)
        self.assertEqual(after["inquiry"]["state"], "unavailable", "a finished run must not keep a pending question")
        self.assertFalse(after["inquiry"]["answer"]["available"])

    def test_inquiry_never_mutates_the_execution_deadline_or_restarts_the_run(self):
        run = self.start("inquiry-deadline")
        run_id = run["runId"]
        before = eventually(
            lambda: (value if (value := call_service("inquire", {"runId": run_id}, self.state))["live"]["available"] else None),
            "the per-run bridge to bind",
        )
        call_service("inquire", {"runId": run_id, "inquiryId": "q-deadline", "question": "progress?"}, self.state)
        after = call_service("status", {"runId": run_id}, self.state)
        self.assertEqual(after["timeoutSeconds"], before["deadline"]["timeoutSeconds"])
        self.assertEqual(after["createdAt"], before["execution"]["createdAt"])
        self.assertNotIn("cancelRequestedAt", after)
        self.assertEqual(after["status"], "running")
        self.assertNotIn("inputHash", after, "the public view never leaks the input hash")

        completed = eventually(
            lambda: (value if (value := call_service("status", {"runId": run_id}, self.state))["status"] == "completed" else None),
            "the untouched run to complete",
            timeout=30,
        )
        self.assertEqual(completed["runId"], run_id)
        self.assertEqual(completed["resultAvailable"], True)

    def test_malformed_inquire_inputs_are_rejected_without_touching_the_run(self):
        run = self.start("inquiry-malformed")
        run_id = run["runId"]
        cases = [
            ({"runId": run_id, "question": "no id"}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "inquiryId": "q"}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "inquiryId": "bad id!", "question": "x"}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "inquiryId": "q", "question": "  "}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "timeoutMs": 99999}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "waitMs": 99999}, "INVALID_ARGUMENT"),
            ({"runId": run_id, "unknown": 1}, "INVALID_ARGUMENT"),
            ({"runId": "00000000-0000-4000-8000-000000000000"}, "NOT_FOUND"),
        ]
        for params, code in cases:
            with self.assertRaises(ServiceError) as failure:
                call_service("inquire", params, self.state)
            self.assertEqual(failure.exception.code, code, params)
        still = call_service("status", {"runId": run_id}, self.state)
        self.assertEqual(still["status"], "running")
        self.assertEqual(still["inquiries"], {})

    def test_await_keeps_waiting_while_inquiries_run_in_parallel(self):
        run = self.start("inquiry-parallel")
        run_id = run["runId"]
        eventually(
            lambda: call_service("inquire", {"runId": run_id}, self.state)["live"]["available"],
            "the per-run bridge to bind",
        )
        results = {}

        def waiter():
            results["await"] = await_run({"runId": run_id, "waitSeconds": 60}, state_dir=self.state)

        thread = threading.Thread(target=waiter, name="buddy-await")
        thread.start()
        asked = call_service("inquire", {"runId": run_id, "inquiryId": "q-parallel", "question": "still there?"}, self.state)
        self.assertTrue(asked["inquiry"]["recorded"])
        self.assertEqual(call_service("status", {"runId": run_id}, self.state)["status"], "running")
        thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "await must finish once the run completes")
        self.assertEqual(results["await"]["runId"], run_id)
        self.assertEqual(results["await"]["status"], "completed")
        self.assertTrue(results["await"]["ok"])

    def test_cli_returns_the_same_envelope_and_fails_on_bad_input(self):
        run = self.start("inquiry-cli")
        run_id = run["runId"]
        eventually(
            lambda: call_service("inquire", {"runId": run_id}, self.state)["live"]["available"],
            "the per-run bridge to bind",
        )
        env = {**os.environ}
        child = subprocess.run(
            [sys.executable, "-m", "buddy.cli", "inquire", json.dumps({"runId": run_id})],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        payload = json.loads(child.stdout)
        self.assertEqual(payload["runId"], run_id)
        self.assertEqual(payload["live"]["available"], True)

        bad = subprocess.run(
            [sys.executable, "-m", "buddy.cli", "inquire", json.dumps({"runId": run_id, "inquiryId": "x"})],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(json.loads(bad.stdout)["error"]["code"], "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
