"""The unified claude run seam itself: a real native connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.claude.native_run.run`
directly — a real spawned native CLI (the private stream-json fixtures), a
real frozen :class:`RunRequest`, the role observer contract and one factual
:class:`RunResult`. Nothing here wraps the old controller entry: the request
goes through the same initialize, user-message boundary, send/wait/drain
settlement and conservative stop the governed turn and the read-only call
always shared, and the legacy adapter path is exercised by its own suites.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import native_run
from hey_my_buddy.buddy.harnesses.claude.native_run import run, run_discovery
from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunContinuation,
    RunFeedback,
    RunIdentity,
    RunRequest,
    SessionService,
)
from hey_my_buddy.buddy.roles.worker_services import OUTCOME_SCHEMA as WORKER_SCHEMA
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint, LiveWireObserve
from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
from hey_my_buddy.protocol.contracts import HarnessRunLive
from hey_my_buddy.protocol.activity import is_newer, normalize_activity

FIXTURE = Path(__file__).parent / "fixtures/fake_claude.py"
STREAM_FIXTURE = Path(__file__).parent / "fixtures/mock_claude.py"
USAGE_FIXTURE = Path(__file__).parent / "fixtures/fake_claude_usage.py"
CONFIGURATION = {"provider": "anthropic", "model": "claude-opus-5-5[1m]", "effort": "high"}
READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["profileId"],
               "properties": {"profileId": {"type": "string"}, "reason": {"type": "string"},
                              "evidence": {"type": "array", "items": {"type": "string"}}}}


def flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


class NativeRunCase(unittest.TestCase):
    """The shared fake-CLI harness for direct run-seam calls."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-claude-seam-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cwd = self.base / "checkout"
        self.cwd.mkdir()
        FIXTURE.chmod(0o755)
        STREAM_FIXTURE.chmod(0o755)
        USAGE_FIXTURE.chmod(0o755)
        self.state_path = self.base / "fixture.json"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("BUDDY_", "ANTHROPIC_", "CLAUDE_"))
                       and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_DEV_SOURCE="1", BUDDY_CLAUDE_CLI=str(FIXTURE),
                           BUDDY_CLAUDE_FIXTURE_STATE=str(self.state_path),
                           BUDDY_CLAUDE_SETTINGS_POLICY="isolated",
                           BUDDY_STATE_DIR=str(self.base / "state"),
                           BUDDY_RUNTIME_ROOT=str(self.base / "runtime"))
        self.patcher = mock.patch.dict(os.environ, environment, clear=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.case = "ok"

    def fixture_case(self, case: str):
        os.environ["BUDDY_CLAUDE_FIXTURE_CASE"] = case
        self.case = case

    def use_fixture(self, fixture: Path):
        os.environ["BUDDY_CLAUDE_CLI"] = str(fixture)

    def request(self, *, scope: str = "write", schema=None, timeout: int = 10,
                continuation=None, network=None, denied=()) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="task-1", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                                 generation=1, invocation_id=uuid.uuid4().hex, turn_id="turn-1"),
            harness="claude",
            configuration=RunConfiguration(**CONFIGURATION),
            cwd=str(self.cwd),
            private_state=PrivateStatePaths(
                invocation_root=str(self.base / f"invocation-{uuid.uuid4().hex[:8]}"),
                native_root=str(self.base / f"native-{uuid.uuid4().hex[:8]}")),
            input_text="fixture governed prompt", tool_scope=scope,
            output_schema=schema if schema is not None else WORKER_SCHEMA,
            budget=RunBudget(timeout_seconds=timeout), continuation=continuation,
            network_allowed_domains=network, additional_denied_tools=denied)

    def execute(self, request: RunRequest, *, observer=None):
        return run(request, observer=observer or (lambda _facts: FEEDBACK_CONTINUE),
                   services=None, cancelled=lambda: False)

    def fixture_state(self) -> dict:
        return json.loads(self.state_path.read_text())


