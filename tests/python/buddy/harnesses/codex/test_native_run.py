"""The unified codex run seam itself: a real native connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.codex.native_run.run`
directly — a real spawned App Server (the offline fixtures), a real frozen
:class:`RunRequest`, the role's observation rules and a :class:`RunResult`
read back as facts. Nothing here wraps a prepared controller dict; the request
goes through the private home preparation, the handshake, the per-carrier
thread configuration, turn admission and the event pump, and the stop evidence
comes from the same owned process group the legacy controller held. The two
existing Python simulators keep their roles: ``mock_codex.py`` drives the
Worker and review carriers, ``no_tool_codex.py`` the fast no-tool carrier.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.routing.router import answer_schema
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint, LiveWireObserve
from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
from hey_my_buddy.protocol.contracts import HarnessRunLive
from hey_my_buddy.protocol.activity import is_newer, normalize_activity
from hey_my_buddy.buddy.harnesses.codex import native_run
from hey_my_buddy.buddy.harnesses.codex.home import remove_coding_auth
from hey_my_buddy.buddy.harnesses.codex.native_run import RunServices, run, run_discovery
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.buddy.harnesses.registry import worker_format
from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE,
    FEEDBACK_STOP,
    MAX_UNKNOWN_EVENT_TYPES,
    PrivateStatePaths,
    ResumeCheckpoint,
    RunBudget,
    RunConfiguration,
    RunContinuation,
    RunFeedback,
    RunIdentity,
    RunRequest,
)
from hey_my_buddy.buddy.roles.run_observers import CORRECTION_SUFFIX, FastCorrection
from hey_my_buddy.buddy.roles.structured_call import correction_code
from hey_my_buddy.buddy.roles.turn_io import input_hash
from hey_my_buddy.errors import BoardError

FIXTURE = Path(__file__).parent / "fixtures/mock_codex.py"
FAST_FIXTURE = Path(__file__).parent / "fixtures/no_tool_codex.py"
#: The output schema the registered native-schema Worker format really sends.
OUTCOME_SCHEMA = worker_format("codex").schema
FAST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["profileId"],
               "properties": {"profileId": {"type": "string", "enum": ["legal"]}}}
REVIEW_SCHEMA = answer_schema(["legal"])


def activity_endpoint(identity):
    return CTwoLiveEndpoint(identity, EXISTING_CAPABILITIES["codex"], HarnessRunLive,
                            instance_id="a" * 64, token="b" * 64)


def observed_activity(endpoint, identity):
    query = LiveWireObserve(identity=identity, instance_id="a" * 64, token="b" * 64,
                            fields=("activity",), limit=10)
    return json.loads(endpoint.observe(json.dumps(query.to_payload())))["activity"]


class SeamCase(unittest.TestCase):
    """The shared private environment for direct seam calls."""

    def setUp(self):
        developer_source = mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1"})
        developer_source.start()
        self.addCleanup(developer_source.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-codex-seam-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cwd = self.base / "checkout"
        self.cwd.mkdir()
        self.home = self.base / "native-account"
        self.home.mkdir()
        (self.home / "auth.json").write_text('{"fixture": "fake-auth-only"}')
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and key not in
                            ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "OPENAI_API_KEY", "CODEX_API_KEY")}
        self.environment.update(HOME=str(self.base), CODEX_HOME=str(self.home),
                                BUDDY_STATE_DIR=str(self.base / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.base / "runtime"))
        os.environ.update(self.environment)

    def set_case(self, case: str) -> None:
        os.environ["BUDDY_CODEX_FIXTURE_CASE"] = case
        self.addCleanup(os.environ.pop, "BUDDY_CODEX_FIXTURE_CASE", None)

    def halt_reported_unconfirmed(self):
        """Run the real group halt once, then record its confirmation as unconfirmed.

        The real stop still executes, so no child survives the test; only the
        recorded confirmation is replaced — a group whose stop cannot be
        proven after the fact.
        """
        real = native_run._halt_owned_group

        def halt_then_unconfirmed(process, handle, deadline):
            shutdown, signalled = real(process, handle, deadline)
            return False, signalled

        return mock.patch.object(native_run, "_halt_owned_group", halt_then_unconfirmed)

    def request(self, *, tool_scope: str, output_schema: dict, prompt: str,
                timeout: int = 8, continuation=None, capture_evidence=False,
                input_sha256: str | None = None, turn_id: str | None = None,
                native: str | None = None) -> RunRequest:
        marker = uuid.uuid4().hex[:8]
        return RunRequest(
            identity=RunIdentity(task_id="goal-1", attempt_id=f"attempt-{marker}",
                                 generation=1, invocation_id=uuid.uuid4().hex,
                                 turn_id=turn_id, input_sha256=input_sha256),
            harness="codex",
            configuration=RunConfiguration(provider="openai", model="fixture-model", effort="low"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(
                invocation_root=str(self.base / f"invocation-{marker}"),
                native_root=str(self.base / (native or f"native-{marker}"))),
            input_text=prompt, tool_scope=tool_scope, output_schema=output_schema,
            budget=RunBudget(timeout_seconds=timeout), continuation=continuation,
            capture_evidence=capture_evidence)

    def worker_services(self, live=None) -> RunServices:
        return RunServices(
            credential_source={"home": str(self.home), "source": "worker",
                               "credentialRevision": 1, "identity": "a" * 64},
            live=live)


class FastSeamTests(SeamCase):
    def setUp(self):
        super().setUp()
        FIXTURE.chmod(0o755)
        FAST_FIXTURE.chmod(0o755)
        os.environ.update(self.environment)
        os.environ["BUDDY_CODEX_CLI"] = str(FAST_FIXTURE)
        os.environ["BUDDY_CODEX_FIXTURE_STATE"] = str(self.base / "fast-trace.json")
        cache = {"client_version": "0.157.0", "models": [{
            "slug": "fixture-model", "display_name": "Fixture", "shell_type": "unified_exec",
            "apply_patch_tool_type": "freeform", "experimental_supported_tools": ["clock"],
            "supported_reasoning_levels": [{"effort": "low"}], "tool_mode": "code_mode_only",
            "multi_agent_version": "v2"}]}
        (self.home / "models_cache.json").write_text(json.dumps(cache))

    def fast_run(self, case: str, correction: FastCorrection, *, timeout: int = 6,
                 observer=None):
        self.set_case(case)
        prompt = "Pick a profile"
        return run(self.request(tool_scope="none", output_schema=FAST_SCHEMA,
                                prompt=prompt, timeout=timeout),
                   observer=observer or correction.observer, services=None,
                   cancelled=lambda: False)

    def test_a_settled_no_tool_answer_is_a_final_value_fact(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("ok", correction)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"profileId": "legal"}')
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.correction_count, 0)
        self.assertIsNone(correction.stop_reason)
        self.assertTrue(result.model_started)
        package = result.tool_evidence.value
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["configuration"], "private-no-tool")
        self.assertIs(result.completion_evidence.stream_end, True)

    def test_one_format_correction_runs_a_second_turn_on_the_same_thread(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("format", correction)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(result.value.raw, '{"profileId": "legal"}')
        # Both root turns are listed as native roots of this one run.
        self.assertEqual(len(result.tool_evidence.value["nativeIdentity"]), 2)
        trace = json.loads(Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).read_text())
        self.assertEqual([turn["input"][0]["text"] for turn in trace["turns"]],
                         ["Pick a profile",
                          "Pick a profile\n\nFormat correction: answer-invalid-json" + CORRECTION_SUFFIX])

    def test_tool_facts_stop_the_run_through_the_observer_feedback(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("typed", correction)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertTrue(any(event["toolName"] == "commandExecution" for event in package["events"]))
        self.assertTrue(result.stop_evidence.interrupt.requested)

    def test_unknown_events_stop_as_invalid_protocol_through_the_observer(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        seen = []

        def observer(facts):
            seen.append(dict(facts))
            return correction.observer(facts)

        result = self.fast_run("unknown", correction, observer=observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "invalid-protocol")
        # The unknown event's own count is the observer statistics' fact.
        self.assertTrue(any(facts["unknownEvents"] == {"countsByType": {"futureThing": 1},
                                                      "total": 1} for facts in seen),
                        [facts["unknownEvents"] for facts in seen])

    def test_a_denied_interaction_is_refused_then_stops_the_run(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("request", correction)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        retained = Path(next(ref.location for ref in result.evidence_refs
                             if ref.kind == "denied-interactions"))
        raw = retained.read_bytes()
        self.assertEqual(len(raw), next(ref.size_bytes for ref in result.evidence_refs
                                        if ref.kind == "denied-interactions"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         next(ref.sha256 for ref in result.evidence_refs
                              if ref.kind == "denied-interactions"))
        records = json.loads(raw)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["method"], "item/commandExecution/requestApproval")

    def test_a_drained_fast_stream_keeps_its_eof_fact_without_a_confirmed_stop(self):
        # The observed EOF is a stream fact of its own: an unknown group must
        # not overwrite it, and the unknown stays unknown.
        with self.halt_reported_unconfirmed():
            correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
            result = self.fast_run("ok", correction)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        self.assertEqual(result.end.native_exit_code, 0)
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "unknown")
        self.assertIs(result.completion_evidence.stream_end, True)
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        self.assertIsNone(result.stop_evidence.interrupt.requested)

    def test_eof_without_settlement_fails_the_stream(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("eof", correction)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "transport-error")
        self.assertFalse(result.tool_evidence.value["streamComplete"])
        self.assertIsNot(result.completion_evidence.stream_end, True)

    def test_deadline_stops_reports_interrupt_and_removes_private_auth(self):
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        request = self.request(tool_scope="none", output_schema=FAST_SCHEMA,
                               prompt="Pick a profile", timeout=1)
        self.set_case("hang")
        result = run(request, observer=correction.observer, services=None,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "deadline")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        # A hung native never reads the interrupt request; the honest basis
        # says the interrupt went out unconfirmed.
        self.assertEqual(result.stop_evidence.interrupt.basis, "native-turn-interrupt-unconfirmed")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        # The native SIGTERM survives as its own exit-code fact.
        self.assertEqual(result.end.native_exit_code, -15)
        self.assertFalse((Path(request.private_state.native_root) / "codex-home" / "auth.json").is_symlink())

    def test_mismatched_no_tool_policy_is_refused_before_model_input(self):
        for case in ("config-mismatch", "wrong-model"):
            with self.subTest(case=case):
                correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
                result = self.fast_run(case, correction)
                self.assertEqual(result.end.status, "error")
                self.assertEqual(result.end.reason_code, "no-tool-policy-unverified")
                self.assertIsNone(result.model_started)
                self.assertIsNone(result.effective_policy.tools)

    def test_an_unrunnable_executable_reports_spawn_never_happened(self):
        garbage = self.base / "not-a-cli"
        garbage.write_bytes(b"\x00\x01not an executable")
        garbage.chmod(0o755)
        os.environ["BUDDY_CODEX_CLI"] = str(garbage)
        request = self.request(tool_scope="none", output_schema=FAST_SCHEMA,
                               prompt="Pick a profile", timeout=5)
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = run(request, observer=correction.observer, services=None,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "adapter-unavailable")
        self.assertIsNone(result.model_started)
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "gone")
        self.assertIsNone(result.end.native_exit_code)
        self.assertFalse((Path(request.private_state.native_root) / "codex-home" / "auth.json").is_symlink())

    def test_an_unlisted_model_still_reaches_the_no_tool_turn(self):
        # The no-tool carrier shares the one relaxed initialization check: the
        # selected name reaches the native thread and turn, and the native
        # rejection is the failure; the absence fact stays on the run result.
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        self.set_case("unlisted-model")
        result = run(self.request(tool_scope="none", output_schema=FAST_SCHEMA,
                                  prompt="Pick a profile", timeout=6),
                     observer=correction.observer, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-rpc-error")
        # The bounded native refusal message rides beside the machine code.
        self.assertEqual(result.end.message,
                         "Codex rejected turn/start: model not found")
        trace = json.loads(Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).read_text())
        self.assertEqual(trace["thread"]["model"], "fixture-model")
        evidence = native_run.native_evidence(result)
        self.assertIs(evidence["selectedModelListed"], False)
        self.assertEqual(evidence["selectedModel"],
                         {"provider": "openai", "model": "fixture-model", "effort": "low"})

    def test_a_preparation_failure_reports_known_not_started_facts(self):
        # The Host probe's shape: the private no-tool home cannot be prepared,
        # no process is ever spawned, and the stop facts say so — a known
        # not-started "gone", not an unknown group inferred from a missing pid.
        (self.home / "models_cache.json").write_text("{}")
        correction = FastCorrection(FAST_SCHEMA, "Pick a profile")
        result = self.fast_run("ok", correction)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "no-tool-policy-unverified")
        self.assertIsNone(result.model_started)
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "gone")
        self.assertIsNone(result.end.native_exit_code)
        self.assertNotIn("native-stderr", {ref.kind for ref in result.evidence_refs})


class CodexSeamCase(SeamCase):
    def setUp(self):
        super().setUp()
        FIXTURE.chmod(0o755)
        os.environ.update(self.environment)
        os.environ["BUDDY_CODEX_CLI"] = str(FIXTURE)
        os.environ["BUDDY_CODEX_FIXTURE_STATE"] = str(self.base / "trace.json")

    def worker_run(self, case: str, *, continuation=None, timeout: int = 12,
                   observer=None, input_text: str = "fixture governed task", live=None):
        self.set_case(case)
        turn_input = {"taskId": "goal-1", "attemptId": "attempt-check", "generation": 7,
                      "turnId": "turn-check"}
        return run(self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                                prompt=input_text, timeout=timeout, continuation=continuation,
                                input_sha256=input_hash(turn_input), turn_id="turn-check",
                                native="worker-native"),
                   observer=observer or (lambda _facts: FEEDBACK_CONTINUE),
                   services=self.worker_services(live=live), cancelled=lambda: False)

    def review_run(self, case: str, *, observer=None, capture_evidence=False, timeout: int = 8):
        self.set_case(case)
        return run(self.request(tool_scope="read", output_schema=REVIEW_SCHEMA,
                                prompt="Select from the frozen packet", timeout=timeout,
                                capture_evidence=capture_evidence),
                   observer=observer or (lambda _facts: FEEDBACK_CONTINUE),
                   services=RunServices(),
                   cancelled=lambda: False)


class ReviewCorrection:
    """The review call's rule: fail closed on facts, correct once, never enum."""

    SUFFIX = ". Return exactly the supplied JSON Schema; do not repeat exploration."

    def __init__(self, output_schema: dict, base_prompt: str):
        self.output_schema = output_schema
        self.base_prompt = base_prompt
        self.correction_count = 0
        self.stop_reason: str | None = None

    def observer(self, facts) -> RunFeedback:
        if int(facts.get("deniedInteractions") or 0) > 0:
            return self._stop("no-tool-violation")
        if int(facts.get("toolMarkerFrames") or 0) > 0 or int(facts.get("toolCalls") or 0) > 0:
            return self._stop("no-tool-violation")
        unknown = facts.get("unknownEvents")
        total = int(unknown.get("total") or 0) if isinstance(unknown, dict) else 0
        if total > 0:
            return self._stop("invalid-protocol")
        if facts.get("settled") and self.correction_count == 0:
            raw = facts.get("rawAnswer")
            code = correction_code(raw, self.output_schema) if isinstance(raw, str) else "answer-invalid-json"
            if code is not None:
                self.correction_count += 1
                return RunFeedback(action="correct",
                                   input_text=f"{self.base_prompt}\n\nFormat correction: {code}{self.SUFFIX}")
        return FEEDBACK_CONTINUE

    def _stop(self, reason: str) -> RunFeedback:
        if self.stop_reason is None:
            self.stop_reason = reason
        return FEEDBACK_STOP


