"""The ADR-025 run contract: frozen values and payload packages.

Synthetic fixtures only: no harness or model is started. The Codex payload
fixture carries exactly the field names the current controller writes, so the
tool-evidence tests pin how much evidence an existing package honestly holds.
The bounds of the internal values hold the old allowed sets: a 300k input,
a 70k raw value and an empty-rooted incomplete tool-evidence package pass.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

from pydantic import ValidationError

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.errors import BoardError
from hey_my_buddy import paths as local_paths
from hey_my_buddy.protocol import tool_evidence

STATE_DIR = Path(os.environ["HMB_STATE_DIR"])

FIXTURES = Path(__file__).parent / "fixtures" / "run_contract"
def fixture_payloads():
    payloads = json.loads((FIXTURES / "codex-payloads.json").read_text(encoding="utf-8"))
    for payload in payloads["payloads"].values():
        if "toolEvidence" in payload:
            package = payload["toolEvidence"]
            reference = tool_evidence.write_tool_calls(package["binding"], [], state_dir=STATE_DIR)
            assert reference["sha256"] == package["calls"]["sha256"]
            assert reference["bytes"] == package["calls"]["bytes"]
            package["calls"] = reference
    return {"codex": payloads}


PAYLOADS = fixture_payloads()

IDENTITY = {"taskId": "task-fixture", "attemptId": "attempt-fixture", "generation": 1,
            "invocationId": "invocation-fixture", "turnId": "turn-fixture", "inputSha256": "a" * 64}


def identity() -> rc.RunIdentity:
    return rc.RunIdentity.from_payload(dict(IDENTITY))


def edited(request: rc.RunRequest, **changes: object) -> rc.RunRequest:
    """One revalidated request with the named snake_case fields replaced."""
    fields = dict(request.__dict__)
    fields.update(changes)
    return rc.RunRequest(**fields)


def full_request(tmp: Path) -> rc.RunRequest:
    return rc.RunRequest(
        identity=identity(), harness="zcode",
        buddy=rc.RunBuddy(provider="fixture-provider", model="fixture-model", effort="off"),
        cwd=str(tmp / "checkout"), private_state=rc.PrivateStatePaths(state_dir=str(tmp), invocation_root=str(tmp / "inv"),
                                                                      native_root=str(tmp / "native")),
        session_record_path=str(tmp / "session.jsonl"),
        input_text="one bounded role-assembled input", permission="read",
        output_schema=rc.FrozenJson({"type": "object", "required": ["answer"],
                                     "properties": {"answer": {"type": "string"}}}),
        budget=rc.RunBudget(timeout_seconds=60),
        session_services=(rc.SessionService(tool_names=["mcp__finish__hey_my_buddy_finish_turn"]),),
        capture_evidence=True)


def full_result() -> rc.RunResult:
    return rc.RunResult(
        identity=identity(), harness="zcode",
        end=rc.RunEnd(status="ok", native_exit_code=0),
        harness_version="0.42.0", model_started=True,

        buddy=rc.ResultBuddy(
            requested=rc.RunBuddy(provider="fixture-provider", model="fixture-model", effort="off"),
            checked=rc.CheckedBuddy(model=rc.CheckedValue(value="fixture-model"))),
        native_identity=rc.NativeIdentity(session_id="s1"),

        value=rc.RunValue(schema_status="valid",  parsed=rc.FrozenJson({"a": 1}),
                           correction_count=0),
        completion_evidence=rc.CompletionEvidence( stream_end=True),
        tool_evidence=rc.FrozenJson(tool_evidence.ToolEventEvidence({
            "harness": "zcode", "taskId": "task-fixture", "attemptId": "attempt-fixture", "generation": 1,
        }, state_dir=STATE_DIR).finish([{"sessionId": "s1"}], True)),

        effective_policy=rc.EffectivePolicy(
            tools=rc.PolicyFact(
                                requested=rc.FrozenJson({"toolAllowlist": []}))),
        activity=rc.FrozenJson({"phase": "streaming-model", "eventSeq": 3}),
        usage=rc.FrozenJson({"version": 1, "inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 2,
                             "inputBasis": "includes-cached", "source": "fixture", "scope": "attempt",
                             "completeness": "complete", "nativeRecords": 1}),
        native_failure=rc.FrozenJson({"code": "quota-exceeded", "nativeCode": "QUOTA",
                                      "source": "fixture", "observedAt": "2026-01-01T00:00:00Z",
                                      "rejectionPhase": "before-model"}),
        last_assistant_message=rc.FrozenJson({
            "version": 1, "text": "done", "source": "fixture", "sourceId": "m1", "sourceBytes": 4,
            "sha256": hashlib.sha256(b"done").hexdigest(), "truncated": False}),
        continuation=rc.ContinuationFacts(resumable=None),
        stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone"),
                                      interrupt=rc.InterruptEvidence(requested=False)),
        evidence_refs=(rc.EvidenceRef(kind="runner-stdout", location=str(Path(tempfile.gettempdir()).resolve() / "runner.log"),
                                      size_bytes=120, sha256="c" * 64),))


class NativeEvidenceContractTests(unittest.TestCase):
    def test_blank_buddy_and_previous_session_are_refused_at_the_boundary(self):
        request = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        for blank in (" ", "\t\n", "\u3000"):
            for field in ("provider", "model", "effort"):
                with self.subTest(field=field, blank=blank):
                    fields = {**request, "buddy": {**request["buddy"], field: blank}}
                    with self.assertRaises(BoardError):
                        rc.RunRequest.from_payload(fields)
            for mode in ("native-session", "reconstructed-new-session"):
                with self.subTest(mode=mode, blank=blank):
                    with self.assertRaises(BoardError):
                        rc.RunRequest.from_payload({**request, "continuation": {
                            "mode": mode, "previousSessionId": blank}})
        configured = rc.RunBuddy(provider=" provider ", model="model", effort="off")
        self.assertEqual(configured.provider, " provider ")
        self.assertIsNone(rc.RunContinuation(mode="reconstructed-new-session").previous_session_id)

    def test_unknown_end_and_bounded_native_evidence_roundtrip_losslessly(self):
        evidence = rc.NativeEvidence(unknown_events={"countsByType": {"future-event": 2}, "total": 2},
                                     unconfirmed=["buddy-readback", "tool-scope"])
        result = full_result().model_copy(update={
            "end": rc.RunEnd(status="unknown", reason_code="native-end-unread"),
            "native_evidence": evidence})
        decoded = rc.RunResult.from_payload(result.to_payload())
        self.assertEqual(decoded, result)
        self.assertEqual(decoded.native_evidence.to_payload(), {
            "unknownEvents": {"countsByType": {"future-event": 2}, "total": 2},
            "unconfirmed": ["buddy-readback", "tool-scope"]})
        self.assertEqual(decoded.native_evidence.unknown_events.counts_by_type, (("future-event", 2),))

    def test_native_evidence_refuses_bad_shapes_counts_and_unconfirmed_names(self):
        malformed = [
            {"unknownEvents": {"countsByType": [], "total": 0}},
            {"unknownEvents": {"countsByType": [["future", 1]], "total": 1}},
            {"unknownEvents": {"countsByType": {str(i): 1 for i in range(65)}, "total": 65}},
            {"unknownEvents": {"countsByType": {"future": True}, "total": 1}},
            {"unknownEvents": {"countsByType": {"future": -1}, "total": 1}},
            {"unknownEvents": {"countsByType": {"future": rc.MAX_COUNT + 1}, "total": 1}},
            {"unknownEvents": {"countsByType": {"": 1}, "total": 1}},
            {"unknownEvents": {"countsByType": {"a" * 129: 1}, "total": 1}},
            {"unknownEvents": {"countsByType": {}, "total": False}},
            {"unknownEvents": {"countsByType": {}, "total": rc.MAX_COUNT + 1}},
            {"unconfirmed": ["field"] * 17},
            {"unconfirmed": "field"},
            {"unconfirmed": [42]},
            {"extra": "field"},
        ] + [{"unconfirmed": [name]} for name in ("", "Readback", "tool_scope", "a b", "a" * 65, "a\n")]
        fields = full_result().to_payload()
        for bad in malformed:
            with self.subTest(evidence=bad), self.assertRaises(BoardError):
                rc.RunResult.from_payload({**fields, "nativeEvidence": bad})
        maximum = rc.NativeEvidence(unknown_events={
            "countsByType": {str(i): rc.MAX_COUNT for i in range(64)}, "total": rc.MAX_COUNT},
            unconfirmed=["a" * 64] * 16)
        self.assertEqual(len(maximum.unknown_events.counts_by_type), 64)
        self.assertEqual(len(maximum.unconfirmed), 16)


class CodecRoundtripTests(unittest.TestCase):
    def test_run_request_carries_one_permission_level(self):
        request = edited(full_request(Path(tempfile.gettempdir()).resolve()), permission="write")
        self.assertEqual(rc.RunRequest.from_payload(request.to_payload()).permission, "write")
        self.assertNotIn("workerPermission", request.to_payload())
        self.assertNotIn("toolScope", request.to_payload())
        with self.assertRaises(BoardError):
            edited(request, permission="unknown")

    def test_codex_full_worker_selects_the_worker_carrier(self):
        from hey_my_buddy.buddy.harnesses.codex import native_run as codex
        from hey_my_buddy.buddy.harnesses.registry import require_permission
        request = edited(full_request(Path(tempfile.gettempdir()).resolve()), harness="codex",
                         permission="full")
        require_permission({"harness": "codex"}, "full")
        with mock.patch.object(codex, "_run", return_value=object()) as native:
            codex.run(request, observer=lambda _event: None, services=None, cancelled=lambda: False)
        self.assertEqual(native.call_args.args[2], "worker")
        require_permission({"harness": "claude"}, "full")

    def test_undeclared_full_worker_permission_is_refused(self):
        from hey_my_buddy.buddy.harnesses.registry import require_permission
        from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
        with mock.patch.dict(PERMISSION_LEVELS, {"claude": {
                level: declaration for level, declaration in PERMISSION_LEVELS["claude"].items()
                if level != "full"}}):
            with self.assertRaises(BoardError) as caught:
                require_permission({"harness": "claude"}, "full")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
            self.assertEqual(caught.exception.details["requiredCapability"], "permission:full")

    def test_a_resume_checkpoint_is_absent_or_carries_all_three_bound_facts(self):
        fields = {"mode": "native-session", "previousSessionId": "previous",
                  "checkpoint": {"nativeTurnId": "native-turn", "attemptId": "previous-attempt",
                                 "inputSha256": "a" * 64}}
        value = rc.RunContinuation.from_payload(fields)
        self.assertEqual(value.to_payload(), fields)
        self.assertIsNone(rc.RunContinuation(mode="native-session", previous_session_id="previous").checkpoint)
        for missing in fields["checkpoint"]:
            partial = {k: v for k, v in fields["checkpoint"].items() if k != missing}
            with self.subTest(missing=missing), self.assertRaises(BoardError):
                rc.RunContinuation.from_payload(fields | {"checkpoint": partial})

    def test_removed_native_restrictions_are_rejected_on_the_wire(self):
        request = full_request(Path(tempfile.gettempdir()).resolve())
        for fields in ({"networkAllowedDomains": []}, {"additionalDeniedTools": ["mcp__*"]},
                       {"toolScope": "read"}, {"workerPermission": {"level": "read"}}):
            with self.subTest(fields=fields), self.assertRaises(BoardError):
                rc.RunRequest.from_payload(request.to_payload() | fields)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-run-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_a_full_request_and_result_roundtrip_through_payloads(self):
        request = full_request(self.root)
        self.assertEqual(rc.RunRequest.from_payload(request.to_payload()), request)
        result = full_result()
        self.assertEqual(rc.RunResult.from_payload(result.to_payload()), result)

    def test_the_format_version_is_the_exact_integer(self):
        text = json.dumps(full_request(self.root).to_payload())
        for wrong in ("true", "1.0"):
            mutated = json.loads(text)
            mutated["formatVersion"] = json.loads(wrong)
            with self.assertRaises(BoardError, msg=wrong):
                rc.RunRequest.from_payload(mutated)
        result_payload = full_result().to_payload()
        result_payload["formatVersion"] = True
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(result_payload)

    def test_unknown_stays_unknown_in_a_minimal_result(self):
        result = rc.RunResult(identity=identity(), harness="codex", end=rc.RunEnd(status="cancelled"))
        self.assertIsNone(result.model_started)
        self.assertIsNone(result.buddy.requested)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.native_identity)
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertIsNone(result.stop_evidence.interrupt.requested)
        back = rc.RunResult.from_payload(result.to_payload())
        self.assertIsNone(back.model_started)
        self.assertIsNone(back.harness_version)
        # An absent optional text is None/JSON null end to end; the legacy
        # receipts' literal "unknown" spelling is a role projection, not a
        # wire form of this format (UnknownableText is gone).
        self.assertIsNone(result.to_payload()["harnessVersion"])

    def test_a_decoded_requested_block_roundtrips_without_an_observed_key(self):
        # The requested block survives decode unchanged; the legacy receipts'
        # ``observed`` key is the role projection's None placeholder, not a
        # wire field of this format.
        request = full_request(self.root)
        result = rc.RunResult(identity=identity(), harness="zcode", end=rc.RunEnd(status="ok"),
                              buddy=rc.ResultBuddy(requested=request.buddy))
        self.assertNotIn("observed", result.buddy.to_payload())
        self.assertEqual(result.buddy.checked, rc.CheckedBuddy())
        decoded = rc.RunResult.from_payload(result.to_payload())
        self.assertEqual(decoded.buddy.requested, request.buddy)
        self.assertEqual(decoded.buddy.checked, rc.CheckedBuddy())
        with self.assertRaises(BoardError, msg="observed"):
            rc.RunResult.from_payload({**result.to_payload(),
                                  "buddy": {**result.buddy.to_payload(),
                                                    "observed": {"model": "fixture-model"}}})

    def test_signal_terminations_and_windows_codes_roundtrip(self):
        for end, layer in ((rc.RunEnd(status="cancelled", native_exit_code=-15),
                            rc.StopLayer(group_state="gone")),
                           (rc.RunEnd(status="error", native_exit_code=4294967295),
                            rc.StopLayer(group_state="gone"))):
            result = rc.RunResult(identity=identity(), harness="codex", end=end)
            result = rc.RunResult(
                identity=identity(), harness="codex", end=end,
                stop_evidence=rc.StopEvidence(native=layer,
                                              interrupt=rc.InterruptEvidence()))
            decoded = rc.RunResult.from_payload(result.to_payload())
            self.assertEqual(decoded.end.native_exit_code, end.native_exit_code)
        with self.assertRaises(BoardError):
            rc.RunEnd(status="error", native_exit_code=2**33)

    def test_the_result_frame_enforces_its_fixed_key_set(self):
        payload = full_result().to_payload()
        self.assertEqual(rc.RunResult.from_payload(payload).identity, identity())
        extra = dict(payload)
        extra["role"] = "worker"
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(extra)
        missing = {key: value for key, value in payload.items() if key != "end"}
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(missing)
        for broken in (None, "frame", [], 7):
            with self.assertRaises(BoardError, msg=repr(broken)):
                rc.RunResult.from_payload(broken)
        wrong_version = dict(payload)
        wrong_version["formatVersion"] = 1.0
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(wrong_version)

    def test_every_nesting_level_keeps_the_closed_key_set(self):
        # Ordinary pydantic semantics since the step 2-B simplification: an
        # unknown member is refused at every nesting level and a required field
        # may not be omitted, while a default-valued field may be — the wire
        # no longer re-implements "defaults are required" (the ContextVar key
        # set and its snake_case refusal went with it).
        def without(payload: dict, path: str) -> dict:
            node = payload
            *parents, leaf = path.split(".")
            for parent in parents:
                node = node[int(parent)] if parent.isdigit() else node[parent]
            del node[leaf]
            return payload

        request = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        # Ordinary default semantics since the step 2-B simplification: a
        # default-valued field may be omitted, a required field may not.
        for path in ("identity.turnId", "identity.inputSha256", "sessionServices.0.toolNames",
                     "captureEvidence", "continuation", "sessionRecordPath"):
            decoded = rc.RunRequest.from_payload(without(json.loads(json.dumps(request)), path))
            self.assertIsInstance(decoded, rc.RunRequest, path)
        for path in ("identity.taskId", "budget.timeoutSeconds", "outputSchema", "privateState.stateDir"):
            with self.assertRaises(BoardError, msg=path):
                rc.RunRequest.from_payload(without(json.loads(json.dumps(request)), path))
        result = full_result().to_payload()
        for path in ("stopEvidence.native.groupState", "buddy.checked.model",
                     "end.message", "completionEvidence.streamEnd"):
            self.assertIsInstance(
                rc.RunResult.from_payload(without(json.loads(json.dumps(result)), path)), rc.RunResult, path)
        with self.assertRaises(BoardError):
            rc.RunRequest.from_payload({**request, "identity": {**request["identity"], "extra": 1}})
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload({**result, "end": {"status": "ok", "reasonCode": None,
                                                    "nativeExitCode": 0, "extra": 1}})
        # The subset rule of the native identity keeps working: one key is
        # enough, an unknown key and an empty object are not.
        self.assertEqual(rc.NativeIdentity.from_payload({"sessionId": "s1"}).session_id, "s1")
        for broken in ({}, {"bogus": "x"}):
            with self.assertRaises(BoardError):
                rc.NativeIdentity.from_payload(broken)

    def test_complete_input_and_independently_bounded_evidence_roundtrip(self):
        prompt = "汉😀\0\n" * 250000
        self.assertGreater(len(prompt.encode()), 2 * 1024 * 1024)
        request = edited(full_request(self.root), input_text=prompt)
        decoded = rc.RunRequest.from_payload(request.to_payload())
        self.assertEqual(decoded.input_text, prompt)
        # The final value keeps the old strict 512 KiB controller-read range.
        raw = " " * 70000 + '{"answer": 1}'
        value = rc.RunValue(schema_status="valid",  raw=raw)
        self.assertEqual(len(value.raw), 70000 + len('{"answer": 1}'))
        with self.assertRaises(BoardError):
            rc.RunValue(schema_status="valid",  raw="x" * (rc.MAX_VALUE_BYTES + 1))
        # Growing call facts exceed a message without enlarging its envelope.
        collector = tool_evidence.ToolEventEvidence({
            "harness": "codex", "taskId": "task-fixture", "attemptId": "attempt-fixture", "generation": 1}, state_dir=STATE_DIR)
        root = {"sessionId": "s" * 128}
        for index in range(1000):
            for phase in ("start", "end"):
                collector.observe({"nativeIdentity": root, "callId": f"c{index}" + "x" * 120,
                                   "toolName": "mcp_" + "n" * 500, "category": "other", "phase": phase})
        package = collector.finish([root], True)
        self.assertGreater(package["calls"]["bytes"], rc.MAX_PACKAGE_BYTES)
        self.assertLess(len(json.dumps(package).encode()), 4096)
        payload = full_result().to_payload()
        payload["toolEvidence"] = package
        decoded = rc.RunResult.from_payload(payload).tool_evidence.value
        self.assertEqual(len(list(tool_evidence.read_tool_calls(decoded, state_dir=STATE_DIR))), 1000)
        payload = full_result().to_payload()
        payload["evidenceRefs"] = [{"kind": "blob", "location": "x" * 900000}]
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(payload)

    def test_all_request_paths_follow_the_execution_platform(self):
        cases = (
            (PureWindowsPath, (r"D:\private", r"D:/private", r"\\host\share\private"),
             ("relative/private", r"D:private", r"\private", "/private")),
            (PurePosixPath, ("/private", "/private/with space", "/private/非ASCII"),
             ("relative/private", r"D:\private", r"D:/private", r"\\host\share\private")),
        )
        for flavor, roots, invalid in cases:
            # Pure paths select the OS semantics without a Windows filesystem.
            with self.subTest(platform=flavor.__name__), mock.patch.object(local_paths, "Path", flavor):
                for root in roots:
                    request = full_request(flavor(root))
                    self.assertEqual(rc.RunRequest.from_payload(request.to_payload()), request)
                for value in invalid:
                    request = full_request(flavor(roots[0]))
                    for field in ("cwd", "session_record_path"):
                        with self.subTest(field=field, value=value), self.assertRaises(BoardError):
                            edited(request, **{field: value})
                    for field in ("state_dir", "invocation_root", "native_root"):
                        fields = dict(request.private_state.__dict__)
                        fields[field] = value
                        with self.subTest(field=field, value=value), self.assertRaises(BoardError):
                            rc.PrivateStatePaths(**fields)


class StrictScalarTypeTests(unittest.TestCase):
    """Ordinary fields keep strict scalar types at both entries (step 2-P).

    The step 1 review confirmed the behavior was right but untested: an int
    never passes a bool field and a str never passes an int field, on the
    Python constructor exactly as on the wire decode. The covered fields are
    the ones whose type is held by the shared strict run contract alone.
    """

    def test_python_construction_refuses_int_for_bool_and_str_for_int(self):
        request = full_request(Path(tempfile.gettempdir()).resolve())
        with self.assertRaises(BoardError, msg="capture_evidence"):
            edited(request, capture_evidence=1)
        with self.assertRaises(BoardError, msg="budget.timeout_seconds"):
            edited(request, budget=rc.RunBudget(timeout_seconds="60"))
        with self.assertRaises(BoardError, msg="budget.timeout_seconds"):
            edited(request, budget=rc.RunBudget(timeout_seconds=0))
        unbounded = edited(request, budget=rc.RunBudget(timeout_seconds=None))
        self.assertIsNone(rc.RunRequest.from_payload(unbounded.to_payload()).budget.timeout_seconds)
        with self.assertRaises(BoardError, msg="identity.generation"):
            rc.RunIdentity(task_id="task", attempt_id="attempt", generation=True,
                           invocation_id="invocation")
        with self.assertRaises(BoardError, msg="end.native_exit_code"):
            rc.RunEnd(status="ok", native_exit_code="0")

    def test_the_wire_entries_refuse_int_for_bool_and_str_for_int(self):
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        payload["captureEvidence"] = 1
        with self.assertRaises(BoardError, msg="captureEvidence"):
            rc.RunRequest.from_payload(payload)
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        payload["budget"]["timeoutSeconds"] = "60"
        with self.assertRaises(BoardError, msg="timeoutSeconds"):
            rc.RunRequest.from_payload(payload)
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        payload["identity"]["generation"] = True
        with self.assertRaises(BoardError, msg="generation"):
            rc.RunRequest.from_payload(payload)
        result_payload = full_result().to_payload()
        result_payload["modelStarted"] = 1
        with self.assertRaises(BoardError, msg="modelStarted"):
            rc.RunResult.from_payload(result_payload)
        result_payload = full_result().to_payload()
        result_payload["end"]["nativeExitCode"] = "0"
        with self.assertRaises(BoardError, msg="nativeExitCode"):
            rc.RunResult.from_payload(result_payload)


class SessionRecordFailureTests(unittest.TestCase):
    def test_ungrouped_request_omits_the_session_record_path(self):
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        del payload["sessionRecordPath"]
        request = rc.RunRequest.from_payload(payload)
        self.assertIsNone(request.session_record_path)
        self.assertIsNone(request.to_payload()["sessionRecordPath"])

    def test_failure_roundtrips_without_changing_other_run_facts(self):
        result = full_result()
        payload = result.to_payload()
        failure = {"code": "session-record-write-failed", "params": {"category": "OSError", "message": "No space left on device", "truncated": False}}
        decoded = rc.RunResult.from_payload({**payload, "sessionRecordFailure": failure})
        self.assertEqual(decoded.session_record_failure.to_payload(), failure)
        self.assertEqual({**decoded.to_payload(), "sessionRecordFailure": None}, payload)
        self.assertEqual(decoded.end.status, "ok")
        self.assertEqual(decoded.stop_evidence.native.group_state, "gone")
        close_failure = {"category": "OSError", "message": "close failed", "truncated": False}
        with_close = {**failure, "params": {**failure["params"], "closeFailure": close_failure}}
        self.assertEqual(rc.RunResult.from_payload({**payload, "sessionRecordFailure": with_close})
                         .session_record_failure.to_payload(), with_close)
        for bad in ({**failure, "code": "unknown"}, {**failure, "params": {**failure["params"], "category": None}}):
            with self.subTest(bad=bad), self.assertRaises(BoardError):
                rc.RunResult.from_payload({**payload, "sessionRecordFailure": bad})
        for bad_close in ({**close_failure, "category": None},
                          {**close_failure, "message": "x" * 8193},
                          {**close_failure, "unexpected": "x"}):
            with self.subTest(bad_close=bad_close), self.assertRaises(BoardError):
                rc.RunResult.from_payload({**payload, "sessionRecordFailure": {
                    **failure, "params": {**failure["params"], "closeFailure": bad_close}}})


class FreezingTests(unittest.TestCase):
    def test_mutating_constructor_input_never_changes_the_frozen_value(self):
        mutable_identity = dict(IDENTITY)
        requested = {"provider": "fixture-provider", "model": "fixture-model", "effort": "off"}
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        tools = ["mcp__finish__hey_my_buddy_finish_turn"]
        root = Path(tempfile.gettempdir()).resolve()
        request = rc.RunRequest(
            identity=rc.RunIdentity.from_payload(mutable_identity), harness="codex",
            buddy=rc.RunBuddy.from_payload(requested), cwd=str(root / "checkout"),
            private_state=rc.PrivateStatePaths(state_dir=str(root), invocation_root=str(root / "inv"), native_root=str(root / "native")),
            session_record_path=str(root / "session.jsonl"),
            input_text="x",
            permission="none",
            output_schema=rc.FrozenJson(schema), budget=rc.RunBudget(timeout_seconds=60),
            session_services=(rc.SessionService(tool_names=tools),))
        mutable_identity["attemptId"] = "changed"
        mutable_identity["inputSha256"] = "f" * 64
        requested["model"] = "changed"
        schema["properties"]["answer"]["type"] = "changed"
        tools.append("mcp__finish__hey_my_buddy_checkpoint")
        self.assertEqual(request.identity.attempt_id, "attempt-fixture")
        self.assertEqual(request.identity.input_sha256, "a" * 64)
        self.assertEqual(request.buddy.model, "fixture-model")
        self.assertEqual(request.output_schema.value["properties"]["answer"]["type"], "string")
        self.assertEqual(request.session_services[0].tool_names, ("mcp__finish__hey_my_buddy_finish_turn",))

    def test_a_dict_parsed_value_is_frozen_on_construction(self):
        mutable = {"list": [1]}
        value = rc.RunValue(schema_status="unknown",  parsed=mutable)
        self.assertIsInstance(value.parsed, rc.FrozenJson)
        with self.assertRaises(TypeError):
            value.parsed["list"].append(2)  # not subscriptable, not mutable
        mutable["list"].append(3)
        self.assertEqual(value.parsed.value, {"list": [1]})
        self.assertEqual(value.to_payload()["parsed"], {"list": [1]})

    def test_frozen_json_hands_out_fresh_copies_and_enforces_its_bounds(self):
        frozen = rc.FrozenJson({"nested": {"list": [1, 2]}})
        payload = frozen.value
        payload["nested"]["list"].append(3)
        self.assertEqual(frozen.value, {"nested": {"list": [1, 2]}})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.text = "{}"
        with self.assertRaises(BoardError):
            # A pre-built FrozenJson still respects the field's own bound.
            rc.FrozenJson.from_value({"blob": "x" * 70000}, "catalog model extra", maximum=65536)

    def test_nested_values_reject_in_place_mutation(self):
        request = full_request(Path(tempfile.gettempdir()).resolve())
        with self.assertRaises(ValidationError):
            request.budget.timeout_seconds = 1
        service = rc.SessionService(tool_names=["mcp__finish__hey_my_buddy_finish_turn"])
        with self.assertRaises(TypeError):
            service.tool_names[0] = "changed.example"
        with self.assertRaises(AttributeError):
            request.session_services[0].tool_names.append("extra")


class NonEmptyInputTests(unittest.TestCase):
    """The whole-input nonempty bound holds on both entries; NUL still rides."""

    def request(self, **changes):
        base = full_request(Path(tempfile.gettempdir()).resolve())
        fields = dict(base.__dict__)
        fields.update(changes)
        return rc.RunRequest(**fields)

    def test_construction_and_decode_refuse_an_empty_whole_input(self):
        with self.assertRaises(BoardError):
            self.request(input_text="")
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        payload["inputText"] = ""
        with self.assertRaises(BoardError):
            rc.RunRequest.from_payload(payload)

    def test_a_nul_bearing_input_still_roundtrips(self):
        request = self.request(input_text="line one\x00line two")
        back = rc.RunRequest.from_payload(request.to_payload())
        self.assertEqual(back.input_text, "line one\x00line two")

    def test_a_correction_with_an_empty_input_is_refused_on_both_entries(self):
        with self.assertRaises(BoardError):
            rc.RunFeedback(action="correct", input_text="")
        payload = rc.RunFeedback(action="correct", input_text="real text").to_payload()
        payload["inputText"] = ""
        with self.assertRaises(BoardError):
            rc.RunFeedback.from_payload(payload)

    def test_a_native_session_continuation_requires_its_previous_session(self):
        with self.assertRaises(BoardError):
            rc.RunContinuation(mode="native-session")
        with self.assertRaises(BoardError):
            rc.RunContinuation(mode="native-session", previous_session_id="")
        payload = rc.RunContinuation(mode="reconstructed-new-session").to_payload()
        self.assertIsNone(rc.RunContinuation.from_payload(payload).previous_session_id)
        wire = rc.RunContinuation(mode="native-session", previous_session_id="s-1").to_payload()
        wire["previousSessionId"] = None
        with self.assertRaises(BoardError):
            rc.RunContinuation.from_payload(wire)

    def test_the_harness_version_carries_the_real_probe_bound(self):
        result = rc.RunResult(identity=identity(), harness="zcode",
                              end=rc.RunEnd(status="ok"), harness_version="v" * 80)
        self.assertEqual(result.harness_version, "v" * 80)
        with self.assertRaises(BoardError):
            rc.RunResult(identity=identity(), harness="zcode",
                         end=rc.RunEnd(status="ok"), harness_version="v" * 81)
        self.assertIn("v" * 80, json.dumps(result.to_payload()))


class RunFeedbackTests(unittest.TestCase):
    """The minimal in-process observer answer of the run seam (step 2-B)."""

    def test_the_three_actions_roundtrip_and_only_a_correction_carries_input(self):
        continue_feedback = rc.FEEDBACK_CONTINUE
        stop_feedback = rc.FEEDBACK_STOP
        correct = rc.RunFeedback(action="correct", input_text="the complete next input")
        self.assertEqual((continue_feedback.action, stop_feedback.action), ("continue", "stop"))
        for value, expected in ((continue_feedback, "continue"), (stop_feedback, "stop"), (correct, "correct")):
            back = rc.RunFeedback.from_payload(value.to_payload())
            self.assertEqual(back, value)
            self.assertEqual(back.action, expected)
        self.assertEqual(correct.input_text, "the complete next input")

    def test_a_correction_without_input_and_a_carrying_continue_are_refused(self):
        with self.assertRaises(BoardError):
            rc.RunFeedback(action="correct")
        with self.assertRaises(BoardError):
            rc.RunFeedback(action="continue", input_text="smuggled")
        with self.assertRaises(BoardError):
            rc.RunFeedback(action="stop", input_text="smuggled")
        with self.assertRaises(BoardError):
            rc.RunFeedback(action="restart")


class InterfaceSurfaceTests(unittest.TestCase):
    def test_no_role_authority_or_secret_material_fits_the_request(self):
        payload = full_request(Path(tempfile.gettempdir()).resolve()).to_payload()
        for forbidden in ("role", "routingMode", "agentCredential", "boardClient", "turnInput",
                          "taskBrief", "database", "controlToken", "frozenAccount", "network"):
            mutated = dict(payload)
            mutated[forbidden] = "anything"
            with self.assertRaises(BoardError, msg=forbidden):
                rc.RunRequest.from_payload(mutated)

    def test_a_foreign_usage_package_is_refused_not_dropped(self):
        payload = full_result().to_payload()
        payload["usage"] = {"scope": "session", "inputTokens": 1}
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(payload)

    def test_native_failure_requires_explicit_rejection_phase(self):
        payload = full_result().to_payload()
        failure = dict(payload["nativeFailure"])
        failure.pop("rejectionPhase")
        payload["nativeFailure"] = failure
        with self.assertRaises(BoardError):
            rc.RunResult.from_payload(payload)

    def test_the_harness_run_protocol_declares_registered_surface(self):
        class Stub:
            supported_request_controls = ()
            prepare_run_services = None
            native_evidence = None
            run_discovery = None
            run_delete_sessions = None

            def run(self, request, *, observer, services, cancelled):
                return full_result()

            def check_preparation(self, spec, environment):
                return None

            def session_facts(self, native_root, session_id):
                return {}

            def validate_turn_provenance(self, record):
                return None

        self.assertIsInstance(Stub(), rc.HarnessRun)

    def test_registered_runs_declare_native_operations(self):
        from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, native_operation

        for name, module in RUN_SEAMS.items():
            with self.subTest(harness=name):
                self.assertIsInstance(module, rc.HarnessRun)
                self.assertIs(native_operation(name, "discover"), module.run_discovery)
        self.assertIs(native_operation("codex", "delete-sessions"), RUN_SEAMS["codex"].run_delete_sessions)
        for name in ("claude", "dsh", "zcode"):
            with self.subTest(harness=name):
                with self.assertRaises(BoardError) as failure:
                    native_operation(name, "delete-sessions")
                self.assertEqual(failure.exception.code, "UNSUPPORTED")

    def test_tool_evidence_packages_keep_incomplete_facts(self):
        package = PAYLOADS["codex"]["payloads"]["fastSuccess"]["toolEvidence"]
        result_payload = full_result().to_payload()
        result_payload["toolEvidence"] = package
        self.assertEqual(rc.RunResult.from_payload(result_payload).tool_evidence.value, package)
        # The existing collector publishes failure and unknown packages before
        # any native root was observed; the structure carries them, and whether
        # such a package can pass stays the role's and the board's judgment.
        incomplete = json.loads(json.dumps(package))
        incomplete["nativeIdentity"] = []
        incomplete["streamComplete"] = False
        payload = full_result().to_payload()
        payload["toolEvidence"] = incomplete
        decoded = rc.RunResult.from_payload(payload)
        self.assertEqual(decoded.tool_evidence.value["nativeIdentity"], [])
        self.assertFalse(decoded.tool_evidence.value["streamComplete"])
        # Incomplete calls remain bounded, digest-bound facts. The run boundary
        # validates the envelope; the service validates rows and judges them.
        carried = json.loads(json.dumps(package))
        row = {"nativeIdentity": {}, "callId": None, "categories": ["other"], "phases": [],
               "facts": [{"toolName": None, "category": "other"}], "consistent": True,
               "endBeforeStart": False, "incomplete": True, "malformed": True, "belongsToRoot": False}
        carried["calls"] = tool_evidence.write_tool_calls(carried["binding"], [row], state_dir=STATE_DIR)
        payload = full_result().to_payload()
        payload["toolEvidence"] = carried
        decoded = rc.RunResult.from_payload(payload)
        self.assertEqual(list(tool_evidence.read_tool_calls(decoded.tool_evidence.value, state_dir=STATE_DIR)), [row])
        for mutation in ({"version": 1}, {"calls": []},
                         {"calls": {**package["calls"], "sha256": "x" * 64}},
                         {"calls": {**package["calls"], "path": "relative/calls.jsonl"}},
                         {"calls": {**package["calls"], "bytes": True}}):
            broken = json.loads(json.dumps(package))
            broken.update(mutation)
            payload = full_result().to_payload()
            payload["toolEvidence"] = broken
            with self.assertRaises(BoardError):
                rc.RunResult.from_payload(payload)

    def test_frozen_json_rejects_nonfinite_numbers_at_canonicalization(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(BoardError):
                rc.FrozenJson.from_value({"number": value}, "fixture", maximum=1024)



if __name__ == "__main__":
    unittest.main()