class WorkerCarrierTests(NativeRunCase):
    def test_one_write_run_delivers_the_native_schema_value_and_stop_facts(self):
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.end.reason_code)
        # The schema verdict is the role's check; the driver reports the
        # delivered value with its own raw text and parsed form.
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")
        self.assertEqual(result.value.raw, json.dumps(result.value.parsed.value,
                                                     separators=(",", ":"), ensure_ascii=False))
        completion = result.completion_evidence
        self.assertTrue(completion.stream_end)
        self.assertTrue(result.model_started)
        # The confirmed root is the session the CLI was actually launched with.
        self.assertEqual(result.native_identity.session_id,
                         flag_value(self.fixture_state()["argv"], "--session-id"))
        self.assertNotIn("--resume", self.fixture_state()["argv"])
        self.assertEqual(result.tool_evidence.value["nativeIdentity"],
                         [{"sessionId": result.native_identity.session_id}])
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)

    def test_the_catalog_check_confirms_only_what_native_readback_proved(self):
        result = self.execute(self.request())
        checked = result.configuration.checked
        # The checked block restates exactly the requested values the native
        # checks confirmed; no per-check ledger travels beside it.
        self.assertEqual(checked.provider.value, "anthropic")
        self.assertEqual(checked.model.value, CONFIGURATION["model"])
        self.assertEqual(checked.effort.value, CONFIGURATION["effort"])

    def test_a_model_outside_the_catalog_is_refused_before_the_user_message(self):
        self.fixture_case("ok")
        request = self.request()
        broken = request.model_copy(update={"configuration": RunConfiguration(
            provider="anthropic", model="claude-not-in-catalog", effort="high")})
        result = self.execute(broken)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-configuration")
        # Nothing is claimed that the run never reached.
        self.assertFalse(result.model_started)
        checked = result.configuration.checked
        self.assertIsNone(checked.provider)
        self.assertIsNone(checked.model)
        self.assertIsNone(checked.effort)
        self.assertEqual(self.fixture_state()["userTurns"], 0)

    def test_the_activity_fact_and_observed_evidence_survive_the_run(self):
        result = self.execute(self.request())
        activity = result.activity.value
        self.assertEqual(activity["phase"], "finishing")
        self.assertEqual(activity["counts"]["modelTurns"], 1)
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        # The observed model set is a bounded observation, never an identity.
        self.assertEqual(observations["observedModels"],
                         ["claude-haiku-4-5-20251001", "claude-opus-5-5[1m]"])
        self.assertEqual(observations["totalCostUsd"], 0.01)
        self.assertEqual(observations["sessionModel"], CONFIGURATION["model"])
        turn_facts = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                          if ref.kind == "native-turn-facts")).read_bytes())
        self.assertEqual(turn_facts["structuredOutputSource"], "json-schema")
        self.assertTrue(turn_facts["initObserved"])
        self.assertTrue(turn_facts["backgroundSettled"])
        self.assertIn("native-stderr", {ref.kind for ref in result.evidence_refs})

    def test_usage_comes_from_the_native_records_only(self):
        self.use_fixture(USAGE_FIXTURE)
        self.fixture_case("usage-ok")
        request = self.request(timeout=20).model_copy(update={"configuration": RunConfiguration(
            provider="anthropic", model="claude-fixture-5", effort="high")})
        result = self.execute(request)
        self.assertEqual(result.end.status, "ok", result.end.message)
        usage = result.usage.value
        self.assertEqual(usage["source"], "claude/stream-json-result-usage")
        self.assertEqual(usage["completeness"], "complete")
        # The native prompt count excludes cached input; the canonical attempt
        # total adds the cache back instead of undercounting it.
        self.assertEqual(usage["inputTokens"], 88000)
        self.assertEqual(usage["cachedInputTokens"], 73000)
        self.assertEqual(usage["outputTokens"], 350)
        self.assertEqual(usage["reasoningOutputTokens"], 25)
        self.assertEqual(result.last_assistant_message.value["sourceId"], "msg-fixture-0002")
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertEqual(observations["observedModels"], ["claude-fixture-5"])
        # The unified-windows fractions convert to percentage windows; an
        # out-of-range or missing value would contribute no window.
        quota = observations["quota"]
        self.assertEqual(quota["source"], "claude/stream-json-rate-limit-event")
        windows = {window["name"]: window["usedPercent"] for window in quota["windows"]}
        self.assertEqual(windows, {"five_hour": 42.0, "seven_day": 90.0})

    def test_a_denied_native_permission_is_a_reported_fact_not_an_outcome(self):
        self.fixture_case("permission")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        # The refused interaction's own full record — native request and tool
        # identities included — travels in the retained evidence reference.
        records = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                       if ref.kind == "denied-interactions")).read_bytes())["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["toolName"], records[0]["requestId"]),
                         ("WebFetch", "perm-1"))
        # The outcome rewrite is the role's assembly; the delivered value keeps
        # its own completed fact for the role to judge.
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")

    def test_background_work_must_settle_before_the_result_is_usable(self):
        self.fixture_case("bg-running")
        unsettled = self.execute(self.request())
        self.assertEqual(unsettled.end.status, "error")
        self.assertEqual(unsettled.end.reason_code, "background-work-unsettled")
        self.assertIsNone(unsettled.completion_evidence)
        self.fixture_case("bg-settled")
        settled = self.execute(self.request())
        self.assertEqual(settled.end.status, "ok", settled.end.message)
        self.assertTrue(settled.completion_evidence.stream_end)


