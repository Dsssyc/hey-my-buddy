"""The ADR-025 step 1-A run contract: frozen values, codec, payload packages.

Synthetic fixtures only: no harness or model is started. The Codex payload
fixture carries exactly the field names the current controller writes, so the
tool-evidence tests pin how much evidence an existing package honestly holds.
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

from pydantic import ValidationError

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.errors import BoardError

FIXTURES = Path(__file__).parent / "fixtures" / "run_contract"
PAYLOADS = {"codex": json.loads((FIXTURES / "codex-payloads.json").read_text())}

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
        configuration=rc.RunConfiguration(provider="fixture-provider", model="fixture-model", effort="off"),
        cwd=str(tmp / "checkout"), private_state=rc.PrivateStatePaths(invocation_root=str(tmp / "inv"),
                                                                      native_root=str(tmp / "native")),
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
            requested=rc.RunConfiguration(provider="fixture-provider", model="fixture-model", effort="off"),
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

    def test_every_nesting_level_enforces_its_exact_wire_keys(self):
        # A default-valued field missing from the wire, a snake_case spelling
        # and an unknown member are refused at every nesting level, exactly as
        # the hand-written per-class codecs refused them.
        def without(payload: dict, path: str) -> dict:
            node = payload
            *parents, leaf = path.split(".")
            for parent in parents:
                node = node[int(parent)] if parent.isdigit() else node[parent]
            del node[leaf]
            return payload

        request = full_request(Path("/tmp")).to_payload()
        for path in ("identity.turnId", "budget.toolCalls", "network.allowedDomains",
                     "sessionServices.0.deliveryMode", "frozenAccount.nativeLocation"):
            with self.assertRaises(BoardError, msg=path):
                rc.decode_run_request(without(json.loads(json.dumps(request)), path))
        result = full_result().to_payload()
        for path in ("stopEvidence.native.groupState", "configuration.checked.model",
                     "end.signal", "modelStartEvidence.eventSequence"):
            with self.assertRaises(BoardError, msg=path):
                rc.decode_run_result(without(json.loads(json.dumps(result)), path))
        with self.assertRaises(BoardError):
            rc.decode_run_request({**request, "network": {"requested": False, "allowed_domains": None}})
        with self.assertRaises(BoardError):
            rc.decode_run_request({**request, "identity": {**identity().to_payload(), "task_id": "x"}})
        with self.assertRaises(BoardError):
            rc.decode_run_result({**result, "end": {"status": "ok", "reasonCode": None,
                                                    "nativeExitCode": 0, "signal": None, "extra": 1}})
        # The subset rule of the native identity keeps working beside the exact
        # rule: one key is enough, an unknown key and an empty object are not.
        self.assertEqual(rc.NativeIdentity.from_payload({"sessionId": "s1"}).session_id, "s1")
        for broken in ({}, {"bogus": "x"}):
            with self.assertRaises(BoardError):
                rc.NativeIdentity.from_payload(broken)

        counts = rc.UnknownEvents(counts=(("foo", 1),), total=1)
        self.assertEqual(counts.to_payload()["countsByType"], {"foo": 1})
        array_counts = {"countsByType": [["foo", 1]], "total": 1, "truncated": False}
        with self.assertRaises(BoardError):
            rc.UnknownEvents.from_payload(array_counts)
        with self.assertRaises(BoardError):
            rc.decode_run_result({**result, "unknownEvents": array_counts})
        for bad_domains in ("", "1", "x", {}):
            with self.assertRaises(BoardError, msg=repr(bad_domains)):
                rc.decode_run_request({**request, "network": {"requested": False,
                                                             "allowedDomains": bad_domains}})

    def test_the_new_bounds_hold_the_old_allowed_sets(self):
        # The role-assembled input follows the board's 1 MiB task text bound, so
        # a 300,000-character non-turn prompt is a legal request.
        request = edited(full_request(self.root), input_text="x" * 300000)
        decoded = rc.decode_run_request(rc.encode_run_request(request))
        self.assertEqual(len(decoded.input_text), 300000)
        with self.assertRaises(BoardError):
            edited(request, input_text="x" * (rc.MAX_INPUT_TEXT_BYTES + 1))
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
        paths = rc.PrivateStatePaths(invocation_root="C:\\private\\inv", native_root="C:/private/native")
        self.assertEqual(paths.invocation_root, "C:\\private\\inv")
        unc = rc.PrivateStatePaths(invocation_root="\\\\host\\share\\inv", native_root="/tmp/native")
        self.assertTrue(unc.invocation_root.startswith("\\\\"))
        layer = rc.StopLayer(group_state="gone", exit_code=4294967295)
        self.assertEqual(layer.exit_code, 4294967295)
        with self.assertRaises(BoardError):
            rc.PrivateStatePaths(invocation_root="relative/inv", native_root="/tmp/native")


class FreezingTests(unittest.TestCase):
    def test_mutating_constructor_input_never_changes_the_frozen_value(self):
        mutable_identity = dict(IDENTITY)
        requested = {"provider": "fixture-provider", "model": "fixture-model", "effort": "off"}
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        domains = ["example.com"]
        request = rc.RunRequest(
            identity=rc.RunIdentity.from_payload(mutable_identity), harness="codex",
            configuration=rc.RunConfiguration.from_payload(requested), cwd="/tmp/checkout",
            private_state=rc.PrivateStatePaths(invocation_root="/tmp/inv", native_root="/tmp/native"),
            input_text="x",
            tool_scope="none", network=rc.NetworkPolicy(requested=False, allowed_domains=domains),
            output_schema=rc.FrozenJson(schema), budget=rc.RunBudget(timeout_seconds=60,
                                                                     max_output_bytes=1024))
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
        with self.assertRaises(ValidationError):
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

    def test_a_foreign_usage_package_is_refused_not_dropped(self):
        payload = full_result().to_payload()
        payload["usage"] = {"scope": "session", "inputTokens": 1}
        with self.assertRaises(BoardError):
            rc.decode_run_result(payload)

    def test_the_harness_run_protocol_declares_run(self):
        class Stub:
            def run(self, request, *, observer, services, cancelled):
                return full_result()

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


if __name__ == "__main__":
    unittest.main()
