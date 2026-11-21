"""Native snapshot contracts exercised through four offline CLI simulators."""
import json
import os
import unittest
from pathlib import Path

from buddy.harnesses.claude import test_native_run as claude
from buddy.harnesses.codex import test_native_run as codex
from buddy.harnesses.dsh import test_native_run as dsh
from buddy.harnesses.zcode import test_native_run as zcode
from buddy.harnesses.snapshot_support import assert_snapshot_run, mount_snapshot
from hey_my_buddy.buddy.roles.run_observers import (
    FastCorrection, MaintenanceCorrection, ReviewObserver, selection_validator,
)
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
from hey_my_buddy.paths import native_path
from hey_my_buddy.protocol.router_selection import answer_schema


class ClaudeSnapshotTests(claude.NativeRunCase):
    def test_snapshot_sessions_retain_signed_error_pages_and_correct_once(self):
        schema = {"type": "object", "properties": {"choice": {"type": "string"}},
                  "required": ["choice"], "additionalProperties": False}
        for scope in ("none", "read"):
            with self.subTest(scope=scope):
                self.state_path.unlink(missing_ok=True)
                self.fixture_case("snapshot")
                request = self.request(scope=scope, schema=schema, timeout=45)
                request = mount_snapshot(request.model_copy(update={
                    "identity": request.identity.model_copy(update={"turn_id": None})}))
                correction = FastCorrection(schema, request.input_text)
                result = self.execute(request, observer=correction.observer)
                assert_snapshot_run(self, request, result, rounds=2)
                argv = self.fixture_state()["argv"]
                self.assertEqual(argv[argv.index("--allowedTools") + 1], "mcp__hey_my_buddy_snapshot__*")
                self.assertEqual("--strict-mcp-config" in argv, scope == "none")
                tools = argv[argv.index("--tools") + 1]
                self.assertEqual(tools == "", scope == "none")
                self.assertNotIn("ToolSearch", tools)
                self.assertIn('{"wrong":1}', self.fixture_state()["inputs"][1])
                self.assertEqual(result.usage.value["inputTokens"], 24)


class CodexSnapshotTests(codex.SeamCase):
    def test_snapshot_rpc_id_pairs_with_native_items_across_correction_turns(self):
        codex.select_fixture(os.environ, self.base, "codex", codex.FAST_FIXTURE)
        os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / "fast-trace.json")
        request = mount_snapshot(self.request(permission="none", output_schema=codex.FAST_SCHEMA, prompt="Pick a buddy"))
        self.set_case("snapshot")
        correction = FastCorrection(codex.FAST_SCHEMA, request.input_text, session_preserved=True)
        result = codex.run(request, observer=correction.observer, services=None, cancelled=lambda: False)
        assert_snapshot_run(self, request, result, rounds=2)
        trace = json.loads(Path(os.environ["HMB_CODEX_FIXTURE_STATE"]).read_text(encoding="utf-8"))
        self.assertEqual(trace["thread"]["config"]["mcp_servers"], {"user-tool": {"enabled": False}})
        self.assertEqual(trace["thread"]["config"]["web_search"], "disabled")
        self.assertTrue(all(value is False for key, value in trace["thread"]["config"].items()
                            if key.startswith(("features.", "agents."))))
        self.assertNotIn(request.input_text, trace["turns"][1]["input"][0]["text"])


    def test_code_mode_dynamic_calls_verify_in_none_and_read_without_raw_function_frames(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence, read_tool_calls
        codex.select_fixture(os.environ, self.base, "codex", codex.FAST_FIXTURE)
        for scope in ("none", "read"):
            with self.subTest(scope=scope):
                os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / f"{scope}-trace.json")
                request = mount_snapshot(self.request(permission=scope, output_schema=codex.FAST_SCHEMA, prompt="Pick a buddy"))
                self.set_case("snapshot-exec")
                observer = FastCorrection(codex.FAST_SCHEMA, request.input_text, session_preserved=True).observer \
                    if scope == "none" else (lambda _facts: codex.FEEDBACK_CONTINUE)
                result = codex.run(request, observer=observer, services=None, cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                evidence = result.tool_evidence.value
                state = Path(request.private_state.state_dir)
                self.assertTrue(evidence["streamComplete"])
                self.assertEqual(evidence["toolCalls"], 3)
                self.assertIsNone(judge_tool_evidence(evidence, scope, state_dir=state))
                calls = list(read_tool_calls(evidence, state_dir=state))
                query_calls = [call for call in calls if call["facts"] == [{"toolName": "query_task", "category": "other"}]]
                self.assertEqual(len(query_calls), 2)
                self.assertTrue(all(call["sessionServiceVerified"] for call in query_calls))
                self.assertEqual({call["outcome"] for call in query_calls}, {"success", "failure"})
                self.assertEqual(evidence["toolSummary"], [
                    {"toolName": "exec", "calls": 1, "failed": 0, "unknown": 1},
                    {"toolName": "query_task", "calls": 2, "failed": 1, "unknown": 0},
                ])

    def test_code_mode_nested_command_stops_fast_snapshot_run_immediately(self):
        codex.select_fixture(os.environ, self.base, "codex", codex.FAST_FIXTURE)
        os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / "rogue-trace.json")
        request = mount_snapshot(self.request(permission="none", output_schema=codex.FAST_SCHEMA, prompt="Pick a buddy"))
        self.set_case("snapshot-exec-rogue")
        correction = FastCorrection(codex.FAST_SCHEMA, request.input_text, session_preserved=True)
        result = codex.run(request, observer=correction.observer, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "observer-interrupt", result.end.message)
        self.assertEqual(correction.stop_reason, "no-tool-violation")