class ReviewSeamTests(CodexSeamCase):
    def test_a_fast_native_correction_publishes_the_second_round_final_activity(self):
        self.set_case("readonly-repair")
        prompt = "Select from the frozen packet"
        request = self.request(tool_scope="read", output_schema=REVIEW_SCHEMA, prompt=prompt)
        endpoint = activity_endpoint(request.identity)
        rule = ReviewCorrection(REVIEW_SCHEMA, prompt)
        with mock.patch.object(endpoint, "publish_activity", wraps=endpoint.publish_activity) as publish:
            result = run(request, observer=rule.observer, services=RunServices(live=endpoint),
                         cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 2, "toolCalls": 0})
        self.assertEqual(observed_activity(endpoint, request.identity), result.activity.value)
        self.assertEqual(publish.call_args.args[0], result.activity.value)

    def test_a_completed_review_stream_stays_complete_without_a_confirmed_stop(self):
        # The Host probe's shape: the real halt executed, its recorded
        # confirmation says unconfirmed, and the completed round's stream fact
        # must not be overwritten by the failed stop.
        with self.halt_reported_unconfirmed():
            result = self.review_run("ok")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        self.assertEqual(result.end.native_exit_code, 0)
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "unknown")
        package = result.tool_evidence.value
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(len(package["nativeIdentity"]), 1)

    def test_a_review_stream_without_a_final_state_stays_incomplete(self):
        result = self.review_run("no-final")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertFalse(result.tool_evidence.value["streamComplete"])
        self.assertIsNone(result.completion_evidence.stream_end)

    def test_a_review_call_reports_policy_readback_roots_and_raw_answer(self):
        result = self.review_run("ok")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw,
                         json.dumps({"profileId": "legal", "reason": "Read-only fixture", "evidence": []}))
        self.assertEqual(result.value.correction_count, 0)
        package = result.tool_evidence.value
        self.assertEqual(len(package["nativeIdentity"]), 1)
        self.assertEqual(result.native_identity.session_id,
                         package["nativeIdentity"][0]["sessionId"])
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["activePermissionProfile"]["id"], "buddy-router")
        # The read-back native configuration — the acknowledged state, with its
        # own filesystem grant for this checkout — is the retained evidence.
        retained = {ref.kind: ref for ref in result.evidence_refs}
        readback = json.loads(Path(retained["review-config-readback"].location).read_text())
        filesystem = readback["config"]["permissions"]["buddy-router"]["filesystem"]
        self.assertEqual(filesystem[str(self.cwd.resolve())], "read")
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["binding"]["taskId"], "goal-1")

    def test_review_tool_facts_stop_through_the_observer_and_interrupt_natively(self):
        rule = ReviewCorrection(REVIEW_SCHEMA, "Select from the frozen packet")
        result = self.review_run("readonly-budget", observer=rule.observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(rule.stop_reason, "no-tool-violation")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "native-turn-interrupt-ack")
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertGreaterEqual(package["toolCalls"], 1)

    def test_review_correction_is_once_and_never_for_enum_violations(self):
        rule = ReviewCorrection(REVIEW_SCHEMA, "Select from the frozen packet")
        result = self.review_run("readonly-repair", observer=rule.observer)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(rule.correction_count, 1)
        self.assertEqual(len(result.tool_evidence.value["nativeIdentity"]), 2)
        outside = ReviewCorrection(REVIEW_SCHEMA, "Select from the frozen packet")
        result = self.review_run("readonly-outside", observer=outside.observer)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.correction_count, 0)
        self.assertEqual(len(result.tool_evidence.value["nativeIdentity"]), 1)
        self.assertEqual(outside.correction_count, 0)

    def test_review_policy_mismatch_is_refused_before_model_input(self):
        for case in ("readonly-config-mismatch", "readonly-policy-mismatch"):
            with self.subTest(case=case):
                result = self.review_run(case)
                self.assertEqual(result.end.status, "error")
                self.assertEqual(result.end.reason_code, "readonly-policy-unverified")
                self.assertIsNone(result.model_started)
                self.assertIsNone(result.effective_policy.tools)

    def test_review_capture_retains_bounded_native_events_as_evidence(self):
        result = self.review_run("readonly-evidence", capture_evidence=True)
        self.assertEqual(result.end.status, "ok", result.end.message)
        retained = {ref.kind: ref for ref in result.evidence_refs}
        self.assertIn("review-captured-events", retained)
        capture = json.loads(Path(retained["review-captured-events"].location).read_text())
        self.assertTrue(capture["toolEvents"])
        self.assertTrue(capture["rawToolEvents"])
        self.assertEqual(capture["turns"][0]["turnId"], result.native_identity.turn_id)


