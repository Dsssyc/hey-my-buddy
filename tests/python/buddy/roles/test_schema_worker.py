"""The schema Worker uses one role policy and real native fixture deliveries."""
from __future__ import annotations

import os
from pathlib import Path
import hashlib
import json
import threading
import tempfile
import uuid
from contextlib import nullcontext
import unittest
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, adapter, worker_format, worker_permission
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.harnesses.run_contract import SessionService
from hey_my_buddy.buddy.roles import inline_run, run_controller, run_execution
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.errors import BoardError
from buddy.harnesses.claude.test_native_run import BUDDY, NativeRunCase


class CodexSchemaTests(unittest.TestCase):
    def test_native_result_schema_offers_waiting_only_for_lead(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                ordinary = worker_format(harness)
                lead = ordinary.for_turn({"context": {"delegationAuthority": True}})
                ordinary_choices = ordinary.schema["properties"]["outcome"]["anyOf"][0]["properties"]["disposition"]["enum"]
                lead_choices = lead.schema["properties"]["outcome"]["anyOf"][0]["properties"]["disposition"]["enum"]
                self.assertNotIn("waiting", ordinary_choices)
                self.assertIn("waiting", lead_choices)
                self.assertNotIn("waiting", ordinary.schema["properties"]["outcome"]["anyOf"][0]["properties"]["disposition"]["enum"])

    def test_worker_permission_capabilities_match_role_paths(self):
        expected = {
            "codex": ("codex", "workspace", "cancel", "artifacts", "deadline", "native-session",
                      "permission:read", "permission:write", "permission:full"),
            "claude": ("claude", "workspace", "cancel", "artifacts", "deadline", "permission:read", "permission:write", "permission:full"),
            "dsh": ("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session",
                    "permission:read", "permission:write", "permission:full"),
            "zcode": ("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session",
                      "permission:read", "permission:write", "permission:full"),
        }
        for harness, capabilities in expected.items():
            with self.subTest(harness=harness):
                self.assertEqual(adapter(harness).capabilities, capabilities)

    def test_capabilities_are_derived_from_the_turn_declaration(self):
        from hey_my_buddy.buddy.harnesses.registry import _WORKER_PERMISSIONS
        for harness in ("codex", "claude", "dsh", "zcode"):
            with self.subTest(harness=harness), mock.patch.dict(_WORKER_PERMISSIONS, {
                    harness: {"read": ("read", "enforced")}}):
                capabilities = adapter(harness).capabilities
                self.assertEqual([value for value in capabilities if value.startswith("permission:")],
                                 ["permission:read"])

    def test_worker_levels_declare_native_scope_and_enforcement(self):
        expected = {
            "codex": {"read": ("read", "enforced"), "write": ("write", "enforced"),
                      "full": ("full", "enforced")},
            "claude": {"read": ("read", "enforced"), "write": ("write", "enforced"), "full": ("full", "enforced")},
            "dsh": {"read": ("read", "enforced"), "write": ("write", "enforced"), "full": ("full", "enforced")},
            "zcode": {"read": ("read", "enforced"), "write": ("write", "convention"), "full": ("full", "enforced")},
        }
        for harness, levels in expected.items():
            for level, (scope, strength) in levels.items():
                with self.subTest(harness=harness, level=level):
                    permission = worker_permission(harness, level)
                    self.assertEqual((permission.level, permission.tool_scope, permission.enforcement),
                                     (level, scope, strength))

    def test_every_worker_path_carries_the_frozen_workspace_access(self):
        for harness in ("codex", "claude", "dsh", "zcode"):
            for access in ("read", "write", "full"):
                with self.subTest(harness=harness, access=access), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    turn = {"taskId": "task", "attemptId": "attempt", "generation": 1,
                            "turnId": "turn", "executionWorkspace": {"access": access}}
                    (root / "input.json").write_text(canonical_json(turn))
                    (root / "task.txt").write_text("Task")
                    binding = SimpleNamespace(services=None,
                                              description=SessionService(tool_names=("mcp__test__finish",)),
                                              completion_tool="finish", checkpoint_tool="checkpoint",
                                              answer_tool="answer")
                    module = SimpleNamespace(prepare_services=lambda **_kwargs: binding)
                    control = {"harness": harness, "privateRoot": str(root), "directory": str(root),
                               "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                               "nativeRoot": str(root / "native"), "invocationId": "invocation",
                               "spec": {"provider": "fixture", "model": "fixture", "effort": "low"},
                               "cwd": str(root), "timeoutSeconds": 10}
                    request, _services, _observer, error = run_execution.worker_request(control, module)
                    self.assertIsNone(error)
                    declaration = worker_permission(harness, access)
                    self.assertEqual(request.tool_scope, declaration.tool_scope)
                    self.assertEqual(request.worker_permission, declaration)

    def test_worker_rejects_an_undeclared_full_permission(self):
        from hey_my_buddy.buddy.harnesses.registry import _WORKER_PERMISSIONS
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.json").write_text(canonical_json({
                "taskId": "task", "attemptId": "attempt", "generation": 1,
                "turnId": "turn", "executionWorkspace": {"access": "full"}}))
            (root / "task.txt").write_text("Task")
            control = {"harness": "zcode", "privateRoot": str(root), "directory": str(root),
                       "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt")}
            with mock.patch.dict(_WORKER_PERMISSIONS, {"zcode": {
                    level: declaration for level, declaration in _WORKER_PERMISSIONS["zcode"].items()
                    if level != "full"}}), self.assertRaises(BoardError) as caught:
                run_execution.worker_request(control, SimpleNamespace())
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
            self.assertEqual(caught.exception.details["requiredCapability"], "permission:full")

    def test_every_codex_object_has_required_properties_and_rejects_additions(self):
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)

        check(worker_format("codex").schema)

    def test_codex_buddy_suggestion_is_required_and_nullable(self):
        request = worker_format("codex").schema["properties"]["outcome"]["anyOf"][1]["properties"]["request"]
        self.assertIn("suggestedBuddyId", request["required"])
        self.assertEqual(request["properties"]["suggestedBuddyId"],
                         {"anyOf": [{"type": "string"}, {"type": "null"}]})
        claude_request = worker_format("claude").schema["properties"]["outcome"]["anyOf"][1]["properties"]["request"]
        self.assertNotIn("suggestedBuddyId", claude_request["required"])


