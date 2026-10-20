"""The shared review role against real private Python native fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import uuid
from contextlib import nullcontext
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
from hey_my_buddy.buddy.roles.run_observers import ReviewObserver
from buddy.harnesses.claude.test_native_run import BUDDY, NativeRunCase, READ_SCHEMA, STREAM_FIXTURE


class RegisteredReviewTests(NativeRunCase):
    def review(self, *, tool_budget=4):
        root = self.base / uuid.uuid4().hex
        root.mkdir(mode=0o700)
        control = {
            "operation": "review", "harness": "claude", "spec": BUDDY,
            "taskId": "task", "attemptId": "attempt", "generation": 1,
            "invocationId": uuid.uuid4().hex, "privateRoot": str(root),
            "directory": str(root),
            "nativeRoot": str(root / "native"), "sessionRecordPath": str(root / "session.jsonl"),
            "cwd": str(self.cwd), "timeoutSeconds": 10,
            "executionLiveFile": str(root / "execution-live.json"),
            "live": {"instanceId": "a" * 64, "token": "b" * 64},
            "canCorrect": False,
            "readOnlyRequest": {"prompt": "Choose", "outputSchema": READ_SCHEMA,
                                "budget": {"toolCalls": tool_budget}, "captureEvidence": False},
        }
        identity = RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                               invocation_id=control["invocationId"])
        handle = run_execution.InlineRunHandle(control, identity, grace_seconds=10)
        with mock.patch.dict(RUN_SEAMS, {"claude": native_run}), \
                mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            result, request, verdict, code = inline_run.execute(
                control, handle, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            payload = run_execution._review_result(result, request, verdict)
        return result, request, payload, code

    def test_a_review_uses_the_stored_request_and_shared_read_level(self):
        result, request, payload, code = self.review()
        self.assertEqual(code, 0)
        self.assertEqual(request.input_text, structured_prompt("Choose", READ_SCHEMA))
        self.assertEqual(request.permission, "read")
        self.assertNotIn("networkAllowedDomains", request.to_payload())
        self.assertNotIn("additionalDeniedTools", request.to_payload())
        self.assertEqual(payload["rawAnswer"]["buddyId"], "legal")
        self.assertTrue(payload["answerValid"])
        self.assertEqual(result.identity, request.identity)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_native_binding_keeps_paths_out_of_the_request(self):
        with mock.patch.object(native_run, "prepare_run_services", return_value=None, create=True) as bind:
            _result, request, _payload, code = self.review()
        self.assertEqual(code, 0)
        # This fixture's service factory has no declared inputs. Private
        # roots remain held by the role; no unread factory arguments.
        bind.assert_called_once_with()
        self.assertTrue(request.private_state.invocation_root.startswith(str(self.base)))
        self.assertNotIn("account", request.to_payload())
        self.assertEqual(request.session_services, ())

    def test_a_real_tool_over_budget_is_stopped_by_the_role_and_keeps_its_fact(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("budget")
        result, _request, payload, code = self.review(tool_budget=1)
        self.assertEqual(code, 1)
        self.assertEqual(payload["code"], "readonly-budget-exhausted")
        self.assertEqual(payload["usage"]["toolCalls"], 2)
        self.assertEqual(payload["toolEvidence"]["events"][0]["toolName"], "Read")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_correction_keeps_review_budget_and_the_original_review_prompt(self):
        observer = ReviewObserver(2, READ_SCHEMA, "Choose", can_correct=True)
        tolerated = {"toolCalls": 2, "unknownEvents": {"total": 1}, "deniedInteractions": 1}
        self.assertEqual(observer.observer(tolerated).action, "continue")
        answer = observer.observer(tolerated | {"settled": True, "rawAnswer": "{"})
        self.assertEqual(answer.action, "correct")
        self.assertEqual(answer.input_text, "Choose\n\nFormat correction: answer-invalid-json. "
                         "Return exactly the supplied JSON Schema; do not repeat exploration.")
        self.assertEqual(observer.observer({"settled": True, "rawAnswer": "{"}).action, "continue")
        self.assertEqual(observer.observer({"toolCalls": 3}).action, "stop")
        self.assertEqual(observer.stop_reason, "readonly-budget-exhausted")
        no_correction = ReviewObserver(2, READ_SCHEMA, "Choose", can_correct=False)
        self.assertEqual(no_correction.observer({"settled": True, "rawAnswer": "{"}).action, "continue")