class WorkerSeamTests(CodexSeamCase):
    def test_completed_worker_turn_reports_checkpoint_binding_and_activity(self):
        result = self.worker_run("ok")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(json.loads(result.value.raw)["outcome"]["disposition"], "completed")
        self.assertEqual(result.native_identity.turn_id, "native-turn-1")
        retained = Path(next(ref.location for ref in result.evidence_refs
                             if ref.kind == "native-checkpoint"))
        checkpoint = json.loads(retained.read_text())
        self.assertTrue(checkpoint["bindingSaved"])
        # The saved binding is the fact ``resumable`` reports: the run says
        # resumable exactly when its binding was saved.
        self.assertIs(result.continuation.resumable, True)
        self.assertIsNotNone(result.last_assistant_message.value)
        self.assertGreaterEqual(result.native_event_count, 2)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertFalse((self.base / "activity" / "activity.json").exists())
        remove_coding_auth(self.base / "worker-native")

    def test_worker_native_usage_and_quota_snapshot_are_retained(self):
        result = self.worker_run("usage")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNotNone(result.usage.value)
        self.assertEqual(result.usage.value["scope"], "attempt")
        self.assertIn("quota-snapshot", {ref.kind for ref in result.evidence_refs})
        self.assertIsNone(result.native_failure)
        remove_coding_auth(self.base / "worker-native")

    def test_worker_quota_failure_attribution_is_reported(self):
        result = self.worker_run("quota-failure")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertIsNotNone(result.native_failure.value)
        self.assertEqual(result.native_failure.value["nativeCode"], "usageLimitExceeded")
        remove_coding_auth(self.base / "worker-native")

    def test_denied_native_requests_are_facts_on_a_completed_worker_turn(self):
        result = self.worker_run("approval")
        self.assertEqual(result.end.status, "ok", result.end.message)
        # The attention conversion is the role's judgment at wiring; the driver
        # reports the completed native turn and the refusal facts as they were.
        self.assertEqual(json.loads(result.value.raw)["outcome"]["disposition"], "completed")
        retained = Path(next(ref.location for ref in result.evidence_refs
                             if ref.kind == "denied-interactions"))
        raw = retained.read_bytes()
        self.assertEqual(len(raw), next(ref.size_bytes for ref in result.evidence_refs
                                        if ref.kind == "denied-interactions"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         next(ref.sha256 for ref in result.evidence_refs
                              if ref.kind == "denied-interactions"))
        records = json.loads(raw)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["method"], "item/commandExecution/requestApproval")
        self.assertEqual(records[0]["turnId"], "native-turn-1")
        remove_coding_auth(self.base / "worker-native")

    def test_worker_unknown_events_are_counted_never_failed(self):
        seen = []

        def observer(facts):
            seen.append(dict(facts))
            return FEEDBACK_CONTINUE

        result = self.worker_run("worker-unknown", observer=observer)
        self.assertEqual(result.end.status, "ok", result.end.message)
        # The unknown event's own count is the observer statistics' fact.
        self.assertEqual(seen[-1]["unknownEvents"],
                         {"countsByType": {"thread/hologram/updated": 1}, "total": 1})
        remove_coding_auth(self.base / "worker-native")

    def test_unknown_event_types_merge_at_the_public_bound(self):
        # More distinct unclassified types in one real turn than the public
        # result carries: the run still settles with a bounded, exact-total
        # merge instead of failing result construction, and the observer's own
        # cumulative statistics carry the same bounded mapping.
        seen = []

        def observer(facts):
            seen.append(dict(facts))
            return FEEDBACK_CONTINUE

        result = self.worker_run("worker-unknown-flood", observer=observer)
        self.assertEqual(result.end.status, "ok", result.end.message)
        counts = seen[-1]["unknownEvents"]["countsByType"]
        total = seen[-1]["unknownEvents"]["total"]
        self.assertEqual(total, 65)
        self.assertEqual(sum(counts.values()), 65)
        self.assertEqual(len(counts), MAX_UNKNOWN_EVENT_TYPES)
        self.assertEqual(list(counts)[:MAX_UNKNOWN_EVENT_TYPES - 1],
                         [f"native/future/{number}" for number in range(MAX_UNKNOWN_EVENT_TYPES - 1)])
        self.assertEqual(counts["(unlisted-native-events)"], 2)

    def test_worker_tool_facts_are_live_public_evidence(self):
        seen = []

        def observer(facts):
            seen.append(dict(facts))
            return FEEDBACK_CONTINUE

        result = self.worker_run("worker-tool", observer=observer)
        self.assertEqual(result.end.status, "ok", result.end.message)
        # The same cumulative count reaches the observer live during the pump,
        # at settlement, and in the published package — never a terminal
        # patch-up and never a second accounting.
        self.assertTrue(any(facts["toolCalls"] == 1 and not facts["settled"] for facts in seen),
                        [facts["toolCalls"] for facts in seen])
        self.assertEqual([facts["toolCalls"] for facts in seen if facts["settled"]], [1])
        package = result.tool_evidence.value
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(package["binding"]["taskId"], "goal-1")
        self.assertEqual([event["phase"] for event in package["events"]], ["start", "end"])
        self.assertEqual([event["toolName"] for event in package["events"]],
                         ["commandExecution", "commandExecution"])
        # This run's one root turn is the package's own native root.
        self.assertEqual(package["nativeIdentity"],
                         [{"sessionId": result.native_identity.session_id,
                           "turnId": "native-turn-1"}])
        remove_coding_auth(self.base / "worker-native")

    def test_worker_tool_facts_count_in_activity(self):
        result = self.worker_run("worker-tool")
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["counts"]["toolCalls"], 1)
        self.assertFalse((self.base / "activity" / "activity.json").exists())
        remove_coding_auth(self.base / "worker-native")

    def test_native_events_publish_through_the_shared_endpoint_callback(self):
        self.set_case("worker-tool")
        request = self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                               prompt="fixture governed task", timeout=12,
                               input_sha256=input_hash({"fixture": "live"}), turn_id="turn-live",
                               native="worker-native")
        endpoint = activity_endpoint(request.identity)
        with mock.patch.object(endpoint, "publish_activity", wraps=endpoint.publish_activity) as publish:
            result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=self.worker_services(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        payloads = [call.args[0] for call in publish.call_args_list]
        self.assertEqual([payload["phase"] for payload in payloads],
                         ["waiting-model", "tool-running", "streaming-model", "finishing"])
        self.assertEqual(payloads[-1]["counts"], {"modelTurns": 1, "toolCalls": 1})
        self.assertTrue(all(normalize_activity(payload) == payload for payload in payloads))
        self.assertTrue(all(is_newer(new, old) for old, new in zip(payloads, payloads[1:])))
        self.assertEqual(observed_activity(endpoint, request.identity), result.activity.value)
        foreign = request.identity.model_copy(update={"attempt_id": "another-attempt"})
        self.assertIsNone(observed_activity(endpoint, foreign))
        self.assertFalse(Path(request.private_state.invocation_root, "activity.json").exists())
        remove_coding_auth(self.base / "worker-native")

    def test_refused_activity_publication_retains_native_final_facts(self):
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = False
        result = self.worker_run("worker-tool", live=endpoint)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 1, "toolCalls": 1})
        self.assertGreater(endpoint.publish_activity.call_count, 0)
        writer = native_run._ActivityWriter(endpoint)
        from hey_my_buddy.buddy.harnesses.codex.protocol import TurnEvidence
        evidence = TurnEvidence("thread", "turn")
        writer.write(evidence, "waiting-model")
        self.assertIsNone(writer.publisher.current(), "refusal is never a transmitted receipt")
        self.assertIsNotNone(writer.last_payload)
        remove_coding_auth(self.base / "worker-native")

    def test_codex_activity_preserves_native_counts_and_coalesced_tool_metadata(self):
        from hey_my_buddy.buddy.harnesses.codex.protocol import TurnEvidence
        from hey_my_buddy.protocol.activity import ActivityPublisher
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        writer = native_run._ActivityWriter(endpoint)
        tick = [10.0]
        writer.publisher = ActivityPublisher(endpoint.publish_activity, clock=lambda: tick[0])
        evidence = TurnEvidence("thread", "turn")

        def observe(method, item=None):
            params = {"threadId": "thread", "turnId": "turn"}
            if item is not None:
                params["item"] = item
            phase, tool = evidence.observe({"method": method, "params": params})
            writer.write(evidence, phase, tool, model_turns_base=2, tool_calls=7)

        with mock.patch.object(native_run.time, "monotonic", side_effect=lambda: tick[0]):
            observe("item/started", {"id": "tool-1", "type": "commandExecution"})
            tick[0] = 10.5
            observe("item/started", {"id": "tool-2", "type": "fileChange"})
            self.assertEqual(endpoint.publish_activity.call_count, 1)
            tick[0] = 12.5
            observe("item/started", {"id": "tool-3", "type": "mcpToolCall"})
            self.assertEqual(endpoint.publish_activity.call_count, 2)
            tick[0] = 12.6
            observe("item/completed", {"id": "agent", "type": "agentMessage"})
        receipts = [call.args[0] for call in endpoint.publish_activity.call_args_list]
        self.assertEqual([receipt["eventSeq"] for receipt in receipts], [1, 3, 4])
        self.assertEqual(receipts[-1]["counts"], {"modelTurns": 2, "toolCalls": 7})
        self.assertEqual(receipts[-1]["toolName"], "mcpToolCall")
        self.assertEqual(receipts[-1]["lastToolActivityAt"], receipts[-2]["lastToolActivityAt"])
        self.assertEqual(writer.last_payload, receipts[-1])

    def test_unknown_events_and_native_failure_keep_their_actual_activity(self):
        for case, status in (("worker-unknown", "ok"), ("failed", "error")):
            with self.subTest(case=case):
                endpoint = mock.Mock(spec=CTwoLiveEndpoint)
                endpoint.publish_activity.return_value = True
                result = self.worker_run(case, live=endpoint)
                self.assertEqual(result.end.status, status, result.end.message)
                payloads = [call.args[0] for call in endpoint.publish_activity.call_args_list]
                self.assertIn("streaming-model", [payload["phase"] for payload in payloads])
                self.assertEqual(payloads[-1]["phase"], "finishing")
                self.assertEqual(payloads[-1], result.activity.value)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                remove_coding_auth(self.base / "worker-native")

    def test_native_session_resume_reuses_the_bound_thread(self):
        first = self.worker_run("ok")
        self.assertEqual(first.end.status, "ok", first.end.message)
        remove_coding_auth(self.base / "worker-native")
        previous = first.native_identity.session_id
        second = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session", previous_session_id=previous))
        self.assertEqual(second.end.status, "ok", second.end.message)
        self.assertEqual(second.native_identity.session_id, previous)
        self.assertEqual(second.native_identity.turn_id, "native-turn-2")
        remove_coding_auth(self.base / "worker-native")

    def test_resume_without_a_binding_is_refused(self):
        result = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session", previous_session_id="thread-404"))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        remove_coding_auth(self.base / "worker-native")

    def _checkpoint_of(self, result) -> ResumeCheckpoint:
        """The public checkpoint facts exactly as the first run's evidence kept them."""
        retained = Path(next(ref.location for ref in result.evidence_refs
                             if ref.kind == "native-checkpoint"))
        checkpoint = json.loads(retained.read_text())
        return ResumeCheckpoint(native_turn_id=checkpoint["nativeTurnId"],
                                attempt_id=checkpoint["attemptId"],
                                input_sha256=checkpoint["inputSha256"])

    def test_a_matching_resume_checkpoint_resumes_the_bound_thread(self):
        first = self.worker_run("ok")
        self.assertEqual(first.end.status, "ok", first.end.message)
        remove_coding_auth(self.base / "worker-native")
        previous = first.native_identity.session_id
        second = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session", previous_session_id=previous,
                                               checkpoint=self._checkpoint_of(first)))
        self.assertEqual(second.end.status, "ok", second.end.message)
        self.assertEqual(second.native_identity.session_id, previous)
        self.assertEqual(second.native_identity.turn_id, "native-turn-2")
        remove_coding_auth(self.base / "worker-native")

    def test_a_checkpoint_native_turn_id_unlike_the_binding_is_refused(self):
        first = self.worker_run("ok")
        remove_coding_auth(self.base / "worker-native")
        kept = self._checkpoint_of(first)
        wrong = ResumeCheckpoint(native_turn_id="native-turn-9", attempt_id=kept.attempt_id,
                                 input_sha256=kept.input_sha256)
        result = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session",
                                               previous_session_id=first.native_identity.session_id,
                                               checkpoint=wrong))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        remove_coding_auth(self.base / "worker-native")

    def test_a_checkpoint_attempt_id_unlike_the_binding_is_refused(self):
        first = self.worker_run("ok")
        remove_coding_auth(self.base / "worker-native")
        kept = self._checkpoint_of(first)
        wrong = ResumeCheckpoint(native_turn_id=kept.native_turn_id, attempt_id="attempt-other",
                                 input_sha256=kept.input_sha256)
        result = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session",
                                               previous_session_id=first.native_identity.session_id,
                                               checkpoint=wrong))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        remove_coding_auth(self.base / "worker-native")

    def test_a_checkpoint_input_sha256_unlike_the_binding_is_refused(self):
        first = self.worker_run("ok")
        remove_coding_auth(self.base / "worker-native")
        kept = self._checkpoint_of(first)
        wrong = ResumeCheckpoint(native_turn_id=kept.native_turn_id, attempt_id=kept.attempt_id,
                                 input_sha256=kept.input_sha256[:-1]
                                 + ("0" if kept.input_sha256[-1] != "0" else "1"))
        result = self.worker_run(
            "ok", continuation=RunContinuation(mode="native-session",
                                               previous_session_id=first.native_identity.session_id,
                                               checkpoint=wrong))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        remove_coding_auth(self.base / "worker-native")

    def test_a_worker_correction_feedback_is_refused(self):
        self.set_case("ok")

        def correcting(_facts):
            return RunFeedback(action="correct", input_text="retry")

        request = self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                               prompt="fixture governed task", timeout=12,
                               input_sha256=input_hash({"taskId": "goal-1", "attemptId": "x",
                                                        "generation": 1, "turnId": "t"}),
                               turn_id="t", native="worker-native")
        with self.assertRaises(BoardError):
            run(request, observer=correcting, services=self.worker_services(),
                cancelled=lambda: False)
        remove_coding_auth(self.base / "worker-native")

    def test_unconfirmed_native_group_stop_reports_unknown_not_gone(self):
        observed = []

        def unconfirmed(handle, settle_seconds: float = 2.0) -> bool:
            observed.append(handle)
            return False

        with mock.patch.object(ProcessHandle, "shutdown_confirmed", unconfirmed):
            result = self.worker_run("ok")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "unknown")
        # The governed turn's observed completion is a stream fact of its own;
        # the unknown group must not overwrite it — and still never saves a
        # resumable binding. (The halt's real handle below shows the leader
        # reaped: the reappearance is that handle's fact.)
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        self.assertIsNone(result.continuation.resumable)
        # No orphan: the halt really stopped the group it owned.
        handle = observed[0]
        self.assertIsNotNone(handle.process.poll())
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.5))
        remove_coding_auth(self.base / "worker-native")

    def test_cancelled_run_reports_cancel_and_native_interrupt(self):
        started_at = time.monotonic()

        def cancel_after_admission():
            return time.monotonic() - started_at > 1.5

        self.set_case("hang")
        result = run(self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                                  prompt="fixture governed task", timeout=12,
                                  input_sha256=input_hash({"taskId": "goal-1", "attemptId": "attempt-check",
                                                           "generation": 7, "turnId": "turn-check"}),
                                  turn_id="turn-check", native="worker-native"),
                     observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=self.worker_services(), cancelled=cancel_after_admission)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "user-cancel")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "native-turn-interrupt-unconfirmed")
        # The native SIGTERM survives as its own exit-code fact.
        self.assertEqual(result.end.native_exit_code, -15)
        remove_coding_auth(self.base / "worker-native")


