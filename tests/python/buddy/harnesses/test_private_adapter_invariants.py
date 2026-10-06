"""Real mock-controller executions preserve only declared attempt evidence."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.protocol import attempt_evidence
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses import controller
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.runtime.command import CommandAdapter
from hey_my_buddy.buddy.roles.router import DecisionAdapter
from hey_my_buddy.buddy.harnesses.base import ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.private_dirs import cleanup_attempt_credentials, context_root, native_root
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.roles import structured_call as read_only, turn_io
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.roles.controller import FastPreparation, ReviewPreparation, start_router_preparation
import buddy.harnesses.codex.test_no_tool_codex as codex_fast_tests
import buddy.harnesses.zcode.test_no_tool_zcode as zcode_fast_tests
import buddy.harnesses.dsh.test_dsh_role_wiring as dsh_coding_tests
import buddy.harnesses.claude.test_claude as claude_tests
import buddy.harnesses.codex.test_codex as codex_tests
import buddy.harnesses.dsh.test_no_tool_dsh as dsh_tests
import buddy.harnesses.zcode.test_zcode as zcode_tests


class NoToolEvidenceSafetyTests(unittest.TestCase):
    """Stopped controller fixtures cannot redirect evidence writes outside their binding."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="no-tool-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.outside = self.root / "outside"
        self.outside.mkdir(mode=0o700)
        self.sentinels = [self.outside / name for name in ("request.json", "result.json")]
        for sentinel in self.sentinels:
            sentinel.write_text("outside-sentinel")
        self.index = 0

    def start(self):
        self.index += 1
        context = ExecutionContext("task", f"attempt-{self.index}", self.index,
            {"adapter": "codex", "provider": "openai", "model": "fixture-model", "effort": "low",
             "cwd": str(self.cwd), "timeoutSeconds": 3},
            self.root / "state" / "attempts" / "task" / f"attempt-{self.index}", {},
            {"BUDDY_STATE_DIR": str(self.root / "state"), "BUDDY_RUNTIME_ROOT": str(self.root / "runtime"),
             "BUDDY_DEV_SOURCE": "1"})
        request = NoToolStructuredRequest(str(self.cwd), "frozen original prompt", {"type": "object"}, 3)
        process = SimpleNamespace(returncode=0)
        handle = SimpleNamespace(process=process, log_paths=context.log_paths(), shutdown_confirmed=lambda: True)
        module = SimpleNamespace(check_preparation=lambda *_args: None, native_evidence=lambda _result: {})
        self.enterContext(mock.patch.dict(RUN_SEAMS, {"codex": module}))
        with (mock.patch.object(controller, "owned_popen", return_value=process) as spawn,
              mock.patch.object(controller, "ProcessHandle", return_value=handle)):
            self.assertIs(run_execution.start_fast(module, "codex", context, request), handle)
        control = handle.role_run_control
        self.assertEqual(spawn.call_args.args[0][-2:], ["--control", str(Path(control["privateRoot"]) / "role-run-control.json")])
        frame, _services, _observer, _correction = run_execution.fast_request(control, module)
        Path(control["requestFile"]).write_text(rc.encode_run_request(frame))
        Path(control["verdictFile"]).write_text(json.dumps({"stopReason": None, "elapsedMs": 1}))
        result = rc.RunResult(identity=frame.identity, harness="codex", end=rc.RunEnd(status="ok"),
            value=rc.RunValue( schema_status="unknown", raw="{}"),
            stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
        Path(handle.log_paths["stdout"]).write_text(rc.encode_run_result(result))
        return context, request, handle

    def assert_sentinels(self):
        for sentinel in self.sentinels:
            self.assertEqual(sentinel.read_text(), "outside-sentinel")

    def assert_retention_failure(self, handle):
        outcome = read_only.collect(handle)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.error, "evidence-retention-failed")
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result["processState"]["shutdownConfirmed"])
        diagnostic = outcome.result["evidenceRetention"]
        self.assertEqual(handle.evidence_retention_failure, diagnostic)
        self.assertEqual(diagnostic["privateRoot"], str(handle.no_tool_evidence.private_root))
        self.assertEqual(diagnostic["evidenceRoot"], str(handle.no_tool_evidence.evidence_root))
        self.assertTrue(handle.no_tool_evidence.private_root.exists())
        self.assert_sentinels()

    def test_control_edits_and_control_links_cannot_rebind_paths_request_or_identity(self):
        for replace_with_link in (False, True):
            with self.subTest(control_link=replace_with_link):
                context, request, handle = self.start()
                binding = handle.no_tool_evidence
                original_identity = (context.task_id, context.attempt_id, context.generation, context.directory)
                malicious = {"directory": str(self.outside), "evidenceRoot": str(self.outside),
                             "noToolRequest": {"prompt": "malicious replacement"}}
                control_path = Path(handle.role_run_control["privateRoot"]) / "role-run-control.json"
                if replace_with_link:
                    control_path.unlink()
                    control_path.symlink_to(self.outside / "control.json")
                control_path.write_text(json.dumps(malicious))
                request.output_schema["properties"] = {"mutated": {"type": "string"}}
                context.task_id, context.attempt_id, context.generation = "changed", "changed", 999
                context.directory = self.outside
                with self.assertRaises(FrozenInstanceError):
                    binding.evidence_root = self.outside
                outcome = read_only.collect(handle)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                retained = json.loads((binding.evidence_root / "call-1/request.json").read_text())
                self.assertEqual(retained["prompt"], "frozen original prompt")
                self.assertEqual(retained["outputSchema"], {"type": "object"})
                self.assertEqual((binding.task_id, binding.attempt_id, binding.generation, binding.directory), original_identity)
                self.assert_sentinels()
                self.assertFalse((self.outside / "call-1").exists())

    def test_evidence_root_and_call_parent_links_refuse_copy_before_any_outside_write(self):
        for parent in ("root", "call", "file"):
            with self.subTest(parent=parent):
                _, _, handle = self.start()
                evidence = handle.no_tool_evidence.evidence_root
                if parent in ("call", "file"):
                    evidence.mkdir(mode=0o700)
                    evidence = evidence / "call-1"
                if parent == "file":
                    evidence.mkdir(mode=0o700)
                    (evidence / "result.json").symlink_to(self.sentinels[1])
                else:
                    evidence.symlink_to(self.outside, target_is_directory=True)
                self.assert_retention_failure(handle)

    def test_linked_private_source_parent_is_not_read_or_copied(self):
        _, _, handle = self.start()
        root = handle.no_tool_evidence.private_root
        (root / "call-1").symlink_to(self.outside, target_is_directory=True)
        self.assert_retention_failure(handle)
        self.assertFalse(handle.no_tool_evidence.evidence_root.exists())

    def test_arbitrary_windows_reparse_metadata_on_any_parent_refuses_evidence_write(self):
        for tag in (0xA0000003, 0xA000000C, 0x80000042):
            with self.subTest(reparse_tag=tag):
                _, _, handle = self.start()
                parent = handle.no_tool_evidence.evidence_root
                parent.mkdir(mode=0o700)
                original_lstat = Path.lstat
                def metadata(path, *args, **kwargs):
                    value = original_lstat(path, *args, **kwargs)
                    if path == parent:
                        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400, st_reparse_tag=tag)
                    return value
                with mock.patch.object(Path, "lstat", metadata):
                    self.assert_retention_failure(handle)
                self.assertFalse((parent / "call-1").exists())

    def test_evidence_io_failure_preserves_actual_native_stop_and_diagnostic_paths(self):
        _, _, handle = self.start()
        with mock.patch.object(read_only, "private_json", side_effect=OSError("fixture evidence disk failure")):
            self.assert_retention_failure(handle)

    def test_plain_and_immutable_json_refuse_final_links_and_every_parent_link(self):
        for exclusive in (False, True):
            for location in ("file", "parent", "ancestor"):
                with self.subTest(exclusive=exclusive, location=location):
                    directory = self.root / f"json-{exclusive}-{location}"
                    directory.mkdir(mode=0o700)
                    if location == "file":
                        target = directory / "result.json"
                        target.symlink_to(self.sentinels[1])
                    else:
                        parent = directory / "linked"
                        parent.symlink_to(self.outside, target_is_directory=True)
                        target = parent / "result.json" if location == "parent" else parent / "new" / "result.json"
                    with self.assertRaises(BoardError) as refused:
                        turn_io.private_json(target, {"overwritten": True}, exclusive=exclusive)
                    self.assertEqual(refused.exception.code, "PRIVATE_PATH_UNSAFE")
                    self.assert_sentinels()
                    self.assertFalse((self.outside / "new").exists())
                    self.assertFalse(any(self.outside.glob(".*.tmp")))

    def test_plain_json_refuses_any_windows_reparse_target_or_parent(self):
        for location in ("file", "parent"):
            with self.subTest(location=location):
                directory = self.root / f"reparse-{location}"
                directory.mkdir(mode=0o700)
                target = directory / "result.json"
                target.write_text("original-evidence")
                rejected = target if location == "file" else directory
                original_lstat = Path.lstat
                def metadata(path, *args, **kwargs):
                    value = original_lstat(path, *args, **kwargs)
                    if path == rejected:
                        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400, st_reparse_tag=0x80000042)
                    return value
                with mock.patch.object(Path, "lstat", metadata), self.assertRaises(BoardError):
                    turn_io.private_json(target, {"overwritten": True})
                self.assertEqual(target.read_text(), "original-evidence")
                self.assertFalse(any(directory.glob(".*.tmp")))


