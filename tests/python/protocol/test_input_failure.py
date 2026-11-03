"""The failure envelope is bounded and preserves the measured source facts."""
from copy import deepcopy
import unittest

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.capabilities import InputLimit
from hey_my_buddy.protocol.input_failure import input_failure, input_failure_view, normalize_input_failure
from hey_my_buddy.protocol.input_limit import PromptParts, check_prompt_input


def failure(*, unknown=False):
    fields = dict(form="cli-app-server", scope="turn-start-text", source="synthetic")
    limit = (InputLimit.unknown(**fields) if unknown else
             InputLimit.known(**fields, value=10, unit="unicode-scalars", versions=("0.164.0",)))
    check = check_prompt_input(limit, PromptParts("角色", '😀\\"\n', "输入", "上下文", "交付", "格式"))
    return input_failure(check, stage="native-admission" if unknown else "preflight", harness="codex",
                         version=None if unknown else "0.164.0", native_request_sent=unknown, model_started=False)


class InputFailureTests(unittest.TestCase):
    def test_known_and_unknown_roundtrip_through_the_native_result(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                value = failure(unknown=unknown)
                self.assertEqual(normalize_input_failure(value), value)
                result = rc.RunResult(identity=rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                    invocation_id="invocation"), harness="codex", end=rc.RunEnd(status="error"), input_failure=value)
                wire = result.to_payload()
                self.assertEqual(rc.RunResult.from_payload(wire).input_failure.value, value)
                self.assertEqual(input_failure_view({"inputFailure": value}), value)
                self.assertEqual(input_failure_view({"result": {"inputFailure": value}}), value)
                self.assertIsNone(input_failure_view(None))
                self.assertIsNone(input_failure_view({"result": {}}))

    def test_false_or_ambiguous_sending_and_model_facts_do_not_become_stop_proof(self):
        value = {**failure(unknown=True), "nativeRequestSent": None, "modelStarted": None}
        self.assertEqual(normalize_input_failure(value), value)
        result = rc.RunResult(identity=rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1,
            invocation_id="invocation"), harness="codex", end=rc.RunEnd(status="error"), input_failure=value,
            stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="unknown")))
        self.assertEqual(rc.RunResult.from_payload(result.to_payload()).stop_evidence.native.group_state, "unknown")

    def test_result_boundary_rejects_bad_sums_fabricated_unknown_limits_and_unbounded_native_message(self):
        bad_sum = deepcopy(failure())
        bad_sum["measurement"]["parts"]["task"] += 1
        bad_unknown = deepcopy(failure(unknown=True))
        bad_unknown["limit"]["value"] = 0
        bad_bytes = deepcopy(failure(unknown=True))
        bad_bytes["measurement"]["utf8Bytes"] += 1
        bad_preflight = {**failure(), "nativeRequestSent": True}
        bad_model = {**failure(), "modelStarted": 0}
        long_message = {**failure(unknown=True), "message": "中" * 171}
        for value in (bad_sum, bad_unknown, bad_bytes, bad_preflight, bad_model, long_message,
                      {**failure(), "extra": 1}):
            with self.subTest(value=value):
                self.assertIsNone(normalize_input_failure(value))
                with self.assertRaises(BoardError):
                    rc.RunResult(identity=rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                        invocation_id="invocation"), harness="codex", end=rc.RunEnd(status="error"), input_failure=value)

    def test_bounded_native_fields_and_empty_observed_version_are_preserved(self):
        value = {**failure(unknown=True), "harness": {"name": "codex", "form": "cli-app-server", "version": ""},
                 "nativeMethod": "turn/start", "nativeCode": -32602, "input_error_code": "input_too_large",
                 "max_chars": 10, "actual_chars": 11, "message": ""}
        self.assertEqual(normalize_input_failure(value), value)


if __name__ == "__main__":
    unittest.main()
