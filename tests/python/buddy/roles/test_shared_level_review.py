"""DSH and ZCode Router review through the shared role and native run seams.

Both native processes are private offline fixtures. The Router receives a
structured answer through its final-message carrier and verifies its frozen
input only after the native process has stopped.
"""
from __future__ import annotations

import os
import json
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
import uuid

from buddy.harnesses.dsh.test_native_run import NativeRunCase as DshNativeCase
from buddy.harnesses.zcode.test_native_run import NativeRunCase as ZcodeNativeCase
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.buddy.roles.router import DecisionAdapter
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence


ANSWER = '{"buddyId":"legal","note":"fixture","evidence":[]}'
SCHEMA = router.answer_schema(["legal"], "review")


class _ReviewCases:
    harness: str

    def prepare_fixture(self, *, correction=False):
        raise NotImplementedError

    def native_prompts(self):
        raise NotImplementedError

    def review_context(self, document: dict) -> ExecutionContext:
        cwd = self.base / "cwd" if self.harness == "dsh" else self.cwd
        return ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"provider": self.provider, "model": self.model,
                  "effort": self.effort, "cwd": str(cwd), "routerBudgetSeconds": 10},
            directory=self.base / ("router-attempt-" + uuid.uuid4().hex),
            runtime={}, environment=dict(os.environ),
            decision_input=document,
            session_record_path=str(self.base / ("session-" + uuid.uuid4().hex + ".jsonl")))

    def document(self) -> dict:
        return {"routingMode": "review", "task": "Choose the legal buddy",
                "buddy": {"harness": self.harness},
                "buddies": [{"buddyId": "legal"}], "outputSchema": SCHEMA,
                "budget": {"timeoutSeconds": 10, "toolCalls": 0}}

    def test_inline_review_returns_a_structured_answer_under_read(self):
        self.prepare_fixture()
        root = self.base / ("inline-review-" + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        cwd = self.base / "cwd" if self.harness == "dsh" else self.cwd
        control = {
            "operation": "review", "harness": self.harness,
            "spec": {"provider": self.provider, "model": self.model, "effort": self.effort},
            "taskId": "task", "attemptId": "attempt", "generation": 1,
            "invocationId": uuid.uuid4().hex, "privateRoot": str(root),
            "stateDir": str(self.base / "state"),
            "directory": str(root), "nativeRoot": str(root / "native"),
            "sessionRecordPath": str(root / "session.jsonl"), "cwd": str(cwd),
            "timeoutSeconds": 10, "canCorrect": False,
            "readOnlyRequest": {"prompt": "Choose the legal buddy",
                                "outputSchema": SCHEMA, "budget": {"toolCalls": 0},
                                "captureEvidence": False},
        }
        identity = RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                               invocation_id=control["invocationId"])
        handle = run_execution.InlineRunHandle(control, identity, grace_seconds=10)
        with mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            result, request, verdict, code = inline_run.execute(control, handle)
        payload = run_execution._review_result(result, request, verdict)
        self.assertEqual(code, 0)
        self.assertEqual(request.permission, "read")
        self.assertEqual(request.input_text, structured_prompt("Choose the legal buddy", SCHEMA))
        self.assertEqual(self.native_prompts(), [request.input_text])
        self.assertEqual(json.loads(payload["rawAnswer"]),
                         {"buddyId": "legal", "note": "fixture", "evidence": []})
        self.assertEqual(payload["usage"]["toolCalls"], 0)
        self.assertLessEqual(payload["usage"]["toolCalls"], control["readOnlyRequest"]["budget"]["toolCalls"])
        self.assertIsNone(judge_tool_evidence(payload["toolEvidence"], "read",
                                               state_dir=Path(control["stateDir"])))
        self.assertEqual(result.effective_policy.tools.requested.value, self.native_policy)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_review_schema_reaches_the_native_initial_and_correction_prompts(self):
        self.prepare_fixture(correction=True)
        context = self.review_context(self.document())
        adapter = DecisionAdapter()
        with mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            adapter.prepare(context)
            handle = adapter.start(context)
            self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if not handle.finished else None)
            self.assertEqual(handle.wait(20), 0, handle.error or handle.result.end)
            outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertEqual(outcome.result["correctionCount"], 1)
        prompts = self.native_prompts()
        self.assertEqual(len(prompts), 2)
        marker = "Return only one JSON value matching this schema: "
        for prompt in prompts:
            supplied = prompt.split(marker, 1)[1].split("\n\nFormat correction:", 1)[0]
            self.assertEqual(json.loads(supplied), SCHEMA)
        self.assertTrue(prompts[1].startswith(prompts[0] + "\n\nFormat correction:"))

    def test_router_accepts_an_unchanged_frozen_copy_within_budget(self):
        self.prepare_fixture()
        document = self.document()
        context = self.review_context(document)
        adapter = DecisionAdapter()
        with mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            adapter.prepare(context)
            handle = adapter.start(context)
            self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if not handle.finished else None)
            self.assertEqual(handle.wait(20), 0, handle.error or handle.result.end)
            frozen = handle.router_input[1]
            outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertEqual(outcome.result["decision"],
                         {"buddyId": "legal", "note": "fixture", "evidence": []})
        self.assertTrue(outcome.result["inputVerification"]["unchanged"])
        self.assertLessEqual(outcome.result["usage"]["toolCalls"], document["budget"]["toolCalls"])
        self.assertIsNone(judge_tool_evidence(outcome.result["toolEvidence"], "read",
                                               state_dir=Path(context.environment["HMB_STATE_DIR"])))
        self.assertFalse(frozen.exists(), "a verified private copy is discarded")

    def test_router_rejects_a_changed_frozen_copy_after_native_stop(self):
        self.prepare_fixture()
        document = self.document()
        context = self.review_context(document)
        adapter = DecisionAdapter()
        with mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            adapter.prepare(context)
            handle = adapter.start(context)
            self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if not handle.finished else None)
            self.assertEqual(handle.wait(20), 0, handle.error or handle.result.end)
            self.assertEqual(handle.request.permission, "read")
            self.assertTrue(handle.shutdown_confirmed())
            frozen = handle.router_input[1]
            (frozen / "changed.txt").write_text("tampered after the native turn", encoding="utf-8", newline="\n")
            outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "router-input-changed")
        self.assertEqual(outcome.result["inputVerification"]["unchanged"], False)
        self.assertEqual(outcome.result["stopEvidence"]["native"]["shutdownConfirmed"], True)
        self.assertTrue(frozen.exists(), "a changed copy stays available for inspection")


