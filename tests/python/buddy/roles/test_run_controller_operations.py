"""Named controller operations and deletion receipts at the process boundary."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.roles import run_controller, run_execution
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.errors import BoardError


class ControllerOperationTests(unittest.TestCase):
    def control(self, operation="delete-sessions"):
        return {"operation": operation, "harness": "codex",
                "arguments": {"session_ids": ["owned"]} if operation == "delete-sessions" else {}, "cwd": "/fixture",
                "privateRoot": "/fixture", "nativeRoot": "/fixture/native", "timeoutSeconds": 1}

    def test_controller_resolves_the_registered_operation_and_passes_arguments(self):
        seam = Mock(return_value={"owned": "deleted"})
        with patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=SimpleNamespace(run_delete_sessions=seam)):
            frame, code = run_controller.execute(self.control(), threading.Event())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(frame)["sessions"], {"owned": "deleted"})
        self.assertEqual(seam.call_args.kwargs["session_ids"], ["owned"])

    def test_unknown_operation_is_refused_before_resolving_any_harness(self):
        for operation in ("review", "fixture-operation", None, []):
            with self.subTest(operation=operation), patch("hey_my_buddy.buddy.harnesses.registry.run_seam") as resolve:
                with self.assertRaises(BoardError) as caught:
                    run_controller.execute({"operation": operation}, threading.Event())
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
                resolve.assert_not_called()

    def test_control_file_cannot_supply_a_seam_specification(self):
        control = {**self.control(), "specification": {"seam": "run_review"}}
        with patch("hey_my_buddy.buddy.harnesses.registry.run_seam") as resolve, self.assertRaises(BoardError) as caught:
            run_controller.execute(control, threading.Event())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        resolve.assert_not_called()

    def test_controller_process_preserves_unknown_operation_error_before_native_launch(self):
        with tempfile.TemporaryDirectory(prefix='unknown-controller-operation-') as directory:
            control = Path(directory) / 'control.json'
            run_execution.private_json(control, {'operation': 'review', 'harness': 'codex'})
            completed = subprocess.run(
                [sys.executable, '-m', 'hey_my_buddy.buddy.roles.run_controller', '--control', str(control)],
                env={**os.environ, 'PATH': str(Path(directory) / 'no-executables')},
                capture_output=True, text=True, timeout=10, encoding="utf-8")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['code'], 'INVALID_ARGUMENT')
        self.assertEqual(payload['message'], 'Unknown native controller operation')
        self.assertIs(payload['modelStarted'], False)
        self.assertIs(payload['processState']['shutdownConfirmed'], True)

    def test_deadline_disposition_follows_the_registered_operation(self):
        error = CodexProtocolError("deadline", "fixture deadline")
        error.discovery_shutdown_confirmed = True
        for operation, status, exit_code in (("delete-sessions", "error", 1), ("discover", "unknown", 0)):
            with self.subTest(status=status), patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=
                    SimpleNamespace(run_delete_sessions=Mock(side_effect=error), run_discovery=Mock(side_effect=error))):
                frame, code = run_controller.execute(self.control(operation),
                                                    threading.Event())
                self.assertEqual(code, exit_code)
                self.assertEqual(json.loads(frame)["status"], status)

    def test_controller_bounds_display_text_but_preserves_native_error(self):
        native = {"code": -32000, "message": "原因" * 400}
        error = CodexProtocolError("native-rpc-error", native["message"], native_error=native)
        error.discovery_shutdown_confirmed = True
        with patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=
                SimpleNamespace(run_delete_sessions=Mock(side_effect=error))):
            frame, code = run_controller.execute(self.control(), threading.Event())
        payload = json.loads(frame)
        self.assertEqual(code, 1)
        self.assertLessEqual(len(payload["error"].encode("utf-8")), 512)
        self.assertEqual(payload["nativeError"], native)


class DeletionReceiptBoundaryTests(unittest.TestCase):
    def test_native_control_tree_is_in_state_outside_the_worker_tmpdir(self):
        from hey_my_buddy.protocol.owned_process import ProcessCreationEvidence
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state, worker_tmp = root / 'state', root / 'worker-tmp'
            worker_tmp.mkdir()
            reached = RuntimeError('launch boundary reached')
            reached.owned_popen_evidence = ProcessCreationEvidence(False, True, None)
            controls = []
            def launch(**kwargs):
                argv, _cwd, _environment = kwargs['prepare']()
                path = Path(argv[-1])
                controls.append(path)
                control = json.loads(path.read_bytes())
                for field in ('privateRoot', 'nativeRoot', 'cwd'):
                    self.assertTrue(Path(control[field]).is_relative_to(state / 'tmp'))
                self.assertTrue(path.is_relative_to(state / 'tmp'))
                raise reached
            with patch.dict(os.environ, {'HMB_STATE_DIR': str(state), 'TMPDIR': str(worker_tmp)}), \
                    patch('hey_my_buddy.buddy.harnesses.registry.run_seam', return_value=SimpleNamespace(run_delete_sessions=Mock())), \
                    patch.object(run_execution, 'launch_controller', side_effect=launch):
                with self.assertRaises(RuntimeError) as caught:
                    run_execution.delete_sessions('codex', ['owned'])
            self.assertIs(caught.exception, reached)
            self.assertEqual(len(controls), 1)
            self.assertEqual(list(worker_tmp.iterdir()), [])
            self.assertEqual(list((state / 'tmp').iterdir()), [])

    def test_control_file_carries_only_the_operation_name_arguments_and_context(self):
        handle = Mock()
        handle.wait.return_value = 0
        collection = SimpleNamespace(payload={'status': 'ok', 'sessions': {'owned': 'deleted'}},
                                     exit_code=0, stop_confirmed=True)

        def launch(**kwargs):
            argv, _, _ = kwargs['prepare']()
            control = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            self.assertEqual(set(control), {'operation', 'arguments', 'harness', 'privateRoot',
                                             'nativeRoot', 'cwd', 'timeoutSeconds'})
            self.assertEqual(control['operation'], 'delete-sessions')
            self.assertEqual(control['arguments'], {'session_ids': ['owned']})
            return handle

        with patch.object(run_execution, 'launch_controller', side_effect=launch), \
             patch.object(run_execution, 'collect_controller', return_value=collection):
            self.assertEqual(run_execution.delete_sessions('codex', ['owned']), {'owned': 'deleted'})

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
        self.assertEqual(caught.exception.message, native["message"])
        self.assertEqual(caught.exception.details["nativeError"], native)
