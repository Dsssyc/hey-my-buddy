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


    def test_snapshot_tools_also_mount_in_read(self):
        codex.select_fixture(os.environ, self.base, "codex", codex.FAST_FIXTURE)
        os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / "read-trace.json")
        request = mount_snapshot(self.request(permission="read", output_schema=codex.FAST_SCHEMA, prompt="Pick a buddy"))
        self.set_case("snapshot")
        result = codex.run(request, observer=lambda _facts: codex.FEEDBACK_CONTINUE,
                           services=None, cancelled=lambda: False)
        assert_snapshot_run(self, request, result, rounds=1)


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
                if scope == "none":
                    self.assertIn("mcp-resources", result.effective_policy.tools.requested.value["disabledRows"])


class ZcodeSnapshotTests(zcode.NativeRunCase):
    def test_snapshot_allowlist_and_signed_failed_pages_survive_fresh_sessions(self):
        for scope in ("none", "read"):
            with self.subTest(scope=scope):
                os.environ["HMB_ZCODE_TEST_CASE"] = f"snapshot-{scope}"
                prompt = structured_prompt("Choose a buddy", zcode.SCHEMA)
                request = mount_snapshot(self.fast_request(prompt).model_copy(update={"permission": scope}))
                correction = FastCorrection(zcode.SCHEMA, prompt)
                result = zcode.run(request, observer=correction.observer, services=None, cancelled=lambda: False)
                assert_snapshot_run(self, request, result, rounds=2)
                self.assertEqual(result.value.raw, '{"choice":"a"}')
                self.assertEqual(result.usage.value["nativeRecords"], 2)



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
                hard = {**facts, "rawAnswer": '{"buddyId":"unknown","extra":1}'}
                self.assertEqual(observer.observer(hard).action, "stop")
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