class DshSnapshotTests(dsh.NativeRunCase):
    def test_snapshot_mount_keeps_the_final_segment_and_signed_failed_queries(self):
        for scope in ("none", "read"):
            with self.subTest(scope=scope):
                self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", "wrong",
                                         "--final-answer", '{"choice":"a"}', "--session-record", "usage"]
                request = mount_snapshot(self.fast_request("Pick a choice", scope=scope))
                correction = FastCorrection(dsh.SCHEMA, request.input_text)
                result = dsh.run(request, observer=correction.observer, services=None, cancelled=lambda: False)
                assert_snapshot_run(self, request, result, rounds=2)
                self.assertEqual(result.value.raw, '{"choice":"a"}')
                policy = result.effective_policy.tools.requested.value
                patch = native_path(Path(request.private_state.invocation_root) / "dsh-launch-patch.json")
                disabled = [row["id"] for row in json.loads(patch.read_text(encoding="utf-8")) if row.get("disabled")]
                self.assertEqual(disabled, policy["disabledRows"])
                self.assertEqual(disabled[-2:], ["mcp-resources", "plan-mode"])
                if scope == "read":
                    self.assertEqual(policy, {"permissionMode": "read-only", "disabledRows": ["mcp-resources", "plan-mode"]})


class ZcodeSnapshotTests(zcode.NativeRunCase):
    def snapshot_setup(self, scope, *, fault=""):
        os.environ["HMB_ZCODE_TEST_CASE"] = f"snapshot-{scope}{'-' + fault if fault else ''}"
        schema = answer_schema("review") if scope == "read" else zcode.SCHEMA
        prompt = structured_prompt("Choose a buddy", schema)
        request = self.fast_request(prompt).model_copy(update={"permission": scope, "output_schema": schema})
        if scope == "none":
            observer = FastCorrection(schema, prompt)
        else:
            validator = selection_validator({"routingMode": "review", "domainNodes": [], "buddies": []})
            observer = ReviewObserver(schema, prompt, can_correct=True, validator=validator)
        return mount_snapshot(request), observer

    def test_snapshot_allowlist_and_signed_failed_pages_survive_fresh_sessions(self):
        for scope in ("none", "read"):
            with self.subTest(scope=scope):
                request, observer = self.snapshot_setup(scope)
                result = zcode.run(request, observer=observer.observer, services=None, cancelled=lambda: False)
                assert_snapshot_run(self, request, result, rounds=2)
                self.assertTrue(result.tool_evidence.value["streamComplete"])
                if scope == "read":
                    self.assertIsNone(json.loads(result.value.raw)["buddyId"])
                else:
                    self.assertEqual(result.value.raw, '{"choice":"a"}')
                self.assertEqual(result.usage.value["nativeRecords"], 2)
                self.assertIsNone(result.native_evidence)
                self.assertTrue(any(ref.kind == "unsupported-native-requests" for ref in result.evidence_refs))

    def test_snapshot_observers_stop_on_the_first_unexplained_frame(self):
        for scope, code in (("none", "no-tool-violation"), ("read", "router-tools-forbidden")):
            for fault, expected_calls in (("rogue", 0), ("child", 0), ("permission", 1), ("count", 2)):
                with self.subTest(scope=scope, fault=fault):
                    request, observer = self.snapshot_setup(scope, fault=fault)
                    stopped = []

                    def observe(facts):
                        feedback = observer.observer(facts)
                        if feedback.action == "stop":
                            stopped.append(facts)
                        return feedback

                    result = zcode.run(request, observer=observe, services=None, cancelled=lambda: False)
                    self.assertEqual(result.end.reason_code, "observer-interrupt", result.end.message)
                    self.assertEqual(observer.stop_reason, code)
                    self.assertFalse(stopped[0]["settled"])
                    self.assertEqual(stopped[0]["toolMarkerFrames"], 1)
                    self.assertEqual(stopped[0]["toolCalls"], expected_calls)
                    self.assertFalse(result.tool_evidence.value["streamComplete"])



