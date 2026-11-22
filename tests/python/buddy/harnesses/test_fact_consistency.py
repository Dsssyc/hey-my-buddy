"""Compare shared run facts from the four fake-native subprocesses."""
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

from buddy.harnesses.fixture_selection import select_command
from hey_my_buddy.blackboard.evaluation.native_observations import persist
from hey_my_buddy.blackboard.store.db import Database, utc_now
from hey_my_buddy.buddy.harnesses import run_contract
from hey_my_buddy.buddy.harnesses.claude import native_run as claude
from hey_my_buddy.buddy.harnesses.codex import native_run as codex
from hey_my_buddy.buddy.harnesses.dsh import native_run as dsh
from hey_my_buddy.buddy.harnesses.registry import worker_format
from hey_my_buddy.buddy.harnesses.zcode import native_run as zcode
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.run_execution import _worker_facts
from hey_my_buddy.buddy.roles.run_observers import worker_observer
from hey_my_buddy.protocol.turn_checkpoint import input_hash
from hey_my_buddy.protocol.turn_output import validate_outcome


HERE = Path(__file__).parent
FIXTURES = {
    "claude": (claude, "fake_claude.py", "HMB_CLAUDE_FIXTURE_CASE",
               ("anthropic", "claude-opus-5-5[1m]", "high")),
    "codex": (codex, "no_tool_codex.py", "HMB_CODEX_FIXTURE_CASE",
              ("openai", "fixture-model", "low")),
    "dsh": (dsh, "workflow_agent.py", None, ("deepseek-official", "deepseek-flash", "high")),
    "zcode": (zcode, "mock_zcode.py", "HMB_ZCODE_TEST_CASE", ("fixture-api", "fixture-model", "low")),
}
DSH_CASE_ARGS = {
    "ok": (),
    "unsupported-request": ("--fact-case", "unsupported-request"),
    "permission-denial": ("--fact-case", "permission-denial"),
    "missing-model": ("--fact-case", "missing-model"),
    "unknown-event": ("--emit-unknown-update",),
    "quota": ("--workflow-mode", "quota"),
    "rate-limited": ("--workflow-mode", "quota", "--native-error-code", "RATE_LIMIT"),
}


class NativeFactConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="native-facts-"))).resolve()

    def run_case(self, harness, case, *, root=None, previous=None):
        root = root if root is not None else Path(tempfile.mkdtemp(dir=self.root))
        marker = uuid.uuid4().hex
        invocation = root / f"run-{marker}"
        native = root / "native"
        cwd = root / "checkout"
        home = root / "home"
        cwd.mkdir(exist_ok=True)
        home.mkdir(exist_ok=True)
        module, fixture, case_variable, (provider, model, effort) = FIXTURES[harness]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("HMB_")}
        environment.update(
            HOME=str(home), USERPROFILE=str(home), CLAUDE_CONFIG_DIR=str(home / ".claude"),
            CODEX_HOME=str(home / ".codex"), DSH_HOME=str(home / ".dsh"), ZCODE_DATA_BASE_DIR=str(home),
            HMB_STATE_DIR=str(root / "state"), HMB_RUNTIME_ROOT=str(root / "runtime"), HMB_DEV_SOURCE="1",
            HMB_CLAUDE_FIXTURE_STATE=str(root / "claude.json"), HMB_CODEX_FIXTURE_STATE=str(root / "codex.json"),
        )
        command = [sys.executable, str(HERE / harness / "fixtures" / fixture)]
        if harness == "dsh":
            command.extend(("--log", str(root / "agent.jsonl"), "--fixture-root", str(root),
                            "--prompt-mode", "final", *DSH_CASE_ARGS[case]))
        else:
            environment[case_variable] = case
        select_command(environment, root, harness, command)

        identity = {"taskId": "fixture-task", "attemptId": f"attempt-{marker}",
                    "generation": 2 if previous else 1, "turnId": f"turn-{marker}"}
        sha = input_hash(identity)
        schema = worker_format(harness).schema if harness == "claude" else {"type": "object"}
        continuation = (run_contract.RunContinuation(mode="native-session", previous_session_id=previous)
                        if previous else None)
        services = None
        session_services = ()
        if harness == "zcode":
            schema = worker_services.OUTCOME_SCHEMA
            binding = zcode.prepare_services(
                invocation_root=invocation, identity=identity, input_sha256=sha,
                attention_path=invocation / "attention.json", session_tools=worker_services.session_tools(checkpoint=False),
                completion_tool="hey_my_buddy_finish_turn", validate_outcome=validate_outcome,
                inquiry=None, inquiry_tools=(), native_stderr=str(invocation / "stderr.log"),
            )
            services = binding.services
            session_services = (binding.description,)
        request = run_contract.RunRequest(
            identity=run_contract.RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                                              generation=identity["generation"], turn_id=identity["turnId"],
                                              invocation_id=marker, input_sha256=sha),
            harness=harness, buddy=run_contract.RunBuddy(provider=provider, model=model, effort=effort),
            cwd=str(cwd), session_record_path=str(invocation / "session.jsonl"),
            private_state=run_contract.PrivateStatePaths(state_dir=environment["HMB_STATE_DIR"],
                                                         invocation_root=str(invocation), native_root=str(native)),
            input_text="Return the fixture result.", permission="write" if harness in ("claude", "zcode") else "none",
            output_schema=schema, budget=run_contract.RunBudget(timeout_seconds=20), continuation=continuation,
            session_services=session_services,
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            return module.run(request, observer=worker_observer, services=services, cancelled=lambda: False)

    def test_unsupported_reverse_requests_are_retained_without_permission_denials(self):
        for harness, case, method in (
            ("claude", "unsupported-request", "future_callback"),
            ("codex", "unsupported-request", "unknown/method"),
            ("dsh", "unsupported-request", "unknown/method"),
            ("zcode", "unsupported-requests", "interaction/browserList"),
        ):
            with self.subTest(harness=harness):
                result = self.run_case(harness, case)
                self.assertEqual(result.end.status, "ok", result.end)
                requests = result.native_requests
                self.assertIsNotNone(requests)
                self.assertGreaterEqual(requests.unsupported_requests, 1)
                self.assertIn(method, requests.unsupported_methods)
                self.assertEqual(requests.permission_denials, 0)
                facts = _worker_facts(result)
                self.assertEqual(facts["unsupportedNativeRequests"], requests.unsupported_requests)
                self.assertEqual(facts["unsupportedNativeRequestMethods"], list(requests.unsupported_methods))

    def test_permission_refusals_do_not_project_unsupported_requests(self):
        for harness, case in (("claude", "permission"), ("codex", "request"),
                              ("dsh", "permission-denial"), ("zcode", "permission-denial")):
            with self.subTest(harness=harness):
                result = self.run_case(harness, case)
                requests = result.native_requests
                self.assertIsNotNone(requests)
                self.assertGreaterEqual(requests.permission_denials, 1)
                self.assertEqual(requests.unsupported_requests, 0)
                facts = _worker_facts(result)
                self.assertNotIn("unsupportedNativeRequests", facts)
                self.assertNotIn("unsupportedNativeRequestMethods", facts)

    def test_unconfirmed_readbacks_are_separate_from_unknown_events(self):
        for harness, case, fact in (
            ("claude", "permission-mode-missing", "permission-mode-readback"),
            ("codex", "config-unreadable", "settings-readback"),
            ("dsh", "missing-model", "model-readback"),
            ("zcode", "mode-unrecognized", "permission-mode-readback"),
        ):
            with self.subTest(harness=harness):
                if harness == "zcode":
                    root = Path(tempfile.mkdtemp(dir=self.root))
                    first = self.run_case(harness, "ok", root=root)
                    self.assertEqual(first.end.status, "ok", first.end)
                    result = self.run_case(harness, case, root=root, previous=first.native_identity.session_id)
                else:
                    result = self.run_case(harness, case)
                self.assertEqual(result.end.status, "ok", result.end)
                evidence = result.native_evidence
                self.assertIsNotNone(evidence)
                self.assertIn(fact, evidence.unconfirmed)
                self.assertLessEqual(set(evidence.unconfirmed), set(run_contract.UNCONFIRMED_FACTS))
                self.assertIsNone(evidence.unknown_events)
                facts = _worker_facts(result)["nativeEvidence"]
                self.assertEqual(facts["unconfirmed"], list(evidence.unconfirmed))
                self.assertNotIn("unknownEvents", facts)

    def test_unknown_native_events_are_projected_from_an_otherwise_confirmed_run(self):
        for harness, case in (("claude", "unknown-event"), ("codex", "unknown"),
                              ("dsh", "unknown-event"), ("zcode", "unknown-event")):
            with self.subTest(harness=harness):
                result = self.run_case(harness, case)
                self.assertEqual(result.end.status, "ok", result.end)
                evidence = result.native_evidence
                self.assertIsNotNone(evidence)
                self.assertIsNotNone(evidence.unknown_events)
                self.assertGreaterEqual(evidence.unknown_events.total, 1)
                self.assertEqual(evidence.unconfirmed, ())
                self.assertEqual(_worker_facts(result)["nativeEvidence"]["unknownEvents"],
                                 evidence.unknown_events.to_payload())

    def test_model_identity_uses_the_basis_each_harness_can_confirm(self):
        for harness in FIXTURES:
            with self.subTest(harness=harness):
                result = self.run_case(harness, "ok")
                self.assertEqual(result.end.status, "ok", result.end)
                buddy = result.buddy
                self.assertEqual(buddy.checked.model.value, buddy.requested.model)
                self.assertEqual(buddy.session_model, buddy.requested.model)
                basis = "catalog-validated" if harness == "claude" else "native-readback"
                self.assertEqual(buddy.checked.model.basis, basis)
                if harness == "claude":
                    self.assertEqual(buddy.observed_models,
                                     ("claude-haiku-4-5-20251001", "claude-opus-5-5[1m]"))
                    facts = _worker_facts(result)
                    self.assertEqual(facts["sessionModel"], buddy.session_model)
                    self.assertEqual(facts["observedModels"], list(buddy.observed_models))

        for harness, case in (("codex", "missing-model"), ("dsh", "missing-model"),
                              ("zcode", "config-unrecognized")):
            with self.subTest(harness=harness, case=case):
                result = self.run_case(harness, case)
                self.assertEqual(result.end.status, "ok", result.end)
                buddy = result.buddy
                self.assertIsNone(buddy.session_model)
                if harness == "codex":
                    self.assertEqual(buddy.checked.model.value, buddy.requested.model)
                    self.assertEqual(buddy.checked.model.basis, "catalog-validated")
                else:
                    self.assertIsNone(buddy.checked.model)

    def test_only_quota_rejections_write_provider_cooling(self):
        for harness, case, code, native_code in (
            ("claude", "quota-rejected", "quota-exceeded", "five_hour"),
            ("claude", "result-quota", "quota-exceeded", "error_quota_during_execution"),
            ("claude", "api-429", "rate-limited", "http_429"),
            ("claude", "rate-limited", "rate-limited", "rate_limit_reached"),
            ("claude", "overload-before", "overloaded", "overloaded"),
            ("codex", "quota-before", "quota-exceeded", "usageLimitExceeded"),
            ("codex", "rate-limited", "rate-limited", "rateLimitExceeded"),
            ("codex", "overload-before", "overloaded", "serverOverloaded"),
            ("dsh", "quota", "quota-exceeded", "QUOTA"),
            ("dsh", "rate-limited", "rate-limited", "RATE_LIMIT"),
            ("zcode", "turn-failed-quota", "quota-exceeded", "1308"),
            ("zcode", "turn-failed-rate-limit", "rate-limited", "1308"),
        ):
            with self.subTest(harness=harness, case=case):
                started_at = utc_now()
                result = self.run_case(harness, case)
                self.assertIsNotNone(result.provider_rejection, result.end)
                rejection = result.provider_rejection.value
                self.assertEqual(rejection["code"], code)
                self.assertEqual(rejection["nativeCode"], native_code)
                facts = _worker_facts(result)
                self.assertEqual(facts["providerRejection"], rejection)

                provider = result.buddy.requested.provider
                board = Database(Path(tempfile.mkdtemp(dir=self.root)))
                self.addCleanup(board.close)
                board.initialize()
                now = utc_now()
                with board.write() as connection:
                    persist(connection, {"model_harness": harness, "adapter": harness,
                                         "model_provider": provider, "started_at": started_at},
                            facts, status=facts["status"], now=now,
                            shutdown_confirmed=facts["processState"]["shutdownConfirmed"])
                with board.connect() as connection:
                    rows = connection.execute("SELECT harness, provider, cooling_until FROM provider_cooling").fetchall()
                if code == "quota-exceeded":
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual((row["harness"], row["provider"]), (harness, provider))
                    self.assertGreater(datetime.fromisoformat(row["cooling_until"]), datetime.fromisoformat(now))
                else:
                    self.assertEqual(rows, [])
