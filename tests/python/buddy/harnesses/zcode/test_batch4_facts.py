"""Batch 4 facts and guarded boundaries, using private in-process/native fixtures."""
import json
import os
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from pydantic import ValidationError

from buddy.harnesses.zcode.test_native_run import NativeRunCase
from hey_my_buddy.buddy.harnesses.run_contract import (
    MAX_UNKNOWN_EVENT_TYPES, UNLISTED_NATIVE_EVENTS,
    RunContinuation, RunFeedback, RunBuddy,
)
from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError
from hey_my_buddy.errors import BoardError


class NoToolShapeTests(unittest.TestCase):
    def event(self, kind, payload, *, seq=1, session="root", turn="turn"):
        return {"method": "session/event", "params": {"type": kind, "payload": payload,
            "sessionId": session, "turnId": turn, "seq": seq}}

    def start(self):
        facts = native_run._RunFacts()
        protocol = native_run.NoToolProtocol("root", "input", on_unknown=facts.note_unknown)
        protocol.observe(self.event("turn.started", {"inputId": "input"}), 1)
        return protocol, facts

    def test_notification_shape_continues_with_fact(self):
        protocol, facts = self.start()
        protocol.observe({"method": "process/resourceSample", "params": ["secret"]}, 2)
        self.assertEqual(facts.unknown_counts, {"notification-parameters-unrecognized": 1})
        self.assertFalse(protocol.completed)

    def test_event_payload_shape_continues_with_fact(self):
        protocol, facts = self.start()
        protocol.observe(self.event("message.upserted", ["secret"], seq=2), 2)
        self.assertEqual(facts.unknown_counts, {"event-payload-unrecognized": 1})
        self.assertEqual(protocol.events, 2)

    def test_unknown_answer_shape_is_unknown_and_settlement_continues(self):
        protocol, facts = self.start()
        protocol.observe(self.event("turn.completed", {
            "inputId": "input", "resultType": "success", "response": ["secret"]}, seq=2), 2)
        protocol.observe({"method": "state.updated", "params": {
            "sessionId": "root", "reason": "prompt_completed"}}, 3)
        self.assertTrue(protocol.settled)
        self.assertIsNone(protocol.raw_answer)
        self.assertEqual(facts.unknown_counts, {"final-answer-unrecognized": 1})

    def test_completion_identity_failure_and_size_limit_still_reject(self):
        for payload, code in (({"inputId": "other", "resultType": "success"}, "wrong-native-turn"),
                              ({"inputId": "input", "resultType": "error"}, "native-turn-failed"),
                              ({"inputId": "input", "resultType": "success", "response": "界" * 22000}, "invalid-native-result")):
            with self.subTest(code=code):
                protocol, _facts = self.start()
                with self.assertRaises(NativeError) as error:
                    protocol.observe(self.event("turn.completed", payload, seq=2), 2)
                self.assertEqual(error.exception.code, code)
        protocol, _facts = self.start()
        payload = {"inputId": "input", "resultType": "success", "response": "ok"}
        protocol.observe(self.event("turn.completed", payload, seq=2), 2)
        with self.assertRaises(NativeError) as error:
            protocol.observe(self.event("turn.completed", payload, seq=3), 3)
        self.assertEqual(error.exception.code, "wrong-native-turn")

    def test_metadata_identity_and_sequence_still_reject(self):
        for params in ({"sessionId": "foreign", "turnId": "turn", "kind": "future", "eventSeq": 1},
                       {"sessionId": "root", "turnId": "foreign", "kind": "future", "eventSeq": 1},
                       {"sessionId": "root", "turnId": "turn", "kind": "future", "eventSeq": False}):
            protocol, _facts = self.start()
            with self.subTest(params=params), self.assertRaises(NativeError):
                protocol.observe({"method": "v4/telemetry/event", "params": params}, 2)

    def test_unknown_session_metadata_without_turn_identity_is_a_fact(self):
        protocol, facts = self.start()
        protocol.observe({"method": "v4/telemetry/event", "params": {
            "sessionId": "root", "kind": "future", "eventSeq": 1}}, 2)
        self.assertEqual(facts.unknown_counts, {"metadata-kind-unrecognized": 1})
        self.assertFalse(protocol.completed)

    def test_missing_lifecycle_parameters_and_payload_still_cannot_prove_identity(self):
        protocol, _facts = self.start()
        with self.assertRaises(NativeError):
            protocol.observe({"method": "session/event", "params": []}, 2)
        with self.assertRaises(NativeError):
            protocol.observe(self.event("turn.completed", [], seq=2), 2)