class DiscoverySeamTests(SeamCase):
    def setUp(self):
        super().setUp()
        FIXTURE.chmod(0o755)
        os.environ.update(self.environment)
        os.environ["BUDDY_CODEX_CLI"] = str(FIXTURE)
        os.environ["BUDDY_CODEX_FIXTURE_STATE"] = str(self.base / "discovery-trace.json")
        self.addCleanup(os.environ.pop, "BUDDY_CODEX_FIXTURE_CASE", None)

    def discover(self, marker: str):
        self.set_case("bad-catalog")
        return run_discovery(cwd=str(self.cwd), invocation_root=self.base / f"discovery-{marker}",
                             native_root=self.base / f"discovery-native-{marker}",
                             timeout_seconds=10, cancelled=lambda: False)

    def test_discovery_reads_the_catalog_without_a_prompt_or_turn(self):
        self.set_case("ok")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native",
                                timeout_seconds=10, cancelled=lambda: False)
        self.assertEqual(catalog["adapter"], "codex")
        self.assertEqual(catalog["harnessVersion"], "codex-cli fixture")
        models = catalog["providers"][0]["models"]
        self.assertEqual([model["id"] for model in models], ["fixture-model"])
        self.assertEqual(models[0]["efforts"], ["low", "high"])
        # No thread was ever created: discovery reads metadata only.
        self.assertFalse((self.base / "discovery-trace.json").exists())

    def test_discovery_failure_carries_the_halt_s_own_stop_fact(self):
        # A metadata error after the App Server was owned: the original reason
        # is kept and the exception carries the halt's actual confirmation —
        # True only for a group whose disappearance was observed, False when
        # the stop could not be proven. Nothing is inferred from the code.
        with self.assertRaises(CodexProtocolError) as confirmed:
            self.discover("confirmed")
        self.assertEqual(confirmed.exception.code, "invalid-catalog")
        self.assertIs(confirmed.exception.discovery_shutdown_confirmed, True)
        with self.halt_reported_unconfirmed():
            with self.assertRaises(CodexProtocolError) as unknown:
                self.discover("unknown")
        self.assertEqual(unknown.exception.code, "invalid-catalog")
        self.assertIs(unknown.exception.discovery_shutdown_confirmed, False)


