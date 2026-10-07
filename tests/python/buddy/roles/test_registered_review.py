"""The shared review role against real private Python native fixtures."""
from __future__ import annotations

import json
import threading
import uuid
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request, decode_run_result
from hey_my_buddy.buddy.roles import run_controller, run_execution
from hey_my_buddy.buddy.roles.run_observers import ReviewObserver
from buddy.harnesses.claude.test_native_run import CONFIGURATION, NativeRunCase, READ_SCHEMA, STREAM_FIXTURE


class RegisteredReviewTests(NativeRunCase):
    def review(self, *, tool_budget=4):
        root = self.base / uuid.uuid4().hex
        root.mkdir(mode=0o700)
        control = {
            "operation": "review", "harness": "claude", "spec": CONFIGURATION,
            "taskId": "task", "attemptId": "attempt", "generation": 1,
            "invocationId": uuid.uuid4().hex, "privateRoot": str(root),
            "directory": str(root),
            "account": {"adapter": "claude", "source": "native", "revision": 3},
            "nativeRoot": str(root / "native"), "cwd": str(self.cwd), "timeoutSeconds": 10,
            "requestFile": str(root / "request.json"), "verdictFile": str(root / "verdict.json"),
            "canCorrect": False,
            "readOnlyRequest": {"prompt": "Choose", "outputSchema": READ_SCHEMA,
                                "budget": {"toolCalls": tool_budget}, "captureEvidence": False},
        }
        with mock.patch.dict(RUN_SEAMS, {"claude": native_run}):
            frame, code = run_controller.execute(control, threading.Event())
            result = decode_run_result(frame)
            request = decode_run_request((root / "request.json").read_bytes())
            verdict = json.loads((root / "verdict.json").read_bytes())
            payload = run_execution._review_result(result, request, verdict)
        return result, request, payload, code

    def test_a_review_uses_the_stored_request_and_result_and_explicit_posture(self):
        result, request, payload, code = self.review()
        self.assertEqual(code, 0)
        self.assertEqual(request.input_text, "Choose")
        self.assertEqual(request.tool_scope, "read")
        self.assertEqual(request.network_allowed_domains, ())
        self.assertEqual(request.additional_denied_tools, ("mcp__*", "WebFetch", "WebSearch", "Agent", "Task"))
        self.assertEqual(payload["rawAnswer"]["profileId"], "legal")
        self.assertTrue(payload["answerValid"])
        self.assertEqual(result.identity, request.identity)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_native_binding_keeps_the_frozen_account_and_paths_out_of_the_request(self):
        with mock.patch.object(native_run, "prepare_run_services", return_value=None, create=True) as bind:
            _result, request, _payload, code = self.review()
        self.assertEqual(code, 0)
        values = bind.call_args.kwargs
        self.assertEqual(set(values), {"invocation_root", "native_root", "activity_dir", "account", "tool_scope"})
        self.assertEqual(values["tool_scope"], "read")
        self.assertEqual(values["account"], {"adapter": "claude", "source": "native", "revision": 3})
        self.assertEqual(str(values["activity_dir"]), request.private_state.invocation_root)
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
