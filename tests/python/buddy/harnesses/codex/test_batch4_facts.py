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
    PrivateStatePaths, RunBudget, RunBuddy, RunIdentity, RunRequest,
)
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.harnesses.registry import worker_permission


class NativeFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-codex-facts-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = RunRequest(
            identity=RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                 invocation_id="invocation"),
            harness="codex", buddy=RunBuddy(provider="openai", model="model", effort="low"),
            cwd=str(self.root), private_state=PrivateStatePaths(invocation_root=str(self.root),
                                                              native_root=str(self.root / "native")),
            input_text="Read the input", output_schema={"type": "object"}, tool_scope="none",
            budget=RunBudget(timeout_seconds=10))
        self.receipt = {"thread": {"id": "thread", "cwd": str(self.root), "turns": []},
                        "model": "model", "modelProvider": "openai", "cwd": str(self.root),
                        "approvalPolicy": "never", "activePermissionProfile": {"id": "hey-my-buddy-router"},
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
                self.assertEqual(catalog["discoveries"][0], {"harness": "codex", "status": "unknown",
                                                           "reason": {"code": "model-cursor-shape-unreadable", "params": {}}})
                self.assertEqual(listed, set())
                connection.call.assert_called_once()

    def test_unreadable_initial_config_keeps_catalog_unknown_for_every_carrier(self):
        page = {"data": [{"model": "model", "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}]}
        for mode in ("worker", "fast", "review"):
            for response in ({}, {"config": None}, {"config": []}, {"config": 17}):
                with self.subTest(mode=mode, response=response):
                    connection = mock.Mock()
                    connection.call.side_effect = [{}, {}, response, page]
                    state = native_run._RunState()
                    selected = native_run._initialize(connection, SimpleNamespace(cwd=str(self.root), version="fixture"),
                                                      mode, {"model": "model", "effort": "low"}, state)
                    self.assertIsNone(selected)
                    self.assertEqual(state.catalog_read["discovery"]["status"], "unknown")
                    self.assertEqual(state.catalog_read["discovery"]["reason"],
                                     {"code": "config-shape-unreadable", "params": {}})
                    self.assertEqual(state.catalog_read["warnings"], [])
                    self.assertFalse(state.model_started)

    def test_readable_empty_config_still_uses_native_default_provider(self):
        connection = mock.Mock()
        connection.call.return_value = {"data": []}
        catalog, listed = native_run._catalog(connection, "fixture", native_run.native_provider({}))
        self.assertEqual(catalog["discoveries"][0]["status"], "complete")
        self.assertEqual(catalog["providers"][0]["provider"], "openai")
        self.assertEqual(listed, set())

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

    def test_different_native_model_still_refuses_even_beyond_readback_bound(self):
        for mode, code in (("review", "readonly-configuration-mismatch"), ("fast", "buddy-mismatch")):
            for model in ("different-model", "", "x" * (STRICT_RESULT_BYTES + 1)):
                with self.subTest(mode=mode, model_length=len(model)):
                    receipt = {**self.receipt, "model": model}
                    with self.assertRaises(CodexProtocolError) as caught:
                        self.configure(mode, receipt, {})
                    self.assertEqual(caught.exception.code, code)

    def test_unreadable_review_model_continues_with_unconfirmed_model_fact(self):
        for model in (None, 17, []):
            with self.subTest(model=model):
                receipt = {**self.receipt, "model": model}
                thread, state = self.configure("review", receipt, {})
                self.assertEqual(thread, "thread")
                self.assertEqual(state.policy_checks["selectedModel"]["status"], "unknown")
                checked = native_run._checked_buddy(self.request, state, "review").checked
                self.assertIsNone(checked.model)
                self.assertIsNone(checked.effort)

    def test_review_sandbox_capability_still_refuses_missing_or_non_read_only_type(self):
        for sandbox in (None, [], "new-shape", {}, {"type": "workspaceWrite"}):
            with self.subTest(sandbox=sandbox):
                receipt = {**self.receipt, "sandbox": sandbox}
                with self.assertRaises(CodexProtocolError) as caught:
                    self.configure("review", receipt, {})
                self.assertEqual(caught.exception.code, "readonly-policy-unverified")

    def test_unreadable_review_receipt_restrictions_continue_as_unverified(self):
        for change in ({"activePermissionProfile": []}, {"activePermissionProfile": {"id": 17}},
                       {"approvalPolicy": []}, {"approvalPolicy": None},
                       {"sandbox": {"type": "readOnly", "networkAccess": []}}):
            with self.subTest(change=change):
                thread, state = self.configure("review", {**self.receipt, **change}, {})
                self.assertEqual(thread, "thread")
                self.assertEqual(state.policy_checks["threadReceipt"]["status"], "unknown")

    def test_different_review_receipt_restrictions_refuse_before_a_turn(self):
        for change in ({"activePermissionProfile": {"id": "different"}},
                       {"approvalPolicy": "on-request"},
                       {"sandbox": {"type": "readOnly", "networkAccess": True}}):
            with self.subTest(change=change):
                connection = mock.Mock()
                connection.call.side_effect = [{**self.receipt, **change}, {"config": {}}]
                state = native_run._RunState()
                with self.assertRaises(CodexProtocolError) as caught:
                    native_run._configure_review(connection, self.request, state)
                self.assertEqual(caught.exception.code, "readonly-policy-unverified")
                self.assertIsNotNone(state.review_receipt)
                self.assertFalse(state.model_started)
                self.assertNotIn("turn/start", [call.args[0] for call in connection.call.call_args_list])

    def test_different_review_config_controls_refuse_with_actual_readback(self):
        expected = native_run.tomllib.loads(native_run.read_only_config(str(self.root)))
        changes = ({"web_search": "enabled"}, {"approval_policy": "on-request"},
                   {"default_permissions": "different"},
                   {"permissions": {"hey-my-buddy-router": {"network": {"enabled": True}}}},
                   {"permissions": {"hey-my-buddy-router": {"filesystem": {str(self.root): "write"}}}},
                   {"permissions": {"hey-my-buddy-router": {"filesystem": {"/extra": "read"}}}})
        for change in changes:
            with self.subTest(change=change):
                connection = mock.Mock()
                connection.call.side_effect = [self.receipt, {"config": change}]
                state = native_run._RunState()
                with self.assertRaises(CodexProtocolError) as caught:
                    native_run._configure_review(connection, self.request, state)
                self.assertEqual(caught.exception.code, "readonly-policy-unverified")
                self.assertIsNotNone(state.review_config)
                self.assertFalse(state.model_started)
                self.assertNotIn("turn/start", [call.args[0] for call in connection.call.call_args_list])
        thread, state = self.configure("review", config=expected)
        self.assertEqual(thread, "thread")
        self.assertEqual(state.policy_checks["configReadback"]["status"], "confirmed")

    def test_different_provider_refuses_even_without_catalog_confirmation(self):
        for mode, code in (("fast", "buddy-mismatch"),
                           ("review", "readonly-configuration-mismatch"),
                           ("worker", "buddy-mismatch")):
            for provider in ("other", "", "x" * (STRICT_RESULT_BYTES + 1)):
                with self.subTest(mode=mode, provider_length=len(provider)):
                    connection = mock.Mock()
                    receipt = {**self.receipt, "modelProvider": provider}
                    connection.call.side_effect = [receipt, {"config": {}}]
                    state = native_run._RunState()
                    with self.assertRaises(CodexProtocolError) as caught:
                        if mode == "worker":
                            request = self.request.model_copy(update={"worker_permission": worker_permission("codex", "write")})
                            native_run._configure_worker(connection, request, SimpleNamespace(), state)
                        else:
                            getattr(native_run, "_configure_" + mode)(connection, self.request, state)
                    self.assertEqual(caught.exception.code, code)
                    self.assertFalse(state.model_started)
                    self.assertNotIn("turn/start", [call.args[0] for call in connection.call.call_args_list])

    def test_unreadable_provider_continues_with_empty_checked_provider(self):
        for mode in ("fast", "review", "worker"):
            for provider in (None, 17, []):
                with self.subTest(mode=mode, provider=provider):
                    connection = mock.Mock()
                    connection.call.side_effect = [{**self.receipt, "modelProvider": provider}, {"config": {}}]
                    state = native_run._RunState()
                    if mode == "worker":
                        request = self.request.model_copy(update={"worker_permission": worker_permission("codex", "write")})
                        thread = native_run._configure_worker(connection, request, SimpleNamespace(), state)
                    else:
                        thread = getattr(native_run, "_configure_" + mode)(connection, self.request, state)
                    self.assertEqual(thread, "thread")
                    self.assertIsNone(native_run._checked_buddy(self.request, state, mode).checked.provider)

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
        receipt = {**self.receipt, "activePermissionProfile": [], "sandbox": {"type": "readOnly", "networkAccess": []}}
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
        receipt = {**self.receipt, "model": None, "modelProvider": [large],
                   "approvalPolicy": [large], "activePermissionProfile": {"id": [large]},
                   "sandbox": {"type": "readOnly", "networkAccess": large}}
        config = {"web_search": [large], "approval_policy": [large], "default_permissions": [large],
                  "permissions": {"hey-my-buddy-router": {"filesystem": {large: []}}}}
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

    def test_fast_connection_notifications_without_thread_identity_continue(self):
        facts = native_run._RunFacts()
        classifier = native_run._Classifier(facts)
        state = {"started": False, "final": None, "completed": None}
        carrier = native_run._FastProtocol("thread", "turn", state, TurnEvidence("thread", "turn"),
                                           native_run._RunState())
        for params in (None, [], {}, {"turnId": "unbound-turn"}):
            message = {"method": "account/updated", "params": params}
            classifier.observe(message)
            carrier(message)
        self.assertEqual(facts.unknown_counts, {"account/updated": 4})
        self.assertEqual(state, {"started": False, "final": None, "completed": None})

    def test_fast_connection_notification_names_do_not_bypass_explicit_identity(self):
        carrier = native_run._FastProtocol("thread", "turn", {}, TurnEvidence("thread", "turn"),
                                           native_run._RunState())
        for method in ("account/updated", "account/rateLimits/updated",
                       "remoteControl/status/changed", "deprecationNotice", "thread/started"):
            for params in ({"threadId": "foreign"}, {"threadId": None},
                           {"threadId": "thread", "turnId": "foreign"}):
                with self.subTest(method=method, params=params):
                    with self.assertRaises(CodexProtocolError) as caught:
                        carrier({"method": method, "params": params})
                    self.assertEqual(caught.exception.code, "invalid-native-result")

    def test_thread_started_checks_nested_session_identity(self):
        state = {"started": False, "final": None, "completed": None}
        carrier = native_run._FastProtocol("thread", "turn", state, TurnEvidence("thread", "turn"),
                                           native_run._RunState())
        for thread in ({"id": "foreign"}, {"id": ""}):
            with self.subTest(thread=thread):
                with self.assertRaises(CodexProtocolError) as caught:
                    carrier({"method": "thread/started", "params": {"thread": thread}})
                self.assertEqual(caught.exception.code, "invalid-native-result")
        for params in (None, [], {}, {"thread": None}, {"thread": []}, {"thread": {}},
                       {"thread": {"id": None}}, {"thread": {"id": 17}}, {"thread": {"id": "thread"}}):
            with self.subTest(params=params):
                carrier({"method": "thread/started", "params": params})
        self.assertEqual(state, {"started": False, "final": None, "completed": None})

    def test_shared_native_facts_reach_all_three_carrier_results_without_duplicate_keys(self):
        expected = {"unknownEvents": {"countsByType": {"account/updated": 1}, "total": 1},
                    "unconfirmed": ["settings-readback", "thread-receipt", "model-catalog"]}
        for mode, scope in (("fast", "none"), ("review", "read"), ("worker", "write")):
            with self.subTest(mode=mode):
                root = self.root / mode
                root.mkdir(mode=0o700)
                request = self.request.model_copy(update={"tool_scope": scope, "private_state": PrivateStatePaths(
                    invocation_root=str(root), native_root=str(root / "native"))})
                state = native_run._RunState(status="ok", raw_answer='{"answer": "retained"}', shutdown=True,
                    policy_checks={"configReadback": {"status": "unknown"},
                                   "threadReceipt": {"status": "unknown"}, "selectedModel": {"status": "unknown"}},
                    catalog_read={"discovery": {"status": "unknown",
                                                "reason": {"code": "model-list-shape-unreadable", "params": {}}},
                                  "warnings": []})
                facts = native_run._RunFacts()
                facts.note_unknown("account/updated")
                result = native_run._build_result(request, None, state, mode, None, facts,
                                                  SimpleNamespace(last_payload=None))
                self.assertEqual(result.native_evidence.to_payload(), expected)
                self.assertEqual(native_run._read_evidence_ref(result, "unknown-events"), expected["unknownEvents"])
                projection = native_run.native_evidence(result)
                self.assertEqual(projection["catalogRead"], state.catalog_read)
                self.assertEqual(projection["policyCheck"], state.policy_checks)
                self.assertNotIn("unknownEvents", projection)
                self.assertNotIn("unconfirmed", projection)
                verdict = {"stopReason": None, "elapsedMs": 1}
                if mode == "fast":
                    payload = run_execution._fast_result(result, request, verdict)
                elif mode == "review":
                    payload = run_execution._review_result(result, request, verdict)
                else:
                    payload = run_execution._worker_facts(result)
                self.assertEqual(payload["nativeEvidence"], expected)
                self.assertNotIn("unknownEvents", payload)
                self.assertNotIn("unconfirmed", payload)

    def test_confirmed_reads_and_unreadable_selected_model_add_no_unconfirmed_name(self):
        state = native_run._RunState(policy_checks={"configReadback": {"status": "confirmed"},
                                                   "threadReceipt": {"status": "confirmed"},
                                                   "selectedModel": {"status": "unknown"}},
                                     catalog_read={"discovery": {"status": "complete"}, "warnings": []})
        result = native_run._build_result(self.request, None, state, "fast", None, native_run._RunFacts(),
                                          SimpleNamespace(last_payload=None))
        self.assertEqual(result.native_evidence.unconfirmed, ())
        self.assertIsNone(result.native_evidence.unknown_events)
        self.assertNotIn("nativeEvidence", run_execution._base_result(result))


if __name__ == "__main__":
    unittest.main()
