"""The schema Worker uses one role policy and real native fixture deliveries."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, worker_format
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request, decode_run_result
from hey_my_buddy.buddy.roles import run_controller, run_execution
from hey_my_buddy.json_codec import canonical_json
from buddy.harnesses.claude.test_native_run import CONFIGURATION, NativeRunCase


class SchemaWorkerTests(NativeRunCase):
    def worker(self, case="ok"):
        self.fixture_case(case)
        root = self.base / uuid.uuid4().hex
        root.mkdir(mode=0o700)
        turn = {"taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn",
                "resumeMode": "initial", "previousSessionId": None,
                "executionWorkspace": {"access": "read"}}
        (root / "input.json").write_text(canonical_json(turn))
        (root / "task.txt").write_text("Do this bounded task")
        control = {"operation": "worker", "harness": "claude", "spec": CONFIGURATION,
                   "invocationId": uuid.uuid4().hex, "privateRoot": str(root),
                   "directory": str(root), "nativeRoot": str(root / "native"),
                   "cwd": str(self.cwd), "timeoutSeconds": 10,
                   "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                   "outputFile": str(root / "output.json"),
                   "requestFile": str(root / "request.json"), "verdictFile": str(root / "verdict.json")}
        # 3-B2 owns the harness's path-only service binding; this test exercises
        # the shared role, the existing real fixture process and its actual
        # frames without editing that microtask's native files.
        with mock.patch.dict(RUN_SEAMS, {"claude": native_run}), \
                mock.patch.object(native_run, "prepare_run_services", return_value=None, create=True):
            frame, code = run_controller.execute(control, threading.Event())
        result = decode_run_result(frame)
        request = decode_run_request((root / "request.json").read_bytes())
        verdict = json.loads((root / "verdict.json").read_bytes())
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
        payload = run_execution._worker_facts(result)
        self.assertFalse(run_execution._worker_reports(result, payload))
        run_execution._worker_result(result, control, turn, request.input_text, payload)
        record = json.loads((root / "output.json").read_bytes())
        self.assertEqual(record["outcome"]["disposition"], "completed")
        self.assertTrue(record["provenance"]["structuredOutputValidated"])
        self.assertEqual(record["attemptId"], request.identity.attempt_id)
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
        run_execution._worker_result(result, control, turn, request.input_text, payload)
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

    def test_the_moved_schemas_and_prompt_prefixes_equal_the_accepted_baseline(self):
        # Captured from the two old protocol/runner implementations at
        # 02b1f76; keep their intentional description/optional-field differences.
        hashes = {
            "codex": ("71b8134b2e41ebe2045afa8f3808f0f04b3cee52e0d979918ba4381c150dedec",
                      "5763aa5435a21a02d83f481a0a77f6d5062adace6862442617a17382fc2bf61c"),
            "claude": ("cd91d26ede0d45b6bed2b7747520a1358ab334874c26f9d19bd2f348ae5e15a3",
                       "6714f3aa9afe52e9a10be75b2dd219abbc3234f6c2f77bdda644745db252b0a6"),
        }
        for name, (schema_hash, prefix_hash) in hashes.items():
            with self.subTest(harness=name):
                format = worker_format(name)
                self.assertEqual(hashlib.sha256(canonical_json(format.schema).encode()).hexdigest(), schema_hash)
                self.assertEqual(hashlib.sha256("\n\n".join(format.prefixes).encode()).hexdigest(), prefix_hash)
        self.assertEqual(worker_format("codex").tool_scope({"executionWorkspace": {"access": "read"}}), "write")
