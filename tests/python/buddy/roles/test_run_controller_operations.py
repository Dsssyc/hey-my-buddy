"""Controller specifications and deletion receipts at the process boundary."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.roles import run_controller, run_execution
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.errors import BoardError


class ControllerSpecificationTests(unittest.TestCase):
    def control(self, *, unknown_result=None):
        return {"operation": "fixture-operation", "harness": "codex",
                "specification": {"seam": "fixture_seam", "resultKey": "fixtureResult",
                                  "unknownResult": unknown_result},
                "arguments": {"session_ids": ["owned"]}, "cwd": "/fixture",
                "privateRoot": "/fixture", "nativeRoot": "/fixture/native", "timeoutSeconds": 1}

    def test_controller_uses_the_supplied_seam_arguments_and_result_key(self):
        seam = Mock(return_value={"owned": "deleted"})
        with patch.object(run_controller, "run_seam", return_value=SimpleNamespace(fixture_seam=seam)):
            frame, code = run_controller.execute(self.control(), threading.Event(), Path("/fixture/state"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(frame)["fixtureResult"], {"owned": "deleted"})
        self.assertEqual(seam.call_args.kwargs["session_ids"], ["owned"])

    def test_deadline_disposition_follows_the_specification(self):
        error = CodexProtocolError("deadline", "fixture deadline")
        error.discovery_shutdown_confirmed = True
        for unknown_result, status, exit_code in ((None, "error", 1), ({}, "unknown", 0)):
            with self.subTest(status=status), patch.object(run_controller, "run_seam", return_value=
                    SimpleNamespace(fixture_seam=Mock(side_effect=error))):
                frame, code = run_controller.execute(self.control(unknown_result=unknown_result),
                                                    threading.Event(), Path("/fixture/state"))
                self.assertEqual(code, exit_code)
                self.assertEqual(json.loads(frame)["status"], status)

    def test_controller_bounds_display_text_but_preserves_native_error(self):
        native = {"code": -32000, "message": "原因" * 400}
        error = CodexProtocolError("native-rpc-error", native["message"], native_error=native)
        error.discovery_shutdown_confirmed = True
        with patch.object(run_controller, "run_seam", return_value=
                SimpleNamespace(fixture_seam=Mock(side_effect=error))):
            frame, code = run_controller.execute(self.control(), threading.Event(), Path("/fixture/state"))
        payload = json.loads(frame)
        self.assertEqual(code, 1)
        self.assertLessEqual(len(payload["error"].encode("utf-8")), 512)
        self.assertEqual(payload["nativeError"], native)


class DeletionReceiptBoundaryTests(unittest.TestCase):
    def receipt(self, payload, *, stopped=True, wait_result=0, exit_code=0):
        handle = Mock()
        handle.wait.return_value = wait_result
        collection = SimpleNamespace(payload=payload, exit_code=exit_code, stop_confirmed=stopped)
        with patch.object(run_execution, "launch_controller", return_value=handle), \
             patch.object(run_execution, "collect_controller", return_value=collection):
            return run_execution.delete_sessions("codex", ["owned", "gone", "owned"])

    def test_accepts_exact_keys_and_supported_results_after_request_deduplication(self):
        sessions = {"owned": "deleted", "gone": "already-gone"}
        self.assertEqual(self.receipt({"status": "ok", "sessions": sessions}), sessions)

    def test_rejects_non_objects_missing_or_extra_keys_and_invalid_values(self):
        for sessions in (None, [], "invalid", {}, {"owned": "deleted"},
                         {"owned": "deleted", "gone": "already-gone", "extra": "deleted"},
                         {"owned": "unknown", "gone": "deleted"},
                         {"owned": [], "gone": "deleted"}):
            with self.subTest(sessions=sessions), self.assertRaises(BoardError) as caught:
                self.receipt({"status": "ok", "sessions": sessions})
            self.assertEqual(caught.exception.code, "INVALID_PROTOCOL")
        with self.assertRaises(BoardError) as caught:
            self.receipt({"status": "ok"})
        self.assertEqual(caught.exception.code, "INVALID_PROTOCOL")

    def test_deletion_timeout_and_unknown_status_are_failures(self):
        for payload, wait_result in (({"status": "unknown"}, 0),
                                     ({"status": "ok", "sessions": {"owned": "deleted", "gone": "deleted"}}, None)):
            with self.subTest(payload=payload), self.assertRaises(BoardError) as caught:
                self.receipt(payload, wait_result=wait_result)
            self.assertEqual(caught.exception.code, "HARNESS_UNAVAILABLE")

    def test_display_bound_is_applied_at_the_controller_process_boundary(self):
        native = {"code": -32000, "message": "原因" * 400}
        with self.assertRaises(BoardError) as caught:
            self.receipt({"status": "error", "error": native["message"], "nativeError": native}, exit_code=1)
        self.assertLessEqual(len(str(caught.exception).encode("utf-8")), 512)
        self.assertEqual(caught.exception.details["nativeError"], native)
