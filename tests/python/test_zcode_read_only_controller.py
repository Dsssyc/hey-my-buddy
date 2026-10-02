"""L6-B ZCode read-only controller wiring: eligibility, entry and lifecycle.

No paid model calls. The eligibility path runs the real contract helper against
synthetic public bundles with every process interface armed to fail, the start
entry is checked for its environment-bound re-qualification, and the whole
controller stack — adapter start, private environment, owned process, EOF
drain, common structured finish — runs against a private fake ZCode CLI in
``fixtures/mock_zcode_read_only_controller.py``. The blackboard judge is the
only place an allowance verdict appears; the controller's own receipts are
asserted to carry facts only.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from buddy import tool_evidence
from buddy.adapters.base import ExecutionContext, ReadOnlyStructuredRequest
from buddy.adapters import read_only
from buddy.adapters.zcode import ZcodeAdapter, _NATIVE_CONTRACT_CACHE
from buddy.errors import BoardError

from test_zcode_read_only_protocol import GOOD_BUNDLE

FIXTURE = Path(__file__).parent / "fixtures" / "mock_zcode_read_only_controller.py"
SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}


@contextlib.contextmanager
def process_interfaces_refuse():
    """Arm every process interface; the free eligibility check must not reach one."""
    guard = AssertionError("the free read-only check started a process")
    with mock.patch("subprocess.Popen", side_effect=guard), \
         mock.patch("subprocess.run", side_effect=guard), \
         mock.patch("subprocess.call", side_effect=guard), \
         mock.patch("subprocess.check_output", side_effect=guard), \
         mock.patch("os.posix_spawn", side_effect=guard, create=True):
        yield


class ContractCheckTests(unittest.TestCase):
    """The real free helper: pure filesystem, bounded cache, clear reasons."""

    def setUp(self):
        _NATIVE_CONTRACT_CACHE.clear()
        self.addCleanup(_NATIVE_CONTRACT_CACHE.clear)
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def bundle(self, text=GOOD_BUNDLE, name="cli.cjs"):
        path = self.root / name
        path.write_text(text)
        return path

    def environment(self, cli):
        return {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(cli), "BUDDY_NODE": sys.executable}

    def test_a_qualifying_public_bundle_passes_without_any_process(self):
        cli = self.bundle()
        with process_interfaces_refuse(), dev_source():
            self.assertIsNone(ZcodeAdapter()._native_contract_check(self.environment(cli)))
        with process_interfaces_refuse(), dev_cli_environment(cli):
            check = ZcodeAdapter().local_read_only_check()
        self.assertTrue(check["eligible"])
        self.assertFalse(check["systemSandbox"])
        self.assertEqual(check["reasonCode"], None)
        self.assertEqual(ZcodeAdapter().read_only_tool_categories, ("read", "search"))
        self.assertTrue(ZcodeAdapter().read_only_structured)
        self.assertTrue(ZcodeAdapter().read_only_structured_resume)
        self.assertEqual(ZcodeAdapter().system_sandbox_platforms, ())

    def test_a_broken_mechanism_is_ineligible_with_the_contract_reason(self):
        cli = self.bundle(GOOD_BUNDLE.replace('"plan",', ""))
        with process_interfaces_refuse(), dev_source():
            problem = ZcodeAdapter()._native_contract_check(self.environment(cli))
        with dev_cli_environment(cli):
            check = ZcodeAdapter().local_read_only_check()
        self.assertIn("does not offer plan", problem)
        self.assertFalse(check["eligible"])
        self.assertEqual(check["reasonCode"], "readonly-native-contract-unverified")
        self.assertIn("plan", check["reason"])

    def test_missing_non_js_oversized_and_unreadable_bundles_each_refuse(self):
        missing = self.root / "absent.cjs"
        with dev_source():
            problem = ZcodeAdapter()._native_contract_check(self.environment(missing))
        self.assertIn("could not be located", problem)
        executable = FIXTURE
        self.assertTrue(executable.is_file())
        with dev_source():
            problem = ZcodeAdapter()._native_contract_check(self.environment(executable))
        self.assertIn("not a public JS/CJS/MJS bundle", problem)
        oversized = self.bundle(name="big.cjs")
        with open(oversized, "wb") as stream:
            stream.truncate(33 * 1024 * 1024)
        with process_interfaces_refuse(), dev_source():
            problem = ZcodeAdapter()._native_contract_check(self.environment(oversized))
        self.assertIn("exceeds the 32 MiB", problem)
        unreadable = self.bundle(name="secret.cjs")
        unreadable.chmod(0o000)
        self.addCleanup(unreadable.chmod, 0o600)
        with dev_source():
            problem = ZcodeAdapter()._native_contract_check(self.environment(unreadable))
        self.assertIn("could not be read", problem)

    def test_an_unselected_cli_is_refused_rather_than_probed(self):
        with mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "0"}), process_interfaces_refuse():
            problem = ZcodeAdapter()._native_contract_check({})
        self.assertIn("not resolvable without a native probe", problem)

    def test_the_cache_reuses_only_an_unchanged_stat_identity(self):
        import buddy.adapters.zcode_read_only as protocol
        cli = self.bundle()
        seen = []
        original = protocol.native_contract_problem

        def counting(text):
            seen.append(text)
            return original(text)

        with dev_source(), mock.patch.object(protocol, "native_contract_problem", side_effect=counting):
            adapter = ZcodeAdapter()
            self.assertIsNone(adapter._native_contract_check(self.environment(cli)))
            self.assertIsNone(adapter._native_contract_check(self.environment(cli)))
            self.assertEqual(len(seen), 1)
            with open(cli, "a") as stream:
                stream.write("// touched\n")
            problem = adapter._native_contract_check(self.environment(cli))
            self.assertEqual(len(seen), 2)
            self.assertIsNone(problem)
            cli.unlink()
            self.assertIn("could not be located", adapter._native_contract_check(self.environment(cli)))
            self.assertEqual(len(seen), 2)  # a missing CLI never rescans the text

    def test_the_cache_is_bounded_and_the_result_cannot_mutate_it(self):
        adapter = ZcodeAdapter()
        bundles = [self.bundle(name=f"cli-{index}.cjs") for index in range(9)]
        with dev_source():
            for cli in bundles:
                self.assertIsNone(adapter._native_contract_check(self.environment(cli)))
            self.assertEqual(len(_NATIVE_CONTRACT_CACHE), 8)
            self.assertNotIn(str(bundles[0].resolve()), [key[0] for key in _NATIVE_CONTRACT_CACHE])
        with dev_cli_environment(bundles[8]):
            first = adapter.local_read_only_check()
        first["eligible"] = False
        first["reasonCode"] = "tampered"
        first["reason"] = "tampered"
        with dev_cli_environment(bundles[8]):
            second = adapter.local_read_only_check()
        self.assertTrue(second["eligible"])
        self.assertEqual(second["reasonCode"], None)


class StartRecheckTests(unittest.TestCase):
    """The start entry re-qualifies the CLI this context will actually start."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-ro-start-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.good = self.root / "good.cjs"
        self.good.write_text(GOOD_BUNDLE)
        self.bad = self.root / "bad.cjs"
        self.bad.write_text(GOOD_BUNDLE.replace('"plan",', ""))
        self.context = ExecutionContext(
            "task-ro-1", "attempt-ro-1", 2,
            {"provider": "fixture-api", "model": "fixture-model", "effort": "low",
             "cwd": str(self.root), "timeoutSeconds": 5},
            self.root / "attempt", {}, {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(self.good), "BUDDY_NODE": sys.executable,
                                        "BUDDY_STATE_DIR": str(self.root / "state")})
        self.request = ReadOnlyStructuredRequest(str(self.root), "Choose a profile", SCHEMA,
                                                  budget={"timeoutSeconds": 5, "toolCalls": 4})

    def test_a_qualified_environment_reaches_the_generic_start(self):
        with dev_source(), mock.patch("buddy.adapters.read_only.start") as start:
            start.return_value = "handle"
            self.assertIs(ZcodeAdapter().start_read_only_structured(self.context, self.request), "handle")
            start.assert_called_once_with("zcode", self.context, self.request)

    def test_an_unqualified_environment_is_refused_before_any_start(self):
        context = mock.MagicMock(spec=ExecutionContext)
        context.environment = {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(self.bad), "BUDDY_NODE": sys.executable}
        with dev_source(), mock.patch("buddy.adapters.read_only.start") as start:
            with self.assertRaises(BoardError) as caught:
                ZcodeAdapter().start_read_only_structured(context, self.request)
            start.assert_not_called()
        self.assertIn("does not offer plan", str(caught.exception.message))

    def test_a_qualified_default_never_endorses_another_command(self):
        # The outer environment points at a qualifying bundle while the context
        # will actually start the non-bundle CLI: only the context's own CLI is
        # qualified, so the start refuses.
        with mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(self.good),
                                          "BUDDY_NODE": sys.executable}), \
                mock.patch("buddy.adapters.read_only.start") as start:
            context = mock.MagicMock(spec=ExecutionContext)
            context.environment = {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(FIXTURE)}
            with self.assertRaises(BoardError) as caught:
                ZcodeAdapter().start_read_only_structured(context, self.request)
            start.assert_not_called()
        self.assertIn("not a public JS/CJS/MJS bundle", str(caught.exception.message))