class PrivateAdapterInvariants(unittest.TestCase):
    def fixture(self, kind):
        value = kind(methodName="runTest")
        value.setUp()
        self.addCleanup(value.doCleanups)
        return value

    def assert_partition(self, context):
        self.assertTrue(context.directory.is_dir())
        for path in context.directory.rglob("*"):
            if path.is_file():
                self.assertFalse(path.is_symlink(), path)
                self.assertTrue(attempt_evidence.is_evidence(path.relative_to(context.directory)), path)
        report = backup.preflight(Path(context.environment["BUDDY_STATE_DIR"]))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["skipped"]["count"], 0, report)
        self.assertEqual(report["rejected"]["count"], 0, report)

    def test_governed_mock_codex_zcode_claude_and_native_continuation(self):
        for kind, adapter in ((codex_tests.CodexAdapterTests, "codex"), (zcode_tests.ZcodeFixtureCase, "zcode"),
                              (claude_tests.ClaudeAdapterTests, "claude")):
            with self.subTest(adapter=adapter):
                fixture = self.fixture(kind)
                context = fixture.context()
                state = Path(context.environment["BUDDY_STATE_DIR"])
                context.directory = state / "attempts" / context.task_id / context.attempt_id
                context.agent_credential = "private-fixture-credential"
                execution = fixture.execute(context)
                outcome = execution[1] if isinstance(execution, tuple) else execution
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed)
                private = context_root(context, adapter)
                self.assertTrue((private / "agent-credential.json").is_file())
                cleanup_attempt_credentials(state, adapter, context.task_id, context.attempt_id)
                self.assertFalse((private / "agent-credential.json").exists())
                if adapter == "zcode":
                    self.assertFalse((private / "personal-provider.json").exists())
                    self.assertFalse((private / "finish-bridge.json").exists())
                    self.assertFalse((private / "inquiry.json").exists())
                if adapter in {"codex", "zcode"}:
                    self.assertTrue(any(native_root(state, adapter, context.task_id).glob("*.json")))
                self.assert_partition(context)

    def test_mock_dsh_fast_router_and_command_leave_only_evidence(self):
        fixture = self.fixture(dsh_tests.DshNoToolTests)
        state = Path(fixture.environment["BUDDY_STATE_DIR"])
        evidence = state / "attempts/task/attempt-1"
        context = ExecutionContext("task", "attempt-1", 1,
            {"adapter": "dsh", **dsh_tests.SPEC,
             "cwd": str(fixture.cwd), "timeoutSeconds": 3}, evidence, {}, fixture.environment)
        request = NoToolStructuredRequest(str(fixture.cwd), "Choose a profile", dsh_tests.SCHEMA, 3)
        handle = start_router_preparation(FastPreparation("dsh", DshAdapter(), request, context, fixture.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertEqual(handle.wait(8), 0)
        outcome = read_only.collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        control = handle.role_run_control
        self.assertTrue(Path(control["directory"]).is_relative_to(context_root(context, "dsh")))
        self.assertTrue((handle.no_tool_evidence.evidence_root / "call-1/result.json").is_file())
        self.assert_partition(context)

        command_evidence = state / "attempts/command-task/attempt-2"
        command = ExecutionContext("command-task", "attempt-2", 1,
            {"adapter": "command", "cwd": str(fixture.cwd), "task": "fixture command", "timeoutSeconds": 3,
             "argv": [sys.executable, "-c", "print('ok')"]}, command_evidence, {}, fixture.environment)
        adapter = CommandAdapter()
        child = adapter.start(command)
        self.addCleanup(lambda: child.terminate(grace_seconds=0.1) if child.group_alive() else None)
        self.assertEqual(child.wait(5), 0)
        self.assertEqual(adapter.collect(child, command).status, "ok")
        self.assert_partition(command)

    def test_codex_and_zcode_fast_router_calls_use_private_native_roots(self):
        for kind, adapter in ((codex_fast_tests.NoToolCodexTests, CodexAdapter),
                              (zcode_fast_tests.NoToolFakeProtocolTests, ZcodeAdapter)):
            with self.subTest(adapter=adapter.name):
                fixture = self.fixture(kind)
                if adapter.name == "codex":
                    environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
                    environment.update(HOME=str(fixture.root), CODEX_HOME=str(fixture.home),
                        PYTHONPATH=str(Path(__file__).parents[4] / "src"), BUDDY_DEV_SOURCE="1",
                        BUDDY_CODEX_CLI=str(codex_fast_tests.FIXTURE),
                        BUDDY_CODEX_FIXTURE_STATE=str(fixture.root / "trace.json"),
                        BUDDY_STATE_DIR=str(fixture.root / "state"), BUDDY_RUNTIME_ROOT=str(fixture.root / "runtime"))
                    spec = {"provider": "openai", "model": "fixture-model", "effort": "low"}
                else:
                    environment = {**fixture.environment, "BUDDY_ZCODE_TEST_CASE": "ok"}
                    spec = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
                evidence = Path(environment["BUDDY_STATE_DIR"]) / "attempts/task/attempt"
                context = ExecutionContext("task", "attempt", 1,
                    {"adapter": adapter.name, **spec, "cwd": str(fixture.cwd), "timeoutSeconds": 3},
                    evidence, {}, environment)
                native = adapter()
                request = NoToolStructuredRequest(str(fixture.cwd), "Pick a profile", codex_fast_tests.SCHEMA, 3)
                handle = start_router_preparation(FastPreparation(native.name, native, request, context, fixture.cwd))
                self.addCleanup(lambda h=handle: h.terminate(grace_seconds=0.1) if h.group_alive() else None)
                self.assertEqual(handle.wait(8), 0)
                from hey_my_buddy.buddy.roles.structured_call import collect
                outcome = collect(handle)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                control = (handle.role_run_control if hasattr(handle, "role_run_control")
                           else json.loads((evidence / "no-tool-control.json").read_text()))
                self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, adapter.name)))
                cleanup_attempt_credentials(Path(environment["BUDDY_STATE_DIR"]), adapter.name, "task", "attempt")
                self.assert_partition(context)

    def test_review_read_only_calls_use_codex_private_root(self):
        fixture = self.fixture(codex_tests.CodexAdapterTests)
        context = fixture.context()
        account_home = fixture.root / "fixture-codex-home"
        account_home.mkdir(mode=0o700)
        (account_home / "auth.json").write_text('{"fixture":"login"}')
        context.environment["CODEX_HOME"] = str(account_home)
        context.directory = Path(context.environment["BUDDY_STATE_DIR"]) / "attempts" / context.task_id / context.attempt_id
        context.turn = None
        context.agent_credential = None
        # A review-mode call is a read-only structured invocation of the native
        # adapter; the private-root and partition invariants live on that path.
        request = ReadOnlyStructuredRequest(str(fixture.cwd), "Select from the frozen packet",
                                             router.answer_schema(["legal"]), router.budget())
        handle = start_router_preparation(ReviewPreparation("codex", CodexAdapter(), request, context,
                                                            (None, fixture.cwd, "fixture-digest")))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(20))
        outcome = read_only.collect(handle)
        self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
        control = handle.role_run_control
        self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, "codex")))
        self.assertFalse((context_root(context, "codex") / "review-native/codex-home/auth.json").exists())
        cleanup_attempt_credentials(Path(context.environment["BUDDY_STATE_DIR"]), "codex", context.task_id, context.attempt_id)
        self.assert_partition(context)

    def test_decision_fast_router_wraps_each_native_adapter(self):
        setups = (
            (dsh_tests.DshNoToolTests, "dsh"),
            (zcode_fast_tests.NoToolFakeProtocolTests, "zcode"),
            (codex_fast_tests.NoToolCodexTests, "codex"),
        )
        for kind, name in setups:
            with self.subTest(adapter=name):
                fixture = self.fixture(kind)
                if name == "dsh":
                    env = fixture.environment
                    spec = dsh_tests.SPEC
                elif name == "zcode":
                    env = {**fixture.environment, "BUDDY_ZCODE_TEST_CASE": "ok"}
                    spec = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
                else:
                    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
                    env.update(HOME=str(fixture.root), CODEX_HOME=str(fixture.home),
                        PYTHONPATH=str(Path(__file__).parents[4] / "src"), BUDDY_DEV_SOURCE="1",
                        BUDDY_CODEX_CLI=str(codex_fast_tests.FIXTURE),
                        BUDDY_CODEX_FIXTURE_STATE=str(fixture.root / "trace.json"),
                        BUDDY_STATE_DIR=str(fixture.root / "state"), BUDDY_RUNTIME_ROOT=str(fixture.root / "runtime"))
                    spec = {"provider": "openai", "model": "fixture-model", "effort": "low"}
                context = ExecutionContext("task", "attempt", 1,
                    {"adapter": "decision", "cwd": str(fixture.cwd), "timeoutSeconds": 60},
                    Path(env["BUDDY_STATE_DIR"]) / "attempts/task/attempt", {}, env,
                    decision_input={"profile": {"adapter": name, **spec},
                        "profiles": [{"profileId": "legal"}], "routingMode": "fast",
                        "tableRevision": 1, "task": "Select", "outputSchema": router.answer_schema(["legal"]),
                        "budget": {"timeoutSeconds": 60}})
                handle = DecisionAdapter().start(context)
                self.addCleanup(lambda h=handle: h.terminate(grace_seconds=0.1) if h.group_alive() else None)
                self.assertIsNotNone(handle.wait(8))
                outcome = DecisionAdapter().collect(handle, context)
                self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
                control = (handle.role_run_control if hasattr(handle, "role_run_control")
                           else json.loads((context.directory / "no-tool-control.json").read_text()))
                self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, name)))
                cleanup_attempt_credentials(Path(env["BUDDY_STATE_DIR"]), name, "task", "attempt")
                self.assert_partition(context)

    def test_dsh_coding_and_reconstructed_claude_continuation(self):
        fixture = self.fixture(dsh_coding_tests.DshRoleCase)
        original_record = fixture.governed_record
        def with_session_record(context, **kwargs):
            original_record(context, **kwargs)
            selection = json.loads(fixture.record.read_text())
            selection["dsh"]["command"] += ["--session-record", "usage"]
            fixture.record.write_text(json.dumps(selection))
        fixture.governed_record = with_session_record
        for index in (1, 2):
            with self.subTest(adapter="dsh", attempt=index):
                context = fixture.context(index=index, attempt=f"attempt-{index}")
                state = Path(context.environment["BUDDY_STATE_DIR"])
                context.directory = state / "attempts/task" / context.attempt_id
                handle, outcome = fixture.execute(context)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                control = handle.role_run_control
                self.assertTrue(Path(control["inquiry"]["errorPath"]).is_relative_to(context.directory))
                self.assertTrue((Path(control["nativeRoot"]) / "dsh-home/sessions").is_dir())
                cleanup_attempt_credentials(state, "dsh", "task", context.attempt_id)
                self.assertFalse((context_root(context, "dsh") / "inquiry.json").exists())
                self.assertTrue(Path(handle.log_paths["stdout"]).is_file())
                self.assert_partition(context)

        claude = self.fixture(claude_tests.ClaudeAdapterTests)
        initial = claude.context()
        initial.directory = Path(initial.environment["BUDDY_STATE_DIR"]) / "attempts" / initial.task_id / initial.attempt_id
        first = claude.execute(initial)
        self.assertEqual(first.status, "ok", first.to_report())
        cleanup_attempt_credentials(Path(initial.environment["BUDDY_STATE_DIR"]), "claude", initial.task_id, initial.attempt_id)
        continuation = claude.context(index=2, previous=first.result["turn"]["sessionId"])
        continuation.directory = Path(continuation.environment["BUDDY_STATE_DIR"]) / "attempts" / continuation.task_id / continuation.attempt_id
        second = claude.execute(continuation)
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["turn"]["resumeMode"], "reconstructed-new-session")
        cleanup_attempt_credentials(Path(continuation.environment["BUDDY_STATE_DIR"]), "claude", continuation.task_id, continuation.attempt_id)
        self.assert_partition(initial)
        self.assert_partition(continuation)

    def test_codex_and_zcode_native_continuation_survives_credential_cleanup(self):
        for kind, name in ((codex_tests.CodexAdapterTests, "codex"),
                           (zcode_tests.ZcodeFixtureCase, "zcode")):
            with self.subTest(adapter=name):
                fixture = self.fixture(kind)
                first = fixture.context()
                state = Path(first.environment["BUDDY_STATE_DIR"])
                first.directory = state / "attempts" / first.task_id / first.attempt_id
                execution = fixture.execute(first)
                outcome = execution[1] if isinstance(execution, tuple) else execution
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                session = outcome.result["turn"]["sessionId"]
                cleanup_attempt_credentials(state, name, first.task_id, first.attempt_id)
                second = fixture.context(index=2, previous=session)
                second.directory = state / "attempts" / second.task_id / second.attempt_id
                resumed = fixture.execute(second)
                result = resumed[1] if isinstance(resumed, tuple) else resumed
                self.assertEqual(result.status, "ok", result.to_report())
                self.assertEqual(result.result["turn"]["sessionId"], session)
                cleanup_attempt_credentials(state, name, second.task_id, second.attempt_id)
                self.assert_partition(first)
                self.assert_partition(second)


if __name__ == "__main__":
    unittest.main()