class DshSharedLevelReviewTests(_ReviewCases, DshNativeCase):
    harness = "dsh"
    provider, model, effort = "fake", "m1", "high"
    native_policy = {"permissionMode": "read-only"}

    def prepare_fixture(self, *, correction=False):
        self.extra_agent_args = ["--prompt-mode", "final"]
        if correction:
            self.extra_agent_args += ["--final-answer", "not json"]
        self.extra_agent_args += ["--final-answer", ANSWER]
        os.environ["HMB_STATE_DIR"] = str(self.base / "state")

    def native_prompts(self):
        return ["".join(part["text"] for part in entry["raw"]["params"]["prompt"])
                for entry in self.agent_log()
                if entry.get("dir") == "in" and entry["raw"].get("method") == "session/prompt"]


class ZcodeSharedLevelReviewTests(_ReviewCases, ZcodeNativeCase):
    harness = "zcode"
    provider, model, effort = "fixture-api", "fixture-model", "low"
    native_policy = {"mode": "plan"}

    def prepare_fixture(self, *, correction=False):
        # This copy of the offline fake app server gives the Router's own
        # schema-shaped answer while retaining its native plan-mode assertions.
        source = self.fake.read_text(encoding="utf-8")
        source = source.replace('{"choice":"a"}', ANSWER)
        source = source.replace("elif case in ('write-final', 'write-post-close'):",
                                "elif case in ('write-final', 'write-post-close', 'correct'):")
        source = source.replace("if method == 'session/send':\n        prompt_sent = True\n",
                                "if method == 'session/send':\n"
                                "        with Path(__file__).with_name('review-prompts.jsonl').open('a', encoding='utf-8', newline='\\n') as capture:\n"
                                "            capture.write(json.dumps(p['content']) + '\\n')\n"
                                "        prompt_sent = True\n")
        self.fake.write_text(source, encoding="utf-8", newline="\n")
        os.environ["HMB_ZCODE_TEST_CASE"] = "correct" if correction else "write-final"

    def native_prompts(self):
        return [json.loads(line) for line in self.fake.with_name("review-prompts.jsonl").read_text(encoding="utf-8").splitlines()]