class ReadOnlyControllerTests(unittest.TestCase):
    """The full controller stack against the private fake ZCode CLI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-ro-ctl-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.frozen = self.root / "frozen"
        self.frozen.mkdir(mode=0o700)
        self.builtin = self.root / "builtin.json"
        self.personal = self.root / "personal.json"
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {"templateRules": [], "providerRules": []}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [
            {"providerId": "fixture-api", "config": {"access": {"type": "api-key", "apiKey": "private-test-secret"}}}]}}}))
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment.update(BUDDY_DEV_SOURCE="1", BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_ZCODE_CLI=str(FIXTURE),
                                ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.builtin),
                                ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.personal))
        self.attempt = self.root / "attempt"
        self.sequence = 0

    def execute(self, case="tools3", *, timeout=8, tool_budget=8, cancel=False, kill_leader=False, capture=False):
        # One private attempt directory per execution: the record file the fake
        # CLI appends to is derived from the attempt's native root.
        self.sequence += 1
        directory = self.root / f"attempt-{self.sequence}"
        context = ExecutionContext(
            "task-ro-1", f"attempt-ro-{self.sequence}", 2,
            {"provider": "fixture-api", "model": "fixture-model", "effort": "low",
             "cwd": str(self.frozen), "timeoutSeconds": timeout},
            directory, {}, {**self.environment, "BUDDY_ZCODE_TEST_CASE": case})
        request = ReadOnlyStructuredRequest(str(self.frozen), "Choose a profile", SCHEMA,
                                             budget={"timeoutSeconds": timeout, "toolCalls": tool_budget},
                                             capture_evidence=capture)
        # The controller wiring may mock the free-check seam; the eligibility
        # paths above exercise the real helper.
        with mock.patch.object(ZcodeAdapter, "_native_contract_check", return_value=None):
            handle = ZcodeAdapter().start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=1) if handle.group_alive() else None)
        if kill_leader:
            time.sleep(0.6)
            handle.process.kill()
        elif cancel:
            time.sleep(0.3)
            handle.terminate(grace_seconds=4)
        self.assertIsNotNone(handle.wait(timeout + 14))
        outcome = read_only.collect(handle)
        if outcome.result.get("code") == "invalid-native-result" and not kill_leader:
            self.fail("controller emitted no result: "
                      + (directory / "runner.stderr.log").read_text(errors="replace"))
        return outcome, context

    def creates(self, context):
        control = json.loads((context.directory / "readonly-control.json").read_text())
        record = Path(control["nativeRoot"]) / "storage" / "read-only-create-params.jsonl"
        if not record.is_file():
            return []
        return [json.loads(line) for line in record.read_text().splitlines()]

    def test_full_read_search_round_with_publication_and_no_turn_authority(self):
        outcome, context = self.execute("tools3")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["resolved"], {"provider": "fixture-api", "model": "fixture-model", "effort": "low"})
        self.assertIsNone(result["observed"])
        self.assertTrue(result["modelStarted"])
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-1", "turnId": "t-1"})
        self.assertEqual((result["rawAnswer"], result["answerValid"], result["correctionCount"]),
                         ('{"choice":"a"}', True, 0))
        self.assertEqual(result["usage"]["toolCalls"], 3)
        self.assertIsNone(result["usage"]["bytesRead"])
        self.assertIsInstance(result["usage"]["elapsedMs"], int)
        self.assertGreaterEqual(result["usage"]["elapsedMs"], 0)
        self.assertNotIn("zeroToolVerified", result)
        for governed in ("turn", "nativeTurnId", "inquiry", "nativeAttention", "turnResultPath"):
            self.assertNotIn(governed, result)
        self.assertFalse((context.directory / "finish-bridge.json").exists())
        self.assertFalse(context.turn_output_file().exists())
        package = result["toolEvidence"]
        self.assertEqual(package["binding"], {"adapter": "zcode", "taskId": "task-ro-1",
                                              "attemptId": context.attempt_id, "generation": 2})
        self.assertEqual([(event["toolName"], event["category"], event["phase"]) for event in package["events"]],
                         [("Read", "read", "start"), ("Read", "read", "end"),
                          ("Glob", "search", "start"), ("Glob", "search", "end"),
                          ("Grep", "search", "start"), ("Grep", "search", "end")])
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"}])
        self.assertEqual((package["toolCalls"], package["streamComplete"]), (3, True))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))
        self.assertEqual(self.creates(context), [{
            "workspace": {"workspacePath": str(self.frozen), "workspaceKey": str(self.frozen)},
            "mode": "plan", "titleGenerationEnabled": False,
            "toolAllowlist": ["Read", "Glob", "Grep"], "mcpServers": [],
            "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False}])

    def test_the_generic_control_carries_only_read_only_authority(self):
        outcome, context = self.execute("ok", capture=True)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        control = json.loads((context.directory / "readonly-control.json").read_text())
        self.assertEqual(set(control), {"directory", "nativeRoot", "account", "cwd", "timeoutSeconds",
                                        "taskId", "attemptId", "generation", "sessionId", "access",
                                        "activityFile", "spec", "readOnlyRequest"})
        self.assertEqual(control["taskId"], "task-ro-1")
        self.assertEqual(control["attemptId"], context.attempt_id)
        self.assertEqual(control["generation"], 2)
        self.assertEqual(control["spec"], {"provider": "fixture-api", "model": "fixture-model", "effort": "low"})
        self.assertEqual(control["readOnlyRequest"]["budget"], {"timeoutSeconds": 8, "toolCalls": 8})
        self.assertTrue(control["readOnlyRequest"]["captureEvidence"])
        self.assertEqual(outcome.result["nativeEvidence"], {"streamEof": True})

    def test_native_report_mismatches_fail_before_any_model_input(self):
        for case, code in (("config-mismatch", "configuration-mismatch"),
                           ("effort-mismatch", "configuration-mismatch"),
                           ("child-root", "wrong-native-session"),
                           ("ws-mismatch", "wrong-native-workspace"),
                           ("sub-wrong-sid", "invalid-protocol"),
                           ("sub-bad-seq", "invalid-protocol"),
                           ("sub-replay", "invalid-protocol")):
            with self.subTest(case=case):
                outcome, context = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], code)
                self.assertFalse(outcome.result["modelStarted"])
                self.assertIsNone(outcome.result["observed"])
                self.assertEqual(len(self.creates(context)), 1)
                package = outcome.result["toolEvidence"]
                self.assertEqual((package["events"], package["nativeIdentity"]), ([], []))
                self.assertFalse(package["streamComplete"])

    def test_forbidden_categories_stay_facts_and_only_the_blackboard_forbids(self):
        for case, name, category in (("bash", "Bash", "execute"), ("mcp", "mcp__fix__tool", "other")):
            with self.subTest(case=case):
                outcome, context = self.execute(case)
                # The controller reports its round as settled; it never judges
                # the allowance itself.
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                package = outcome.result["toolEvidence"]
                self.assertEqual([event["toolName"] for event in package["events"]], [name, name])
                self.assertEqual({event["category"] for event in package["events"]}, {category})
                self.assertTrue(package["streamComplete"])
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                                 tool_evidence.TOOLS_FORBIDDEN)

    def test_a_native_rpc_request_is_refused_with_the_facts_kept(self):
        outcome, context = self.execute("rpc")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        self.assertTrue(outcome.result["modelStarted"])
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"], [])
        self.assertEqual(package["toolCalls"], 0)
        self.assertFalse(package["streamComplete"])
        self.assertNotIn("zeroToolVerified", outcome.result)

    def test_foreign_late_missing_id_end_first_and_sequence_facts(self):
        # A foreign child frame is kept as a fact and only fails the judge.
        outcome, _ = self.execute("foreign")
        self.assertEqual(outcome.status, "ok")
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"][0]["nativeIdentity"], {"sessionId": "s-child", "turnId": "t-child"})
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        # A tool frame after the completed turn marks the stream incomplete.
        outcome, _ = self.execute("late")
        self.assertEqual(outcome.status, "ok")
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"][0]["toolName"], "Read")
        self.assertEqual(package["toolCalls"], 1)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        # An end frame without its start and a scheduled frame without an id
        # stay unnamed, incomplete facts.
        for case in ("end-first", "missing-id"):
            with self.subTest(case=case):
                outcome, _ = self.execute(case)
                self.assertEqual(outcome.status, "ok")
                package = outcome.result["toolEvidence"]
                self.assertEqual(package["toolCalls"], 0)
                self.assertFalse(package["streamComplete"])
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        # A non-monotonic canonical sequence fails the protocol outright.
        outcome, _ = self.execute("badseq")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "invalid-protocol")

    def test_one_format_correction_accumulates_roots_calls_and_time(self):
        outcome, context = self.execute("correct-tools")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        result = outcome.result
        self.assertEqual(result["correctionCount"], 1)
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-2", "turnId": "t-2"})
        self.assertEqual(result["usage"]["toolCalls"], 2)
        self.assertIsInstance(result["usage"]["elapsedMs"], int)
        package = result["toolEvidence"]
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"},
                                                     {"sessionId": "s-2", "turnId": "t-2"}])
        self.assertEqual(package["toolCalls"], 2)
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))
        creates = self.creates(context)
        self.assertEqual(len(creates), 2)
        self.assertEqual(creates[0], creates[1])

    def test_the_tool_budget_counts_n_and_n_plus_one(self):
        outcome, _ = self.execute("tools3", tool_budget=3)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["usage"]["toolCalls"], 3)
        outcome, _ = self.execute("tools3", tool_budget=2)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "readonly-budget-exhausted")
        self.assertEqual(outcome.result["toolEvidence"]["toolCalls"], 3)
        self.assertFalse(outcome.result["toolEvidence"]["streamComplete"])

    def test_close_failure_keeps_the_pending_marker_and_the_facts(self):
        outcome, _ = self.execute("close-fail")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "session-close-unconfirmed")
        self.assertTrue(outcome.result["modelStarted"])
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"], [])
        self.assertFalse(package["streamComplete"])
        self.assertNotIn("zeroToolVerified", outcome.result)

    def test_timeout_and_cancel_settle_with_confirmed_stop_and_honest_facts(self):
        outcome, _ = self.execute("timeout", timeout=2)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "timeout")
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("zeroToolVerified", outcome.result)
        self.assertFalse(outcome.result["toolEvidence"]["streamComplete"])
        outcome, _ = self.execute("timeout", timeout=10, cancel=True)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("zeroToolVerified", outcome.result)

    def test_native_and_outer_stop_unknown_never_claim_a_stopped_group(self):
        # The native server exits abnormally after a settled round: the group
        # is gone but the exit is not normal, so the receipt keeps its facts
        # and reports the failed shutdown honestly.
        outcome, _ = self.execute("exit-dirty")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "native-shutdown-failed")
        self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])
        self.assertEqual(outcome.result["processState"]["nativeExitCode"], 3)
        self.assertTrue(outcome.result["toolEvidence"]["streamComplete"])
        # A killed controller leader can prove nothing about the native group it
        # owned: no result, no fabricated evidence and no stop confirmation.
        outcome, _ = self.execute("sleep", timeout=10, kill_leader=True)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "invalid-native-result")
        self.assertFalse(outcome.shutdown_confirmed)
        self.assertNotIn("toolEvidence", outcome.result)
        self.assertNotIn("zeroToolVerified", outcome.result)
        time.sleep(2.4)  # let the orphaned fake server exit before cleanup


def dev_source():
    """The dev-source flag lives in the outer environment, like cli_command reads it."""
    return mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1"})


def dev_cli_environment(cli):
    """The outer environment a no-argument eligibility read resolves through."""
    return mock.patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1", "BUDDY_ZCODE_CLI": str(cli),
                                        "BUDDY_NODE": sys.executable})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
