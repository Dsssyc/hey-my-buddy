"""The ADR-025 step 1-A run contract: frozen values, codec, legacy payload mapping.

Synthetic fixtures only: no harness or model is started. The four harness
payload fixtures carry exactly the field names the current controllers write,
and every mapping goes through ``legacy_facts`` so the tests pin exactly how
much evidence each existing payload honestly carries — never more, never less.
The bounds of the new format are chosen to hold the old allowed sets, and the
tests lock that: a 300k input, a 70k raw value and an empty-rooted incomplete
tool-evidence package all pass, while the new frames refuse oversized and
ill-typed forms explicitly.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses import legacy_facts as lf
from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.errors import BoardError

FIXTURES = Path(__file__).parent / "fixtures" / "run_contract"
PAYLOADS = {name: json.loads((FIXTURES / f"{name}-payloads.json").read_text())
            for name in ("codex", "claude", "zcode", "dsh")}

IDENTITY = {"taskId": "task-fixture", "attemptId": "attempt-fixture", "generation": 1,
            "invocationId": "invocation-fixture", "turnId": "turn-fixture", "inputSha256": "a" * 64}


def identity() -> rc.RunIdentity:
    return rc.RunIdentity.from_payload(dict(IDENTITY))


def full_request(tmp: Path) -> rc.RunRequest:
    return rc.RunRequest(
        identity=identity(), harness="zcode",
        configuration=rc.RunConfiguration("fixture-provider", "fixture-model", "off"),
        cwd=str(tmp / "checkout"), private_state=rc.PrivateStatePaths(str(tmp / "inv"), str(tmp / "native")),
        input_text="one bounded role-assembled input", tool_scope="read",
        network=rc.NetworkPolicy(requested=False, allowed_domains=None),
        output_schema=rc.FrozenJson({"type": "object", "required": ["answer"],
                                     "properties": {"answer": {"type": "string"}}}),
        budget=rc.RunBudget(timeout_seconds=60, max_output_bytes=65536, tool_calls=8),
        frozen_account=rc.FrozenAccountReference(adapter="zcode", source="native", revision=0,
                                                 credential_revision=0),
        session_services=(rc.SessionService(service_id="finish", kind="completion",
                                            tool_names=["buddy_finish_turn"],
                                            input_schema=rc.FrozenJson({"type": "object"}),
                                            delivery_mode="in-turn"),),
        capture_evidence=True,
    )


def full_result() -> rc.RunResult:
    return rc.RunResult(
        identity=identity(), harness="zcode",
        end=rc.RunEnd(status="ok", native_exit_code=0),
        harness_version="0.42.0", model_started=True,
        model_start_evidence=rc.ModelStartEvidence(basis="input-admitted",
                                                   native_identity=rc.NativeIdentity(session_id="s1")),
        configuration=rc.ResultConfiguration(
            requested=rc.RunConfiguration("fixture-provider", "fixture-model", "off"),
            checked=rc.CheckedConfiguration(model=rc.CheckedValue(value="fixture-model",
                                                                 basis="native-readback",
                                                                 source="zcode/session-snapshot")),
            observed=rc.FrozenJson({"model": "fixture-model"}),
            checks=("catalog-membership", "native-readback")),
        native_identity=rc.NativeIdentity(session_id="s1", input_id="buddy-x"),
        root_identities=(rc.NativeIdentity(session_id="s1"),),
        value=rc.RunValue(schema_status="valid", mechanism="completion-tool", parsed=rc.FrozenJson({"a": 1}),
                          validation_basis="zcode-signed-receipt", correction_count=0),
        completion_evidence=rc.CompletionEvidence(mechanism="completion-tool", stream_end=True,
                                                  call_id="call-1", event_order=7, receipt_ref="/tmp/r",
                                                  receipt_verified=True, native_outcome="turn.completed"),
        tool_evidence=rc.FrozenJson({"version": 1,
                                     "binding": {"adapter": "zcode", "taskId": "task-fixture",
                                                 "attemptId": "attempt-fixture", "generation": 1},
                                     "nativeIdentity": [{"sessionId": "s1"}], "streamComplete": True,
                                     "events": [], "toolCalls": 0, "unsettledToolCalls": 0, "truncated": False}),
        denied_interactions=(rc.DeniedInteraction(method="permission.requested", action="denied",
                                                  native_identity=rc.NativeIdentity(session_id="s1")),),
        unknown_events=rc.UnknownEvents(counts=(("native.note", 2),), total=2),
        effective_policy=rc.EffectivePolicy(
            tools=rc.PolicyFact(enforcement="native", reported=rc.FrozenJson({"toolAllowlist": []}),
                                basis="zcode/session-snapshot", limitations=())),
        activity=rc.FrozenJson({"phase": "streaming-model", "eventSeq": 3}),
        usage=rc.FrozenJson({"version": 1, "inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 2,
                             "inputBasis": "includes-cached", "source": "fixture", "scope": "attempt",
                             "completeness": "complete", "nativeRecords": 1}),
        native_failure=rc.FrozenJson({"version": 1, "code": "quota-exceeded", "nativeCode": "QUOTA",
                                      "source": "fixture", "observedAt": "2026-01-01T00:00:00Z"}),
        last_assistant_message=rc.FrozenJson({
            "version": 1, "text": "done", "source": "fixture", "sourceId": "m1", "sourceBytes": 4,
            "sha256": hashlib.sha256(b"done").hexdigest(), "truncated": False}),
        continuation=rc.ContinuationFacts(resumable=None, native_session_ref="s1"),
        stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone", started=True,
                                                          leader_exited=True, exit_code=0,
                                                          observation_basis="owned-process-group"),
                                      controller=rc.StopLayer(group_state="gone", started=True,
                                                              observation_basis="owned-process-group"),
                                      interrupt=rc.InterruptEvidence(requested=False)),
        evidence_refs=(rc.EvidenceRef(kind="runner-stdout", location=str(Path("/tmp") / "runner.log"),
                                      size_bytes=120, sha256="c" * 64),),
    )


class CodecRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-run-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_a_full_request_and_result_roundtrip_through_json(self):
        request = full_request(self.root)
        for form in (rc.encode_run_request(request),
                     rc.encode_run_request(request).encode(),
                     request.to_payload()):
            self.assertEqual(rc.decode_run_request(form), request)
        result = full_result()
        for form in (rc.encode_run_result(result),
                     rc.encode_run_result(result).encode(),
                     result.to_payload()):
            self.assertEqual(rc.decode_run_result(form), result)

    def test_the_format_version_is_the_exact_integer(self):
        text = rc.encode_run_request(full_request(self.root))
        for wrong in ("true", "1.0"):
            mutated = json.loads(text)
            mutated["formatVersion"] = json.loads(wrong)
            with self.assertRaises(BoardError, msg=wrong):
                rc.decode_run_request(mutated)
        result_payload = full_result().to_payload()
        result_payload["formatVersion"] = True
        with self.assertRaises(BoardError):
            rc.decode_run_result(result_payload)

    def test_the_frames_refuse_strict_json_violations(self):
        text = rc.encode_run_request(full_request(self.root))
        with self.assertRaises(BoardError):
            rc.decode_run_request(text.replace('"off"', '"off", "effort": "off"'))
        with self.assertRaises(BoardError):
            rc.decode_run_request(text.replace('"off"', 'NaN'))
        with self.assertRaises(BoardError):
            rc.decode_run_request(b"\xff\xfe not utf-8")

    def test_unknown_stays_unknown_in_a_minimal_result(self):
        result = rc.RunResult(identity=identity(), harness="codex", end=rc.RunEnd(status="cancelled"))
        self.assertIsNone(result.model_started)
        self.assertEqual(result.model_start_evidence.basis, "unknown")
        self.assertIsNone(result.configuration.requested)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.native_identity)
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertEqual(result.stop_evidence.controller.group_state, "unknown")
        self.assertIsNone(result.stop_evidence.interrupt.requested)
        back = rc.decode_run_result(rc.encode_run_result(result))
        self.assertIsNone(back.model_started)
        self.assertIsNone(back.harness_version)
        self.assertIn('"harnessVersion":"unknown"', rc.encode_run_result(result))

    def test_a_decoded_observed_block_is_never_filled_from_the_request(self):
        request = full_request(self.root)
        result = rc.RunResult(identity=identity(), harness="zcode", end=rc.RunEnd(status="ok"),
                              configuration=rc.ResultConfiguration(requested=request.configuration))
        self.assertIsNone(result.configuration.observed)
        self.assertEqual(result.configuration.checked, rc.CheckedConfiguration())
        decoded = rc.decode_run_result(result.to_payload())
        self.assertEqual(decoded.configuration.requested, request.configuration)
        self.assertIsNone(decoded.configuration.observed)
        self.assertEqual(decoded.configuration.checked, rc.CheckedConfiguration())

    def test_signal_terminations_and_windows_codes_roundtrip(self):
        for end, layer in ((rc.RunEnd(status="cancelled", native_exit_code=-15, signal="SIGTERM"),
                            rc.StopLayer(group_state="gone", exit_code=-9)),
                           (rc.RunEnd(status="error", native_exit_code=4294967295),
                            rc.StopLayer(group_state="gone", exit_code=259))):
            result = rc.RunResult(identity=identity(), harness="codex", end=end)
            result = rc.RunResult(
                identity=identity(), harness="codex", end=end,
                stop_evidence=rc.StopEvidence(native=layer, controller=rc.StopLayer(),
                                              interrupt=rc.InterruptEvidence()))
            decoded = rc.decode_run_result(rc.encode_run_result(result))
            self.assertEqual(decoded.end.native_exit_code, end.native_exit_code)
            self.assertEqual(decoded.end.signal, end.signal)
            self.assertEqual(decoded.stop_evidence.native.exit_code, layer.exit_code)
        with self.assertRaises(BoardError):
            rc.RunEnd(status="error", native_exit_code=2**33)

    def test_the_result_frame_enforces_its_fixed_key_set(self):
        payload = full_result().to_payload()
        self.assertEqual(rc.decode_run_result(payload).identity, identity())
        extra = dict(payload)
        extra["role"] = "worker"
        with self.assertRaises(BoardError):
            rc.decode_run_result(extra)
        missing = {key: value for key, value in payload.items() if key != "end"}
        with self.assertRaises(BoardError):
            rc.decode_run_result(missing)
        for broken in (None, "frame", [], 7):
            with self.assertRaises(BoardError, msg=repr(broken)):
                rc.decode_run_result(broken)
        wrong_version = dict(payload)
        wrong_version["formatVersion"] = 1.0
        with self.assertRaises(BoardError):
            rc.decode_run_result(wrong_version)

    def test_the_new_bounds_hold_the_old_allowed_sets(self):
        # The role-assembled input follows the board's 1 MiB task text bound, so
        # a 300,000-character non-turn prompt is a legal request.
        request = dataclasses.replace(full_request(self.root), input_text="x" * 300000)
        decoded = rc.decode_run_request(rc.encode_run_request(request))
        self.assertEqual(len(decoded.input_text), 300000)
        with self.assertRaises(BoardError):
            dataclasses.replace(request, input_text="x" * (rc.MAX_INPUT_TEXT_BYTES + 1))
        # The final value keeps the old strict 512 KiB controller-read range.
        raw = " " * 70000 + '{"answer": 1}'
        value = rc.RunValue(schema_status="valid", mechanism="final-message", raw=raw)
        self.assertEqual(len(value.raw), 70000 + len('{"answer": 1}'))
        with self.assertRaises(BoardError):
            rc.RunValue(schema_status="valid", mechanism="final-message", raw="x" * (rc.MAX_VALUE_BYTES + 1))
        # The fact-package bound holds a maximal legal tool-evidence package
        # (128 events at the spec's own field bounds), which the old strict
        # read could carry.
        package = {"version": 1,
                   "binding": {"adapter": "codex", "taskId": "task-fixture",
                               "attemptId": "attempt-fixture", "generation": 1},
                   "nativeIdentity": [{"sessionId": "s" * 128}], "streamComplete": True,
                   "events": [], "toolCalls": 64, "unsettledToolCalls": 0, "truncated": False}
        for index in range(64):
            for phase in ("start", "end"):
                package["events"].append({"nativeIdentity": {"sessionId": "s" * 128},
                                          "callId": f"c{index}" + "x" * 120,
                                          "toolName": "mcp_" + "n" * 500,
                                          "category": "other", "phase": phase})
        self.assertGreater(len(json.dumps(package).encode()), 100000)
        payload = full_result().to_payload()
        payload["toolEvidence"] = package
        self.assertEqual(len(rc.decode_run_result(payload).tool_evidence.value["events"]), 128)
        # A Mapping frame is bounded exactly like the equivalent text frame.
        payload = full_result().to_payload()
        payload["evidenceRefs"] = [{"kind": "blob", "location": "x" * 900000}]
        with self.assertRaises(BoardError):
            rc.decode_run_result(payload)
        self.assertGreater(len(json.dumps(payload)), rc.MAX_RUN_RESULT_BYTES - 8 * rc.MAX_RUN_REQUEST_BYTES)

    def test_windows_absolute_paths_and_32bit_exit_codes_are_existing_values(self):
        paths = rc.PrivateStatePaths("C:\\private\\inv", "C:/private/native")
        self.assertEqual(paths.invocation_root, "C:\\private\\inv")
        unc = rc.PrivateStatePaths("\\\\host\\share\\inv", "/tmp/native")
        self.assertTrue(unc.invocation_root.startswith("\\\\"))
        layer = rc.StopLayer(group_state="gone", exit_code=4294967295)
        self.assertEqual(layer.exit_code, 4294967295)
        with self.assertRaises(BoardError):
            rc.PrivateStatePaths("relative/inv", "/tmp/native")


class FreezingTests(unittest.TestCase):
    def test_mutating_constructor_input_never_changes_the_frozen_value(self):
        mutable_identity = dict(IDENTITY)
        requested = {"provider": "fixture-provider", "model": "fixture-model", "effort": "off"}
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        domains = ["example.com"]
        request = rc.RunRequest(
            identity=rc.RunIdentity.from_payload(mutable_identity), harness="codex",
            configuration=rc.RunConfiguration.from_payload(requested), cwd="/tmp/checkout",
            private_state=rc.PrivateStatePaths("/tmp/inv", "/tmp/native"), input_text="x",
            tool_scope="none", network=rc.NetworkPolicy(requested=False, allowed_domains=domains),
            output_schema=rc.FrozenJson(schema), budget=rc.RunBudget(60, 1024))
        mutable_identity["attemptId"] = "changed"
        mutable_identity["inputSha256"] = "f" * 64
        requested["model"] = "changed"
        schema["properties"]["answer"]["type"] = "changed"
        domains.append("evil.example")
        self.assertEqual(request.identity.attempt_id, "attempt-fixture")
        self.assertEqual(request.identity.input_sha256, "a" * 64)
        self.assertEqual(request.configuration.model, "fixture-model")
        self.assertEqual(request.output_schema.value["properties"]["answer"]["type"], "string")
        self.assertEqual(request.network.allowed_domains, ("example.com",))

    def test_a_dict_parsed_value_is_frozen_on_construction(self):
        mutable = {"list": [1]}
        value = rc.RunValue(schema_status="unknown", mechanism="final-message", parsed=mutable)
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
        request = full_request(Path("/tmp"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.budget.timeout_seconds = 1
        domains = rc.NetworkPolicy(requested=False, allowed_domains=["example.com"])
        with self.assertRaises(TypeError):
            domains.allowed_domains[0] = "changed.example"
        with self.assertRaises(AttributeError):
            request.session_services[0].tool_names.append("extra")


class InterfaceSurfaceTests(unittest.TestCase):
    def test_no_role_authority_or_secret_material_fits_the_request(self):
        payload = full_request(Path("/tmp")).to_payload()
        for forbidden in ("role", "routingMode", "agentCredential", "boardClient", "turnInput",
                          "taskBrief", "database", "controlToken"):
            mutated = dict(payload)
            mutated[forbidden] = "anything"
            with self.assertRaises(BoardError, msg=forbidden):
                rc.decode_run_request(mutated)

    def test_the_frozen_account_is_a_scalar_non_secret_reference(self):
        with self.assertRaises(BoardError):
            rc.FrozenAccountReference.from_payload({"adapter": "codex", "source": "native", "revision": 0,
                                                    "credentialRevision": 0, "identity": None,
                                                    "nativeLocation": None, "apiKey": "sk-secret"})
        reference = rc.FrozenAccountReference(adapter="codex", source="native", revision=3,
                                              credential_revision=2)
        self.assertEqual(reference.to_payload(),
                         {"adapter": "codex", "source": "native", "revision": 3,
                          "credentialRevision": 2, "identity": None, "nativeLocation": None})

    def test_denied_interactions_never_carry_tool_arguments(self):
        trace = {"nativeDeniedRequests": [
            {"method": "execCommandApproval", "requestId": 41,
             "params": {"threadId": "thread-fixture-0001", "turnId": "turn-fixture-0001",
                        "command": "rm -rf /", "cwd": "/tmp/checkout"},
             "response": {"code": -32601, "denied": True}}]}
        (denied,) = lf.denied_interactions_from_payload(trace)
        self.assertEqual(denied.request_id, "41")
        self.assertEqual(denied.native_identity.to_payload(),
                         {"threadId": "thread-fixture-0001", "turnId": "turn-fixture-0001"})
        rendered = json.dumps(denied.to_payload())
        self.assertNotIn("rm -rf", rendered)
        self.assertNotIn("params", rendered)
        legacy = lf.denied_interactions_from_payload(
            {"deniedRequests": [{"method": "applyPatchApproval", "threadId": "t1", "turnId": "t2"}]})
        self.assertIsNone(legacy[0].request_id)

    def test_the_harness_run_protocol_declares_run_and_discover(self):
        class Stub:
            def run(self, request, *, observer, services, cancelled):
                return full_result()

            def discover(self):
                return None

        self.assertIsInstance(Stub(), rc.HarnessRun)

    def test_tool_evidence_packages_keep_incomplete_facts(self):
        package = PAYLOADS["codex"]["payloads"]["fastSuccess"]["toolEvidence"]
        result_payload = full_result().to_payload()
        result_payload["toolEvidence"] = package
        self.assertEqual(rc.decode_run_result(result_payload).tool_evidence.value, package)
        # The existing collector publishes failure and unknown packages before
        # any native root was observed; the structure carries them, and whether
        # such a package can pass stays the role's and the board's judgment.
        incomplete = json.loads(json.dumps(package))
        incomplete["nativeIdentity"] = []
        incomplete["streamComplete"] = False
        payload = full_result().to_payload()
        payload["toolEvidence"] = incomplete
        decoded = rc.decode_run_result(payload)
        self.assertEqual(decoded.tool_evidence.value["nativeIdentity"], [])
        self.assertFalse(decoded.tool_evidence.value["streamComplete"])
        for mutation in ({"version": 2}, {"events": [{"nativeIdentity": {}, "callId": "c",
                                                      "toolName": "Read", "category": "read",
                                                      "phase": "start"}]}):
            broken = json.loads(json.dumps(package))
            broken.update(mutation)
            payload = full_result().to_payload()
            payload["toolEvidence"] = broken
            with self.assertRaises(BoardError):
                rc.decode_run_result(payload)


class LegacyMappingTests(unittest.TestCase):
    """Every fixture payload maps to exactly the fact strength it really has."""

    def test_codex_governed_success_keeps_catalog_level_checks_and_layered_exits(self):
        payload = PAYLOADS["codex"]["payloads"]["governedSuccess"]
        end = lf.end_from_payload(payload)
        self.assertEqual((end.status, end.native_exit_code), ("ok", 0))
        started, evidence = lf.model_start_from_payload("codex", payload)
        self.assertIs(started, True)
        self.assertEqual(evidence.basis, "input-sent")
        self.assertEqual(evidence.native_identity.to_payload(),
                         {"sessionId": "thread-fixture-0001", "turnId": "turn-fixture-0001"})
        configuration = lf.configuration_from_payload("codex", payload)
        self.assertEqual(configuration.checked.model.basis, "catalog-membership")
        self.assertEqual(configuration.checked.model.source, "codex/app-server-model-list")
        self.assertIsNone(configuration.observed)
        self.assertEqual(configuration.checks, ("catalog-membership",))
        # The inner native exit and the outer controller exit stay separate.
        stop = lf.stop_evidence_from_payload(payload, harness="codex", outer_exit_code=3)
        self.assertEqual(stop.native.exit_code, 0)
        self.assertEqual(stop.native.observation_basis, "owned-process-group")
        self.assertEqual(stop.controller.exit_code, 3)
        result = rc.RunResult(identity=identity(), harness="codex", end=end, harness_version="0.157.0",
                              model_started=started, model_start_evidence=evidence,
                              configuration=configuration,
                              native_identity=evidence.native_identity,
                              usage=rc.FrozenJson(payload["tokenUsage"]),
                              quota=rc.FrozenJson(payload["quota"]),
                              stop_evidence=stop)
        decoded = rc.decode_run_result(result.to_payload())
        self.assertEqual(decoded.usage.value["inputBasis"], "includes-cached")
        self.assertEqual(decoded.quota.value["windows"][0]["name"], "primary")

    def test_codex_spawn_failure_does_not_become_a_started_model_or_a_missing_group(self):
        payload = PAYLOADS["codex"]["payloads"]["spawnFailure"]
        started, evidence = lf.model_start_from_payload("codex", payload)
        self.assertIs(started, False)
        self.assertEqual(evidence.basis, "input-sent")
        stop = lf.stop_evidence_from_payload(payload, harness="codex", started=False)
        self.assertEqual(stop.native.group_state, "gone")
        self.assertIs(stop.native.started, False)
        self.assertEqual(stop.native.observation_basis, "spawn-not-started")

    def test_codex_fast_value_maps_only_the_real_fields(self):
        success = PAYLOADS["codex"]["payloads"]["fastSuccess"]
        self.assertNotIn("answer", success)
        value = lf.value_from_fast_payload("codex", success)
        self.assertEqual((value.schema_status, value.mechanism), ("valid", "final-message"))
        self.assertEqual(value.validation_basis, "json-schema-subset")
        self.assertEqual(value.correction_count, 0)
        # The parsed value is the old path's own decode of the checked raw text.
        self.assertEqual(value.parsed.value, {"answer": "fixture answer", "citations": []})
        corrected = PAYLOADS["codex"]["payloads"]["fastFormatCorrected"]
        roots = tuple(lf.native_identity_from_payload(turn) for turn in corrected["nativeTurns"])
        self.assertEqual(len(roots), 2)
        package = rc.RunResult(identity=identity(), harness="codex",
                               end=lf.end_from_payload(corrected),
                               root_identities=roots,
                               tool_evidence=rc.FrozenJson(corrected["toolEvidence"]))
        decoded = rc.decode_run_result(package.to_payload())
        self.assertEqual(len(decoded.root_identities), 2)

    def test_an_incomplete_unknown_package_is_carried_not_refused(self):
        payload = PAYLOADS["codex"]["payloads"]["failureIncompleteRoots"]
        end = lf.end_from_payload(payload)
        stop = lf.stop_evidence_from_payload(payload, harness="codex")
        result = rc.RunResult(identity=identity(), harness="codex", end=end,
                              tool_evidence=rc.FrozenJson(payload["toolEvidence"]),
                              stop_evidence=stop)
        package = rc.decode_run_result(result.to_payload()).tool_evidence.value
        self.assertEqual(package["nativeIdentity"], [])
        self.assertFalse(package["streamComplete"])
        self.assertEqual(end.reason_code, "invalid-native-result")

    def test_claude_refusal_and_interrupt_facts_come_from_the_payload(self):
        refusal = PAYLOADS["claude"]["payloads"]["thirdPartyAuthRefusal"]
        started, evidence = lf.model_start_from_payload("claude", refusal)
        self.assertIs(started, False)
        configuration = lf.configuration_from_payload("claude", refusal)
        self.assertEqual(configuration.requested.model, "fixture-model")
        self.assertIsNone(configuration.checked.model)
        self.assertEqual(lf.end_from_payload(refusal).reason_code, "first-party-auth-required")
        interrupted = PAYLOADS["claude"]["payloads"]["streamInterrupted"]
        stop = lf.stop_evidence_from_payload(interrupted, harness="claude")
        self.assertIs(stop.interrupt.requested, True)
        self.assertIs(stop.interrupt.acknowledged, True)
        # A result without an interrupt record keeps unknown, never False.
        plain = lf.stop_evidence_from_payload({}, harness="claude").interrupt.to_payload()
        self.assertEqual(plain, {"requested": None, "acknowledged": None, "basis": None})
        result = rc.RunResult(identity=identity(), harness="claude", end=lf.end_from_payload(interrupted),
                              tool_evidence=rc.FrozenJson(interrupted["toolEvidence"]), stop_evidence=stop)
        package = rc.decode_run_result(result.to_payload()).tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["unsettledToolCalls"], 1)

    def test_non_boolean_model_started_values_stay_unknown(self):
        for bad in ("unknown", 1, None):
            started, evidence = lf.model_start_from_payload("codex", {"modelStarted": bad})
            self.assertIsNone(started, msg=repr(bad))
            self.assertEqual(evidence.basis, "unknown", msg=repr(bad))

    def test_a_confirmed_stop_report_is_not_spawn_evidence(self):
        stop = lf.stop_evidence_from_payload({"processState": {"shutdownConfirmed": True}},
                                             harness="codex")
        self.assertEqual(stop.native.to_payload(),
                         {"groupState": "gone", "started": None, "leaderExited": None,
                          "exitCode": None, "observationBasis": "owned-process-group"})

    def test_zcode_readback_check_turn_identity_and_quota_whitelist(self):
        payload = PAYLOADS["zcode"]["payloads"]["governedSuccess"]
        configuration = lf.configuration_from_payload("zcode", payload)
        self.assertEqual(configuration.checked.model.basis, "native-readback")
        self.assertEqual(configuration.checks, ("native-readback",))
        started, evidence = lf.model_start_from_payload("zcode", payload)
        self.assertEqual(evidence.basis, "input-admitted")
        # The governed Worker result names its roots with sessionId and
        # nativeTurnId; the helper reads the real keys, not a fixture alias.
        self.assertEqual(evidence.native_identity.to_payload(),
                         {"sessionId": "session-fixture-0001", "turnId": "turn-fixture-0001"})
        quota_payload = PAYLOADS["zcode"]["payloads"]["governedQuotaFailure"]
        result = rc.RunResult(identity=identity(), harness="zcode", end=lf.end_from_payload(quota_payload),
                              native_failure=rc.FrozenJson(quota_payload["quotaFailure"]))
        failure = rc.decode_run_result(result.to_payload()).native_failure.value
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["nativeCode"], "QUOTA")

    def test_zcode_transport_failure_keeps_its_own_native_error(self):
        payload = PAYLOADS["zcode"]["payloads"]["transportFailure"]
        error = lf.native_error_from_payload(payload)
        self.assertEqual(error.value, {"code": "connection-closed", "kind": "transport"})
        result = rc.RunResult(identity=identity(), harness="zcode", end=lf.end_from_payload(payload),
                              native_error=error)
        decoded = rc.decode_run_result(result.to_payload())
        self.assertEqual(decoded.native_error.value, {"code": "connection-closed", "kind": "transport"})
        self.assertIsNone(decoded.native_failure)
        self.assertEqual(decoded.end.reason_code, "connection-closed")

    def test_dsh_governed_absence_stays_unknown_and_needs_a_declared_source(self):
        payload = PAYLOADS["dsh"]["payloads"]["governedResult"]
        started, evidence = lf.model_start_from_payload("dsh", payload)
        self.assertIsNone(started)
        self.assertEqual(evidence.basis, "unknown")
        configuration = lf.configuration_from_payload("dsh", payload)
        # The governed DSH path reports the request only: requested never
        # becomes checked or observed, and reasoningEffort is the same fact.
        self.assertEqual(configuration.requested.effort, "off")
        self.assertIsNone(configuration.checked.model)
        self.assertIsNone(configuration.observed)
        self.assertEqual(configuration.checks, ())
        with self.assertRaises(BoardError):
            lf.stop_evidence_from_payload(payload, harness="dsh")
        node_stop = lf.stop_evidence_from_payload(payload, harness="dsh",
                                                  native_observation_basis="legacy-node-report")
        self.assertEqual(node_stop.native.group_state, "gone")
        self.assertEqual(node_stop.native.observation_basis, "legacy-node-report")
        self.assertIsNone(node_stop.native.started)
        result = rc.RunResult(identity=identity(), harness="dsh", end=lf.end_from_payload(payload),
                              model_started=started, model_start_evidence=evidence,
                              configuration=configuration, stop_evidence=node_stop)
        decoded = rc.decode_run_result(result.to_payload())
        self.assertIsNone(decoded.model_started)
        self.assertEqual(decoded.model_start_evidence.basis, "unknown")
        self.assertEqual(decoded.stop_evidence.native.observation_basis, "legacy-node-report")
        self.assertIsNone(decoded.usage)

    def test_dsh_python_no_tool_stop_is_an_owned_observation(self):
        payload = PAYLOADS["dsh"]["payloads"]["noToolSuccess"]
        stop = lf.stop_evidence_from_payload(payload, harness="dsh",
                                             native_observation_basis="owned-process-group")
        self.assertEqual(stop.native.observation_basis, "owned-process-group")
        self.assertEqual(stop.native.exit_code, 0)
        started, evidence = lf.model_start_from_payload("dsh", payload)
        self.assertIs(started, True)
        self.assertEqual(evidence.basis, "legacy-report")
        value = lf.value_from_fast_payload("dsh", payload)
        self.assertEqual(value.validation_basis, "legacy-node-json-schema-subset")
        self.assertEqual(value.parsed.value, {"answer": "fixture direct answer"})

    def test_a_non_quota_native_failure_keeps_its_own_position(self):
        payload = PAYLOADS["dsh"]["payloads"]["noToolNativeFailure"]
        error = lf.native_error_from_payload(payload)
        self.assertEqual(error.value, {"code": "CONTEXT_WINDOW_EXCEEDED", "kind": "error"})
        end = lf.end_from_payload(payload)
        self.assertEqual((end.status, end.reason_code, end.native_exit_code),
                         ("error", "native-turn-failed", 1))
        # The whitelist quota attribution and the raw record are separate
        # fields; one does not mask or replace the other.
        result = rc.RunResult(identity=identity(), harness="dsh", end=end, native_error=error)
        decoded = rc.decode_run_result(result.to_payload())
        self.assertEqual(decoded.native_error.value["code"], "CONTEXT_WINDOW_EXCEEDED")
        self.assertIsNone(decoded.native_failure)
        with self.assertRaises(BoardError):
            rc.RunResult(identity=identity(), harness="dsh", end=end,
                         native_error=rc.FrozenJson({"kind": "no-code"}))

    def test_the_old_ordinary_allowed_set_is_not_tightened(self):
        # The real old validator (plain json.loads) accepts non-finite numbers
        # with an empty schema; the conversion keeps its conclusion, retains
        # the raw verbatim and records parsed as unknown — the bounded-JSON
        # representation cannot carry NaN, and nothing turns this into a
        # native failure or a changed schema verdict.
        from hey_my_buddy.buddy.roles.structured_call import valid_answer
        self.assertIs(valid_answer("NaN", {}), True)
        value = lf.value_from_fast_payload("codex",
                                           {"rawAnswer": "NaN", "answerValid": True,
                                            "correctionCount": 0})
        self.assertEqual(value.schema_status, "valid")
        self.assertEqual(value.raw, "NaN")
        self.assertIsNone(value.parsed)
        self.assertEqual(value.validation_basis, "json-schema-subset")
        # A value with a bounded-JSON representation still decodes.
        normal = lf.value_from_fast_payload("codex",
                                            {"rawAnswer": '{"answer": 1}', "answerValid": True})
        self.assertEqual(normal.parsed.value, {"answer": 1})

    def test_a_foreign_usage_package_is_refused_not_dropped(self):
        payload = full_result().to_payload()
        payload["usage"] = {"scope": "session", "inputTokens": 1}
        with self.assertRaises(BoardError):
            rc.decode_run_result(payload)

    def test_catalog_facts_project_back_to_the_existing_payloads(self):
        catalog = {"source": "codex-native-app-server", "adapter": "codex", "harnessVersion": "0.157.0",
                   "discoveredAt": "2026-01-01T00:00:00Z",
                   "providers": [{"adapter": "codex", "provider": "openai",
                                  "displayName": "OpenAI ChatGPT plan", "packageName": "codex",
                                  "packageVersion": "0.157.0",
                                  "models": [{"id": "fixture-model", "name": "Fixture Model",
                                              "description": "", "efforts": ["low", "high"],
                                              "inputModalities": ["text"], "available": True,
                                              "contextWindow": 828400}]}]}
        facts = lf.catalog_facts_from_payload(catalog)
        self.assertEqual(facts.providers[0].models[0].extra.value, {"contextWindow": 828400})
        self.assertTrue(lf.catalog_facts_equal_payload(facts, catalog))
        # A supported complete empty catalog is the empty fact it is.
        empty = json.loads(json.dumps(PAYLOADS["zcode"]["payloads"]["discoverEmptyCatalog"]))["catalog"]
        empty_facts = lf.catalog_facts_from_payload(empty)
        self.assertEqual(empty_facts.providers, ())
        self.assertTrue(lf.catalog_facts_equal_payload(empty_facts, empty))
        # An omitted warnings key keeps being omitted; an explicit empty list
        # stays an explicit empty list.
        without = {key: value for key, value in empty.items() if key != "warnings"}
        self.assertTrue(lf.catalog_facts_equal_payload(lf.catalog_facts_from_payload(without), without))
        self.assertNotIn("warnings", lf.catalog_facts_from_payload(without).to_payload())
        with self.assertRaises(BoardError):
            lf.catalog_facts_from_payload({"adapter": "codex"})


if __name__ == "__main__":
    unittest.main()