class SchemaWorkerTests(NativeRunCase):
    def worker(self, case="ok", *, previous=None):
        self.fixture_case(case)
        root = self.base / uuid.uuid4().hex
        root.mkdir(mode=0o700)
        turn = {"taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn",
                "resumeMode": "initial", "previousSessionId": previous,
                "executionWorkspace": {"access": "read"}}
        (root / "input.json").write_text(canonical_json(turn))
        (root / "task.txt").write_text("Do this bounded task")
        control = {"operation": "worker", "harness": "claude", "spec": BUDDY,
                   "invocationId": uuid.uuid4().hex, "privateRoot": str(root),
                   "directory": str(root), "nativeRoot": str(root / "native"),
                   "cwd": str(self.cwd), "timeoutSeconds": 10,
                   "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                   "outputFile": str(root / "output.json"),
                   "executionLiveFile": str(root / "execution-live.json"),
                   "live": {"instanceId": "a" * 64, "token": "b" * 64}}
        identity = RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                               invocation_id=control["invocationId"], turn_id="turn",
                               input_sha256=run_execution.input_hash(turn))
        handle = run_execution.InlineRunHandle(control, identity, grace_seconds=0)
        with mock.patch.dict(RUN_SEAMS, {"claude": native_run}), \
                mock.patch.object(native_run, "prepare_run_services", return_value=None, create=True), \
                mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)):
            result, request, verdict, code = inline_run.execute(
                control, handle, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        return root, turn, control, result, request, verdict, code

    def test_native_schema_delivery_keeps_the_shared_worker_record_and_original_read_scope(self):
        root, turn, control, result, request, verdict, code = self.worker()
        self.assertEqual(code, 0)
        self.assertNotIn("workerError", verdict)
        self.assertEqual(request.tool_scope, "read")
        self.assertIsNone(request.network_allowed_domains)
        self.assertEqual(request.additional_denied_tools, ())
        self.assertEqual(request.session_services, ())
        self.assertIn("Do this bounded task", request.input_text)
        record = json.loads((root / "output.json").read_bytes())
        self.assertEqual(record["outcome"]["disposition"], "completed")
        self.assertTrue(record["provenance"]["structuredOutputValidated"])
        self.assertEqual(record["attemptId"], request.identity.attempt_id)
        self.assertEqual(record["promptSha256"], hashlib.sha256(request.input_text.encode()).hexdigest())
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_native_success_with_an_invalid_role_value_returns_a_failed_controller_exit(self):
        root, _turn, _control, result, _request, verdict, code = self.worker("bad-structured")
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(code, 1)
        self.assertEqual(verdict["workerError"]["code"], "invalid-result")
        self.assertFalse((root / "output.json").exists())
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_denied_permissions_turn_a_native_completed_value_into_role_attention(self):
        root, turn, control, result, request, _verdict, code = self.worker("permission")
        self.assertEqual(code, 0)
        payload = run_execution._worker_facts(result)
        self.assertFalse(run_execution._worker_reports(result, payload))
        record = json.loads((root / "output.json").read_bytes())
        self.assertEqual(record["outcome"]["disposition"], "attention")
        self.assertTrue(record["provenance"]["controllerAttention"])
        self.assertFalse(record["provenance"]["structuredOutputValidated"])
        self.assertGreater(payload["nativeAttention"]["requests"], 0)
        self.assertIn("WebFetch", record["outcome"]["summary"])

    def test_unreadable_role_evidence_keeps_the_native_result_and_stop(self):
        with mock.patch.object(run_execution, "worker_delivery", side_effect=OSError("missing evidence")):
            _root, _turn, _control, result, _request, verdict, code = self.worker()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["workerError"]["code"], "invalid-role-result")
        self.assertEqual(result.end.status, "ok")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_an_initial_turn_with_a_previous_session_never_reaches_the_native_run(self):
        with mock.patch.object(native_run, "run", side_effect=AssertionError("native execution")):
            root, _turn, _control, result, _request, _verdict, code = self.worker(previous="old-session")
        self.assertEqual(code, 1)
        self.assertEqual(result.end.reason_code, "invalid-resume-mode")
        self.assertIs(result.model_started, False)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertFalse((root / "output.json").exists())

    def test_discovery_errors_need_explicit_boolean_native_stop_evidence(self):
        control = {"operation": "discover", "harness": "claude", "cwd": str(self.cwd),
                   "privateRoot": str(self.base), "nativeRoot": str(self.base / "native"), "timeoutSeconds": 1}
        for stopped in (None, False, True, "true"):
            with self.subTest(stopped=stopped):
                error = RuntimeError("tokenSource none")
                error.discovery_shutdown_confirmed = stopped
                with mock.patch.dict(RUN_SEAMS, {"claude": native_run}), \
                        mock.patch.object(native_run, "run_discovery", side_effect=error):
                    frame, code = run_controller.execute(control, threading.Event(), state_dir=Path(os.environ["HMB_STATE_DIR"]))
                payload = json.loads(frame)
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"], "tokenSource none")
                self.assertIs(payload["modelStarted"], False)
                self.assertIs(payload["processState"]["shutdownConfirmed"], stopped is True)

    def test_native_schemas_stay_fixed_and_full_prompt_hashes_include_the_worker_role(self):
        # Codex requires a nullable buddy suggestion; ADR-030
        # pins the full assembled prompt, including the published Worker text.
        hashes = {
            "codex": ("b46e2bd89c8d171059f35ad52c7c97c8b98fd32bc49382da1c299b4d0d500f1a",
                      "b8156e7eb034d31e259c3b530c334bcf7189d7e804ed34210194dd5a10e8c3b4"),
            "claude": ("ff7983882b2a1adefe1cad314fec9c009086ca2ae3fd38d33fe9fddf248f9bca",
                       "d7903b22bfce84b7894a803b54bf2db97dcb0e1a540923ddcdb5f6efe2cb2581"),
        }
        for name, (schema_hash, prompt_hash) in hashes.items():
            with self.subTest(harness=name):
                format = worker_format(name)
                self.assertEqual(hashlib.sha256(canonical_json(format.schema).encode()).hexdigest(), schema_hash)
                self.assertEqual(hashlib.sha256(format.prompt("task", {"turn": 1}).encode()).hexdigest(), prompt_hash)
        self.assertEqual(worker_permission("codex", "read").tool_scope, "read")