class BoundaryAndDrainTests(unittest.TestCase):
    def test_known_missing_model_and_effort_capabilities_still_reject(self):
        spec = {"provider": "api", "model": "model", "effort": "low"}
        for choices, code in (([], "buddy-unavailable"),
                              ([{"ref": {"providerId": "api", "modelId": "model"},
                                 "reasoning": {"levels": [{"value": "high"}]}}], "invalid-buddy")):
            with self.subTest(code=code):
                connection = mock.Mock()
                snapshot = {"session": {"sessionId": "root"}, "settings": {"model": {"available": choices}}}
                with self.assertRaises(NativeError) as error:
                    native_run.configure_session(connection, snapshot, spec)
                self.assertEqual(error.exception.code, code)
                connection.call.assert_not_called()

    def test_a_readable_target_row_is_judged_on_its_own(self):
        spec = {"provider": "api", "model": "model", "effort": "low"}
        refusing = {"ref": {"providerId": "api", "modelId": "model"},
                    "reasoning": {"levels": [{"value": "high"}]}}
        supporting = {"ref": {"providerId": "api", "modelId": "model"},
                      "reasoning": {"levels": [{"value": "low"}]}}
        other = {"ref": {"providerId": "api", "modelId": "other"},
                 "reasoning": {"levels": [{"value": "low"}]}}
        unreadable = {"ref": []}
        with self.subTest(case="target-effort-refused-despite-an-unreadable-row"):
            connection = mock.Mock()
            snapshot = {"session": {"sessionId": "root"},
                        "settings": {"model": {"available": [refusing, unreadable]}}}
            with self.assertRaises(NativeError) as error:
                native_run.configure_session(connection, snapshot, spec)
            self.assertEqual(error.exception.code, "invalid-buddy")
            connection.call.assert_not_called()
        with self.subTest(case="target-supported-effort-continues-with-the-fact"):
            unknown = []
            connection = mock.Mock()
            connection.call.side_effect = [{}, {}]
            snapshot = {"session": {"sessionId": "root"},
                        "settings": {"model": {"available": [supporting, unreadable]}}}
            result = native_run.configure_session(connection, snapshot, spec, note_unknown=unknown.append)
            self.assertEqual([call.args[0] for call in connection.call.call_args_list],
                             ["session/setModel", "session/setThoughtLevel"])
            self.assertIn("buddy-catalog-unrecognized", unknown)
            self.assertEqual(result, {"provider": None, "model": None, "effort": None})
        with self.subTest(case="readable-catalog-without-the-target-refuses"):
            connection = mock.Mock()
            snapshot = {"session": {"sessionId": "root"},
                        "settings": {"model": {"available": [other]}}}
            with self.assertRaises(NativeError) as error:
                native_run.configure_session(connection, snapshot, spec)
            self.assertEqual(error.exception.code, "buddy-unavailable")
            connection.call.assert_not_called()

    def test_blank_buddy_fields_are_refused_at_the_request_boundary(self):
        for key in ("provider", "model", "effort"):
            spec = {"provider": "api", "model": "model", "effort": "low", key: " \t"}
            with self.subTest(key=key), self.assertRaises(BoardError):
                RunBuddy(**spec)

    def test_continuation_boundary_rejects_invalid_mode_and_missing_session(self):
        for spec in ({"mode": "other"}, {"mode": "native-session"},
                     {"mode": "native-session", "previous_session_id": ""},
                     {"mode": "native-session", "previous_session_id": " \t"}):
            with self.subTest(spec=spec), self.assertRaises((BoardError, ValidationError)):
                RunContinuation(**spec)
        request = types.SimpleNamespace(continuation=RunContinuation(
            mode="native-session", previous_session_id="root"))
        self.assertEqual(native_run._continuation_of(request), ("native-session", "root"))

    def test_unknown_event_types_stop_growing_at_the_cap_and_keep_the_total(self):
        facts = native_run._RunFacts()
        for index in range(70):
            facts.note_unknown(f"future-method-{index}")
        self.assertEqual(len(facts.unknown_counts), MAX_UNKNOWN_EVENT_TYPES)
        self.assertEqual(sum(facts.unknown_counts.values()), 70)
        self.assertEqual(facts.unknown_counts[UNLISTED_NATIVE_EVENTS], 7)
        facts.note_unknown("future-method-0")
        self.assertEqual(len(facts.unknown_counts), MAX_UNKNOWN_EVENT_TYPES)
        self.assertEqual(sum(facts.unknown_counts.values()), 71)

    def test_the_evidence_file_and_the_shared_outcome_carry_the_same_bounded_counts(self):
        facts = native_run._RunFacts()
        for index in range(70):
            facts.note_unknown(f"future-method-{index}")
        with tempfile.TemporaryDirectory(prefix="buddy-zcode-facts-") as root:
            invocation = native_run.ensure_private_dir(Path(root))
            refs = native_run._evidence_refs(invocation_root=invocation,
                                             stderr_path=invocation / "missing-stderr.log",
                                             state=native_run._RunState(), facts=facts, record=None)
            ref = next(item for item in refs if item.kind == "native-run-facts")
            retained = json.loads(Path(ref.location).read_text())["unknownEvents"]
        shared = native_run._shared_unknown_events(facts)
        self.assertEqual(len(retained["countsByType"]), MAX_UNKNOWN_EVENT_TYPES)
        self.assertEqual(retained["countsByType"], dict(shared.unknown_events.counts_by_type))
        self.assertEqual(retained["total"], shared.unknown_events.total)

    def drain(self, message):
        connection = types.SimpleNamespace(messages=queue.Queue())
        connection.messages.put(message)
        connection.messages.put(None)
        facts = native_run._RunFacts()
        state = native_run._RunState(status="ok")
        seen = []
        complete = native_run._drain(connection, mock.Mock(), native_run._Classifier(facts),
            mock.Mock(), facts, state, lambda: seen.append(dict(facts.unknown_counts)))
        return complete, facts, state, seen

    def test_late_interaction_continues_drain_and_reaches_observer(self):
        complete, facts, state, seen = self.drain({"id": "late", "method": "future/interaction", "params": {}})
        self.assertTrue(complete)
        self.assertEqual(state.status, "ok")
        self.assertEqual(facts.unknown_counts["interaction-after-settlement"], 1)
        self.assertIn("interaction-after-settlement", seen[-1])

    def test_unclaimed_response_still_rejects(self):
        complete, _facts, state, _seen = self.drain({"id": "foreign", "result": {}})
        self.assertFalse(complete)
        self.assertEqual(state.reason, "invalid-protocol")

    def test_unrecognized_catalog_is_unknown_not_complete_empty(self):
        for snapshot in ({}, {"settings": None}, {"settings": {"model": {"available": "secret"}}},
                         {"settings": {"model": {"available": [None, {"ref": []}]}}}):
            with self.subTest(snapshot=snapshot):
                receipt = native_run.catalog(snapshot, {}, "fixture")
                self.assertEqual(receipt["discoveries"][0]["status"], "unknown")
                self.assertEqual(receipt["providers"], [])
                self.assertNotIn("secret", json.dumps(receipt))
        receipt = native_run.catalog({"settings": {"model": {"available": []}}}, {}, "fixture")
        self.assertEqual(receipt["discoveries"][0]["status"], "complete")