class CorrectionPolicyTests(unittest.TestCase):
    def test_protocol_20_reports_error_locations_and_refuses_hard_bounds(self):
        schema = answer_schema("review", exploration=False)
        validator = selection_validator({"routingMode": "review", "domainNodes": [], "buddies": []})
        facts = {"settled": True, "rawAnswer": '{"buddyId": null,\n"note": ]}',
                 "toolCalls": 2, "sessionToolCalls": 2}
        for preserved in (True, False):
            with self.subTest(session_preserved=preserved):
                observer = ReviewObserver(schema, "Role and frozen request", can_correct=True,
                                          validator=validator, session_preserved=preserved)
                feedback = observer.observer(facts)
                self.assertEqual(feedback.action, "correct")
                self.assertIn('"line":2', feedback.input_text)
                self.assertIn('"column":9', feedback.input_text)
                self.assertEqual("Role and frozen request" in feedback.input_text, not preserved)
                self.assertEqual(facts["rawAnswer"] in feedback.input_text, not preserved)
                self.assertEqual(observer.observer(facts).action, "continue")
                hard_observer = ReviewObserver(schema, "Role and frozen request", can_correct=True,
                                               validator=validator, session_preserved=preserved)
                hard = {**facts, "rawAnswer": '{"buddyId":"unknown","extra":1}'}
                self.assertEqual(hard_observer.observer(hard).action, "continue")
                self.assertEqual(hard_observer.correction.correction_count, 0)
                self.assertIsNone(hard_observer.stop_reason)
        observer = ReviewObserver(schema, "base", can_correct=False, validator=validator)
        for key in ("deniedInteractions", "toolMarkerFrames", "readToolViolations"):
            self.assertEqual(observer.observer({**facts, key: 1}).action, "stop")

    def test_maintenance_keeps_legacy_observations_and_bounds_previous_answer(self):
        publications = []

        def publish(raw, index, observations):
            publications.append((raw, index, observations))
            return {"errors": [{"field": "$.summaries[0]"}]}

        raw = "x" * 5000
        for preserved in (True, False):
            observer = MaintenanceCorrection({}, "Frozen maintenance request", publish,
                                             session_preserved=preserved)
            feedback = observer.observer({"settled": True, "rawAnswer": raw,
                                          "toolCalls": 1, "sessionToolCalls": 1})
            self.assertEqual(feedback.action, "correct")
            self.assertEqual(set(publications[-1][2]), {"toolCalls", "toolMarkerFrames", "deniedInteractions"})
            self.assertEqual("Frozen maintenance request" in feedback.input_text, not preserved)
            self.assertEqual("x" * 4000 in feedback.input_text, not preserved)
            self.assertNotIn(raw, feedback.input_text)