class ReadCarrierTests(NativeRunCase):
    #: The review-read posture the role passes explicitly: offline plus the
    #: session-wide denials the old review call derived from its schema.
    REVIEW_DENIALS = ("mcp__*", "WebFetch", "WebSearch", "Agent", "Task")

    def argv_and_settings(self, result):
        argv = self.fixture_state()["argv"]
        settings = json.loads(Path(flag_value(argv, "--settings")).read_bytes())
        return argv, settings

    def test_one_review_read_takes_the_role_requested_posture_and_reports_it(self):
        result = self.execute(self.request(scope="read", schema=READ_SCHEMA,
                                           network=(), denied=self.REVIEW_DENIALS))
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.parsed.value["profileId"], "legal")
        policy = result.effective_policy.tools
        self.assertEqual(policy.requested.value["tools"], ["Glob", "Grep", "LS", "Read"])
        self.assertEqual(policy.requested.value["permissionMode"], "default")
        # The denials and the offline allowlist are the request's own values.
        self.assertEqual(policy.requested.value["disallowedTools"],
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write", *self.REVIEW_DENIALS])
        self.assertEqual(policy.requested.value["sandboxNetworkAllowedDomains"], [])
        argv, settings = self.argv_and_settings(result)
        self.assertEqual(settings["sandbox"]["network"]["allowedDomains"], [])
        self.assertEqual(flag_value(argv, "--disallowedTools").split(","),
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write", *self.REVIEW_DENIALS])

    def test_an_ordinary_worker_read_keeps_the_native_default_posture(self):
        # No controls requested: the read scope alone must not tighten the
        # network allowlist or add the review denials.
        result = self.execute(self.request(scope="read"))
        self.assertEqual(result.end.status, "ok", result.end.message)
        policy = result.effective_policy.tools.requested.value
        self.assertEqual(policy["disallowedTools"],
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])
        from hey_my_buddy.buddy.harnesses.claude.config import PACKAGE_REGISTRY_DOMAINS
        self.assertEqual(policy["sandboxNetworkAllowedDomains"], list(PACKAGE_REGISTRY_DOMAINS))
        _argv, settings = self.argv_and_settings(result)
        self.assertEqual(settings["sandbox"]["network"]["allowedDomains"],
                         list(PACKAGE_REGISTRY_DOMAINS))

    def test_the_unified_postures_match_the_legacy_three_paths_exactly(self):
        # The unified run's argv/settings must equal what the config derives
        # for the same posture: worker postures pass the worker schema and no
        # extra denials, the review posture passes the caller's answer schema
        # and its explicit denial sequence. Each run's argv is captured from
        # the fixture state immediately, before the next run overwrites it.
        from hey_my_buddy.buddy.harnesses.claude.config import (
            execution_args,
            sandbox_settings,
        )

        def normalized(argv):
            return [flag_value(argv, flag) if flag in argv else None
                    for flag in ("--tools", "--permission-mode", "--disallowedTools")]

        session = "00000000-0000-4000-8000-000000000000"
        postures = (
            ("worker-write", {}, execution_args(session_id=session, model="m", effort="high",
                                                settings_path="/s.json", read_only=False,
                                                output_schema=WORKER_SCHEMA),
             sandbox_settings()),
            ("worker-read", {"scope": "read"},
             execution_args(session_id=session, model="m", effort="high",
                            settings_path="/s.json", read_only=True, output_schema=WORKER_SCHEMA),
             sandbox_settings()),
            ("review", {"scope": "read", "schema": READ_SCHEMA, "network": (),
                        "denied": self.REVIEW_DENIALS},
             execution_args(session_id=session, model="m", effort="high",
                            settings_path="/s.json", read_only=True, output_schema=READ_SCHEMA,
                            additional_denied_tools=self.REVIEW_DENIALS),
             sandbox_settings(())),
        )
        for name, request_values, legacy_argv, legacy_settings in postures:
            with self.subTest(posture=name):
                result = self.execute(self.request(**request_values))
                self.assertEqual(result.end.status, "ok", result.end.message)
                argv, settings = self.argv_and_settings(result)
                self.assertEqual(normalized(argv), normalized(legacy_argv))
                self.assertEqual(settings, legacy_settings)

    def test_a_write_run_keeps_the_registry_allowlist_and_its_subagent_tools(self):
        result = self.execute(self.request(scope="write"))
        self.assertEqual(result.end.status, "ok", result.end.message)
        policy = result.effective_policy.tools.requested.value
        self.assertIn("Agent", policy["tools"])
        self.assertNotIn("mcp__*", policy["disallowedTools"])
        _argv, settings = self.argv_and_settings(result)
        self.assertTrue(settings["sandbox"]["network"]["allowedDomains"])

    def test_the_shared_send_wait_drain_serves_both_carriers(self):
        # The same fixture run under both scopes settles through the same
        # primitive: both report the observed session, the explicit success
        # criteria and a stream complete only at the observed end.
        for scope in ("write", "read"):
            with self.subTest(scope=scope):
                result = self.execute(self.request(scope=scope))
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertTrue(result.completion_evidence.stream_end)
                # The observed native event count is a real count of this run.
                self.assertGreaterEqual(result.native_event_count, 1)


class RequestGateTests(NativeRunCase):
    def test_this_harness_declares_no_no_tool_service_or_resume_capability(self):
        with self.assertRaises(BoardError):
            self.execute(self.request(scope="none"))
        request = self.request()
        with self.assertRaises(BoardError):
            run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                services=object(), cancelled=lambda: False)
        with self.assertRaises(BoardError):
            self.execute(request.model_copy(update={"session_services": (
                SessionService(tool_names=["mcp__x__y"]),)}))
        with self.assertRaises(BoardError):
            self.execute(self.request(continuation=RunContinuation(mode="native-session",
                                                                  previous_session_id="00000000-0000-4000-8000-000000000000")))
        # The reconstruction mode stays legal and never resumes natively.
        self.fixture_case("ok")
        reconstructed = self.execute(self.request(
            continuation=RunContinuation(mode="reconstructed-new-session",
                                         previous_session_id=str(uuid.uuid4()))))
        self.assertEqual(reconstructed.end.status, "ok", reconstructed.end.message)
        self.assertFalse(reconstructed.continuation.resumable)
        self.assertNotIn("--resume", self.fixture_state()["argv"])

    def test_the_module_declares_the_request_controls_it_consumes(self):
        self.assertEqual(native_run.supported_request_controls,
                         ("network_allowed_domains", "additional_denied_tools"))

    def test_the_module_carries_the_registered_role_surface(self):
        # 3-B2 delivers the wiring: the one native body also exposes every
        # operation the shared role executor consumes, and the legacy adapter
        # entries are gone — registration is the only switch left, and it is
        # the Host's line in the registry.
        for operation in ("run", "run_discovery", "check_preparation", "session_facts",
                          "validate_turn_provenance", "prepare_run_services"):
            self.assertTrue(callable(getattr(native_run, operation, None)), operation)
        from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
        description = ClaudeAdapter()
        self.assertFalse(any(hasattr(description, entry) for entry in
                             ("prepare", "start", "collect", "cancel",
                              "start_read_only_structured", "start_no_tool_structured")))
        self.assertEqual(description.validate_turn_provenance({"provenance": {}}),
                         "the Claude turn lacks native completion and initialization evidence")

    def test_third_party_provider_overrides_are_refused_before_spawn(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://gateway.example"
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "third-party-provider")
        # Nothing was ever spawned: the confirmed never-spawned fact and no
        # native exit code.
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertIsNone(result.end.native_exit_code)
        self.assertFalse(self.state_path.exists())

    def test_an_unsupported_settings_policy_is_refused(self):
        os.environ["BUDDY_CLAUDE_SETTINGS_POLICY"] = "inherit"
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "settings-policy-unsupported")