class NativeShapeRoundTests(NativeRunCase):
    def test_unrecognized_catalog_read_continues_without_a_model_prompt(self):
        original = native_run.NativeConnection.call
        def call(connection, method, params):
            result = original(connection, method, params)
            if method == "session/create":
                result["settings"]["model"]["available"] = None
            return result
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "discovery"
        with mock.patch.object(native_run.NativeConnection, "call", call):
            receipt = native_run.run_discovery(cwd=str(self.cwd), invocation_root=self.base / "reading",
                native_root=self.base / "reading-native", timeout_seconds=3, cancelled=lambda: False)
        self.assertEqual(receipt["discoveries"][0]["status"], "unknown")
        self.assertEqual(receipt["providers"], [])
        self.assertTrue(any("not fully recognized" in warning for warning in receipt["warnings"]))

    def execute_shape(self, rewrite):
        self.fake.write_text(rewrite(self.fake.read_text()))
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        seen = []
        def observer(facts):
            seen.append(facts)
            return RunFeedback(action="continue")
        result = native_run.run(self.fast_request("Return only one JSON value matching this schema: {}"), observer=observer,
                                services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        ref = next(ref for ref in result.evidence_refs if ref.kind == "native-run-facts")
        retained = json.loads(Path(ref.location).read_text())["unknownEvents"]["countsByType"]
        self.assertTrue(any(f["unknownEvents"]["total"] for f in seen))
        # The shared outlet carries the same counts the evidence file retains.
        shared = result.native_evidence.unknown_events
        self.assertEqual(dict(shared.counts_by_type), retained)
        self.assertEqual(shared.total, sum(retained.values()))
        self.assertEqual(result.native_evidence.unconfirmed, ())
        self.assertNotIn("evidenceRefs", native_run.native_evidence(result))
        return result, retained

    def test_unknown_native_selection_continues_with_result_and_observer_facts(self):
        result, facts = self.execute_shape(lambda source: source.replace(
            "'providerId':provider,'modelId':'fixture-model',\n          'options':{'reasoningLevel':'low'}",
            "'unrecognized':'secret'"))
        self.assertIn("buddy-selection-unrecognized", facts)
        self.assertIsNone(result.buddy.checked.model)
        self.assertIsNone(result.buddy.checked.effort)

    def test_rpc_shape_continues_with_result_and_observer_facts(self):
        _result, facts = self.execute_shape(lambda source: source.replace(
            "send({'id':call['id'],'result':result})",
            "send({'id':call['id'],'result':None if method == 'session/subscribe' else result})"))
        self.assertIn("rpc-result/session/subscribe/null", facts)

    def test_notification_and_event_payload_shapes_continue_with_result_facts(self):
        _result, facts = self.execute_shape(lambda source: source.replace(
            "event('turn.started',1,{'inputId':ident})",
            "event('turn.started',0,{'inputId':ident})\n        send({'method':'process/resourceSample','params':[]})\n        event('message.upserted',1,[])"))
        self.assertIn("notification-parameters-unrecognized", facts)
        self.assertIn("event-payload-unrecognized", facts)

    def test_confirmed_readback_mismatch_is_rejected_before_model_start(self):
        self.fake.write_text(self.fake.read_text().replace(
            "'providerId':provider,'modelId':'fixture-model',\n          'options':{'reasoningLevel':'low'}",
            "'providerId':provider,'modelId':'observed-model',\n          'options':{'reasoningLevel':'low'}"))
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
        original = native_run.NativeConnection.call
        calls = []
        def call(connection, method, params):
            calls.append(method)
            return original(connection, method, params)
        with mock.patch.object(native_run.NativeConnection, "call", call):
            result = native_run.run(self.fast_request("Return only one JSON value matching this schema: {}"),
                observer=lambda _facts: RunFeedback(action="continue"), services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "buddy-mismatch")
        self.assertIsNone(result.model_started)
        self.assertIsNone(result.buddy.checked.model)
        self.assertIsNone(result.completion_evidence)
        self.assertNotIn("session/send", calls)

    def test_unknown_final_answer_continues_with_unknown_value(self):
        result, facts = self.execute_shape(lambda source: source.replace("'response':answer", "'response':[]"))
        self.assertIsNone(result.value.raw)
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertIn("final-answer-unrecognized", facts)

    def test_admission_session_mismatch_and_real_refusal_still_reject(self):
        source = self.fake.read_text()
        for result, code in (("{'accepted':True,'sessionId':'foreign'}", "wrong-native-session"),
                             ("{'accepted':False,'sessionId':f's-{session}'}", "native-admission-failed")):
            with self.subTest(code=code):
                self.fake.write_text(source.replace("{'accepted':True,'sessionId':f's-{session}'}", result))
                os.environ["BUDDY_ZCODE_TEST_CASE"] = "ok"
                outcome = native_run.run(self.fast_request("Return only one JSON value matching this schema: {}"),
                    observer=lambda _facts: RunFeedback(action="continue"), services=None, cancelled=lambda: False)
                self.assertEqual(outcome.end.reason_code, code)
                self.assertIsNone(outcome.completion_evidence)


if __name__ == "__main__":
    unittest.main()
