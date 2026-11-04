"""Initial-input checks at the execution seam, with no native/model calls."""
from contextlib import nullcontext
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.roles import inline_run, prompts, run_execution, turn_io
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
from hey_my_buddy.install.runtime import resource_path
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.capabilities import InputLimit
from hey_my_buddy.protocol.input_failure import INPUT_REJECTED
from hey_my_buddy.protocol.input_limit import measure_prompt
from buddy.roles.test_role_prompts import real_turn_input, prompt_input


def known(value):
    return InputLimit.known(form="cli-app-server", scope="turn-start-text", source="synthetic",
                            value=value, unit="unicode-scalars", versions=("0.164.0",))


class PartitionTests(unittest.TestCase):
    def test_source_partitions_preserve_canonical_json_and_count_host_input_once(self):
        host = '中文😀e\u0301\\"\n\x00'
        turn = real_turn_input()
        turn["context"].update(delegationAuthority=True,
            continuation={"kind": "host", "input": host, "note": "备注😀", "trigger": None},
            hostDecision={"requestId": "request", "decision": "continue", "note": host})
        prompt = prompts.worker_prompt(host, turn, delivery=(host, ""), credential_file='/private/证据".json')
        expected = (resource_path("roles.worker").read_text(encoding="utf-8") + "\n"
                    + resource_path("roles.lead").read_text(encoding="utf-8") + "\n\n"
                    + "Credential file path: " + canonical_json('/private/证据".json')
                    + "\n\n" + host + "\n\n" + "" + "\n\n" + host
                    + "\n\n" + canonical_json(prompt_input(turn)))
        self.assertEqual(prompt, expected)
        self.assertEqual(prompt.parts.task, "\n\n" + host)
        self.assertEqual(prompt.parts.host_input,
            '"input":' + canonical_json(host) + ',"note":' + canonical_json("备注😀")
            + ',"hostDecision":' + canonical_json(turn["context"]["hostDecision"]))
        self.assertIn(resource_path("roles.lead").read_text(encoding="utf-8"), prompt.parts.role)
        self.assertIn(canonical_json('/private/证据".json'), prompt.parts.delivery)
        self.assertNotIn('"input":', prompt.parts.context)
        sizes = measure_prompt(prompt.parts)
        self.assertEqual(sizes.total.unicode_scalars, len(prompt))
        self.assertEqual(sizes.total.utf8_bytes, len(prompt.encode()))
        self.assertEqual(sum(sizes.part_sizes("unicode-scalars").values()), len(prompt))
        self.assertEqual(sum(sizes.part_sizes("utf-8-bytes").values()), len(prompt.encode()))

    def test_router_role_and_schema_have_their_own_sources(self):
        schema = {"type": "object", "description": '中文😀\\"\n'}
        prompt = structured_prompt(prompts.router_prompt("Choose 中文😀"), schema)
        self.assertEqual(prompt.parts.role, resource_path("roles.router").read_text(encoding="utf-8") + "\n")
        self.assertEqual(prompt.parts.task, "Choose 中文😀")
        self.assertEqual(prompt.parts.format,
                         "\n\nReturn only one JSON value matching this schema: " + canonical_json(schema))
        self.assertEqual(measure_prompt(prompt.parts).total.unicode_scalars, len(prompt))

    def test_an_inquiry_answer_uses_its_existing_bound_without_initial_prompt_preflight(self):
        with tempfile.TemporaryDirectory(prefix="inquiry-input-") as directory:
            root = Path(directory)
            identity = {"taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn"}
            journal = root / "inquiries.jsonl"
            journal.write_text(canonical_json({"version": 1, "inquiryId": "question", "state": "queued",
                "question": "问题😀", "questionSha256": hashlib.sha256("问题😀".encode()).hexdigest(),
                "askedAt": "2026-01-01T00:00:00Z", **identity}) + "\n", encoding="utf-8")
            config = {"identity": identity, "inputSha256": "a" * 64, "key": "b" * 64,
                      "attentionPath": str(root / "attention.json"), "inquiryJournalPath": str(journal)}
            with mock.patch.object(inline_run, "check_prompt_input", side_effect=AssertionError("unexpected preflight")), \
                 mock.patch("hey_my_buddy.protocol.input_limit.check_prompt_input", side_effect=AssertionError("unexpected preflight")):
                result = worker_services.call_session_tool("hey_my_buddy_answer_inquiry",
                    {"inquiryId": "question", "answer": "已核对😀"}, config)
            self.assertFalse(result.get("isError"), result)
            self.assertIn("inquiry-answer", result["content"][0]["text"])


class InitialCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="input-preflight-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module = SimpleNamespace(prepare_run_services=lambda **_kwargs: None)
        self.turn = real_turn_input()
        self.control = {"operation": "worker", "harness": "codex", "privateRoot": str(self.root),
            "nativeRoot": str(self.root / "native"), "stateDir": str(self.root),
            "sessionRecordPath": str(self.root / "session.jsonl"), "directory": str(self.root),
            "cwd": str(self.root), "invocationId": "invocation", "timeoutSeconds": None,
            "inputFile": str(self.root / "input.json"), "taskFile": str(self.root / "task.txt"),
            "outputFile": str(self.root / "output.json"), "credentialFile": str(self.root / "credential.json"),
            "spec": {"provider": "fixture", "model": "fixture", "effort": "low"}}
        (self.root / "task.txt").write_text('任务 中文😀\\"\n', encoding="utf-8")
        self.freeze_turn()

    def freeze_turn(self):
        manifest = self.turn["executionWorkspace"]
        manifest["workspaceId"] = "ws-" + "a" * 32
        manifest["manifestSha256"] = hashlib.sha256(canonical_json(
            {key: value for key, value in manifest.items() if key != "manifestSha256"}).encode()).hexdigest()
        path = self.root / "workspaces" / manifest["workspaceId"]
        path.mkdir(parents=True, exist_ok=True)
        turn_io.private_json(path / "manifest.json", manifest)
        frozen = {**self.turn, "executionWorkspace": {key: manifest[key] for key in ("workspaceId", "manifestSha256")}}
        turn_io.private_json(self.root / "input.json", frozen)

    def request(self):
        if self.control["operation"] == "worker":
            return run_execution.worker_request(self.control, self.module)[0]
        if self.control["operation"] == "review":
            return run_execution.review_request(self.control, self.module)[0]
        return run_execution.fast_request(self.control, self.module)[0]

    def execute(self, limit, *, admission=None, stopped=True, native_version="0.164.0", native_call=None):
        request = self.request()
        native_result = rc.RunResult(identity=request.identity, harness=request.harness,
            end=rc.RunEnd(status="error", reason_code="native-rpc-error", message="native refused the input"),
            harness_version=native_version, native_request_sent=True, model_started=False,
            native_admission_error=admission,
            stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone" if stopped else "unknown")))
        handle = run_execution.InlineRunHandle(self.control, request.identity, grace_seconds=0)
        select_limit = inline_run.select_input_limit
        with mock.patch.object(inline_run, "run_seam", return_value=self.module), \
             mock.patch.object(inline_run, "selected", return_value={"version": "0.164.0"}), \
             mock.patch.object(inline_run, "select_input_limit", side_effect=lambda harness, version:
                               limit if version == "0.164.0" else select_limit(harness, version)), \
             mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)) as live, \
             mock.patch.object(inline_run, "run_harness", return_value=native_result, side_effect=native_call) as native:
            result, used_request, verdict, code = inline_run.execute(self.control, handle)
        return result, used_request, verdict, code, native.call_count, live.call_count

    def test_equal_limit_allows_one_more_scalar_refuses_before_native_for_worker_lead_and_resume(self):
        for authority, mode in ((False, "initial"), (True, "initial"), (False, "native-session")):
            with self.subTest(authority=authority, mode=mode):
                self.turn["context"]["delegationAuthority"] = authority
                self.turn.update(resumeMode=mode, previousSessionId="previous" if mode == "native-session" else None)
                self.freeze_turn()
                total = len(self.request().input_text)
                allowed = self.execute(known(total))
                self.assertEqual(allowed[4:], (1, 1))
                result, _request, _verdict, code, calls, live_calls = self.execute(known(total - 1))
                self.assertEqual((calls, live_calls, code), (0, 0, 1))
                self.assertEqual(result.end.reason_code, INPUT_REJECTED)
                self.assertFalse(result.native_request_sent)
                self.assertFalse(result.model_started)
                self.assertIsNone(result.native_identity)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                failure = result.input_failure.value
                self.assertEqual(failure["stage"], "preflight")
                self.assertEqual(failure["measurement"]["total"], total)
                self.assertEqual(sum(failure["measurement"]["parts"].values()), total)
                self.assertFalse((self.root / "output.json").exists())

    def test_unknown_starts_and_native_rejection_has_both_diagnostics_without_stop_inference(self):
        limit = InputLimit.unknown(form="cli-app-server", scope="turn-start-text", source="synthetic")
        native_error = {"nativeMethod": "turn/start", "nativeCode": -32602, "input_error_code": "input_too_large",
                        "max_chars": 10, "actual_chars": 11, "message": "native says no"}
        result, request, _verdict, code, calls, _live = self.execute(limit, admission=native_error, stopped=False)
        self.assertEqual((calls, code), (1, 1))
        self.assertEqual(result.end.reason_code, INPUT_REJECTED)
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        failure = run_execution._base_result(result)["inputFailure"]
        self.assertEqual(failure["stage"], "native-admission")
        self.assertEqual(failure["limit"]["state"], "unknown")
        self.assertNotIn("value", failure["limit"])
        self.assertEqual(failure["measurement"]["total"], len(request.input_text))
        self.assertEqual(failure["measurement"]["utf8Bytes"], len(request.input_text.encode()))
        self.assertEqual(sum(failure["measurement"]["utf8Parts"].values()), len(request.input_text.encode()))
        self.assertTrue(failure["nativeRequestSent"])
        self.assertFalse(failure["modelStarted"])
        for key, value in native_error.items():
            self.assertEqual(failure[key], value)

    def test_large_in_message_is_only_an_existing_harness_error_with_diagnostics(self):
        limit = InputLimit.unknown(form="cli-app-server", scope="turn-start-text", source="synthetic")
        result, request, *_ = self.execute(limit, admission={"nativeMethod": "turn/start", "nativeCode": -32602,
                                                           "message": "a large problem"})
        self.assertEqual(result.end.reason_code, "native-rpc-error")
        self.assertEqual(result.input_failure.value["measurement"]["total"], len(request.input_text))

    def test_native_version_outside_the_verified_range_does_not_claim_a_known_limit(self):
        result, request, *_ = self.execute(known(100_000), native_version="0.165.0",
            admission={"nativeMethod": "turn/start", "nativeCode": "input_too_large"})
        facts = result.input_failure.value
        self.assertEqual(facts["harness"]["version"], "0.165.0")
        self.assertEqual(facts["limit"]["state"], "unknown")
        self.assertEqual(facts["measurement"]["total"], len(request.input_text))

    def test_correction_is_not_preflighted_again_even_when_it_is_longer_than_the_initial_limit(self):
        self.control.update(operation="fast", taskId="task", attemptId="attempt", generation=1,
            noToolRequest={"prompt": prompts.router_prompt("Choose"), "outputSchema": {"type": "object"}})
        initial_size = len(self.request().input_text)
        sent = []
        def native(_module, request, *, observer, **_kwargs):
            sent.append(request.input_text)
            feedback = observer({"settled": True, "rawAnswer": "{"})
            self.assertEqual(feedback.action, "correct")
            sent.append(feedback.input_text)
            return rc.RunResult(identity=request.identity, harness=request.harness,
                end=rc.RunEnd(status="error", reason_code="fixture-end"),
                native_request_sent=True, model_started=True,
                stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
        with mock.patch.object(inline_run, "check_prompt_input", wraps=inline_run.check_prompt_input) as check:
            result, *_ = self.execute(known(initial_size), native_call=native)
        self.assertEqual(result.end.reason_code, "fixture-end")
        self.assertEqual(check.call_count, 1)
        self.assertGreater(len(sent[1]), initial_size)

    def test_router_fast_review_and_maintenance_keep_their_failure_result_paths(self):
        for operation, maintenance in (("fast", False), ("review", False), ("fast", True)):
            with self.subTest(operation=operation, maintenance=maintenance):
                self.control.update(operation=operation, maintenance=maintenance,
                    taskId="task", attemptId="attempt", generation=1, canCorrect=False,
                    noToolRequest={"prompt": prompts.router_prompt("Choose"), "outputSchema": {"type": "object"}},
                    readOnlyRequest={"prompt": prompts.router_prompt("Choose"), "outputSchema": {"type": "object"},
                                     "budget": {"toolCalls": 0}})
                result, request, verdict, code, calls, _live = self.execute(known(1))
                self.assertEqual((calls, code), (0, 1))
                project = run_execution._review_result if operation == "review" else run_execution._fast_result
                payload = project(result, request, verdict)
                self.assertEqual(payload["code"], INPUT_REJECTED)
                self.assertNotIn("turn", payload)
                self.assertIsNone(verdict["stopReason"])


if __name__ == "__main__":
    unittest.main()