class SettlementGuardTests(NativeRunCase):
    def test_model_output_before_the_user_message_is_rejected_with_its_facts_kept(self):
        self.use_fixture(STREAM_FIXTURE)
        # The premature frame is written before the initialize response, so
        # the boundary rejection follows from pipe order, not thread timing.
        self.fixture_case("early-before-response")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-started-early")
        # The premature tool fact was projected before the boundary rejection.
        package = result.tool_evidence.value
        self.assertEqual(package["events"][0]["toolName"], "Read")
        self.assertFalse(package["streamComplete"])

    def test_the_native_identity_is_published_only_after_native_confirmation(self):
        # An unconfirmed handshake leaves the preallocated id out of the
        # native identity; the identity the stream did report stays in the
        # fact packages, never masquerading as this run's root.
        self.fixture_case("invalid-json")
        broken = self.execute(self.request(timeout=20))
        self.assertEqual(broken.end.status, "error")
        self.assertIsNone(broken.native_identity)
        # No native root was ever confirmed, and the package lists none.
        self.assertEqual(broken.tool_evidence.value["nativeIdentity"], [])
        self.assertTrue(broken.model_started, "the input send itself is still a fact")
        self.fixture_case("init-wrong-session")
        foreign = self.execute(self.request())
        self.assertEqual(foreign.end.status, "error")
        self.assertEqual(foreign.end.reason_code, "wrong-native-session")
        self.assertIsNone(foreign.native_identity)
        # The foreign identity the native stream actually reported.
        self.assertEqual(foreign.tool_evidence.value["nativeIdentity"],
                         [{"sessionId": "not-the-allocation"}])
        self.assertIsNone(foreign.continuation)

    def test_a_native_failure_keeps_the_observed_stream_end(self):
        # The transport's own EOF is a fact of the stream: an explicit native
        # failure does not erase it, and the business verdict travels in the
        # end status, the group disappearance in the stop evidence.
        self.fixture_case("failed")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        self.assertIsNone(result.completion_evidence)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)

    def test_wrong_session_and_duplicate_results_are_rejected(self):
        for case, reason in (("wrong-session", "wrong-native-session"),
                             ("duplicate-result", "invalid-protocol"),
                             ("no-result", "transport-error")):
            with self.subTest(case=case):
                self.fixture_case(case)
                result = self.execute(self.request())
                self.assertEqual(result.end.status, "error", result.end.message)
                self.assertEqual(result.end.reason_code, reason)

    def test_an_oversized_delivered_value_drops_alone_and_keeps_every_confirmed_fact(self):
        # A 600 KiB structured delivery — under the native 8 MiB frame bound,
        # over the run contract's value bound — must cost the value alone:
        # the confirmed stop, the checked configuration, the usage and the
        # completion evidence all still reach the caller.
        self.fixture_case("large-value")
        result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.value)
        self.assertIsNotNone(result.completion_evidence)
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.end.native_exit_code, 0)
        self.assertEqual(result.configuration.checked.model.value, CONFIGURATION["model"])
        self.assertIsNotNone(result.last_assistant_message)

    def test_a_quota_rejection_is_infrastructure_not_a_model_result(self):
        self.fixture_case("quota-rejected")
        result = self.execute(self.request(timeout=20))
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "quota-rejected")
        self.assertIn("ADAPTER_UNAVAILABLE", result.end.message)
        failure = result.native_failure.value
        self.assertEqual(failure["nativeCode"], "five_hour")
        self.assertEqual(failure["source"], "claude/stream-json-rate-limit-event")
        self.assertIsNone(result.value)
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertTrue(self.fixture_state()["interrupted"])
        # The observations package carries the legacy bounded receipt shape —
        # the two fields, the original reset value, nothing on a clean run —
        # and the interrupt pair with the peer's own acknowledgement reply.
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertEqual(observations["quotaFailure"],
                         {"rateLimitType": "five_hour", "resetsAt": "2026-09-26T12:00:00Z"})
        self.assertIs(observations["nativeInterruptRequested"], True)
        self.assertIs(observations["nativeInterruptAcknowledged"], True)
        self.fixture_case("ok")
        clean = self.execute(self.request())
        observations = json.loads(Path(next(ref.location for ref in clean.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        self.assertNotIn("quotaFailure", observations)
        self.assertNotIn("nativeInterruptRequested", observations)
        self.assertNotIn("nativeInterruptAcknowledged", observations)

    def test_cancellation_interrupts_the_native_run(self):
        self.fixture_case("hang")
        result = run(self.request(timeout=20), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: True)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "user-cancel")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertTrue(self.fixture_state()["interrupted"])
        observations = json.loads(Path(next(ref.location for ref in result.evidence_refs
                                            if ref.kind == "native-observations")).read_bytes())
        # The real reply of the interrupted peer, not the later group stop.
        self.assertIs(observations["nativeInterruptAcknowledged"], True)

    def test_an_unconfirmed_native_group_stop_reports_unknown_not_gone(self):
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        self.fixture_case("ok")
        observed: list[ProcessHandle] = []

        def unconfirmed(handle, settle_seconds: float = 2.0) -> bool:
            observed.append(handle)
            return False

        with mock.patch.object(ProcessHandle, "shutdown_confirmed", unconfirmed):
            result = self.execute(self.request())
        native = result.stop_evidence.native
        self.assertEqual(native.group_state, "unknown")
        self.assertIsNotNone(result.end.native_exit_code)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        # The EOF the drain already observed and the group's disappearance are
        # separate facts: an unconfirmed stop never back-infers the stream end.
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertTrue(result.tool_evidence.value["streamComplete"])
        # No orphan: the halt really terminated the fake CLI's group.
        handle = observed[0]
        self.assertIsNotNone(handle.process.poll())
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.5))

    def test_a_late_unrepresentable_package_keeps_every_other_observed_fact(self):
        self.fixture_case("ok")
        with mock.patch("hey_my_buddy.buddy.harnesses.claude.protocol.TurnEvidence.token_usage",
                        return_value={"inputTokens": "not-a-number"}):
            result = self.execute(self.request())
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")
        # No completion-tool receipt exists on this carrier; the native
        # schema's own stream-end fact is what completion evidence carries.
        self.assertTrue(result.completion_evidence.stream_end)


