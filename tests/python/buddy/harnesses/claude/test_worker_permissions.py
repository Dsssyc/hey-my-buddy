"""Claude Worker levels use native modes, settings and denied-input facts."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from buddy.harnesses.claude.test_native_run import NativeRunCase, READ_SCHEMA, flag_value
from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.claude.config import (
    DENIED_WRITE_TOOLS, READONLY_TOOLS, WRITABLE_TOOLS, sandbox_settings,
)
from hey_my_buddy.buddy.harnesses.registry import worker_permission
from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation
from hey_my_buddy.buddy.roles.run_execution import worker_delivery
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json


class WorkerPermissionTests(NativeRunCase):
    def worker_request(self, level, **kwargs):
        permission = worker_permission("claude", level)
        return self.request(scope=permission.tool_scope, **kwargs).model_copy(
            update={"worker_permission": permission})

    def observations(self, result):
        return json.loads(Path(next(ref.location for ref in result.evidence_refs
                                    if ref.kind == "native-observations")).read_bytes())

    def test_each_level_sends_its_native_posture_and_keeps_the_actual_policy(self):
        for level, mode, tools in (("read", "default", READONLY_TOOLS),
                                   ("write", "acceptEdits", WRITABLE_TOOLS),
                                   ("full", "bypassPermissions", WRITABLE_TOOLS)):
            with self.subTest(level=level):
                result = self.execute(self.worker_request(level))
                self.assertEqual(result.end.status, "ok", result.end.message)
                argv = self.fixture_state()["argv"]
                settings = json.loads(Path(flag_value(argv, "--settings")).read_bytes())
                self.assertEqual(flag_value(argv, "--permission-mode"), mode)
                self.assertEqual(flag_value(argv, "--tools"), ",".join(tools))
                policy = result.effective_policy.tools.requested.value
                self.assertEqual(policy["tools"], list(tools))
                self.assertEqual(policy["permissionMode"], mode)
                self.assertEqual(policy["sandbox"], settings["sandbox"])
                if level == "read":
                    self.assertEqual(settings, sandbox_settings())
                    self.assertEqual(flag_value(argv, "--disallowedTools"), ",".join(DENIED_WRITE_TOOLS))
                elif level == "write":
                    self.assertEqual(settings, {"sandbox": {
                        "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
                        "allowUnsandboxedCommands": False, "excludedCommands": [],
                        "network": {"allowedDomains": ["*"], "strictAllowlist": False,
                                    "allowLocalBinding": True}}})
                else:
                    self.assertEqual(settings, {"sandbox": {"enabled": False}})
                self.assertEqual(self.observations(result)["permissionMode"], mode)
                self.assertEqual(result.native_evidence.unconfirmed, ())

    def test_known_different_mode_stops_before_processing_model_or_tool_output(self):
        self.fixture_case("permission-mode-mismatch")
        for level in ("write", "full"):
            with self.subTest(level=level):
                result = self.execute(self.worker_request(level))
                self.assertEqual(result.end.reason_code, "unsupported-capability")
                self.assertIn("cannot provide", result.end.message)
                self.assertIsNone(result.value)
                self.assertEqual(result.tool_evidence.value["events"], [])
                self.assertEqual(self.observations(result)["permissionMode"], "default")

    def test_unread_mode_is_a_fact_and_does_not_reject_the_turn(self):
        for case in ("permission-mode-unread", "permission-mode-future", "permission-mode-missing"):
            with self.subTest(case=case):
                self.fixture_case(case)
                result = self.execute(self.worker_request("full"))
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertEqual(self.observations(result)["permissionModeStatus"], "unread")
                self.assertIsNone(self.observations(result)["permissionMode"])
                self.assertEqual(result.native_evidence.unconfirmed, ("settings-readback",))

    def test_multiple_denials_share_one_bounded_input_fact(self):
        collector = native_run._TurnCollector(self.worker_request("write"), mock.Mock(),
                                              lambda _facts: native_run.RunFeedback(action="continue"))
        collector._connection = mock.Mock()
        collector._connection.observations.return_value = {"unknownEvents": 0, "unreadResponses": 0}
        for index, command in enumerate(("npm test", "node node_modules/typescript/bin/tsc --noEmit", "测" * 300)):
            collector.on_request({"request_id": str(index), "request": {
                "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": command}}})
        self.assertTrue(collector.denied_operation_summary.startswith(
            "Bash: npm test\nBash: node node_modules/typescript/bin/tsc --noEmit\nBash: "))
        self.assertLessEqual(len(collector.denied_operation_summary.encode("utf-8")), 512)
        self.assertEqual(collector._connection.send.call_count, 3)

    def test_reconstructed_continuations_apply_each_new_level_in_both_directions(self):
        previous = None
        sessions = []
        for level, mode in (("read", "default"), ("write", "acceptEdits"),
                            ("full", "bypassPermissions"), ("write", "acceptEdits"), ("read", "default")):
            continuation = RunContinuation(mode="reconstructed-new-session", previous_session_id=previous) if previous else None
            result = self.execute(self.worker_request(level, continuation=continuation))
            self.assertEqual(result.end.status, "ok", result.end.message)
            previous = result.native_identity.session_id
            sessions.append(previous)
            argv = self.fixture_state()["argv"]
            self.assertEqual(flag_value(argv, "--permission-mode"), mode)
            self.assertNotIn("--resume", argv)
            self.assertFalse(result.continuation.resumable)
        self.assertEqual(len(set(sessions)), len(sessions))

    def test_native_denials_deliver_command_or_path_without_write_content_and_are_byte_bounded(self):
        for case, expected in (("permission-command", "Bash: FOO=bar npm --prefix apps/console test"),
                                ("permission-path", "Write: /outside/denied.txt"),
                                ("permission-long-command", "Bash: echo ")):
            with self.subTest(case=case):
                self.fixture_case(case)
                result = self.execute(self.worker_request("write"))
                self.assertEqual(result.end.status, "ok", result.end.message)
                outcome, facts, _ = worker_delivery(result)
                summary = facts["deniedOperationSummary"]
                self.assertLessEqual(len(summary.encode("utf-8")), 512)
                self.assertTrue(summary.startswith(expected))
                self.assertEqual(outcome["disposition"], "attention")
                self.assertEqual(outcome["request"]["attempted"],
                                 "The owned controller denied the correlated native permission request without granting access: " + summary)
                self.assertNotIn("not an input summary", summary)

    def test_router_review_argv_and_settings_are_unchanged_and_fast_stays_unsupported(self):
        denied = ("mcp__*", "WebFetch", "WebSearch", "Agent", "Task")
        result = self.execute(self.request(scope="read", schema=READ_SCHEMA, network=(), denied=denied))
        self.assertEqual(result.end.status, "ok", result.end.message)
        argv = self.fixture_state()["argv"]
        self.assertEqual(argv[1:], ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--no-session-persistence", "--tools", ",".join(READONLY_TOOLS),
            "--permission-prompt-tool", "stdio", "--settings", flag_value(argv, "--settings"),
            "--session-id", flag_value(argv, "--session-id"), "--model", "claude-opus-5-5[1m]",
            "--permission-mode", "default", "--disallowedTools", ",".join((*DENIED_WRITE_TOOLS, *denied)),
            "--effort", "high", "--json-schema", canonical_json(READ_SCHEMA)])
        self.assertEqual(json.loads(Path(flag_value(argv, "--settings")).read_bytes()), sandbox_settings(()))
        with mock.patch.object(native_run, "_spawn_native") as spawn, self.assertRaises(BoardError) as caught:
            self.execute(self.request(scope="none", schema=READ_SCHEMA))
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        spawn.assert_not_called()
