"""ADR-030 native shape facts and the ownership/budget boundaries they retain."""
from __future__ import annotations

import copy
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.harnesses.codex import native_run
from hey_my_buddy.buddy.harnesses.controller import STRICT_RESULT_BYTES
from hey_my_buddy.buddy.harnesses.codex.protocol import Connection, CodexProtocolError, TurnEvidence
from hey_my_buddy.buddy.harnesses.run_contract import (
    PrivateStatePaths, RunBudget, RunConfiguration, RunIdentity, RunRequest,
)


class NativeFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-codex-facts-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = RunRequest(
            identity=RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                 invocation_id="invocation"),
            harness="codex", configuration=RunConfiguration(provider="openai", model="model", effort="low"),
            cwd=str(self.root), private_state=PrivateStatePaths(invocation_root=str(self.root),
                                                              native_root=str(self.root / "native")),
            input_text="Read the input", output_schema={"type": "object"}, tool_scope="none",
            budget=RunBudget(timeout_seconds=10))
        self.receipt = {"thread": {"id": "thread", "cwd": str(self.root), "turns": []},
                        "model": "model", "modelProvider": "openai", "cwd": str(self.root),
                        "approvalPolicy": "never", "activePermissionProfile": {"id": "buddy-router"},
                        "sandbox": {"type": "readOnly", "networkAccess": False}}

    def configure(self, mode, receipt=None, config=None):
        connection = mock.Mock()
        connection.call.side_effect = [receipt or self.receipt, {"config": config}]
        state = native_run._RunState()
        if mode == "fast":
            result = native_run._configure_fast(connection, self.request, state)
        else:
            result = native_run._configure_review(connection, self.request, state)
        return result, state

    def test_catalog_unreadable_cursor_stops_reading_with_unknown_fact(self):
        for cursor in ("", 12, []):
            with self.subTest(cursor=cursor):
                connection = mock.Mock()
                connection.call.return_value = {"data": [], "nextCursor": cursor}
                catalog, listed = native_run._catalog(connection, "version", "openai")
                self.assertEqual(catalog["discoveries"][0], {"adapter": "codex", "status": "unknown",
                                                           "reason": "model-cursor-shape-unreadable"})
                self.assertEqual(listed, set())
                connection.call.assert_called_once()

    def test_catalog_count_budget_still_refuses(self):
        connection = mock.Mock()
        connection.call.return_value = {"data": [{}] * 201}
        with self.assertRaises(CodexProtocolError) as caught:
            native_run._catalog(connection, "version", "openai")
        self.assertEqual(caught.exception.code, "invalid-catalog")

    def test_catalog_repeated_cursor_still_refuses(self):
        connection = mock.Mock()
        connection.call.return_value = {"data": [], "nextCursor": "same"}
        with self.assertRaises(CodexProtocolError) as caught:
            native_run._catalog(connection, "version", "openai")
        self.assertEqual(caught.exception.code, "invalid-catalog")
        self.assertEqual(connection.call.call_count, 2)

    def test_fast_ownership_and_history_still_refuse(self):
        for change, reason in (({"id": None}, "wrong-native-thread"),
                               ({"id": ""}, "wrong-native-thread"),
                               ({"cwd": str(self.root / "foreign")}, "wrong-native-workspace"),
                               ({"turns": [{"id": "old"}]}, "wrong-native-thread"),
                               ({"turns": "unreadable"}, "wrong-native-thread")):
            with self.subTest(change=change):
                receipt = copy.deepcopy(self.receipt)
                receipt["thread"].update(change)
                with self.assertRaises(CodexProtocolError) as caught:
                    self.configure("fast", receipt, {})
                self.assertEqual(caught.exception.code, reason)

    def test_fast_unverified_approval_continues_and_retains_receipt(self):
        receipt = {**self.receipt, "approvalPolicy": "on-request"}
        thread, state = self.configure("fast", receipt, {"web_search": "disabled", "approval_policy": "never"})
        self.assertEqual(thread, "thread")
        self.assertEqual(state.policy_checks["threadReceipt"]["status"], "unknown")
        refs = native_run._evidence_refs(self.request, state, native_run._RunFacts())
        result = SimpleNamespace(evidence_refs=refs)
        self.assertEqual(native_run._read_evidence_ref(result, "fast-thread-receipt")["approvalPolicy"], "on-request")

    def test_review_model_mismatch_continues_as_unknown_with_actual_model(self):
        receipt = {**self.receipt, "model": "different-model", "modelProvider": "native-provider"}
        thread, state = self.configure("review", receipt, {})
        self.assertEqual(thread, "thread")
        self.assertEqual(state.policy_checks["selectedModel"]["status"], "unknown")
        checked = native_run._checked_configuration(self.request, state, "review").checked
        self.assertEqual(checked.model.value, "different-model")
        self.assertEqual(checked.provider.value, "native-provider")
        self.assertIsNone(checked.effort)
        refs = native_run._evidence_refs(self.request, state, native_run._RunFacts())
        result = SimpleNamespace(evidence_refs=refs)
        self.assertEqual(native_run._read_evidence_ref(result, "review-thread-receipt")["model"], "different-model")

    def test_review_checkout_and_thread_ownership_still_refuse(self):
        for top, nested, reason in (({"cwd": str(self.root / "foreign")}, {}, "readonly-configuration-mismatch"),
                                    ({"cwd": None}, {}, "readonly-configuration-mismatch"),
                                    ({}, {"id": ""}, "wrong-native-workspace"),
                                    ({}, {"cwd": str(self.root / "foreign")}, "wrong-native-workspace")):
            with self.subTest(top=top, nested=nested):
                receipt = copy.deepcopy(self.receipt)
                receipt.update(top)
                receipt["thread"].update(nested)
                with self.assertRaises(CodexProtocolError) as caught:
                    self.configure("review", receipt, {})
                self.assertEqual(caught.exception.code, reason)

    def test_unreadable_policy_shapes_continue_as_unknown(self):
        receipt = {**self.receipt, "activePermissionProfile": [], "sandbox": "new-shape"}
        thread, state = self.configure("review", receipt,
                                                {"features": [], "permissions": "new-shape"})
        self.assertEqual(thread, "thread")
        self.assertEqual(state.policy_checks["configReadback"]["status"], "unknown")
        self.assertEqual(state.policy_checks["threadReceipt"]["status"], "unknown")
        thread, fast_state = self.configure("fast", config=[])
        self.assertEqual(thread, "thread")
        self.assertEqual(fast_state.policy_checks["configReadback"]["status"], "unknown")

    def test_missing_network_readback_keeps_review_restriction_unconfirmed(self):
        receipt = {**self.receipt, "sandbox": {"type": "readOnly"}}
        thread, state = self.configure("review", receipt, {})
        self.assertEqual(thread, "thread")
        self.assertEqual(state.policy_checks["threadReceipt"]["status"], "unknown")

    def test_large_unverified_policy_fields_keep_bounded_projectable_facts(self):
        large = "x" * (STRICT_RESULT_BYTES + 1)
        receipt = {**self.receipt, "model": large, "modelProvider": large,
                   "approvalPolicy": large, "activePermissionProfile": {"id": large},
                   "sandbox": {"type": large}}
        config = {"web_search": large, "approval_policy": large, "default_permissions": large,
                  "permissions": {"buddy-router": {"filesystem": {large: "write"}}}}
        for mode in ("fast", "review"):
            with self.subTest(mode=mode):
                thread, state = self.configure(mode, receipt, config)
                self.assertEqual(thread, "thread")
                self.assertEqual(state.policy_checks["configReadback"]["status"], "unknown")
                self.assertEqual(state.policy_checks["selectedModel"]["status"], "unknown")
                refs = native_run._evidence_refs(self.request, state, native_run._RunFacts())
                for ref in refs:
                    self.assertLessEqual(ref.size_bytes, STRICT_RESULT_BYTES)
                    value = native_run._read_evidence_ref(SimpleNamespace(evidence_refs=refs), ref.kind)
                    self.assertIsInstance(value, dict)
                # Use distinct invocation directories, as evidence is immutable.
                if mode == "fast":
                    root = self.root / "review"
                    root.mkdir()
                    self.request = self.request.model_copy(update={"private_state": PrivateStatePaths(
                        invocation_root=str(root), native_root=str(root / "native"))})

    def test_unreadable_interrupt_reply_does_not_confirm_acknowledgement(self):
        for result, basis in (([], "native-turn-interrupt-unconfirmed"),
                              ({}, "native-turn-interrupt-ack")):
            with self.subTest(result=result):
                connection = Connection.__new__(Connection)
                connection.next_id = 1
                connection.pending_ids = set()
                connection.responses = {}
                connection.strict_responses = False
                connection.after_pump = None
                connection.messages = queue.Queue()
                connection.messages.put({"id": 1, "result": result})
                connection.send = lambda _message: None
                state = native_run._RunState(thread_id="thread", turn_id="turn")
                native_run._interrupt(connection, state)
                self.assertTrue(state.interrupt_requested)
                self.assertEqual(state.interrupt_basis, basis)

    def test_review_capture_keeps_unreadable_item_as_bounded_event(self):
        facts = native_run._RunFacts()
        message = {"method": "item/started", "params": {"threadId": "thread", "turnId": "turn", "item": "new-shape"}}
        self.assertTrue(native_run._Classifier(facts).observe(message))
        capture = {"toolEvents": [], "rawToolEvents": [], "truncated": False}
        native_run._capture_frame(capture, message)
        self.assertEqual(capture["toolEvents"], [message])
        self.assertEqual(facts.unknown_counts, {"item-shape-unreadable": 1})

    def test_unknown_frame_shape_does_not_hide_explicit_foreign_identity(self):
        facts = native_run._RunFacts()
        classifier = native_run._Classifier(facts)
        carrier = native_run._FastProtocol("thread", "turn", {}, TurnEvidence("thread", "turn"),
                                           native_run._RunState())
        for method in ([], {}, None):
            with self.subTest(method=method):
                message = {"method": method, "params": {"threadId": "foreign", "turnId": "turn"}}
                self.assertTrue(classifier.observe(message))
                with self.assertRaises(CodexProtocolError) as caught:
                    carrier(message)
                self.assertEqual(caught.exception.code, "invalid-native-result")
        self.assertEqual(facts.unknown_counts, {"unknown-method": 3})


if __name__ == "__main__":
    unittest.main()