class ObserverContractTests(NativeRunCase):
    def stopping_observer(self):
        seen: list[dict] = []

        def observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        return observer, seen

    def test_a_tool_fact_stops_the_run_through_the_observer_feedback(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("clean")
        observer, seen = self.stopping_observer()
        result = self.execute(self.request(), observer=observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertTrue(package["events"])
        # The cumulative counts reached the role before the stop; unknown
        # events stay the honest absence this harness never classified.
        self.assertTrue(any(facts["toolCalls"] > 0 for facts in seen))
        self.assertTrue(all(facts["unknownEvents"] is None for facts in seen))

    def test_a_denied_interaction_reaches_the_role_before_further_waiting(self):
        self.fixture_case("permission")
        observer, seen = self.stopping_observer()

        def denied_watcher(facts):
            seen.append(dict(facts))
            if int(facts.get("deniedInteractions") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.request(), observer=denied_watcher)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(any(facts["deniedInteractions"] > 0 for facts in seen))
        # The peer received its deny answer before the stop took effect.
        self.assertEqual(self.fixture_state()["permissionReply"]["behavior"], "deny")

    def test_a_correction_is_refused_this_harness_takes_none(self):
        self.fixture_case("ok")

        def correcting(_facts):
            return RunFeedback(action="correct", input_text="again")
        with self.assertRaises(BoardError) as caught:
            self.execute(self.request(), observer=correcting)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.fixture_state()["userTurns"], 1)


class DiscoveryTests(NativeRunCase):
    def test_discovery_initializes_only_and_never_sends_a_user_message(self):
        catalog = run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discover-invocation",
                                native_root=self.base / "discover-native", timeout_seconds=25,
                                cancelled=lambda: False)
        models = [model["id"] for model in catalog["providers"][0]["models"]]
        self.assertIn(CONFIGURATION["model"], models)
        # The default alias never becomes a catalog identity.
        self.assertTrue(all(model != "default" for model in models))
        state = self.fixture_state()
        self.assertFalse(state["userTurns"])
        self.assertTrue(state["initialize"])
        self.assertIn("--no-session-persistence", state["argv"])
        self.assertTrue((self.base / "discover-invocation" / "native.stderr.log").is_file())

    def test_discovery_failures_carry_the_actual_native_stop_fact(self):
        # A pre-spawn refusal never started anything: the known never-started
        # fact, not a guess from the error code — and no native child exists.
        os.environ["ANTHROPIC_BASE_URL"] = "https://gateway.example"
        try:
            with self.assertRaises(native_run.ClaudeProtocolError) as caught:
                run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-pre",
                              native_root=self.base / "ds-pre-n", timeout_seconds=25, cancelled=lambda: False)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
            self.assertFalse(self.state_path.exists(), "no native child may exist for a pre-spawn refusal")
        finally:
            del os.environ["ANTHROPIC_BASE_URL"]
        # A settled refusal after a confirmed stop carries the true fact, and
        # the outer layer may recycle its directory on it.
        self.fixture_case("no-auth")
        with self.assertRaises(native_run.ClaudeProtocolError) as caught:
            run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-ok",
                          native_root=self.base / "ds-ok-n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "first-party-auth-required")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)

    def test_discovery_metadata_error_with_an_unknown_stop_reports_false(self):
        # The metadata refusal itself is real, but an unobserved native stop
        # must stay false: the outer layer keeps the directory for inspection
        # instead of trusting the error to mean the group is gone.
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        self.fixture_case("no-auth")
        with mock.patch.object(ProcessHandle, "shutdown_confirmed",
                               side_effect=lambda settle_seconds=2.0: False):
            with self.assertRaises(native_run.ClaudeProtocolError) as caught:
                run_discovery(cwd=str(self.cwd), invocation_root=self.base / "ds-unk",
                              native_root=self.base / "ds-unk-n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "first-party-auth-required")
        self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
        # The witness is the real fact, never the error code: the same code
        # carried True when the stop was actually observed.

    def test_discovery_refuses_third_party_overrides_and_bad_cli(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://gateway.example"
        with self.assertRaises(native_run.ClaudeProtocolError) as caught:
            run_discovery(cwd=str(self.cwd), invocation_root=self.base / "d2",
                          native_root=self.base / "d2n", timeout_seconds=25, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "third-party-provider")
        del os.environ["ANTHROPIC_BASE_URL"]
        os.environ["BUDDY_CLAUDE_CLI"] = str(self.base / "missing-cli")
        with self.assertRaises(native_run.ClaudeUnavailable) as caught:
            run_discovery(cwd=str(self.cwd), invocation_root=self.base / "d3",
                          native_root=self.base / "d3n", timeout_seconds=25, cancelled=lambda: False)
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)


class RoleSurfaceTests(NativeRunCase):
    """The operations the shared role executor consumes beyond the run itself."""

    def test_check_preparation_refuses_before_any_spawn(self):
        from hey_my_buddy.buddy.harnesses.claude.config import ClaudeUnavailable
        with self.assertRaises(BoardError) as caught:
            native_run.check_preparation({"provider": "openai"}, dict(os.environ))
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn("first-party Anthropic provider", caught.exception.message)
        with self.assertRaises(BoardError) as caught:
            native_run.check_preparation({"provider": "anthropic"},
                                         {**os.environ, "BUDDY_CLAUDE_SETTINGS_POLICY": "global"})
        self.assertIn("BUDDY_CLAUDE_SETTINGS_POLICY=isolated", caught.exception.message)
        with self.assertRaises(BoardError) as caught:
            native_run.check_preparation({"provider": "anthropic"},
                                         {**os.environ, "ANTHROPIC_BASE_URL": "https://gw.example"})
        self.assertIn("ANTHROPIC_BASE_URL", caught.exception.message)
        self.assertNotIn("gw.example", caught.exception.message)
        with mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1", "BUDDY_CLAUDE_CLI": str(self.base / "nope")}):
            with mock.patch.object(native_run, "cli_command", side_effect=ClaudeUnavailable("CLI missing")):
                with self.assertRaises(BoardError) as caught:
                    native_run.check_preparation({"provider": "anthropic"}, dict(os.environ))
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertFalse(self.state_path.exists(), "no native child may start from a preparation check")
        native_run.check_preparation({"provider": "anthropic"}, dict(os.environ))

    def test_the_optional_live_service_binding_consumes_the_endpoint(self):
        binding = native_run.prepare_run_services()
        self.assertEqual(binding, native_run.RunServices())
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(endpoint.publish_activity.call_args.args[0], result.activity.value)
        with self.assertRaises(BoardError):
            run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                services={"live": endpoint}, cancelled=lambda: False)

    def test_session_facts_report_the_user_store_without_a_binding_claim(self):
        facts = native_run.session_facts(self.base / "native", "session-x")
        self.assertEqual(facts["adapter"], "claude")
        self.assertEqual((facts["sessionId"], facts["captured"]), ("session-x", True))
        self.assertEqual(facts["storageScope"], "harness-user-store")
        self.assertEqual(facts["nativeAppVisibility"], "unknown")
        self.assertNotIn("bindingPresent", facts)
        empty = native_run.session_facts(self.base / "native", None)
        self.assertFalse(empty["captured"])

    def test_native_events_publish_the_identity_bound_shared_endpoint_activity(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("clean")
        request = self.request()
        endpoint = CTwoLiveEndpoint(request.identity, EXISTING_CAPABILITIES["claude"], HarnessRunLive,
                                   instance_id="a" * 64, token="b" * 64)
        with mock.patch.object(endpoint, "publish_activity", wraps=endpoint.publish_activity) as publish:
            result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        payloads = [call.args[0] for call in publish.call_args_list]
        self.assertEqual([payload["phase"] for payload in payloads],
                         ["waiting-model", "starting", "tool-running", "streaming-model", "finishing"])
        self.assertEqual(payloads[-1]["counts"], {"modelTurns": 1, "toolCalls": 1})
        self.assertTrue(all(normalize_activity(payload) == payload for payload in payloads))
        self.assertTrue(all(is_newer(new, old) for old, new in zip(payloads, payloads[1:])))
        query = LiveWireObserve(identity=request.identity, instance_id="a" * 64, token="b" * 64,
                                fields=("activity",), limit=10)
        snapshot = json.loads(endpoint.observe(json.dumps(query.to_payload())))
        self.assertEqual(snapshot["activity"], result.activity.value)
        foreign = query.model_copy(update={"identity": request.identity.model_copy(
            update={"attempt_id": "other-attempt"})})
        self.assertIsNone(json.loads(endpoint.observe(json.dumps(foreign.to_payload())))["activity"])
        self.assertFalse(Path(request.private_state.invocation_root, "activity.json").exists())

    def test_refused_activity_publication_keeps_native_final_activity(self):
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = False
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertEqual(result.activity.value["counts"]["modelTurns"], 1)
        self.assertGreater(endpoint.publish_activity.call_count, 0)
        publisher = native_run.ActivityPublisher(endpoint.publish_activity)
        self.assertFalse(publisher.publish(result.activity.value))
        self.assertIsNone(publisher.current(), "refusal is never a transmitted receipt")

    def test_unknown_native_tools_keep_actual_callback_counts_and_final_facts(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("unknown")
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.activity.value["counts"], {"modelTurns": 2, "toolCalls": 2})
        receipts = [call.args[0] for call in endpoint.publish_activity.call_args_list]
        self.assertEqual(receipts[-1], result.activity.value)
        self.assertEqual([receipt["phase"] for receipt in receipts],
                         ["waiting-model", "starting", "tool-running", "finishing"])

    def test_native_failure_preserves_the_actual_callback_activity(self):
        self.fixture_case("failed")
        endpoint = mock.Mock(spec=CTwoLiveEndpoint)
        endpoint.publish_activity.return_value = True
        result = run(self.request(), observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=native_run.RunServices(live=endpoint), cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, "native-turn-failed")
        self.assertEqual(endpoint.publish_activity.call_args.args[0], result.activity.value)
        self.assertEqual(result.activity.value["phase"], "finishing")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")


class StructuredDeliveryTests(NativeRunCase):
    """The CLI's built-in StructuredOutput delivery is completion evidence, not a tool call.

    Every case drives the real run seam with the stream fixture's scripted
    native frames; the identification is the projection's own native facts —
    this run's schema command line, the confirmed root, the exact built-in
    name, the call id and the final structured value it delivered.
    """

    def delivered(self):
        return {"profileId": "legal", "reason": "Read-only stream fixture", "evidence": []}

    def review_request(self, **kwargs):
        return self.request(scope="read", schema=READ_SCHEMA, network=(),
                            denied=ReadCarrierTests.REVIEW_DENIALS, **kwargs)

    def package_of(self, result):
        self.assertEqual(result.end.status, "ok", result.end.message)
        return result.tool_evidence.value

    def test_a_verified_delivery_is_completion_evidence_and_never_a_tool_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-clean")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The real Read call counts and settles; the delivery and its
        # tool_result pairing are gone from the ordinary tool facts.
        self.assertEqual([(event["toolName"], event["phase"]) for event in package["events"]],
                         [("Read", "start"), ("Read", "end")])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))
        # The verified delivery is excluded from the tool facts by its call
        # identity and delivered as the run's own value.
        self.assertNotIn("StructuredOutput", [event["toolName"] for event in package["events"]])
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.value.parsed.value, self.delivered())

    def test_a_zero_tool_budget_accepts_a_structured_review(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-only")
        seen: list[dict] = []

        def budget_observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.review_request(), observer=budget_observer)
        package = self.package_of(result)
        self.assertEqual((package["toolCalls"], package["events"]), (0, []))
        # The delivery is the run's own value, never a charged tool call.
        self.assertEqual(result.value.parsed.value, self.delivered())
        # The role's cumulative count never charged the value carrier.
        self.assertTrue(all(facts["toolCalls"] == 0 for facts in seen))
        self.assertTrue(any(facts["settled"] for facts in seen))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_a_zero_budget_unqualified_delivery_stops_the_run_at_settlement(self):
        # The unverified candidate waits for its association mid-run, but the
        # settlement that precedes the role's settled facts releases it: the
        # role's budget stop fires on the same count the package publishes.
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        seen: list[dict] = []

        def budget_observer(facts):
            seen.append(dict(facts))
            if int(facts.get("toolCalls") or 0) > 0:
                return RunFeedback(action="stop")
            return FEEDBACK_CONTINUE
        result = self.execute(self.review_request(), observer=budget_observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertTrue(any(facts["settled"] and facts["toolCalls"] == 1 for facts in seen))
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(len(package["events"]), 2)
        # The unqualified call is no one's delivery: it stays an ordinary
        # counted tool fact under its own native call id.
        self.assertEqual([event["callId"] for event in package["events"]], ["toolu_so_1"] * 2)

    def shared_review_observer(self, budget: int):
        try:
            from hey_my_buddy.buddy.roles.run_observers import ReviewObserver
        except ImportError:
            self.skipTest("the shared ReviewObserver is not in this baseline; its "
                          "authoritative run is the 3-C validation copy")
        return ReviewObserver(budget, READ_SCHEMA, "review the frozen packet", can_correct=False)

    def test_the_shared_review_observer_stops_an_unqualified_zero_budget_delivery(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        review = self.shared_review_observer(0)
        result = self.execute(self.review_request(), observer=review.observer)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(review.stop_reason, "readonly-budget-exhausted")
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_the_shared_review_observer_accepts_a_legal_zero_budget_delivery(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-only")
        review = self.shared_review_observer(0)
        result = self.execute(self.review_request(), observer=review.observer)
        package = self.package_of(result)
        self.assertIsNone(review.stop_reason)
        self.assertEqual((package["toolCalls"], package["events"]), (0, []))
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_a_conflicting_full_input_on_one_call_refuses_the_exemption(self):
        for case in ("structured-conflict-ab", "structured-conflict-ba"):
            with self.subTest(case=case):
                self.use_fixture(STREAM_FIXTURE)
                self.fixture_case(case)
                result = self.execute(self.review_request())
                package = self.package_of(result)
                self.assertEqual(package["toolCalls"], 1)
                self.assertEqual([(event["toolName"], event["phase"]) for event in package["events"]],
                                 [("StructuredOutput", "start"), ("StructuredOutput", "end")])
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                                 tool_evidence.TOOLS_FORBIDDEN)

    def test_an_evicted_first_input_still_refuses_a_conflicting_second(self):
        # Nine delivery uses exhaust the input retention bound; the first
        # call's later, different complete input matches the final value, but
        # the conflict its first input recorded survives the eviction: all
        # nine distinct calls stay counted and none is exempted.
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-evict-conflict")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 9)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_an_evicted_input_reproves_the_value_when_the_same_input_returns(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-evict-same")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The consistent repeat re-proves c1's delivery after the bound evicted
        # its first full text; the eight mismatched calls stay counted and the
        # re-proved delivery alone is the run's own value.
        self.assertEqual(package["toolCalls"], 8)
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_duplicate_stream_and_assistant_projections_exclude_one_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-dupes")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], package["events"]),
                         (0, 0, []))
        # The one verified delivery is excluded from the facts and delivered
        # as the run's own value.
        self.assertEqual(result.value.parsed.value, self.delivered())
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_a_bare_name_without_the_value_association_stays_an_ordinary_tool_call(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-unverified")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["category"] for event in package["events"]], ["other", "other"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)
        # The turn still completed; the call simply proves no delivery.
        self.assertEqual([event["callId"] for event in package["events"]], ["toolu_so_1"] * 2)

    def test_a_mounted_same_name_mcp_tool_is_never_exempted(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-mcp")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["toolName"] for event in package["events"]],
                         ["mcp__server__StructuredOutput"] * 2)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_a_subagent_substream_same_name_call_keeps_its_own_facts(self):
        self.use_fixture(STREAM_FIXTURE)
        self.fixture_case("structured-subagent")
        result = self.execute(self.review_request())
        package = self.package_of(result)
        # The root's delivery is excluded; the subagent's look-alike call stays
        # a fact under its parent-call identity and fails the root check.
        self.assertEqual(package["toolCalls"], 1)
        subagent_events = [event for event in package["events"]
                           if event["toolName"] == "StructuredOutput"]
        self.assertEqual(len(subagent_events), 2)
        self.assertTrue(all("callId" in event["nativeIdentity"] for event in subagent_events))
        # The look-alike call settles as a disallowed other call; its foreign
        # identity is kept in the facts on top of that.
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
