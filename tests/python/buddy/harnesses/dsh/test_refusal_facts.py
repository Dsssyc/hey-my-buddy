"""DSH refusal boundaries through the run, without spawning a native process.

Only transport and owned process observations are simulated. The real ACP
client, configuration selection, prompt/update pump and result assembly run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.dsh.acp.client import AcpClient
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpRequestError
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE, RunContinuation
from hey_my_buddy.errors import BoardError

from .test_native_run import NativeRunCase


def options():
    return [{"id": "model", "currentValue": '["fake","m1"]',
             "options": [{"options": [{"value": '["fake","m1"]'}]}]},
            {"id": "reasoning_effort", "currentValue": "high",
             "options": [{"value": "high"}]}]


class RefusalFactsTests(NativeRunCase):
    def setUp(self):
        super().setUp()
        self.initialize_value = {"agentInfo": {"version": "0.0.1"}}
        self.session_value = {"sessionId": "session-1", "configOptions": options()}
        self.echoes = {"model": {"configOptions": options()},
                       "reasoning_effort": {"configOptions": options()}}
        self.answer = '{"choice":"a"}'
        self.stop_reason = "end_turn"
        self.callbacks = []
        self.methods = []
        self.connection = mock.Mock()
        self.connection.observe.side_effect = self.callbacks.append
        self.connection.request.side_effect = self.request
        self.connection.facts.return_value = {}
        self.connection.stderr_text.return_value = ""
        self.connection.frame_log = None
        self.client = AcpClient(mock.Mock(returncode=0), mock.Mock(), self.connection)
        self.shutdown = {"shutdownConfirmed": True, "groupObserved": "gone",
                         "leaderExited": True, "leaderExitCode": 0}
        self.client.shutdown = mock.Mock(side_effect=lambda **_kwargs: self.shutdown)
        patcher = mock.patch.object(native_run, "_launch_agent", return_value=self.client)
        self.launch = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, method, params, timeout):
        self.methods.append(method)
        if method == "initialize":
            return copy.deepcopy(self.initialize_value)
        if method in ("session/new", "session/resume"):
            return copy.deepcopy(self.session_value)
        if method == "session/set_config_option":
            response = self.echoes[params["configId"]]
            if isinstance(response, Exception):
                raise response
            return copy.deepcopy(response)
        if method == "session/prompt":
            message = {"method": "session/update", "params": {
                "sessionId": params["sessionId"], "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": self.answer}}}}
            for callback in self.callbacks:
                callback(message)
            return {"stopReason": self.stop_reason}
        if method == "session/close":
            return {}
        self.fail(f"unexpected native method {method}")

    def run_final(self, **kwargs):
        return native_run.run(self.fast_request("prompt", **kwargs),
                              observer=lambda _facts: FEEDBACK_CONTINUE,
                              services=None, cancelled=lambda: False)

    def native_facts(self, result):
        ref = next(ref for ref in result.evidence_refs if ref.kind == "dsh-native-facts")
        raw = Path(ref.location).read_bytes()
        self.assertEqual(ref.size_bytes, len(raw))
        self.assertEqual(ref.sha256, hashlib.sha256(raw).hexdigest())
        return json.loads(raw)

    def assert_continued(self, result):
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, self.answer)
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertIn("session/prompt", self.methods)
        self.assertIn("session/close", self.methods)
        self.client.shutdown.assert_called_once()

    def test_unknown_initialize_shape_continues_with_redacted_handshake_fact(self):
        for value in (None, [], "private-secret"):
            with self.subTest(value=value):
                self.initialize_value = value
                self.client.shutdown.reset_mock()
                result = self.run_final()
                self.assert_continued(result)
                self.assertIsNone(result.harness_version)
                fact = self.native_facts(result)["initialize"]
                self.assertEqual(fact["status"], "unknown")
                self.assertEqual(fact["shape"], type(value).__name__)
                self.assertNotIn("private-secret", json.dumps(fact))

    def test_unknown_config_options_continues_without_claiming_checked_values(self):
        for value in (None, {"private": "secret"}, "new-selector-shape"):
            with self.subTest(value=value):
                self.session_value["configOptions"] = value
                self.client.shutdown.reset_mock()
                self.methods.clear()
                result = self.run_final()
                self.assert_continued(result)
                self.assertEqual(result.configuration.checked.model_dump(),
                                 {"provider": None, "model": None, "effort": None})
                fact = self.native_facts(result)["configOptions"]
                self.assertEqual(fact["status"], "unknown")
                self.assertNotIn("secret", json.dumps(fact))
                self.assertNotIn("session/set_config_option", self.methods)

    def test_unknown_agent_info_shape_is_retained_without_failing_handshake(self):
        self.initialize_value = {"agentInfo": "private-secret"}
        result = self.run_final()
        self.assert_continued(result)
        self.assertIsNone(result.harness_version)
        fact = self.native_facts(result)["agentInfo"]
        self.assertEqual(fact["status"], "unknown")
        self.assertNotIn("private-secret", json.dumps(fact))

    def test_unknown_model_readback_continues_and_only_effort_is_checked(self):
        for response in (None, {"configOptions": {}}, {"configOptions": []},
                         {"configOptions": [{"id": "model", "currentValue": {"new": "shape"}}]}):
            with self.subTest(response=response):
                self.echoes["model"] = response
                self.client.shutdown.reset_mock()
                result = self.run_final()
                self.assert_continued(result)
                self.assertIsNone(result.configuration.checked.provider)
                self.assertIsNone(result.configuration.checked.model)
                self.assertEqual(result.configuration.checked.effort.value, "high")
                self.assertEqual(self.native_facts(result)["modelReadback"]["status"], "unknown")

    def test_unknown_effort_readback_continues_and_model_stays_checked(self):
        self.echoes["reasoning_effort"] = {"configOptions": "new-shape"}
        result = self.run_final()
        self.assert_continued(result)
        self.assertEqual(result.configuration.checked.provider.value, "fake")
        self.assertEqual(result.configuration.checked.model.value, "m1")
        self.assertIsNone(result.configuration.checked.effort)
        self.assertEqual(self.native_facts(result)["effortReadback"]["status"], "unknown")

    def test_confirmed_different_model_still_refuses_before_prompt(self):
        self.echoes["model"]["configOptions"][0]["currentValue"] = '["fake","m2"]'
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "configuration-mismatch")
        self.assertNotIn("session/prompt", self.methods)

    def test_confirmed_different_effort_still_refuses_before_prompt(self):
        self.echoes["reasoning_effort"]["configOptions"][1]["currentValue"] = "low"
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "configuration-mismatch")
        self.assertNotIn("session/prompt", self.methods)

    def test_native_configuration_refusal_is_not_an_unknown_shape(self):
        self.echoes["model"] = AcpRequestError("session/set_config_option", {"code": -1})
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "configuration-unavailable")
        self.assertNotIn("session/prompt", self.methods)

    def test_recognized_catalog_without_requested_model_still_refuses(self):
        self.session_value["configOptions"][0]["options"] = []
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "configuration-unavailable")
        self.assertNotIn("session/prompt", self.methods)

    def test_recognized_catalog_without_requested_effort_still_refuses(self):
        self.session_value["configOptions"][1]["options"] = []
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "invalid-configuration")
        self.assertNotIn("session/prompt", self.methods)

    def test_unconfirmed_tool_rows_continue_with_actual_patch_and_unknown_fact(self):
        with mock.patch.object(native_run, "NONE_SCOPE_DISABLED_ROWS", ()):
            result = self.run_final()
        self.assert_continued(result)
        self.assertEqual(self.launch.call_args.kwargs["scope_rows"],
                         [{"id": "plan-mode", "disabled": True}])
        self.assertEqual(result.effective_policy.tools.requested.value,
                         {"disabledRows": ["plan-mode"]})
        self.assertEqual(self.native_facts(result)["toolScope"], {
            "status": "unknown", "reason": "scope-configuration-unconfirmed",
            "disabledRows": ["plan-mode"]})

    def test_final_answer_byte_limit_still_refuses(self):
        self.answer = "x" * (native_run._MAX_ANSWER_BYTES + 1)
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "answer-too-large")
        self.assertEqual(result.end.status, "error")
        self.assertIsNone(result.value)

    def test_final_answer_at_byte_limit_continues(self):
        self.answer = "x" * native_run._MAX_ANSWER_BYTES
        self.assert_continued(self.run_final())

    def test_unconfirmed_group_stop_still_refuses_after_unknown_handshake(self):
        self.initialize_value = []
        self.shutdown = {"shutdownConfirmed": False, "groupObserved": "unknown",
                         "leaderExited": True, "leaderExitCode": 0}
        with mock.patch.object(native_run, "stop_evidence", return_value=self.shutdown):
            result = self.run_final()
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertEqual(self.native_facts(result)["initialize"]["status"], "unknown")

    def test_missing_root_session_still_refuses_after_unknown_handshake(self):
        self.initialize_value = []
        self.session_value["sessionId"] = None
        result = self.run_final()
        self.assertEqual(result.end.reason_code, "wrong-native-session")
        self.assertNotIn("session/prompt", self.methods)

    def test_missing_stop_reason_ends_unknown_after_close_and_stop(self):
        self.stop_reason = None
        result = self.run_final()
        self.assertEqual(result.end.status, "unknown")
        self.assertEqual(result.end.reason_code, native_run.UNREAD_STOP_REASON)
        self.assertIsNone(result.value, "an unreadable turn end owes no settled answer")
        self.assertIsNone(result.completion_evidence)
        self.assertIsNone(result.continuation, "an unknown end establishes no resumable binding")
        self.assertFalse(result.tool_evidence.value["streamComplete"])
        self.assertIn("session/close", self.methods)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.client.shutdown.assert_called_once()

    def test_unknown_catalog_shape_reports_an_unknown_read_with_reason(self):
        self.session_value["configOptions"] = {}
        catalog = native_run.run_discovery(cwd=str(self.base / "cwd"),
                                           invocation_root=self.base / "discovery",
                                           native_root=self.root, timeout_seconds=30, cancelled=lambda: False)
        self.assertEqual(catalog["discoveries"],
                         [{"adapter": "dsh", "status": "unknown", "accountStatus": "not-applicable",
                           "reason": native_run.UNREAD_CATALOG_REASON}])
        self.assertEqual(catalog["providers"], [])
        self.assertTrue(any("configuration options had an unrecognized shape" in warning
                            for warning in catalog["warnings"]), catalog["warnings"])
        self.assertNotIn("session/prompt", self.methods)
        self.assertIn("session/close", self.methods)
        self.assertEqual(self.client.shutdown.call_count, 1)

    def test_unread_catalog_shape_still_refuses_an_unconfirmed_stop(self):
        self.session_value["configOptions"] = "new-selector-shape"
        self.shutdown = {"shutdownConfirmed": False, "groupObserved": "unknown",
                         "leaderExited": True, "leaderExitCode": 0}
        with mock.patch.object(native_run, "stop_evidence", return_value=self.shutdown):
            with self.assertRaises(native_run.NativeError) as caught:
                native_run.run_discovery(cwd=str(self.base / "cwd"),
                                         invocation_root=self.base / "discovery",
                                         native_root=self.root, timeout_seconds=30,
                                         cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "native-shutdown-failed")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, False)

    def test_unread_shapes_reach_the_shared_outlet_as_unknown_events(self):
        self.initialize_value = []
        self.session_value["configOptions"] = {}
        result = self.run_final()
        self.assert_continued(result)
        evidence = result.native_evidence
        self.assertEqual(evidence.unknown_events.counts_by_type,
                         (("initialize", 1), ("configOptions", 1)))
        self.assertEqual(evidence.unknown_events.total, 2)
        self.assertEqual(evidence.unconfirmed, ())

    def test_unconfirmed_readback_and_tool_scope_reach_the_shared_outlet(self):
        self.echoes["model"] = {"configOptions": []}
        with mock.patch.object(native_run, "NONE_SCOPE_DISABLED_ROWS", ()):
            result = self.run_final()
        self.assert_continued(result)
        evidence = result.native_evidence
        self.assertEqual(evidence.unknown_events.counts_by_type, (("modelReadback", 1),))
        self.assertEqual(evidence.unconfirmed,
                         (native_run.UNCONFIRMED_TOOL_SCOPE,
                          native_run.UNCONFIRMED_CONFIGURATION_READBACK))
        # The unknown-event kind names and the unconfirmed names stay the bounded
        # vocabulary: no payload, path or credential travels in either.
        rendered = json.dumps(evidence.to_payload())
        self.assertNotIn("private", rendered)
        self.assertNotIn("/", rendered)

    def test_the_three_role_results_carry_the_shared_native_facts(self):
        from hey_my_buddy.buddy.roles import run_execution

        self.initialize_value = []
        request = self.fast_request("prompt")
        result = native_run.run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                                services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        facts = result.native_evidence.to_payload()
        self.assertEqual(facts["unknownEvents"],
                         {"countsByType": {"initialize": 1}, "total": 1})
        verdict = {"stopReason": None, "elapsedMs": 1}
        payloads = (run_execution._fast_result(result, request, verdict),
                    run_execution._review_result(result, request, verdict),
                    run_execution._worker_facts(result))
        for payload in payloads:
            self.assertEqual(payload["nativeEvidence"]["unknownEvents"], facts["unknownEvents"])

    def test_discovery_keeps_unknown_handshake_fact_without_prompt(self):
        self.initialize_value = []
        catalog = native_run.run_discovery(cwd=str(self.base / "cwd"),
                                           invocation_root=self.base / "discovery",
                                           native_root=self.root, timeout_seconds=30, cancelled=lambda: False)
        self.assertEqual(catalog["harnessVersion"], "unknown")
        self.assertTrue(any('"initialize"' in warning and '"status": "unknown"' in warning
                            for warning in catalog["warnings"]))
        self.assertNotIn("session/prompt", self.methods)
        self.assertIn("session/close", self.methods)

    def test_request_boundary_rejects_relative_cwd_and_invalid_scope(self):
        request = self.fast_request("prompt")
        fields = {name: getattr(request, name) for name in type(request).model_fields}
        for field, invalid in (("cwd", "relative"), ("tool_scope", "invalid")):
            with self.subTest(field=field), self.assertRaises(BoardError):
                type(request)(**{**fields, field: invalid})
        self.launch.assert_not_called()

    def test_continuation_boundary_rejects_invalid_previous_session(self):
        # The request boundary is the one place a blank previous session
        # identity is refused, for both continuation modes; the run module no
        # longer repeats that check.
        for value in (None, "", 42, "   ", "\t\n"):
            with self.subTest(value=value), self.assertRaises(BoardError):
                RunContinuation(mode="native-session", previous_session_id=value)
        with self.assertRaises(BoardError):
            RunContinuation(mode="reconstructed-new-session", previous_session_id="   ")
        self.assertEqual(RunContinuation(mode="reconstructed-new-session",
                                         previous_session_id=None).previous_session_id, None)

    def test_client_transmits_boundary_validated_cwd_for_new_and_resume(self):
        cwd = str(self.base / "cwd")
        self.client.new_session(cwd)
        self.client.resume_session("session-1", cwd)
        self.assertEqual(self.connection.request.call_args_list[-2].args[1]["cwd"], cwd)
        self.assertEqual(self.connection.request.call_args_list[-1].args[1]["cwd"], cwd)

    def test_continuation_projection_uses_validated_modes_and_identities(self):
        request = self.fast_request("prompt")
        fields = {name: getattr(request, name) for name in type(request).model_fields}
        for mode, previous in (("native-session", "session-1"),
                               ("reconstructed-new-session", None)):
            continuation = RunContinuation(mode=mode, previous_session_id=previous)
            projected = native_run._continuation_of(type(request)(**{**fields, "continuation": continuation}))
            self.assertEqual(projected, (mode, previous))