class CatalogTrustTests(CodexSeamCase):
    """ADR-027: the discovery's account fact and the relaxed execution check."""

    def test_discovery_reports_the_same_session_s_confirmed_account_fact(self):
        self.set_case("ok")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native",
                                timeout_seconds=10, cancelled=lambda: False)
        self.assertEqual(catalog["discoveries"],
                         [{"adapter": "codex", "status": "complete", "accountStatus": "confirmed"}])
        self.assertEqual(catalog["warnings"], [])

    def test_discovery_reports_models_with_an_unknown_account_fact(self):
        # The account fact is reported, never judged: a session whose account
        # readback is unrecognized still reports the models it read, marked
        # unknown, and whether to trust the reading is the board's decision.
        self.set_case("account-unknown")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native",
                                timeout_seconds=10, cancelled=lambda: False)
        self.assertEqual([model["id"] for model in catalog["providers"][0]["models"]],
                         ["fixture-model"])
        self.assertEqual(catalog["discoveries"],
                         [{"adapter": "codex", "status": "complete", "accountStatus": "unknown"}])
        self.assertTrue(any("account" in warning for warning in catalog["warnings"]),
                        catalog["warnings"])

    def test_a_chatgpt_account_without_a_usable_plan_stays_unknown(self):
        # Type alone never confirms the account: a chatgpt readback whose plan
        # is missing, null or "unknown" keeps the reading unknown, with the
        # models still reported beside the fact.
        for case in ("account-plan-missing", "account-plan-null", "account-plan-unknown"):
            with self.subTest(case=case):
                self.set_case(case)
                catalog = run_discovery(cwd=str(self.cwd),
                                        invocation_root=self.base / f"discovery-{case}",
                                        native_root=self.base / f"discovery-native-{case}",
                                        timeout_seconds=10, cancelled=lambda: False)
                self.assertEqual([model["id"] for model in catalog["providers"][0]["models"]],
                                 ["fixture-model"])
                self.assertEqual(catalog["discoveries"],
                                 [{"adapter": "codex", "status": "complete",
                                   "accountStatus": "unknown"}])
                self.assertTrue(any("account" in warning for warning in catalog["warnings"]),
                                catalog["warnings"])

    def _model_check_ref(self, result):
        ref = next(ref for ref in result.evidence_refs if ref.kind == "model-check")
        raw = Path(ref.location).read_bytes()
        self.assertEqual(len(raw), ref.size_bytes)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref.sha256)
        return json.loads(raw)

    def test_an_unlisted_model_still_reaches_the_native_turn(self):
        result = self.worker_run("unlisted-model")
        # The relaxed check did not refuse: the selected name went to the
        # native thread and turn under its own identity, and the native
        # rejection — not a catalog reading — is the failure, with the
        # native refusal message retained beside the machine code.
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-rpc-error")
        self.assertEqual(result.end.message,
                         "Codex rejected turn/start: model not found: fixture-model")
        state = json.loads(Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).read_text())
        thread = next(iter(state["threads"].values()))
        self.assertEqual(thread["model"], "fixture-model")
        self.assertEqual(thread["rejectedTurns"],
                         [{"model": "fixture-model", "effort": "low"}])
        # The absence is this run's own public fact, with the selected
        # identity kept beside it.
        self.assertEqual(self._model_check_ref(result),
                         {"adapter": "codex", "selectedModelListed": False,
                          "selected": {"provider": "openai", "model": "fixture-model", "effort": "low"}})
        evidence = native_run.native_evidence(result)
        self.assertEqual(evidence["selectedModelListed"], False)
        self.assertEqual(evidence["selectedModel"],
                         {"provider": "openai", "model": "fixture-model", "effort": "low"})
        remove_coding_auth(self.base / "worker-native")

    def test_a_long_multibyte_native_error_still_reaches_the_run_result(self):
        # A native reason of repeated three-byte CJK plus four-byte characters
        # is far past the public UTF-8 byte budget: the message travels cut on
        # byte safety with the prefix and a readable reason, never erased to
        # None by the bound.
        result = self.worker_run("long-native-error")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-rpc-error")
        message = result.end.message
        self.assertIsNotNone(message)
        raw = message.encode("utf-8")
        self.assertLessEqual(len(raw), 512)
        self.assertTrue(raw.startswith("Codex rejected turn/start: 模型不可用".encode()),
                        message)
        self.assertNotIn("\N{REPLACEMENT CHARACTER}", message)
        remove_coding_auth(self.base / "worker-native")

    def test_a_listed_model_with_an_unlisted_effort_is_still_refused(self):
        # The fixture catalog lists fixture-model with low/high only: a listed
        # model whose effort the reading does not list keeps its refusal.
        marker = uuid.uuid4().hex[:8]
        turn_input = {"taskId": "goal-1", "attemptId": "attempt-check", "generation": 7,
                      "turnId": "turn-check"}
        result = run(RunRequest(
            identity=RunIdentity(task_id="goal-1", attempt_id=f"attempt-{marker}",
                                 generation=1, invocation_id=uuid.uuid4().hex,
                                 turn_id="turn-check", input_sha256=input_hash(turn_input)),
            harness="codex",
            configuration=RunConfiguration(provider="openai", model="fixture-model", effort="medium"),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(
                invocation_root=str(self.base / f"invocation-{marker}"),
                native_root=str(self.base / "worker-native")),
            input_text="fixture governed task", tool_scope="write", output_schema=OUTCOME_SCHEMA,
            budget=RunBudget(timeout_seconds=12)),
            observer=lambda _facts: FEEDBACK_CONTINUE,
            services=self.worker_services(), cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-configuration")
        self.assertIn("not in the current native catalog", result.end.message)
        # The refusal precedes any native thread, and records no absence fact.
        state = json.loads(Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).read_text()) \
            if Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).exists() else {"threads": {}}
        self.assertEqual(state["threads"], {})
        self.assertIsNone(result.model_started)
        self.assertFalse(any(ref.kind == "model-check" for ref in result.evidence_refs))
        remove_coding_auth(self.base / "worker-native")

    def test_a_listed_model_without_a_legal_effort_is_refused_before_any_turn(self):
        # The read names the selected model's identity but offers no legal
        # effort for it: the publishable catalog omits the row, yet execution
        # still sees the model as listed and refuses the effort here — it
        # never reaches a thread or turn, and never counts as an absence.
        result = self.worker_run("no-efforts-row")
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-configuration")
        self.assertIn("not in the current native catalog", result.end.message)
        state = json.loads(Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).read_text()) \
            if Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"]).exists() else {"threads": {}}
        self.assertEqual(state["threads"], {})
        self.assertIsNone(result.model_started)
        self.assertFalse(any(ref.kind == "model-check" for ref in result.evidence_refs))
        remove_coding_auth(self.base / "worker-native")

    def test_discovery_omits_a_row_without_a_legal_effort(self):
        # The discovery receipt stays publishable: the effort-less row is
        # omitted with its warning, never emitted as an illegal catalog model.
        self.set_case("no-efforts-row")
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discovery",
                                native_root=self.base / "discovery-native",
                                timeout_seconds=10, cancelled=lambda: False)
        self.assertEqual(catalog["providers"][0]["models"], [])
        self.assertTrue(any("omitted" in warning for warning in catalog["warnings"]),
                        catalog["warnings"])
        self.assertEqual(catalog["discoveries"],
                         [{"adapter": "codex", "status": "complete", "accountStatus": "confirmed"}])


if __name__ == "__main__":
    unittest.main()
